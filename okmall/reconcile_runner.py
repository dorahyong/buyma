# -*- coding: utf-8 -*-
"""
MERGE reconcile — 진입점 (register=create / stock=edit, multi-PC 안전)

merge 테이블(buyma_listings)에서 작업 대상 그룹을 골라 BUYMA 로 push 한다.
모든 push 는 그룹 락(GET_LOCK)+락 후 재확인을 거치므로, 몇 대 PC에서 동시에
돌려도 같은 상품을 중복 등록/충돌 수정하지 않는다.

  create (register 역할): 그룹 멤버 누구도 미등록 → 신규 등록
      = winner + 카테고리 + 이름/옵션 한글 없음 + 옵션≥1 + 이미지 + 미등록
  edit   (stock 역할):    게시 멤버 정확히 1개 → 재고/가격/소싱 수정(반복 정상)

Usage:
    python reconcile_runner.py                          # dry-run, create, 3건
    python reconcile_runner.py --mode edit --limit 5    # dry-run, edit
    python reconcile_runner.py --listing-id 9           # 특정 listing 1건
    python reconcile_runner.py --shard 0/3              # 샤딩 (group_key 해시 % 3 == 0)
    python reconcile_runner.py --summary                # JSON 생략, 요약만

    # 실제 push (운영 쓰기) — --confirm-live 필수
    python reconcile_runner.py --execute --confirm-live              # 신규 등록
    python reconcile_runner.py --mode edit --execute --confirm-live  # 재고/가격 수정
"""

import os
import json
import time
import argparse

import reconcile_buyma_push as push
import reconcile_ensure_group as eg
from dedup_corrector_merge import canonicalize


def _decimal_default(o):
    try:
        from decimal import Decimal
        if isinstance(o, Decimal):
            return float(o)
    except Exception:
        pass
    raise TypeError


def select_clean_new_listing_ids(conn, limit=3, listing_id=None, shard=None):
    """깨끗한 신규 출품가능 listing id 목록."""
    where = [
        "l.is_active=1",
        "l.winner_offering_id IS NOT NULL",
        "l.category_id IS NOT NULL AND l.category_id<>0",
        "l.name NOT REGEXP '[가-힣]'",
        "EXISTS (SELECT 1 FROM listing_options o WHERE o.listing_id=l.id AND o.is_active=1)",
        # 옵션 값(색/사이즈)에 한글 있으면 BUYMA가 '不正な文字' 로 거부 → 제외
        "NOT EXISTS (SELECT 1 FROM listing_options ok WHERE ok.listing_id=l.id AND ok.is_active=1 AND (ok.color_value REGEXP '[가-힣]' OR ok.size_value REGEXP '[가-힣]'))",
        # 이미지 최소 1장 (없으면 거부). ※IP차단은 사전판별 불가 → 반응적 처리
        "EXISTS (SELECT 1 FROM listing_images i WHERE i.listing_id=l.id AND i.cloudflare_image_url IS NOT NULL)",
        # 이미 merge-등록(buyma_product_id 있음)/시도중(pending)/실패(fail) 제외 → 배치가 새 것으로 전진
        "l.buyma_product_id IS NULL",
        "(l.status IS NULL OR l.status NOT IN ('pending', 'fail', 'success'))",
    ]
    params = []
    if listing_id is not None:
        where.append("l.id=%s")
        params.append(listing_id)
    if shard is not None:
        i, n = shard
        where.append("MOD(CRC32(l.group_key), %s)=%s")
        params += [n, i]

    sql = f"""
        SELECT l.id
        FROM buyma_listings l
        JOIN source_offerings so ON so.listing_id=l.id
        JOIN ace_products a ON a.id=so.ace_product_id
        WHERE {' AND '.join(where)}
        GROUP BY l.id
        HAVING COUNT(DISTINCT CASE WHEN a.is_published=1 THEN a.buyma_product_id END)=0
        ORDER BY l.id
        LIMIT %s
    """
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r['id'] for r in cur.fetchall()]


