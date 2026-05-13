# -*- coding: utf-8 -*-
"""
data_cache.json 캐시 파일 생성 스크립트.

  - 크롤러(buyma_self_stats_collector.py)가 끝나면 자동 호출됨.
  - 필요시 수동 호출:
        cd manage_server && python3 build_cache.py
  - 매일 새벽 cron에서도 호출 가능 (크롤 → 자동 호출이긴 하나 별도 트리거용).

DB → ace_products + raw_scraped_data + ace_product_images + buyma_product_stats
머지 → data_cache.json 파일에 한 번에 저장. API는 그 파일을 응답.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE.parent / '.env')

# 같은 폴더의 products_api.py 직접 임포트
sys.path.insert(0, str(BASE))
from products_api import CACHE_PATH, build_and_save_cache   # noqa: E402

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> None:
    log("DB → 머지 → 캐시 파일 생성 시작")
    payload = build_and_save_cache(DB_CONFIG)
    log(f"완료: {payload['count']}건 → {CACHE_PATH}")


if __name__ == '__main__':
    main()
