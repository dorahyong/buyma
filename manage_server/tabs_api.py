# -*- coding: utf-8 -*-
"""필터 탭 저장용 API.

product_filter_tabs 테이블:
    id          VARCHAR(40)  PK         서버 발급 ('t' + epoch_ms)
    name        VARCHAR(80)
    filter_json JSON
    created_at, updated_at

저장 단위 = "탭 정의(이름 + filter 구조)". 상품 데이터와 무관.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pymysql


NAME_MAX_LEN = 80

# filter-tabs.js 의 OPS 와 동일하게 유지
_VALID_OPS = {
    'lt', 'lte', 'eq', 'gte', 'gt',
    'before', 'after', 'within', 'between',
    'isnull', 'notnull',
    'contains', 'ncontains', 'neq',
    'true', 'false',
}

# filter-tabs.js 의 FIELDS 와 동일하게 유지
_VALID_FIELDS = {
    'access_count', 'access_avg', 'access_7d',
    'cart_count', 'cart_avg',
    'favorite_count', 'fav_avg',
    'rank_position',
    'margin_amount_krw', 'margin_rate', 'price_yen',
    'buyma_lowest_price', 'available_lowest_price_jpy',
    'reg_days', 'registered_at', 'expire_at',
    'price_updated_at', 'source_updated_at',
    'source_count', 'same_count', 'top1_is_ours',
    'status', 'brand_name_en',
    'name_ko', 'name_ja', 'model_id',
    'db_mismatch_reason',
}


def _connect(db_cfg: Dict[str, Any]):
    cfg = {k: v for k, v in db_cfg.items() if k != 'cursorclass'}
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **cfg)


class ValidationError(ValueError):
    """검증 실패."""


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValidationError('name must be a string')
    s = name.strip()
    if not s:
        raise ValidationError('name is required')
    if len(s) > NAME_MAX_LEN:
        raise ValidationError(f'name too long (max {NAME_MAX_LEN})')
    return s


def _validate_filter(filter_obj: Any) -> Dict[str, Any]:
    if not isinstance(filter_obj, dict):
        raise ValidationError('filter must be an object')
    groups = filter_obj.get('groups')
    if not isinstance(groups, list) or not groups:
        raise ValidationError('filter.groups must be a non-empty list')
    norm_groups: List[Dict[str, Any]] = []
    for gi, g in enumerate(groups):
        if not isinstance(g, dict):
            raise ValidationError(f'group[{gi}] must be an object')
        conds = g.get('conditions')
        if not isinstance(conds, list) or not conds:
            raise ValidationError(f'group[{gi}].conditions must be a non-empty list')
        norm_conds: List[Dict[str, Any]] = []
        for ci, c in enumerate(conds):
            if not isinstance(c, dict):
                raise ValidationError(f'group[{gi}].conditions[{ci}] must be an object')
            field = c.get('field')
            op = c.get('op')
            if field not in _VALID_FIELDS:
                raise ValidationError(f'unknown field: {field!r}')
            if op not in _VALID_OPS:
                raise ValidationError(f'unknown op: {op!r}')
            nc: Dict[str, Any] = {'field': field, 'op': op}
            if 'value' in c:
                nc['value'] = c.get('value')
            if 'value2' in c:
                nc['value2'] = c.get('value2')
            norm_conds.append(nc)
        norm_groups.append({'conditions': norm_conds})
    return {'groups': norm_groups}


def _row_to_tab(r: Dict[str, Any]) -> Dict[str, Any]:
    filt = r.get('filter_json')
    if isinstance(filt, (bytes, bytearray)):
        filt = filt.decode('utf-8')
    if isinstance(filt, str):
        try:
            filt = json.loads(filt)
        except (TypeError, ValueError):
            filt = {'groups': []}
    out = {
        'id': r['id'],
        'name': r['name'],
        'filter': filt,
    }
    for k in ('created_at', 'updated_at'):
        v = r.get(k)
        if v is not None and hasattr(v, 'isoformat'):
            out[k] = v.isoformat(sep=' ', timespec='seconds')
    return out


def list_tabs(db_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, filter_json, created_at, updated_at "
                "FROM product_filter_tabs ORDER BY created_at, id"
            )
            rows = cur.fetchall()
    return [_row_to_tab(r) for r in rows]


def create_tab(db_cfg: Dict[str, Any], name: Any, filter_obj: Any) -> Dict[str, Any]:
    nm = _validate_name(name)
    fl = _validate_filter(filter_obj)
    tab_id = f"t{int(time.time() * 1000)}"
    payload = json.dumps(fl, ensure_ascii=False)
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO product_filter_tabs (id, name, filter_json) "
                    "VALUES (%s, %s, %s)",
                    (tab_id, nm, payload),
                )
                cur.execute(
                    "SELECT id, name, filter_json, created_at, updated_at "
                    "FROM product_filter_tabs WHERE id = %s",
                    (tab_id,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _row_to_tab(row) if row else {'id': tab_id, 'name': nm, 'filter': fl}


def update_tab(db_cfg: Dict[str, Any], tab_id: str,
               name: Any, filter_obj: Any) -> Optional[Dict[str, Any]]:
    nm = _validate_name(name)
    fl = _validate_filter(filter_obj)
    payload = json.dumps(fl, ensure_ascii=False)
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE product_filter_tabs "
                    "SET name = %s, filter_json = %s WHERE id = %s",
                    (nm, payload, tab_id),
                )
                cur.execute(
                    "SELECT id, name, filter_json, created_at, updated_at "
                    "FROM product_filter_tabs WHERE id = %s",
                    (tab_id,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _row_to_tab(row) if row else None


def delete_tab(db_cfg: Dict[str, Any], tab_id: str) -> bool:
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM product_filter_tabs WHERE id = %s",
                    (tab_id,),
                )
                affected = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return affected > 0
