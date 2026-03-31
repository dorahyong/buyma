"""
카테고리 정리 도구

mall_categories ↔ ace_products 간 카테고리 매핑을 관리한다.
일반적인 사용 순서: register → match(또는 import) → apply

────────────────────────────────────────────────────────────────
1. register  미등록 경로를 mall_categories에 등록
────────────────────────────────────────────────────────────────
  대상: raw_scraped_data.category_path가 같은 source_site의
        mall_categories.full_path에 없는 경로
  처리: INSERT (buyma_category_id=NULL, is_active=NULL)
        gender 자동 추출 (MEN→male, WOMEN→female, KIDS→kids)

────────────────────────────────────────────────────────────────
2. match     Gemini API로 buyma_category_id 자동 매칭
────────────────────────────────────────────────────────────────
  대상: mall_categories에서 buyma_category_id IS NULL
        AND (is_active=1 OR is_active IS NULL)
        ※ is_active=0 (명시적 비활성화)은 제외
  처리: categories.md(buyma 마스터 599개) 기반, 100건씩 배치
        buyma_category_id UPDATE (is_active는 변경 안 함)

────────────────────────────────────────────────────────────────
3. apply     ace_products.category_id=0에 매핑 반영
────────────────────────────────────────────────────────────────
  대상: ace_products.category_id=0
        AND mall_categories.buyma_category_id IS NOT NULL
        AND (is_active=1 OR is_active IS NULL)
  조인: ace_products.raw_data_id → raw_scraped_data.id
        → category_path = mall_categories.full_path (같은 source_site)
  처리: ace_products.category_id = mall_categories.buyma_category_id

────────────────────────────────────────────────────────────────
4. import    txt → CSV 변환 후 mall_categories 일괄 업데이트
────────────────────────────────────────────────────────────────
  CSV 형식 (헤더 필수):
      id,buyma_category_id
      1153,4548        ← 값 있으면 buyma_category_id=값, is_active=1
      1475,            ← 빈 값이면 buyma_category_id=NULL, is_active=0

────────────────────────────────────────────────────────────────
is_active 상태 의미:
  NULL = 신규 등록 (미검증)     → match/apply 대상 O
  1    = 활성 (검증 완료)       → match/apply 대상 O
  0    = 명시적 비활성화        → match/apply 대상 X
────────────────────────────────────────────────────────────────

사용법:
    python category_cleaner.py register                         # 미등록 경로 INSERT
    python category_cleaner.py register --mall labellusso       # 특정 수집처만
    python category_cleaner.py match                            # Gemini 자동 매칭
    python category_cleaner.py match --mall labellusso          # 특정 수집처만
    python category_cleaner.py match --dry-run                  # 미리보기
    python category_cleaner.py apply                            # ace_products 반영
    python category_cleaner.py apply --mall labellusso          # 특정 수집처만
    python category_cleaner.py import --txt data.txt             # txt → csv 변환 + 업데이트
    python category_cleaner.py import --csv mappings.csv        # CSV로 직접 업데이트
"""

import os
import sys
import csv
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

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
CATEGORIES_MD = Path(__file__).resolve().parent.parent / "okmall" / "buyma_master_data_20260226" / "md" / "categories.md"


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306)),
        charset='utf8mb4',
    )


# ── 1. register ──────────────────────────────────────────────

def cmd_register(args):
    """raw_scraped_data의 category_path 중 mall_categories에 없는 경로를 등록"""
    conn = get_connection()
    cur = conn.cursor(pymysql.cursors.DictCursor)

    mall_filter = ""
    params = []
    if args.mall:
        mall_filter = "AND rsd.source_site = %s"
        params.append(args.mall)

    # mall_categories에 없는 category_path 찾기
    cur.execute(f"""
        SELECT rsd.source_site, rsd.category_path, COUNT(*) as cnt
        FROM raw_scraped_data rsd
        WHERE rsd.category_path IS NOT NULL AND rsd.category_path != ''
        {mall_filter}
        AND NOT EXISTS (
            SELECT 1 FROM mall_categories mc
            WHERE mc.full_path = rsd.category_path AND mc.mall_name = rsd.source_site
        )
        GROUP BY rsd.source_site, rsd.category_path
        ORDER BY rsd.source_site, cnt DESC
    """, params)

    missing = cur.fetchall()
    log(f"미등록 경로: {len(missing)}개")

    if not missing:
        log("모든 category_path가 mall_categories에 등록되어 있습니다.")
        conn.close()
        return

    inserted = 0
    for row in missing:
        source_site = row['source_site']
        category_path = row['category_path']
        parts = [p.strip() for p in category_path.split(' > ')]

        gender = 'unisex'
        if any(k in category_path for k in ['여성', 'WOMEN']):
            gender = 'female'
        elif any(k in category_path for k in ['남성', 'MEN']):
            gender = 'male'
        elif any(k in category_path for k in ['KIDS']):
            gender = 'kids'

        depth1 = parts[1] if len(parts) > 1 else ''
        depth2 = parts[2] if len(parts) > 2 else ''
        depth3 = parts[3] if len(parts) > 3 else ''

        cur.execute("""
            INSERT INTO mall_categories
            (mall_name, category_id, gender, depth1, depth2, depth3, full_path, buyma_category_id, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
        """, (source_site, category_path, gender, depth1, depth2, depth3, category_path))
        inserted += 1

        if inserted % 50 == 0:
            log(f"  {inserted}/{len(missing)} 등록...")

    conn.commit()
    conn.close()
    log(f"완료: {inserted}개 경로 등록됨 (buyma_category_id 매핑 필요)")


