"""
raw_scraped_data 테이블 데이터를 ace 테이블로 변환하는 스크립트

작성일: 2026-01-20
수정일: 2026-01-22
목적: 오케이몰에서 수집한 원본 데이터(raw_scraped_data)를
     바이마 API 형식에 맞는 ace 테이블 데이터로 변환

실행 전 필수 조건:
1. ace_tables_create.sql 실행하여 테이블 생성
2. mall_brands 테이블에 브랜드 매핑 데이터 입력
3. mall_categories 테이블에 카테고리 매핑 데이터 입력
4. shipping_config 테이블에 배송 설정 데이터 입력

사용법:
    python raw_to_ace_converter_20260120.py [--dry-run] [--limit N] [--brand BRAND_NAME]

옵션:
    --dry-run: 실제 저장하지 않고 변환 결과만 출력
    --limit N: 처리할 최대 레코드 수 지정
    --brand BRAND_NAME: 특정 브랜드만 처리 (예: "A BATHING APE")
"""

import json
import re
import argparse
import uuid
import os
import sys
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# convert_to_japanese.py에서 번역 맵과 함수 가져오기
from convert_to_japanese_gemini import KOREAN_TO_JAPANESE, convert_to_japanese

# .env 파일 로드 (프로젝트 루트에서)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# 설정값
# =====================================================

# DB 연결 정보
DB_URL = os.getenv('DATABASE_URL', 'mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4')

# 바이마 상품명 형식 템플릿
# 형식: 【즉발】브랜드 상품명【국내발】
BUYMA_NAME_TEMPLATE = "【即発】{brand} {product_name}【国内発】"

# 기본 구매 기한 (일 단위, 최대 90일)
DEFAULT_AVAILABLE_DAYS = 90

# 마진 및 가격 설정 (README_buyma_api.md 기준)
BUYMA_SALES_FEE_RATE = 0.055  # 바이마 판매수수료 5.5%
VAT_REFUND_RATE = 1 / 11      # 부가세 환급율
DEFAULT_SHIPPING_FEE = 15000  # 기본 배송비

# 환율 설정 (원/엔)
# 참고: README_buyma_api.md에 따르면 환율은 9.2로 고정
EXCHANGE_RATE_WON_TO_YEN = 9.2
EXCHANGE_RATE_KRW_TO_JPY = round(1 / EXCHANGE_RATE_WON_TO_YEN, 4)  # 약 0.1087
EXCHANGE_RATE_FOR_REFERENCE_PRICE = 0.1  # 엔화 정가는 KRW / 10 고정 로직 유지
MIN_PRICE_JPY = 500  # 최소 엔화 판매가

# =====================================================
# 바이마 API 고정값 (중요)
# =====================================================
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',       # 구매 지역 ID (고정)
    'shipping_area_id': '2002003000',     # 발송 지역 ID (고정)
    'theme_id': 98,                       # 테마 ID (고정)
    'duty': 'included',                   # 관세 정보 (고정)
    'shipping_method_id': 1063035,            # 배송 방법 ID (고정)
}

# 구매처명 템플릿: 브랜드명 + 正規販売店
BUYING_SHOP_NAME_TEMPLATE = "{brand_name}正規販売店"

# =====================================================
# 사이즈 상세(options.details) 매핑
# =====================================================

# 측정 키 → 일본어 키 매핑 (영문 + 한국어 지원)
MEASUREMENT_KEY_TO_JAPANESE = {
    # === 영문 키 (기존 데이터 호환) ===
    'shoulder': '肩幅',
    'chest': '胸囲',
    'total_length': '着丈',
    'sleeve_length': '袖丈',
    'sleeve_width': '袖幅',
    'waist': 'ウエスト',
    'hip': 'ヒップ',
    'rise': '股上',
    'inseam': '股下',
    'thigh': 'もも周り',
    'hem': 'すそ周り',
    'outseam': '総丈',
    'width': '横',
    'height': '高さ',
    'depth': 'マチ',
    'handle': '持ち手',
    'heel_height': 'ヒール高',
    'foot_width': '足幅',
    'length': '長さ',
    'circumference': '円周',
    'thickness': '厚み',

    # === 한국어 키 (새로운 원본 데이터) ===
    # 상의
    '어깨 너비': '肩幅',
    '어깨너비': '肩幅',
    '어깨': '肩幅',
    '가슴 너비': '胸囲',
    '가슴너비': '胸囲',
    '가슴단면': '胸囲',
    '가슴': '胸囲',
    '총장': '着丈',
    '총기장': '着丈',
    '팔길이': '袖丈',
    '팔 길이': '袖丈',
    '소매길이': '袖丈',
    '소매 길이': '袖丈',
    '소매너비': '袖幅',
    '소매 너비': '袖幅',
    '소매단면': '袖幅',

    # 하의
    '허리 너비': 'ウエスト',
    '허리너비': 'ウエスト',
    '허리단면': 'ウエスト',
    '허리': 'ウエスト',
    '엉덩이 너비': 'ヒップ',
    '엉덩이너비': 'ヒップ',
    '엉덩이단면': 'ヒップ',
    '엉덩이': 'ヒップ',
    '밑위': '股上',
    '안기장': '股下',
    '허벅지 너비': 'もも周り',
    '허벅지너비': 'もも周り',
    '허벅지단면': 'もも周り',
    '허벅지': 'もも周り',
    '밑단 너비': 'すそ周り',
    '밑단너비': 'すそ周り',
    '밑단단면': 'すそ周り',
    '밑단': 'すそ周り',
    '바깥기장': '総丈',

    # 가방/소품
    '가로': '横',
    '가로 길이': '横',
    '하단 가로': '底横',
    '세로': '縦',
    '세로 길이': '縦',
    '높이': '高さ',
    '두께': 'マチ',
    '손잡이 높이': '持ち手',
    '손잡이': '持ち手',
    '중량': '重さ',
    '무게': '重さ',
    '굽 높이': 'ヒール高',
    '머리둘레 최소 길이': '頭周り',
    '챙 길이': 'つば',


    # 제외 항목 (None으로 매핑하면 무시됨)
    '숄더끈 높이': None,
    '숄더끈 높이(최대)': None,
    '숄더끈 높이(최소)': None,
}

