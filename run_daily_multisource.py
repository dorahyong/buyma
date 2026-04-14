# -*- coding: utf-8 -*-
"""
멀티소스 일일 자동화 (kasina, nextzennpack, labellusso, trendmecca)

Phase 1: Collector 4개 병렬 (--skip-existing)
Phase 2: Converter 4개 순차 → Dedup
Phase 3: Price(2+2) + Image(2+2) + Stock(2+2)  3트랙 병렬
Phase 4: Register(2+2)

예상 소요: 약 16~17시간

사용법:
    python run_daily_multisource.py                  # 전체 실행
    python run_daily_multisource.py --phase 2        # Phase 2부터 실행
    python run_daily_multisource.py --phase 3        # Phase 3부터 실행
    python run_daily_multisource.py --dry-run        # 테스트 (명령만 출력)
"""

import os
import sys
import subprocess
import argparse
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# =====================================================
# 설정
# =====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES = ['kasina', 'nextzennpack', 'labellusso', 'trendmecca']

# 2+2 병렬 그룹 (데이터 크기 균형: kasina 17k + nextzennpack 2k ≈ labellusso 8k + trendmecca 7k)
GROUP_A = ['kasina', 'nextzennpack']
GROUP_B = ['labellusso', 'trendmecca']

# 각 사이트별 스크립트 경로
COLLECTOR_SCRIPTS = {
    'kasina': 'kasina/kasina_collector.py',
    'nextzennpack': 'nextzennpack/nextzennpack_collector.py',
    'labellusso': 'labellusso/labellusso_collector.py',
    'trendmecca': 'trendmecca/trendmecca_collector.py',
}

STOCK_SCRIPTS = {
    'kasina': 'kasina/stock_price_synchronizer_kasina.py',
    'nextzennpack': 'nextzennpack/stock_price_synchronizer_nextzennpack.py',
    'labellusso': 'labellusso/stock_price_synchronizer_labellusso.py',
    'trendmecca': 'trendmecca/stock_price_synchronizer_trendmecca.py',
}

# 공용 스크립트
CONVERTER_SCRIPT = 'kasina/raw_to_converter_kasina.py'
DEDUP_SCRIPT = 'okmall/dedup_corrector.py'
PRICE_SCRIPT = 'okmall/buyma_lowest_price_collector.py'
IMAGE_SCRIPT = 'okmall/r2_image_uploader.py'
REGISTER_SCRIPT = 'okmall/buyma_new_product_register.py'

# =====================================================
# 유틸리티
# =====================================================

log_lock = threading.Lock()


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_lock:
        print(f"[{timestamp}] [{level}] {message}", flush=True)


def run_script(script_path: str, extra_args: list = None, dry_run: bool = False) -> int:
    """스크립트 실행"""
    full_path = os.path.join(BASE_DIR, script_path)
    cmd = [sys.executable, full_path]
    if extra_args:
        cmd.extend(extra_args)

    log(f"실행: {' '.join(cmd)}")

    if dry_run:
        log(f"  [DRY-RUN] 스킵")
        return 0

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    result = subprocess.run(cmd, env=env, encoding='utf-8')
    if result.returncode != 0:
        log(f"  종료 코드: {result.returncode}", "ERROR")
    return result.returncode


def run_parallel(tasks: list, max_workers: int = 2) -> dict:
    """여러 작업을 병렬 실행. tasks = [(name, func, args), ...]"""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for name, func, args in tasks:
            future = executor.submit(func, *args)
            futures[future] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                log(f"{name} 오류: {e}", "ERROR")
                results[name] = -1

    return results


def run_2plus2(run_func, dry_run: bool = False):
    """2+2 병렬 실행 (Group A → Group B)"""
    # Group A: 2개 병렬
    tasks_a = [(src, run_func, (src, dry_run)) for src in GROUP_A]
    results_a = run_parallel(tasks_a, max_workers=2)
    for src, rc in results_a.items():
        if rc != 0:
            log(f"  {src} 실패 (rc={rc})", "WARNING")

    # Group B: 2개 병렬
    tasks_b = [(src, run_func, (src, dry_run)) for src in GROUP_B]
    results_b = run_parallel(tasks_b, max_workers=2)
    for src, rc in results_b.items():
        if rc != 0:
            log(f"  {src} 실패 (rc={rc})", "WARNING")

    return {**results_a, **results_b}


