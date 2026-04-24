# -*- coding: utf-8 -*-
"""네이버 스토어별 collector 순차 실행

순서 (예상 상품 수):
  premiumsneakers : 1,082
  fabstyle        : 1,845
  loutique        : 912
  t1global        : 1,213
  vvano           : 370
  veroshopmall    : 3,319
"""
import os
import sys
import subprocess
from datetime import datetime

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'premiumsneakers', 'premiumsneakers_collector.py')

SOURCES = [
    'premiumsneakers',
    'fabstyle',
    'loutique',
    't1global',
    'vvano',
    'veroshopmall',
]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    extra_args = sys.argv[1:]  # e.g. --skip-existing
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    log("=" * 60)
    log(f"네이버 collector 순차 실행 시작 ({len(SOURCES)}개)")
    log("=" * 60)

    for i, src in enumerate(SOURCES, 1):
        log(f"\n[{i}/{len(SOURCES)}] {src} 시작")
        cmd = [sys.executable, '-X', 'utf8', SCRIPT, '--source', src] + extra_args
        rc = subprocess.run(cmd, env=env).returncode
        if rc != 0:
            log(f"[{i}/{len(SOURCES)}] {src} 종료 코드 {rc} (오류) — 다음으로 진행")
        else:
            log(f"[{i}/{len(SOURCES)}] {src} 완료")

    log("=" * 60)
    log("전체 종료")
    log("=" * 60)


if __name__ == '__main__':
    main()
