"""
raw_scraped_data의 category_path를 mall_categories 테이블에 동기화하는 스크립트

목적: raw_scraped_data에 있는 category_path가 mall_categories에 없으면 INSERT
     buyma_category_id는 수기로 입력할 것이므로 NULL로 생성

사용법:
    python sync_categories.py              # 실제 실행
    python sync_categories.py --dry-run    # 미리보기 (INSERT 안함)
"""

import argparse
from datetime import datetime
from sqlalchemy import create_engine, text

# DB 연결 정보
DB_URL = "mysql+pymysql://block:1234@54.180.248.182:3306/buyma?charset=utf8mb4"


def log(message: str, level: str = "INFO") -> None:
    """로그 출력 함수"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def parse_category_path(category_path: str) -> dict:
    """
    카테고리 경로를 파싱하여 depth1, depth2, depth3으로 분리

    Args:
        category_path: "ACTIVITY·LIFE > 액티비티 > 러닝 > 상의" 형식

    Returns:
        {
            'full_path': 'ACTIVITY·LIFE > 액티비티 > 러닝 > 상의',
            'depth1': 'ACTIVITY·LIFE',
            'depth2': '액티비티',
            'depth3': '러닝',
            'gender': None  # 필요시 추출 로직 추가
        }
    """
    if not category_path:
        return None

    parts = [p.strip() for p in category_path.split('>')]

    return {
        'full_path': category_path,
        'depth1': parts[0] if len(parts) > 0 else None,
        'depth2': parts[1] if len(parts) > 1 else None,
        'depth3': parts[2] if len(parts) > 2 else None,
        'gender': None  # 성별은 별도 로직으로 추출 가능
    }


def sync_categories(dry_run: bool = False):
    """
    raw_scraped_data의 category_path를 mall_categories에 동기화

    Args:
        dry_run: True면 INSERT 하지 않고 미리보기만
    """
    engine = create_engine(DB_URL)

    log("=" * 60)
    log("카테고리 동기화 시작")
    log("=" * 60)

    if dry_run:
        log("*** DRY RUN 모드 - INSERT 하지 않음 ***", "WARNING")

    with engine.connect() as conn:
        # 1. raw_scraped_data에서 고유한 category_path 조회
        log("1. raw_scraped_data에서 category_path 조회 중...")
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

        # 2. mall_categories에서 기존 full_path 조회
        log("2. mall_categories에서 기존 full_path 조회 중...")
        result = conn.execute(text("""
            SELECT mall_name, full_path
            FROM mall_categories
            WHERE full_path IS NOT NULL
        """))

        existing_categories = set()
        for row in result:
            key = f"{row[0]}|{row[1]}"
            existing_categories.add(key)

        log(f"   → mall_categories에 {len(existing_categories)}개 카테고리 존재")

        # 3. 누락된 카테고리 찾기
        log("3. 누락된 카테고리 확인 중...")
        missing_categories = []

        for raw_cat in raw_categories:
            key = f"{raw_cat['source_site']}|{raw_cat['category_path']}"
            if key not in existing_categories:
                missing_categories.append(raw_cat)

        log(f"   → {len(missing_categories)}개 카테고리 누락됨")

        if not missing_categories:
            log("모든 카테고리가 이미 존재합니다. 동기화 완료!")
            return

        # 4. 누락된 카테고리 출력 및 INSERT
        log("")
        log("=" * 60)
        log("누락된 카테고리 목록:")
        log("=" * 60)

        for idx, cat in enumerate(missing_categories):
            parsed = parse_category_path(cat['category_path'])
            log(f"[{idx+1}] {cat['source_site']} | {cat['category_path']}")
            log(f"     depth1: {parsed['depth1']}")
            log(f"     depth2: {parsed['depth2']}")
            log(f"     depth3: {parsed['depth3']}")

            if not dry_run:
                # INSERT 실행
                conn.execute(text("""
                    INSERT INTO mall_categories (
                        mall_name, full_path, depth1, depth2, depth3,
                        buyma_category_id, is_active
                    ) VALUES (
                        :mall_name, :full_path, :depth1, :depth2, :depth3,
                        NULL, 1
                    )
                    ON DUPLICATE KEY UPDATE
                        depth1 = VALUES(depth1),
                        depth2 = VALUES(depth2),
                        depth3 = VALUES(depth3)
                """), {
                    'mall_name': cat['source_site'],
                    'full_path': cat['category_path'],
                    'depth1': parsed['depth1'],
                    'depth2': parsed['depth2'],
                    'depth3': parsed['depth3']
                })

        if not dry_run:
            conn.commit()
            log("")
            log(f"총 {len(missing_categories)}개 카테고리 INSERT 완료!")
            log("buyma_category_id는 수기로 입력해주세요.")
        else:
            log("")
            log(f"DRY RUN: {len(missing_categories)}개 카테고리가 INSERT 대상입니다.")
            log("실제 INSERT 하려면 --dry-run 옵션을 제거하고 다시 실행하세요.")

    log("=" * 60)
    log("카테고리 동기화 종료")
    log("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='raw_scraped_data의 category_path를 mall_categories에 동기화'
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
