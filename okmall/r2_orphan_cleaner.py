# -*- coding: utf-8 -*-
"""
Cloudflare R2 고아 파일 삭제 스크립트

사용법:
    python r2_orphan_cleaner.py                      # 실제 삭제
    python r2_orphan_cleaner.py --dry-run            # 테스트 (삭제 안함)
    python r2_orphan_cleaner.py --file=urls.csv      # 특정 CSV 파일 사용

CSV 파일 형식:
    - 첫 번째 행은 헤더 (cloudflare_image_url)
    - URL 형식: https://pub-a807a826f8c3469590e8...r2.dev/upload/12345_001_abc123.jpg

작성일: 2026-02-09
"""

import os
import csv
import argparse
import time
from datetime import datetime
from typing import List, Tuple
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# =====================================================
# 설정
# =====================================================

# Cloudflare R2 설정 (.env에서 로드)
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "https://94fae922764d4f66d866710a7206e438.r2.cloudflarestorage.com")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "buyma-images")

# 배치 삭제 설정
BATCH_SIZE = 1000  # R2는 한 번에 최대 1000개 삭제 가능
DELETE_DELAY = 0.5  # 배치 간 딜레이 (초)

# 기본 CSV 파일명
DEFAULT_CSV_FILE = "r2_data.csv"


# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def extract_key_from_url(url: str) -> str:
    """
    R2 URL에서 파일 키(경로) 추출
    
    예: https://pub-xxx.r2.dev/upload/12345_001_abc.jpg
        → upload/12345_001_abc.jpg
    """
    if not url:
        return ""
    
    parsed = urlparse(url)
    # 경로에서 앞의 '/' 제거
    key = parsed.path.lstrip('/')
    return key


def load_urls_from_csv(csv_file: str) -> List[str]:
    """CSV 파일에서 URL 목록 로드"""
    urls = []
    
    if not os.path.exists(csv_file):
        log(f"CSV 파일을 찾을 수 없습니다: {csv_file}", "ERROR")
        return urls
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        
        # 첫 번째 행이 헤더인지 확인
        first_row = next(reader, None)
        if first_row:
            # 헤더가 아니면 (URL로 시작하면) 데이터로 처리
            if first_row[0].startswith('http'):
                urls.append(first_row[0])
        
        # 나머지 행 처리
        for row in reader:
            if row and row[0].startswith('http'):
                urls.append(row[0])
    
    log(f"CSV에서 {len(urls)}개 URL 로드 완료")
    return urls


# =====================================================
# R2 삭제 클래스
# =====================================================

class R2OrphanCleaner:
    """R2 고아 파일 삭제기"""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.s3_client = None
        
        if not dry_run:
            self._validate_config()
            self.s3_client = self._create_s3_client()
        
        log(f"R2OrphanCleaner 초기화 (dry_run={dry_run})")
    
    def _validate_config(self) -> None:
        """R2 설정 검증"""
        missing = []
        if not R2_ACCESS_KEY_ID:
            missing.append("R2_ACCESS_KEY_ID")
        if not R2_SECRET_ACCESS_KEY:
            missing.append("R2_SECRET_ACCESS_KEY")
        if not R2_ENDPOINT_URL:
            missing.append("R2_ENDPOINT_URL")
        
        if missing:
            raise ValueError(f"누락된 R2 설정: {', '.join(missing)}\n.env 파일을 확인해주세요.")
    
    def _create_s3_client(self):
        """S3 호환 클라이언트 생성"""
        return boto3.client(
            's3',
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=Config(
                signature_version='s3v4',
                retries={'max_attempts': 3}
            )
        )
    
    def delete_batch(self, keys: List[str]) -> Tuple[int, int]:
        """
        배치로 파일 삭제 (최대 1000개)
        
        Returns:
            (성공 개수, 실패 개수)
        """
        if not keys:
            return 0, 0
        
        if self.dry_run:
            log(f"  [DRY-RUN] {len(keys)}개 파일 삭제 예정")
            return len(keys), 0
        
        # S3 DeleteObjects 형식으로 변환
        delete_objects = {'Objects': [{'Key': key} for key in keys]}
        
        try:
            response = self.s3_client.delete_objects(
                Bucket=R2_BUCKET_NAME,
                Delete=delete_objects
            )
            
            deleted = len(response.get('Deleted', []))
            errors = len(response.get('Errors', []))
            
            # 에러 상세 로그
            for error in response.get('Errors', []):
                log(f"  삭제 실패: {error.get('Key')} - {error.get('Message')}", "WARNING")
            
            return deleted, errors
            
        except ClientError as e:
            log(f"  배치 삭제 오류: {e}", "ERROR")
            return 0, len(keys)
    
    def run(self, csv_file: str) -> dict:
        """
        전체 실행
        
        Args:
            csv_file: URL 목록이 담긴 CSV 파일 경로
        
        Returns:
            실행 통계
        """
        log("=" * 60)
        log("R2 고아 파일 삭제 시작")
        log("=" * 60)
        
        if self.dry_run:
            log("*** DRY RUN 모드 - 실제 삭제 안함 ***", "WARNING")
        
        # 1. CSV에서 URL 로드
        urls = load_urls_from_csv(csv_file)
        if not urls:
            log("삭제할 URL이 없습니다.")
            return {'total': 0, 'deleted': 0, 'failed': 0}
        
        # 2. URL → 파일 키 변환
        keys = []
        for url in urls:
            key = extract_key_from_url(url)
            if key:
                keys.append(key)
        
        log(f"삭제 대상 파일: {len(keys)}개")
        
        # 3. 배치 단위로 삭제
        total_deleted = 0
        total_failed = 0
        batch_count = (len(keys) + BATCH_SIZE - 1) // BATCH_SIZE
        
        for i in range(0, len(keys), BATCH_SIZE):
            batch_num = i // BATCH_SIZE + 1
            batch_keys = keys[i:i + BATCH_SIZE]
            
            log(f"배치 {batch_num}/{batch_count}: {len(batch_keys)}개 처리 중...")
            
            deleted, failed = self.delete_batch(batch_keys)
            total_deleted += deleted
            total_failed += failed
            
            log(f"  → 삭제: {deleted}개, 실패: {failed}개")
            
            # 배치 간 딜레이
            if i + BATCH_SIZE < len(keys):
                time.sleep(DELETE_DELAY)
        
        # 4. 결과 출력
        log("=" * 60)
        log("삭제 완료!")
        log(f"  총 대상: {len(keys)}개")
        log(f"  삭제 성공: {total_deleted}개")
        log(f"  삭제 실패: {total_failed}개")
        
        # 예상 용량 절감
        estimated_size_mb = total_deleted * 0.5  # 평균 500KB 가정
        log(f"  예상 용량 절감: 약 {estimated_size_mb:.0f} MB ({estimated_size_mb/1024:.1f} GB)")
        log("=" * 60)
        
        return {
            'total': len(keys),
            'deleted': total_deleted,
            'failed': total_failed
        }


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='R2 고아 파일 삭제기')
    parser.add_argument(
        '--file',
        type=str,
        default=DEFAULT_CSV_FILE,
        help=f'삭제할 URL 목록 CSV 파일 (기본: {DEFAULT_CSV_FILE})'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='테스트 모드 (실제 삭제 안함)'
    )
    
    args = parser.parse_args()
    
    try:
        cleaner = R2OrphanCleaner(dry_run=args.dry_run)
        result = cleaner.run(args.file)
        
        if result['failed'] > 0:
            log("일부 파일 삭제에 실패했습니다.", "WARNING")
        
    except Exception as e:
        log(f"실행 오류: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()