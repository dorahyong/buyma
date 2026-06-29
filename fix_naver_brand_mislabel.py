# -*- coding: utf-8 -*-
"""
네이버 순회형 수집기의 기존 브랜드 오라벨 교정.

원인: 브랜드 URL이 스토어 홈으로 리다이렉트되면 홈 잡탕 상품을 그 브랜드로 통째 도장
      찍음(carpi 등). raw_json_data.manufacturer(네이버 제조사)가 정답 신호.

판단(collector와 동일 로직):
  - 라벨 == 제조사(표기차 흡수) 또는 같은 계열('BURBERRY' ⊂ 'BURBERRY KIDS')  → 정상, 건드림X
  - 제조사가 한글/지저분('CP컴퍼니')                                          → 신뢰X, 건드림X(보고만)
  - 라벨 ≠ 제조사 & 완전히 다른 브랜드:
      · 제조사가 mall_brands에 아는 브랜드  → [고신뢰] 정식표기로 자동 교정 (carpi 버그)
      · 제조사가 카탈로그에 없음            → [검토] 보고만 (--include-unmapped로 원문 저장)

published(BUYMA 게시완료) 행은 raw 라벨만 고쳐지고 출품 브랜드는 그대로 → 별도 재등록 절차.
이 스크립트는 raw_scraped_data.brand_name_en만 교정. ace 전파는 reupdate_ace_brand_name.py.

사용법:
    python fix_naver_brand_mislabel.py                    # dry-run, 규모 보고
    python fix_naver_brand_mislabel.py --source carpi
    python fix_naver_brand_mislabel.py --apply            # 고신뢰(매핑됨) + 미게시 교정
    python fix_naver_brand_mislabel.py --apply --include-published   # 게시완료 raw도
    python fix_naver_brand_mislabel.py --apply --include-unmapped    # 카탈로그 없는 브랜드도 원문 저장
"""
import os, sys, re, json, argparse, time
from collections import Counter
import pymysql
from dotenv import load_dotenv
load_dotenv()


def _brand_key(s):
    return re.sub(r'[^A-Z0-9]', '', (s or '').upper())


def _has_hangul(s):
    return bool(re.search(r'[가-힣]', s or ''))


def _same_brand(a, b):
    ka, kb = _brand_key(a), _brand_key(b)
    if not ka or not kb:
        return True
    if ka == kb:
        return True
    short, long = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    return len(short) >= 3 and short in long


