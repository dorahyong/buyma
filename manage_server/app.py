# -*- coding: utf-8 -*-
"""
DB 조회 전용 미니 서버 — 웹훅(okmall_reference/server.py)과 역할 분리.

  - 기본: 127.0.0.1:8001
  - 경로: GET /manage, GET /health (JSON)

  nginx 에서 바이마 API 도메인으로 붙일 때 예시:
    location /manage {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

  같은 .env (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME 등) 사용.
"""

import sys
from pathlib import Path

import os

import pymysql
from pymysql import err as mysql_err
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from products_api import build_payload, get_sources, get_images  # noqa: E402

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parent
_STATS_DIR = _BASE_DIR.parent / "buyma_stats"
app = Flask(__name__, template_folder=str(_BASE_DIR / "templates"))

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

_MANAGE_LIMIT_CHOICES = (50, 100, 200, 500)


def _fetch_table_names(conn):
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        rows = cur.fetchall()
        if not rows:
            return []
        key = list(rows[0].keys())[0]
        return [r[key] for r in rows]


def _select_from_table(conn, table, limit):
    with conn.cursor() as cur:
        try:
            cur.execute(
                f"SELECT * FROM `{table}` ORDER BY id DESC LIMIT %s",
                (limit,),
            )
        except (mysql_err.ProgrammingError, mysql_err.OperationalError):
            cur.execute(f"SELECT * FROM `{table}` LIMIT %s", (limit,))
        rows = cur.fetchall()
    columns = list(rows[0].keys()) if rows else []
    return columns, rows


def _render(table, limit, all_tables, columns, rows, error):
    return render_template(
        "manage.html",
        error=error,
        columns=columns,
        rows=rows,
        table=table or "",
        limit=limit,
        all_tables=sorted(all_tables),
        limits=_MANAGE_LIMIT_CHOICES,
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "manage_server"}), 200


@app.route("/manage/products/")
def manage_products_view():
    return send_from_directory(_STATS_DIR, "products.html")


@app.route("/manage/products/data.json")
def manage_products_data():
    """products.html이 fetch로 받아 가는 데이터. SQL JOIN으로 model_id당 1행 조회."""
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        payload = build_payload(db_cfg)
    except Exception as e:
        return jsonify({"error": str(e), "items": [], "count": 0}), 500
    return jsonify(payload)


@app.route("/manage/products/sources.json")
def manage_products_sources():
    """sources 팝업용 — ?model_id=XXX"""
    model_id = request.args.get('model_id', '').strip()
    if not model_id:
        return jsonify({"error": "model_id required", "sources": []}), 400
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        sources = get_sources(db_cfg, model_id)
    except Exception as e:
        return jsonify({"error": str(e), "sources": []}), 500
    return jsonify({"model_id": model_id, "sources": sources})


@app.route("/manage/products/images.json")
def manage_products_images():
    """이미지 팝업용 — ?model_id=XXX"""
    model_id = request.args.get('model_id', '').strip()
    if not model_id:
        return jsonify({"error": "model_id required", "images": []}), 400
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        images = get_images(db_cfg, model_id)
    except Exception as e:
        return jsonify({"error": str(e), "images": []}), 500
    return jsonify({"model_id": model_id, "images": images})


@app.route("/manage/products/<path:filename>")
def manage_products_assets(filename):
    return send_from_directory(_STATS_DIR, filename)


@app.route("/manage")
def manage_dashboard():
    raw_table = (request.args.get("table") or "").strip()
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    if limit not in _MANAGE_LIMIT_CHOICES:
        limit = min(_MANAGE_LIMIT_CHOICES, key=lambda x: abs(x - limit))

    try:
        conn = pymysql.connect(**DB_CONFIG)
        try:
            all_tables = _fetch_table_names(conn)
            if not raw_table:
                return _render("", limit, all_tables, [], [], None)

            if raw_table not in all_tables:
                return (
                    _render(
                        raw_table,
                        limit,
                        all_tables,
                        [],
                        [],
                        f"존재하지 않는 테이블입니다: {raw_table}",
                    ),
                    400,
                )

            columns, rows = _select_from_table(conn, raw_table, limit)
        finally:
            conn.close()
    except Exception as e:
        conn = None
        try:
            conn = pymysql.connect(**DB_CONFIG)
            all_tables = _fetch_table_names(conn)
        except Exception:
            all_tables = []
        finally:
            if conn:
                conn.close()
        return (
            render_template(
                "manage.html",
                error=str(e),
                columns=[],
                rows=[],
                table=raw_table or "",
                limit=limit,
                all_tables=sorted(all_tables),
                limits=_MANAGE_LIMIT_CHOICES,
            ),
            500,
        )

    return _render(raw_table, limit, all_tables, columns, rows, None)


if __name__ == "__main__":
    port = int(os.getenv("MANAGE_SERVER_PORT", "8001"))
    app.run(host="127.0.0.1", port=port, debug=False)
