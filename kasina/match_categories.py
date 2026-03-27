"""
mall_categories 테이블에서 buyma categories.md를 매칭하여 buyma_category_id를 채우는 스크립트

매칭 방식: Gemini API로 mall_categories.full_path를 buyma 카테고리에 매칭
대상: mall_categories에서 buyma_category_id IS NULL인 행

사용법:
    python match_categories.py              # 실제 실행
    python match_categories.py --dry-run    # 미리보기 (UPDATE 안함)
    python match_categories.py --mall labellusso  # 특정 수집처만
"""

import os
import sys
import json
import time
import argparse
import requests
import pymysql
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

# Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# categories.md 경로
CATEGORIES_MD = Path(__file__).resolve().parent.parent / "okmall" / "buyma_master_data_20260226" / "md" / "categories.md"


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def load_buyma_categories() -> list[dict]:
    """categories.md에서 buyma 카테고리 목록 로드"""
    categories = []

    with open(CATEGORIES_MD, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line.startswith('|') or line.startswith('| ---') or line.startswith('| ﻿'):
            continue

        cells = [c.strip() for c in line.split('|')]
        if len(cells) < 7:
            continue

        cat_id = cells[1].strip().strip('"')
        if not cat_id.isdigit():
            continue

        categories.append({
            'id': int(cat_id),
            'paths': cells[2].strip(),
            'paths_ko': cells[3].strip(),
            'name': cells[4].strip(),
            'name_ko': cells[5].strip(),
        })

    return categories


def match_with_gemini(unmapped: list[dict], buyma_categories: list[dict]) -> dict:
    """
    Gemini API로 미매칭 카테고리를 buyma 카테고리에 매칭

    Returns:
        { id: buyma_category_id, ... }
    """
    buyma_list = []
    for cat in buyma_categories:
        buyma_list.append({
            'id': cat['id'],
            'path_ko': f"{cat['paths_ko']}/{cat['name_ko']}",
        })

    targets = {}
    for cat in unmapped:
        targets[str(cat['id'])] = {
            'mall_name': cat['mall_name'],
            'gender': cat['gender'],
            'full_path': cat['full_path'],
        }

    prompt = (
        "아래 [매칭 대상]의 각 카테고리를 [BUYMA 카테고리] 목록에서 가장 적합한 것과 매칭해주세요.\n\n"
        "규칙:\n"
        "1. gender가 'female'이면 여성 패션, 'male'이면 남성 패션, 'kids'면 베이비・키즈 카테고리에서 선택\n"
        "2. full_path의 의미를 파악하여 가장 적합한 buyma 카테고리 id를 선택\n"
        "3. 예: female / 'WOMEN > 가방 > 토트백' → 여성 패션/가방/토트백 (id: 3100)\n"
        "4. 예: male / 'MEN > 슈즈 > 스니커즈/슬립온' → 남성 패션/신발/스니커즈 (id: 3321)\n"
        "5. 정확히 맞는 게 없으면 가장 가까운 상위/유사 카테고리 선택\n"
        "6. 매칭 불가능하면 null\n\n"
        "[매칭 대상]\n"
        + json.dumps(targets, ensure_ascii=False) + "\n\n"
        "[BUYMA 카테고리]\n"
        + json.dumps(buyma_list, ensure_ascii=False) + "\n\n"
        "JSON 객체로 응답 (키=매칭 대상의 id, 값=buyma category id 숫자 또는 null):\n"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        }
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=120,
            )

            if resp.status_code == 429:
                wait_time = (attempt + 1) * 10
                log(f"API 한도 초과, {wait_time}초 대기... ({attempt+1}/3)", "WARNING")
                time.sleep(wait_time)
                continue

            resp.raise_for_status()
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            result = json.loads(raw)

            if isinstance(result, list):
                converted = {}
                for item in result:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            converted[k] = int(v) if v is not None and str(v).isdigit() else v
                return converted

            return {k: (int(v) if v is not None and str(v).isdigit() else v) for k, v in result.items()}
        except requests.exceptions.Timeout:
            log(f"API 타임아웃, 재시도... ({attempt+1}/3)", "WARNING")
            time.sleep(5)
        except Exception as e:
            log(f"Gemini API 오류 ({attempt+1}/3): {e}", "ERROR")
            if attempt < 2:
                time.sleep(3)

    return {}


def main():
    parser = argparse.ArgumentParser(
        description='mall_categories에서 buyma_category_id IS NULL인 행을 Gemini로 매칭'
    )
    parser.add_argument('--dry-run', action='store_true', help='UPDATE 하지 않고 미리보기만')
    parser.add_argument('--mall', type=str, default=None, help='특정 수집처만 처리 (예: labellusso)')
    args = parser.parse_args()

    log("=" * 60)
    log("카테고리 매칭 시작")
    log("=" * 60)

    if args.dry_run:
        log("*** DRY RUN 모드 - UPDATE 하지 않음 ***", "WARNING")

    # 1. buyma 카테고리 로드
    log("1. buyma 카테고리 로드 중...")
    buyma_categories = load_buyma_categories()
    log(f"   → {len(buyma_categories)}개 buyma 카테고리 로드됨")

    # 2. mall_categories에서 buyma_category_id IS NULL 조회
    log("2. 미매칭 카테고리 조회 중...")
    conn = pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306)),
        charset='utf8mb4',
    )
    cur = conn.cursor(pymysql.cursors.DictCursor)

    sql = "SELECT id, mall_name, gender, depth1, depth2, full_path FROM mall_categories WHERE buyma_category_id IS NULL AND is_active = 1"
    if args.mall:
        sql += f" AND mall_name = %s"
        cur.execute(sql, (args.mall,))
    else:
        cur.execute(sql)

    unmapped = cur.fetchall()
    log(f"   → 미매칭 카테고리: {len(unmapped)}개")

    if not unmapped:
        log("모든 카테고리가 이미 매칭 완료되었습니다.")
        conn.close()
        return

    # 3. Gemini API로 매칭
    log("3. Gemini API로 buyma_category_id 매칭 중...")
    matched = match_with_gemini(unmapped, buyma_categories)

    matched_count = sum(1 for v in matched.values() if v is not None)
    log(f"   → {matched_count}/{len(unmapped)}개 매칭 성공")

    # 4. 결과 출력 및 UPDATE
    log("")
    log("=" * 60)
    log("카테고리 매칭 결과:")
    log("=" * 60)

    id_to_name = {cat['id']: f"{cat['paths_ko']}/{cat['name_ko']}" for cat in buyma_categories}

    update_count = 0
    for cat in unmapped:
        cat_id = str(cat['id'])
        buyma_id = matched.get(cat_id)
        buyma_name = id_to_name.get(buyma_id, "매칭 실패") if buyma_id else "매칭 실패"

        status = "OK" if buyma_id else "FAIL"
        log(f"[{status}] {cat['mall_name']} | {cat['full_path']}")
        log(f"     → buyma: {buyma_id} ({buyma_name})")

        if not args.dry_run and buyma_id:
            cur.execute(
                "UPDATE mall_categories SET buyma_category_id = %s WHERE id = %s",
                (buyma_id, cat['id']),
            )
            update_count += 1

    if not args.dry_run:
        conn.commit()
        log("")
        log(f"UPDATE {update_count}개 완료! (매칭 {matched_count}/{len(unmapped)}개)")
    else:
        log("")
        log(f"DRY RUN: {matched_count}/{len(unmapped)}개 매칭됨. 실제 실행하려면 --dry-run 제거")

    conn.close()

    log("=" * 60)
    log("카테고리 매칭 종료")
    log("=" * 60)


if __name__ == "__main__":
    main()
