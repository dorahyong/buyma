# -*- coding: utf-8 -*-
"""
BUYMA 자동화 파이프라인 오케스트레이터 v2 (멀티소스)

신규 상품 등록 프로세스를 브랜드별 파이프라인으로 자동 실행:

  1. COLLECT   - 해당 브랜드의 모든 수집처 병렬 수집
  2. CONVERT   - 데이터 변환 (번역 제외)
  3. PRICE     - 바이마 최저가 수집
  4. MERGE     - 멀티소스 재고 통합 (마진 기반)
  5. TRANSLATE - 번역 (최저가 확보분만)
  6. IMAGE     - 이미지 수집 + 업로드 (최저가 확보분만)
  7. REGISTER  - 바이마 상품 등록

특징:
  - COLLECT: 브랜드별로 N개 수집처 병렬 실행 (okmall+kasina+... 동시)
  - 브랜드별 병렬, 단계별 동시 실행 1개 제한 (리소스 보호)
  - buyma_brand_name 기준 중복 제거 (okmall NIKE + kasina NIKE = 1 파이프라인)
  - 중단 후 재실행 시 이어서 진행 (pipeline_control 테이블 기반)
  - 날짜 기반 배치 초기화 (어제 배치는 무시)

사용법:
    python orchestrator_v2.py                          # 전체 실행
    python orchestrator_v2.py --brand NIKE             # 특정 브랜드만
    python orchestrator_v2.py --exclude NIKE ADIDAS    # 특정 브랜드 제외
    python orchestrator_v2.py --until PRICE            # PRICE까지만
    python orchestrator_v2.py --mode PARTIAL           # CONVERT, MERGE, TRANSLATE, IMAGE 스킵
    python orchestrator_v2.py --skip-collect           # COLLECT 스킵

작성일: 2026-02-25
수정일: 2026-03-10 (멀티소스 브랜드별 파이프라인)
"""

import os
import sys
import subprocess
import argparse
import threading
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
import pymysql
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 로그 파일 설정
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
_log_file = None
_log_lock = threading.Lock()

# 수집처별 collector 스크립트 매핑
# 새 수집처 추가 시 여기에 등록
COLLECTOR_SCRIPTS = {
    'okmall': {
        'script': 'okmall_all_brands_collector.py',
        'cwd': None,  # None = 현재 디렉토리 (okmall/)
    },
    'kasina': {
        'script': 'kasina_collector.py',
        'cwd': '../kasina',  # kasina 디렉토리
    },
}


def _init_log_file(batch_id: str):
    """배치 ID 기반 로그 파일 초기화"""
    global _log_file
    log_path = os.path.join(LOG_DIR, f"{batch_id}.log")
    _log_file = open(log_path, 'a', encoding='utf-8', buffering=1)
    return log_path


def _write_log(message: str):
    """콘솔 + 파일 동시 출력 (스레드 안전)"""
    with _log_lock:
        print(message)
        sys.stdout.flush()
        if _log_file:
            _log_file.write(message + '\n')
            _log_file.flush()

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# DB 연결 정보
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}