def select_edit_listing_ids(conn, limit=3, listing_id=None, shard=None):
    """수정(EDIT) 대상 listing id 목록 — BUYMA에 게시된 멤버가 정확히 1개인 그룹.

    CREATE 와 달리 buyma_product_id/status 로 거르지 않는다(EDIT 은 반복 갱신이 정상).
    이름·이미지는 게시 멤버의 locked/ace 값을 쓰므로 여기선 옵션 한글만 거른다
    (옵션 값은 그대로 BUYMA 로 전송되므로).
    """
    where = [
        "l.is_active=1",
        "l.winner_offering_id IS NOT NULL",
        "EXISTS (SELECT 1 FROM listing_options o WHERE o.listing_id=l.id AND o.is_active=1)",
        # 옵션 값(색/사이즈)에 한글 있으면 BUYMA가 '不正な文字' 로 거부 → 제외
        "NOT EXISTS (SELECT 1 FROM listing_options ok WHERE ok.listing_id=l.id AND ok.is_active=1 AND (ok.color_value REGEXP '[가-힣]' OR ok.size_value REGEXP '[가-힣]'))",
    ]
    params = []
    if listing_id is not None:
        where.append("l.id=%s")
        params.append(listing_id)
    if shard is not None:
        i, n = shard
        where.append("MOD(CRC32(l.group_key), %s)=%s")
        params += [n, i]

    sql = f"""
        SELECT l.id
        FROM buyma_listings l
        JOIN source_offerings so ON so.listing_id=l.id
        JOIN ace_products a ON a.id=so.ace_product_id
        WHERE {' AND '.join(where)}
        GROUP BY l.id
        HAVING COUNT(DISTINCT CASE WHEN a.is_published=1 THEN a.buyma_product_id END)=1
        ORDER BY l.id
        LIMIT %s
    """
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r['id'] for r in cur.fetchall()]


def parse_shard(s):
    if not s:
        return None
    i, n = s.split('/')
    return int(i), int(n)


# ============================================================
# AUTO 모드 — 상품(ace_products) 기준 → ensure_group(즉석) → 분류 → push
#   배치(buyma_listings 미리채움) 의존 없음. 그룹락으로 multi-PC 안전.
# ============================================================

def select_groups_to_process(conn, limit=3, source=None, model_no=None, shard=None, scope='all'):
    """처리할 그룹(seed model_no, brand_id) 목록 — ace_products 기준.
    중복모델 제외(옛 dedup) 안 함. canonical 단위로 dedup해서 같은 그룹 중복 처리 방지.

    scope:
      'new'       register 역할 — 모델이 아직 바이마 미등록인 것만 (그룹 미등록 → CREATE)
      'published' stock 역할 — 모델이 이미 바이마 등록된 것 (그룹 등록됨 → EDIT/삭제)
      'all'       전부 (기본)
    """
    where = [
        "a.is_active=1",
        "a.model_no IS NOT NULL AND a.model_no<>''",
        "(a.status IS NULL OR a.status<>'deleted')",
        "a.category_id IS NOT NULL AND a.category_id>0",
        "a.name NOT REGEXP '[가-힣]'",
        # ★ model_no 한글 제외 (기존 register process_product 와 동일 — 한글 model_no 스킵)
        "a.model_no NOT REGEXP '[가-힣ㄱ-ㅎㅏ-ㅣ]'",
        "EXISTS (SELECT 1 FROM ace_product_images i WHERE i.ace_product_id=a.id AND i.cloudflare_image_url IS NOT NULL)",
    ]
    params = []
    # scope 사전필터 (효율용; 최종 판정은 process_one_group 의 n_pub 게이트)
    if scope == 'new':
        # 같은 model_no 가 바이마에 등록(is_published=1)된 게 없는 것만 = 미등록 그룹
        where.append("NOT EXISTS (SELECT 1 FROM ace_products p2 WHERE p2.model_no=a.model_no AND p2.is_published=1)")
    elif scope == 'published':
        # 같은 model_no 가 바이마에 등록된 그룹
        where.append("EXISTS (SELECT 1 FROM ace_products p2 WHERE p2.model_no=a.model_no AND p2.is_published=1)")
    if source:
        where.append("a.source_site=%s"); params.append(source)
    if model_no:
        where.append("a.model_no=%s"); params.append(model_no)
    if shard is not None:
        i, n = shard
        where.append("MOD(CRC32(a.model_no), %s)=%s"); params += [n, i]
    sql = f"""
        SELECT a.model_no, a.brand_id, MAX(a.id) AS max_id
        FROM ace_products a
        WHERE {' AND '.join(where)}
        GROUP BY a.model_no, a.brand_id
        ORDER BY max_id DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # canonical 단위 dedup + 쓰레기 model_no(짧은 canonical) 제외
    seen, groups = set(), []
    for r in rows:
        canon = canonicalize(r['model_no'])
        if len(canon) < 5:           # '-', '#1000' 등 쓰레기 제외
            continue
        key = (r['brand_id'], canon)
        if key in seen:
            continue
        seen.add(key)
        groups.append((r['model_no'], r['brand_id']))
        if len(groups) >= limit:
            break
    return groups


def _n_published(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT a.buyma_product_id) n
            FROM source_offerings so JOIN ace_products a ON a.id=so.ace_product_id AND a.is_published=1
            WHERE so.listing_id=%s
        """, (listing_id,))
        return cur.fetchone()['n']


