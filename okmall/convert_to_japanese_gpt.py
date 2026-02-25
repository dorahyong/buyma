# -*- coding: utf-8 -*-
"""
한글을 일본어로 변환하는 스크립트 (GPT 버전)

작성일: 2026-01-30
번역 엔진: OpenAI GPT API
"""

import os
import sys
import re
import time
import argparse
import requests
from datetime import datetime
from typing import Dict, Optional

import pymysql
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# DB 설정
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

# =====================================================
# OpenAI GPT API 설정
# =====================================================

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
GPT_MODEL = "gpt-4o-mini"  # 저렴한 모델

# 번역 캐시 (동일 텍스트 반복 호출 방지)
_translation_cache: Dict[str, str] = {}

# =====================================================
# 한글 → 일본어 매핑 테이블
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

    # === 의류 부위 (바지) ===
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

    # === 의류 부위 (상의) ===
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
    "숄더끈 높이(최소)": "ショルダー高さ(最小)",
    "숄더끈 높이(최대)": "ショルダー高さ(最大)",
    "숄더끈 높이": "ショルダー高さ",
    "손잡이 높이": "ハンドル高さ",
    "스트랩 길이": "ストラップ長さ",
    "굽": "ヒール",
    "무게": "重さ",
    "팔길이": "裄丈",
    "하단": "下部",
    "장식": "装飾",
    "길이": "長さ",
    "총 길이": "全長",
    "머리둘레 최소 길이": "頭周り最小",
    "챙 길이": "つばの長さ",
    "중량": "重量",
    "도": "cm",
    "소매": "袖",
    "조끼소매": "袖",
    "지퍼": "ジッパー",
    "기타": "その他",

    # === 소재 ===
    "소재": "素材",
    "겉감": "表地",
    "안감": "裏地",
    "면": "コットン",
    "폴리우레탄": "ポリウレタン",
    "폴리에스터": "ポリエステル",
    "폴리에스테르": "ポリエステル",
    "폴리아미드": "ポリアミド",
    "나일론": "ナイロン",
    "울": "ウール",
    "실크": "シルク",
    "린넨": "リネン",
    "리넨": "リネン",
    "레이온": "レーヨン",
    "비스코스": "ビスコース",
    "아크릴": "アクリル",
    "캐시미어": "カシミヤ",
    "모달": "モダール",
    "텐셀": "テンセル",
    "스판덱스": "スパンデックス",
    "엘라스틴": "エラスタン",
    "소가죽": "牛革",
    "양가죽": "羊革",
    "돼지가죽": "豚革",
    "스웨이드": "スエード",
    "캔버스": "キャンバス",
    "데님": "デニム",
    "가죽": "レザー",
    "합성피혁": "合成皮革",
    "인조가죽": "合成皮革",
    "폴리카보네이트": "ポリカーボネート",
    "칼슘카보네이트": "炭酸カルシウム",
    "큐프로": "キュプラ",
    "라피아": "ラフィア",
    "메리노": "メリノ",
    "알파카": "アルパカ",
    "엘라스테렐": "エラステレル",
    "송아지": "カーフ",
    "염": "染め",
    "유기농": "オーガニック",
    "재활용": "リサイクル",
    "충전재": "中綿",
    "카라": "カラー",

    # === 카테고리 ===
    "남성": "メンズ",
    "여성": "レディース",
    "가방": "バッグ",
    "숄더백": "ショルダーバッグ",
    "숄더 백": "ショルダーバッグ",
    "크로스백": "クロスボディバッグ",
    "토트백": "トートバッグ",
    "백팩": "バックパック",
    "클러치": "クラッチ",
    "지갑": "財布",
    "의류": "ウェア",
    "아우터": "アウター",
    "자켓": "ジャケット",
    "코트": "コート",
    "니트": "ニット",
    "스웨터": "セーター",
    "셔츠": "シャツ",
    "티셔츠": "Tシャツ",
    "팬츠": "パンツ",
    "청바지": "ジーンズ",
    "데님팬츠": "デニムパンツ",
    "스커트": "スカート",
    "원피스": "ワンピース",
    "신발": "シューズ",
    "스니커즈": "スニーカー",
    "부츠": "ブーツ",
    "샌들": "サンダル",
    "로퍼": "ローファー",
    "악세사리": "アクセサリー",
    "액세서리": "アクセサリー",
    "모자": "帽子",
    "벨트": "ベルト",
    "스카프": "スカーフ",
    "선글라스": "サングラス",
    "시계": "時計",
}

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def get_db_connection():
    """DB 연결 생성"""
    return pymysql.connect(**DB_CONFIG)


