# -*- coding: utf-8 -*-
import os
import sys
import json
import subprocess
import time
import argparse
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymysql
from dotenv import load_dotenv
from typing import List, Dict, Optional

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

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
    # 전체 단계 정의 (순서 중요)
    ALL_STAGES = ['COLLECT', 'CONVERT', 'IMAGE', 'PRICE', 'REGISTER']

    def __init__(self, run_mode: str, mall_filter: str = None, brand_filter: str = None,
                 exclude_brands: List[str] = None, until_stage: str = None):
        self.run_mode = run_mode  # 'FULL' or 'PARTIAL'
        self.mall_filter = mall_filter
        self.brand_filter = brand_filter
        self.exclude_brands = [b.upper() for b in (exclude_brands or [])]
        self.batch_id = None
        self.conn = None

        # --until 옵션 처리: 지정된 단계까지만 실행
        if until_stage:
            until_stage_upper = until_stage.upper()
            if until_stage_upper not in self.ALL_STAGES:
                raise ValueError(f"유효하지 않은 단계: {until_stage}. 가능한 값: {', '.join(self.ALL_STAGES)}")
            until_idx = self.ALL_STAGES.index(until_stage_upper) + 1
            self.stages = self.ALL_STAGES[:until_idx]
        else:
            self.stages = self.ALL_STAGES.copy()

        self.final_stage = self.stages[-1]  # 마지막 단계 (배치 완료 판단용)

        # 파이프라인용: 각 단계별 동시 실행 제한 (1개씩만)
        self.stage_locks = {stage: threading.Semaphore(1) for stage in self.stages}
        # DB 연결 동시 접근 방지
        self.db_lock = threading.Lock()

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

    def connect_db(self):
        self.conn = pymysql.connect(**DB_CONFIG)

    def close_db(self):
        if self.conn:
            self.conn.close()

    def get_or_create_batch(self) -> str:
        """실행 중인 배치를 가져오거나 새로 생성"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                # 1. 실행 중인 배치 확인
                cursor.execute(
                    "SELECT batch_id FROM pipeline_batches WHERE status = 'RUNNING' AND run_mode = %s LIMIT 1",
                    (self.run_mode,)
                )
                result = cursor.fetchone()

                if result:
                    self.batch_id = result['batch_id']
                    self.log(f"기존 배치 발견: {self.batch_id} (이어하기 모드)")
                else:
                    # 2. 새 배치 생성
                    self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cursor.execute(
                        "INSERT INTO pipeline_batches (batch_id, run_mode, status) VALUES (%s, %s, 'RUNNING')",
                        (self.batch_id, self.run_mode)
                    )
                    self.conn.commit()
                    self.log(f"새 배치 생성: {self.batch_id} (모드: {self.run_mode})")

        return self.batch_id

    def get_target_brands(self) -> List[Dict]:
        """처리 대상 브랜드 목록 조회 (쇼핑몰명과 바이마명 둘 다 가져옴)"""
        with self.conn.cursor() as cursor:
            sql = "SELECT mall_name, mall_brand_name_en, buyma_brand_name FROM mall_brands WHERE is_active = 1"
            params = []

            if self.mall_filter:
                sql += " AND mall_name = %s"
                params.append(self.mall_filter)

            if self.brand_filter:
                # 필터링은 mall_brand_name_en 기준으로 수행
                sql += " AND UPPER(mall_brand_name_en) = %s"
                params.append(self.brand_filter.upper())

            # 제외 브랜드 처리
            if self.exclude_brands:
                placeholders = ', '.join(['%s'] * len(self.exclude_brands))
                sql += f" AND UPPER(mall_brand_name_en) NOT IN ({placeholders})"
                params.extend(self.exclude_brands)

            cursor.execute(sql, params)
            return cursor.fetchall()

    def run(self):
        """전체 파이프라인 실행 메인 루프 (파이프라인 병렬 처리)"""
        try:
            self.connect_db()
            self.get_or_create_batch()

            brands = self.get_target_brands()
            self.log(f"총 {len(brands)}개 브랜드 처리 예정 (파이프라인 모드)")
            self.log(f"실행 단계: {' → '.join(self.stages)}")

            if self.exclude_brands:
                self.log(f"제외된 브랜드: {', '.join(self.exclude_brands)}")

            # pipeline_batches에 총 브랜드 수 업데이트
            with self.db_lock:
                with self.conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE pipeline_batches SET total_brands = %s WHERE batch_id = %s",
                        (len(brands), self.batch_id)
                    )
                    self.conn.commit()

            # 파이프라인 병렬 처리: 각 브랜드는 스레드에서 실행
            # 단, 같은 단계는 세마포어로 1개씩만 실행됨
            max_workers = min(len(brands), len(self.stages))  # 최대 동시 실행 = 단계 수

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for brand_info in brands:
                    future = executor.submit(self.process_brand_pipeline, brand_info)
                    futures[future] = brand_info

                # 결과 수집
                for future in as_completed(futures):
                    brand_info = futures[future]
                    mall_brand = brand_info['mall_brand_name_en']
                    try:
                        success = future.result()
                        if success:
                            self.log(f"<<< [{mall_brand}] 파이프라인 완료")
                        else:
                            self.log(f"<<< [{mall_brand}] 파이프라인 실패", "ERROR")
                    except Exception as e:
                        self.log(f"<<< [{mall_brand}] 예외 발생: {e}", "ERROR")

            # 모든 브랜드 처리가 끝나면 배치 완료 처리
            self.finish_batch()

        except Exception as e:
            self.log(f"오케스트레이터 실행 중 치명적 오류: {e}", "ERROR")
        finally:
            self.close_db()

    def process_brand_pipeline(self, brand_info: Dict) -> bool:
        """한 브랜드의 전체 파이프라인 처리 (스레드에서 실행)"""
        mall = brand_info['mall_name']
        mall_brand = brand_info['mall_brand_name_en']
        buyma_brand = brand_info['buyma_brand_name'] or mall_brand

        self.log(f">>> [{mall} | {mall_brand}] 파이프라인 시작")

        for stage in self.stages:
            # 해당 단계 락 획득 (다른 브랜드가 같은 단계 실행 중이면 대기)
            with self.stage_locks[stage]:
                # 1. 단계 상태 확인
                status = self.get_stage_status(mall, mall_brand, stage)

                if status == 'DONE':
                    self.log(f"  [{mall_brand}] [{stage}] 이미 완료됨. 스킵.")
                    continue

                # 2. PARTIAL 모드 스킵 로직
                if self.run_mode == 'PARTIAL' and stage in ['CONVERT', 'IMAGE']:
                    self.log(f"  [{mall_brand}] [{stage}] PARTIAL 모드이므로 자동 스킵.")
                    self.update_stage_status(mall, mall_brand, stage, 'DONE', "Skipped in PARTIAL mode")
                    continue

                # 3. 워커 실행
                target_brand_name = mall_brand if stage in ['COLLECT', 'CONVERT'] else buyma_brand
                self.log(f"  [{mall_brand}] [{stage}] 실행 중...")

                success = self.execute_worker(mall, mall_brand, stage, target_brand_name)

                if not success:
                    self.log(f"  [{mall_brand}] [{stage}] 에러 발생. 이 브랜드 중단.", "ERROR")
                    return False

        return True

    def get_stage_status(self, mall: str, brand: str, stage: str) -> str:
        """DB에서 해당 단계의 현재 상태 조회"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """SELECT status FROM pipeline_control
                       WHERE batch_id = %s AND mall_name = %s AND brand_name = %s AND stage = %s""",
                    (self.batch_id, mall, brand, stage)
                )
                result = cursor.fetchone()
                return result['status'] if result else 'PENDING'

    def update_stage_status(self, mall: str, brand: str, stage: str, status: str, error_msg: str = None):
        """DB에 단계 상태 업데이트"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                sql = """
                    INSERT INTO pipeline_control (batch_id, mall_name, brand_name, run_mode, stage, status, error_msg, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        status = VALUES(status),
                        error_msg = VALUES(error_msg),
                        updated_at = NOW()
                """
                cursor.execute(sql, (self.batch_id, mall, brand, self.run_mode, stage, status, error_msg))
                self.conn.commit()

    def execute_worker(self, mall: str, brand_id_name: str, stage: str, target_brand_name: str) -> bool:
        """실제 스크립트(워커) 호출"""
        self.update_stage_status(mall, brand_id_name, stage, 'RUNNING')
        
        commands = []
        
        if stage == 'COLLECT':
            commands.append([sys.executable, 'okmall_all_brands_collector.py', '--brand', target_brand_name])
        
        elif stage == 'CONVERT':
            commands.append([sys.executable, 'raw_to_ace_converter.py', '--brand', target_brand_name])
            
        elif stage == 'IMAGE':
            # 이미지 수집과 업로드를 순차적으로 실행
            commands.append([sys.executable, 'image_collector_parallel.py', '--brand', target_brand_name])
            commands.append([sys.executable, 'r2_image_uploader.py', '--brand', target_brand_name])
            
        elif stage == 'PRICE':
            commands.append([sys.executable, 'buyma_lowest_price_collector.py', '--brand', target_brand_name])
            
        elif stage == 'REGISTER':
            commands.append([sys.executable, 'buyma_product_register.py', '--brand', target_brand_name])

        for cmd in commands:
            script_name = cmd[1]
            self.log(f"    [EXEC] {' '.join(cmd)}")
            
            try:
                # 윈도우 환경 대응을 위한 인코딩 설정
                exec_encoding = 'utf-8' if sys.platform != 'win32' else 'cp949'
                
                # 실시간 로그 출력을 위해 Popen 사용
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding=exec_encoding,
                    errors='replace',
                    bufsize=1,
                    universal_newlines=True
                )

                # 일꾼(스크립트)이 내뱉는 로그를 실시간으로 화면에 출력
                if process.stdout:
                    for line in iter(process.stdout.readline, ''):
                        print(line, end='')
                        sys.stdout.flush()

                process.wait()

                if process.returncode != 0:
                    # 에러 발생 시 상세 로그는 이미 위에서 출력됨
                    raise subprocess.CalledProcessError(process.returncode, cmd)
                
                self.log(f"    [SUCCESS] {script_name} 완료")
            except subprocess.CalledProcessError as e:
                error_msg = f"스크립트 실행 실패: {script_name}"
                self.log(error_msg, "ERROR")
                self.update_stage_status(mall, brand_id_name, stage, 'ERROR', error_msg[:500])
                return False
            except Exception as e:
                self.log(f"예외 발생: {str(e)}", "ERROR")
                self.update_stage_status(mall, brand_id_name, stage, 'ERROR', str(e)[:500])
                return False

        self.update_stage_status(mall, brand_id_name, stage, 'DONE')
        return True

    def finish_batch(self):
        """배치 최종 마감"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                # 성공한 브랜드 수 계산 (마지막 단계 기준)
                cursor.execute(
                    """SELECT COUNT(DISTINCT brand_name) as cnt FROM pipeline_control
                       WHERE batch_id = %s AND stage = %s AND status = 'DONE'""",
                    (self.batch_id, self.final_stage)
                )
                success_count = cursor.fetchone()['cnt']

                cursor.execute(
                    "UPDATE pipeline_batches SET status = 'COMPLETED', end_time = NOW(), success_brands = %s WHERE batch_id = %s",
                    (success_count, self.batch_id)
                )
                self.conn.commit()
                self.log(f"전체 배치 완료: {self.batch_id} (성공 브랜드: {success_count}, 마지막 단계: {self.final_stage})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='BUYMA 파이프라인 코어 실행기 (파이프라인 모드)')
    parser.add_argument('--mode', type=str, choices=['FULL', 'PARTIAL'], default='FULL', help='실행 모드 (FULL 또는 PARTIAL)')
    parser.add_argument('--mall', type=str, help='특정 쇼핑몰만 지정 (예: okmall)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 지정 (예: AMI)')
    parser.add_argument('--exclude', type=str, nargs='+', help='제외할 브랜드 (예: --exclude A.P.C NIKE ADIDAS)')
    parser.add_argument('--until', type=str, choices=['COLLECT', 'CONVERT', 'IMAGE', 'PRICE', 'REGISTER'],
                        help='지정 단계까지만 실행 (예: --until PRICE)')
    args = parser.parse_args()

    orchestrator = Orchestrator(
        run_mode=args.mode,
        mall_filter=args.mall,
        brand_filter=args.brand,
        exclude_brands=args.exclude,
        until_stage=args.until
    )
    orchestrator.run()