# 너비→둘레 변환이 필요한 키 (x2)
MEASUREMENT_KEYS_NEED_DOUBLE = {'가슴', '가슴 너비', '가슴너비', '가슴단면', 'chest',
                                '허벅지', '허벅지 너비', '허벅지너비', '허벅지단면', 'thigh',
                                '밑단', '밑단 너비', '밑단너비', '밑단단면', 'hem'}

# size_details2.csv 경로
SIZE_DETAILS_CSV_PATH = os.path.join(os.path.dirname(__file__), 'size_details2.csv')

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력 함수"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

def sanitize_text(text: str) -> str:
    """
    바이마 API 거부 문자를 정제: 유럽형 특수문자 제거 및 특수 기호 치환
    """
    if not text:
        return ""
    
    # 1. NFD 정규화로 악센트 분리 (é -> e + ´)
    normalized = unicodedata.normalize('NFD', text)
    # 2. Mn(악센트 기호) 카테고리만 필터링하여 제거 후 재결합
    sanitized = "".join([c for c in normalized if unicodedata.category(c) != 'Mn'])
    
    # 3. 추가적인 특수 기호들 안전한 문자로 변경
    replacements = {
        '’': "'", '‘': "'", '“': '"', '”': '"', 
        '–': '-', '—': '-', '™': '(TM)', '®': '(R)', 
        '©': '(C)', '…': '...', '½': '1/2', '⅓': '1/3', '¼': '1/4'
    }
    for old, new in replacements.items():
        sanitized = sanitized.replace(old, new)
        
    return unicodedata.normalize('NFC', sanitized)


def safe_json_loads(json_str: str) -> Optional[Dict]:
    """안전한 JSON 파싱"""
    if not json_str:
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log(f"JSON 파싱 오류: {e}", "ERROR")
        return None


def extract_numeric_value(text: str) -> Optional[str]:
    """
    측정값 텍스트에서 숫자만 추출

    예: "45cm 전후" → "45.0"
         "720g 전후" → "720.0"
         "54.5cm" → "54.5"
    """
    if not text:
        return None

    # 숫자(소수점 포함) 추출
    match = re.search(r'([\d.]+)', str(text))
    if match:
        value = match.group(1)
        # 소수점이 없으면 .0 추가
        if '.' not in value:
            value = value + '.0'
        return value
    return None


def load_category_size_keys(csv_path: str = None) -> Dict[int, List[str]]:
    """
    size_details2.csv에서 카테고리별 허용 사이즈 키 로드

    Returns:
        Dict[category_id, List[일본어 키]]
    """
    if csv_path is None:
        csv_path = SIZE_DETAILS_CSV_PATH

    category_keys = {}

    try:
        import csv
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # 헤더 스킵

            for row in reader:
                if len(row) >= 3:
                    jp_key = row[0].strip().replace('\ufeff', '')
                    category_id_str = row[2].strip()

                    if jp_key and category_id_str and category_id_str.isdigit():
                        category_id = int(category_id_str)
                        if category_id not in category_keys:
                            category_keys[category_id] = []
                        if jp_key not in category_keys[category_id]:
                            category_keys[category_id].append(jp_key)

        log(f"카테고리별 사이즈 키 매핑 {len(category_keys)}개 카테고리 로드 완료")
    except FileNotFoundError:
        log(f"size_details2.csv 파일을 찾을 수 없습니다: {csv_path}", "WARNING")
    except Exception as e:
        log(f"size_details2.csv 로드 실패: {e}", "WARNING")

    return category_keys


