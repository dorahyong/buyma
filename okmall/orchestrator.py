# -*- coding: utf-8 -*-
"""
BUYMA 자동화 파이프라인 오케스트레이터

신규 상품 등록 프로세스를 6단계로 자동 실행:
  1. COLLECT   - OkMall 상품 수집
  2. CONVERT   - 데이터 변환 (번역 제외)
  3. PRICE     - 바이마 최저가 수집
  4. TRANSLATE - 번역 (최저가 확보분만)
  5. IMAGE     - 이미지 수집 + 업로드 (최저가 확보분만)
  6. REGISTER  - 바이마 상품 등록

특징:
  - 파이프라인 병렬 처리 (브랜드 A의 CONVERT 중 브랜드 B는 COLLECT)
  - 중단 후 재실행 시 이어서 진행 (pipeline_control 테이블 기반)
  - 날짜 기반 배치 초기화 (어제 배치는 무시)
  - 단계별 동시 실행 1개 제한 (리소스 보호)

사용법:
    python orchestrator.py                          # 전체 브랜드, 전체 단계
    python orchestrator.py --brand NIKE             # 특정 브랜드만
    python orchestrator.py --exclude NIKE ADIDAS    # 특정 브랜드 제외
    python orchestrator.py --until PRICE            # REGISTER 전까지만
    python orchestrator.py --mode PARTIAL           # CONVERT, IMAGE 스킵

작성일: 2026-02-25
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


def _init_log_file(batch_id: str):
    """배치 ID 기반 로그 파일 초기화"""
    global _log_file
    log_path = os.path.join(LOG_DIR, f"{batch_id}.log")
    _log_file = open(log_path, 'a', encoding='utf-8', buffering=1)  # line-buffered
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
    """파이프라인 오케스트레이터"""
    
    ALL_STAGES = ['COLLECT', 'CONVERT', 'PRICE', 'TRANSLATE', 'IMAGE', 'REGISTER']
    
    def __init__(self, run_mode: str = 'FULL', 
                 mall_filter: Optional[str] = None,
                 brand_filter: Optional[str] = None,
                 exclude_brands: Optional[List[str]] = None,
                 until_stage: Optional[str] = None):
        """
        Args:
            run_mode: 'FULL' (전체) or 'PARTIAL' (CONVERT, IMAGE 스킵)
            mall_filter: 특정 쇼핑몰만 (예: 'okmall')
            brand_filter: 특정 브랜드만 (예: 'NIKE')
            exclude_brands: 제외할 브랜드 리스트
            until_stage: 지정 단계까지만 실행 (예: 'PRICE')
        """
        self.run_mode = run_mode
        self.mall_filter = mall_filter
        self.brand_filter = brand_filter
        self.exclude_brands = [b.upper() for b in (exclude_brands or [])]
        self.batch_id = None
        self.conn = None
        
        # 실행할 단계 결정
        if until_stage:
            until_stage_upper = until_stage.upper()
            if until_stage_upper not in self.ALL_STAGES:
                raise ValueError(f"유효하지 않은 단계: {until_stage}. 가능: {', '.join(self.ALL_STAGES)}")
            until_idx = self.ALL_STAGES.index(until_stage_upper) + 1
            self.stages = self.ALL_STAGES[:until_idx]
        else:
            self.stages = self.ALL_STAGES.copy()
        
        self.final_stage = self.stages[-1]
        
        # 동시성 제어
        self.stage_locks = {stage: threading.Semaphore(1) for stage in self.stages}
        self.db_lock = threading.Lock()
    
    def log(self, message: str, level: str = "INFO"):
        """로그 출력 (콘솔 + 파일)"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_log(f"[{timestamp}] [{level}] {message}")
    
    def connect_db(self):
        """DB 연결"""
        self.conn = pymysql.connect(**DB_CONFIG)
    
    def close_db(self):
        """DB 연결 종료"""
        if self.conn:
            self.conn.close()
    
    def get_or_create_batch(self) -> str:
        """실행 중인 배치를 가져오거나 새로 생성 (날짜 기반 초기화)"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                today = date.today().strftime("%Y%m%d")
                
                # 1. 오늘 날짜의 RUNNING 배치 확인
                cursor.execute(
                    """SELECT batch_id FROM pipeline_batches 
                       WHERE status = 'RUNNING' 
                       AND run_mode = %s 
                       AND batch_id LIKE %s
                       LIMIT 1""",
                    (self.run_mode, f"{today}%")
                )
                result = cursor.fetchone()
                
                if result:
                    self.batch_id = result['batch_id']
                    self.log(f"오늘의 기존 배치 발견: {self.batch_id} (이어하기 모드)")
                else:
                    # 2. 어제 날짜의 RUNNING 배치가 있으면 FAILED로 마감
                    cursor.execute(
                        """UPDATE pipeline_batches 
                           SET status = 'FAILED', 
                               end_time = NOW() 
                           WHERE status = 'RUNNING' 
                           AND run_mode = %s
                           AND batch_id NOT LIKE %s""",
                        (self.run_mode, f"{today}%")
                    )
                    if cursor.rowcount > 0:
                        self.log(f"어제 RUNNING 배치 {cursor.rowcount}건을 FAILED로 처리")
                    
                    # 3. 새 배치 생성
                    self.batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cursor.execute(
                        """INSERT INTO pipeline_batches 
                           (batch_id, run_mode, status) 
                           VALUES (%s, %s, 'RUNNING')""",
                        (self.batch_id, self.run_mode)
                    )
                    self.conn.commit()
                    self.log(f"새 배치 생성: {self.batch_id} (모드: {self.run_mode})")
        
        return self.batch_id
    
    def get_target_brands(self) -> List[Dict]:
        """처리 대상 브랜드 목록 조회"""
        with self.conn.cursor() as cursor:
            sql = """SELECT mall_name, mall_brand_name_en, buyma_brand_name 
                     FROM mall_brands 
                     WHERE is_active = 1"""
            params = []
            
            if self.mall_filter:
                sql += " AND mall_name = %s"
                params.append(self.mall_filter)
            
            if self.brand_filter:
                sql += " AND UPPER(mall_brand_name_en) = %s"
                params.append(self.brand_filter.upper())
            
            if self.exclude_brands:
                placeholders = ', '.join(['%s'] * len(self.exclude_brands))
                sql += f" AND UPPER(mall_brand_name_en) NOT IN ({placeholders})"
                params.extend(self.exclude_brands)
            
            cursor.execute(sql, params)
            return cursor.fetchall()
    
    def run(self):
        """전체 파이프라인 실행"""
        try:
            self.connect_db()
            self.get_or_create_batch()

            # ★ 배치 ID 확정 후 로그 파일 초기화
            log_path = _init_log_file(self.batch_id)
            self.log(f"로그 파일: {log_path}")

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
            
            # 파이프라인 병렬 처리
            max_workers = min(len(brands), len(self.stages))
            
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
                            self.log(f"✓ [{mall_brand}] 파이프라인 완료")
                        else:
                            self.log(f"✗ [{mall_brand}] 파이프라인 실패", "ERROR")
                    except Exception as e:
                        self.log(f"✗ [{mall_brand}] 예외 발생: {e}", "ERROR")
            
            # 배치 마감
            self.finish_batch()
            
        except Exception as e:
            self.log(f"오케스트레이터 치명적 오류: {e}", "ERROR")
            self.mark_batch_failed()
        finally:
            self.close_db()
    
    def process_brand_pipeline(self, brand_info: Dict) -> bool:
        """한 브랜드의 전체 파이프라인 순차 처리"""
        mall = brand_info['mall_name']
        mall_brand = brand_info['mall_brand_name_en']
        buyma_brand = brand_info['buyma_brand_name'] or mall_brand
        
        self.log(f">>> [{mall} | {mall_brand}] 파이프라인 시작")
        
        for stage in self.stages:
            # 단계별 락 획득 (같은 단계는 한 번에 1개만 실행)
            with self.stage_locks[stage]:
                # 1. 상태 확인
                status = self.get_stage_status(mall, mall_brand, stage)
                
                if status == 'DONE':
                    self.log(f"  [{mall_brand}] [{stage}] 이미 완료. 스킵.")
                    continue
                
                # 2. PARTIAL 모드 스킵 로직
                if self.run_mode == 'PARTIAL' and stage in ['CONVERT', 'TRANSLATE', 'IMAGE']:
                    self.log(f"  [{mall_brand}] [{stage}] PARTIAL 모드 → 스킵")
                    self.update_stage_status(mall, mall_brand, stage, 'DONE', "Skipped in PARTIAL mode")
                    continue
                
                # 3. 워커 실행
                target_brand_name = mall_brand if stage in ['COLLECT', 'CONVERT'] else buyma_brand
                self.log(f"  [{mall_brand}] [{stage}] 실행 중...")
                
                success = self.execute_worker(mall, mall_brand, stage, target_brand_name)
                
                if not success:
                    self.log(f"  [{mall_brand}] [{stage}] 실패 → 브랜드 중단", "ERROR")
                    return False
        
        return True
    
    def get_stage_status(self, mall: str, brand: str, stage: str) -> str:
        """단계 상태 조회"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                cursor.execute(
                    """SELECT status FROM pipeline_control
                       WHERE batch_id = %s 
                       AND mall_name = %s 
                       AND brand_name = %s 
                       AND stage = %s""",
                    (self.batch_id, mall, brand, stage)
                )
                result = cursor.fetchone()
                return result['status'] if result else 'PENDING'
    
    def update_stage_status(self, mall: str, brand: str, stage: str, 
                           status: str, error_msg: Optional[str] = None):
        """단계 상태 업데이트"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                sql = """
                    INSERT INTO pipeline_control 
                    (batch_id, mall_name, brand_name, run_mode, stage, status, error_msg, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        status = VALUES(status),
                        error_msg = VALUES(error_msg),
                        updated_at = NOW()
                """
                cursor.execute(sql, (
                    self.batch_id, mall, brand, self.run_mode, 
                    stage, status, error_msg
                ))
                self.conn.commit()
    
    def execute_worker(self, mall: str, brand: str, stage: str, target_brand_name: str) -> bool:
        """워커 스크립트 실행"""
        self.update_stage_status(mall, brand, stage, 'RUNNING')
        
        # 단계별 명령어 구성
        commands = []
        
        # --source 옵션 (mall_filter가 있으면 전달)
        source_args = ['--source', mall] if self.mall_filter else []

        if stage == 'COLLECT':
            # 소스별 collector 분기
            if mall == 'nextzennpack':
                commands.append([
                    sys.executable,
                    os.path.join('..', 'nextzennpack', 'nextzennpack_collector.py'),
                    '--brand', target_brand_name,
                    '--skip-existing'
                ])
            elif mall == 'kasina':
                commands.append([
                    sys.executable,
                    os.path.join('..', 'kasina', 'kasina_collector.py'),
                    '--brand', target_brand_name,
                    '--skip-existing'
                ])
            else:
                commands.append([
                    sys.executable,
                    'okmall_all_brands_collector.py',
                    '--brand', target_brand_name,
                    '--skip-existing'
                ])

        elif stage == 'CONVERT':
            commands.append([
                sys.executable,
                'raw_to_ace_converter.py',
                '--brand', target_brand_name,
                '--skip-translation'
            ] + source_args)

        elif stage == 'TRANSLATE':
            commands.append([
                sys.executable,
                'convert_to_japanese_gemini.py',
                '--brand', target_brand_name,
                '--price-checked-only'
            ] + source_args)

        elif stage == 'IMAGE':
            commands.append([
                sys.executable,
                'image_collector_parallel.py',
                '--brand', target_brand_name,
                '--price-checked-only'
            ] + source_args)
            commands.append([
                sys.executable,
                'r2_image_uploader.py',
                '--brand', target_brand_name
            ] + source_args)

        elif stage == 'PRICE':
            commands.append([
                sys.executable,
                'buyma_lowest_price_collector.py',
                '--brand', target_brand_name
            ] + source_args)

        elif stage == 'REGISTER':
            commands.append([
                sys.executable,
                'buyma_new_product_register.py',
                '--brand', target_brand_name
            ] + source_args)
        
        # 명령어 순차 실행
        for cmd in commands:
            script_name = os.path.basename(cmd[1])
            self.log(f"    [실행] {script_name} --brand {target_brand_name}")
            
            try:
                # subprocess로 워커 실행 (자식 프로세스도 UTF-8 강제)
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
                
                # 실시간 로그 출력 (콘솔 + 파일)
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
        
        # 모든 명령어 성공
        self.update_stage_status(mall, brand, stage, 'DONE')
        return True
    
    def finish_batch(self):
        """배치 정상 완료 처리"""
        with self.db_lock:
            with self.conn.cursor() as cursor:
                # 성공 브랜드 수 계산 (마지막 단계 기준)
                cursor.execute(
                    """SELECT COUNT(DISTINCT brand_name) as cnt 
                       FROM pipeline_control
                       WHERE batch_id = %s 
                       AND stage = %s 
                       AND status = 'DONE'""",
                    (self.batch_id, self.final_stage)
                )
                success_count = cursor.fetchone()['cnt']
                
                cursor.execute(
                    """UPDATE pipeline_batches 
                       SET status = 'COMPLETED', 
                           end_time = NOW(), 
                           success_brands = %s 
                       WHERE batch_id = %s""",
                    (success_count, self.batch_id)
                )
                self.conn.commit()
                
                self.log("=" * 60)
                self.log(f"배치 완료: {self.batch_id}")
                self.log(f"  성공: {success_count}개 브랜드")
                self.log(f"  최종 단계: {self.final_stage}")
                self.log("=" * 60)
    
    def mark_batch_failed(self):
        """배치 실패 처리"""
        if not self.conn or not self.batch_id:
            return
        
        try:
            with self.db_lock:
                with self.conn.cursor() as cursor:
                    cursor.execute(
                        """UPDATE pipeline_batches 
                           SET status = 'FAILED', 
                               end_time = NOW() 
                           WHERE batch_id = %s""",
                        (self.batch_id,)
                    )
                    self.conn.commit()
                    self.log(f"배치 실패 처리: {self.batch_id}", "ERROR")
        except Exception as e:
            self.log(f"배치 실패 처리 중 오류: {e}", "ERROR")


def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(
        description='BUYMA 파이프라인 오케스트레이터',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python orchestrator.py                          # 전체 브랜드, 전체 단계
  python orchestrator.py --brand NIKE             # 특정 브랜드만
  python orchestrator.py --exclude NIKE ADIDAS    # 특정 브랜드 제외
  python orchestrator.py --until PRICE            # REGISTER 전까지만
  python orchestrator.py --mode PARTIAL           # CONVERT, IMAGE 스킵
        """
    )
    
    parser.add_argument('--mode', 
                       type=str, 
                       choices=['FULL', 'PARTIAL'], 
                       default='FULL',
                       help='실행 모드 (FULL: 전체, PARTIAL: CONVERT/IMAGE 스킵)')
    
    parser.add_argument('--source',
                       type=str,
                       help='특정 소스 사이트만 지정 (예: okmall, kasina, nextzennpack)')
    
    parser.add_argument('--brand', 
                       type=str,
                       help='특정 브랜드만 지정 (예: NIKE)')
    
    parser.add_argument('--exclude', 
                       type=str, 
                       nargs='+',
                       help='제외할 브랜드 (예: --exclude NIKE ADIDAS)')
    
    parser.add_argument('--until',
                       type=str,
                       choices=['COLLECT', 'CONVERT', 'PRICE', 'TRANSLATE', 'IMAGE', 'REGISTER'],
                       help='지정 단계까지만 실행 (예: --until PRICE)')
    
    args = parser.parse_args()

    _write_log("=" * 60)
    _write_log("BUYMA 파이프라인 오케스트레이터")
    _write_log("=" * 60)

    orchestrator = Orchestrator(
        run_mode=args.mode,
        mall_filter=args.source,
        brand_filter=args.brand,
        exclude_brands=args.exclude,
        until_stage=args.until
    )

    try:
        orchestrator.run()
    finally:
        if _log_file:
            _log_file.close()


if __name__ == "__main__":
    main()
