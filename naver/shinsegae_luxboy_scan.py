# -*- coding: utf-8 -*-
"""
신세계(ssg01) 상품 중 '럭스보이 입점 상품' 판별 스캐너

배경:
  럭스보이는 소싱처에서 제외했으나, 신세계 스마트스토어에 입점해 있어 신세계 수집분에 섞여 들어온다.
  이런 상품은 지재권 문제로 이미지를 쓸 수 없다(상품 자체는 매입처로 계속 사용 가능).
  판별 근거는 상품 상세 영역(div#DEFAULT)의 이미지 주소 도메인 — luxboyimage.com.
  수집기는 네이버 상품 API의 productImages 만 저장하므로(전부 phinf 도메인) DB만으로는 판별 불가.
  → 상세 페이지를 다시 방문해 확인한다.

이 스크립트는 '판정만' 한다. 이미지 삭제·바이마 처리는 후속 스크립트 담당.

판정 결과: shinsegae_luxboy_scan 테이블
  is_luxboy=1  럭스보이 이미지 발견
  is_luxboy=0  상세는 정상 로드됐고 럭스보이 이미지 없음
  detail_ok=0  상세를 못 읽음(로드 실패/캡챠) → 판정 보류, 재실행 대상

우선순위(tier):
  1  이미 바이마에 등록된 목록에 물린 신세계 소싱
  2  등록후보(살아있는 소싱)
  3  나머지 신세계 상품 전부

사용법:
    python3 shinsegae_luxboy_scan.py --create-table              # 테이블 생성(최초 1회)
    python3 shinsegae_luxboy_scan.py --test-url <URL>            # 단일 URL 판정 테스트(DB 기록 안 함)
    python3 shinsegae_luxboy_scan.py --tier 1 --limit 100        # 등록분 100개 시범
    python3 shinsegae_luxboy_scan.py --tier 1                    # 등록분 전체
    python3 shinsegae_luxboy_scan.py --tier 2
    python3 shinsegae_luxboy_scan.py --tier 3
    python3 shinsegae_luxboy_scan.py --retry-failed              # detail_ok=0 재시도
    python3 shinsegae_luxboy_scan.py --report                    # 진행/집계만 출력

중단해도 됨 — 이미 판정한 상품은 건너뛰므로 그냥 다시 실행하면 이어서 돈다.
"""

import os
import sys
import io
import re
import json
import random
import logging
import asyncio
import argparse
from typing import Dict, List, Optional

import pymysql
from dotenv import load_dotenv

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

SOURCE_SITE = 'shinsegae'
STORE_HOME = 'https://smartstore.naver.com/ssg01'

# naver/naver_cookies.json — 수집기와 공용
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'naver_cookies.json')

# 차단 대상 이미지 도메인. 추후 다른 입점사가 추가되면 여기에만 넣으면 된다.
BLOCKED_IMAGE_DOMAINS = ('luxboyimage.com',)

DETAIL_DELAY = (0.6, 1.4)     # 상품 간 간격 (네이버 429 회피)
DETAIL_MAX_RETRIES = 2
SAVE_EVERY = 20               # 판정 결과 배치 저장 단위
COOLDOWN_AFTER_FAILS = 5      # 연속 실패 이 횟수면 긴 휴식
COOLDOWN_SECONDS = 120


