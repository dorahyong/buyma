# -*- coding: utf-8 -*-
"""현업 문의 관리용 API.

field_inquiries 테이블:
    id               INT AUTO_INCREMENT PK
    buyma_product_id VARCHAR(30)   NULL
    buyma_url        VARCHAR(500)  NULL
    product_url      VARCHAR(500)  NULL   소싱처 상품 URL
    mall             VARCHAR(60)   NULL
    brand            VARCHAR(120)  NULL
    author           VARCHAR(60)   NULL       작성자
    content          TEXT          NOT NULL   문의내용(유일한 필수값)
    status           ENUM          '확인'/'처리중'/'완료' (기본 '확인')
    responder        VARCHAR(60)   NULL       답변자
    resolution       TEXT          NULL       처리내용
    created_at, updated_at

행 단위로만 관리 — 상세페이지 없음. 추가/삭제 + 처리상태·처리내용 수정만.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pymysql


CONTENT_MAX_LEN = 5000
STR_MAX_LEN = 500

VALID_STATUS = ('확인', '처리중', '완료')
DEFAULT_STATUS = '확인'

# create 시 받을 수 있는 컬럼 (content 만 필수)
CREATE_COLUMNS = (
    'buyma_product_id', 'buyma_url', 'product_url', 'mall', 'brand', 'author',
    'content', 'status', 'responder', 'resolution',
)
# update(부분수정) 허용 컬럼
EDITABLE_COLUMNS = {
    'buyma_product_id', 'buyma_url', 'product_url', 'mall', 'brand', 'author',
    'content', 'status', 'responder', 'resolution',
}


def _connect(db_cfg: Dict[str, Any]):
    cfg = {k: v for k, v in db_cfg.items() if k != 'cursorclass'}
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, **cfg)


class ValidationError(ValueError):
    """검증 실패."""


def _clean_str(v: Any, maxlen: int) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if len(s) > maxlen:
        s = s[:maxlen]
    return s


def _clean_content(v: Any) -> str:
    if not isinstance(v, str):
        if v is None:
            raise ValidationError('content is required')
        v = str(v)
    s = v.strip()
    if not s:
        raise ValidationError('content is required')
    if len(s) > CONTENT_MAX_LEN:
        s = s[:CONTENT_MAX_LEN]
    return s


def _clean_status(v: Any) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return DEFAULT_STATUS
    s = str(v).strip()
    if s not in VALID_STATUS:
        raise ValidationError(f'status must be one of {VALID_STATUS}, got {s!r}')
    return s


def _row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k in ('created_at', 'updated_at'):
        v = r.get(k)
        if v is not None and hasattr(v, 'isoformat'):
            out[k] = v.isoformat(sep=' ', timespec='seconds')
    return out


def list_inquiries(db_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    with _connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, buyma_product_id, buyma_url, product_url, mall, brand, author, "
                "content, status, responder, resolution, created_at, updated_at "
                "FROM field_inquiries ORDER BY id DESC"
            )
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def create_inquiry(db_cfg: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError('payload must be an object')

    values = {
        'buyma_product_id': _clean_str(payload.get('buyma_product_id'), 30),
        'buyma_url': _clean_str(payload.get('buyma_url'), STR_MAX_LEN),
        'product_url': _clean_str(payload.get('product_url'), STR_MAX_LEN),
        'mall': _clean_str(payload.get('mall'), 60),
        'brand': _clean_str(payload.get('brand'), 120),
        'author': _clean_str(payload.get('author'), 60),
        'content': _clean_content(payload.get('content')),
        'status': _clean_status(payload.get('status')),
        'responder': _clean_str(payload.get('responder'), 60),
        'resolution': _clean_str(payload.get('resolution'), CONTENT_MAX_LEN),
    }

    cols = list(values.keys())
    placeholders = ', '.join(['%s'] * len(cols))
    col_sql = ', '.join(f'`{c}`' for c in cols)

    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO field_inquiries ({col_sql}) VALUES ({placeholders})",
                    [values[c] for c in cols],
                )
                new_id = cur.lastrowid
                cur.execute(
                    "SELECT id, buyma_product_id, buyma_url, product_url, mall, brand, author, "
                    "content, status, responder, resolution, created_at, updated_at "
                    "FROM field_inquiries WHERE id = %s",
                    (new_id,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _row_to_dict(row)


def update_inquiry(db_cfg: Dict[str, Any], inquiry_id: int,
                   changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(changes, dict) or not changes:
        raise ValidationError('changes must be a non-empty object')

    set_parts: List[str] = []
    params: List[Any] = []
    for col, raw in changes.items():
        if col not in EDITABLE_COLUMNS:
            raise ValidationError(f'column not editable: {col!r}')
        if col == 'content':
            val: Any = _clean_content(raw)
        elif col == 'status':
            val = _clean_status(raw)
        elif col == 'buyma_product_id':
            val = _clean_str(raw, 30)
        elif col == 'mall':
            val = _clean_str(raw, 60)
        elif col == 'brand':
            val = _clean_str(raw, 120)
        elif col in ('author', 'responder'):
            val = _clean_str(raw, 60)
        elif col == 'resolution':
            val = _clean_str(raw, CONTENT_MAX_LEN)
        else:  # buyma_url, product_url
            val = _clean_str(raw, STR_MAX_LEN)
        set_parts.append(f'`{col}` = %s')
        params.append(val)

    params.append(inquiry_id)
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE field_inquiries SET {', '.join(set_parts)} WHERE id = %s",
                    params,
                )
                cur.execute(
                    "SELECT id, buyma_product_id, buyma_url, product_url, mall, brand, author, "
                    "content, status, responder, resolution, created_at, updated_at "
                    "FROM field_inquiries WHERE id = %s",
                    (inquiry_id,),
                )
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _row_to_dict(row) if row else None


def delete_inquiry(db_cfg: Dict[str, Any], inquiry_id: int) -> bool:
    with _connect(db_cfg) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM field_inquiries WHERE id = %s", (inquiry_id,))
                affected = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return affected > 0
