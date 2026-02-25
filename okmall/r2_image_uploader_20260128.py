"""
Cloudflare R2에 이미지 업로드 및 ace_product_images 테이블 업데이트 스크립트

기능:
- ace_product_images 테이블에서 source_image_url 조회
- 원본 이미지를 Cloudflare R2에 업로드
- 생성된 R2 퍼블릭 URL을 테이블에 업데이트

사용법:
    python r2_image_uploader.py                    # 전체 실행
    python r2_image_uploader.py --limit=10         # 최대 10개만 처리
    python r2_image_uploader.py --dry-run          # 테스트 (업로드 안함)
    python r2_image_uploader.py --retry-failed     # 실패한 것만 재시도
    python r2_image_uploader.py --ace-product-id=123  # 특정 상품 ID만 처리

사전 준비:
    pip install boto3 requests sqlalchemy pymysql

환경 변수 (.env 파일):
    R2_ACCESS_KEY_ID=your_access_key_id
    R2_SECRET_ACCESS_KEY=your_secret_access_key
    R2_ENDPOINT_URL=https://94fae922764d4f66d866710a7206e438.r2.cloudflarestorage.com
    R2_BUCKET_NAME=buyma-images
    R2_PUBLIC_URL=https://pub-xxxxx.r2.dev

작성일: 2026-01-19
"""

import argparse
import os
import time
import hashlib
import mimetypes
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from urllib.parse import urlparse
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import requests
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# =====================================================
# 설정
# =====================================================

# 데이터베이스 설정
DB_URL = os.getenv("DATABASE_URL", "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4")

# Cloudflare R2 설정
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "https://94fae922764d4f66d866710a7206e438.r2.cloudflarestorage.com")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "buyma-images")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")  # https://pub-xxxxx.r2.dev

# 업로드 설정
UPLOAD_PREFIX = "upload"  # R2 버킷 내 폴더 경로 (소스별 구분)
REQUEST_TIMEOUT = 30  # 이미지 다운로드 타임아웃 (초)
RETRY_COUNT = 3  # 실패 시 재시도 횟수
RETRY_DELAY = 2  # 재시도 간 대기 시간 (초)

# NOT FOUND 표시
NOT_FOUND_VALUE = "not found"


# =====================================================
# 데이터 클래스
# =====================================================

@dataclass
class ImageRecord:
    """이미지 레코드"""
    id: int
    ace_product_id: int
    position: int
    source_image_url: str
    cloudflare_image_url: Optional[str] = None
    is_uploaded: int = 0


@dataclass
class UploadResult:
    """업로드 결과"""
    image_id: int
    success: bool
    r2_url: Optional[str] = None
    error_message: Optional[str] = None


# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def generate_filename(source_url: str, ace_product_id: int, position: int) -> str:
    """
    소스 URL과 상품 정보로 고유한 파일명 생성

    Args:
        source_url: 원본 이미지 URL
        ace_product_id: 상품 ID
        position: 이미지 순서

    Returns:
        고유한 파일명 (예: 12345_001_abc123.jpg)
    """
    # URL에서 확장자 추출
    parsed = urlparse(source_url)
    path = parsed.path
    ext = Path(path).suffix.lower()

    # 확장자가 없거나 이상한 경우 기본값
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        ext = '.jpg'

    # URL 해시로 고유성 확보
    url_hash = hashlib.md5(source_url.encode()).hexdigest()[:8]

    # 파일명: {상품ID}_{순서}_{해시}.{확장자}
    filename = f"{ace_product_id}_{position:03d}_{url_hash}{ext}"

    return filename


def get_content_type(filename: str) -> str:
    """파일명으로 Content-Type 추출"""
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or 'image/jpeg'


# =====================================================
# R2 업로더 클래스
# =====================================================

