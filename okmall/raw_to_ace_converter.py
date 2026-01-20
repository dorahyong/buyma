"""
raw_scraped_data 테이블 데이터를 ace 테이블로 변환하는 스크립트

작성일: 2026-01-18
목적: 오케이몰에서 수집한 원본 데이터(raw_scraped_data)를
     바이마 API 형식에 맞는 ace 테이블 데이터로 변환

실행 전 필수 조건:
1. ace_tables_create.sql 실행하여 테이블 생성
2. brand_mapping 테이블에 브랜드 매핑 데이터 입력
3. category_mapping 테이블에 카테고리 매핑 데이터 입력
4. shipping_config 테이블에 배송 설정 데이터 입력

사용법:
    python raw_to_ace_converter.py [--dry-run] [--limit N] [--brand BRAND_NAME]

옵션:
    --dry-run: 실제 저장하지 않고 변환 결과만 출력
    --limit N: 처리할 최대 레코드 수 지정
    --brand BRAND_NAME: 특정 브랜드만 처리 (예: NIKE)
"""

import json
import re
import argparse
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# =====================================================
# 설정값
# =====================================================

# DB 연결 정보
DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"

# 바이마 상품명 형식 템플릿
# 형식: 【즉발】브랜드 상품명【국내발】
BUYMA_NAME_TEMPLATE = "【即発】{brand} {product_name}【国内発】"

# 기본 구매 기한 (일 단위, 최대 90일)
DEFAULT_AVAILABLE_DAYS = 30

# 가격 마진율 (예: 1.3 = 30% 마진)
PRICE_MARGIN_RATE = 1.3

# 최소 판매 가격 (엔화)
MIN_PRICE_JPY = 5000

# =====================================================
# 바이마 API 고정값 (중요)
# =====================================================
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',       # 구매 지역 ID (고정)
    'shipping_area_id': '2002003000',     # 발송 지역 ID (고정)
    'theme_id': 98,                       # 테마 ID (고정)
    'duty': 'included',                   # 관세 정보 (고정)
    'shipping_method_id': 369,            # 배송 방법 ID (고정)
}

# 구매처명 템플릿: 브랜드명 + 正規販売店
BUYING_SHOP_NAME_TEMPLATE = "{brand_name}正規販売店"

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력 함수"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def safe_json_loads(json_str: str) -> Optional[Dict]:
    """안전한 JSON 파싱"""
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log(f"JSON 파싱 오류: {e}", "ERROR")
        return None


