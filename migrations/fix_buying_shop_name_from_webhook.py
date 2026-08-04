# -*- coding: utf-8 -*-
"""buyma_listings.buying_shop_name 을 바이마가 알려준 값으로 맞춘다.

왜:
  매입처 이름은 게시 후 바이마가 변경을 허용하지 않는다("変更できません").
  그런데 우리는 그동안 winner(가장 싼 소싱몰)가 바뀔 때마다 이 값을 덮어썼다.
  바이마 값은 등록 당시 그대로인데 우리 값만 바뀌어, 장부가 실제와 어긋났다.
  (2026-08-04 덮어쓰기 자체는 중단함 — resolve_merge / reconcile_ensure_group)

진실의 출처:
  buyma_listing_api_logs.api_response_json = 바이마가 웹훅으로 돌려준 상품 정보 원문.
  그 안의 buying_shop_name 이 바이마가 실제로 갖고 있는 값이다.
  (편집 불가 값이므로 웹훅이 오래됐어도 그 값은 여전히 유효하다)

한계:
  웹훅 기록이 있는 목록만 대조·보정할 수 있다. 기록이 없으면 바이마 값을 알 길이 없다.

사용:
    python migrations/fix_buying_shop_name_from_webhook.py            # 미리보기
    python migrations/fix_buying_shop_name_from_webhook.py --execute  # 백업 + 보정
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    c = pymysql.connect(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
                        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
                        database=os.getenv('DB_NAME'), charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor)
    cur = c.cursor()
    cur.execute("""SELECT g.buyma_listing_id lid, g.api_response_json j,
                          bl.buying_shop_name ours, bl.brand_name, bl.is_published, bl.status
                     FROM buyma_listing_api_logs g
                     JOIN buyma_listings bl ON bl.id = g.buyma_listing_id""")
    plan, no_val, same = [], 0, 0
    for r in cur.fetchall():
        try:
            theirs = (json.loads(r['j']) or {}).get('buying_shop_name')
        except Exception:
            theirs = None
        if not theirs:
            no_val += 1
            continue
        if (r['ours'] or '') == theirs:
            same += 1
            continue
        if r['status'] == 'deleted':
            # 삭제된 상품에 옛 값을 다시 적어두면, 나중에 되살릴 때 그 값이 나간다 → 건드리지 않음
            continue
        plan.append({'listing_id': r['lid'], 'before': r['ours'], 'after': theirs,
                     'brand_name': r['brand_name'], 'is_published': r['is_published'],
                     'status': r['status']})

    print(f"대조 가능: {same + len(plan):,}건  (일치 {same:,} / 어긋남 {len(plan):,})")
    print(f"웹훅에 매입처 값이 없어 판정 불가: {no_val:,}건")
    empties = sum(1 for p in plan if not p['before'])
    print(f"  어긋남 중 우리 값이 비어 있던 것: {empties:,}건")

    if not args.execute:
        print("\n(미리보기 — 아무것도 바꾸지 않음. 실제 반영은 --execute)")
        for p in plan[:5]:
            print(f"    listing={p['listing_id']}  {p['before']!r} → {p['after']!r}")
        c.close()
        return

    tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = os.path.join(BASE, 'migrations', f'fix_buying_shop_name_backup_{tag}.json')
    json.dump(plan, open(bak, 'w', encoding='utf-8'), ensure_ascii=False, indent=2, default=str)
    print(f"\n백업: {bak}")

    done = 0
    for p in plan:
        cur.execute("""UPDATE buyma_listings SET buying_shop_name=%s, updated_at=CURRENT_TIMESTAMP
                        WHERE id=%s""", (p['after'], p['listing_id']))
        done += cur.rowcount
    c.commit()
    print(f"반영 완료: {done:,}건")
    c.close()


if __name__ == '__main__':
    main()
