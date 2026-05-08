# -*- coding: utf-8 -*-
"""
바이마 셀러 상품의 "최근 7일 조회수" 수집기

대상 API: GET https://www.buyma.com/rorapi/sell/products/{buyma_product_id}?_={ts}
응답 필드: data.access.last_7days

상품 1개당 1번 호출이 필요해서 양이 많음. 동시 요청으로 단축:
  출품중 약 45,000건 × 0.5s/req ≈ 6.3시간 (단일)
                       ÷ 동시 5     ≈ 1.3시간

대상 ID 소스: buyma_self_stats_latest.json (= 셀러 페이지에 실제로 떠 있는 상품)

출력: buyma_self_7days_latest.json
  { collected_at, count, items: [{ buyma_product_id, last_7days }] }

로그인 쿠키는 buyma_cleaners/buyma_cookies.json 공유.

사용법:
    python3 buyma_self_7days_collector.py                  # 전체 (오래 걸림)
    python3 buyma_self_7days_collector.py --limit 100      # 테스트
    python3 buyma_self_7days_collector.py --workers 5      # 동시 워커 수 (기본 5)
    python3 buyma_self_7days_collector.py --resume         # 기존 latest.json에 이어서 모자란 것만
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests as req_lib

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BUYMA_BASE_URL = "https://www.buyma.com"
API_URL_TEMPLATE = "{base}/rorapi/sell/products/{{pid}}?_={{ts}}".format(base=BUYMA_BASE_URL)

COOKIE_FILE = os.path.normpath(
    os.path.join(SCRIPT_DIR, '..', 'buyma_cleaners', 'buyma_cookies.json')
)
SELLER_STATS_LATEST = os.path.join(SCRIPT_DIR, 'buyma_self_stats_latest.json')
OUT_LATEST = os.path.join(SCRIPT_DIR, 'buyma_self_7days_latest.json')
OUT_LATEST_JS = os.path.join(SCRIPT_DIR, 'buyma_self_7days_latest.js')

PER_REQUEST_SLEEP = 0.3   # 워커당 호출 간 sleep (초)
REQUEST_TIMEOUT = 15


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def make_session() -> req_lib.Session:
    if not os.path.exists(COOKIE_FILE):
        log(f"쿠키 파일 없음: {COOKIE_FILE}", "ERROR")
        log("buyma_cleaners 쪽에서 먼저 로그인:")
        log("  cd ../buyma_cleaners && python3 buyma_orphan_cleaner.py --login")
        sys.exit(1)
    with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    s = req_lib.Session()
    s.cookies.update({c['name']: c['value'] for c in cookies})
    s.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36'
        ),
        'Accept': 'application/json, text/plain, */*',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept-Language': 'ja,en;q=0.9',
    })
    return s


def fetch_one(session: req_lib.Session, pid: str) -> Optional[Dict]:
    """단일 상품의 last_7days 조회"""
    ts = int(time.time() * 1000)
    url = API_URL_TEMPLATE.format(pid=pid, ts=ts)
    try:
        resp = session.get(
            url,
            headers={'Referer': f'{BUYMA_BASE_URL}/my/sell/{pid}/edit?tab=b'},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return {'buyma_product_id': pid, 'last_7days': None, 'error': f'HTTP {resp.status_code}'}
        data = resp.json()
        access = (data.get('data') or {}).get('access') or {}
        return {
            'buyma_product_id': pid,
            'last_7days': access.get('last_7days'),
        }
    except Exception as e:
        return {'buyma_product_id': pid, 'last_7days': None, 'error': str(e)[:80]}


def load_target_pids() -> List[str]:
    """셀러 통계 latest에서 buyma_product_id 리스트 추출"""
    if not os.path.exists(SELLER_STATS_LATEST):
        log(f"셀러 통계 파일 없음: {SELLER_STATS_LATEST}", "ERROR")
        log("먼저 buyma_self_stats_collector.py 를 실행해주세요.")
        sys.exit(1)
    with open(SELLER_STATS_LATEST, 'r', encoding='utf-8') as f:
        data = json.load(f)
    pids = []
    seen = set()
    for it in data.get('items', []):
        pid = str(it.get('buyma_product_id') or '').strip()
        if pid and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    log(f"대상 buyma_product_id: {len(pids)}개 (셀러 통계 기준)")
    return pids


def load_existing_results() -> Dict[str, Dict]:
    """resume용: 기존 결과 읽기"""
    if not os.path.exists(OUT_LATEST):
        return {}
    with open(OUT_LATEST, 'r', encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for it in data.get('items', []):
        pid = str(it.get('buyma_product_id') or '')
        if pid and it.get('last_7days') is not None:
            out[pid] = it
    return out


def save_results(items_by_pid: Dict[str, Dict]) -> None:
    items = list(items_by_pid.values())
    payload = {
        'collected_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(items),
        'items': items,
    }
    with open(OUT_LATEST, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(OUT_LATEST_JS, 'w', encoding='utf-8') as f:
        f.write('window.SEVEN_DAYS_DATA = ')
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write(';\n')


def main():
    p = argparse.ArgumentParser(description='바이마 last_7days 수집')
    p.add_argument('--limit', type=int, default=None, help='상품 N개만 처리 (테스트)')
    p.add_argument('--workers', type=int, default=5, help='동시 워커 수 (기본 5)')
    p.add_argument('--resume', action='store_true', help='기존 결과 유지하고 누락분만')
    p.add_argument('--save-every', type=int, default=2000, help='N건마다 중간 저장')
    args = p.parse_args()

    log("=" * 60)
    log("바이마 last_7days 수집 시작")
    log(f"  workers={args.workers}, limit={args.limit}, resume={args.resume}")
    log("=" * 60)

    session = make_session()
    pids = load_target_pids()
    if args.limit:
        pids = pids[:args.limit]

    existing = load_existing_results() if args.resume else {}
    if args.resume:
        log(f"기존 결과: {len(existing)}건. 누락분만 처리.")
        pids = [p for p in pids if p not in existing]
        log(f"이번 처리 대상: {len(pids)}건")

    if not pids:
        log("처리할 항목 없음")
        return

    items_by_pid: Dict[str, Dict] = dict(existing)
    done = 0
    err = 0
    started = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = []
        for pid in pids:
            futures.append(ex.submit(_worker, session, pid))

        for fut in as_completed(futures):
            res = fut.result()
            if res:
                items_by_pid[res['buyma_product_id']] = res
                if res.get('error'):
                    err += 1
            done += 1

            if done % 200 == 0 or done == len(pids):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(pids) - done) / rate if rate > 0 else 0
                log(f"  진행 {done:>6,}/{len(pids):,} "
                    f"(에러 {err}) · {rate:.1f} req/s · 예상 남은 {eta/60:.1f}분")

            if done % args.save_every == 0:
                save_results(items_by_pid)
                log(f"  중간 저장: {len(items_by_pid):,}건")

    save_results(items_by_pid)

    log("=" * 60)
    log(f"완료: 누적 {len(items_by_pid):,}건 (이번 처리 {done:,}건, 에러 {err}건)")
    log(f"  → {OUT_LATEST}")
    log("=" * 60)


def _worker(session: req_lib.Session, pid: str) -> Optional[Dict]:
    res = fetch_one(session, pid)
    time.sleep(PER_REQUEST_SLEEP)
    return res


if __name__ == '__main__':
    main()