def convert_krw_to_jpy(krw_price: int, exchange_rate: float = 0.11) -> int:
    """
    원화를 엔화로 변환

    Args:
        krw_price: 원화 가격
        exchange_rate: 환율 (기본값: 100원 = 11엔)

    Returns:
        엔화 가격 (100엔 단위로 반올림)
    """
    jpy_price = int(krw_price * exchange_rate)
    # 100엔 단위로 반올림
    jpy_price = ((jpy_price + 50) // 100) * 100
    return max(jpy_price, MIN_PRICE_JPY)


def generate_reference_number() -> str:
    """
    바이마 관리번호(reference_number) 생성

    형식: UUID (Java의 UUID.randomUUID().toString()과 동일)
    예: 550e8400-e29b-41d4-a716-446655440000
    """
    return str(uuid.uuid4())


def convert_season_to_id(season_type: str) -> Optional[int]:
    """
    시즌 타입을 바이마 시즌 ID로 변환

    Args:
        season_type: 시즌 타입 문자열 (예: "25FW", "26SS", "24AW")

    Returns:
        바이마 시즌 ID (매핑 없으면 None)

    참고: 바이마 시즌 ID는 실제 API 문서에서 확인 필요
    아래는 예시 매핑입니다.
    """
    if not season_type:
        return None

    # 시즌 타입 정규화 (공백 제거, 대문자 변환)
    season = season_type.strip().upper()

    # 시즌 매핑 테이블 (예시 - 실제 바이마 시즌 ID로 수정 필요)
    # 형식: {시즌코드: 바이마_시즌_ID}
    season_mapping = {
        # 2024년
        '24SS': None,  # 2024 Spring/Summer
        '24FW': None,  # 2024 Fall/Winter
        '24AW': None,  # 2024 Autumn/Winter (FW와 동일)
        # 2025년
        '25SS': None,  # 2025 Spring/Summer
        '25FW': None,  # 2025 Fall/Winter
        '25AW': None,  # 2025 Autumn/Winter
        # 2026년
        '26SS': None,  # 2026 Spring/Summer
        '26FW': None,  # 2026 Fall/Winter
        '26AW': None,  # 2026 Autumn/Winter
    }

    return season_mapping.get(season, None)


def format_buyma_product_name(brand_name: str, product_name: str, model_id: str = None) -> str:
    """
    바이마 상품명 형식으로 변환

    Args:
        brand_name: 브랜드명 (영문)
        product_name: 상품명
        model_id: 모델번호 (선택)

    Returns:
        바이마 형식 상품명
    """
    # 상품명에서 불필요한 부분 제거
    clean_name = product_name.strip()

    # 모델번호가 있으면 추가
    if model_id:
        name = f"{brand_name} {clean_name} ({model_id})"
    else:
        name = f"{brand_name} {clean_name}"

    # 바이마 형식으로 포맷
    return BUYMA_NAME_TEMPLATE.format(brand=brand_name, product_name=clean_name)


def generate_product_comments(raw_data: Dict, options: List[Dict]) -> str:
    """
    바이마 상품 설명(comments) 생성

    최대 3000자 제한
    """
    comments_parts = []

    # 1. 브랜드 및 상품 정보
    comments_parts.append(f"■ 브랜드: {raw_data.get('brand_name_en', '')} ({raw_data.get('brand_name_kr', '')})")
    comments_parts.append(f"■ 상품명: {raw_data.get('product_name', '')}")

    if raw_data.get('model_id'):
        comments_parts.append(f"■ 모델번호: {raw_data.get('model_id')}")

    # 2. 카테고리 정보
    if raw_data.get('category_path'):
        comments_parts.append(f"■ 카테고리: {raw_data.get('category_path')}")

    # 3. 사이즈 정보
    if options:
        comments_parts.append("\n■ 사이즈 옵션:")
        for opt in options:
            size_info = f"  - {opt.get('tag_size', 'FREE')}"
            if opt.get('real_size'):
                size_info += f" (실측: {opt.get('real_size')})"
            comments_parts.append(size_info)

    # 4. 구매 안내
    comments_parts.append("\n■ 구매 안내:")
    comments_parts.append("  - 정품 100% 보장")
    comments_parts.append("  - 한국 국내 발송 (빠른 배송)")
    comments_parts.append("  - 재고 확인 후 구매 부탁드립니다")

    # 5. 주의사항
    comments_parts.append("\n■ 주의사항:")
    comments_parts.append("  - 모니터 환경에 따라 색상이 다르게 보일 수 있습니다")
    comments_parts.append("  - 실측 사이즈는 측정 방법에 따라 1-3cm 오차가 있을 수 있습니다")

    # 최대 3000자 제한
    full_comments = "\n".join(comments_parts)
    if len(full_comments) > 3000:
        full_comments = full_comments[:2997] + "..."

    return full_comments


def extract_color_from_options(options: List[Dict]) -> str:
    """옵션에서 색상 정보 추출"""
    if not options:
        return "FREE"

    colors = set()
    for opt in options:
        color = opt.get('color', '')
        if color and color.lower() not in ['', 'one color', '단일색상']:
            colors.add(color)

    if not colors:
        return "FREE"

    return list(colors)[0] if len(colors) == 1 else "マルチカラー"


# =====================================================
# 데이터 변환 클래스
# =====================================================

class RawToAceConverter:
    """raw_scraped_data를 ace 테이블로 변환하는 클래스"""

    def __init__(self, db_url: str):
        """
        Args:
            db_url: SQLAlchemy DB 연결 URL
        """
        self.engine = create_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)

        # 매핑 데이터 캐시
        self._brand_mapping_cache = {}
        self._category_mapping_cache = {}
        self._shipping_config_cache = None

        log("RawToAceConverter 초기화 완료")

    def load_brand_mapping(self) -> Dict[str, Dict]:
        """
        브랜드 매핑 데이터 로드
        기존 mall_brands 테이블에서 okmall 브랜드와 바이마 브랜드 ID 매핑을 로드

        mall_brands 테이블 구조:
        - mall_name: 쇼핑몰명 (okmall)
        - mall_brand_name_ko: 한글 브랜드명
        - mall_brand_name_en: 영문 브랜드명
        - buyma_brand_id: 바이마 브랜드 ID
        - buyma_brand_name: 바이마 브랜드명
        - is_active: 활성화 여부
        """
        if self._brand_mapping_cache:
            return self._brand_mapping_cache

        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT mall_brand_name_en, mall_brand_name_ko, buyma_brand_id, buyma_brand_name
                FROM mall_brands
                WHERE mall_name = 'okmall' AND is_active = 1
            """))

            for row in result:
                key = row[0].upper() if row[0] else ""
                self._brand_mapping_cache[key] = {
                    'source_brand_en': row[0],
                    'source_brand_kr': row[1],
                    'buyma_brand_id': int(row[2]) if row[2] else 0,
                    'buyma_brand_name': row[3]
                }

        log(f"브랜드 매핑 {len(self._brand_mapping_cache)}건 로드 완료 (mall_brands 테이블)")
        return self._brand_mapping_cache

    def load_category_mapping(self) -> Dict[str, Dict]:
        """
        카테고리 매핑 데이터 로드
        기존 mall_categories 테이블에서 okmall 카테고리와 바이마 카테고리 ID 매핑을 로드

        mall_categories 테이블 구조:
        - id: PK
        - mall_name: 쇼핑몰명 (okmall)
        - category_id: 카테고리 ID
        - gender: 성별
        - depth1, depth2, depth3: 카테고리 깊이별 이름
        - full_path: 전체 카테고리 경로
        - buyma_category_id: 바이마 카테고리 ID
        - is_active: 활성화 여부
        """
        if self._category_mapping_cache:
            return self._category_mapping_cache

        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT full_path, buyma_category_id, depth1, depth2, depth3
                FROM mall_categories
                WHERE mall_name = 'okmall' AND is_active = 1
            """))

            for row in result:
                key = row[0] if row[0] else ""
                # 카테고리명 조합 (depth1 > depth2 > depth3)
                category_name_parts = [p for p in [row[2], row[3], row[4]] if p]
                category_name = " > ".join(category_name_parts) if category_name_parts else None

                self._category_mapping_cache[key] = {
                    'source_category_path': row[0],
                    'buyma_category_id': int(row[1]) if row[1] else 0,
                    'buyma_category_name': category_name
                }

        log(f"카테고리 매핑 {len(self._category_mapping_cache)}건 로드 완료 (mall_categories 테이블)")
        return self._category_mapping_cache

    def load_shipping_config(self) -> Optional[Dict]:
        """
        기본 배송 설정 로드

        shipping_config 테이블 구조:
        - config_name: 설정명
        - buying_area_id: 구매 지역 ID (고정: 2002003000)
        - shipping_area_id: 발송 지역 ID (고정: 2002003000)
        - shipping_method_id: 배송 방법 ID (고정: 369)
        - theme_id: 테마 ID (고정: 98)
        - duty: 관세 정보 (고정: included)
        """
        if self._shipping_config_cache:
            return self._shipping_config_cache

        with self.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT config_name, buying_area_id, shipping_area_id,
                       shipping_method_id, theme_id, duty
                FROM shipping_config
                WHERE is_default = 1 AND is_active = 1
                LIMIT 1
            """))

            row = result.fetchone()
            if row:
                self._shipping_config_cache = {
                    'config_name': row[0],
                    'buying_area_id': row[1],
                    'shipping_area_id': row[2],
                    'shipping_method_id': row[3],
                    'theme_id': row[4],
                    'duty': row[5]
                }
                log(f"기본 배송 설정 로드: {row[0]}")
            else:
                log("기본 배송 설정이 없습니다. shipping_config 테이블을 확인하세요.", "WARNING")

        return self._shipping_config_cache

    def get_brand_info(self, brand_en: str) -> Dict:
        """
        브랜드 정보 조회

        매핑이 없으면 brand_id=0, brand_name=원본 브랜드명 반환
        """
        brand_mapping = self.load_brand_mapping()
        key = brand_en.upper() if brand_en else ""

        if key in brand_mapping:
            return brand_mapping[key]

        # 매핑이 없는 경우
        log(f"브랜드 매핑 없음: {brand_en}", "WARNING")
        return {
            'buyma_brand_id': 0,
            'buyma_brand_name': brand_en
        }

    def get_category_info(self, category_path: str) -> Dict:
        """
        카테고리 정보 조회

        매핑이 없거나 buyma_category_id가 NULL이면 0 반환
        """
        category_mapping = self.load_category_mapping()

        # 정확히 일치하는 매핑 찾기
        if category_path in category_mapping:
            result = category_mapping[category_path]
            # buyma_category_id가 None이면 0으로 처리
            if result.get('buyma_category_id') is None or result.get('buyma_category_id') == 0:
                log(f"카테고리 매핑 있으나 buyma_category_id 미설정: {category_path}", "WARNING")
                result['buyma_category_id'] = 0
            return result

        # 부분 일치 시도 (상위 카테고리로)
        path_parts = category_path.split(' > ') if category_path else []
        for i in range(len(path_parts), 0, -1):
            partial_path = ' > '.join(path_parts[:i])
            if partial_path in category_mapping:
                log(f"부분 카테고리 매핑 사용: {partial_path}", "INFO")
                result = category_mapping[partial_path]
                # buyma_category_id가 None이면 0으로 처리
                if result.get('buyma_category_id') is None or result.get('buyma_category_id') == 0:
                    result['buyma_category_id'] = 0
                return result

        log(f"카테고리 매핑 없음: {category_path}", "WARNING")
        return {
            'buyma_category_id': 0,  # 매핑 없음 - 수기 입력 필요
            'buyma_category_name': None
        }

    def fetch_raw_data(self, limit: int = None, brand: str = None) -> List[Dict]:
        """
        raw_scraped_data 테이블에서 변환 대상 데이터 조회

        Args:
            limit: 최대 조회 건수
            brand: 특정 브랜드만 조회

        Returns:
            raw 데이터 리스트
        """
        with self.engine.connect() as conn:
            query = """
                SELECT r.id, r.source_site, r.mall_product_id, r.brand_name_en,
                       r.brand_name_kr, r.product_name, r.p_name_full, r.model_id,
                       r.category_path, r.original_price, r.raw_price, r.stock_status,
                       r.raw_json_data, r.product_url, r.created_at, r.updated_at
                FROM raw_scraped_data r
                LEFT JOIN ace_products a ON r.id = a.raw_data_id
                WHERE a.id IS NULL
            """
            params = {}

            if brand:
                query += " AND UPPER(r.brand_name_en) = :brand"
                params['brand'] = brand.upper()

            query += " ORDER BY r.id"

            if limit:
                query += " LIMIT :limit"
                params['limit'] = limit

            result = conn.execute(text(query), params)

            raw_data_list = []
            for row in result:
                raw_data_list.append({
                    'id': row[0],
                    'source_site': row[1],
                    'mall_product_id': row[2],
                    'brand_name_en': row[3],
                    'brand_name_kr': row[4],
                    'product_name': row[5],
                    'p_name_full': row[6],
                    'model_id': row[7],
                    'category_path': row[8],
                    'original_price': float(row[9]) if row[9] else 0,
                    'raw_price': float(row[10]) if row[10] else 0,
                    'stock_status': row[11],
                    'raw_json_data': row[12],
                    'product_url': row[13],
                    'created_at': row[14],
                    'updated_at': row[15]
                })

            log(f"변환 대상 raw 데이터 {len(raw_data_list)}건 조회 완료")
            return raw_data_list

    def convert_single_raw_to_ace(self, raw_data: Dict) -> Dict:
        """
        단일 raw_scraped_data를 ace_products 형식으로 변환

        Args:
            raw_data: raw_scraped_data 레코드

        Returns:
            ace_products 형식 딕셔너리
        """
        # JSON 데이터 파싱
        json_data = safe_json_loads(raw_data.get('raw_json_data', '{}'))
        if not json_data:
            json_data = {}

        # 브랜드 정보
        brand_info = self.get_brand_info(raw_data.get('brand_name_en', ''))

        # 카테고리 정보
        category_info = self.get_category_info(raw_data.get('category_path', ''))

        # 배송 설정
        shipping_config = self.load_shipping_config() or {}

        # 옵션 정보 추출
        options = json_data.get('options', [])

        # 바이마 상품명 생성
        buyma_name = format_buyma_product_name(
            brand_name=raw_data.get('brand_name_en', ''),
            product_name=raw_data.get('product_name', ''),
            model_id=raw_data.get('model_id')
        )

        # 상품 설명 생성
        comments = generate_product_comments(raw_data, options)

        # 가격 계산 (원화 → 엔화 변환 + 마진)
        source_price = raw_data.get('raw_price', 0) or raw_data.get('original_price', 0)
        jpy_price = convert_krw_to_jpy(int(source_price))
        selling_price = int(jpy_price * PRICE_MARGIN_RATE)
        # 100엔 단위로 반올림
        selling_price = ((selling_price + 50) // 100) * 100

        # 구매 기한 계산
        available_until = (datetime.now() + timedelta(days=DEFAULT_AVAILABLE_DAYS)).strftime("%Y-%m-%d")

        # 구매처명 생성: 브랜드명 + 正規販売店
        brand_name_for_shop = raw_data.get('brand_name_en', '') or brand_info.get('buyma_brand_name', '')
        buying_shop_name = BUYING_SHOP_NAME_TEMPLATE.format(brand_name=brand_name_for_shop)

        # 시즌 정보 추출 및 변환
        season_type = json_data.get('season')  # raw_json_data에서 시즌 추출 (예: "25FW")
        season_id = convert_season_to_id(season_type)

        # ace_products 데이터 구성 (고정값 적용)
        ace_product = {
            'raw_data_id': raw_data['id'],
            'source_site': raw_data.get('source_site', 'okmall'),
            'reference_number': generate_reference_number(),             # UUID 형식
            'control': 'draft',
            'name': buyma_name,
            'comments': comments,
            'brand_id': brand_info.get('buyma_brand_id', 0),
            'brand_name': brand_info.get('buyma_brand_name'),
            'category_id': category_info.get('buyma_category_id', 0),
            'price': selling_price,
            'regular_price': None,
            'reference_price': int(raw_data.get('original_price', 0) * 0.11) if raw_data.get('original_price') else None,
            'available_until': available_until,
            # 고정값 적용
            'buying_area_id': BUYMA_FIXED_VALUES['buying_area_id'],      # 2002003000 (고정)
            'shipping_area_id': BUYMA_FIXED_VALUES['shipping_area_id'],  # 2002003000 (고정)
            'buying_shop_name': buying_shop_name,                         # 브랜드명 + 正規販売店
            'model_no': raw_data.get('model_id'),
            'theme_id': BUYMA_FIXED_VALUES['theme_id'],                  # 98 (고정)
            'season_id': season_id,                                       # 시즌 변환 결과 (없으면 None)
            'source_model_id': raw_data.get('model_id'),
            'duty': BUYMA_FIXED_VALUES['duty'],                          # included (고정)
            'source_product_url': raw_data.get('product_url'),
            'source_original_price': raw_data.get('original_price'),
            'source_sales_price': raw_data.get('raw_price'),
        }

        # 이미지 데이터
        images = json_data.get('images', [])
        ace_images = []
        for idx, img_url in enumerate(images[:20]):  # 최대 20장
            ace_images.append({
                'position': idx + 1,
                'source_image_url': img_url,
                'cloudflare_image_url': None,
                'buyma_image_path': None,
                'is_uploaded': 0
            })

        # 옵션 데이터 (색상/사이즈)
        ace_options = []
        colors = set()
        sizes = []

        for idx, opt in enumerate(options):
            color = opt.get('color', 'FREE')
            size = opt.get('tag_size', 'FREE')

            if color and color not in colors:
                colors.add(color)
                ace_options.append({
                    'option_type': 'color',
                    'value': color,
                    'master_id': None,  # 바이마 마스터 ID는 별도 매핑 필요
                    'position': len([o for o in ace_options if o['option_type'] == 'color']) + 1,
                    'source_option_value': color
                })

            if size and size not in [s['value'] for s in sizes]:
                details = None
                if opt.get('real_size'):
                    details = json.dumps({'실측': opt.get('real_size')}, ensure_ascii=False)

                sizes.append({
                    'option_type': 'size',
                    'value': size,
                    'master_id': None,
                    'position': len(sizes) + 1,
                    'details_json': details,
                    'source_option_value': size
                })

        ace_options.extend(sizes)

        # 색상이 없으면 기본값 추가
        if not colors:
            ace_options.append({
                'option_type': 'color',
                'value': 'FREE',
                'master_id': 99,  # 색상 미선택
                'position': 1,
                'source_option_value': None
            })

        # 사이즈가 없으면 기본값 추가
        if not sizes:
            ace_options.append({
                'option_type': 'size',
                'value': 'FREE',
                'master_id': 0,  # 사이즈 미선택
                'position': 1,
                'source_option_value': None
            })

        # 재고(Variants) 데이터
        ace_variants = []
        for opt in options:
            color_val = opt.get('color', 'FREE') or 'FREE'
            size_val = opt.get('tag_size', 'FREE') or 'FREE'

            stock_type = 'stock_in_hand' if opt.get('status') == 'in_stock' else 'out_of_stock'

            options_json = json.dumps([
                {'type': 'color', 'value': color_val},
                {'type': 'size', 'value': size_val}
            ], ensure_ascii=False)

            ace_variants.append({
                'color_value': color_val,
                'size_value': size_val,
                'options_json': options_json,
                'stock_type': stock_type,
                'stocks': 1 if stock_type == 'stock_in_hand' else 0,
                'source_option_code': opt.get('option_code'),
                'source_stock_status': opt.get('status')
            })

        # 옵션이 없으면 기본 Variant 추가
        if not ace_variants:
            ace_variants.append({
                'color_value': 'FREE',
                'size_value': 'FREE',
                'options_json': json.dumps([
                    {'type': 'color', 'value': 'FREE'},
                    {'type': 'size', 'value': 'FREE'}
                ]),
                'stock_type': 'stock_in_hand' if raw_data.get('stock_status') == 'in_stock' else 'out_of_stock',
                'stocks': 1,
                'source_option_code': None,
                'source_stock_status': raw_data.get('stock_status')
            })

        # 배송 방법 데이터 (고정값: 369)
        ace_shipping = [{
            'shipping_method_id': BUYMA_FIXED_VALUES['shipping_method_id']  # 369 (고정)
        }]

        return {
            'product': ace_product,
            'images': ace_images,
            'options': ace_options,
            'variants': ace_variants,
            'shipping': ace_shipping
        }

    def save_ace_data(self, ace_data: Dict) -> int:
        """
        변환된 ace 데이터를 DB에 저장

        Args:
            ace_data: convert_single_raw_to_ace() 반환값

        Returns:
            생성된 ace_products.id
        """
        with self.engine.connect() as conn:
            # 1. ace_products 저장
            product = ace_data['product']
            result = conn.execute(text("""
                INSERT INTO ace_products (
                    raw_data_id, source_site, reference_number, control, name, comments,
                    brand_id, brand_name, category_id, price, regular_price, reference_price,
                    available_until, buying_area_id, shipping_area_id, buying_shop_name,
                    model_no, theme_id, season_id, source_model_id, duty, source_product_url,
                    source_original_price, source_sales_price
                ) VALUES (
                    :raw_data_id, :source_site, :reference_number, :control, :name, :comments,
                    :brand_id, :brand_name, :category_id, :price, :regular_price, :reference_price,
                    :available_until, :buying_area_id, :shipping_area_id, :buying_shop_name,
                    :model_no, :theme_id, :season_id, :source_model_id, :duty, :source_product_url,
                    :source_original_price, :source_sales_price
                )
            """), product)

            ace_product_id = result.lastrowid

            # 2. ace_product_images 저장
            for img in ace_data['images']:
                img['ace_product_id'] = ace_product_id
                conn.execute(text("""
                    INSERT INTO ace_product_images (
                        ace_product_id, position, source_image_url, cloudflare_image_url,
                        buyma_image_path, is_uploaded
                    ) VALUES (
                        :ace_product_id, :position, :source_image_url, :cloudflare_image_url,
                        :buyma_image_path, :is_uploaded
                    )
                """), img)

            # 3. ace_product_options 저장
            for opt in ace_data['options']:
                opt['ace_product_id'] = ace_product_id
                conn.execute(text("""
                    INSERT INTO ace_product_options (
                        ace_product_id, option_type, value, master_id, position,
                        details_json, source_option_value
                    ) VALUES (
                        :ace_product_id, :option_type, :value, :master_id, :position,
                        :details_json, :source_option_value
                    )
                """), opt)

            # 4. ace_product_variants 저장
            for var in ace_data['variants']:
                var['ace_product_id'] = ace_product_id
                conn.execute(text("""
                    INSERT INTO ace_product_variants (
                        ace_product_id, color_value, size_value, options_json,
                        stock_type, stocks, source_option_code, source_stock_status
                    ) VALUES (
                        :ace_product_id, :color_value, :size_value, :options_json,
                        :stock_type, :stocks, :source_option_code, :source_stock_status
                    )
                """), var)

            # 5. ace_product_shipping 저장
            for ship in ace_data['shipping']:
                ship['ace_product_id'] = ace_product_id
                conn.execute(text("""
                    INSERT INTO ace_product_shipping (
                        ace_product_id, shipping_method_id
                    ) VALUES (
                        :ace_product_id, :shipping_method_id
                    )
                """), ship)

            conn.commit()

            return ace_product_id

    def run_conversion(self, limit: int = None, brand: str = None, dry_run: bool = False) -> Dict:
        """
        전체 변환 프로세스 실행

        Args:
            limit: 최대 처리 건수
            brand: 특정 브랜드만 처리
            dry_run: True면 실제 저장하지 않음

        Returns:
            처리 결과 통계
        """
        log("=" * 60)
        log("raw_scraped_data → ace 테이블 변환 시작")
        log("=" * 60)

        if dry_run:
            log("*** DRY RUN 모드 - 실제 저장하지 않음 ***", "WARNING")

        # 매핑 데이터 로드
        self.load_brand_mapping()
        self.load_category_mapping()
        self.load_shipping_config()

        # 변환 대상 데이터 조회
        raw_data_list = self.fetch_raw_data(limit=limit, brand=brand)

        if not raw_data_list:
            log("변환 대상 데이터가 없습니다.")
            return {'total': 0, 'success': 0, 'failed': 0}

        # 변환 처리
        success_count = 0
        failed_count = 0

        for idx, raw_data in enumerate(raw_data_list):
            try:
                log(f"[{idx+1}/{len(raw_data_list)}] 변환 중: raw_id={raw_data['id']}, "
                    f"brand={raw_data['brand_name_en']}, product={raw_data['product_name'][:30]}...")

                # 변환
                ace_data = self.convert_single_raw_to_ace(raw_data)

                if dry_run:
                    # Dry run 모드: 결과만 출력
                    log(f"  → 변환 완료 (저장 생략)")
                    log(f"    - 상품명: {ace_data['product']['name'][:50]}...")
                    log(f"    - 가격: {ace_data['product']['price']}엔")
                    log(f"    - 이미지: {len(ace_data['images'])}장")
                    log(f"    - 옵션: {len(ace_data['options'])}개")
                    log(f"    - Variants: {len(ace_data['variants'])}개")
                else:
                    # 실제 저장
                    ace_product_id = self.save_ace_data(ace_data)
                    log(f"  → 저장 완료: ace_product_id={ace_product_id}")

                success_count += 1

            except Exception as e:
                import traceback
                log(f"  → 변환 실패: {str(e)}", "ERROR")
                log(f"    상세 에러: {traceback.format_exc()}", "ERROR")
                failed_count += 1
                continue

        # 결과 출력
        log("=" * 60)
        log("변환 완료!")
        log(f"  총 처리: {len(raw_data_list)}건")
        log(f"  성공: {success_count}건")
        log(f"  실패: {failed_count}건")
        log("=" * 60)

        return {
            'total': len(raw_data_list),
            'success': success_count,
            'failed': failed_count
        }


# =====================================================
# 메인 실행
# =====================================================

def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='raw_scraped_data를 ace 테이블로 변환'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='실제 저장하지 않고 변환 결과만 출력'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='처리할 최대 레코드 수'
    )
    parser.add_argument(
        '--brand',
        type=str,
        default=None,
        help='특정 브랜드만 처리 (예: NIKE)'
    )

    args = parser.parse_args()

    try:
        converter = RawToAceConverter(DB_URL)
        result = converter.run_conversion(
            limit=args.limit,
            brand=args.brand,
            dry_run=args.dry_run
        )

        if result['failed'] > 0:
            log("일부 변환이 실패했습니다. 로그를 확인하세요.", "WARNING")
            exit(1)

    except Exception as e:
        log(f"변환 중 오류 발생: {str(e)}", "ERROR")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
