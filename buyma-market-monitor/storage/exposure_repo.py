"""품번 노출순위(exposure) 대상 산출·스냅샷/히스토리/상태 CRUD."""
from __future__ import annotations

import sqlite3


def normalize_model_query(raw: str | None) -> str | None:
    if raw is None:
        return None
    q = raw.strip()
    return q or None


def list_competitive_models(conn: sqlite3.Connection) -> list[str]:
    """ACTIVE 상품 중 서로 다른 셀러 2+가 같은 품번을 파는 경쟁 품번."""
    rows = conn.execute(
        """
        SELECT brand_model_number FROM items
        WHERE status = 'ACTIVE'
          AND brand_model_number IS NOT NULL
          AND brand_model_number != ''
        GROUP BY brand_model_number
        HAVING COUNT(DISTINCT seller_id) >= 2
        """
    ).fetchall()
    out: list[str] = []
    for r in rows:
        q = normalize_model_query(r[0])
        if q:
            out.append(q)
    return out


def list_own_models(conn: sqlite3.Connection) -> list[str]:
    """자사 취급 품번 (buyma_listings). 테이블이 없으면 빈 목록 (SQLite 테스트)."""
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT model_no FROM buyma_listings
            WHERE is_published = 1 AND is_active = 1
              AND model_no IS NOT NULL AND model_no != ''
            """
        ).fetchall()
    except Exception:
        return []
    out: list[str] = []
    for r in rows:
        q = normalize_model_query(r[0])
        if q:
            out.append(q)
    return out


def list_target_models(conn: sqlite3.Connection) -> list[str]:
    """경쟁 품번 ∪ 자사 품번 (정렬된 unique)."""
    return sorted(set(list_competitive_models(conn)) | set(list_own_models(conn)))


def list_pending_models(conn: sqlite3.Connection, day_prefix: str) -> list[str]:
    """오늘(day_prefix=YYYY-MM-DD) 아직 ok로 수집되지 않은 대상 품번.

    day_prefix는 observed_at / last_collected_at ISO 앞 10자리와 비교.
    """
    targets = list_target_models(conn)
    if not targets:
        return []
    # ISO observed_at 예: 2026-08-04T12:00:00+09:00 → 당일 ok 분은 LIKE 'YYYY-MM-DD%'
    done = {
        r[0]
        for r in conn.execute(
            """
            SELECT model_query FROM exposure_state
            WHERE last_status = 'ok'
              AND last_collected_at LIKE ?
            """,
            (day_prefix + "%",),
        ).fetchall()
    }
    return [m for m in targets if m not in done]


def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    model_query: str,
    observed_at: str,
    n_results_page1: int | None,
    total_results: int | None,
    floor_price_yen: int | None,
    status: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO exposure_snapshot
          (model_query, observed_at, n_results_page1, total_results, floor_price_yen, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (model_query, observed_at, n_results_page1, total_results, floor_price_yen, status),
    )
    return int(cur.lastrowid)


def insert_history_rows(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    model_query: str,
    observed_at: str,
    rows: list[dict],
) -> None:
    """rows: [{rank, item_id, price_yen, seller_name, seller_id}, ...] 표시순 그대로."""
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO exposure_history
          (snapshot_id, rank, model_query, item_id, price_yen, seller_name, seller_id, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                r["rank"],
                model_query,
                r["item_id"],
                r.get("price_yen"),
                r.get("seller_name"),
                r.get("seller_id"),
                observed_at,
            )
            for r in rows
        ],
    )


def upsert_state(
    conn: sqlite3.Connection,
    model_query: str,
    last_collected_at: str,
    last_status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO exposure_state (model_query, last_collected_at, last_status)
        VALUES (?, ?, ?)
        ON CONFLICT(model_query) DO UPDATE SET
          last_collected_at=excluded.last_collected_at,
          last_status=excluded.last_status
        """,
        (model_query, last_collected_at, last_status),
    )


def save_exposure_result(
    conn: sqlite3.Connection,
    *,
    model_query: str,
    observed_at: str,
    status: str,
    listings: list[dict] | None = None,
    total_results: int | None = None,
) -> int:
    """스냅샷+히스토리+상태 일괄 저장. snapshot_id 반환.

    listings 는 parse 결과(표시순). status=ok 이고 비어 있으면 empty 로 정규화해도
    호출측에서 status를 정해 넘겨도 된다.
    """
    listings = listings or []
    prices = [r["price_yen"] for r in listings if r.get("price_yen") is not None]
    floor = min(prices) if prices else None
    snapshot_id = insert_snapshot(
        conn,
        model_query=model_query,
        observed_at=observed_at,
        n_results_page1=len(listings),
        total_results=total_results,
        floor_price_yen=floor,
        status=status,
    )
    if status == "ok" and listings:
        insert_history_rows(
            conn,
            snapshot_id=snapshot_id,
            model_query=model_query,
            observed_at=observed_at,
            rows=listings,
        )
    upsert_state(conn, model_query, observed_at, status)
    return snapshot_id
