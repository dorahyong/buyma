# -*- coding: utf-8 -*-
"""buyma_listings 의 관리번호 두 칸이 어긋난 것을 바이마가 아는 값으로 맞춘다.

대상: is_published=1 이고 locked_reference_number <> reference_number
진실: buyma_listing_events 의 최신 reference_number (바이마가 웹훅으로 돌려준 값)
처리: reference_number, locked_reference_number 를 모두 그 값으로 통일

왜 필요한가:
  - 출품정지(재고 API)는 상품번호 없이 관리번호만으로 상품을 지목한다.
    번호가 틀리면 엉뚱한 상품에 나가거나 아무 데도 안 나간다.
  - 수정(EDIT)도 굳힌 사본을 우선 쓰므로 틀린 값이면 반영이 안 될 수 있다.

안전장치:
  - 진실을 못 찾은 행은 건드리지 않는다.
  - 바꿀 값이 다른 행에 이미 있으면(유니크 충돌) 그 행은 건너뛴다.
  - 실행 전 원본을 JSON 으로 백업한다.

사용:
    python migrations/fix_locked_ref_mismatch.py            # 미리보기
    python migrations/fix_locked_ref_mismatch.py --execute  # 백업 + 실제 반영
"""
import os
import sys
import json
import argparse
from datetime import datetime

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)


def conn():
    return pymysql.connect(
        host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true', help='실제 반영 (미지정 시 미리보기)')
    args = ap.parse_args()

    c = conn()
    cur = c.cursor()

    cur.execute("""
        SELECT id, buyma_product_id, reference_number, locked_reference_number
          FROM buyma_listings
         WHERE is_published = 1
           AND locked_reference_number IS NOT NULL
           AND locked_reference_number <> reference_number
    """)
    rows = cur.fetchall()
    print(f"어긋난 게시중 목록: {len(rows)}건")
    if not rows:
        c.close()
        return

    # 바이마가 돌려준 번호 (최신)
    pids = [r['buyma_product_id'] for r in rows if r['buyma_product_id']]
    truth = {}
    if pids:
        ph = ','.join(['%s'] * len(pids))
        cur.execute(f"""SELECT buyma_product_id, reference_number
                          FROM buyma_listing_events
                         WHERE buyma_product_id IN ({ph}) AND reference_number IS NOT NULL
                         ORDER BY event_at""", pids)
        for e in cur.fetchall():
            truth[e['buyma_product_id']] = e['reference_number']

    plan, skip = [], []
    for r in rows:
        t = truth.get(r['buyma_product_id'])
        if not t:
            skip.append((r['id'], '바이마 기록 없음'))
            continue
        if t not in (r['reference_number'], r['locked_reference_number']):
            skip.append((r['id'], '두 값 어느 쪽도 아님'))
            continue
        # 유니크 충돌 검사 (다른 행이 그 번호를 쓰고 있는가)
        cur.execute("SELECT id FROM buyma_listings WHERE reference_number=%s AND id<>%s", (t, r['id']))
        if cur.fetchone():
            skip.append((r['id'], '다른 목록이 그 번호를 쓰는 중'))
            continue
        plan.append({'id': r['id'], 'buyma_product_id': r['buyma_product_id'],
                     'before_ref': r['reference_number'],
                     'before_locked': r['locked_reference_number'],
                     'after': t,
                     'case': '현재값이 진짜' if t == r['reference_number'] else '굳힌사본이 진짜'})

    n_cur = sum(1 for p in plan if p['case'] == '현재값이 진짜')
    n_lock = len(plan) - n_cur
    print(f"  맞출 것: {len(plan)}건  (현재값이 진짜 {n_cur} / 굳힌사본이 진짜 {n_lock})")
    print(f"  건너뜀 : {len(skip)}건")
    for lid, why in skip[:10]:
        print(f"    - listing={lid}: {why}")

    if not args.execute:
        print("\n(미리보기 — 아무것도 바꾸지 않음. 실제 반영은 --execute)")
        for p in plan[:5]:
            print(f"    listing={p['id']} [{p['case']}] → {p['after']}")
        c.close()
        return

    tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = os.path.join(BASE, 'migrations', f'fix_locked_ref_backup_{tag}.json')
    with open(bak, 'w', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"\n백업: {bak}")

    done = 0
    for p in plan:
        cur.execute("""UPDATE buyma_listings
                          SET reference_number=%s, locked_reference_number=%s, updated_at=CURRENT_TIMESTAMP
                        WHERE id=%s""", (p['after'], p['after'], p['id']))
        done += cur.rowcount
    c.commit()
    print(f"반영 완료: {done}건")
    c.close()


if __name__ == '__main__':
    main()
