# -*- coding: utf-8 -*-
"""신규 편입 10개 mall stock+price 동기화 순차 실행 (lovegrande 제외)

대상:
  smartstore: maniaon, bblue, euroline, unico, kometa,
              larlashoes, thegrande, upset, luxlimit, pano

stock_price_synchronizer_naver.py --source <mall> 을 10개 순차 호출.
실패해도 다음 mall로 진행 (rc 로그만 남김).

실행 전제:
  - naver_cookies.json 사전 로그인

사용법:
    python naver/run_stock_10_new.py
    python naver/run_stock_10_new.py --dry-run
"""
import os
import sys
import subprocess
from datetime import datetime

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'stock_price_synchronizer_naver.py')

SOURCES = [
    'maniaon',
    'bblue',
    'euroline',
    'unico',
    'kometa',
    'larlashoes',
    'thegrande',
    'upset',
    'luxlimit',
    'pano',
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    extra_args = sys.argv[1:]  # e.g. --dry-run
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    log("=" * 60)
    log(f"네이버 신규 10개 mall stock 순차 실행 시작")
    log(f"  대상: {', '.join(SOURCES)}")
    if extra_args:
        log(f"  추가 인자: {' '.join(extra_args)}")
    log("=" * 60)

    start = datetime.now()
    results = {}

    for i, src in enumerate(SOURCES, 1):
        log(f"\n[{i}/{len(SOURCES)}] {src} 시작")
        cmd = [sys.executable, '-X', 'utf8', SCRIPT, '--source', src] + extra_args
        rc = subprocess.run(cmd, env=env).returncode
        results[src] = rc
        if rc != 0:
            log(f"[{i}/{len(SOURCES)}] {src} 종료 코드 {rc} (오류) — 다음으로 진행")
        else:
            log(f"[{i}/{len(SOURCES)}] {src} 완료")

    elapsed = datetime.now() - start
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    log("=" * 60)
    log(f"전체 종료 (소요: {hours}시간 {minutes}분 {seconds}초)")
    failed = [s for s, rc in results.items() if rc != 0]
    if failed:
        log(f"  실패 {len(failed)}개: {', '.join(failed)}")
    else:
        log(f"  전체 성공 ({len(SOURCES)}/{len(SOURCES)})")
    log("=" * 60)


if __name__ == '__main__':
    main()
