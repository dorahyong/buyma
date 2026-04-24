# -*- coding: utf-8 -*-
"""
네이버 11개 mall 일일 자동화

대상 mall:
  smartstore (브랜드 리스트):     premiumsneakers, fabstyle, loutique, t1global, vvano, veroshopmall
  smartstore (전체상품 URL):      dmont, tuttobene, thefactor2
  brandstore (brand.naver.com):   carpi, joharistore

Phase 1: Collector 직렬 1→11 (네이버 캡챠 회피, 쿠키 공유)
Phase 2: Converter 순차 → Dedup
Phase 3: Price(4 workers) + Image(4 workers)  2트랙 병렬
Phase 4: Register(4 workers 병렬)
Phase 5: Stock (11 mall 공용 1개 스크립트, Register 결과 반영)

Stock이 Register 뒤에 오는 이유:
  - Register 결과로 새로 is_published=1 된 상품을 Stock이 반영 가능
  - Stock은 은등록된 상품만 대상이라 Register와 겹치지 않음

실행 전제:
  - WARP OFF (네이버 DNS 차단 회피)
  - naver/naver_cookies.json 사전 로그인
      → python naver/premiumsneakers/premiumsneakers_collector.py --login

사용법:
    python run_daily_naver.py                     # 전체 실행
    python run_daily_naver.py --phase 2           # Phase 2부터
    python run_daily_naver.py --phase 5           # Stock만
    python run_daily_naver.py --dry-run           # 명령만 출력
"""

import os
import sys
import io
import subprocess
import argparse
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# =====================================================
# 설정
# =====================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES = [
    'premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall',
    'dmont', 'tuttobene', 'thefactor2',
    'carpi', 'joharistore',
]

# collector 분류
BRAND_COLLECTOR = 'naver/premiumsneakers/premiumsneakers_collector.py'
CATEGORY_COLLECTOR = 'naver/premiumsneakers/premiumsneakers_category_collector.py'
BRANDSTORE_COLLECTOR = 'naver/premiumsneakers/brand_store_collector.py'

COLLECTOR_MAP = {
    'premiumsneakers': BRAND_COLLECTOR,
    'fabstyle':        BRAND_COLLECTOR,
    'loutique':        BRAND_COLLECTOR,
    't1global':        BRAND_COLLECTOR,
    'vvano':           BRAND_COLLECTOR,
    'veroshopmall':    BRAND_COLLECTOR,
    'dmont':           CATEGORY_COLLECTOR,
    'tuttobene':       CATEGORY_COLLECTOR,
    'thefactor2':      CATEGORY_COLLECTOR,
    'carpi':           BRANDSTORE_COLLECTOR,
    'joharistore':     BRANDSTORE_COLLECTOR,
}

# 공용 스크립트
CONVERTER_SCRIPT = 'kasina/raw_to_converter_kasina.py'
DEDUP_SCRIPT = 'okmall/dedup_corrector.py'
PRICE_SCRIPT = 'okmall/buyma_lowest_price_collector.py'
IMAGE_SCRIPT = 'okmall/r2_image_uploader.py'
REGISTER_SCRIPT = 'okmall/buyma_new_product_register.py'
NAVER_STOCK_SCRIPT = 'naver/stock_price_synchronizer_naver.py'

# 병렬 워커 수
CONVERT_WORKERS = 4
PRICE_WORKERS = 4
IMAGE_WORKERS = 4
REGISTER_WORKERS = 4

# =====================================================
# 유틸리티
# =====================================================

log_lock = threading.Lock()


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_lock:
        print(f"[{timestamp}] [{level}] {message}", flush=True)


def run_script(script_path: str, extra_args: list = None, dry_run: bool = False) -> int:
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
    env['PYTHONUTF8'] = '1'

    result = subprocess.run(cmd, env=env, encoding='utf-8')
    if result.returncode != 0:
        log(f"  종료 코드: {result.returncode}", "ERROR")
    return result.returncode


def run_parallel(tasks: list, max_workers: int) -> dict:
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


# =====================================================
# Phase 1: Collector 직렬 (1→11, 네이버 캡챠 회피)
# =====================================================

def phase1_collect(dry_run: bool = False):
    log("=" * 60)
    log(f"Phase 1: Collector 직렬 실행 (1→11, 전 {len(SOURCES)}개)")
    log("=" * 60)

    for i, src in enumerate(SOURCES, 1):
        log(f"\n[{i}/{len(SOURCES)}] {src} (collector: {COLLECTOR_MAP[src].split('/')[-1]})")
        # 모든 네이버 collector가 --source, --skip-existing 지원
        rc = run_script(COLLECTOR_MAP[src], ['--source', src, '--skip-existing'], dry_run=dry_run)
        if rc != 0:
            log(f"  {src} collector 실패 (rc={rc}) — 다음으로 진행", "WARNING")
        else:
            log(f"  {src} collector 완료")

    log("\nPhase 1 완료")


# =====================================================
# Phase 2: Converter 순차 → Dedup
# =====================================================

