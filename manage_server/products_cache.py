# -*- coding: utf-8 -*-
"""products_api.build_payload 결과를 메모리에 캐시.

백그라운드 스레드가 부팅 직후 + REFRESH_INTERVAL초마다 갱신.
다중 worker 환경에서는 worker별 별도 캐시 유지.
"""

import threading
import time
import traceback
from typing import Dict, Optional

from products_api import build_payload

REFRESH_INTERVAL = 300  # 5분

_CACHE: Dict[str, object] = {
    'payload': None,
    'built_at': 0.0,
}
_LOCK = threading.Lock()


def _refresh_loop(db_cfg: Dict) -> None:
    while True:
        try:
            t = time.perf_counter()
            print("[cache] build start", flush=True)
            payload = build_payload(db_cfg)
            with _LOCK:
                _CACHE['payload'] = payload
                _CACHE['built_at'] = time.time()
            elapsed = time.perf_counter() - t
            print(f"[cache] build done in {elapsed:.1f}s, items={payload.get('count')}", flush=True)
        except Exception:
            print("[cache] build failed:", flush=True)
            traceback.print_exc()
        time.sleep(REFRESH_INTERVAL)


def start(db_cfg: Dict) -> None:
    """앱 부팅 시 1회 호출. 백그라운드 스레드 시작."""
    th = threading.Thread(
        target=_refresh_loop,
        args=(db_cfg,),
        daemon=True,
        name='products-cache-refresher',
    )
    th.start()


def get() -> Optional[Dict]:
    with _LOCK:
        return _CACHE['payload']
