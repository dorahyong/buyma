# -*- coding: utf-8 -*-
"""
한글을 일본어로 배치 번역하는 스크립트 (Gemini 버전)

특징:
- 20개 상품 단위 배치 처리
- 중복 텍스트 제거로 API 비용 절감
- JSON 형식 요청/응답으로 정확한 파싱
- ace_products, ace_product_options, ace_product_variants 일괄 업데이트

작성일: 2026-01-30
번역 엔진: Google Gemini API (REST)
"""

import os
import sys
import re
import json
import time
import argparse
import requests
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import pymysql
from dotenv import load_dotenv

# .env 파일 로드 (시스템 환경 변수보다 .env 우선)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

# =====================================================
# 설정
# =====================================================

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# 배치 설정
BATCH_SIZE = 20  # 한 번에 처리할 상품 수
MAX_TEXTS_PER_REQUEST = 30  # 한 번의 API 요청에 포함할 최대 텍스트 수

# =====================================================
# 한글 → 일본어 매핑 테이블 (하드코딩)
# =====================================================

KOREAN_TO_JAPANESE = {
    # === 제목/섹션 ===
    "실측 정보": "実寸情報",
    "혼용률": "素材",
    "브랜드": "ブランド",
    "상품명": "商品名",
    "모델번호": "型番",
    "카테고리": "カテゴリー",
    "사이즈 옵션": "サイズオプション",
    "구매 안내": "ご購入について",
    "주의사항": "ご注意",

    # === 안내 문구 ===
    "정품 100% 보장": "100%正規品保証",
    "한국 국내 발송 (빠른 배송)": "韓国国内発送（迅速配送）",
    "한국 국내 발송": "韓国国内発送",
    "빠른 배송": "迅速配送",
    "재고 확인 후 구매 부탁드립니다": "在庫確認後、ご購入お願いいたします",
    "모니터 환경에 따라 색상이 다르게 보일 수 있습니다": "モニター環境により色味が異なる場合がございます",
    "실측 사이즈는 측정 방법에 따라 1-3cm 오차가 있을 수 있습니다": "実寸サイズは測定方法により1-3cmの誤差がある場合がございます",

    # === 사이즈 관련 ===
    "사이즈": "サイズ",
    "인치": "インチ",
    "전후": "前後",
    "단일사이즈": "FREE",
    "프리사이즈": "FREE",
    "원사이즈": "ONE SIZE",

    # === 의류 부위 ===
    "허리 너비": "ウエスト幅",
    "허리단면": "ウエスト幅",
    "밑위": "股上",
    "안기장": "股下",
    "바깥기장": "総丈",
    "허벅지 너비": "わたり幅",
    "허벅지단면": "わたり幅",
    "밑단 너비": "裾幅",
    "밑단단면": "裾幅",
    "엉덩이 너비": "ヒップ幅",
    "엉덩이단면": "ヒップ幅",
    "엉덩이": "ヒップ",
    "어깨 너비": "肩幅",
    "어깨단면": "肩幅",
    "가슴 너비": "身幅",
    "가슴단면": "身幅",
    "소매 길이": "袖丈",
    "소매길이": "袖丈",
    "총장": "着丈",
    "총기장": "着丈",
    "암홀단면": "アームホール",
    "소매단면": "袖幅",

    # === 가방 부위 ===
    "가로": "横",
    "세로": "縦",
    "높이": "高さ",
    "너비": "幅",
    "폭": "幅",
    "숄더끈 높이": "ショルダー高さ",
    "손잡이 높이": "ハンドル高さ",
    "무게": "重さ",
    "중량": "重量",
    "길이": "長さ",

    # === 소재 ===
    "소재": "素材",
    "겉감": "表地",
    "안감": "裏地",
    "면": "コットン",
    "폴리에스터": "ポリエステル",
    "나일론": "ナイロン",
    "울": "ウール",
    "실크": "シルク",
    "가죽": "レザー",
    "합성피혁": "合成皮革",
    "충전재": "中綿",

    # === 색상 ===
    "블랙": "ブラック",
    "화이트": "ホワイト",
    "네이비": "ネイビー",
    "그레이": "グレー",
    "베이지": "ベージュ",
    "브라운": "ブラウン",
    "레드": "レッド",
    "블루": "ブルー",
    "그린": "グリーン",
    "핑크": "ピンク",
    "옐로우": "イエロー",
    "오렌지": "オレンジ",
    "퍼플": "パープル",
    "실버": "シルバー",
    "골드": "ゴールド",
    "멀티": "マルチ",

    # === 카테고리 ===
    "남성": "メンズ",
    "여성": "レディース",
    "가방": "バッグ",
    "의류": "ウェア",
    "신발": "シューズ",
    "액세서리": "アクセサリー",
}

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def get_db_connection():
    """DB 연결 생성"""
    return pymysql.connect(**DB_CONFIG)


