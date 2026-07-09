# -*- coding: utf-8 -*-
"""
수동 재publish 배치 — 게시 상품의 대표이미지를 '뱃지 썸네일'로 BUYMA 에 반영.

동작:
  1) 썸네일 생성됐고(is_generated=1) 아직 반영 안 된(buyma_applied_at IS NULL)
     게시 상품(is_published=1)을 고른다.
  2) 검증된 수정 경로(reconcile_buyma_push.execute_edit_safe)로 BUYMA 에 edit 전송.
     - get_product_images 가 이미 썸네일 우선 반환하므로 대표이미지가 자동으로 뱃지본이 됨.
     - stock sync 와 동시실행 안전(group_lock).
  3) 성공 → ace_product_thumbnails.buyma_applied_at 기록. 실패 → buyma_apply_error 기록.
  재실행 안전: 이미 반영된 건(buyma_applied_at) 자동 skip → 끊겨도 이어서.

사용:
  python thumbnail_buyma_apply.py                          # DRY RUN (전송 안 함, 대상/내용 확인)
  python thumbnail_buyma_apply.py --limit=20               # 20건만 (여전히 dry-run)
  python thumbnail_buyma_apply.py --brand=Nike             # 특정 브랜드만
  python thumbnail_buyma_apply.py --limit=5 --confirm-live # ★실제 전송 (소량부터 권장)
  python thumbnail_buyma_apply.py --confirm-live           # 전체 실제 전송
옵션:
  --delay 0.4   각 전송 사이 대기(초). 기본 0.3 (BUYMA 과호출 방지)
  --listing-id  특정 listing 하나만
"""
import os, sys, time, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'okmall'))
import reconcile_buyma_push as rp  # 검증된 edit 경로 재사용
from datetime import datetime


def log(msg, level="INFO"):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level}] {msg}", flush=True)


def fetch_targets(conn, limit=None, brand=None, listing_id=None):
    """반영 대상: 썸네일 있고 아직 반영 안 된 게시상품 → (listing_id, ace_id, brand)."""
    sql = """
        SELECT DISTINCT bl.id AS listing_id, a.id AS ace_id, a.brand_name
        FROM ace_product_thumbnails t
        JOIN ace_products a       ON a.id = t.ace_product_id
        JOIN source_offerings so  ON so.ace_product_id = a.id
        JOIN buyma_listings bl    ON bl.id = so.listing_id
        WHERE t.is_generated = 1
          AND t.buyma_applied_at IS NULL
          AND a.is_published = 1
          AND a.buyma_product_id IS NOT NULL
    """
    params = []
    if listing_id:
        sql += " AND bl.id = %s "; params.append(listing_id)
    if brand:
        sql += " AND UPPER(a.brand_name) LIKE %s "; params.append(f"%{brand.upper()}%")
    sql += " ORDER BY bl.id "
    if limit:
        sql += " LIMIT %s "; params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def mark_applied(conn, ace_id, ok, error=None):
    with conn.cursor() as cur:
        if ok:
            cur.execute("""UPDATE ace_product_thumbnails
                           SET buyma_applied_at = NOW(), buyma_apply_error = NULL
                           WHERE ace_product_id = %s AND is_generated = 1""", (ace_id,))
        else:
            cur.execute("""UPDATE ace_product_thumbnails
                           SET buyma_apply_error = %s
                           WHERE ace_product_id = %s AND is_generated = 1""", (str(error)[:2000], ace_id))
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description='게시상품 대표이미지를 뱃지 썸네일로 BUYMA 반영')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--brand', type=str, default=None)
    ap.add_argument('--listing-id', type=int, default=None)
    ap.add_argument('--delay', type=float, default=0.3, help='전송 간 대기(초)')
    ap.add_argument('--confirm-live', action='store_true', help='실제 전송(없으면 DRY RUN)')
    args = ap.parse_args()

    dry = not args.confirm_live
    conn = rp.get_connection()
    targets = fetch_targets(conn, limit=args.limit, brand=args.brand, listing_id=args.listing_id)

    log("=" * 60)
    log(f"BUYMA 반영 배치 {'[DRY RUN]' if dry else '[LIVE 전송]'}"
        + (f" brand={args.brand}" if args.brand else "") + f" | 대상 {len(targets)}건")
    if dry:
        log("*** DRY RUN — 실제 전송 안 함. 실제 전송은 --confirm-live ***", "WARN")
    log("=" * 60)
    if not targets:
        log("반영할 대상이 없습니다.")
        return

    stats = {'ok': 0, 'skip': 0, 'fail': 0, 'no_thumb': 0}
    for i, row in enumerate(targets, 1):
        lid, ace_id, brand = row['listing_id'], row['ace_id'], row['brand_name']
        try:
            listing = rp.fetch_listing(conn, lid)
            if listing is None:
                log(f"[{i}/{len(targets)}] listing={lid} 사라짐 → skip", "WARN")
                stats['skip'] += 1
                continue
            res = rp.execute_edit_safe(conn, listing, dry_run=dry)

            # 방어: 실제로 payload 대표이미지가 썸네일인지 확인
            req = res.get('request')
            if req:
                img0 = (req.get('product', {}).get('images') or [{}])[0].get('path', '')
                is_thumb = '/thumbnail/' in img0
            else:
                is_thumb = None

            if res.get('skipped'):
                log(f"[{i}/{len(targets)}] listing={lid} skip: {res.get('reason')}", "WARN")
                stats['skip'] += 1
            elif dry:
                tag = '썸네일✅' if is_thumb else ('원본⚠️' if is_thumb is False else '?')
                log(f"[{i}/{len(targets)}] listing={lid} ace={ace_id} [{brand}] 대표이미지={tag}")
                if is_thumb is False:
                    stats['no_thumb'] += 1
                else:
                    stats['ok'] += 1
            else:
                resp = res.get('response') or {}
                if resp.get('success'):
                    mark_applied(conn, ace_id, True)
                    log(f"[{i}/{len(targets)}] listing={lid} ace={ace_id} => 반영✅ ({resp.get('status_code')})", "SUCCESS")
                    stats['ok'] += 1
                else:
                    err = resp.get('error') or resp.get('status_code') or 'unknown'
                    mark_applied(conn, ace_id, False, err)
                    log(f"[{i}/{len(targets)}] listing={lid} ace={ace_id} => 실패: {str(err)[:120]}", "ERROR")
                    stats['fail'] += 1
        except Exception as e:
            mark_applied(conn, ace_id, False, e)
            log(f"[{i}/{len(targets)}] listing={lid} ace={ace_id} => 예외: {e}", "ERROR")
            stats['fail'] += 1

        if args.delay and not dry:
            time.sleep(args.delay)

    log("=" * 60)
    log(f"완료 — 반영 {stats['ok']} / 스킵 {stats['skip']} / 실패 {stats['fail']}"
        + (f" / 원본경고 {stats['no_thumb']}" if stats['no_thumb'] else ""))
    if dry:
        log("DRY RUN 이었습니다. 실제 전송은 --confirm-live (소량 --limit 부터 권장).", "WARN")
    conn.close()


if __name__ == '__main__':
    main()
