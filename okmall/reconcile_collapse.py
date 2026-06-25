# -*- coding: utf-8 -*-
"""
MERGE reconcile — COLLAPSE 모드 (같은 상품이 BUYMA에 2개+ 출품된 진짜 중복 정리)

대상: 게시 멤버(is_published=1) 2개+ 인 그룹 중,
      멤버 model_no 가 기호제거 후 완전히 동일한 것 = '순수 표기차 중복'(삭제 안전).
      (색/사이즈/시즌 토큰이 다른 '변종'은 SKU 손실 위험 → 여기서 제외, 수동검토)

처리:
  1. keeper = buyma_product_stats 누적가치 최고 (판매>매출>찜>장바구니>조회, 동점이면 오래된 id)
  2. keeper 를 merge union 옵션으로 EDIT (carry everything) — group_lock 안에서
  3. 나머지(loser) BUYMA delete (control=delete, loser reference_number)
     → 실제 is_published=0/status=deleted 는 웹훅이 반영

안전:
  - group_lock(group_key) + 락 후 재조회 → multi-PC 안전
  - 락 후 게시 멤버가 1개 이하면(이미 정리됨) 스킵
  - delete 는 비가역 → --execute + --confirm-live 둘 다 필요

Usage:
    python reconcile_collapse.py                      # 안전(A) 그룹 분석 요약
    python reconcile_collapse.py --list               # A/B 분류 전체 나열
    python reconcile_collapse.py --dry-run            # A 그룹 keeper/loser + EDIT/DELETE 계획
    python reconcile_collapse.py --execute --confirm-live   # 실제 실행 (운영 쓰기!)
"""

import re
import time
import argparse

import reconcile_buyma_push as push
import buyma_new_product_register as reg


def norm(m):
    return re.sub(r'[^A-Za-z0-9]', '', (m or '')).upper()


def same_product(models):
    """기호무시 부분문자열/앞6자 공유 → 같은 상품 계열(과병합 아님)."""
    ms = [norm(m) for m in models if m]
    if len(ms) <= 1:
        return True
    base = min(ms, key=len)
    return all(base in m or m in base or base[:6] == m[:6] for m in ms)


def _rankkey(m):
    z = lambda v: v if v is not None else 0
    # 높을수록 keeper. 동점이면 오래된(작은 buyma_product_id) 유지 → 마지막 항 음수
    return (z(m['sold_count']), z(m['sales_amount_jpy']), z(m['favorite_count']),
            z(m['cart_count']), z(m['access_count']), -int(m['buyma_product_id']))


def collapse_candidates(conn):
    """게시멤버 2+ 그룹을 A(순수표기차·안전)/B(변종·보류)/over(과병합)로 분류."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT l.id, COUNT(DISTINCT CASE WHEN a.is_published=1 THEN a.buyma_product_id END) n
            FROM buyma_listings l
            JOIN source_offerings so ON so.listing_id=l.id
            JOIN ace_products a ON a.id=so.ace_product_id
            WHERE l.is_active=1 AND l.winner_offering_id IS NOT NULL
            GROUP BY l.id HAVING n>=2
        """)
        ids = [r['id'] for r in cur.fetchall()]
    A, B, over = [], [], []
    for lid in ids:
        models = _published_models(conn, lid)
        if not same_product(models):
            over.append((lid, models))
            continue
        if len({norm(m) for m in models}) == 1:
            A.append((lid, models))
        else:
            B.append((lid, models))
    return A, B, over


