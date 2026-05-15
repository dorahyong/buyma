# -*- coding: utf-8 -*-
"""mall_categories 검수 페이지용 데이터 액세스 + buyma categories 검색.

- get_mall_names(db_cfg)              : mall_categories에 등장하는 mall_name distinct
- get_categories(db_cfg, **filters)   : 필터링된 mall_categories 행 조회
- apply_updates(db_cfg, changes)      : 변경 사항 일괄 적용 (PK = id)
- search_buyma_categories(q, limit)   : buyma_master_data/categories.csv 부분일치 검색

PK = id — readonly.
created_at도 readonly (DB가 자동 관리).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Dict, Optional, Any

import pymysql


ROOT = Path(__file__).resolve().parent.parent
CATEGORIES_CSV = ROOT / 'buyma_master_data' / 'categories.csv'

EDITABLE_COLUMNS = {
    'buyma_category_id',
    'is_active',
}

SELECT_COLUMNS = [
    'id', 'mall_name',
    'gender', 'depth1', 'depth2', 'depth3', 'depth4',
    'full_path', 'mall_category_url',
    'buyma_category_id', 'is_active',
    'created_at',
]


# categories.csv 메모리 캐시 (앱 부팅 시 1회 로드)
_BUYMA_CATEGORIES_CACHE: Optional[List[Dict[str, Any]]] = None


def _connect(db_cfg: Dict[str, Any]):
    cfg = {k: v for k, v in db_cfg.items() if k != 'cursorclass'}
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **cfg)


def _load_buyma_categories() -> List[Dict[str, Any]]:
    """categories.csv 로드 후 캐시."""
    global _BUYMA_CATEGORIES_CACHE
    if _BUYMA_CATEGORIES_CACHE is not None:
        return _BUYMA_CATEGORIES_CACHE
    rows: List[Dict[str, Any]] = []
    if not CATEGORIES_CSV.exists():
        _BUYMA_CATEGORIES_CACHE = rows
        return rows
    with open(CATEGORIES_CSV, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                rows.append({
                    'id': int(r['id']),
                    'paths': r.get('paths') or '',
                    'name': r.get('name') or '',
                    'limited': (r.get('limited') or '').strip().lower() == 'true',
                })
            except (KeyError, ValueError):
                continue
    _BUYMA_CATEGORIES_CACHE = rows
    return rows


def get_mall_names(db_cfg: Dict[str, Any]) -> List[str]:
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT mall_name FROM mall_categories ORDER BY mall_name")
            return [r['mall_name'] for r in cur.fetchall() if r['mall_name']]


def get_categories(
    db_cfg: Dict[str, Any],
    mall_name: Optional[str] = None,
    is_active: Optional[int] = None,
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
    if unmapped_only:
        where.append("(buyma_category_id IS NULL OR buyma_category_id = 0)")
    if search:
        like = f"%{search}%"
        where.append(
            "(full_path LIKE %s OR depth1 LIKE %s OR depth2 LIKE %s "
            " OR depth3 LIKE %s OR depth4 LIKE %s)"
        )
        params.extend([like, like, like, like, like])
    where_sql = ' AND '.join(where) if where else '1'

    cols_sql = ', '.join(f'`{c}`' for c in SELECT_COLUMNS)
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {cols_sql} FROM mall_categories "
                f"WHERE {where_sql} "
                f"ORDER BY mall_name, full_path LIMIT %s",
                (*params, int(limit)),
            )
            rows = cur.fetchall()
    # datetime → isoformat (JSON 직렬화)
    for r in rows:
        for k in ('created_at',):
            v = r.get(k)
            if v is not None and hasattr(v, 'isoformat'):
                r[k] = v.isoformat(sep=' ', timespec='seconds')
    return rows


def apply_updates(db_cfg: Dict[str, Any], changes: List[Dict[str, Any]]) -> int:
    """changes = [{pk:{id}, fields:{col:val, ...}}, ...]
    Returns: 영향받은 행 수 합계.
    """
    updated = 0
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                for ch in changes:
                    pk = ch.get('pk') or {}
                    row_id = pk.get('id')
                    fields_raw = ch.get('fields') or {}
                    fields = {k: v for k, v in fields_raw.items() if k in EDITABLE_COLUMNS}
                    if row_id is None or not fields:
                        continue
                    # 빈 문자열은 NULL로 정규화
                    norm: Dict[str, Any] = {}
                    for k, v in fields.items():
                        if isinstance(v, str) and v.strip() == '':
                            norm[k] = None
                        else:
                            norm[k] = v
                    set_clause = ', '.join(f"`{k}` = %s" for k in norm.keys())
                    params = list(norm.values()) + [int(row_id)]
                    cur.execute(
                        f"UPDATE mall_categories SET {set_clause} WHERE id = %s",
                        params,
                    )
                    updated += cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return updated


def search_buyma_categories(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """categories.csv 부분일치(대소문자 무시). id 정확 일치 우선."""
    if not query:
        return []
    q = query.strip()
    if not q:
        return []
    rows = _load_buyma_categories()
    ql = q.lower()
    matched: List[Dict[str, Any]] = []
    # id 정확 일치 우선
    if q.isdigit():
        qid = int(q)
        for r in rows:
            if r['id'] == qid:
                matched.append(r)
                break
    # paths + name 부분일치
    for r in rows:
        if r in matched:
            continue
        if ql in r['paths'].lower() or ql in r['name'].lower():
            matched.append(r)
            if len(matched) >= limit:
                break
    return matched[:limit]