def connect():
    return pymysql.connect(
        host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--source', type=str, default=None)
    ap.add_argument('--include-published', action='store_true',
                    help='BUYMA 게시완료 상품의 raw 라벨도 교정 (기본: 미게시만)')
    ap.add_argument('--include-unmapped', action='store_true',
                    help='mall_brands에 없는 제조사도 원문으로 저장 (기본: 고신뢰만)')
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    # 1) 네이버 스토어 목록
    cur.execute("SELECT site_name FROM mall_sites WHERE site_url LIKE '%naver.com%'")
    naver_stores = {r['site_name'] for r in cur.fetchall()}
    if args.source:
        naver_stores &= {args.source}
        if not naver_stores:
            print(f"'{args.source}'는 네이버 스토어 목록에 없음."); return
    print(f"[1/4] 네이버 스토어: {len(naver_stores)}개")

    # 2) mall_brands 정식표기 lookup ((store, brand_key) → raw_brand_name)
    cur.execute("SELECT mall_name, raw_brand_name, mall_brand_name_en FROM mall_brands WHERE is_active=1")
    key2write = {}
    for r in cur.fetchall():
        if r['mall_name'] not in naver_stores:
            continue
        write_val = (r['raw_brand_name'] or '').strip() or (r['mall_brand_name_en'] or '').strip()
        if not write_val:
            continue
        for src in (r['raw_brand_name'], r['mall_brand_name_en']):
            k = _brand_key(src)
            if k:
                key2write.setdefault((r['mall_name'], k), write_val)
    print(f"[2/4] mall_brands 정식표기 키: {len(key2write)}개")

    # 3) raw 스캔 + 분류
    fmt = ','.join(['%s'] * len(naver_stores))
    cur.execute(f"""SELECT id, source_site, brand_name_en, raw_json_data
                    FROM raw_scraped_data WHERE source_site IN ({fmt})""", tuple(naver_stores))
    rows = cur.fetchall()
    print(f"[3/4] raw 행 스캔: {len(rows)}건")

    mapped_fix = []    # (id, store, old, new, mfr) — 고신뢰 자동교정
    unmapped_fix = []  # 카탈로그 없는 브랜드 (검토)
    hangul_skip = []   # 한글 제조사 (건드림X)
    no_mfr = 0
    for r in rows:
        try:
            mfr = ((json.loads(r['raw_json_data']) or {}).get('manufacturer', '') or '').strip()
        except Exception:
            mfr = ''
        old = (r['brand_name_en'] or '').strip()
        if not mfr or not _brand_key(mfr):
            no_mfr += 1; continue
        if _same_brand(old, mfr):
            continue  # 정상(동일/같은 계열)
        if _has_hangul(mfr):
            hangul_skip.append((r['id'], r['source_site'], old, mfr)); continue
        canon = key2write.get((r['source_site'], _brand_key(mfr)))
        if canon:
            mapped_fix.append((r['id'], r['source_site'], old, canon, mfr))
        else:
            unmapped_fix.append((r['id'], r['source_site'], old, mfr, mfr))

    # 4) 게시 상태 교차
    pub_state = {}
    all_ids = [m[0] for m in mapped_fix + unmapped_fix]
    for i in range(0, len(all_ids), 1000):
        chunk = all_ids[i:i+1000]
        f2 = ','.join(['%s'] * len(chunk))
        cur.execute(f"""SELECT raw_data_id, MAX(is_published) pub FROM ace_products
                        WHERE raw_data_id IN ({f2}) GROUP BY raw_data_id""", tuple(chunk))
        for r in cur.fetchall():
            pub_state[r['raw_data_id']] = int(r['pub'] or 0)

    def buckets(lst):
        p = sum(1 for m in lst if pub_state.get(m[0]) == 1)
        u = sum(1 for m in lst if pub_state.get(m[0]) == 0)
        n = sum(1 for m in lst if m[0] not in pub_state)
        return p, u, n

    # ---- 리포트 ----
    print(f"\n{'='*60}")
    print(f"■ [고신뢰] 자동교정 대상(완전히 다른 브랜드, mall_brands에 있음): {len(mapped_fix)}건")
    if mapped_fix:
        p, u, n = buckets(mapped_fix)
        print(f"    └ 게시완료 {p} / 미게시 {u} / 미변환 {n}")
        for s, c in Counter(m[1] for m in mapped_fix).most_common():
            print(f"      {s}: {c}")
        print("    샘플:")
        for rid, store, old, new, mfr in mapped_fix[:10]:
            tag = 'GESI' if pub_state.get(rid) == 1 else ('unpub' if pub_state.get(rid) == 0 else 'no-ace')
            print(f"      [{store}/{tag}] {old!r} → {new!r}")

    print(f"\n■ [검토] 카탈로그에 없는 제조사(자동교정 제외): {len(unmapped_fix)}건")
    if unmapped_fix:
        for b, c in Counter(m[3] for m in unmapped_fix).most_common(15):
            print(f"      {b}: {c}")

    print(f"\n■ [무시] 한글/지저분 제조사(신뢰X): {len(hangul_skip)}건")
    if hangul_skip:
        for b, c in Counter(m[3] for m in hangul_skip).most_common(10):
            print(f"      {b}: {c}")
    print(f"\n■ manufacturer 없어 판단 제외: {no_mfr}건")
    print('='*60)

    # ---- 적용 ----
    if not args.apply:
        print(f"\n[DRY-RUN] 실제 교정: python {os.path.basename(sys.argv[0])} --apply")
        conn.close(); return

    targets = list(mapped_fix)
    if args.include_unmapped:
        targets += unmapped_fix
    if not args.include_published:
        before = len(targets)
        targets = [m for m in targets if pub_state.get(m[0]) != 1]
        if before - len(targets):
            print(f"\n  (게시완료 {before-len(targets)}건 제외 — --include-published로 포함 가능)")

    print(f"\n>>> --apply: raw_scraped_data.brand_name_en UPDATE {len(targets)}건")
    BATCH, done = 200, 0
    for i in range(0, len(targets), BATCH):
        chunk = targets[i:i+BATCH]
        for attempt in range(3):
            try:
                for rid, _, _, new, _ in chunk:
                    cur.execute("UPDATE raw_scraped_data SET brand_name_en=%s, updated_at=NOW() WHERE id=%s",
                                (new, rid))
                conn.commit(); break
            except pymysql.err.OperationalError as e:
                print(f"  DB 끊김({e.args[0]}) — 재연결 재시도 {attempt+1}/3")
                time.sleep(2); conn.ping(reconnect=True); cur = conn.cursor()
        else:
            print("  3회 실패 — 중단(멱등하니 재실행 가능)"); break
        done += len(chunk); print(f"  진행: {done}/{len(targets)}")
    print(">>> 완료\n다음 단계:")
    print("  1) 미게시 ace 전파:  python reupdate_ace_brand_name.py --apply")
    print("  2) 게시완료 상품: 재브랜드 재등록 절차(별도)")
    conn.close()


if __name__ == '__main__':
    main()
