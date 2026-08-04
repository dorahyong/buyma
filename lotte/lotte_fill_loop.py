# -*- coding: utf-8 -*-
"""롯데 Fill 루프 — 번역 → 이미지 → 등록을 반복해 빈 슬롯을 채운다.

동작:
  매 사이클: 게시중(is_published=1) 개수를 보고
    - TARGET 이상이면 → Fill 중지(슬롯 참, Swap 단계). 대기 후 재확인.
    - 여유 있으면 → 번역(price된 것) → 이미지(등록가능분) → 등록(여유분만큼) → 대기.
  각 단계는 멱등(이미 된 건 스킵)이라, PRICE가 계속 쌓는 신규분만 자연히 처리된다.

전제: PRICE(buyma_lowest_price_collector --source lotte)는 별도로 계속 돌고 있어야 후보가 늘어난다.
      WARP 켜져 있어야 함(register PS API는 무관하나, 같은 환경 가정).

사용:
    set USE_LISTING_AUTHORITY=1   (내부에서도 세팅함)
    python lotte_fill_loop.py                 # 기본 target 80000, 간격 1800초
    python lotte_fill_loop.py --target 80000 --interval 1800
    python lotte_fill_loop.py --once          # 한 사이클만(테스트)
"""
import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
load_dotenv(os.path.join(BASE, '.env'), override=True)

DB = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)),
          user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
          database=os.getenv('DB_NAME'), charset='utf8mb4', connect_timeout=10, read_timeout=30)

ENV = dict(os.environ, USE_LISTING_AUTHORITY='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def published_count():
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM buyma_listings WHERE is_active=1 AND is_published=1")
            return cur.fetchone()[0]
    finally:
        conn.close()


def run_stage(cmd, name):
    log(f"  ▶ {name} 시작: {' '.join(os.path.basename(c) if c.endswith('.py') else c for c in cmd)}")
    try:
        r = subprocess.run(cmd, cwd=BASE, env=ENV)
        ok = (r.returncode == 0)
        log(f"  ◀ {name} 종료 (exit={r.returncode}, {'OK' if ok else '실패'})")
        return ok
    except Exception as e:
        log(f"  ✗ {name} 예외: {e}")
        return False


def one_cycle(target):
    pub = published_count()
    room = target - pub
    log(f"=== 사이클: 게시중 {pub:,} / 목표 {target:,} / 여유 {room:,} ===")
    if room <= 0:
        log("  슬롯 참(게시중 >= 목표) → Fill 중지. (여기서부터 Swap 단계 필요)")
        return False  # 더 채울 것 없음

    # 1) 번역 (price된 것만) — 실패해도 이미지/등록은 이미 번역된 분으로 진행 가능하니 계속
    run_stage([PY, os.path.join('okmall', 'convert_to_japanese_gemini.py'),
               '--source', 'lotte', '--price-checked-only'], '번역')
    # 2) 이미지 (등록가능=번역된 것만 업로드; 0af87bc 필터)
    run_stage([PY, os.path.join('okmall', 'r2_image_uploader.py'), '--source', 'lotte'], '이미지')
    # 3) 등록 — 여유분(room)만큼만 (8만 넘지 않게)
    run_stage([PY, os.path.join('okmall', 'reconcile_runner.py'),
               '--mode', 'auto', '--scope', 'new', '--source', 'lotte',
               '--limit', str(room), '--execute', '--confirm-live'], '등록')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', type=int, default=80000, help='게시중 목표 슬롯(이 이상이면 Fill 중지)')
    ap.add_argument('--interval', type=int, default=1800, help='사이클 간 대기(초)')
    ap.add_argument('--once', action='store_true', help='한 사이클만(테스트)')
    args = ap.parse_args()

    log("=" * 60)
    log(f"롯데 Fill 루프 시작 (target={args.target:,}, interval={args.interval}s, once={args.once})")
    log("=" * 60)
    try:
        while True:
            try:
                more = one_cycle(args.target)
            except Exception as e:
                log(f"사이클 예외(다음 사이클 계속): {e}")
                more = True
            if args.once:
                log("--once → 1회 종료")
                break
            log(f"다음 사이클까지 {args.interval}s 대기...")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log("중단(Ctrl+C) — 루프 종료")


if __name__ == '__main__':
    main()