def _has_instock_options(conn, listing_id):
    """재고 있는(out_of_stock 아닌) 활성 옵션이 1개+ 인가."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) n FROM listing_options
            WHERE listing_id=%s AND is_active=1 AND stock_type<>'out_of_stock'
        """, (listing_id,))
        return cur.fetchone()['n'] > 0


def process_one_group(conn, model_no, brand_id, dry_run=True, lock_timeout=10, scope='all', no_push=False):
    """그룹락 안에서: ensure_group(즉석 빌드) → 분류(CREATE/EDIT/COLLAPSE) → push.
    락을 여기서 한 번 잡고, 내부는 비-safe execute 사용(이중 락 방지).

    scope: 'new'=미등록 그룹만(register) / 'published'=등록 그룹만(stock) / 'all'=전부.
    no_push: ensure_group(merge/winner 갱신)만 하고 BUYMA push 생략(소싱교정 내부적용).
    """
    import re as _re
    # 기존 register process_product 와 동일: 한글 model_no 스킵
    if _re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', model_no or ''):
        return {'skipped': True, 'reason': f'model_no 한글 → 스킵', 'model_no': model_no}
    canon = canonicalize(model_no)
    with push.group_lock(conn, canon, timeout=lock_timeout) as got:
        if not got:
            return {'skipped': True, 'reason': f'다른 PC 처리중 (canon={canon})', 'model_no': model_no}

        # 1) 그룹 즉석 빌드 (execute 시 실제 적재; dry_run 은 기존 그룹만 미리보기)
        res = eg.ensure_group(conn, model_no, brand_id, dry_run=dry_run)
        listing_id = res.get('listing_id')
        if not listing_id:
            return {'skipped': True, 'reason': res.get('reason', 'listing 없음'),
                    'model_no': model_no, 'members': len(res.get('members', []))}

        listing = push.fetch_listing(conn, listing_id)
        n_pub = _n_published(conn, listing_id)

        # 출품 가능 여부: winner(마진O) 있고 + 재고 있는 옵션 1개+ (기존 stock 과 동일 기준)
        sellable = bool(listing.get('winner_offering_id')) and _has_instock_options(conn, listing_id)
        already_live = bool(listing.get('reference_number')) and (
            listing.get('status') in ('pending', 'success') or listing.get('is_published'))

        # ── scope 게이트 (register/stock 분담) ──
        group_on_buyma = (n_pub >= 1) or already_live
        if scope == 'new' and group_on_buyma:
            return {'skipped': True, 'reason': '이미 바이마 등록된 그룹 → stock 담당',
                    'listing_id': listing_id, 'model_no': model_no}
        if scope == 'published' and not group_on_buyma:
            return {'skipped': True, 'reason': '미등록 그룹 → register 담당',
                    'listing_id': listing_id, 'model_no': model_no}

        # ── no_push: merge/winner 는 ensure_group 이 이미 반영(소싱교정 내부적용). BUYMA push 생략. ──
        #   소싱 교정은 fulfillment 시점에 winner 몰에서 매입 → BUYMA 출품 변경 불필요.
        #   옛 stock(가격/재고 push)과 충돌 없음.
        if no_push:
            return {'mode': 'merge_only', 'listing_id': listing_id, 'model_no': model_no,
                    'n_pub': n_pub, 'winner_offering_id': listing.get('winner_offering_id'),
                    'reason': '소싱/옵션 merge 내부반영, BUYMA push 생략'}

        # ── 출품 불가(마진X/전체품절) ──  기존 stock 동일: live면 삭제, 신규면 등록 안 함
        if not sellable:
            if not already_live:
                return {'skipped': True, 'reason': '출품불가(마진X/품절) + 미등록 → 등록 안 함',
                        'listing_id': listing_id, 'model_no': model_no}
            if dry_run:
                return {'dry_run': True, 'listing_id': listing_id, 'model_no': model_no,
                        'n_pub': n_pub, 'mode': 'retire'}
            r = push.execute_retire(conn, listing, dry_run=False)
            return {'mode': 'retire', 'listing_id': listing_id, 'model_no': model_no, **r}

        # ── 출품 가능 → 분류(CREATE/EDIT/COLLAPSE) ──
        if dry_run:
            mode = 'create' if n_pub == 0 else ('edit' if n_pub == 1 else 'collapse')
            return {'dry_run': True, 'listing_id': listing_id, 'model_no': model_no,
                    'n_pub': n_pub, 'mode': mode, 'resolve': res.get('resolve')}

        if n_pub == 0:
            r = push.execute_create(conn, listing, dry_run=False)
            return {'mode': 'create', 'listing_id': listing_id, 'model_no': model_no, **r}
        elif n_pub == 1:
            r = push.execute_edit(conn, listing, dry_run=False)
            return {'mode': 'edit', 'listing_id': listing_id, 'model_no': model_no, **r}
        else:
            return {'skipped': True, 'reason': f'COLLAPSE({n_pub}개) 수동검토', 'listing_id': listing_id,
                    'model_no': model_no}


