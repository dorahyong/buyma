# -*- coding: utf-8 -*-
"""products_api.build_payload 결과를 메모리에 캐시.

백그라운드 스레드가 부팅 직후 + REFRESH_INTERVAL초마다 갱신.
캐시 빌드 시점에 JSON bytes + gzip bytes 까지 미리 만들어 두므로
매 요청은 직렬화/압축 비용 없이 그대로 송신한다.
"""

import gzip
import json
import threading
import time
import traceback
from typing import Dict, Optional, Tuple

from products_api import build_payload

REFRESH_INTERVAL = 300  # 5분

_CACHE: Dict[str, Optional[bytes]] = {
    'json': None,
    'gzip': None,
    'built_at': None,
}
_LOCK = threading.Lock()


def _refresh_loop(db_cfg: Dict) -> None:
    while True:
        try:
            t0 = time.perf_counter()
            print("[cache] build start", flush=True)
            payload = build_payload(db_cfg)
            t1 = time.perf_counter()
            json_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
            t2 = time.perf_counter()
            gz_bytes = gzip.compress(json_bytes, compresslevel=6)
            t3 = time.perf_counter()
            with _LOCK:
                _CACHE['json'] = json_bytes
                _CACHE['gzip'] = gz_bytes
                _CACHE['built_at'] = time.time()
            print(
                f"[cache] build done: total={t3-t0:.1f}s "
                f"(query={t1-t0:.1f}s, json={t2-t1:.1f}s, gzip={t3-t2:.1f}s), "
                f"raw={len(json_bytes)/1e6:.1f}MB, gzip={len(gz_bytes)/1e6:.1f}MB, "
                f"items={payload.get('count')}",
                flush=True,
            )
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


def get() -> Tuple[Optional[bytes], Optional[bytes]]:
    """(json_bytes, gzip_bytes) 튜플 반환. 워밍업 전이면 (None, None)."""
    with _LOCK:
        return _CACHE['json'], _CACHE['gzip']