class Orchestrator:
    """파이프라인 오케스트레이터 v2 (멀티소스)"""

    ALL_STAGES = ['COLLECT', 'CONVERT', 'PRICE', 'MERGE', 'TRANSLATE', 'IMAGE', 'REGISTER']

    def __init__(self, run_mode: str = 'FULL',
                 mall_filter: Optional[str] = None,
                 brand_filter: Optional[str] = None,
                 exclude_brands: Optional[List[str]] = None,
                 until_stage: Optional[str] = None,
                 skip_collect: bool = False):
        self.run_mode = run_mode
        self.mall_filter = mall_filter
        self.brand_filter = brand_filter
        self.exclude_brands = [b.upper() for b in (exclude_brands or [])]
        self.skip_collect = skip_collect
        self.batch_id = None
        self.conn = None

        # 실행할 단계 결정
        if until_stage:
            until_upper = until_stage.upper()
            if until_upper not in self.ALL_STAGES:
                raise ValueError(f"유효하지 않은 단계: {until_stage}. 가능: {', '.join(self.ALL_STAGES)}")
            until_idx = self.ALL_STAGES.index(until_upper) + 1
            self.stages = self.ALL_STAGES[:until_idx]
        else:
            self.stages = self.ALL_STAGES.copy()

        if self.skip_collect and 'COLLECT' in self.stages:
            self.stages.remove('COLLECT')

        self.final_stage = self.stages[-1] if self.stages else 'COLLECT'

        # 동시성 제어 (단계별 락)
        self.stage_locks = {stage: threading.Semaphore(1) for stage in self.stages}
        self.db_lock = threading.Lock()

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_log(f"[{timestamp}] [{level}] {message}")

    def connect_db(self):
        self.conn = pymysql.connect(**DB_CONFIG)

    def close_db(self):
        if self.conn:
            self.conn.close()

    # =================================================
    # 배치 관리
    # =================================================

    def get_or_create_batch(self) -> str:
        with self.db_lock:
            with self.conn.cursor() as cursor:
                today = date.today().strftime("%Y%m%d")

                cursor.execute(
                    """SELECT batch_id FROM pipeline_batches
                       WHERE status = 'RUNNING' AND run_mode = %s AND batch_id LIKE %s
                       LIMIT 1""",
                    (self.run_mode, f"{today}%")
                )
                result = cursor.fetchone()

                if result:
                    self.batch_id = result['batch_id']
                    self.log(f"오늘의 기존 배치 발견: {self.batch_id} (이어하기 모드)")
                else:
                    cursor.execute(
                        """UPDATE pipeline_batches
                           SET status = 'FAILED', end_time = NOW()
                           WHERE status = 'RUNNING' AND run_mode = %s AND batch_id NOT LIKE %s""",
                        (self.run_mode, f"{today}%")
                    )
                    if cursor.rowcount > 0:
                        self.log(f"어제 RUNNING 배치 {cursor.rowcount}건을 FAILED로 처리")

                    self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cursor.execute(
                        """INSERT INTO pipeline_batches (batch_id, run_mode, status)
                           VALUES (%s, %s, 'RUNNING')""",
                        (self.batch_id, self.run_mode)
                    )
                    self.conn.commit()
                    self.log(f"새 배치 생성: {self.batch_id} (모드: {self.run_mode})")

        return self.batch_id

    # =================================================
    # 브랜드 / 수집처 조회
    # =================================================

    def get_target_brands(self) -> List[Dict]:
        """
        처리 대상 브랜드 목록 조회 (buyma_brand 기준 중복 제거)

        Returns:
            [{buyma_brand_name, mall_brands: [{mall_name, mall_brand_name_en}]}, ...]
        """
        with self.conn.cursor() as cursor:
            sql = """SELECT mall_name, mall_brand_name_en,
                            COALESCE(buyma_brand_name, mall_brand_name_en) as buyma_brand_name
                     FROM mall_brands
                     WHERE is_active = 1"""
            params = []

            if self.brand_filter:
                sql += " AND (UPPER(mall_brand_name_en) = %s OR UPPER(buyma_brand_name) = %s)"
                params.extend([self.brand_filter.upper(), self.brand_filter.upper()])

            if self.exclude_brands:
                placeholders = ', '.join(['%s'] * len(self.exclude_brands))
                sql += f" AND UPPER(COALESCE(buyma_brand_name, mall_brand_name_en)) NOT IN ({placeholders})"
                params.extend(self.exclude_brands)

            cursor.execute(sql, params)
            rows = cursor.fetchall()

        # buyma_brand_name 기준으로 그룹핑
        brand_map = {}
        for row in rows:
            bbn = row['buyma_brand_name']
            if bbn not in brand_map:
                brand_map[bbn] = {
                    'buyma_brand_name': bbn,
                    'mall_brands': []
                }
            brand_map[bbn]['mall_brands'].append({
                'mall_name': row['mall_name'],
                'mall_brand_name_en': row['mall_brand_name_en'],
            })

        return list(brand_map.values())

    def get_active_collectors(self) -> List[Dict]:
        """mall_sites에서 활성 수집처 목록 조회 → collector 스크립트 매핑"""
        with self.conn.cursor() as cursor:
            sql = "SELECT site_name FROM mall_sites WHERE is_active = 1"
            params = []
            if self.mall_filter:
                sql += " AND site_name = %s"
                params.append(self.mall_filter)
            cursor.execute(sql, params)
            sites = cursor.fetchall()

        collectors = []
        for site in sites:
            site_name = site['site_name']
            if site_name in COLLECTOR_SCRIPTS:
                collectors.append({
                    'site_name': site_name,
                    **COLLECTOR_SCRIPTS[site_name]
                })
            else:
                self.log(f"수집처 '{site_name}'의 collector 스크립트 미등록. 스킵.", "WARNING")
        return collectors

    # =================================================
    # 메인 실행
    # =================================================

    def run(self):
        try:
            self.connect_db()
            self.get_or_create_batch()

            log_path = _init_log_file(self.batch_id)
            self.log(f"로그 파일: {log_path}")
            self.log(f"배치: {self.batch_id}, 모드: {self.run_mode}")

            brands = self.get_target_brands()
            self.log(f"총 {len(brands)}개 브랜드 처리 예정")
            self.log(f"실행 단계: {' → '.join(self.stages)}")

            if self.exclude_brands:
                self.log(f"제외 브랜드: {', '.join(self.exclude_brands)}")

            # 총 브랜드 수 업데이트
            with self.db_lock:
                with self.conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE pipeline_batches SET total_brands = %s WHERE batch_id = %s",
                        (len(brands), self.batch_id)
                    )
                    self.conn.commit()

            if not brands:
                self.log("처리할 브랜드가 없습니다.")
                self.finish_batch()
                return

            # 브랜드별 병렬 처리
            max_workers = min(len(brands), len(self.stages)) if self.stages else 1

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for brand_info in brands:
                    future = executor.submit(self.process_brand_pipeline, brand_info)
                    futures[future] = brand_info

                for future in as_completed(futures):
                    brand_info = futures[future]
                    brand_name = brand_info['buyma_brand_name']
                    try:
                        success = future.result()
                        status = "✓" if success else "✗"
                        self.log(f"{status} [{brand_name}] 파이프라인 {'완료' if success else '실패'}")
                    except Exception as e:
                        self.log(f"✗ [{brand_name}] 예외 발생: {e}", "ERROR")

            self.finish_batch()

        except Exception as e:
            self.log(f"오케스트레이터 치명적 오류: {e}", "ERROR")
            self.mark_batch_failed()
        finally:
            self.close_db()

    # =================================================
    # 브랜드별 파이프라인
    # =================================================

    def process_brand_pipeline(self, brand_info: Dict) -> bool:
        """한 브랜드의 전체 파이프라인 순차 처리"""
        buyma_brand = brand_info['buyma_brand_name']
        mall_brands = brand_info['mall_brands']

        self.log(f">>> [{buyma_brand}] 파이프라인 시작 (수집처: {[mb['mall_name'] for mb in mall_brands]})")

        for stage in self.stages:
            with self.stage_locks[stage]:
                # 1. 상태 확인
                status = self.get_stage_status('_ALL_', buyma_brand, stage)
                if status == 'DONE':
                    self.log(f"  [{buyma_brand}] [{stage}] 이미 완료. 스킵.")
                    continue

                # 2. PARTIAL 모드 스킵
                if self.run_mode == 'PARTIAL' and stage in ['CONVERT', 'MERGE', 'TRANSLATE', 'IMAGE']:
                    self.log(f"  [{buyma_brand}] [{stage}] PARTIAL 모드 → 스킵")
                    self.update_stage_status('_ALL_', buyma_brand, stage, 'DONE', "Skipped in PARTIAL mode")
                    continue

                # 3. 실행
                self.log(f"  [{buyma_brand}] [{stage}] 실행 중...")

                if stage == 'COLLECT':
                    success = self.run_collect_for_brand(buyma_brand, mall_brands)
                else:
                    target_brand_name = buyma_brand
                    success = self.execute_worker('_ALL_', buyma_brand, stage, target_brand_name)

                if not success:
                    self.log(f"  [{buyma_brand}] [{stage}] 실패 → 브랜드 중단", "ERROR")
                    return False

        return True

    # =================================================
    # COLLECT: 브랜드별 N개 수집처 병렬
    # =================================================

    def run_collect_for_brand(self, buyma_brand: str, mall_brands: List[Dict]) -> bool:
        """
        한 브랜드에 대해 모든 수집처 COLLECT 병렬 실행

        mall_brands에 있는 수집처만 실행 (해당 브랜드가 등록된 수집처)
        활성 collector가 있는 수집처만 필터
        """
        self.update_stage_status('_ALL_', buyma_brand, 'COLLECT', 'RUNNING')

        # 해당 브랜드가 등록된 수집처 중 활성 collector가 있는 것만
        active_collectors = self.get_active_collectors()
        active_site_names = {c['site_name'] for c in active_collectors}
        collector_map = {c['site_name']: c for c in active_collectors}

        tasks = []
        for mb in mall_brands:
            mall_name = mb['mall_name']
            if mall_name in active_site_names:
                tasks.append({
                    'collector': collector_map[mall_name],
                    'brand_name': mb['mall_brand_name_en'],
                })

        if not tasks:
            self.log(f"    [{buyma_brand}] 활성 수집처 없음. 스킵.", "WARNING")
            self.update_stage_status('_ALL_', buyma_brand, 'COLLECT', 'DONE', "No active collectors")
            return True

        self.log(f"    [{buyma_brand}] {len(tasks)}개 수집처 병렬 COLLECT")

        results = {}

        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = {}
            for task in tasks:
                future = executor.submit(
                    self._run_single_collector, task['collector'], task['brand_name']
                )
                futures[future] = task['collector']['site_name']

            for future in as_completed(futures):
                site_name = futures[future]
                try:
                    success = future.result()
                    results[site_name] = success
                    status_mark = "✓" if success else "✗"
                    self.log(f"    {status_mark} [{buyma_brand}] [{site_name}] COLLECT {'완료' if success else '실패'}")
                except Exception as e:
                    results[site_name] = False
                    self.log(f"    ✗ [{buyma_brand}] [{site_name}] COLLECT 예외: {e}", "ERROR")

        success_count = sum(1 for v in results.values() if v)

        # 하나라도 성공하면 COLLECT 성공 (전부 실패하면 실패)
        if success_count > 0:
            self.update_stage_status('_ALL_', buyma_brand, 'COLLECT', 'DONE')
            return True
        else:
            self.update_stage_status('_ALL_', buyma_brand, 'COLLECT', 'ERROR', "All collectors failed")
            return False

    def _run_single_collector(self, collector: Dict, brand_name: str) -> bool:
        """단일 수집처 collector 실행 (특정 브랜드)"""
        site_name = collector['site_name']
        script = collector['script']
        cwd = collector.get('cwd')

        base_dir = os.path.dirname(os.path.abspath(__file__))
        work_dir = os.path.normpath(os.path.join(base_dir, cwd)) if cwd else base_dir

        cmd = [sys.executable, script, '--brand', brand_name, '--skip-existing']

        self.log(f"      [{site_name}] 실행: {script} --brand {brand_name}")

        try:
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=env,
                cwd=work_dir
            )

            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if line.strip():
                        _write_log(f"        [{site_name}] {line.rstrip()}")

            process.wait()

            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd)

            return True

        except subprocess.CalledProcessError as e:
            self.log(f"      [{site_name}] 실행 실패 (exit code: {e.returncode})", "ERROR")
            return False

        except Exception as e:
            self.log(f"      [{site_name}] 예외: {e}", "ERROR")
            return False

    # =================================================
    # 워커 실행 (CONVERT ~ REGISTER)
    # =================================================

    def execute_worker(self, mall: str, brand: str, stage: str, target_brand_name: str) -> bool:
        self.update_stage_status(mall, brand, stage, 'RUNNING')

        commands = []

        if stage == 'CONVERT':
            commands.append([
                sys.executable,
                'raw_to_converter_v2.py',
                '--brand', target_brand_name,
                '--skip-translation'
            ])

        elif stage == 'PRICE':
            commands.append([
                sys.executable,
                'buyma_lowest_price_collector.py',
                '--brand', target_brand_name
            ])

        elif stage == 'MERGE':
            commands.append([
                sys.executable,
                'stock_merge.py',
                '--brand', target_brand_name
            ])

        elif stage == 'TRANSLATE':
            commands.append([
                sys.executable,
                'convert_to_japanese_gemini.py',
                '--brand', target_brand_name,
                '--price-checked-only'
            ])

        elif stage == 'IMAGE':
            commands.append([
                sys.executable,
                'image_collector_parallel.py',
                '--brand', target_brand_name,
                '--price-checked-only'
            ])
            commands.append([
                sys.executable,
                'r2_image_uploader.py',
                '--brand', target_brand_name
            ])

        elif stage == 'REGISTER':
            commands.append([
                sys.executable,
                'buyma_new_product_register.py',
                '--brand', target_brand_name
            ])

        for cmd in commands:
            script_name = os.path.basename(cmd[1])
            self.log(f"    [실행] {script_name} --brand {target_brand_name}")

            try:
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    env=env
                )

                if process.stdout:
                    for line in iter(process.stdout.readline, ''):
                        if line.strip():
                            _write_log(f"      {line.rstrip()}")

                process.wait()

                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, cmd)

                self.log(f"    [완료] {script_name}")

            except subprocess.CalledProcessError as e:
                error_msg = f"스크립트 실행 실패: {script_name} (exit code: {e.returncode})"
                self.log(error_msg, "ERROR")
                self.update_stage_status(mall, brand, stage, 'ERROR', error_msg[:500])
                return False

            except Exception as e:
                error_msg = f"예외 발생: {str(e)}"
                self.log(error_msg, "ERROR")
                self.update_stage_status(mall, brand, stage, 'ERROR', str(e)[:500])
                return False

        self.update_stage_status(mall, brand, stage, 'DONE')
        return True

    # =================================================
    # 공통 유틸리티
    # =================================================

    def get_stage_status(self, mall: str, brand: str, stage: str) -> str:
        with self.db_lock:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """SELECT status FROM pipeline_control
                       WHERE batch_id = %s AND mall_name = %s AND brand_name = %s AND stage = %s""",
                    (self.batch_id, mall, brand, stage)
                )
                result = cursor.fetchone()
                return result['status'] if result else 'PENDING'

    def update_stage_status(self, mall: str, brand: str, stage: str,
                           status: str, error_msg: Optional[str] = None):
        with self.db_lock:
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO pipeline_control
                    (batch_id, mall_name, brand_name, run_mode, stage, status, error_msg, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        status = VALUES(status),
                        error_msg = VALUES(error_msg),
                        updated_at = NOW()
                """, (self.batch_id, mall, brand, self.run_mode, stage, status, error_msg))
                self.conn.commit()

    def finish_batch(self):
        with self.db_lock:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(DISTINCT brand_name) as cnt
                       FROM pipeline_control
                       WHERE batch_id = %s AND stage = %s AND status = 'DONE'""",
                    (self.batch_id, self.final_stage)
                )
                success_count = cursor.fetchone()['cnt']

                cursor.execute(
                    """UPDATE pipeline_batches
                       SET status = 'COMPLETED', end_time = NOW(), success_brands = %s
                       WHERE batch_id = %s""",
                    (success_count, self.batch_id)
                )
                self.conn.commit()

                self.log("")
                self.log("=" * 60)
                self.log(f"배치 완료: {self.batch_id}")
                self.log(f"  성공: {success_count}개 브랜드")
                self.log(f"  최종 단계: {self.final_stage}")
                self.log("=" * 60)

    def mark_batch_failed(self):
        if not self.conn or not self.batch_id:
            return
        try:
            with self.db_lock:
                with self.conn.cursor() as cursor:
                    cursor.execute(
                        """UPDATE pipeline_batches SET status = 'FAILED', end_time = NOW()
                           WHERE batch_id = %s""",
                        (self.batch_id,)
                    )
                    self.conn.commit()
                    self.log(f"배치 실패 처리: {self.batch_id}", "ERROR")
        except Exception as e:
            self.log(f"배치 실패 처리 중 오류: {e}", "ERROR")