def phase2_convert_dedup(dry_run: bool = False):
    log("=" * 60)
    log(f"Phase 2: Converter ({CONVERT_WORKERS} workers 병렬) + Dedup")
    log("=" * 60)

    # Converter: source-site 단위 병렬 (source 다르면 같은 데이터 안 건드려서 안전)
    def run_convert_for(src, dry_run):
        return run_script(CONVERTER_SCRIPT,
                          ['--source-site', src, '--skip-translation'],
                          dry_run=dry_run)

    tasks = [(src, run_convert_for, (src, dry_run)) for src in SOURCES]
    results = run_parallel(tasks, max_workers=CONVERT_WORKERS)
    for src, rc in results.items():
        if rc != 0:
            log(f"  Convert {src} 실패 (rc={rc})", "WARNING")

    # Dedup (전체 convert 완료 후 1회)
    log("\n  Dedup 실행 (전체 convert 완료 후)")
    run_script(DEDUP_SCRIPT, dry_run=dry_run)

    log("Phase 2 완료")


# =====================================================
# Phase 3: Price(병렬) + Image(병렬) 2트랙 병렬
# =====================================================

def phase3_price_image(dry_run: bool = False):
    log("=" * 60)
    log("Phase 3: Price + Image (2트랙 병렬)")
    log("=" * 60)

    def run_price_for(src, dry_run):
        return run_script(PRICE_SCRIPT, ['--source', src, '--new-only'], dry_run=dry_run)

    def run_image_for(src, dry_run):
        return run_script(IMAGE_SCRIPT, ['--source', src], dry_run=dry_run)

    def track_price(dry_run):
        log(f"  [Track Price] 시작 ({PRICE_WORKERS} workers)")
        tasks = [(src, run_price_for, (src, dry_run)) for src in SOURCES]
        results = run_parallel(tasks, max_workers=PRICE_WORKERS)
        for src, rc in results.items():
            if rc != 0:
                log(f"    Price {src} 실패 (rc={rc})", "WARNING")
        log(f"  [Track Price] 완료")

    def track_image(dry_run):
        log(f"  [Track Image] 시작 ({IMAGE_WORKERS} workers)")
        tasks = [(src, run_image_for, (src, dry_run)) for src in SOURCES]
        results = run_parallel(tasks, max_workers=IMAGE_WORKERS)
        for src, rc in results.items():
            if rc != 0:
                log(f"    Image {src} 실패 (rc={rc})", "WARNING")
        log(f"  [Track Image] 완료")

    # 2트랙 병렬 실행
    tasks = [
        ('price', track_price, (dry_run,)),
        ('image', track_image, (dry_run,)),
    ]
    run_parallel(tasks, max_workers=2)

    log("Phase 3 완료")


# =====================================================
# Phase 4: Register (병렬)
# =====================================================

def phase4_register(dry_run: bool = False):
    log("=" * 60)
    log(f"Phase 4: Register ({REGISTER_WORKERS} workers 병렬)")
    log("=" * 60)

    def run_register_for(src, dry_run):
        return run_script(REGISTER_SCRIPT, ['--source', src], dry_run=dry_run)

    tasks = [(src, run_register_for, (src, dry_run)) for src in SOURCES]
    results = run_parallel(tasks, max_workers=REGISTER_WORKERS)
    for src, rc in results.items():
        if rc != 0:
            log(f"  Register {src} 실패 (rc={rc})", "WARNING")

    log("Phase 4 완료")


# =====================================================
# Phase 5: Stock (11개 mall 공용 1 스크립트, Register 이후)
# =====================================================

def phase5_stock(dry_run: bool = False):
    log("=" * 60)
    log("Phase 5: Stock (11개 mall 공용 — Register 결과 반영)")
    log("=" * 60)

    # Playwright 단일 세션으로 내부 직렬. --source 없이 11개 전체.
    rc = run_script(NAVER_STOCK_SCRIPT, [], dry_run=dry_run)
    if rc != 0:
        log(f"  Stock 실패 (rc={rc})", "WARNING")

    log("Phase 5 완료")


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='네이버 11개 mall 일일 자동화')
    parser.add_argument('--phase', type=int, default=1, help='시작 Phase (1~5, 기본: 1)')
    parser.add_argument('--dry-run', action='store_true', help='실제 실행 없이 명령만 출력')
    args = parser.parse_args()

    start_time = datetime.now()
    log("=" * 60)
    log("네이버 일일 자동화 시작")
    log(f"  대상: {len(SOURCES)}개 mall — {', '.join(SOURCES)}")
    log(f"  시작 Phase: {args.phase}")
    log(f"  DRY-RUN: {args.dry_run}")
    log("  전제: WARP OFF + naver_cookies.json 사전 로그인")
    log("=" * 60)

    if args.phase <= 1:
        phase1_collect(args.dry_run)

    if args.phase <= 2:
        phase2_convert_dedup(args.dry_run)

    if args.phase <= 3:
        phase3_price_image(args.dry_run)

    if args.phase <= 4:
        phase4_register(args.dry_run)

    if args.phase <= 5:
        phase5_stock(args.dry_run)

    elapsed = datetime.now() - start_time
    hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    log("=" * 60)
    log(f"네이버 일일 자동화 완료 (소요: {hours}시간 {minutes}분 {seconds}초)")
    log("=" * 60)


if __name__ == "__main__":
    main()