def convert_measurements_to_details(
    size_measurements: Dict[str, Any],
    category_id: int = 0,
    category_keys_map: Dict[int, List[str]] = None
) -> List[Dict[str, str]]:
    """
    raw measurements를 BUYMA options.details 형식으로 변환

    Args:
        size_measurements: 한 사이즈의 측정값 딕셔너리
            예: {"어깨 너비": "45cm 전후", "가슴 너비": "54cm 전후", ...}
            또는 {"shoulder": "45cm 전후", "chest": "54cm 전후", ...}
        category_id: BUYMA 카테고리 ID (허용 키 필터링용)
        category_keys_map: 카테고리별 허용 키 매핑 (없으면 필터링 안함)

    Returns:
        BUYMA details 형식 리스트
            예: [{"key": "肩幅", "value": "45.0"}, {"key": "胸囲", "value": "108.0"}]
    """
    if not size_measurements:
        return []

    # 카테고리별 허용 키 가져오기
    allowed_keys = None
    if category_keys_map and category_id in category_keys_map:
        allowed_keys = category_keys_map[category_id]

    details = []

    for orig_key, value in size_measurements.items():
        # summary, weight 등 제외
        if orig_key in ['summary', 'weight', 'filling_weight']:
            continue

        # 키 → 일본어 키 변환 (영문/한국어 모두 지원)
        jp_key = MEASUREMENT_KEY_TO_JAPANESE.get(orig_key)

        # 매핑에 없는 경우 Gemini API로 번역 시도
        if jp_key is None and orig_key not in MEASUREMENT_KEY_TO_JAPANESE:
            # 번역 전에 한국어인지 확인
            import re
            if re.search(r'[가-힣]', orig_key):
                try:
                    jp_key = convert_to_japanese(orig_key)
                    log(f"  → 측정 키 Gemini 번역: '{orig_key}' → '{jp_key}'")
                except Exception as e:
                    log(f"  → 측정 키 번역 실패: '{orig_key}' - {e}", "WARNING")
                    continue
            else:
                # 영문이지만 매핑에 없음
                continue

        # jp_key가 None인 경우 (제외 항목으로 명시된 경우)
        if not jp_key:
            continue

        # 카테고리별 허용 키 필터링 (허용 목록이 있는 경우만)
        if allowed_keys and jp_key not in allowed_keys:
            continue

        # 숫자값 추출
        numeric_value = extract_numeric_value(value)
        if not numeric_value:
            continue

        # 너비 → 둘레 변환 (x2)
        # MEASUREMENT_KEYS_NEED_DOUBLE 세트에 있는 키는 x2 변환
        # - 가슴너비 → 胸囲(가슴둘레)
        # - 허벅지너비 → もも周り(허벅지둘레)
        # - 밑단너비 → すそ周り(밑단둘레)
        if orig_key in MEASUREMENT_KEYS_NEED_DOUBLE:
            try:
                numeric_value = str(float(numeric_value) * 2)
            except ValueError:
                pass

        details.append({
            'key': jp_key,
            'value': numeric_value
        })

    return details


