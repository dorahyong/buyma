# -*- coding: utf-8 -*-
"""
썸네일 생성기 — 상품 대표 이미지에 뱃지(追跡付 / 最安値挑戦 / 送料・関税込)를 얹어
Cloudflare R2 에 올리고, 그 URL 을 ace_product_thumbnails 테이블에 확보한다.

파이프라인 위치:
  원본 → r2_image_uploader.py(cloudflare_image_url 확보) → [이 파일] → buyma 등록

한 번 실행하면:
  1) 아직 썸네일 작업이 안 된 ace_product_images(cloudflare_image_url 있음)를 찾아
  2) 그 이미지에 좌측 하단 뱃지 + 그림자를 합성하고
  3) 완성본을 R2 에 업로드해 thumbnail_cloudflare_url 을 확보하고
  4) ace_product_thumbnails 에 기록한다. (재실행 안전 — 이미 만든 건 건너뜀)

사용:
  python thumbnail_generator.py                 # 전체(대표 이미지만)
  python thumbnail_generator.py --limit=10       # 10건만
  python thumbnail_generator.py --dry-run        # 합성만 하고 업로드/DB 안 함(로컬 미리보기 저장)
  python thumbnail_generator.py --ace-product-id=123
  python thumbnail_generator.py --retry-failed   # 실패분 재시도

사전 준비:
  pip install boto3 requests sqlalchemy pymysql pillow python-dotenv
  thumbnail/assets/ 에 뱃지 PNG 를 넣어주세요 (아래 BADGES 설정과 파일명 일치).

설정 요약: 대표이미지(position=1)만 대상 / 뱃지는 그림자 없는 투명 PNG(그림자는 코드에서 생성) / 좌측 하단 세로 스택.
세부값은 아래 BADGES·레이아웃·그림자 설정 참고.
"""
import argparse
import os
import io
import time
import hashlib
import threading
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import requests
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from PIL import Image, ImageFilter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
PREVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'preview')  # --dry-run 저장 위치
load_dotenv(os.path.join(BASE, '.env'), override=True)

# =====================================================
# 설정
# =====================================================
DB_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@"
                                    f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")

R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "buyma-images")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")
UPLOAD_PREFIX = "thumbnail"          # R2 안에서 썸네일은 별도 폴더에
REQUEST_TIMEOUT = 30
RETRY_COUNT = 2
RETRY_DELAY = 0.5
DEFAULT_WORKERS = 8

# --- 뱃지 설정 -------------------------------------------------------------
# thumbnail/assets/ 안의 파일명과 맞춰주세요. 아래→위 순서로 쌓입니다(bottom-up).
# size 는 '기대 크기'일 뿐이고, 실제로는 PNG 원본 크기를 그대로 씁니다(자동 맞춤 X).
# 위→아래 표시 순서(리스트 첫 항목이 맨 위). 追跡付 → 最安値挑戦 → 送料・関税込
BADGES = [
    {"file": "01_badge.png", "label": "追跡付",       "size": (191, 99)},
    {"file": "02_badge.png", "label": "最安値挑戦",    "size": (280, 99)},
    {"file": "03_badge.png", "label": "送料・関税込", "size": (305, 99)},
]

# --- 레이아웃 --------------------------------------------------------------
BADGE_SCALE = 1.15    # 뱃지 확대 배율 (1.0=원본 크기)
MARGIN_LEFT = 30      # 좌측 여백(px)
MARGIN_BOTTOM = 30    # 하단 여백(px)
GAP = 20              # 뱃지 간 간격(px)
ARRANGEMENT = "vertical"   # 'vertical'(세로 스택) | 'horizontal'(가로 나열)

# --- 그림자(우측 하단, 반투명) --------------------------------------------
SHADOW_ENABLED = True
SHADOW_OFFSET_X = 10
SHADOW_OFFSET_Y = 10
SHADOW_BLUR = 5           # PIL GaussianBlur 반경(≈ CSS blur 5)
SHADOW_COLOR = (0, 0, 0)  # #000000
SHADOW_OPACITY = 0.65     # 65% (더 진하게)

NOT_FOUND_VALUE = "not found"
TARGET_POSITION = 1       # 대표 이미지만


