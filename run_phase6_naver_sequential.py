# -*- coding: utf-8 -*-
"""
네이버 Phase 6 (Stock) 을 mall 별로 순차 실행

run_daily_naver.py --phase 6 만 실행하면 네이버 캡챠가 떠서 제대로 안 도는 문제 때문에,
--source <mall> 을 붙여 mall 하나씩 순차로(하나 끝나면 다음 거) 실행한다.

사용법:
    python run_phase6_naver_sequential.py            # 전체 순차 실행
    python run_phase6_naver_sequential.py --dry-run  # 실행할 명령만 출력
"""

import os
import sys
import io
import subprocess
import argparse
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 요청한 순서 그대로
SOURCES = [
    'premiumsneakers', 'fabstyle', 'loutique', 't1global',
    'vvano', 'veroshopmall', 'dmont', 'tuttobene', 'thefactor2',
    'carpi', 'joharistore', 'maniaon', 'bblue',
    'euroline', 'unico', 'kometa', 'larlashoes', 'thegrande', 'upset', 'luxlimit', 'pano'
]


def main():
    parser = argparse.ArgumentParser(description='네이버 Phase 6 Stock 을 mall 별로 순차 실행')
    parser.add_argument('--dry-run', action='store_true', help='실행할 명령만 출력')
    args = parser.parse_args()

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    total = len(SOURCES)
    results = []

    print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] Phase 6 순차 실행 시작 — 총 {total}개 mall')
    print('=' * 70)

    for idx, source in enumerate(SOURCES, 1):
        cmd = [sys.executable, os.path.join(BASE_DIR, 'run_daily_naver.py'),
               '--phase', '6', '--source', source]

        print(f'\n[{idx}/{total}] {source}')
        print(f'  $ {" ".join(cmd)}')

        if args.dry_run:
            continue

        start = datetime.now()
        proc = subprocess.run(cmd, cwd=BASE_DIR, env=env)
        elapsed = (datetime.now() - start).total_seconds()

        status = 'OK' if proc.returncode == 0 else f'FAIL(rc={proc.returncode})'
        print(f'  -> {status} ({elapsed:.0f}s)')
        results.append((source, proc.returncode, elapsed))

    if not args.dry_run:
        print('\n' + '=' * 70)
        print(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] 완료')
        ok = sum(1 for _, rc, _ in results if rc == 0)
        fail = [s for s, rc, _ in results if rc != 0]
        print(f'  성공 {ok}/{total}')
        if fail:
            print(f'  실패: {", ".join(fail)}')


if __name__ == '__main__':
    main()