# ── 2. match ─────────────────────────────────────────────────

def load_buyma_categories() -> list:
    """categories.md에서 buyma 카테고리 목록 로드"""
    categories = []
    with open(CATEGORIES_MD, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line.startswith('|') or line.startswith('| ---') or line.startswith('| \ufeff'):
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


def match_with_gemini(unmapped: list, buyma_categories: list) -> dict:
    """Gemini API로 미매칭 카테고리를 buyma 카테고리에 매칭"""
    buyma_list = [{'id': c['id'], 'path_ko': f"{c['paths_ko']}/{c['name_ko']}"} for c in buyma_categories]

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


def cmd_match(args):
    """mall_categories에서 buyma_category_id IS NULL인 행을 Gemini로 자동 매칭"""
    if args.dry_run:
        log("*** DRY RUN 모드 - UPDATE 하지 않음 ***", "WARNING")

    log("buyma 카테고리 로드 중...")
    buyma_categories = load_buyma_categories()
    log(f"  → {len(buyma_categories)}개 buyma 카테고리 로드됨")

    conn = get_connection()
    cur = conn.cursor(pymysql.cursors.DictCursor)

    sql = "SELECT id, mall_name, gender, depth1, depth2, full_path FROM mall_categories WHERE buyma_category_id IS NULL AND (is_active = 1 OR is_active IS NULL)"
    params = []
    if args.mall:
        sql += " AND mall_name = %s"
        params.append(args.mall)

    cur.execute(sql, params)
    unmapped = cur.fetchall()
    log(f"미매칭 카테고리: {len(unmapped)}개")

    if not unmapped:
        log("모든 카테고리가 이미 매칭 완료되었습니다.")
        conn.close()
        return

    # 배치 처리 (Gemini 토큰 제한 대응)
    BATCH_SIZE = 100
    id_to_name = {cat['id']: f"{cat['paths_ko']}/{cat['name_ko']}" for cat in buyma_categories}
    total_matched = 0
    total_updated = 0

    for batch_start in range(0, len(unmapped), BATCH_SIZE):
        batch = unmapped[batch_start:batch_start + BATCH_SIZE]
        log(f"Gemini API 매칭 중... ({batch_start+1}~{batch_start+len(batch)}/{len(unmapped)})")

        matched = match_with_gemini(batch, buyma_categories)
        batch_matched = sum(1 for v in matched.values() if v is not None)
        total_matched += batch_matched

        for cat in batch:
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
                total_updated += 1

        if batch_start + BATCH_SIZE < len(unmapped):
            time.sleep(2)

    if not args.dry_run:
        conn.commit()
        log(f"UPDATE {total_updated}개 완료! (매칭 {total_matched}/{len(unmapped)}개)")
    else:
        log(f"DRY RUN: {total_matched}/{len(unmapped)}개 매칭됨. 실제 실행하려면 --dry-run 제거")

    conn.close()


# ── 3. apply ─────────────────────────────────────────────────

def cmd_apply(args):
    """ace_products.category_id=0인 행에 mall_categories의 buyma_category_id 반영"""
    conn = get_connection()
    cur = conn.cursor()

    mall_filter = ""
    params = []
    if args.mall:
        mall_filter = "AND ap.source_site = %s"
        params.append(args.mall)

    # category_id=0이고, mall_categories에 매핑된 경로가 있는 것만 업데이트
    cur.execute(f"""
        UPDATE ace_products ap
        JOIN raw_scraped_data rsd ON ap.raw_data_id = rsd.id
        JOIN mall_categories mc ON rsd.category_path = mc.full_path
            AND mc.mall_name = ap.source_site
        SET ap.category_id = mc.buyma_category_id
        WHERE ap.category_id = 0
        AND mc.buyma_category_id IS NOT NULL
        AND (mc.is_active = 1 OR mc.is_active IS NULL)
        {mall_filter}
    """, params)

    updated = cur.rowcount
    conn.commit()

    # 결과 확인
    cur.execute(f"""
        SELECT ap.source_site,
               SUM(ap.category_id = 0) as still_zero,
               SUM(ap.category_id != 0) as has_category,
               COUNT(*) as total
        FROM ace_products ap
        WHERE ap.is_active = 1
        {'AND ap.source_site = %s' if args.mall else ''}
        GROUP BY ap.source_site
    """, params)

    rows = cur.fetchall()
    log(f"ace_products category_id 업데이트: {updated}건")
    log("")
    log(f"{'source_site':<16} {'category_id=0':>13} {'매핑완료':>10} {'전체':>8}")
    log("-" * 50)
    for row in rows:
        log(f"{row[0]:<16} {row[1]:>13} {row[2]:>10} {row[3]:>8}")

    conn.close()


# ── 4. import ────────────────────────────────────────────────

def txt_to_csv(txt_path: str) -> str:
    """탭 구분 txt 파일에서 id, buyma_category_id, is_active 컬럼만 추출하여 CSV 생성"""
    csv_path = txt_path.rsplit('.', 1)[0] + '.csv'

    with open(txt_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f, delimiter='\t')

        if 'id' not in reader.fieldnames:
            log(f"txt 헤더에 'id' 컬럼이 필요합니다.", "ERROR")
            return None

        rows = []
        for row in reader:
            rows.append({
                'id': row['id'].strip(),
                'buyma_category_id': row.get('buyma_category_id', '').strip(),
                'is_active': row.get('is_active', '').strip(),
            })

    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['id', 'buyma_category_id', 'is_active'])
        writer.writeheader()
        writer.writerows(rows)

    log(f"CSV 생성: {csv_path} ({len(rows)}건)")
    return csv_path