def get_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '54.180.248.182'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER', 'block'),
        password=os.getenv('DB_PASSWORD', '1234'),
        database=os.getenv('DB_NAME', 'buyma'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


# =====================================================
# 테이블
# =====================================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shinsegae_luxboy_scan (
    mall_product_id VARCHAR(100) NOT NULL,
    is_luxboy       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=럭스보이 이미지 발견',
    detail_ok       TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '0=상세를 못 읽음(판정 보류)',
    evidence        VARCHAR(500) NULL COMMENT '발견된 이미지 URL 예시 1개',
    fail_reason     VARCHAR(200) NULL,
    tier            TINYINT      NULL COMMENT '스캔 당시 우선순위',
    checked_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (mall_product_id),
    KEY idx_is_luxboy (is_luxboy),
    KEY idx_detail_ok (detail_ok)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='신세계 수집분 중 럭스보이 입점상품 판별 결과'
"""

# ★ COLLATE 를 raw_scraped_data / ace_products 와 같은 utf8mb4_unicode_ci 로 맞춘다.
#   서버 기본값(utf8mb4_uca1400_ai_ci)으로 만들면 mall_product_id 조인에서
#   "Illegal mix of collations" 로 대상 조회가 통째로 실패한다. (2026-08-18)

# 변환기가 실제로 보는 차단 목록. 판별 결과표는 '판정 기록'이고, 이쪽이 '집행'이다.
#   둘을 나눠 두면 판정과 차단이 어긋나므로(실측: 판정 1,053 vs 차단 446)
#   판정을 저장할 때 여기에도 같이 넣는다.
CREATE_BLOCK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS blocked_image_products (
    source_site     VARCHAR(50)  NOT NULL,
    mall_product_id VARCHAR(100) NOT NULL,
    reason          VARCHAR(100) NOT NULL DEFAULT 'ip_rights',
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_site, mall_product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='상품 단위 이미지 차단 — 몰 전체가 아니라 이 상품만 사진을 쓰지 않는다'
"""


def create_table(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute(CREATE_BLOCK_TABLE_SQL)
        # 이미 서버 기본 COLLATE 로 만들어져 있었으면 맞춰준다(데이터 보존).
        cur.execute("""
            SELECT TABLE_COLLATION FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'shinsegae_luxboy_scan'
        """)
        row = cur.fetchone()
        if row and row['TABLE_COLLATION'] != 'utf8mb4_unicode_ci':
            logger.info(f"COLLATE 교정: {row['TABLE_COLLATION']} → utf8mb4_unicode_ci")
            cur.execute("ALTER TABLE shinsegae_luxboy_scan "
                        "CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn.commit()
    logger.info("shinsegae_luxboy_scan / blocked_image_products 테이블 준비 완료")


def save_results(rows: List[Dict], retries: int = 4):
    """판정 결과 저장. 매번 새 연결로 쓴다.

    스캔은 브라우저 작업이 대부분이라 커넥션이 몇 분씩 놀고, 그 사이 원격 DB가
    연결을 끊는다(2013 Lost connection). 오래 들고 있지 말고 저장할 때만 연결한다.
    """
    if not rows:
        return
    last_err = None
    for attempt in range(retries):
        try:
            conn = get_connection()
            try:
                _save_once(conn, rows)
                return
            finally:
                conn.close()
        except pymysql.err.OperationalError as e:
            last_err = e
            wait = 5 * (attempt + 1)
            logger.warning(f"DB 저장 실패({e.args[0]}) — {wait}초 후 재시도 {attempt + 1}/{retries}")
            import time as _t
            _t.sleep(wait)
    logger.error(f"DB 저장 최종 실패 — {len(rows)}건 유실: {last_err}")


def _save_once(conn, rows: List[Dict]):
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO shinsegae_luxboy_scan
                (mall_product_id, is_luxboy, detail_ok, evidence, fail_reason, tier)
            VALUES (%(mall_product_id)s, %(is_luxboy)s, %(detail_ok)s,
                    %(evidence)s, %(fail_reason)s, %(tier)s)
            ON DUPLICATE KEY UPDATE
                is_luxboy   = VALUES(is_luxboy),
                detail_ok   = VALUES(detail_ok),
                evidence    = VALUES(evidence),
                fail_reason = VALUES(fail_reason),
                tier        = VALUES(tier),
                checked_at  = NOW()
        """, rows)

        # 럭스보이로 판정된 건 곧바로 차단 목록에 올린다.
        #   따로 옮겨 담는 구조면 사람이 잊는다(실측: 판정 1,053 vs 차단 446).
        #   ★ 빼는 건 자동으로 하지 않는다 — 재판정 때 상세를 못 읽으면(detail_ok=0)
        #     is_luxboy 가 0 으로 남는데, 그걸 '해제'로 읽으면 차단이 풀려 사진이 샌다.
        blocked = [{'mp': r['mall_product_id']} for r in rows if r.get('is_luxboy')]
        if blocked:
            cur.execute(CREATE_BLOCK_TABLE_SQL)
            cur.executemany("""
                INSERT IGNORE INTO blocked_image_products
                    (source_site, mall_product_id, reason)
                VALUES (%(source_site)s, %(mp)s, 'luxboy')
            """, [{**b, 'source_site': SOURCE_SITE} for b in blocked])
    conn.commit()


# =====================================================
# 대상 선정
# =====================================================

# tier 1: 이미 바이마에 게시된 목록에 물린 신세계 소싱
TIER1_SQL = """
SELECT DISTINCT r.mall_product_id, r.product_url
FROM buyma_listings b
JOIN source_offerings so ON so.listing_id = b.id
                        AND so.source_site = %s AND so.is_active = 1
JOIN ace_products    a  ON a.id = so.ace_product_id
JOIN raw_scraped_data r ON r.id = a.raw_data_id
LEFT JOIN shinsegae_luxboy_scan s ON s.mall_product_id = r.mall_product_id
WHERE b.is_published = 1
  AND s.mall_product_id IS NULL
"""

# tier 2: 살아있는 소싱(등록후보) — tier1 제외분
TIER2_SQL = """
SELECT DISTINCT r.mall_product_id, r.product_url
FROM source_offerings so
JOIN ace_products    a  ON a.id = so.ace_product_id
JOIN raw_scraped_data r ON r.id = a.raw_data_id
LEFT JOIN shinsegae_luxboy_scan s ON s.mall_product_id = r.mall_product_id
WHERE so.source_site = %s AND so.is_active = 1
  AND s.mall_product_id IS NULL
"""

# tier 3: 나머지 신세계 상품 전부
TIER3_SQL = """
SELECT r.mall_product_id, r.product_url
FROM raw_scraped_data r
LEFT JOIN shinsegae_luxboy_scan s ON s.mall_product_id = r.mall_product_id
WHERE r.source_site = %s
  AND s.mall_product_id IS NULL
"""

RETRY_SQL = """
SELECT s.mall_product_id, r.product_url
FROM shinsegae_luxboy_scan s
JOIN raw_scraped_data r ON r.source_site = %s AND r.mall_product_id = s.mall_product_id
WHERE s.detail_ok = 0
"""

TIER_SQL = {1: TIER1_SQL, 2: TIER2_SQL, 3: TIER3_SQL}


def select_targets(conn, tier: int, limit: Optional[int], retry_failed: bool) -> List[Dict]:
    sql = RETRY_SQL if retry_failed else TIER_SQL[tier]
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cur:
        cur.execute(sql, (SOURCE_SITE,))
        rows = cur.fetchall()
    for r in rows:
        if not r.get('product_url'):
            r['product_url'] = f"{STORE_HOME}/products/{r['mall_product_id']}"
    return rows


# =====================================================
# 판정
# =====================================================

_BLOCKED_RE = re.compile('|'.join(re.escape(d) for d in BLOCKED_IMAGE_DOMAINS), re.IGNORECASE)
# 상세 영역이 실제로 내려왔는지 확인용 — 이게 안 잡히면 '럭스보이 없음'으로 단정하면 안 된다.
#   ★ id="DEFAULT" 는 일부 상품에만 있다(판매자가 에디터로 꾸민 상세). 백화점 직매장 상품처럼
#     상세가 안내문 텍스트뿐인 건 그 표식이 없어서, 멀쩡한 상품이 전부 '판정 보류'로 샜다.
#     상세 데이터 자체가 왔는지는 detailContentText 로 본다(두 유형 모두에 존재). (2026-08-18)
_DETAIL_MARKERS = ('detailContentText', 'id="DEFAULT"', "id='DEFAULT'",
                   'se-main-container', '_productDetail')


def _find_blocked(html: str) -> Optional[str]:
    """차단 도메인 이미지 URL 하나를 찾아 반환. 없으면 None."""
    m = _BLOCKED_RE.search(html or '')
    if not m:
        return None
    # 근처에서 온전한 URL을 뽑아 증거로 남긴다
    around = html[max(0, m.start() - 300): m.end() + 300]
    url_m = re.search(r'https?://[^\s"\'<>\\]*(?:%s)[^\s"\'<>\\]*' % '|'.join(
        re.escape(d) for d in BLOCKED_IMAGE_DOMAINS), around, re.IGNORECASE)
    return (url_m.group(0) if url_m else m.group(0))[:500]


async def judge_product(page, product_url: str) -> Dict:
    """상세 페이지를 열어 차단 도메인 이미지 사용 여부 판정.

    상세 본문은 스크롤 지연 로딩이라 data-src 로만 들어있는 경우가 있어
    HTML 전체 문자열에서 도메인을 찾는다(src/data-src 둘 다 잡힘).
    상품 JSON XHR(detailContents)도 같이 캡처해 보조 근거로 쓴다.
    """
    captured_json = []

    def on_response(resp):
        if '/i/v2/channels/' in resp.url and '/products/' in resp.url:
            captured_json.append(resp)

    page.on('response', on_response)
    try:
        try:
            # ★ wait_until='domcontentloaded' 필수. 기본값('load')은 광고·추천위젯 등
            #   부수 리소스까지 전부 기다려서, 무거운 ssg01 상품은 30초 안에 안 끝나고
            #   타임아웃 → 멀쩡한 상품이 '판정 보류'로 새어 나간다. (2026-08-18)
            await page.goto(product_url, referer=STORE_HOME,
                            wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            return {'detail_ok': 0, 'is_luxboy': 0, 'evidence': None,
                    'fail_reason': f'로드 실패: {str(e)[:150]}'}

        title = (await page.title()) or ''
        if '보안' in title or 'captcha' in title.lower():
            return {'detail_ok': 0, 'is_luxboy': 0, 'evidence': None,
                    'fail_reason': f'캡챠/차단 의심 (title={title[:60]})'}

        # 상세 본문까지 그려지도록 아래로 내린다(지연 로딩 대비)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)
        except Exception:
            pass
        try:
            await page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            pass

        html = await page.content()

        # 상품 JSON 의 상세본문도 합쳐서 본다
        for resp in captured_json:
            try:
                if resp.status == 200:
                    data = await resp.json()
                    dc = (data or {}).get('detailContents')
                    if dc:
                        html += json.dumps(dc, ensure_ascii=False)
            except Exception:
                pass

        hit = _find_blocked(html)
        if hit:
            return {'detail_ok': 1, 'is_luxboy': 1, 'evidence': hit, 'fail_reason': None}

        # 럭스보이가 없다고 단정하려면 상세 영역이 실제로 있었어야 한다
        if not any(mk in html for mk in _DETAIL_MARKERS):
            return {'detail_ok': 0, 'is_luxboy': 0, 'evidence': None,
                    'fail_reason': '상세 영역 미확인 — 판정 보류'}

        return {'detail_ok': 1, 'is_luxboy': 0, 'evidence': None, 'fail_reason': None}
    finally:
        page.remove_listener('response', on_response)


# =====================================================
# 실행
# =====================================================

async def run(tier: int, limit: Optional[int], retry_failed: bool,
              headless: bool, dry_run: bool, test_url: Optional[str]):
    from playwright.async_api import async_playwright

    # 대상 조회에만 쓰고 바로 닫는다 — 스캔은 몇 시간짜리라 커넥션을 들고 있으면
    #   원격 DB 가 놀고 있는 연결을 끊는다. 저장은 save_results 가 그때그때 새로 연결.
    conn = None
    try:
        if test_url:
            # 단일 URL 테스트는 DB를 아예 안 쓴다(원격 DB가 죽어도 판정 로직 확인 가능).
            targets = [{'mall_product_id': test_url.rstrip('/').split('/')[-1],
                        'product_url': test_url}]
            logger.info(f"단일 URL 테스트: {test_url}")
        else:
            conn = get_connection()
            targets = select_targets(conn, tier, limit, retry_failed)
            label = 'detail_ok=0 재시도' if retry_failed else f'tier {tier}'
            logger.info(f"판정 대상({label}): {len(targets):,}개")
            if not targets:
                logger.info("판정할 상품 없음 — 이미 전부 확인됨")
                return
        if conn is not None:
            conn.close()
            conn = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 900},
                locale='ko-KR',
                user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/146.0.0.0 Safari/537.36'),
            )
            if os.path.exists(COOKIE_FILE):
                with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                    await context.add_cookies(json.load(f))
                logger.info(f"쿠키 로드: {COOKIE_FILE}")
            page = await context.new_page()

            pending: List[Dict] = []
            stats = {'luxboy': 0, 'clean': 0, 'failed': 0}
            consecutive_fail = 0
            total = len(targets)

            for i, t in enumerate(targets, 1):
                res = None
                for attempt in range(DETAIL_MAX_RETRIES + 1):
                    res = await judge_product(page, t['product_url'])
                    if res['detail_ok']:
                        break
                    if attempt < DETAIL_MAX_RETRIES:
                        wait = 5 * (attempt + 1)
                        logger.warning(f"[{i}/{total}] {t['mall_product_id']} 재시도 {attempt+1} — {wait}초 대기 ({res['fail_reason']})")
                        await asyncio.sleep(wait)

                if res['detail_ok'] and res['is_luxboy']:
                    stats['luxboy'] += 1
                    consecutive_fail = 0
                    logger.info(f"[{i}/{total}] {t['mall_product_id']} ★ 럭스보이 — {res['evidence'][:90]}")
                elif res['detail_ok']:
                    stats['clean'] += 1
                    consecutive_fail = 0
                    if i % 20 == 0 or total <= 50:
                        logger.info(f"[{i}/{total}] {t['mall_product_id']} 정상 "
                                    f"(누적 럭스보이 {stats['luxboy']} / 정상 {stats['clean']} / 실패 {stats['failed']})")
                else:
                    stats['failed'] += 1
                    consecutive_fail += 1
                    logger.warning(f"[{i}/{total}] {t['mall_product_id']} 판정 보류 — {res['fail_reason']}")

                pending.append({
                    'mall_product_id': t['mall_product_id'],
                    'is_luxboy': res['is_luxboy'],
                    'detail_ok': res['detail_ok'],
                    'evidence': res['evidence'],
                    'fail_reason': res['fail_reason'],
                    'tier': None if (retry_failed or test_url) else tier,
                })

                if not dry_run and not test_url and len(pending) >= SAVE_EVERY:
                    save_results(pending)
                    pending = []

                if consecutive_fail >= COOLDOWN_AFTER_FAILS:
                    logger.error(f"연속 {consecutive_fail}건 실패 — {COOLDOWN_SECONDS}초 휴식 (차단 의심)")
                    await asyncio.sleep(COOLDOWN_SECONDS)
                    consecutive_fail = 0

                await asyncio.sleep(random.uniform(*DETAIL_DELAY))

            if not dry_run and not test_url:
                save_results(pending)
            await browser.close()

        logger.info("=" * 60)
        logger.info(f"  판정 완료 — 럭스보이 {stats['luxboy']:,} / 정상 {stats['clean']:,} / 보류 {stats['failed']:,}")
        judged = stats['luxboy'] + stats['clean']
        if judged:
            logger.info(f"  럭스보이 비율: {stats['luxboy'] / judged * 100:.1f}%")
        if dry_run or test_url:
            logger.info("  (DB 기록 안 함)")
        logger.info("=" * 60)
    finally:
        if conn is not None:
            conn.close()


def report(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tier, detail_ok, is_luxboy, COUNT(*) n
            FROM shinsegae_luxboy_scan GROUP BY 1,2,3 ORDER BY 1,2,3
        """)
        rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) n FROM raw_scraped_data WHERE source_site=%s", (SOURCE_SITE,))
        total = cur.fetchone()['n']
    done = sum(r['n'] for r in rows)
    lux = sum(r['n'] for r in rows if r['is_luxboy'] == 1)
    hold = sum(r['n'] for r in rows if r['detail_ok'] == 0)
    logger.info("=" * 60)
    logger.info(f"  신세계 상품 전체: {total:,}")
    logger.info(f"  판정 완료:        {done:,} ({done / total * 100:.1f}%)" if total else "  판정 완료: 0")
    logger.info(f"  럭스보이:         {lux:,}")
    logger.info(f"  판정 보류:        {hold:,}")
    logger.info("-" * 60)
    for r in rows:
        logger.info(f"  tier={r['tier']} detail_ok={r['detail_ok']} is_luxboy={r['is_luxboy']} → {r['n']:,}")
    logger.info("=" * 60)


def main():
    ap = argparse.ArgumentParser(description='신세계 수집분 중 럭스보이 입점상품 판별')
    ap.add_argument('--create-table', action='store_true', help='결과 테이블 생성 후 종료')
    ap.add_argument('--report', action='store_true', help='진행 상황만 출력하고 종료')
    ap.add_argument('--tier', type=int, choices=[1, 2, 3], default=1,
                    help='1=바이마 등록분(기본) 2=등록후보 3=전체')
    ap.add_argument('--limit', type=int, help='이번 실행에서 처리할 최대 개수')
    ap.add_argument('--retry-failed', action='store_true', help='판정 보류(detail_ok=0)분 재시도')
    ap.add_argument('--test-url', help='단일 URL 판정 테스트 (DB 기록 안 함)')
    ap.add_argument('--headless', action='store_true', help='브라우저 창 숨김')
    ap.add_argument('--dry-run', action='store_true', help='판정만 하고 DB 기록 안 함')
    args = ap.parse_args()

    if args.create_table or args.report:
        conn = get_connection()
        try:
            if args.create_table:
                create_table(conn)
            else:
                report(conn)
        finally:
            conn.close()
        return

    asyncio.run(run(args.tier, args.limit, args.retry_failed,
                    args.headless, args.dry_run, args.test_url))


if __name__ == '__main__':
    main()