def main():
    parser = argparse.ArgumentParser(
        description='BUYMA 파이프라인 오케스트레이터 v2 (멀티소스)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python orchestrator_v2.py                          # 전체 실행
  python orchestrator_v2.py --brand NIKE             # 특정 브랜드만
  python orchestrator_v2.py --exclude NIKE ADIDAS    # 특정 브랜드 제외
  python orchestrator_v2.py --until PRICE            # PRICE까지만
  python orchestrator_v2.py --mode PARTIAL           # CONVERT, MERGE, TRANSLATE, IMAGE 스킵
  python orchestrator_v2.py --skip-collect           # COLLECT 스킵
        """
    )

    parser.add_argument('--mode', type=str, choices=['FULL', 'PARTIAL'], default='FULL',
                       help='실행 모드 (FULL: 전체, PARTIAL: CONVERT/MERGE/TRANSLATE/IMAGE 스킵)')
    parser.add_argument('--mall', type=str, help='특정 수집처만 COLLECT (예: okmall)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 (예: NIKE)')
    parser.add_argument('--exclude', type=str, nargs='+', help='제외할 브랜드 (예: --exclude NIKE ADIDAS)')
    parser.add_argument('--until', type=str,
                       choices=['COLLECT', 'CONVERT', 'PRICE', 'MERGE', 'TRANSLATE', 'IMAGE', 'REGISTER'],
                       help='지정 단계까지만 실행')
    parser.add_argument('--skip-collect', action='store_true', help='COLLECT 스킵')

    args = parser.parse_args()

    _write_log("=" * 60)
    _write_log("BUYMA 파이프라인 오케스트레이터 v2 (멀티소스)")
    _write_log("=" * 60)

    orchestrator = Orchestrator(
        run_mode=args.mode,
        mall_filter=args.mall,
        brand_filter=args.brand,
        exclude_brands=args.exclude,
        until_stage=args.until,
        skip_collect=args.skip_collect
    )

    try:
        orchestrator.run()
    finally:
        if _log_file:
            _log_file.close()


if __name__ == "__main__":
    main()
