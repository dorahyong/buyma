# -*- coding: utf-8 -*-
"""products2_api.build_payload(=buyma_listings 기준) 결과 메모리 캐시.
products_cache.py 와 동일 구조, build_payload 출처만 products2_api."""

import gzip
import json
import threading
import time
import traceback
from typing import Dict, Optional, Tuple

from products2_api import build_payload

REFRESH_INTERVAL = 300  # 5분

_CACHE: Dict[str, Optional[bytes]] = {'json': None, 'gzip': None, 'built_at': None}
_LOCK = threading.Lock()


def _refresh_loop(db_cfg: Dict) -> None:
    while True:
        try:
            t0 = time.perf_counter()
            print("[cache2] build start", flush=True)
            payload = build_payload(db_cfg)
            json_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
            gz_bytes = gzip.compress(json_bytes, compresslevel=6)
            with _LOCK:
                _CACHE['json'] = json_bytes
                _CACHE['gzip'] = gz_bytes
                _CACHE['built_at'] = time.time()
            print(f"[cache2] build done: {time.perf_counter()-t0:.1f}s, items={payload.get('count')}", flush=True)
        except Exception:
            print("[cache2] build failed:", flush=True)
            traceback.print_exc()
        time.sleep(REFRESH_INTERVAL)


def start(db_cfg: Dict) -> None:
    th = threading.Thread(target=_refresh_loop, args=(db_cfg,), daemon=True, name='products2-cache-refresher')
    th.start()


def get() -> Tuple[Optional[bytes], Optional[bytes]]:
    with _LOCK:
        return _CACHE['json'], _CACHE['gzip']