class R2ImageUploader:
    """Cloudflare R2 이미지 업로더"""

    def __init__(self, dry_run: bool = False):
        """
        초기화

        Args:
            dry_run: True면 실제 업로드 안함
        """
        self.dry_run = dry_run
        self.engine = create_engine(DB_URL)

        # R2 클라이언트 초기화
        if not dry_run:
            self._validate_config()
            self.s3_client = self._create_s3_client()
        else:
            self.s3_client = None

        log(f"R2ImageUploader 초기화 (dry_run={dry_run})")

    def _validate_config(self) -> None:
        """R2 설정 검증"""
        missing = []
        if not R2_ACCESS_KEY_ID:
            missing.append("R2_ACCESS_KEY_ID")
        if not R2_SECRET_ACCESS_KEY:
            missing.append("R2_SECRET_ACCESS_KEY")
        if not R2_ENDPOINT_URL:
            missing.append("R2_ENDPOINT_URL")
        if not R2_PUBLIC_URL:
            missing.append("R2_PUBLIC_URL")

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

    def fetch_pending_images(self, limit: int = None, retry_failed: bool = False, ace_product_id: int = None, brand: str = None) -> List[ImageRecord]:
        """
        업로드 대기 중인 이미지 조회

        Args:
            limit: 최대 조회 건수
            retry_failed: True면 실패한 것만 조회
            ace_product_id: 특정 상품 ID만 조회
            brand: 특정 브랜드만 조회

        Returns:
            ImageRecord 리스트
        """
        with self.engine.connect() as conn:
            params = {'not_found': NOT_FOUND_VALUE}

            if retry_failed:
                # 업로드 실패한 것만 (upload_error가 있는 경우)
                query = """
                    SELECT api.id, api.ace_product_id, api.position, api.source_image_url, api.cloudflare_image_url, api.is_uploaded
                    FROM ace_product_images api
                    JOIN ace_products ap ON api.ace_product_id = ap.id
                    WHERE api.source_image_url IS NOT NULL
                      AND api.source_image_url != :not_found
                      AND api.source_image_url != ''
                      AND api.upload_error IS NOT NULL
                """
            else:
                # 아직 업로드 안된 것 (is_uploaded = 0, cloudflare_image_url이 NULL)
                query = """
                    SELECT api.id, api.ace_product_id, api.position, api.source_image_url, api.cloudflare_image_url, api.is_uploaded
                    FROM ace_product_images api
                    JOIN ace_products ap ON api.ace_product_id = ap.id
                    WHERE api.source_image_url IS NOT NULL
                      AND api.source_image_url != :not_found
                      AND api.source_image_url != ''
                      AND (api.cloudflare_image_url IS NULL OR api.cloudflare_image_url = '')
                      AND api.is_uploaded = 0
                """

            if ace_product_id:
                query += " AND api.ace_product_id = :ace_product_id"
                params['ace_product_id'] = ace_product_id
            
            if brand:
                query += " AND UPPER(ap.brand_name) LIKE :brand"
                params['brand'] = f"%{brand.upper()}%"

            query += " ORDER BY api.ace_product_id, api.position"

            if limit:
                query += f" LIMIT {limit}"

            result = conn.execute(text(query), params)

            images = []
            for row in result:
                images.append(ImageRecord(
                    id=row[0],
                    ace_product_id=row[1],
                    position=row[2],
                    source_image_url=row[3],
                    cloudflare_image_url=row[4],
                    is_uploaded=row[5] or 0
                ))

            log(f"업로드 대기 이미지: {len(images)}개")
            return images

    def download_image(self, url: str) -> Optional[bytes]:
        """
        이미지 다운로드

        Args:
            url: 이미지 URL

        Returns:
            이미지 바이트 데이터 또는 None
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://www.wconcept.co.kr/'
        }

        for attempt in range(RETRY_COUNT):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    stream=True
                )
                response.raise_for_status()
                return response.content

            except requests.RequestException as e:
                log(f"  다운로드 실패 (시도 {attempt + 1}/{RETRY_COUNT}): {e}", "WARNING")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAY)

        return None

    def upload_to_r2(self, image_data: bytes, filename: str) -> Optional[str]:
        """
        R2에 이미지 업로드

        Args:
            image_data: 이미지 바이트 데이터
            filename: 저장할 파일명

        Returns:
            퍼블릭 URL 또는 None
        """
        key = f"{UPLOAD_PREFIX}/{filename}"
        content_type = get_content_type(filename)

        for attempt in range(RETRY_COUNT):
            try:
                self.s3_client.put_object(
                    Bucket=R2_BUCKET_NAME,
                    Key=key,
                    Body=image_data,
                    ContentType=content_type
                )

                # 퍼블릭 URL 생성
                public_url = f"{R2_PUBLIC_URL.rstrip('/')}/{key}"
                return public_url

            except ClientError as e:
                log(f"  R2 업로드 실패 (시도 {attempt + 1}/{RETRY_COUNT}): {e}", "WARNING")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAY)

        return None

    def update_database(self, image_id: int, cloudflare_url: str, success: bool, error_message: str = None) -> None:
        """
        DB 업데이트

        Args:
            image_id: 이미지 레코드 ID
            cloudflare_url: Cloudflare R2 퍼블릭 URL
            success: 성공 여부
            error_message: 오류 메시지 (실패 시)
        """
        with self.engine.connect() as conn:
            if success:
                conn.execute(text("""
                    UPDATE ace_product_images
                    SET cloudflare_image_url = :cloudflare_url,
                        is_uploaded = 1,
                        upload_error = NULL
                    WHERE id = :id
                """), {'id': image_id, 'cloudflare_url': cloudflare_url})
            else:
                # 실패 시 upload_error에 오류 메시지 저장
                conn.execute(text("""
                    UPDATE ace_product_images
                    SET upload_error = :error_message
                    WHERE id = :id
                """), {'id': image_id, 'error_message': error_message})

            conn.commit()

    def process_single_image(self, image: ImageRecord) -> UploadResult:
        """
        단일 이미지 처리

        Args:
            image: ImageRecord

        Returns:
            UploadResult
        """
        result = UploadResult(image_id=image.id, success=False)

        try:
            # 1. 이미지 다운로드
            image_data = self.download_image(image.source_image_url)
            if not image_data:
                result.error_message = "다운로드 실패"
                return result

            # 2. 파일명 생성
            filename = generate_filename(
                image.source_image_url,
                image.ace_product_id,
                image.position
            )

            # 3. R2 업로드 (dry_run이 아닐 때만)
            if self.dry_run:
                # 테스트 모드: 가상의 URL 생성
                result.r2_url = f"{R2_PUBLIC_URL or 'https://test.r2.dev'}/{UPLOAD_PREFIX}/{filename}"
                result.success = True
            else:
                r2_url = self.upload_to_r2(image_data, filename)
                if r2_url:
                    result.r2_url = r2_url
                    result.success = True
                else:
                    result.error_message = "R2 업로드 실패"

            return result

        except Exception as e:
            result.error_message = str(e)
            return result

    def run(self, limit: int = None, retry_failed: bool = False, ace_product_id: int = None, brand: str = None) -> Dict:
        """
        전체 실행

        Args:
            limit: 최대 처리 건수
            retry_failed: True면 실패한 것만 재시도
            ace_product_id: 특정 상품 ID만 처리
            brand: 특정 브랜드만 처리

        Returns:
            실행 통계
        """
        log("=" * 60)
        log("R2 이미지 업로드 시작")
        log("=" * 60)

        if self.dry_run:
            log("*** DRY RUN 모드 - 실제 업로드 안함 ***", "WARNING")
        if retry_failed:
            log("*** 실패한 이미지만 재시도 ***", "INFO")
        if ace_product_id:
            log(f"상품 ID 필터: {ace_product_id}")
        if brand:
            log(f"브랜드 필터: {brand}")
        if limit:
            log(f"최대 처리: {limit}건")

        # 대기 중인 이미지 조회
        images = self.fetch_pending_images(limit=limit, retry_failed=retry_failed, ace_product_id=ace_product_id, brand=brand)

        if not images:
            log("업로드할 이미지가 없습니다.")
            return {'total': 0, 'success': 0, 'failed': 0}

        stats = {'total': len(images), 'success': 0, 'failed': 0}

        for idx, image in enumerate(images):
            log(f"\n[{idx + 1}/{len(images)}] id={image.id}, product={image.ace_product_id}, pos={image.position}")
            log(f"  원본: {image.source_image_url[:80]}...")

            # 이미지 처리
            result = self.process_single_image(image)

            if result.success:
                log(f"  => 성공: {result.r2_url}", "SUCCESS")
                stats['success'] += 1

                # DB 업데이트 (dry_run이 아닐 때만)
                if not self.dry_run:
                    self.update_database(image.id, result.r2_url, True)
            else:
                log(f"  => 실패: {result.error_message}", "ERROR")
                stats['failed'] += 1

                # DB 업데이트 (dry_run이 아닐 때만)
                if not self.dry_run:
                    self.update_database(image.id, None, False, result.error_message)

        # 결과 출력
        log("\n" + "=" * 60)
        log("업로드 완료!")
        log(f"  총 처리: {stats['total']}건")
        log(f"  성공: {stats['success']}건")
        log(f"  실패: {stats['failed']}건")
        log("=" * 60)

        return stats


# =====================================================
# 메인
# =====================================================

def main():
    parser = argparse.ArgumentParser(
        description='Cloudflare R2에 이미지 업로드 및 DB 업데이트'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='최대 처리 건수'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='테스트 모드 (실제 업로드 안함)'
    )
    parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='실패한 이미지만 재시도'
    )
    parser.add_argument(
        '--ace-product-id',
        type=int,
        default=None,
        help='특정 상품 ID만 처리'
    )
    parser.add_argument(
        '--brand',
        type=str,
        default=None,
        help='특정 브랜드만 처리'
    )

    args = parser.parse_args()
    headless = args.headless.lower() != 'false' if hasattr(args, 'headless') else True

    try:
        # 업로더 실행
        uploader = R2ImageUploader(dry_run=args.dry_run)
        stats = uploader.run(
            limit=args.limit,
            retry_failed=args.retry_failed,
            ace_product_id=args.ace_product_id,
            brand=args.brand
        )

        if stats.get('failed', 0) > 0:
            log("일부 이미지 업로드에 실패했습니다. --retry-failed 옵션으로 재시도할 수 있습니다.", "WARNING")

    except Exception as e:
        log(f"실행 오류: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
