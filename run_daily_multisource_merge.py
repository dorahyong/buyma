# -*- coding: utf-8 -*-
"""
멀티소스 일일 자동화 — MERGE cutover 사본 (run_daily_multisource.py 의 reconcile 버전)

★ cutover 1차 변경점 (원본 대비):
  - Phase 2: 옛 dedup_corrector(싼몰 죽이기) 제거 → 중복처리는 reconcile 가 내재 처리
  - Phase 4: 옛 register → reconcile_runner --mode auto --scope new (미등록 그룹만, ensure_group 즉석 merge)

★ cutover 2차 변경점 (방식 A):
  - Phase 3 Stock: 옛 *.py(몰별 직접 push) → *_merge.py (refresh-only).
                   각 stock 은 몰 재수집만 하고, run() 끝에 reconcile 이 그 회차 그룹만
                   BUYMA push (옵션합침+싼몰+수정/삭제, --scope published 내장). 그룹락으로 multi-PC 안전.

대상: kasina, nextzennpack, labellusso, 9tems, brickmansion, loromoda, milaneez, maisonparco (총 8개)

Phase 1: Collector 병렬 (--skip-existing) + 카테고리 단계(CATEGORY_FILL 해당 몰만)
Phase 2: Converter 순차(--source-site)  [Dedup 제거]
Phase 3: Price → Translate → Image → Stock 트랙 순차  [Stock = refresh-only + reconcile push]
Phase 4: 신규 등록 = reconcile auto --scope new

※ 카테고리: 9tems·기존 3몰은 수집 중 인라인. brickmansion/loromoda/maisonparco는 --categories,
   milaneez는 --map-categories 단계가 수집 직후 자동 실행됨(CATEGORY_FILL).

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

SOURCES = ['kasina', 'nextzennpack', 'labellusso',
           '9tems', 'brickmansion', 'loromoda', 'milaneez', 'maisonparco']

# 각 사이트별 스크립트 경로
COLLECTOR_SCRIPTS = {
    'kasina': 'kasina/kasina_collector.py',
    'nextzennpack': 'nextzennpack/nextzennpack_collector.py',
    'labellusso': 'labellusso/labellusso_collector.py',
    '9tems': '9tems/9tems_collector.py',
    'brickmansion': 'brickmansion/brickmansion_collector.py',
    'loromoda': 'loromoda/loromoda_collector.py',
    'milaneez': 'milaneez/milaneez_collector.py',
    'maisonparco': 'maisonparco/maisonparco_collector.py',
}

# [MERGE 2차] stock = refresh-only(_merge.py) → run() 끝에 reconcile 가 BUYMA push
#   (옵션합침+싼몰+수정/삭제). 옛 *.py(몰별 직접 push)는 보존, 여기서만 _merge 호출.
STOCK_SCRIPTS = {
    'kasina': 'kasina/stock_price_synchronizer_kasina_merge.py',
    'nextzennpack': 'nextzennpack/stock_price_synchronizer_nextzennpack_merge.py',
    'labellusso': 'labellusso/stock_price_synchronizer_labellusso_merge.py',
    '9tems': '9tems/stock_price_synchronizer_9tems_merge.py',
    'brickmansion': 'brickmansion/stock_price_synchronizer_brickmansion_merge.py',
    'loromoda': 'loromoda/stock_price_synchronizer_loromoda_merge.py',
    'milaneez': 'milaneez/stock_price_synchronizer_milaneez_merge.py',
    'maisonparco': 'maisonparco/stock_price_synchronizer_maisonparco_merge.py',
}

# 수집 후 카테고리(category_path) 채우는 별도 단계가 필요한 몰만 지정.
#   9tems / 기존 3몰(kasina/nextzennpack/labellusso)은 수집 중 인라인 처리 → 여기 없음.
#   collector 스크립트를 해당 플래그로 한 번 더 실행한다.
CATEGORY_FILL = {
    'brickmansion': ['--categories'],
    'loromoda': ['--categories'],
    'maisonparco': ['--categories'],
    'milaneez': ['--map-categories'],
}

# 공용 스크립트
CONVERTER_SCRIPT = 'kasina/raw_to_converter_kasina.py'
DEDUP_SCRIPT = 'okmall/dedup_corrector.py'
PRICE_SCRIPT = 'okmall/buyma_lowest_price_collector.py'
TRANSLATE_SCRIPT = 'okmall/convert_to_japanese_gemini.py'
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


def run_all(run_func, dry_run: bool = False):
    """모든 source 병렬 실행"""
    tasks = [(src, run_func, (src, dry_run)) for src in SOURCES]
    results = run_parallel(tasks, max_workers=len(SOURCES))
    for src, rc in results.items():
        if rc != 0:
            log(f"  {src} 실패 (rc={rc})", "WARNING")
    return results


# =====================================================
# Phase 실행 함수
# =====================================================

def phase1_collect(dry_run: bool = False):
    """Phase 1: Collector 병렬(--skip-existing) + 카테고리 단계"""
    log("=" * 60)
    log(f"Phase 1: Collector {len(SOURCES)}개 병렬 시작")
    log("=" * 60)

    def run_collector(src, dry_run):
        rc = run_script(COLLECTOR_SCRIPTS[src], ['--skip-existing'], dry_run=dry_run)
        # 카테고리 단계가 필요한 몰은 수집 직후 같은 collector를 카테고리 플래그로 한 번 더 실행
        if rc == 0 and src in CATEGORY_FILL:
            log(f"  {src} 카테고리 채우기: {' '.join(CATEGORY_FILL[src])}")
            rc = run_script(COLLECTOR_SCRIPTS[src], CATEGORY_FILL[src], dry_run=dry_run)
        return rc

    tasks = [(src, run_collector, (src, dry_run)) for src in SOURCES]
    results = run_parallel(tasks, max_workers=len(SOURCES))

    for src, rc in results.items():
        status = "완료" if rc == 0 else f"실패(rc={rc})"
        log(f"  {src} collector: {status}")

    log("Phase 1 완료")


def phase2_convert(dry_run: bool = False):
    """Phase 2: Converter 순차 (★ Dedup 제거)

    cutover: 옛 dedup_corrector(우선순위몰 남기고 나머지 죽이기) 제거.
    중복처리는 Phase 4 reconcile(--mode auto)가 그룹락 + ensure_group 으로 상품별 내재 처리.
    """
    log("=" * 60)
    log("Phase 2: Converter 시작 (Dedup 제거 — reconcile 가 내재 처리)")
    log("=" * 60)

    # Converter: 순차 실행 (source-site별)
    for src in SOURCES:
        log(f"  Convert: {src}")
        rc = run_script(CONVERTER_SCRIPT, ['--source-site', src, '--skip-translation'], dry_run=dry_run)
        if rc != 0:
            log(f"  {src} convert 실패", "WARNING")

    log("Phase 2 완료 (dedup 없음)")


def phase3_price_image_stock(dry_run: bool = False):
    """Phase 3: Price(3) → Translate(3) → Image(3) → Stock(3) 트랙 순차"""
    log("=" * 60)
    log("Phase 3: Price → Translate → Image → Stock (트랙 순차) 시작")
    log("=" * 60)

    def run_price(src, dry_run):
        return run_script(PRICE_SCRIPT, ['--source', src, '--new-only'], dry_run=dry_run)

    def run_translate(src, dry_run):
        return run_script(TRANSLATE_SCRIPT, ['--source', src, '--price-checked-only'], dry_run=dry_run)

    def run_image(src, dry_run):
        return run_script(IMAGE_SCRIPT, ['--source', src], dry_run=dry_run)

    def run_stock(src, dry_run):
        return run_script(STOCK_SCRIPTS[src], dry_run=dry_run)

    log("  [Track Price] 시작")
    run_all(run_price, dry_run)
    log("  [Track Price] 완료")

    log("  [Track Translate] 시작")
    run_all(run_translate, dry_run)
    log("  [Track Translate] 완료")

    log("  [Track Image] 시작")
    run_all(run_image, dry_run)
    log("  [Track Image] 완료")

    log("  [Track Stock] 시작")
    run_all(run_stock, dry_run)
    log("  [Track Stock] 완료")

    log("Phase 3 완료")


def phase4_register(dry_run: bool = False):
    """Phase 4: 신규 등록 → reconcile auto (--scope new)

    ★ cutover: 옛 register(buyma_new_product_register, 망가진 model_no dedup) 대체.
       reconcile auto --scope new = 미등록 그룹만 → ensure_group(즉석 merge) → CREATE.
       중복검증(그룹락+ensure_group) 내장. 이미 바이마 등록된 그룹은 자동 스킵(stock 담당).
       소스별 병렬 불필요(그룹이 소스 가로지름) → 단일 호출이 그룹락으로 안전.
    """
    log("=" * 60)
    log("Phase 4: 신규 등록 (reconcile auto --scope new) 시작")
    log("=" * 60)

    rc = run_script('okmall/reconcile_runner.py',
                    ['--mode', 'auto', '--scope', 'new', '--limit', '100000',
                     '--execute', '--confirm-live'],
                    dry_run=dry_run)
    if rc != 0:
        log(f"  reconcile auto(new) 종료코드 {rc}", "WARNING")

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
        phase2_convert(args.dry_run)

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
