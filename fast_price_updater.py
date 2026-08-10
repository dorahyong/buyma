# -*- coding: utf-8 -*-
"""
빠른 최저가 업데이트 (fast_price)

STOCK(merge→reconcile)과 같은 일(최저가 확보·마진 없으면 출품정지)을 하되,
소싱처는 방문하지 않고 DB 소싱값 + 바이마 시세만 본다.

고유 책임:
  - 적응형 스케줄(접전일수록 자주) — buyma_competitor_prices.check_interval_min / next_check_at
  - 경쟁자 없을 때 가격을 올리지 않음(인하·유지 전용; 인상은 STOCK 몫)
  - 상시 due 배치 루프

BUYMA·목록 규칙(판매가·winner·옵션합집합·edit/retire)은 STOCK과 동일 코드:
  reconcile_runner.process_one_group → ensure_group + execute_edit / execute_retire

사용법:
    python fast_price_updater.py                      # 스케줄러 루프
    python fast_price_updater.py --dry-run             # 변경 없이 판단만
    python fast_price_updater.py --brand NIKE           # 스케줄 무시 1회
    python fast_price_updater.py --source okmall        # winner 소싱처 필터, 1회
    python fast_price_updater.py --limit 100
    python fast_price_updater.py --count
    python fast_price_updater.py --id 123               # listing id
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Tuple

import pymysql
import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
OKMALL = os.path.join(BASE, 'okmall')
if OKMALL not in sys.path:
    sys.path.insert(0, OKMALL)

load_dotenv(os.path.join(BASE, '.env'), override=True)

from dedup_corrector_merge import canonicalize  # noqa: E402
from stock_common import (  # noqa: E402
    DB_CONFIG, StockCommonMixin,
)
import reconcile_runner  # noqa: E402
import reconcile_buyma_push as push  # noqa: E402

# reconcile_runner 가 stdout 을 utf-8 로 감싼 뒤이므로, 이중 wrap 하지 않는다.

LOG_DIR = os.path.join(BASE, 'logs')
_log_lock = threading.Lock()


def log(msg: str, level: str = "INFO") -> None:
    """콘솔은 전부. 파일은 1일 1개(logs/fast_price_YYYYMMDD.log), [스킵] 제외."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    if "[스킵]" in msg:
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fpath = os.path.join(LOG_DIR, f"fast_price_{datetime.now().strftime('%Y%m%d')}.log")
        with _log_lock:
            with open(fpath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass

# -------------------------------------------------
# 적응형 스케줄 · 레이트
# -------------------------------------------------
MAX_INTERVAL_MIN = 360       # 안정 수렴 = 6시간
MIN_INTERVAL_MIN = 2         # 접전 최소 = 2분
RATE_PER_SEC = 4.0           # 바이마 검색 상한(전체)
BATCH_SIZE = 400
IDLE_SLEEP_SEC = 30
DEFAULT_WORKERS = 3
REQUEST_DELAY_MIN = 0.1      # 구버전과 동일
REQUEST_DELAY_MAX = 0.3
API_CALL_DELAY = 0.1         # EDIT/출품정지 직후 간격 (구버전과 동일)
ERROR_RETRY_MIN = 30


class RateLimiter:
    def __init__(self, per_sec: float):
        self._interval = 1.0 / per_sec if per_sec > 0 else 0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
            self._next = max(now, self._next) + self._interval


_rate_limiter = RateLimiter(RATE_PER_SEC)


def _next_interval(cur, kept: bool) -> int:
    cur = cur or MAX_INTERVAL_MIN
    if kept:
        return min(cur * 2, MAX_INTERVAL_MIN)
    return min(max(MIN_INTERVAL_MIN, cur // 2), MAX_INTERVAL_MIN)


# -------------------------------------------------
# 본체
# -------------------------------------------------
class FastPriceUpdater(StockCommonMixin):
    """시세 크롤·스케줄은 여기, 판매가/푸시는 reconcile_runner."""

    def __init__(self):
        self.session = requests.Session()
        self.buyma_session = self.session  # StockCommonMixin.get_buyma_lowest_price 용
        self.session.headers.update({
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120.0.0.0 Safari/537.36'),
            'Accept-Language': 'ja,en;q=0.9',
        })

    def get_connection(self) -> pymysql.Connection:
        return pymysql.connect(**DB_CONFIG)

    # ── 대상 조회 (listing 단위) ───────────────────────────
    def get_listings(self, limit: int = None, brand: str = None,
                     source: str = None, listing_id: int = None,
                     respect_schedule: bool = True) -> List[Dict]:
        """출품중 listing. 스케줄 키 = group_key.

        시세 숫자(lowest_price)는 canonicalize(model_no) 행에 저장(STOCK 공유).
        due 판정은 group_key 행의 next_check_at.
        """
        want = limit or BATCH_SIZE
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                if listing_id or brand or source or not respect_schedule:
                    rows = self._fetch_listings(cur, limit=want, brand=brand,
                                               source=source, listing_id=listing_id)
                    rows = self._attach_schedule(cur, rows)
                    if respect_schedule:
                        now = datetime.now()
                        rows = [r for r in rows
                                if r.get('next_check_at') is None or r['next_check_at'] <= now]
                    return rows[:want]

                # 스케줄러: listing 을 id 순으로 페이지하며 due 만 모은다.
                #   (시세표 469k 행에 next_check 가 비어 있는 동안 ORDER BY/NOT EXISTS 는 수 초~수십 초)
                #   주기가 쌓이면 짧은 주기 우선 정렬은 모은 배치 안에서 수행.
                rows = []
                last_id = 0
                now = datetime.now()
                while len(rows) < want:
                    page = self._fetch_listings(
                        cur, extra_sql=" AND bl.id > %s",
                        extra_params=[last_id], limit=200,
                        order="bl.id ASC")
                    if not page:
                        break
                    last_id = page[-1]['listing_id']
                    page = self._attach_schedule(cur, page)
                    for r in page:
                        nxt = r.get('next_check_at')
                        if nxt is not None and nxt > now:
                            continue
                        rows.append(r)
                        if len(rows) >= want:
                            break
                    # 한 바퀴에 due 가 거의 없으면(대부분 미래 스케줄) 과스캔 방지
                    if last_id > 0 and len(rows) == 0 and last_id > 50000:
                        # overdue 키로 보강
                        cur.execute("""
                            SELECT model_key, check_interval_min, next_check_at
                            FROM buyma_competitor_prices
                            WHERE next_check_at IS NOT NULL AND next_check_at <= NOW()
                            LIMIT %s
                        """, (want * 5,))
                        due_sched = {r['model_key']: r for r in cur.fetchall()}
                        if due_sched:
                            keys = list(due_sched.keys())
                            ph = ','.join(['%s'] * len(keys))
                            part = self._fetch_listings(
                                cur,
                                extra_sql=f" AND bl.group_key COLLATE utf8mb4_uca1400_ai_ci IN ({ph})",
                                extra_params=keys)
                            for r in part:
                                s = due_sched.get(r['group_key']) or {}
                                r['check_interval_min'] = s.get('check_interval_min')
                                r['next_check_at'] = s.get('next_check_at')
                                r['model_key'] = r['group_key']
                                rows.append(r)
                        break

                rows.sort(key=lambda x: (
                    x.get('check_interval_min') or MAX_INTERVAL_MIN,
                    x.get('next_check_at') or datetime(1970, 1, 1),
                    x['listing_id'],
                ))
                return rows[:want]
        finally:
            conn.close()

    def _fetch_listings(self, cur, limit: int = None, brand: str = None,
                        source: str = None, listing_id: int = None,
                        extra_sql: str = '', extra_params: list = None,
                        order: str = 'bl.id ASC') -> List[Dict]:
        sql = """
            SELECT
                bl.id AS listing_id,
                bl.brand_id,
                bl.brand_name,
                bl.group_key,
                bl.model_no AS listing_model_no,
                bl.price,
                bl.category_id,
                bl.buyma_product_id,
                bl.winner_offering_id,
                bl.exception_reason,
                COALESCE(a.model_no, bl.model_no, bl.group_key) AS model_no,
                so.purchase_price_krw AS winner_purchase,
                so.source_site AS winner_source,
                bl.group_key AS model_key
            FROM buyma_listings bl
            LEFT JOIN source_offerings so
                   ON so.id = bl.winner_offering_id AND so.is_active = 1
            LEFT JOIN ace_products a ON a.id = so.ace_product_id
            WHERE bl.is_active = 1
              AND bl.is_published = 1
              AND bl.buyma_product_id IS NOT NULL
              AND bl.exception_reason IS NULL
              AND bl.group_key IS NOT NULL
              AND bl.group_key <> ''
        """
        params: list = []
        if listing_id:
            sql += " AND bl.id = %s"
            params.append(listing_id)
        if brand:
            sql += " AND UPPER(bl.brand_name) LIKE %s"
            params.append(f"%{brand.upper()}%")
        if source:
            sql += " AND so.source_site = %s"
            params.append(source.lower())
        if extra_sql:
            sql += extra_sql
            params.extend(extra_params or [])
        sql += f" ORDER BY {order}"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        cur.execute(sql, params)
        return cur.fetchall()

    def _attach_schedule(self, cur, rows: List[Dict]) -> List[Dict]:
        if not rows:
            return rows
        keys = list({r['group_key'] for r in rows if r.get('group_key')})
        sched = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            ph = ','.join(['%s'] * len(chunk))
            cur.execute(f"""
                SELECT model_key, check_interval_min, next_check_at
                FROM buyma_competitor_prices
                WHERE model_key IN ({ph})
            """, chunk)
            for s in cur.fetchall():
                sched[s['model_key']] = s
        for r in rows:
            s = sched.get(r['group_key']) or {}
            r['check_interval_min'] = s.get('check_interval_min')
            r['next_check_at'] = s.get('next_check_at')
            r['model_key'] = r['group_key']
        return rows

    def _mirror_price_for_listing(self, listing_id: int, group_key: str,
                                  lowest_price, error_msg: str = None) -> None:
        """방금 크롤한 시세를 resolve 가 읽을 모든 model_key 에 동일하게 쓴다.

        save_competitor_price(검색품번) 한 줄만 쓰면, 멤버 ace 품번 canon 이
        서로 다를 때 ensure_group 이 옛 시세 행을 집어 판매가가 엇나간다.
        (2026-08-10 listing#10: 검색키 29373 vs group_key 행 28512)
        """
        result = ('found' if lowest_price else
                  ('no_competitor' if (error_msg and '없음' in error_msg) else 'error'))
        keys = set()
        if group_key:
            keys.add(group_key)
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT a.model_no FROM source_offerings so
                    JOIN ace_products a ON a.id = so.ace_product_id
                    WHERE so.listing_id = %s AND so.is_active = 1
                """, (listing_id,))
                for r in cur.fetchall():
                    k = canonicalize(r['model_no'] or '')
                    if k:
                        keys.add(k)
                for key in keys:
                    cur.execute("""
                        INSERT INTO buyma_competitor_prices
                            (model_key, lowest_price, last_result, checked_at)
                        VALUES (%s, %s, %s, NOW())
                        ON DUPLICATE KEY UPDATE
                            lowest_price = VALUES(lowest_price),
                            last_result  = VALUES(last_result),
                            checked_at   = NOW()
                    """, (key, lowest_price, result))
            conn.commit()
        finally:
            conn.close()

    # ── 스케줄 write (group_key 행) ─────────────────────────
    def _reschedule(self, model_key: str, kept: bool, cur_interval_min) -> int:
        """체크 결과로 다음 조회 주기 갱신. 유지=×2(상한 360), 빼앗김=÷2(하한 2).

        next_check_at 에만 ±15% jitter(초) — 구버전과 동일. interval 컬럼은 깨끗한 값 유지.
        """
        new_iv = _next_interval(cur_interval_min, kept)
        base_sec = new_iv * 60
        jittered_sec = int(base_sec + random.uniform(-0.15, 0.15) * base_sec)
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO buyma_competitor_prices
                        (model_key, check_interval_min, next_check_at, checked_at)
                    VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL %s SECOND), NOW())
                    ON DUPLICATE KEY UPDATE
                        check_interval_min = VALUES(check_interval_min),
                        next_check_at = VALUES(next_check_at)
                """, (model_key, new_iv, jittered_sec))
            conn.commit()
        finally:
            conn.close()
        return new_iv

    def _reschedule_error(self, model_key: str, minutes: int = ERROR_RETRY_MIN) -> None:
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO buyma_competitor_prices
                        (model_key, next_check_at, checked_at, last_result)
                    VALUES (%s, DATE_ADD(NOW(), INTERVAL %s MINUTE), NOW(), 'error')
                    ON DUPLICATE KEY UPDATE
                        next_check_at = VALUES(next_check_at),
                        last_result = 'error'
                """, (model_key, minutes))
            conn.commit()
        finally:
            conn.close()

    # ── 단건 처리 ─────────────────────────────────────────
    def process_one(self, row: Dict, dry_run: bool, stats: Dict, stats_lock: threading.Lock,
                    crawl_cache: Dict, cache_lock: threading.Lock) -> None:
        listing_id = row['listing_id']
        model_no = row['model_no']
        model_key = row['model_key']
        brand = row.get('brand_name') or ''
        brand_id = row['brand_id']
        current_price = int(row.get('price') or 0)
        purchase = float(row.get('winner_purchase') or 0)
        cur_iv = row.get('check_interval_min')
        prefix = f"listing#{listing_id} | {brand} | {model_no}"

        if not purchase or purchase <= 0:
            log(f"{prefix} [스킵] winner 매입가 없음")
            with stats_lock:
                stats['skipped'] += 1
            return

        # 시세 크롤 (model_key 당 1회)
        with cache_lock:
            cached = crawl_cache.get(model_key)
        if cached is None:
            _rate_limiter.acquire()
            competitor, err = self.get_buyma_lowest_price(model_no)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            if not dry_run:
                # 검색품번 + 멤버·group_key 전 키에 동일 시세 (resolve 키 분열 방지)
                self._mirror_price_for_listing(listing_id, model_key, competitor, err)
            with cache_lock:
                crawl_cache[model_key] = (competitor, err)
        else:
            competitor, err = cached

        # B: 조회 오류
        if err and "경쟁자 없음" not in err and "검색 결과 없음" not in err:
            log(f"{prefix} [오류] 시세 조회 실패: {err}", "WARNING")
            if not dry_run:
                self._reschedule_error(model_key)
            with stats_lock:
                stats['error'] += 1
            return

        # C: 경쟁자 없음 → 인상 안 함(fast 고유). 스케줄만 유지 쪽으로.
        if err and ("경쟁자 없음" in err or "검색 결과 없음" in err):
            new_iv = _next_interval(cur_iv, True)
            if not dry_run:
                self._reschedule(model_key, kept=True, cur_interval_min=cur_iv)
            log(f"{prefix} [스킵] 경쟁자 없음 (인상 안 함) | 주기 {cur_iv or MAX_INTERVAL_MIN}→{new_iv}분")
            with stats_lock:
                stats['already_lowest'] += 1
            return

        competitor = int(competitor)

        # D: 이미 최저 · 밴드 안 → 푸시 없음
        band_lo, band_hi = competitor - 9, competitor - 1
        if current_price <= competitor and band_lo <= current_price <= band_hi:
            new_iv = _next_interval(cur_iv, True)
            if not dry_run:
                self._reschedule(model_key, kept=True, cur_interval_min=cur_iv)
            log(f"{prefix} [스킵] 이미 최저가 (내 ¥{current_price:,} | 경쟁자 ¥{competitor:,} | 범위 내) | "
                f"주기 {cur_iv or MAX_INTERVAL_MIN}→{new_iv}분")
            with stats_lock:
                stats['already_lowest'] += 1
            return

        # E/F/G: STOCK과 동일 — ensure_group + edit/retire
        #   로그 태그는 구버전과 동일: [조정] / [인하] (실제 retire 는 아래에서 [출품정지])
        kept = current_price <= competitor  # gap 조정은 유지 쪽 주기
        if current_price <= competitor:
            log(f"{prefix} [조정] 최저가지만 gap 큼: ¥{current_price:,}→경쟁자¥{competitor:,} | "
                f"주기 {cur_iv or MAX_INTERVAL_MIN}→{_next_interval(cur_iv, True)}분")
        else:
            log(f"{prefix} [인하] ¥{current_price:,}→경쟁자¥{competitor:,} 기준 | "
                f"주기 {cur_iv or MAX_INTERVAL_MIN}→{_next_interval(cur_iv, False)}분")

        if dry_run:
            # resolve 미리보기(쓰기·푸시 없음)
            conn = self.get_connection()
            try:
                res = reconcile_runner.process_one_group(
                    conn, model_no, brand_id, dry_run=True, scope='published',
                    tag=f"[dry listing#{listing_id}] ")
            finally:
                conn.close()
            mode = res.get('mode') or res.get('reason') or ''
            with stats_lock:
                if res.get('mode') == 'retire' or 'retire' in str(mode):
                    stats['to_soldout'] += 1
                elif res.get('skipped'):
                    stats['skipped'] += 1
                else:
                    stats['price_lowered'] += 1
            log(f"  → dry-run 결과: {res.get('mode') or res.get('reason')}")
            return

        conn = self.get_connection()
        try:
            res = reconcile_runner.process_one_group(
                conn, model_no, brand_id, dry_run=False, scope='published',
                tag=f"[fast listing#{listing_id}] ")
        except Exception as e:
            log(f"{prefix} reconcile 오류: {e}", "ERROR")
            self._reschedule_error(model_key)
            with stats_lock:
                stats['error'] += 1
            return
        finally:
            conn.close()

        new_iv = self._reschedule(model_key, kept=kept, cur_interval_min=cur_iv)

        if res.get('skipped'):
            log(f"  → 스킵: {res.get('reason')} | 주기→{new_iv}분")
            with stats_lock:
                stats['skipped'] += 1
            return

        mode = res.get('mode')
        resp = res.get('response') if isinstance(res.get('response'), dict) else {}

        def _api_ok(r):
            if not r:
                return True
            if 'success' in r:
                return bool(r['success'])
            return True

        if mode == 'retire':
            if res.get('skipped'):
                log(f"  → 출품정지 스킵: {res.get('reason')}", "WARNING")
                with stats_lock:
                    stats['skipped'] += 1
            elif resp.get('success') is False:
                log(f"  → 출품정지 API 실패: {resp}", "ERROR")
                with stats_lock:
                    stats['api_failed'] += 1
            else:
                log(f"{prefix} [출품정지중] 마진 불가 / retire | 주기→{new_iv}분", "WARNING")
                with stats_lock:
                    stats['soldout'] += 1
                    stats['api_called'] += 1
                time.sleep(API_CALL_DELAY)
            return

        if mode == 'edit':
            if _api_ok(resp):
                c2 = self.get_connection()
                try:
                    fresh = push.fetch_listing(c2, listing_id)
                finally:
                    c2.close()
                new_price = (fresh or {}).get('price')
                log(f"  → EDIT 성공 ¥{current_price:,}→¥{new_price:,} | 주기→{new_iv}분")
                with stats_lock:
                    stats['price_lowered'] += 1
                    stats['api_called'] += 1
                time.sleep(API_CALL_DELAY)
            else:
                log(f"  → EDIT 실패: {resp}", "ERROR")
                with stats_lock:
                    stats['api_failed'] += 1
            return

        log(f"  → 기타 결과 mode={mode} reason={res.get('reason')} | 주기→{new_iv}분")
        with stats_lock:
            stats['skipped'] += 1

    # ── 실행 ─────────────────────────────────────────────
    def run(self, limit: int = None, brand: str = None, source: str = None,
            listing_id: int = None, dry_run: bool = False, count_only: bool = False) -> Dict:
        log("=" * 60)
        log("빠른 최저가 업데이트 시작 (listing + reconcile 공유)")
        log(f"  옵션: brand={brand}, source={source}, limit={limit}, "
            f"listing_id={listing_id}, dry_run={dry_run}")
        log(f"  병렬 {DEFAULT_WORKERS} · 레이트 {RATE_PER_SEC}/초 · 배치 {BATCH_SIZE} · "
            f"베이스라인 {MAX_INTERVAL_MIN}분 · 최소 {MIN_INTERVAL_MIN}분")
        log("=" * 60)

        # 필터/--limit/--id 있으면 1회 배치. 인자 없을 때만 무한 스케줄러.
        one_shot = (listing_id is not None or brand is not None
                    or source is not None or limit is not None)
        # 수동 타깃(id/brand/source)은 스케줄 무시. --limit 만 있으면 due 만.
        respect_schedule = (listing_id is None and brand is None and source is None)

        if count_only:
            rows = self.get_listings(limit=limit, brand=brand, source=source,
                                    listing_id=listing_id, respect_schedule=respect_schedule)
            sites: Dict[str, int] = {}
            for r in rows:
                s = r.get('winner_source') or '(winner없음)'
                sites[s] = sites.get(s, 0) + 1
            log(f"대상 listing: {len(rows)}건")
            for s, n in sorted(sites.items(), key=lambda x: -x[1]):
                log(f"  {s}: {n}건")
            return {'total': len(rows)}

        if dry_run:
            log("*** DRY-RUN — ensure_group/푸시 쓰기 없음 ***", "WARNING")

        if one_shot:
            rows = self.get_listings(limit=limit, brand=brand, source=source,
                                    listing_id=listing_id,
                                    respect_schedule=respect_schedule)
            log(f"대상 listing: {len(rows)}건")
            if not rows:
                log("대상 없음")
                return {'total': 0}
            stats = self._process_rows(rows, dry_run)
            log("=" * 60)
            log(f"완료: 인하/조정 {stats['price_lowered']} · 유지 {stats['already_lowest']} · "
                f"출품정지 {stats['soldout']} · 오류 {stats['error']} (총 {stats['total']})")
            log("=" * 60)
            return stats

        log("스케줄러 모드: 주기 짧은 순 우선 (Ctrl+C 중단)")
        cum = {'processed': 0, 'price_lowered': 0, 'already_lowest': 0,
               'soldout': 0, 'error': 0}
        batch_no = 0
        while True:
            rows = self.get_listings(limit=BATCH_SIZE, respect_schedule=True)
            if not rows:
                log(f"due 0건 → {IDLE_SLEEP_SEC}초 대기")
                time.sleep(IDLE_SLEEP_SEC)
                continue
            batch_no += 1
            stats = self._process_rows(rows, dry_run)
            for k in ('price_lowered', 'already_lowest', 'soldout', 'error'):
                cum[k] += stats[k]
            cum['processed'] += stats['total']
            iv_min = rows[0].get('check_interval_min')
            iv_max = rows[-1].get('check_interval_min')
            log(f"[배치#{batch_no}] {stats['total']}건(주기 {iv_min}~{iv_max}분) | "
                f"인하 {stats['price_lowered']} 유지 {stats['already_lowest']} "
                f"출품정지 {stats['soldout']} 오류 {stats['error']} | "
                f"누적 처리 {cum['processed']} · 인하 {cum['price_lowered']}")

    def _process_rows(self, rows: List[Dict], dry_run: bool) -> Dict:
        stats = {
            'total': len(rows), 'already_lowest': 0, 'price_lowered': 0,
            'soldout': 0, 'to_soldout': 0, 'skipped': 0, 'error': 0,
            'api_called': 0, 'api_failed': 0,
        }
        stats_lock = threading.Lock()
        crawl_cache: Dict = {}
        cache_lock = threading.Lock()
        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as ex:
            futs = [
                ex.submit(self.process_one, row, dry_run, stats, stats_lock,
                          crawl_cache, cache_lock)
                for row in rows
            ]
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception as e:
                    log(f"워커 오류: {e}", "ERROR")
                    with stats_lock:
                        stats['error'] += 1
        return stats


def main():
    parser = argparse.ArgumentParser(description='빠른 최저가 업데이트 (listing + reconcile)')
    parser.add_argument('--count', action='store_true', help='대상 건수만')
    parser.add_argument('--dry-run', action='store_true', help='쓰기·푸시 없이 판단만')
    parser.add_argument('--brand', type=str, default=None, help='브랜드 필터 (스케줄 무시 1회)')
    parser.add_argument('--source', type=str, default=None, help='winner 소싱처 필터 (스케줄 무시 1회)')
    parser.add_argument('--limit', type=int, default=None, help='최대 건수')
    parser.add_argument('--id', type=int, default=None, help='buyma_listings.id')
    args = parser.parse_args()

    FastPriceUpdater().run(
        limit=args.limit,
        brand=args.brand,
        source=args.source,
        listing_id=args.id,
        dry_run=args.dry_run,
        count_only=args.count,
    )


if __name__ == '__main__':
    main()