def cmd_import(args):
    """고정 txt 파일(category_cleaner_import_csv.txt)을 읽어 mall_categories 일괄 업데이트

    txt 형식: 탭 구분, 헤더에 id, buyma_category_id, is_active 포함
    사용법: txt 파일을 채워넣고 `python category_cleaner.py import` 실행
    """
    txt_path = os.path.join(os.path.dirname(__file__), 'category_cleaner_import_csv.txt')
    if not os.path.exists(txt_path):
        log(f"txt 파일 없음: {txt_path}", "ERROR")
        return

    csv_path = txt_to_csv(txt_path)
    if not csv_path:
        return

    # csv 기준으로 UPDATE
    conn = get_connection()
    cur = conn.cursor()

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)

        if 'id' not in reader.fieldnames:
            log("CSV 헤더에 'id' 컬럼이 필요합니다.", "ERROR")
            conn.close()
            return

        updated = 0
        skipped = 0
        for row in reader:
            mc_id = row['id'].strip()
            buyma_cat = row.get('buyma_category_id', '').strip()
            is_active = row.get('is_active', '').strip()

            if not mc_id:
                skipped += 1
                continue

            # buyma_category_id
            buyma_val = int(buyma_cat) if buyma_cat and buyma_cat.isdigit() else None
            # is_active
            if is_active and is_active.isdigit():
                active_val = int(is_active)
            elif buyma_val is not None:
                active_val = 1
            else:
                active_val = 0

            if buyma_val is not None:
                cur.execute(
                    "UPDATE mall_categories SET buyma_category_id = %s, is_active = %s WHERE id = %s",
                    (buyma_val, active_val, int(mc_id))
                )
            else:
                cur.execute(
                    "UPDATE mall_categories SET buyma_category_id = NULL, is_active = %s WHERE id = %s",
                    (active_val, int(mc_id))
                )
            updated += 1

    conn.commit()
    conn.close()
    log(f"완료: {updated}건 업데이트 (스킵: {skipped}건)")


# ── main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='카테고리 정리 도구')
    subparsers = parser.add_subparsers(dest='command', help='실행할 명령')

    # register
    p_register = subparsers.add_parser('register', help='미등록 category_path를 mall_categories에 등록')
    p_register.add_argument('--mall', type=str, default=None, help='특정 수집처만 (예: labellusso)')

    # match
    p_match = subparsers.add_parser('match', help='Gemini로 buyma_category_id 자동 매칭')
    p_match.add_argument('--mall', type=str, default=None, help='특정 수집처만')
    p_match.add_argument('--dry-run', action='store_true', help='UPDATE 안 하고 미리보기')

    # apply
    p_apply = subparsers.add_parser('apply', help='ace_products.category_id=0에 매핑 반영')
    p_apply.add_argument('--mall', type=str, default=None, help='특정 수집처만')

    # import
    p_import = subparsers.add_parser('import', help='category_cleaner_import_csv.txt → mall_categories 일괄 업데이트')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    log("=" * 60)
    log(f"카테고리 정리: {args.command}")
    log("=" * 60)

    if args.command == 'register':
        cmd_register(args)
    elif args.command == 'match':
        cmd_match(args)
    elif args.command == 'apply':
        cmd_apply(args)
    elif args.command == 'import':
        cmd_import(args)

    log("=" * 60)
    log("완료")
    log("=" * 60)


if __name__ == "__main__":
    main()