def contains_korean(text: str) -> bool:
    """텍스트에 한글이 포함되어 있는지 확인"""
    if not text:
        return False
    return bool(re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', text))


def generate_text_id(text: str) -> str:
    """텍스트의 고유 ID 생성 (해시)"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()[:8]


def apply_hardcoded_mapping(text: str) -> str:
    """하드코딩 매핑 먼저 적용"""
    if not text:
        return text

    result = text
    sorted_mappings = sorted(KOREAN_TO_JAPANESE.items(), key=lambda x: len(x[0]), reverse=True)

    for korean, japanese in sorted_mappings:
        result = result.replace(korean, japanese)

    result = result.replace("실측:", "実寸:")
    result = result.replace("(실측:", "(実寸:")

    return result


# =====================================================
# 번역 대상 수집
# =====================================================

def collect_translation_targets(conn, brand: str = None, limit: int = None) -> Dict:
    """
    번역이 필요한 모든 텍스트 수집

    Returns:
        {
            "products": [{"id": 123, "name": "...", "colorsize": "..."}],
            "options": [{"id": 456, "value": "..."}],
            "variants": [{"id": 789, "color": "...", "size": "..."}]
        }
    """
    targets = {
        "products": [],
        "options": [],
        "variants": []
    }

    with conn.cursor() as cursor:
        # 1. ace_products 조회 (번역 안 된 상품)
        sql = """
            SELECT p.id, p.name, p.colorsize_comments
            FROM ace_products p
            WHERE p.is_active = 1
              AND (p.colorsize_comments_jp IS NULL OR p.colorsize_comments_jp = '')
        """
        params = []

        if brand:
            sql += " AND p.brand_name = %s"
            params.append(brand)

        sql += " ORDER BY p.id"

        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        cursor.execute(sql, params if params else None)
        products = cursor.fetchall()

        if not products:
            return targets

        product_ids = [p['id'] for p in products]
        targets["products"] = products

        # 2. ace_product_options 조회
        if product_ids:
            format_strings = ','.join(['%s'] * len(product_ids))
            cursor.execute(f"""
                SELECT id, ace_product_id, option_type, value
                FROM ace_product_options
                WHERE ace_product_id IN ({format_strings})
            """, product_ids)
            targets["options"] = cursor.fetchall()

        # 3. ace_product_variants 조회
        if product_ids:
            format_strings = ','.join(['%s'] * len(product_ids))
            cursor.execute(f"""
                SELECT id, ace_product_id, color_value, size_value
                FROM ace_product_variants
                WHERE ace_product_id IN ({format_strings})
            """, product_ids)
            targets["variants"] = cursor.fetchall()

    return targets


def extract_unique_texts(targets: Dict) -> Tuple[Dict[str, str], Dict[str, List]]:
    """
    중복 제거하여 유니크 텍스트 추출

    Returns:
        unique_texts: {text_id: original_text}
        text_locations: {text_id: [{"table": "products", "id": 123, "field": "name"}, ...]}
    """
    unique_texts = {}  # text_id -> original_text
    text_locations = {}  # text_id -> [locations]

    def add_text(text: str, table: str, row_id: int, field: str):
        if not text or not contains_korean(text):
            return

        # 하드코딩 매핑 먼저 적용
        processed = apply_hardcoded_mapping(text)

        # 아직 한국어가 남아있으면 번역 대상
        if not contains_korean(processed):
            return

        text_id = generate_text_id(processed)

        if text_id not in unique_texts:
            unique_texts[text_id] = processed
            text_locations[text_id] = []

        text_locations[text_id].append({
            "table": table,
            "id": row_id,
            "field": field,
            "original": text
        })

    # products
    for p in targets["products"]:
        add_text(p["name"], "products", p["id"], "name")
        add_text(p["colorsize_comments"], "products", p["id"], "colorsize_comments")

    # options
    for o in targets["options"]:
        add_text(o["value"], "options", o["id"], "value")

    # variants
    for v in targets["variants"]:
        add_text(v["color_value"], "variants", v["id"], "color_value")
        add_text(v["size_value"], "variants", v["id"], "size_value")

    return unique_texts, text_locations


# =====================================================
# Gemini API 배치 번역
# =====================================================

def translate_batch_with_gemini(texts: Dict[str, str], max_retries: int = 3) -> Dict[str, str]:
    """
    Gemini API로 배치 번역

    Args:
        texts: {text_id: text_to_translate}

    Returns:
        {text_id: translated_text}
    """
    if not texts:
        return {}

    if not GEMINI_API_KEY:
        log("Gemini API 키가 설정되지 않았습니다.", "ERROR")
        return {}

    # 요청 JSON 구성
    items = [{"id": tid, "text": txt} for tid, txt in texts.items()]

    prompt = f"""다음 한국어 텍스트들을 일본어로 번역하세요.
패션/의류 상품 설명입니다. 전문 용어는 정확하게 번역하세요.
반드시 아래 JSON 형식으로만 응답하세요. 다른 설명은 하지 마세요.

입력:
{json.dumps({"items": items}, ensure_ascii=False)}

출력 형식 (JSON만 출력):
{{"items":[{{"id":"텍스트ID","text":"번역결과"}}]}}"""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192 
        }
    }

    url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)

            if response.status_code == 200:
                result = response.json()
                response_text = result["candidates"][0]["content"]["parts"][0]["text"].strip()

                # JSON 파싱
                try:
                    # 마크다운 코드블록 제거
                    if response_text.startswith("```"):
                        response_text = re.sub(r'^```json?\n?', '', response_text)
                        response_text = re.sub(r'\n?```$', '', response_text)

                    translated_data = json.loads(response_text)
                    translations = {}

                    for item in translated_data.get("items", []):
                        translations[item["id"]] = item["text"]

                    log(f"  → 배치 번역 완료: {len(translations)}개 텍스트")
                    return translations

                except json.JSONDecodeError as e:
                    log(f"JSON 파싱 실패: {e}", "ERROR")
                    log(f"응답 내용: {response_text[:500]}", "DEBUG")
                    # 재시도

            elif response.status_code == 429:
                wait_time = (attempt + 1) * 10
                log(f"API 한도 초과, {wait_time}초 대기... ({attempt + 1}/{max_retries})", "WARNING")
                time.sleep(wait_time)
            else:
                log(f"Gemini API 오류 (HTTP {response.status_code}): {response.text[:200]}", "ERROR")
                return {}

        except requests.exceptions.Timeout:
            log(f"API 타임아웃, 재시도... ({attempt + 1}/{max_retries})", "WARNING")
            time.sleep(5)
        except Exception as e:
            log(f"번역 실패: {e}", "ERROR")
            return {}

    log("최대 재시도 초과", "ERROR")
    return {}


def translate_all_texts(unique_texts: Dict[str, str]) -> Dict[str, str]:
    """
    모든 유니크 텍스트를 배치로 번역

    Returns:
        {original_text: translated_text}
    """
    if not unique_texts:
        return {}

    all_translations = {}
    text_items = list(unique_texts.items())
    total_batches = (len(text_items) + MAX_TEXTS_PER_REQUEST - 1) // MAX_TEXTS_PER_REQUEST

    log(f"총 {len(text_items)}개 텍스트를 {total_batches}개 배치로 번역 시작...")

    for i in range(0, len(text_items), MAX_TEXTS_PER_REQUEST):
        batch_num = i // MAX_TEXTS_PER_REQUEST + 1
        batch = dict(text_items[i:i + MAX_TEXTS_PER_REQUEST])

        log(f"[배치 {batch_num}/{total_batches}] {len(batch)}개 텍스트 번역 중...")

        translations = translate_batch_with_gemini(batch)

        # text_id -> 번역결과를 original_text -> 번역결과로 변환
        for text_id, translated in translations.items():
            if text_id in unique_texts:
                original = unique_texts[text_id]
                all_translations[original] = translated

        # 배치 간 간격
        if i + MAX_TEXTS_PER_REQUEST < len(text_items):
            time.sleep(1)

    return all_translations


# =====================================================
# DB 업데이트
# =====================================================

def update_database(conn, targets: Dict, translation_map: Dict[str, str]) -> Dict[str, int]:
    """
    번역 결과로 DB 일괄 업데이트

    Returns:
        {"products": 업데이트 수, "options": 업데이트 수, "variants": 업데이트 수}
    """
    stats = {"products": 0, "options": 0, "variants": 0}

    def get_translated(original: str) -> str:
        """원본 텍스트의 번역 결과 가져오기"""
        if not original:
            return original

        # 하드코딩 매핑 적용
        processed = apply_hardcoded_mapping(original)

        # API 번역 결과 적용
        if processed in translation_map:
            return translation_map[processed]

        return processed

    with conn.cursor() as cursor:
        # 1. ace_products 업데이트
        for p in targets["products"]:
            name_jp = get_translated(p["name"])
            colorsize_jp = get_translated(p["colorsize_comments"])

            cursor.execute("""
                UPDATE ace_products
                SET name = %s, colorsize_comments_jp = %s
                WHERE id = %s
            """, (name_jp, colorsize_jp, p["id"]))
            stats["products"] += 1

        # 2. ace_product_options 업데이트
        for o in targets["options"]:
            value_jp = get_translated(o["value"])

            cursor.execute("""
                UPDATE ace_product_options
                SET value = %s
                WHERE id = %s
            """, (value_jp, o["id"]))
            stats["options"] += 1

        # 3. ace_product_variants 업데이트
        for v in targets["variants"]:
            color_jp = get_translated(v["color_value"])
            size_jp = get_translated(v["size_value"])

            cursor.execute("""
                UPDATE ace_product_variants
                SET color_value = %s, size_value = %s
                WHERE id = %s
            """, (color_jp, size_jp, v["id"]))
            stats["variants"] += 1

        conn.commit()

    return stats


# =====================================================
# 메인 함수 (raw_to_ace_converter.py에서 import용)
# =====================================================

def convert_to_japanese(text: str) -> str:
    """
    단일 텍스트 번역 (하위 호환성 유지)
    배치 처리가 아닌 개별 호출용
    """
    if not text or not contains_korean(text):
        return text

    # 하드코딩 매핑 적용
    result = apply_hardcoded_mapping(text)

    # 아직 한국어가 남아있으면 API 호출
    if contains_korean(result):
        texts = {generate_text_id(result): result}
        translations = translate_batch_with_gemini(texts)
        if translations:
            text_id = generate_text_id(result)
            if text_id in translations:
                result = translations[text_id]

    return result


# =====================================================
# 배치 처리 메인
# =====================================================

def run_batch_translation(brand: str = None, limit: int = None, dry_run: bool = False):
    """
    배치 번역 실행

    Args:
        brand: 특정 브랜드만 처리 (예: "Nike(ナイキ)")
        limit: 처리할 상품 수 제한
        dry_run: True면 DB 업데이트 없이 테스트
    """
    log("=" * 60)
    log("배치 번역 시작 (Gemini)")
    log(f"브랜드: {brand or '전체'}, 제한: {limit or '없음'}, dry_run: {dry_run}")
    log("=" * 60)

    conn = get_db_connection()

    try:
        # 1단계: 번역 대상 수집
        log("[1/4] 번역 대상 수집 중...")
        targets = collect_translation_targets(conn, brand=brand, limit=limit)

        log(f"  → 상품: {len(targets['products'])}개")
        log(f"  → 옵션: {len(targets['options'])}개")
        log(f"  → Variants: {len(targets['variants'])}개")

        if not targets["products"]:
            log("번역할 대상이 없습니다.")
            return

        # 2단계: 중복 제거
        log("[2/4] 중복 텍스트 제거 중...")
        unique_texts, text_locations = extract_unique_texts(targets)
        log(f"  → 유니크 텍스트: {len(unique_texts)}개")

        if not unique_texts:
            log("번역이 필요한 한국어 텍스트가 없습니다.")
            return

        # 3단계: 배치 번역
        log("[3/4] Gemini API 배치 번역 중...")
        if dry_run:
            log("  → (dry-run) API 호출 생략")
            translation_map = {}
        else:
            translation_map = translate_all_texts(unique_texts)
            log(f"  → 번역 완료: {len(translation_map)}개")

        # 4단계: DB 업데이트
        log("[4/4] DB 업데이트 중...")
        if dry_run:
            log("  → (dry-run) DB 업데이트 생략")
            stats = {"products": 0, "options": 0, "variants": 0}
        else:
            stats = update_database(conn, targets, translation_map)

        log("=" * 60)
        log("배치 번역 완료!")
        log(f"  → 상품 업데이트: {stats['products']}개")
        log(f"  → 옵션 업데이트: {stats['options']}개")
        log(f"  → Variants 업데이트: {stats['variants']}개")
        log("=" * 60)

    finally:
        conn.close()


# =====================================================
# CLI 실행
# =====================================================

def main():
    parser = argparse.ArgumentParser(description='배치 번역 (Gemini)')
    parser.add_argument('--brand', type=str, help='특정 브랜드만 처리')
    parser.add_argument('--limit', type=int, help='처리할 상품 수 제한')
    parser.add_argument('--dry-run', action='store_true', help='테스트 모드')
    args = parser.parse_args()

    run_batch_translation(brand=args.brand, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    main()
