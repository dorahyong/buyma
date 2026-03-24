"""
raw_scraped_data의 category_path를 mall_categories 테이블에 동기화하는 스크립트

목적: raw_scraped_data에 있는 category_path가 mall_categories에 없으면
      categories.md(buyma 마스터)와 Gemini API로 buyma_category_id를 자동 매칭하여 INSERT
      is_active=0으로 생성 (사람이 최종 확인 후 활성화)

사용법:
    python sync_categories.py              # 실제 실행
    python sync_categories.py --dry-run    # 미리보기 (INSERT 안함)
"""

import os
import sys
import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'), override=True)

# DB 연결 정보
DB_URL = os.getenv('DATABASE_URL', f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4")

# Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# categories.md 경로
CATEGORIES_MD = Path(__file__).resolve().parent / "buyma_master_data_20260226" / "md" / "categories.md"


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def parse_category_path(category_path: str) -> dict:
    """카테고리 경로를 파싱하여 depth1, depth2, depth3으로 분리"""
    if not category_path:
        return None

    parts = [p.strip() for p in category_path.split('>')]

    return {
        'full_path': category_path,
        'depth1': parts[0] if len(parts) > 0 else None,
        'depth2': parts[1] if len(parts) > 1 else None,
        'depth3': parts[2] if len(parts) > 2 else None,
    }


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
        # split('|') 결과: ['', 'id', 'paths', 'paths_ko', 'name', 'name_ko', 'limited', '']
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


def match_categories_with_gemini(missing_categories: list[dict], buyma_categories: list[dict]) -> dict:
    """
    Gemini API로 누락된 카테고리를 buyma 카테고리에 매칭

    Returns:
        { "source_site|full_path": buyma_category_id, ... }
    """
    # buyma 카테고리 목록을 간결하게 구성
    buyma_list = []
    for cat in buyma_categories:
        buyma_list.append({
            'id': cat['id'],
            'path_ko': f"{cat['paths_ko']}/{cat['name_ko']}",
            'path_ja': f"{cat['paths']}/{cat['name']}",
        })

    # 매칭 대상 목록
    targets = {}
    for cat in missing_categories:
        key = f"{cat['source_site']}|{cat['category_path']}"
        targets[key] = cat['category_path']

    prompt = (
        "아래 [매칭 대상]의 각 카테고리 경로를 [BUYMA 카테고리] 목록에서 가장 적합한 것과 매칭해주세요.\n\n"
        "규칙:\n"
        "1. MEN → メンズファッション (남성 패션), WOMEN → レディースファッション (여성 패션)\n"
        "2. 경로의 의미를 파악하여 가장 적합한 buyma 카테고리 id를 선택\n"
        "3. 예: 'MEN > FOOTWEAR > SNEAKERS > LOW' → メンズファッション/靴・ブーツ・サンダル > スニーカー (id: 3321)\n"
        "4. 정확히 맞는 게 없으면 가장 가까운 상위 카테고리 선택\n"
        "5. 매칭 불가능하면 null\n\n"
        "[매칭 대상]\n"
        + json.dumps(targets, ensure_ascii=False) + "\n\n"
        "[BUYMA 카테고리]\n"
        + json.dumps(buyma_list, ensure_ascii=False) + "\n\n"
        "JSON 객체로 응답 (키=매칭 대상의 키, 값=buyma category id 숫자 또는 null):\n"
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

            # Gemini가 list로 반환하는 경우 dict로 변환
            if isinstance(result, list):
                converted = {}
                for item in result:
                    if isinstance(item, dict):
                        for k, v in item.items():
                            converted[k] = int(v) if v is not None and str(v).isdigit() else v
                if converted:
                    return converted
                log(f"예상치 못한 응답 형식: {str(result)[:300]}", "WARNING")
                return {}

            # dict인 경우 값을 int로 변환
            return {k: (int(v) if v is not None and str(v).isdigit() else v) for k, v in result.items()}
        except requests.exceptions.Timeout:
            log(f"API 타임아웃, 재시도... ({attempt+1}/3)", "WARNING")
            time.sleep(5)
        except Exception as e:
            log(f"Gemini API 오류 ({attempt+1}/3): {e}", "ERROR")
            if attempt < 2:
                time.sleep(3)

    return {}


def sync_categories(dry_run: bool = False):
    """raw_scraped_data의 category_path를 mall_categories에 동기화"""
    engine = create_engine(DB_URL)

    log("=" * 60)
    log("카테고리 동기화 시작")
    log("=" * 60)

    if dry_run:
        log("*** DRY RUN 모드 - INSERT 하지 않음 ***", "WARNING")

    # 1. buyma 카테고리 로드
    log("1. buyma 카테고리 로드 중...")
    buyma_categories = load_buyma_categories()
    log(f"   → {len(buyma_categories)}개 buyma 카테고리 로드됨")

    with engine.connect() as conn:
        # 2. raw_scraped_data에서 고유한 category_path 조회
        log("2. raw_scraped_data에서 category_path 조회 중...")
        result = conn.execute(text("""
            SELECT DISTINCT source_site, category_path
            FROM raw_scraped_data
            WHERE category_path IS NOT NULL AND category_path != ''
            ORDER BY category_path
        """))

        raw_categories = []
        for row in result:
            raw_categories.append({
                'source_site': row[0],
                'category_path': row[1]
            })

        log(f"   → raw_scraped_data에서 {len(raw_categories)}개 카테고리 발견")

        # 3. mall_categories에서 기존 데이터 조회
        log("3. mall_categories에서 기존 데이터 조회 중...")
        result = conn.execute(text("""
            SELECT mall_name, full_path, buyma_category_id
            FROM mall_categories
            WHERE full_path IS NOT NULL
        """))

        existing_with_id = set()    # buyma_category_id가 있는 것
        existing_without_id = set() # buyma_category_id가 NULL인 것
        for row in result:
            key = f"{row[0]}|{row[1]}"
            if row[2] is not None:
                existing_with_id.add(key)
            else:
                existing_without_id.add(key)

        log(f"   → mall_categories: 매칭완료 {len(existing_with_id)}개, 미매칭(NULL) {len(existing_without_id)}개")

        # 4. 대상 찾기: INSERT 대상 + UPDATE 대상(buyma_category_id IS NULL)
        log("4. 매칭 대상 확인 중...")
        insert_targets = []
        update_targets = []

        for raw_cat in raw_categories:
            key = f"{raw_cat['source_site']}|{raw_cat['category_path']}"
            if key in existing_with_id:
                continue  # 이미 매칭 완료 → 건드리지 않음
            elif key in existing_without_id:
                update_targets.append(raw_cat)
            else:
                insert_targets.append(raw_cat)

        all_targets = insert_targets + update_targets
        log(f"   → INSERT 대상: {len(insert_targets)}개, UPDATE 대상(NULL): {len(update_targets)}개")

        if not all_targets:
            log("모든 카테고리가 이미 매칭 완료되었습니다. 동기화 완료!")
            return

        # 5. Gemini API로 buyma_category_id 자동 매칭
        log("5. Gemini API로 buyma_category_id 자동 매칭 중...")
        matched = match_categories_with_gemini(all_targets, buyma_categories)

        matched_count = sum(1 for v in matched.values() if v is not None)
        log(f"   → {matched_count}/{len(all_targets)}개 매칭 성공")

        # 6. 매칭 결과 출력 및 INSERT/UPDATE
        log("")
        log("=" * 60)
        log("카테고리 매칭 결과:")
        log("=" * 60)

        # buyma_category_id → name 매핑 (로그용)
        id_to_name = {cat['id']: f"{cat['paths_ko']}/{cat['name_ko']}" for cat in buyma_categories}

        insert_count = 0
        update_count = 0

        for idx, cat in enumerate(all_targets):
            parsed = parse_category_path(cat['category_path'])
            key = f"{cat['source_site']}|{cat['category_path']}"
            buyma_id = matched.get(key)
            is_insert = cat in insert_targets
            action = "INSERT" if is_insert else "UPDATE"

            buyma_name = id_to_name.get(buyma_id, "매칭 실패") if buyma_id else "매칭 실패"
            log(f"[{idx+1}] [{action}] {cat['source_site']} | {cat['category_path']}")
            log(f"     → buyma: {buyma_id} ({buyma_name})")

            if not dry_run:
                if is_insert:
                    conn.execute(text("""
                        INSERT INTO mall_categories (
                            mall_name, full_path, depth1, depth2, depth3,
                            buyma_category_id, is_active
                        ) VALUES (
                            :mall_name, :full_path, :depth1, :depth2, :depth3,
                            :buyma_category_id, 0
                        )
                    """), {
                        'mall_name': cat['source_site'],
                        'full_path': cat['category_path'],
                        'depth1': parsed['depth1'],
                        'depth2': parsed['depth2'],
                        'depth3': parsed['depth3'],
                        'buyma_category_id': buyma_id,
                    })
                    insert_count += 1
                else:
                    conn.execute(text("""
                        UPDATE mall_categories
                        SET buyma_category_id = :buyma_category_id
                        WHERE mall_name = :mall_name AND full_path = :full_path
                    """), {
                        'mall_name': cat['source_site'],
                        'full_path': cat['category_path'],
                        'buyma_category_id': buyma_id,
                    })
                    update_count += 1

        if not dry_run:
            conn.commit()
            log("")
            log(f"INSERT {insert_count}개, UPDATE {update_count}개 완료! (매칭 {matched_count}/{len(all_targets)}개)")
            log("is_active=0 상태입니다. 매칭 결과 확인 후 is_active=1로 변경해주세요.")
        else:
            log("")
            log(f"DRY RUN: INSERT {len(insert_targets)}개, UPDATE {len(update_targets)}개 대상 (매칭 {matched_count}/{len(all_targets)}개)")
            log("실제 실행하려면 --dry-run 옵션을 제거하고 다시 실행하세요.")

    log("=" * 60)
    log("카테고리 동기화 종료")
    log("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='raw_scraped_data의 category_path를 mall_categories에 동기화 (buyma_category_id 자동 매칭)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='INSERT 하지 않고 미리보기만'
    )

    args = parser.parse_args()
    sync_categories(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