def _published_models(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT a.model_no FROM source_offerings so
            JOIN ace_products a ON a.id=so.ace_product_id AND a.is_published=1
            WHERE so.listing_id=%s
        """, (listing_id,))
        return [r['model_no'] for r in cur.fetchall()]


def published_members(conn, listing_id):
    """게시 멤버 + stats. keeper 판정/EDIT용 ace 정체성 포함."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.id AS ace_id, a.buyma_product_id, a.is_buyma_locked,
                   a.locked_name, a.locked_brand_id, a.locked_category_id, a.locked_reference_number,
                   a.name, a.brand_id, a.category_id, a.reference_number, a.source_site, a.model_no,
                   s.access_count, s.cart_count, s.favorite_count, s.sold_count, s.sales_amount_jpy
            FROM source_offerings so
            JOIN ace_products a ON a.id=so.ace_product_id AND a.is_published=1
            LEFT JOIN buyma_product_stats s ON s.buyma_product_id=a.buyma_product_id
            WHERE so.listing_id=%s
        """, (listing_id,))
        return cur.fetchall()


def pick_keeper(members):
    """stats 순위로 keeper 1, 나머지 loser."""
    ordered = sorted(members, key=_rankkey, reverse=True)
    return ordered[0], ordered[1:]


def execute_collapse(conn, listing, dry_run=True, lock_timeout=10):
    """한 그룹 COLLAPSE: keeper EDIT(union) → loser delete. group_lock 안전."""
    gk = listing.get('group_key') or f"listing:{listing['id']}"
    with push.group_lock(conn, gk, timeout=lock_timeout) as got:
        if not got:
            return {'skipped': True, 'reason': f'다른 PC 처리중 (group_key={gk})'}
        members = published_members(conn, listing['id'])
        if len(members) < 2:
            return {'skipped': True, 'reason': f'게시 멤버 {len(members)}개 (이미 정리됨)'}
        keeper, losers = pick_keeper(members)

        # 1) keeper 를 union 옵션으로 EDIT (loser 삭제 전에 keeper가 전부 carry)
        req = push.build_edit_request(conn, listing, keeper)
        edit_resp = None
        if req is None:
            return {'skipped': True, 'reason': 'keeper EDIT build None(전체품절)', 'keeper': keeper}
        if not dry_run:
            edit_resp = reg.call_buyma_api(req)
            if not edit_resp.get('success'):
                # keeper 수정 실패 → loser 삭제 보류(안전)
                return {'keeper': keeper, 'losers': losers, 'edit_resp': edit_resp,
                        'aborted': 'keeper EDIT 실패 → 삭제 안 함'}
            time.sleep(0.4)

        # 2) loser 삭제
        del_results = []
        for lo in losers:
            if dry_run:
                del_results.append({'buyma_product_id': lo['buyma_product_id'], 'dry_run': True})
            else:
                dresp = reg.call_buyma_delete_api(lo['reference_number'])
                del_results.append({'buyma_product_id': lo['buyma_product_id'],
                                    'success': dresp.get('success'), 'status_code': dresp.get('status_code'),
                                    'error': dresp.get('error')})
                time.sleep(0.4)
        return {'keeper': keeper, 'losers': losers, 'req': req,
                'edit_resp': edit_resp, 'del_results': del_results, 'dry_run': dry_run}


def _sfmt(m):
    z = lambda v: v if v is not None else 0
    return 'sold=%s sales=%s fav=%s cart=%s acc=%s' % (
        z(m['sold_count']), z(m['sales_amount_jpy']), z(m['favorite_count']),
        z(m['cart_count']), z(m['access_count']))


def main():
    ap = argparse.ArgumentParser(description='MERGE COLLAPSE (진짜 중복 정리)')
    ap.add_argument('--list', action='store_true', help='A/B/과병합 분류 전체 나열')
    ap.add_argument('--dry-run', action='store_true', help='A 그룹 keeper/loser + 계획 출력(쓰기 없음)')
    ap.add_argument('--execute', action='store_true', help='실제 실행 (keeper EDIT + loser 삭제, 운영 쓰기!)')
    ap.add_argument('--confirm-live', action='store_true', help='비가역 삭제 최종 확인 (--execute 와 함께 필수)')
    ap.add_argument('--limit', type=int, help='처리 그룹 수 제한')
    args = ap.parse_args()

    conn = push.get_connection()
    try:
        A, B, over = collapse_candidates(conn)
        print("=" * 64)
        print(f"  COLLAPSE 후보: A(순수표기차·안전) {len(A)} / B(변종·보류) {len(B)} / 과병합 {len(over)}")
        print("=" * 64)

        if args.list:
            print("\n[A. 순수 표기차 — 삭제 안전]")
            for lid, ms in A:
                print('  #%-6s %s' % (lid, '  vs  '.join(repr(m) for m in ms)))
            print("\n[B. 변종 — 보류/검토]")
            for lid, ms in B:
                print('  #%-6s %s' % (lid, '  vs  '.join(repr(m) for m in ms)))
            print("\n[과병합 — 그룹분해 필요]")
            for lid, ms in over:
                print('  #%-6s (%d models)' % (lid, len(ms)))
            return

        targets = [lid for lid, _ in A]
        if args.limit:
            targets = targets[:args.limit]

        if not (args.dry_run or args.execute):
            print("\n(--dry-run 으로 계획 확인, --execute --confirm-live 로 실행)")
            return

        live = args.execute and args.confirm_live
        if args.execute and not args.confirm_live:
            print("⛔ 비가역 삭제 — --confirm-live 필요. (지금은 발사 안 함)")
            return
        if args.execute:
            print(f"목적지: {reg.API_BASE_URL}  ({'★운영(실제 스토어)★' if reg.BUYMA_MODE==1 else '샌드박스'})")

        print(f"\n[{'EXECUTE' if live else 'DRY-RUN'}] A 그룹 {len(targets)}건 처리\n")
        kept = deleted = skipped = failed = 0
        for lid in targets:
            listing = push.fetch_listing(conn, lid)
            res = execute_collapse(conn, listing, dry_run=not live)
            print("=" * 70)
            print(f"#{lid}  {listing['name'][:55]}")
            if res.get('skipped'):
                print(f"  스킵: {res['reason']}")
                skipped += 1
                continue
            if res.get('aborted'):
                print(f"  ⚠ 중단: {res['aborted']}  edit={res['edit_resp']}")
                failed += 1
                continue
            k = res['keeper']
            print(f"  KEEP buyma={k['buyma_product_id']} [{k['model_no']}]  {_sfmt(k)}")
            for lo, dr in zip(res['losers'], res['del_results']):
                if live:
                    okmark = '✅' if dr.get('success') else '❌'
                    print(f"  DEL  buyma={lo['buyma_product_id']} [{lo['model_no']}]  {okmark} code={dr.get('status_code')}")
                    if dr.get('success'):
                        deleted += 1
                    else:
                        failed += 1
                        print(f"       err={str(dr.get('error'))[:200]}")
                else:
                    print(f"  DEL  buyma={lo['buyma_product_id']} [{lo['model_no']}]  (dry-run)")
                    deleted += 1
            kept += 1
        print("\n" + "=" * 70)
        print(f"결과: keeper유지 {kept} / loser삭제 {deleted} / 스킵 {skipped} / 실패 {failed}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