def convert_krw_to_jpy(krw_price: int, exchange_rate: float = None) -> int:
    """
    원화를 엔화로 변환

    Args:
        krw_price: 원화 가격
        exchange_rate: 환율 (기본값: EXCHANGE_RATE_KRW_TO_JPY)

    Returns:
        엔화 가격 (100엔 단위로 반올림)
    """
    if exchange_rate is None:
        exchange_rate = EXCHANGE_RATE_KRW_TO_JPY
    
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
    """
    if not season_type:
        return None

    # 시즌 타입 정규화 (공백 제거, 대문자 변환)
    season = season_type.strip().upper()

    # 시즌 매핑 테이블 (예시 - 실제 바이마 시즌 ID로 수정 필요)
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

    # 바이마 형식으로 포맷 (브랜드명은 영문만 사용)
    return f"【{brand_name}】{clean_name}" + (f"【{model_id}】" if model_id else "")


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
        self._color_master_id_cache = {}
        self._category_size_keys_cache = {}  # 카테고리별 사이즈 키 캐시

        log("RawToAceConverter 초기화 완료")

    def load_color_master_id_mapping(self) -> Dict[str, int]:
        """
        색상명을 바이마 마스터 ID로 매핑하는 딕셔너리 로드
        """
        if self._color_master_id_cache:
            return self._color_master_id_cache

        # colors.csv 파일 경로
        colors_csv_path = os.path.join(os.path.dirname(__file__), 'colors.csv')
        
        try:
            import csv
            with open(colors_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)  # 헤더 스킵
                
                for row in reader:
                    if len(row) >= 2:
                        master_id = int(row[0])
                        # 일본어 색상명과 한국어 색상명 모두 매핑
                        jp_name = row[1].strip() if row[1] else ""
                        kr_name = row[5].strip() if len(row) > 5 and row[5] else ""
                        
                        # 키워드 기반 매핑
                        if 'ホワイト' in jp_name or '白' in jp_name:
                            self._color_master_id_cache['WHITE'] = master_id
                            self._color_master_id_cache['화이트'] = master_id
                            self._color_master_id_cache['흰색'] = master_id
                        if 'ブラック' in jp_name or '黒' in jp_name:
                            self._color_master_id_cache['BLACK'] = master_id
                            self._color_master_id_cache['블랙'] = master_id
                            self._color_master_id_cache['검정'] = master_id
                        if 'グレー' in jp_name or '灰色' in jp_name:
                            self._color_master_id_cache['GREY'] = master_id
                            self._color_master_id_cache['GRAY'] = master_id
                            self._color_master_id_cache['그레이'] = master_id
                            self._color_master_id_cache['회색'] = master_id
                        if 'ブラウン' in jp_name or '茶色' in jp_name:
                            self._color_master_id_cache['BROWN'] = master_id
                            self._color_master_id_cache['브라운'] = master_id
                            self._color_master_id_cache['갈색'] = master_id
                        if 'ベージュ' in jp_name:
                            self._color_master_id_cache['BEIGE'] = master_id
                            self._color_master_id_cache['베이지'] = master_id
                        if 'グリーン' in jp_name or '緑' in jp_name:
                            self._color_master_id_cache['GREEN'] = master_id
                            self._color_master_id_cache['그린'] = master_id
                            self._color_master_id_cache['초록'] = master_id
                        if 'ブルー' in jp_name or '青' in jp_name:
                            self._color_master_id_cache['BLUE'] = master_id
                            self._color_master_id_cache['블루'] = master_id
                            self._color_master_id_cache['파랑'] = master_id
                        if 'パープル' in jp_name or '紫' in jp_name:
                            self._color_master_id_cache['PURPLE'] = master_id
                            self._color_master_id_cache['퍼플'] = master_id
                            self._color_master_id_cache['보라'] = master_id
                        if 'イエロー' in jp_name or '黄色' in jp_name:
                            self._color_master_id_cache['YELLOW'] = master_id
                            self._color_master_id_cache['옐로우'] = master_id
                            self._color_master_id_cache['노랑'] = master_id
                        if 'ピンク' in jp_name:
                            self._color_master_id_cache['PINK'] = master_id
                            self._color_master_id_cache['핑크'] = master_id
                        if 'レッド' in jp_name or '赤' in jp_name:
                            self._color_master_id_cache['RED'] = master_id
                            self._color_master_id_cache['레드'] = master_id
                            self._color_master_id_cache['빨강'] = master_id
                        if 'オレンジ' in jp_name:
                            self._color_master_id_cache['ORANGE'] = master_id
                            self._color_master_id_cache['오렌지'] = master_id
                        if 'シルバー' in jp_name or '銀色' in jp_name:
                            self._color_master_id_cache['SILVER'] = master_id
                            self._color_master_id_cache['실버'] = master_id
                            self._color_master_id_cache['은색'] = master_id
                        if 'ゴールド' in jp_name or '金色' in jp_name:
                            self._color_master_id_cache['GOLD'] = master_id
                            self._color_master_id_cache['골드'] = master_id
                            self._color_master_id_cache['금색'] = master_id
                        if 'クリア' in jp_name or '透明' in jp_name:
                            self._color_master_id_cache['CLEAR'] = master_id
                            self._color_master_id_cache['클리어'] = master_id
                            self._color_master_id_cache['투명'] = master_id
                        if 'ネイビー' in jp_name or '紺' in jp_name:
                            self._color_master_id_cache['NAVY'] = master_id
                            self._color_master_id_cache['네이비'] = master_id
                            self._color_master_id_cache['남색'] = master_id
                        if 'マルチカラー' in jp_name:
                            self._color_master_id_cache['MULTI'] = master_id
                            self._color_master_id_cache['멀티'] = master_id
                            self._color_master_id_cache['MULTICOLOR'] = master_id
                            
            log(f"색상 마스터 ID 매핑 {len(self._color_master_id_cache)}건 로드 완료")
        except FileNotFoundError:
            log(f"colors.csv 파일을 찾을 수 없습니다: {colors_csv_path}", "WARNING")
        except Exception as e:
            log(f"colors.csv 파일 로드 실패: {e}", "WARNING")

        return self._color_master_id_cache

    def get_color_master_id(self, color_name: str) -> int:
        """색상명으로 바이마 마스터 ID 조회"""
        if not color_name:
            return 99
        
        color_mapping = self.load_color_master_id_mapping()
        color_upper = color_name.upper().strip()
        
        if color_upper in color_mapping:
            return color_mapping[color_upper]
        
        for key, master_id in color_mapping.items():
            if key in color_upper or color_upper in key:
                return master_id

        return 99

    def load_category_size_keys_mapping(self) -> Dict[int, List[str]]:
        """카테고리별 허용 사이즈 키 매핑 로드"""
        if self._category_size_keys_cache:
            return self._category_size_keys_cache

        self._category_size_keys_cache = load_category_size_keys()
        return self._category_size_keys_cache

    def load_brand_mapping(self) -> Dict[str, Dict]:
        """브랜드 매핑 데이터 로드"""
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

        log(f"브랜드 매핑 {len(self._brand_mapping_cache)}건 로드 완료")
        return self._brand_mapping_cache

    def load_category_mapping(self) -> Dict[str, Dict]:
        """카테고리 매핑 데이터 로드 (배송비 포함)"""
        if self._category_mapping_cache:
            return self._category_mapping_cache

        with self.engine.connect() as conn:
            # buyma_master_categories_data와 조인하여 배송비도 함께 가져옴
            result = conn.execute(text("""
                SELECT mc.full_path, mc.buyma_category_id, mc.depth1, mc.depth2, mc.depth3, 
                       bmcd.expected_shipping_fee
                FROM mall_categories mc
                LEFT JOIN buyma_master_categories_data bmcd ON mc.buyma_category_id = bmcd.buyma_category_id
                WHERE mc.mall_name = 'okmall' AND mc.is_active = 1
            """))

            for row in result:
                key = row[0] if row[0] else ""
                category_name_parts = [p for p in [row[2], row[3], row[4]] if p]
                category_name = " > ".join(category_name_parts) if category_name_parts else None

                self._category_mapping_cache[key] = {
                    'source_category_path': row[0],
                    'buyma_category_id': int(row[1]) if row[1] else 0,
                    'buyma_category_name': category_name,
                    'expected_shipping_fee': int(row[5]) if row[5] is not None else DEFAULT_SHIPPING_FEE
                }

        log(f"카테고리 매핑 {len(self._category_mapping_cache)}건 로드 완료")
        return self._category_mapping_cache

    def load_shipping_config(self) -> Optional[Dict]:
        """기본 배송 설정 로드"""
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
                log("기본 배송 설정이 없습니다.", "WARNING")

        return self._shipping_config_cache

    def get_brand_info(self, brand_en: str) -> Dict:
        brand_mapping = self.load_brand_mapping()
        key = brand_en.upper() if brand_en else ""
        if key in brand_mapping:
            return brand_mapping[key]
        return {'buyma_brand_id': 0, 'buyma_brand_name': brand_en}

    def get_category_info(self, category_path: str) -> Dict:
        category_mapping = self.load_category_mapping()
        if category_path in category_mapping:
            result = category_mapping[category_path]
            if result.get('buyma_category_id') is None or result.get('buyma_category_id') == 0:
                result['buyma_category_id'] = 0
            return result

        path_parts = category_path.split(' > ') if category_path else []
        for i in range(len(path_parts), 0, -1):
            partial_path = ' > '.join(path_parts[:i])
            if partial_path in category_mapping:
                result = category_mapping[partial_path]
                if result.get('buyma_category_id') is None or result.get('buyma_category_id') == 0:
                    result['buyma_category_id'] = 0
                return result

        return {'buyma_category_id': 0, 'buyma_category_name': None}

    def fetch_raw_data(self, limit: int = None, brand: str = None, raw_id: int = None) -> List[Dict]:
        with self.engine.connect() as conn:
            query = """
                SELECT r.id, r.source_site, r.mall_product_id, r.brand_name_en,
                       r.brand_name_kr, r.product_name, r.p_name_full, r.model_id,
                       r.category_path, r.original_price, r.raw_price, r.stock_status,
                       r.raw_json_data, r.product_url, r.created_at, r.updated_at
                FROM raw_scraped_data r
                LEFT JOIN ace_products a ON r.id = a.raw_data_id
                WHERE 1=1
            """
            params = {}
            if raw_id:
                query += " AND r.id = :raw_id"
                params['raw_id'] = raw_id
            else:
                # 특정 ID 지정이 없을 때만 미변환 데이터 조회
                query += " AND a.id IS NULL"
                
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
                    'id': row[0], 'source_site': row[1], 'mall_product_id': row[2],
                    'brand_name_en': row[3], 'brand_name_kr': row[4], 'product_name': row[5],
                    'p_name_full': row[6], 'model_id': row[7], 'category_path': row[8],
                    'original_price': float(row[9]) if row[9] else 0,
                    'raw_price': float(row[10]) if row[10] else 0,
                    'stock_status': row[11], 'raw_json_data': row[12],
                    'product_url': row[13], 'created_at': row[14], 'updated_at': row[15]
                })
            log(f"변환 대상 raw 데이터 {len(raw_data_list)}건 조회 완료")
            return raw_data_list

    def convert_single_raw_to_ace(self, raw_data: Dict) -> Dict:
        json_data = safe_json_loads(raw_data.get('raw_json_data', '{}')) or {}
        brand_info = self.get_brand_info(raw_data.get('brand_name_en', ''))
        category_info = self.get_category_info(raw_data.get('category_path', ''))
        options = json_data.get('options', [])

        # 1. 상품명 생성 및 정제 (한국어 → 일본어 번역 포함)
        product_name_jp = convert_to_japanese(raw_data.get('product_name', ''))
        buyma_name = format_buyma_product_name(
            brand_name=raw_data.get('brand_name_en', ''),
            product_name=product_name_jp,
            model_id=raw_data.get('model_id')
        )
        buyma_name = sanitize_text(buyma_name)

        # 2. 기본 설명 생성 및 정제
        comments = generate_product_comments(raw_data, options)
        comments = sanitize_text(comments)

        original_price_krw = float(raw_data.get('original_price', 0))
        purchase_price_krw = float(raw_data.get('raw_price', 0))
        
        # 엔화 정가는 기존 로직(KRW / 10) 유지
        original_price_jpy = int(original_price_krw * EXCHANGE_RATE_FOR_REFERENCE_PRICE)
        original_price_jpy = ((original_price_jpy + 50) // 100) * 100

        # 엔화 매입가 (KRW / 9.2) - 신규 추가
        purchase_price_jpy = int(purchase_price_krw / EXCHANGE_RATE_WON_TO_YEN)

        # 예상 배송비 - 신규 추가
        expected_shipping_fee = category_info.get('expected_shipping_fee', DEFAULT_SHIPPING_FEE)

        # 판매가(price)는 0으로 초기 세팅 (최저가 수집기에서 결정)
        selling_price = 0

        available_until = (datetime.now() + timedelta(days=DEFAULT_AVAILABLE_DAYS)).strftime("%Y-%m-%d")
        brand_name_for_shop = raw_data.get('brand_name_en', '') or brand_info.get('buyma_brand_name', '')
        buying_shop_name = BUYING_SHOP_NAME_TEMPLATE.format(brand_name=brand_name_for_shop)

        season_type = json_data.get('season')
        season_id = convert_season_to_id(season_type)

        measurements = json_data.get('measurements', {})
        composition = json_data.get('composition', {})
        colorsize_comments_parts = []
        
        if measurements:
            colorsize_comments_parts.append("【실측 정보】")
            for size_name, size_data in sorted(measurements.items()):
                if isinstance(size_data, dict):
                    colorsize_comments_parts.append(f"\n■ {size_name} 사이즈:")
                    measurement_items = []
                    has_bottom = any(key in size_data for key in ['waist', 'thigh', 'rise', 'hip', 'inseam', 'hem', 'outseam'])
                    
                    if has_bottom:
                        if size_data.get('waist'): measurement_items.append(f"① 허리 너비: {size_data['waist']}")
                        if size_data.get('thigh'): measurement_items.append(f"② 허벅지 너비: {size_data['thigh']}")
                        if size_data.get('rise'): measurement_items.append(f"③ 밑위: {size_data['rise']}")
                        if size_data.get('hip'): measurement_items.append(f"④ 엉덩이 너비: {size_data['hip']}")
                        if size_data.get('inseam'): measurement_items.append(f"⑤ 안기장: {size_data['inseam']}")
                        if size_data.get('hem'): measurement_items.append(f"⑥ 밑단 너비: {size_data['hem']}")
                        if size_data.get('outseam'): measurement_items.append(f"⑦ 바깥기장: {size_data['outseam']}")
                        if size_data.get('weight'): measurement_items.append(f"⑧ 무게: {size_data['weight']}")
                    else:
                        if size_data.get('shoulder'): measurement_items.append(f"① 어깨 너비: {size_data['shoulder']}")
                        if size_data.get('chest'): measurement_items.append(f"② 가슴 너비: {size_data['chest']}")
                        if size_data.get('sleeve_length'): measurement_items.append(f"③ 팔길이: {size_data['sleeve_length']}")
                        if size_data.get('sleeve_width'): measurement_items.append(f"④ 소매너비: {size_data['sleeve_width']}")
                        if size_data.get('collar_height'): measurement_items.append(f"⑤ 카라 높이: {size_data['collar_height']}")
                        if size_data.get('zipper_length'): measurement_items.append(f"⑥ 지퍼 길이: {size_data['zipper_length']}")
                        if size_data.get('total_length'): measurement_items.append(f"⑦ 총장: {size_data['total_length']}")
                        if size_data.get('weight'): measurement_items.append(f"⑧ 무게: {size_data['weight']}")
                    
                    if size_data.get('filling_weight'): measurement_items.append(f"충전재 무게: {size_data['filling_weight']}")
                    for key, value in size_data.items():
                        if key not in ['summary', 'shoulder', 'chest', 'sleeve_length', 'sleeve_width', 'collar_height', 'zipper_length', 'total_length', 'weight', 'filling_weight', 'waist', 'thigh', 'rise', 'hip', 'inseam', 'hem', 'outseam'] and value:
                            measurement_items.append(f"{key.replace('_', ' ')}: {value}")
                    if measurement_items:
                        colorsize_comments_parts.append("  " + " / ".join(measurement_items))
        
        if composition:
            colorsize_comments_parts.append("\n【혼용률】")
            if composition.get('outer'): colorsize_comments_parts.append(f"겉감: {composition['outer']}")
            if composition.get('lining'): colorsize_comments_parts.append(f"안감: {composition['lining']}")
            if composition.get('padding'): colorsize_comments_parts.append(f"충전재: {composition['padding']}")
            if composition.get('material'): colorsize_comments_parts.append(f"소재: {composition['material']}")
        
        colorsize_comments = "\n".join(colorsize_comments_parts) if colorsize_comments_parts else None
        
        # 일본어 변환 추가
        colorsize_comments_jp = convert_to_japanese(colorsize_comments) if colorsize_comments else None

        ace_product = {
            'raw_data_id': raw_data['id'], 'source_site': raw_data.get('source_site', 'okmall'),
            'reference_number': generate_reference_number(), 'control': 'publish', 'name': buyma_name,
            'comments': comments, 'brand_id': brand_info.get('buyma_brand_id', 0), 'brand_name': brand_info.get('buyma_brand_name'),
            'category_id': category_info.get('buyma_category_id', 0), 
            'expected_shipping_fee': expected_shipping_fee,
            'original_price_krw': original_price_krw,
            'purchase_price_krw': purchase_price_krw, 
            'original_price_jpy': original_price_jpy,
            'purchase_price_jpy': purchase_price_jpy,
            'price': selling_price, 'regular_price': None, 'reference_price': original_price_jpy,
            'reference_price_verify_count': 0, 'margin_amount_krw': None, 'margin_rate': None,
            'buyma_lowest_price': None, 'is_lowest_price': 0, 'available_until': available_until,
            'buying_area_id': BUYMA_FIXED_VALUES['buying_area_id'], 'shipping_area_id': BUYMA_FIXED_VALUES['shipping_area_id'],
            'buying_shop_name': buying_shop_name, 'model_no': raw_data.get('model_id'), 'theme_id': BUYMA_FIXED_VALUES['theme_id'],
            'season_id': season_id, 'colorsize_comments': colorsize_comments, 
            'colorsize_comments_jp': colorsize_comments_jp,
            'duty': BUYMA_FIXED_VALUES['duty'],
            'source_product_url': raw_data.get('product_url'), 'source_model_id': raw_data.get('model_id'),
            'source_original_price': original_price_krw, 'source_sales_price': purchase_price_krw,
        }

        images = json_data.get('images', [])
        ace_images = [{'position': i+1, 'source_image_url': url, 'is_uploaded': 0} for i, url in enumerate(images[:20])]

        ace_options = []
        colors = set()
        sizes = []
        for opt in options:
            color_raw = opt.get('color', 'FREE') or 'FREE'
            size_raw = opt.get('tag_size', 'FREE') or 'FREE'
            
            # 1. 색상 번역
            color = convert_to_japanese(color_raw)
            
            # 2. 사이즈 번역 및 단일사이즈 처리
            if size_raw in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈']:
                size = 'FREE'
            else:
                size = convert_to_japanese(size_raw)

            if color and color not in colors:
                colors.add(color)
                ace_options.append({
                    'option_type': 'color', 'value': color, 'master_id': self.get_color_master_id(color),
                    'position': len([o for o in ace_options if o['option_type'] == 'color']) + 1,
                    'details_json': None, 'source_option_value': color_raw
                })
            if size and size not in [s['value'] for s in sizes]:
                # measurements에서 해당 사이즈의 측정값 가져와서 BUYMA 형식으로 변환
                size_measurements = measurements.get(size_raw) or measurements.get(size)
                details_list = []
                if size_measurements and isinstance(size_measurements, dict):
                    details_list = convert_measurements_to_details(
                        size_measurements,
                        category_id=category_info.get('buyma_category_id', 0),
                        category_keys_map=self._category_size_keys_cache
                    )
                details = json.dumps(details_list, ensure_ascii=False) if details_list else None
                sizes.append({
                    'option_type': 'size', 'value': size, 'master_id': 0, 'position': len(sizes) + 1,
                    'details_json': details, 'source_option_value': size_raw
                })
        ace_options.extend(sizes)
        if not colors: ace_options.append({'option_type': 'color', 'value': 'FREE', 'master_id': 99, 'position': 1, 'details_json': None, 'source_option_value': None})
        if not sizes: ace_options.append({'option_type': 'size', 'value': 'FREE', 'master_id': 0, 'position': 1, 'details_json': None, 'source_option_value': None})

        ace_variants = []
        for opt in options:
            color_raw = opt.get('color', 'FREE') or 'FREE'
            size_raw = opt.get('tag_size', 'FREE') or 'FREE'
            
            # 색상/사이즈 번역 (위와 동일 로직)
            color_val = convert_to_japanese(color_raw)
            if size_raw in ['단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈']:
                size_val = 'FREE'
            else:
                size_val = convert_to_japanese(size_raw)
                
            # 사장님 방침: 재고가 있으면 무조건 '주문 후 매입(purchase_for_order)'
            stock_type = 'purchase_for_order' if opt.get('status') == 'in_stock' else 'out_of_stock'
            ace_variants.append({
                'color_value': color_val, 'size_value': size_val,
                'options_json': json.dumps([{'type': 'color', 'value': color_val}, {'type': 'size', 'value': size_val}], ensure_ascii=False),
                'stock_type': stock_type, 'stocks': 1 if stock_type == 'purchase_for_order' else 0,
                'source_option_code': opt.get('option_code'), 'source_stock_status': opt.get('status')
            })
        if not ace_variants:
            stock_type = 'purchase_for_order' if raw_data.get('stock_status') == 'in_stock' else 'out_of_stock'
            ace_variants.append({
                'color_value': 'FREE', 'size_value': 'FREE',
                'options_json': json.dumps([{'type': 'color', 'value': 'FREE'}, {'type': 'size', 'value': 'FREE'}]),
                'stock_type': stock_type,
                'stocks': 1 if stock_type == 'purchase_for_order' else 0,
                'source_option_code': None, 'source_stock_status': raw_data.get('stock_status')
            })

        return {'product': ace_product, 'images': ace_images, 'options': ace_options, 'variants': ace_variants}

    def save_ace_data(self, ace_data: Dict) -> int:
        with self.engine.connect() as conn:
            product = ace_data['product']
            result = conn.execute(text("""
                INSERT INTO ace_products (
                    raw_data_id, source_site, reference_number, control, name, comments,
                    brand_id, brand_name, category_id, expected_shipping_fee, 
                    original_price_krw, purchase_price_krw, original_price_jpy, purchase_price_jpy,
                    price, regular_price, reference_price, reference_price_verify_count,
                    margin_amount_krw, margin_rate, buyma_lowest_price, is_lowest_price,
                    available_until, buying_area_id, shipping_area_id, buying_shop_name,
                    model_no, theme_id, season_id, colorsize_comments, colorsize_comments_jp,
                    source_model_id, duty, source_product_url, source_original_price, source_sales_price
                ) VALUES (
                    :raw_data_id, :source_site, :reference_number, :control, :name, :comments,
                    :brand_id, :brand_name, :category_id, :expected_shipping_fee,
                    :original_price_krw, :purchase_price_krw, :original_price_jpy, :purchase_price_jpy,
                    :price, :regular_price, :reference_price, :reference_price_verify_count,
                    :margin_amount_krw, :margin_rate, :buyma_lowest_price, :is_lowest_price,
                    :available_until, :buying_area_id, :shipping_area_id, :buying_shop_name,
                    :model_no, :theme_id, :season_id, :colorsize_comments, :colorsize_comments_jp,
                    :source_model_id, :duty, :source_product_url, :source_original_price, :source_sales_price
                )
            """), product)
            ace_product_id = result.lastrowid

            for img in ace_data['images']:
                img['ace_product_id'] = ace_product_id
                conn.execute(text("INSERT INTO ace_product_images (ace_product_id, position, source_image_url, is_uploaded) VALUES (:ace_product_id, :position, :source_image_url, :is_uploaded)"), img)

            for opt in ace_data['options']:
                opt['ace_product_id'] = ace_product_id
                conn.execute(text("INSERT INTO ace_product_options (ace_product_id, option_type, value, master_id, position, details_json, source_option_value) VALUES (:ace_product_id, :option_type, :value, :master_id, :position, :details_json, :source_option_value)"), opt)

            for var in ace_data['variants']:
                var['ace_product_id'] = ace_product_id
                conn.execute(text("INSERT INTO ace_product_variants (ace_product_id, color_value, size_value, options_json, stock_type, stocks, source_option_code, source_stock_status) VALUES (:ace_product_id, :color_value, :size_value, :options_json, :stock_type, :stocks, :source_option_code, :source_stock_status)"), var)

            conn.commit()
            return ace_product_id

    def run_conversion(self, limit: int = None, brand: str = None, dry_run: bool = False, raw_id: int = None) -> Dict:
        self.load_brand_mapping()
        self.load_category_mapping()
        self.load_shipping_config()
        self.load_color_master_id_mapping()
        self.load_category_size_keys_mapping()  # 카테고리별 사이즈 키 매핑 로드
        raw_data_list = self.fetch_raw_data(limit=limit, brand=brand, raw_id=raw_id)
        if not raw_data_list: return {'total': 0, 'success': 0, 'failed': 0}
        success, failed = 0, 0
        for idx, raw_data in enumerate(raw_data_list):
            try:
                log(f"[{idx+1}/{len(raw_data_list)}] 변환 중: raw_id={raw_data['id']}, brand={raw_data['brand_name_en']}...")
                ace_data = self.convert_single_raw_to_ace(raw_data)
                if not dry_run: self.save_ace_data(ace_data)
                success += 1
            except Exception as e:
                log(f"  → 변환 실패: {e}", "ERROR")
                failed += 1
        return {'total': len(raw_data_list), 'success': success, 'failed': failed}

def main():
    parser = argparse.ArgumentParser(description='raw_scraped_data를 ace 테이블로 변환')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--brand', type=str, default=None)
    parser.add_argument('--raw-id', type=int, help='특정 raw_scraped_data ID 처리')
    args = parser.parse_args()

    log("=" * 60)
    log("raw_to_ace_converter 시작")
    log(f"  옵션: brand={args.brand}, limit={args.limit}, dry_run={args.dry_run}, raw_id={args.raw_id}")
    log("=" * 60)

    try:
        converter = RawToAceConverter(DB_URL)
        result = converter.run_conversion(limit=args.limit, brand=args.brand, dry_run=args.dry_run, raw_id=args.raw_id)

        log("=" * 60)
        log("변환 완료!")
        log(f"  총 처리: {result['total']}건")
        log(f"  성공: {result['success']}건")
        log(f"  실패: {result['failed']}건")
        if args.dry_run:
            log("  (dry-run 모드: DB 저장 안함)")
        log("=" * 60)
    except Exception as e:
        log(f"변환 중 오류 발생: {e}", "ERROR")

if __name__ == "__main__":
    main()