def log(msg: str, level: str = "INFO") -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level}] {msg}")


# =====================================================
# 뱃지 합성
# =====================================================
class BadgeCompositor:
    """뱃지 PNG 들을 미리 로드해두고, 상품 이미지 위에 좌측 하단 합성."""

    def __init__(self):
        self.badges: List[Image.Image] = []
        self._load_badges()

    def _load_badges(self) -> None:
        for b in BADGES:
            path = os.path.join(ASSETS_DIR, b["file"])
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"뱃지 이미지 없음: {path}\n→ thumbnail/assets/ 에 '{b['file']}' 를 넣어주세요.")
            img = Image.open(path).convert("RGBA")
            if BADGE_SCALE != 1.0:
                img = img.resize((round(img.width * BADGE_SCALE), round(img.height * BADGE_SCALE)),
                                 Image.LANCZOS)
            self.badges.append(img)
            log(f"뱃지 로드: {b['file']} ({img.width}x{img.height}, x{BADGE_SCALE})")

    @staticmethod
    def _make_shadow(badge: Image.Image):
        # returns (shadow_canvas: RGBA, pad: int)
        """뱃지 실루엣 기반 반투명 드롭섀도우 생성.
        여백을 둔 캔버스에 (offset+blur) 만큼 자리를 확보해 잘리지 않게 함."""
        pad = SHADOW_BLUR * 3 + max(SHADOW_OFFSET_X, SHADOW_OFFSET_Y)
        canvas = Image.new("RGBA", (badge.width + pad * 2, badge.height + pad * 2), (0, 0, 0, 0))
        alpha = badge.split()[3].point(lambda a: int(a * SHADOW_OPACITY))
        solid = Image.new("RGBA", badge.size, SHADOW_COLOR + (0,))
        solid.putalpha(alpha)
        canvas.paste(solid, (pad + SHADOW_OFFSET_X, pad + SHADOW_OFFSET_Y), solid)
        canvas = canvas.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))
        return canvas, pad

    def compose(self, product: Image.Image) -> Image.Image:
        """product(RGBA) 위에 뱃지들을 좌측 하단에 얹은 결과 반환."""
        base = product.convert("RGBA")
        W, H = base.size

        # 배치 좌표 계산 (좌측 하단 기준, 리스트 순서대로 아래→위 / 좌→우)
        positions: List[tuple] = []
        if ARRANGEMENT == "vertical":
            # 리스트 첫 항목이 맨 위. 아래에서부터 좌표를 잡고 순서를 되돌려 정렬.
            y_bottom = H - MARGIN_BOTTOM
            for badge in reversed(self.badges):
                y = y_bottom - badge.height
                positions.append((MARGIN_LEFT, y))
                y_bottom = y - GAP
            positions.reverse()
        else:  # horizontal
            x = MARGIN_LEFT
            for badge in self.badges:
                y = H - MARGIN_BOTTOM - badge.height
                positions.append((x, y))
                x += badge.width + GAP

        for badge, (bx, by) in zip(self.badges, positions):
            if SHADOW_ENABLED:
                shadow, pad = self._make_shadow(badge)
                base.alpha_composite(shadow, (bx - pad, by - pad))
            base.alpha_composite(badge, (bx, by))

        return base


# =====================================================
# 데이터
# =====================================================
@dataclass
class ImageRecord:
    id: int
    ace_product_id: int
    position: int
    cloudflare_image_url: str


@dataclass
class GenResult:
    image_id: int
    ace_product_id: int
    source_cf_url: str
    success: bool
    thumb_url: Optional[str] = None
    error: Optional[str] = None


