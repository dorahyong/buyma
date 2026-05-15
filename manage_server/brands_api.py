# -*- coding: utf-8 -*-
"""mall_brands 검수 페이지용 데이터 액세스 + buyma brands 검색.

- get_mall_names(db_cfg)         : mall_brands에 등장하는 mall_name distinct
- get_brands(db_cfg, **filters)  : 필터링된 mall_brands 행 조회
- apply_updates(db_cfg, changes) : 변경 사항 일괄 적용 (PK = mall_name + raw_brand_name)
- search_buyma_brands(q, limit)  : buyma_master_data/brands.csv 부분일치 검색

PK = (mall_name, raw_brand_name) — 두 컬럼은 readonly.
created_at, updated_at도 readonly (DB가 자동 관리).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Dict, Optional, Any

import pymysql


ROOT = Path(__file__).resolve().parent.parent
BRANDS_CSV = ROOT / 'buyma_master_data' / 'brands.csv'

EDITABLE_COLUMNS = {
    'mall_brand_name_en',
    'buyma_brand_id',
    'buyma_brand_name',
    'mapping_level',
    'is_mapped',
    'mall_brand_url',
    'is_active',
    'mall_brand_no',
}

SELECT_COLUMNS = [
    'mall_name', 'raw_brand_name',
    'mall_brand_name_en',
    'buyma_brand_id', 'buyma_brand_name',
    'mapping_level', 'is_mapped',
    'mall_brand_url', 'is_active', 'mall_brand_no',
    'created_at', 'updated_at',
]


# brands.csv 메모리 캐시 (앱 부팅 시 1회 로드)
_BUYMA_BRANDS_CACHE: Optional[List[Dict[str, Any]]] = None


def _connect(db_cfg: Dict[str, Any]):
    cfg = {k: v for k, v in db_cfg.items() if k != 'cursorclass'}
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **cfg)


def _load_buyma_brands() -> List[Dict[str, Any]]:
    """brands.csv 로드 후 캐시."""
    global _BUYMA_BRANDS_CACHE
    if _BUYMA_BRANDS_CACHE is not None:
        return _BUYMA_BRANDS_CACHE
    rows: List[Dict[str, Any]] = []
    if not BRANDS_CSV.exists():
        _BUYMA_BRANDS_CACHE = rows
        return rows
    with open(BRANDS_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    'id': int(r['id']),
                    'brand_name': r['brand_name'],
                    'limited': (r.get('limited') or '').strip().lower() == 'true',
                })
            except (KeyError, ValueError):
                continue
    _BUYMA_BRANDS_CACHE = rows
    return rows


def get_mall_names(db_cfg: Dict[str, Any]) -> List[str]:
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT mall_name FROM mall_brands ORDER BY mall_name")
            return [r['mall_name'] for r in cur.fetchall() if r['mall_name']]


def get_brands(
    db_cfg: Dict[str, Any],
    mall_name: Optional[str] = None,
    is_active: Optional[int] = None,
    is_mapped: Optional[int] = None,
    unmapped_only: bool = False,
    search: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    where, params = [], []
    if mall_name:
        where.append("mall_name = %s")
        params.append(mall_name)
    if is_active is not None:
        where.append("is_active = %s")
        params.append(int(is_active))
    if is_mapped is not None:
        where.append("is_mapped = %s")
        params.append(int(is_mapped))
    if unmapped_only:
        where.append("(buyma_brand_id IS NULL OR buyma_brand_id = 0)")
    if search:
        like = f"%{search}%"
        where.append(
            "(raw_brand_name LIKE %s OR mall_brand_name_en LIKE %s "
            " OR buyma_brand_name LIKE %s)"
        )
        params.extend([like, like, like])
    where_sql = ' AND '.join(where) if where else '1'

    cols_sql = ', '.join(f'`{c}`' for c in SELECT_COLUMNS)
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {cols_sql} FROM mall_brands "
                f"WHERE {where_sql} "
                f"ORDER BY mall_name, raw_brand_name LIMIT %s",
                (*params, int(limit)),
            )
            rows = cur.fetchall()
    # datetime → isoformat (JSON 직렬화)
    for r in rows:
        for k in ('created_at', 'updated_at'):
            v = r.get(k)
            if v is not None and hasattr(v, 'isoformat'):
                r[k] = v.isoformat(sep=' ', timespec='seconds')
    return rows


def apply_updates(db_cfg: Dict[str, Any], changes: List[Dict[str, Any]]) -> int:
    """changes = [{pk:{mall_name, raw_brand_name}, fields:{col:val, ...}}, ...]
    Returns: 영향받은 행 수 합계.
    """
    updated = 0
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                for ch in changes:
                    pk = ch.get('pk') or {}
                    mall_name = pk.get('mall_name')
                    raw_brand_name = pk.get('raw_brand_name')
                    fields_raw = ch.get('fields') or {}
                    fields = {k: v for k, v in fields_raw.items() if k in EDITABLE_COLUMNS}
                    if not mall_name or raw_brand_name is None or not fields:
                        continue
                    # 빈 문자열은 NULL로 정규화 (numeric/optional 컬럼)
                    norm: Dict[str, Any] = {}
                    for k, v in fields.items():
                        if isinstance(v, str) and v.strip() == '':
                            norm[k] = None
                        else:
                            norm[k] = v
                    set_clause = ', '.join(f"`{k}` = %s" for k in norm.keys())
                    params = list(norm.values()) + [mall_name, raw_brand_name]
                    cur.execute(
                        f"UPDATE mall_brands SET {set_clause} "
                        f"WHERE mall_name = %s AND raw_brand_name = %s",
                        params,
                    )
                    updated += cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return updated


def search_buyma_brands(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """brands.csv 부분일치(대소문자 무시). id 일치도 추가 매칭."""
    if not query:
        return []
    q = query.strip()
    if not q:
        return []
    rows = _load_buyma_brands()
    ql = q.lower()
    matched: List[Dict[str, Any]] = []
    # id 정확 일치 우선
    if q.isdigit():
        qid = int(q)
        for r in rows:
            if r['id'] == qid:
                matched.append(r)
                break
    # brand_name 부분일치
    for r in rows:
        if r in matched:
            continue
        if ql in r['brand_name'].lower():
            matched.append(r)
            if len(matched) >= limit:
                break
    return matched[:limit]