def _run_auto(args):
    """AUTO 모드 실행: 상품 그룹 선정 → process_one_group 반복."""
    conn = push.get_connection()
    try:
        groups = select_groups_to_process(
            conn, limit=args.limit, source=args.source,
            model_no=args.model_no, shard=parse_shard(args.shard), scope=args.scope)

        do_writes = args.execute                        # --execute 라야 실제 기록(merge/BUYMA)
        do_buyma = args.execute and not args.no_push    # 실제 BUYMA push 여부 (no_push면 merge만)

        if do_buyma:
            import buyma_new_product_register as reg
            live = (reg.BUYMA_MODE == 1)
            print(f"[AUTO] 목적지: {reg.API_BASE_URL}  ({'★운영(실제 스토어)★' if live else '샌드박스'})")
            if not args.confirm_live:
                print("⛔ 실제 push 는 --confirm-live 가 필요합니다. (지금은 발사 안 함)")
                return
            # 처리량 경계 = --limit. --confirm-live 가 안전장치.
        elif args.no_push:
            print("[AUTO/no-push] merge·winner 갱신만(소싱교정 내부적용), BUYMA push 없음")

        label = 'EXECUTE' if do_buyma else ('MERGE-ONLY' if args.no_push else 'DRY-RUN')
        print(f"[{label}/auto] 대상 그룹 {len(groups)}건\n")
        cnt = {'create': 0, 'edit': 0, 'collapse': 0, 'merge_only': 0, 'skip': 0, 'ok': 0, 'err': 0}
        for model_no, brand_id in groups:
            res = process_one_group(conn, model_no, brand_id, dry_run=not do_writes,
                                    scope=args.scope, no_push=args.no_push)
            print("=" * 70)
            print(f"model_no={model_no!r}  brand_id={brand_id}")
            if res.get('skipped'):
                print(f"  스킵: {res['reason']}")
                cnt['skip'] += 1
                continue
            if res.get('mode') == 'merge_only':
                print(f"  ✓ merge 갱신 listing#{res['listing_id']} winner={res['winner_offering_id']} (push 없음)")
                cnt['merge_only'] += 1
                continue
            if res.get('dry_run'):
                print(f"  → listing#{res['listing_id']}  분류={res['mode']}  (게시멤버 {res['n_pub']})")
                cnt[res['mode']] = cnt.get(res['mode'], 0) + 1
                continue
            # execute push 결과
            mode = res.get('mode')
            resp = res.get('response') or {}
            if resp.get('success'):
                print(f"  ✅ {mode} 성공 listing#{res['listing_id']} status_code={resp.get('status_code')}")
                cnt['ok'] += 1
                cnt[mode] = cnt.get(mode, 0) + 1
            else:
                print(f"  ❌ {mode} 실패: {str(resp.get('error'))[:200]}")
                cnt['err'] += 1
            time.sleep(0.4)
        print("\n" + "=" * 70)
        if args.no_push:
            print(f"결과(merge-only): winner갱신 {cnt['merge_only']} / 스킵 {cnt['skip']}")
        elif args.execute:
            print(f"결과: 성공 {cnt['ok']} (create {cnt['create']}/edit {cnt['edit']}) / 실패 {cnt['err']} / 스킵 {cnt['skip']}")
        else:
            print(f"분류: create {cnt['create']} / edit {cnt['edit']} / collapse {cnt['collapse']} / 스킵 {cnt['skip']}")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description='MERGE reconcile 진입점 (2단계 create dry-run)')
    ap.add_argument('--mode', default='create', choices=['create', 'edit', 'auto'],
                    help='auto=상품기준 ensure_group→분류→push(내재형, 권장) / create / edit(레거시)')
    ap.add_argument('--scope', default='all', choices=['new', 'published', 'all'],
                    help="auto 분담: new=미등록그룹만(register역할) / published=등록그룹(stock역할) / all=전부")
    ap.add_argument('--limit', type=int, default=3)
    ap.add_argument('--listing-id', type=int)
    ap.add_argument('--model-no', type=str, help='auto: 특정 model_no 1건')
    ap.add_argument('--source', type=str, help='auto: 특정 수집처만')
    ap.add_argument('--shard', type=str, help='형식 i/N (예: 0/3)')
    ap.add_argument('--summary', action='store_true', help='요청 JSON 대신 요약만')
    ap.add_argument('--execute', action='store_true', help='실제 BUYMA push (운영 쓰기!)')
    ap.add_argument('--confirm-live', action='store_true', help='운영 실제 push 최종 확인 (--execute 와 함께 필수)')
    ap.add_argument('--no-push', action='store_true',
                    help='auto: merge·winner 갱신만(소싱교정 내부적용), BUYMA push 생략. (stock 2차용, confirm-live 불필요)')
    args = ap.parse_args()

    # ---- AUTO 모드 (내재형: ensure_group → 분류 → push) ----
    if args.mode == 'auto':
        _run_auto(args)
        return

    # 모드별 선정 함수 / 실행 함수 / 동사 디스패치
    if args.mode == 'create':
        select_fn = select_clean_new_listing_ids
        exec_fn = push.execute_create_safe
        verb = '등록'
    else:
        select_fn = select_edit_listing_ids
        exec_fn = push.execute_edit_safe
        verb = '수정'

    conn = push.get_connection()
    try:
        ids = select_fn(
            conn, limit=args.limit, listing_id=args.listing_id, shard=parse_shard(args.shard))

        # ---- 실제 push 경로 ----
        if args.execute:
            import buyma_new_product_register as reg
            live = (reg.BUYMA_MODE == 1)
            print(f"모드={args.mode}  목적지: {reg.API_BASE_URL}  ({'★운영(실제 스토어)★' if live else '샌드박스'})")
            if not args.confirm_live:
                print(f"⛔ 실제 {verb}은 --confirm-live 가 필요합니다. (지금은 발사 안 함)")
                return
            if len(ids) > 500:
                print(f"⛔ 안전캡: 한 번에 500건까지. 현재 {len(ids)}건 → --limit 로 줄이세요.")
                return
            print(f"[EXECUTE/{args.mode}] 실제 {verb} {len(ids)}건: {ids}\n")
            ok = err = skipped = 0
            for lid in ids:
                listing = push.fetch_listing(conn, lid)
                # ★ 그룹 락 + 락 후 재확인 → multi-PC / 동시실행 중복·충돌 완전 차단
                res = exec_fn(conn, listing, dry_run=False)
                time.sleep(0.4)  # 레이트리밋 안전 간격
                print("=" * 70)
                print(f"listing #{lid}  name={listing['name']}")
                if res.get('skipped'):
                    print(f"  스킵: {res['reason']}")
                    skipped += 1
                    continue
                resp = res['response']
                if resp.get('success'):
                    print(f"  ✅ 성공 status_code={resp.get('status_code')} (결과는 웹훅으로 추후 반영)")
                    ok += 1
                else:
                    print(f"  ❌ 실패 status_code={resp.get('status_code')}: {str(resp.get('error'))[:300]}")
                    err += 1
            print("\n" + "=" * 70)
            print(f"{verb} 결과: 성공 {ok} / 실패 {err} / 스킵 {skipped} / 총 {len(ids)}")
            return

        # ---- DRY-RUN (기본) ----
        print(f"[DRY-RUN] 모드={args.mode}  대상 listing: {len(ids)}건  {ids}\n")

        ok = 0
        skipped = 0
        for lid in ids:
            listing = push.fetch_listing(conn, lid)
            res = exec_fn(conn, listing, dry_run=True)
            print("=" * 70)
            print(f"listing #{lid}  group_key={listing['group_key']}  name={listing['name']}")
            if res.get('skipped'):
                print(f"  → 스킵: {res['reason']}")
                skipped += 1
                continue
            req = res['request']
            p = req['product']
            tag = f"id={p.get('id')}" if args.mode == 'edit' else f"ref={res.get('ref')}"
            print(f"  control={p['control']}  {tag}  brand_id={p['brand_id']}  "
                  f"category_id={p['category_id']}  price={p['price']}")
            print(f"  images={len(p.get('images', []))}  options={len(p.get('options', []))}  "
                  f"variants={len(p.get('variants', []))}")
            ok += 1
            if not args.summary:
                print("-" * 70)
                print(json.dumps(req, ensure_ascii=False, indent=2, default=_decimal_default))
        print("\n" + "=" * 70)
        print(f"요약: 빌드 성공 {ok} / 스킵 {skipped} / 총 {len(ids)}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
