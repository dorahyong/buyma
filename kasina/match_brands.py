"""
mall_brands 테이블에서 buyma brands.csv를 매칭하여 buyma_brand_id를 채우는 스크립트

매칭 레벨:
  1: 브랜드(영문)명 완전 일치
  2: 특수문자 제외 후 일치
  3: 특수문자 제외 후 포함 관계
  0: 1~3 단계로 데이터 찾지 못함
"""

import csv
import re
import pymysql
import os
from dotenv import load_dotenv

load_dotenv("C:/Users/hyong/OneDrive/원블록스/buyma/.env")

BRANDS_CSV = "buyma_master_data_20260226/brands.csv"


def remove_special_chars(s):
    """특수문자 제거, 공백도 제거하여 비교용 문자열 반환"""
    return re.sub(r"[^a-zA-Z0-9]", "", s).upper()


def extract_english_name(brand_name):
    """'NIKE(ナイキ)' -> 'NIKE' 영문명 추출"""
    match = re.match(r"^(.+?)(\(.*\))?$", brand_name)
    if match:
        return match.group(1).strip()
    return brand_name.strip()


def load_buyma_brands():
    """brands.csv에서 buyma 브랜드 로드"""
    brands = []
    with open(BRANDS_CSV, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brand_id = int(row["id"])
            brand_name_full = row["brand_name"]
            brand_name_en = extract_english_name(brand_name_full)
            brands.append({
                "id": brand_id,
                "name_full": brand_name_full,
                "name_en": brand_name_en,
                "name_en_upper": brand_name_en.upper(),
                "name_en_clean": remove_special_chars(brand_name_en),
            })
    return brands


def match_brand(mall_brand_en, buyma_brands):
    """
    매칭 레벨 1~3으로 buyma 브랜드 찾기
    Returns: (brand_id, brand_name_full, mapping_level) or (None, None, 0)
    """
    mall_upper = mall_brand_en.strip().upper()
    mall_clean = remove_special_chars(mall_brand_en)

    # Level 1: 완전 일치
    for b in buyma_brands:
        if mall_upper == b["name_en_upper"]:
            return b["id"], b["name_full"], 1

    # Level 2: 특수문자 제외 후 일치
    for b in buyma_brands:
        if mall_clean == b["name_en_clean"]:
            return b["id"], b["name_full"], 2

    # Level 3: 특수문자 제외 후 포함 관계
    candidates = []
    for b in buyma_brands:
        if not mall_clean or not b["name_en_clean"]:
            continue
        if mall_clean in b["name_en_clean"] or b["name_en_clean"] in mall_clean:
            candidates.append(b)

    if len(candidates) == 1:
        b = candidates[0]
        return b["id"], b["name_full"], 3
    elif len(candidates) > 1:
        # 포함 관계에서 여러 개 매칭되면 가장 길이가 비슷한 것 선택
        candidates.sort(key=lambda b: abs(len(b["name_en_clean"]) - len(mall_clean)))
        b = candidates[0]
        return b["id"], b["name_full"], 3

    return None, None, 0


def main():
    buyma_brands = load_buyma_brands()
    print(f"buyma brands.csv 로드: {len(buyma_brands)}개")

    conn = pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306)),
    )
    cur = conn.cursor()

    # buyma_brand_id가 NULL인 브랜드 조회
    cur.execute(
        "SELECT mall_name, mall_brand_name_en, mall_brand_url FROM mall_brands "
        "WHERE buyma_brand_id IS NULL"
    )
    unmapped_brands = cur.fetchall()
    print(f"미매핑 브랜드: {len(unmapped_brands)}개\n")

    stats = {0: 0, 1: 0, 2: 0, 3: 0}

    for mall_name, mall_brand_en, mall_brand_url in unmapped_brands:
        brand_id, brand_name, level = match_brand(mall_brand_en, buyma_brands)
        stats[level] += 1

        if level > 0:
            cur.execute(
                "UPDATE mall_brands SET buyma_brand_id=%s, buyma_brand_name=%s, "
                "mapping_level=%s, is_mapped=1 "
                "WHERE mall_name=%s AND mall_brand_url=%s",
                (brand_id, brand_name, level, mall_name, mall_brand_url),
            )
            print(f"[{mall_name}][L{level}] {mall_brand_en} -> {brand_name} (id={brand_id})")
        else:
            cur.execute(
                "UPDATE mall_brands SET mapping_level=0, is_mapped=0 "
                "WHERE mall_name=%s AND mall_brand_url=%s",
                (mall_name, mall_brand_url),
            )
            print(f"[{mall_name}][L0] {mall_brand_en} -> 매칭 없음")

    conn.commit()
    conn.close()

    print(f"\n===== 매칭 결과 =====")
    print(f"Level 1 (완전 일치):        {stats[1]}개")
    print(f"Level 2 (특수문자 제외 일치): {stats[2]}개")
    print(f"Level 3 (포함 관계):        {stats[3]}개")
    print(f"Level 0 (매칭 실패):        {stats[0]}개")
    print(f"합계: {sum(stats.values())}개")


if __name__ == "__main__":
    main()
