# -*- coding: utf-8 -*-
"""
신규 수집 10개 mall collector 순차 실행 (--skip-existing)

대상 (전부 smartstore.naver.com, category_collector 사용):
  maniaon, bblue, euroline, unico, kometa,
  larlashoes, thegrande, upset, luxlimit, pano

처리: 한 mall이 끝나면 다음 mall 시작 (직렬, 네이버 캡챠 회피)
실제 작업은 naver/premiumsneakers/premiumsneakers_category_collector.py --source <mall> --skip-existing 호출

--skip-existing 동작 (수정됨):
  - raw에 없음 → 수집
  - raw에 있음 + 바이마 미등록(is_published=0) → 재수집
  - raw에 있음 + 바이마 등록(is_published=1) → 스킵

실행 전제:
  - naver/naver_cookies.json 사전 로그인

사용법:
    python run_naver_collect_new10.py            # 10개 순차 수집
    python run_naver_collect_new10.py --dry-run  # 명령만 출력
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
COLLECTOR_SCRIPT = os.path.join(
    BASE_DIR, 'naver', 'premiumsneakers', 'premiumsneakers_category_collector.py'
)

NEW_MALLS = [
    'maniaon', 'bblue', 'euroline', 'unico', 'kometa',
    'larlashoes', 'thegrande', 'upset', 'luxlimit', 'pano',
]


def log(msg: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}", flush=True)


def run_collect_for(src: str, dry_run: bool) -> int:
    cmd = [sys.executable, '-X', 'utf8', COLLECTOR_SCRIPT, '--source', src, '--skip-existing']
    log(f"실행: {' '.join(cmd)}")
    if dry_run:
        log("  [DRY-RUN] 스킵")
        return 0

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    result = subprocess.run(cmd, env=env, encoding='utf-8')
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description='신규 10개 mall collector 순차 실행 (--skip-existing)')
    parser.add_argument('--dry-run', action='store_true', help='실제 실행 없이 명령만 출력')
    args = parser.parse_args()

    start_time = datetime.now()
    log("=" * 60)
    log(f"신규 10개 mall collector 시작 (직렬 처리, --skip-existing)")
    log(f"  대상: {', '.join(NEW_MALLS)}")
    log(f"  DRY-RUN: {args.dry_run}")
    log(f"  전제: naver_cookies.json 사전 로그인")
    log("=" * 60)

    summary = []
    for i, src in enumerate(NEW_MALLS, 1):
        log(f"\n[{i}/{len(NEW_MALLS)}] {src} 시작")
        rc = run_collect_for(src, args.dry_run)
        status = "완료" if rc == 0 else f"실패(rc={rc})"
        log(f"[{i}/{len(NEW_MALLS)}] {src} {status}")
        summary.append((src, rc))

    elapsed = datetime.now() - start_time
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    log("\n" + "=" * 60)
    log(f"전체 완료 (소요: {hours}시간 {minutes}분 {seconds}초)")
    log("=" * 60)
    for src, rc in summary:
        status = "완료" if rc == 0 else f"실패(rc={rc})"
        log(f"  {src:15s} {status}")


if __name__ == '__main__':
    main()