def contains_korean(text: str) -> bool:
    """텍스트에 한글이 포함되어 있는지 확인"""
    if not text:
        return False
    return bool(re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', text))


def translate_with_gpt(text: str, max_retries: int = 3) -> str:
    """
    OpenAI GPT API를 사용하여 한국어를 일본어로 번역

    Args:
        text: 번역할 텍스트
        max_retries: 최대 재시도 횟수

    Returns:
        번역된 텍스트 (실패 시 원본 반환)
    """
    if not text or not contains_korean(text):
        return text

    # 캐시 확인
    cache_key = text[:500]
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]

    if not OPENAI_API_KEY:
        log("OpenAI API 키가 설정되지 않았습니다.", "WARNING")
        return text

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GPT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "당신은 한국어를 일본어로 번역하는 전문 번역가입니다. 패션/의류 상품 설명을 번역합니다. 번역 결과만 출력하세요."
            },
            {
                "role": "user",
                "content": text
            }
        ],
        "temperature": 0.3
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=30)

            if response.status_code == 200:
                result = response.json()
                translated = result["choices"][0]["message"]["content"].strip()

                # 캐시 저장
                _translation_cache[cache_key] = translated

                return translated

            elif response.status_code == 429:
                wait_time = (attempt + 1) * 5
                log(f"GPT API 한도 초과, {wait_time}초 대기 후 재시도... ({attempt + 1}/{max_retries})", "WARNING")
                time.sleep(wait_time)
            else:
                log(f"GPT API 오류 (HTTP {response.status_code}): {response.text[:200]}", "ERROR")
                return text

        except requests.exceptions.Timeout:
            log(f"GPT API 타임아웃, 재시도... ({attempt + 1}/{max_retries})", "WARNING")
            time.sleep(2)
        except Exception as e:
            log(f"GPT 번역 실패: {e}", "ERROR")
            return text

    log(f"GPT 번역 최대 재시도 초과, 원본 반환", "WARNING")
    return text


# =====================================================
# 변환 함수
# =====================================================

def convert_to_japanese(text: str) -> str:
    """
    한글 텍스트를 일본어로 변환

    1. 하드코딩 매핑 먼저 적용 (정확한 용어)
    2. 아직 한국어가 남아있으면 GPT API로 번역
    """
    if not text:
        return text

    result = text

    # 1. 긴 문자열부터 먼저 치환 (부분 매칭 방지)
    sorted_mappings = sorted(KOREAN_TO_JAPANESE.items(), key=lambda x: len(x[0]), reverse=True)

    for korean, japanese in sorted_mappings:
        result = result.replace(korean, japanese)

    # 추가 정리
    result = result.replace("실측:", "実寸:")
    result = result.replace("(실측:", "(実寸:")

    # 2. 아직 한국어가 남아있으면 GPT API로 번역
    if contains_korean(result):
        result = translate_with_gpt(result)

    return result


def convert_comments_to_japanese(comments: str) -> str:
    """comments 필드를 일본어로 변환"""
    if not comments:
        return comments
    return convert_to_japanese(comments)


def convert_colorsize_to_japanese(colorsize: str) -> str:
    """colorsize_comments 필드를 일본어로 변환"""
    if not colorsize:
        return colorsize
    return convert_to_japanese(colorsize)


# =====================================================
# 메인 로직
# =====================================================

def get_products_to_convert(conn, limit: int = None, product_id: int = None):
    """변환 대상 상품 조회"""
    with conn.cursor() as cursor:
        sql = """
            SELECT id, comments, colorsize_comments
            FROM ace_products
            WHERE is_active = 1
              AND (comments_jp IS NULL OR comments_jp = ''
                   OR colorsize_comments_jp IS NULL OR colorsize_comments_jp = '')
        """
        params = []
        if product_id:
            sql += " AND id = %s"
            params.append(product_id)
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        cursor.execute(sql, params if params else None)
        return cursor.fetchall()


def update_product_japanese(conn, product_id: int, comments_jp: str, colorsize_jp: str) -> None:
    """상품의 일본어 필드 업데이트"""
    with conn.cursor() as cursor:
        sql = """
            UPDATE ace_products
            SET comments_jp = %s, colorsize_comments_jp = %s
            WHERE id = %s
        """
        cursor.execute(sql, (comments_jp, colorsize_jp, product_id))
    conn.commit()


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description='한글→일본어 변환 (GPT 버전)')
    parser.add_argument('--limit', type=int, help='처리할 최대 상품 수')
    parser.add_argument('--dry-run', action='store_true', help='실제 DB 업데이트 없이 테스트')
    parser.add_argument('--product-id', type=int, help='특정 상품 ID만 처리')
    args = parser.parse_args()

    log("=" * 60)
    log("한글 → 일본어 변환 시작 (GPT 버전)")
    log(f"모델: {GPT_MODEL}")
    log("=" * 60)

    conn = get_db_connection()

    try:
        products = get_products_to_convert(conn, limit=args.limit, product_id=args.product_id)
        log(f"변환 대상 상품: {len(products)}개")

        if not products:
            log("변환할 상품이 없습니다.")
            return

        success_count = 0
        fail_count = 0

        for i, product in enumerate(products, 1):
            product_id = product['id']
            try:
                comments_jp = convert_comments_to_japanese(product['comments'])
                colorsize_jp = convert_colorsize_to_japanese(product['colorsize_comments'])

                if args.dry_run:
                    log(f"[{i}/{len(products)}] ID={product_id} (dry-run)")
                else:
                    update_product_japanese(conn, product_id, comments_jp, colorsize_jp)
                    log(f"[{i}/{len(products)}] ID={product_id} 변환 완료")

                success_count += 1
            except Exception as e:
                log(f"[{i}/{len(products)}] ID={product_id} 변환 실패: {e}", "ERROR")
                fail_count += 1

        log("\n" + "=" * 60)
        log(f"완료 - 성공: {success_count}, 실패: {fail_count}")
        log("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    main()