# =====================================================
# Phase 실행 함수
# =====================================================

def phase1_collect(dry_run: bool = False):
    """Phase 1: Collector 4개 병렬"""
    log("=" * 60)
    log("Phase 1: Collector 4개 병렬 시작")
    log("=" * 60)

    def run_collector(src, dry_run):
        return run_script(COLLECTOR_SCRIPTS[src], ['--skip-existing'], dry_run=dry_run)

    tasks = [(src, run_collector, (src, dry_run)) for src in SOURCES]
    results = run_parallel(tasks, max_workers=4)

    for src, rc in results.items():
        status = "완료" if rc == 0 else f"실패(rc={rc})"
        log(f"  {src} collector: {status}")

    log("Phase 1 완료")


def phase2_convert_dedup(dry_run: bool = False):
    """Phase 2: Converter 4개 순차 → Dedup"""
    log("=" * 60)
    log("Phase 2: Converter + Dedup 시작")
    log("=" * 60)

    # Converter: 순차 실행 (source-site별)
    for src in SOURCES:
        log(f"  Convert: {src}")
        rc = run_script(CONVERTER_SCRIPT, ['--source-site', src, '--skip-translation'], dry_run=dry_run)
        if rc != 0:
            log(f"  {src} convert 실패", "WARNING")

    # Dedup
    log("  Dedup 실행")
    run_script(DEDUP_SCRIPT, dry_run=dry_run)

    log("Phase 2 완료")


def phase3_price_image_stock(dry_run: bool = False):
    """Phase 3: Price(2+2) + Image(2+2) + Stock(2+2) 3트랙 병렬"""
    log("=" * 60)
    log("Phase 3: Price + Image + Stock (3트랙 병렬) 시작")
    log("=" * 60)

    def run_price(src, dry_run):
        return run_script(PRICE_SCRIPT, ['--source', src, '--new-only'], dry_run=dry_run)

    def run_image(src, dry_run):
        return run_script(IMAGE_SCRIPT, ['--source', src], dry_run=dry_run)

    def run_stock(src, dry_run):
        return run_script(STOCK_SCRIPTS[src], dry_run=dry_run)

    def track_price(dry_run):
        log("  [Track Price] 시작")
        run_2plus2(run_price, dry_run)
        log("  [Track Price] 완료")

    def track_image(dry_run):
        log("  [Track Image] 시작")
        run_2plus2(run_image, dry_run)
        log("  [Track Image] 완료")

    def track_stock(dry_run):
        log("  [Track Stock] 시작")
        run_2plus2(run_stock, dry_run)
        log("  [Track Stock] 완료")

    # 3트랙 병렬 실행
    tasks = [
        ('price', track_price, (dry_run,)),
        ('image', track_image, (dry_run,)),
        ('stock', track_stock, (dry_run,)),
    ]
    run_parallel(tasks, max_workers=3)

    log("Phase 3 완료")


def phase4_register(dry_run: bool = False):
    """Phase 4: Register 2+2"""
    log("=" * 60)
    log("Phase 4: Register (2+2) 시작")
    log("=" * 60)

    def run_register(src, dry_run):
        return run_script(REGISTER_SCRIPT, ['--source', src], dry_run=dry_run)

    run_2plus2(run_register, dry_run)

    log("Phase 4 완료")


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='멀티소스 일일 자동화')
    parser.add_argument('--phase', type=int, default=1, help='시작 Phase (1~4, 기본: 1)')
    parser.add_argument('--dry-run', action='store_true', help='실제 실행 없이 명령만 출력')
    args = parser.parse_args()

    start_time = datetime.now()
    log("=" * 60)
    log("멀티소스 일일 자동화 시작")
    log(f"  대상: {', '.join(SOURCES)}")
    log(f"  시작 Phase: {args.phase}")
    log(f"  DRY-RUN: {args.dry_run}")
    log("=" * 60)

    if args.phase <= 1:
        phase1_collect(args.dry_run)

    if args.phase <= 2:
        phase2_convert_dedup(args.dry_run)

    if args.phase <= 3:
        phase3_price_image_stock(args.dry_run)

    if args.phase <= 4:
        phase4_register(args.dry_run)

    elapsed = datetime.now() - start_time
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    log("=" * 60)
    log(f"멀티소스 일일 자동화 완료 (소요: {hours}시간 {minutes}분 {seconds}초)")
    log("=" * 60)


if __name__ == "__main__":
    main()
