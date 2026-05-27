# -*- coding: utf-8 -*-
"""
mall_brands 수동 매핑 일괄 UPDATE

키: (mall_name, raw_brand_name) — mall_brand_url이 NULL인 행 대상
UPDATE 컬럼: buyma_brand_id, buyma_brand_name, is_active

사용법:
    python buyma_cleaners/update_mall_brands_manual.py            # dry-run
    python buyma_cleaners/update_mall_brands_manual.py --apply    # 실제 업데이트
"""

import os
import sys
import io
import argparse
import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

INPUT_TSV = os.path.join(os.path.dirname(__file__), 'mall_brands_manual_update.tsv')


def parse_tsv(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n').rstrip('\r')
            if not line:
                continue
            parts = line.split('\t')
            if parts[0] == 'mall_name':
                continue
            if len(parts) < 11:
                print(f"[SKIP 컬럼부족 {len(parts)}] {line[:80]}")
                continue
            mall_name, ko, _en, bid, bname = parts[0], parts[1], parts[2], parts[3], parts[4]
            active = parts[8]
            bid_val = None if bid == r'\N' else int(bid)
            bname_val = None if bname == r'\N' else bname
            active_val = int(active)
            rows.append((mall_name, ko, bid_val, bname_val, active_val))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='실제 UPDATE 실행')
    args = parser.parse_args()

    rows = parse_tsv(INPUT_TSV)
    print(f"입력 TSV: {len(rows)}건")

    conn = pymysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()

    stats = {'updated': 0, 'not_found': 0, 'multi_match': 0, 'no_change': 0}
    samples_updated = []
    samples_notfound = []
    samples_multi = []

    for mall_name, ko, bid, bname, active in rows:
        cur.execute(
            "SELECT buyma_brand_id, buyma_brand_name, is_active "
            "FROM mall_brands WHERE mall_name=%s AND raw_brand_name=%s",
            (mall_name, ko),
        )
        matched = cur.fetchall()
        c = len(matched)

        if c == 0:
            stats['not_found'] += 1
            if len(samples_notfound) < 10:
                samples_notfound.append(f"{mall_name}/{ko}")
            continue
        if c > 1:
            stats['multi_match'] += 1
            if len(samples_multi) < 10:
                samples_multi.append(f"{mall_name}/{ko} ({c}건)")

        cur_vals = matched[0]
        if (cur_vals['buyma_brand_id'] == bid
                and cur_vals['buyma_brand_name'] == bname
                and cur_vals['is_active'] == active):
            stats['no_change'] += 1
            continue

        if args.apply:
            cur.execute(
                "UPDATE mall_brands SET buyma_brand_id=%s, buyma_brand_name=%s, is_active=%s "
                "WHERE mall_name=%s AND raw_brand_name=%s",
                (bid, bname, active, mall_name, ko),
            )
            stats['updated'] += cur.rowcount
        else:
            stats['updated'] += c

        if len(samples_updated) < 5:
            samples_updated.append(
                f"{mall_name}/{ko}: id {cur_vals['buyma_brand_id']}→{bid}, "
                f"active {cur_vals['is_active']}→{active}"
            )

    if args.apply:
        conn.commit()
    conn.close()

    print("\n=== 샘플 (최대 5건) ===")
    for s in samples_updated:
        print(f"  [UPD] {s}")
    if samples_notfound:
        print(f"\n=== NOT FOUND (최대 10건) ===")
        for s in samples_notfound:
            print(f"  [NF]  {s}")
    if samples_multi:
        print(f"\n=== MULTI MATCH (최대 10건) ===")
        for s in samples_multi:
            print(f"  [MUL] {s}")

    print("\n=== 결과 ===")
    print(f"  UPDATE:    {stats['updated']}")
    print(f"  변경 없음: {stats['no_change']}")
    print(f"  못 찾음:   {stats['not_found']}")
    print(f"  중복 매칭: {stats['multi_match']}")
    print(f"  모드:      {'APPLY (commit됨)' if args.apply else 'DRY-RUN'}")


if __name__ == '__main__':
    main()