# =====================================================
# 생성기
# =====================================================
class ThumbnailGenerator:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.engine = create_engine(DB_URL)
        self.compositor = BadgeCompositor()
        if not dry_run:
            self._validate_r2()
            self.s3 = self._s3_client()
        else:
            self.s3 = None
            os.makedirs(PREVIEW_DIR, exist_ok=True)
        log(f"ThumbnailGenerator 초기화 (dry_run={dry_run}, 배치={ARRANGEMENT}, 그림자={SHADOW_ENABLED})")

    @staticmethod
    def _validate_r2():
        missing = [k for k, v in {
            "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID, "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY,
            "R2_ENDPOINT_URL": R2_ENDPOINT_URL, "R2_PUBLIC_URL": R2_PUBLIC_URL}.items() if not v]
        if missing:
            raise ValueError(f"누락된 R2 설정: {', '.join(missing)} (.env 확인)")

    @staticmethod
    def _s3_client():
        return boto3.client('s3', endpoint_url=R2_ENDPOINT_URL,
                            aws_access_key_id=R2_ACCESS_KEY_ID, aws_secret_access_key=R2_SECRET_ACCESS_KEY,
                            config=Config(signature_version='s3v4', retries={'max_attempts': 3}))

    def fetch_pending(self, limit=None, retry_failed=False, ace_product_id=None,
                      brand=None, published_only=False) -> List[ImageRecord]:
        """썸네일 작업이 안 된 (또는 실패한) 대표 이미지 조회.
        ace_product_thumbnails 에 완료 기록(is_generated=1)이 없는 이미지를 고른다.
        published_only=True → 이미 BUYMA 게시된 상품만(스토어에 실제 반영되는 대상).
        brand → 특정 브랜드만(brand_name LIKE)."""
        with self.engine.connect() as conn:
            params = {'pos': TARGET_POSITION}
            base = """
                SELECT api.id, api.ace_product_id, api.position, api.cloudflare_image_url
                FROM ace_product_images api
                JOIN ace_products a ON a.id = api.ace_product_id
                LEFT JOIN ace_product_thumbnails t ON t.image_id = api.id
                WHERE api.position = :pos
                  AND api.cloudflare_image_url IS NOT NULL
                  AND api.cloudflare_image_url != ''
            """
            if retry_failed:
                base += " AND t.id IS NOT NULL AND t.is_generated = 0 "
            else:
                base += " AND (t.id IS NULL OR (t.is_generated = 0 AND t.generate_error IS NULL)) "
            if published_only:
                base += " AND a.is_published = 1 AND a.buyma_product_id IS NOT NULL "
            if brand:
                base += " AND UPPER(a.brand_name) LIKE :brand "
                params['brand'] = f"%{brand.upper()}%"
            if ace_product_id:
                base += " AND api.ace_product_id = :apid "
                params['apid'] = ace_product_id
            base += " ORDER BY api.ace_product_id "
            if limit:
                base += f" LIMIT {int(limit)} "
            rows = conn.execute(text(base), params)
            imgs = [ImageRecord(r[0], r[1], r[2], r[3]) for r in rows]
            log(f"썸네일 대기 이미지: {len(imgs)}개")
            return imgs

    def _download(self, url: str) -> Optional[bytes]:
        headers = {'User-Agent': 'Mozilla/5.0'}
        for attempt in range(RETRY_COUNT):
            try:
                r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                return r.content
            except requests.RequestException as e:
                log(f"  다운로드 실패({attempt+1}/{RETRY_COUNT}): {e}", "WARNING")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAY)
        return None

    def _upload(self, data: bytes, filename: str) -> Optional[str]:
        key = f"{UPLOAD_PREFIX}/{filename}"
        for attempt in range(RETRY_COUNT):
            try:
                self.s3.put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=data, ContentType='image/jpeg')
                return f"{R2_PUBLIC_URL.rstrip('/')}/{key}"
            except ClientError as e:
                log(f"  R2 업로드 실패({attempt+1}/{RETRY_COUNT}): {e}", "WARNING")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAY)
        return None

    def process(self, img: ImageRecord) -> GenResult:
        res = GenResult(img.id, img.ace_product_id, img.cloudflare_image_url, success=False)
        try:
            raw = self._download(img.cloudflare_image_url)
            if not raw:
                res.error = "원본 다운로드 실패"
                return res
            product = Image.open(io.BytesIO(raw))
            composed = self.compositor.compose(product)
            # 뱃지 그림자에 투명이 있지만 최종은 흰 배경 합성해 JPEG 로(바이마는 webp/투명 지양)
            out = Image.new("RGB", composed.size, (255, 255, 255))
            out.paste(composed, mask=composed.split()[3])
            buf = io.BytesIO()
            out.save(buf, format='JPEG', quality=95)
            data = buf.getvalue()

            url_hash = hashlib.md5(img.cloudflare_image_url.encode()).hexdigest()[:8]
            filename = f"{img.ace_product_id}_{img.position:03d}_{url_hash}_thumb.jpg"

            if self.dry_run:
                path = os.path.join(PREVIEW_DIR, filename)
                with open(path, 'wb') as f:
                    f.write(data)
                res.thumb_url = path
                res.success = True
            else:
                url = self._upload(data, filename)
                if url:
                    res.thumb_url = url
                    res.success = True
                else:
                    res.error = "R2 업로드 실패"
            return res
        except Exception as e:
            res.error = str(e)
            return res

    def save(self, res: GenResult) -> None:
        """ace_product_thumbnails 에 upsert (image_id 유니크)."""
        with self.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO ace_product_thumbnails
                    (image_id, ace_product_id, thumbnail_cloudflare_url, source_cf_url, is_generated, generate_error)
                VALUES (:image_id, :apid, :url, :src, :ok, :err)
                ON DUPLICATE KEY UPDATE
                    thumbnail_cloudflare_url = VALUES(thumbnail_cloudflare_url),
                    source_cf_url            = VALUES(source_cf_url),
                    is_generated             = VALUES(is_generated),
                    generate_error           = VALUES(generate_error)
            """), {'image_id': res.image_id, 'apid': res.ace_product_id,
                   'url': res.thumb_url if res.success else None,
                   'src': res.source_cf_url,
                   'ok': 1 if res.success else 0,
                   'err': None if res.success else res.error})
            conn.commit()

    def run(self, limit=None, retry_failed=False, ace_product_id=None, workers=DEFAULT_WORKERS,
            brand=None, published_only=False) -> Dict:
        log("=" * 60)
        log(f"썸네일 생성 시작 (병렬 {workers}스레드)"
            + (f" | 게시분만" if published_only else "") + (f" | 브랜드={brand}" if brand else ""))
        if self.dry_run:
            log(f"*** DRY RUN — 업로드/DB 없음, 미리보기 저장: {PREVIEW_DIR} ***", "WARNING")
        imgs = self.fetch_pending(limit, retry_failed, ace_product_id, brand, published_only)
        if not imgs:
            log("작업할 이미지가 없습니다.")
            return {'total': 0, 'success': 0, 'failed': 0}

        stats = {'total': len(imgs), 'success': 0, 'failed': 0}
        lock = threading.Lock()

        def work(idx, img):
            log(f"[{idx+1}/{len(imgs)}] image_id={img.id}, product={img.ace_product_id}")
            res = self.process(img)
            if res.success:
                log(f"  => 성공: {res.thumb_url}", "SUCCESS")
                with lock:
                    stats['success'] += 1
            else:
                log(f"  => 실패: {res.error}", "ERROR")
                with lock:
                    stats['failed'] += 1
            if not self.dry_run:
                self.save(res)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(work, i, im) for i, im in enumerate(imgs)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    log(f"스레드 오류: {e}", "ERROR")

        log("=" * 60)
        log(f"완료 — 총 {stats['total']} / 성공 {stats['success']} / 실패 {stats['failed']}")
        return stats


def main():
    ap = argparse.ArgumentParser(description='뱃지 얹은 BUYMA 썸네일 생성 + R2 업로드')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true', help='합성만, 미리보기 저장(업로드/DB 없음)')
    ap.add_argument('--retry-failed', action='store_true', help='실패분만 재시도')
    ap.add_argument('--ace-product-id', type=int, default=None)
    ap.add_argument('--brand', type=str, default=None, help='특정 브랜드만 (brand_name LIKE)')
    ap.add_argument('--published-only', action='store_true', help='이미 BUYMA 게시된 상품만')
    ap.add_argument('--workers', type=int, default=DEFAULT_WORKERS)
    args = ap.parse_args()

    try:
        gen = ThumbnailGenerator(dry_run=args.dry_run)
        gen.run(limit=args.limit, retry_failed=args.retry_failed,
                ace_product_id=args.ace_product_id, workers=args.workers,
                brand=args.brand, published_only=args.published_only)
    except Exception as e:
        log(f"실행 오류: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == '__main__':
    main()
