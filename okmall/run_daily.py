# -*- coding: utf-8 -*-
"""
일일 자동화 실행 스크립트

1. orchestrator.py 실행 (신규 상품 등록 파이프라인)
2. stock_price_synchronizer.py 실행 (재고/가격 동기화)

사용법:
    python run_daily.py
    python run_daily.py --brand NIKE
    python run_daily.py --sync-only          # 동기화만 실행
    python run_daily.py --register-only      # 등록만 실행
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)

def run_script(script_name: str, extra_args: list = None) -> int:
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, script_name)]
    if extra_args:
        cmd.extend(extra_args)

    log(f"실행: {' '.join(cmd)}")
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    result = subprocess.run(cmd, env=env)
    return result.returncode

def main():
    import argparse
    parser = argparse.ArgumentParser(description='일일 자동화 실행')
    parser.add_argument('--brand', type=str, help='특정 브랜드만')
    parser.add_argument('--sync-only', action='store_true', help='동기화만 실행')
    parser.add_argument('--register-only', action='store_true', help='등록만 실행')
    args, unknown = parser.parse_known_args()

    log("=" * 60)
    log("일일 자동화 시작")
    log("=" * 60)

    # 1. orchestrator.py
    if not args.sync_only:
        log("[1/2] 신규 상품 등록 파이프라인 시작")
        orch_args = []
        if args.brand:
            orch_args += ['--brand', args.brand]
        orch_args += unknown

        rc = run_script('orchestrator.py', orch_args)
        if rc != 0:
            log(f"orchestrator.py 종료 코드: {rc} (오류 발생)")
        else:
            log("orchestrator.py 완료")
    else:
        log("[1/2] 등록 스킵 (--sync-only)")

    # 2. stock_price_synchronizer.py
    if not args.register_only:
        log("[2/2] 재고/가격 동기화 시작")
        sync_args = []
        if args.brand:
            sync_args += ['--brand', args.brand]

        rc = run_script('stock_price_synchronizer.py', sync_args)
        if rc != 0:
            log(f"stock_price_synchronizer.py 종료 코드: {rc} (오류 발생)")
        else:
            log("stock_price_synchronizer.py 완료")
    else:
        log("[2/2] 동기화 스킵 (--register-only)")

    log("=" * 60)
    log("일일 자동화 종료")
    log("=" * 60)

if __name__ == "__main__":
    main()
