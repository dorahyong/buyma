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

from pathlib import Path

import os

import pymysql
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parent
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

_MANAGE_ALLOWED_TABLES = frozenset({"mall_sites", "ace_products"})
_MANAGE_LIMIT_CHOICES = (50, 100, 200, 500)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "manage_server"}), 200


@app.route("/manage")
def manage_dashboard():
    table = (request.args.get("table") or "mall_sites").strip()
    if table not in _MANAGE_ALLOWED_TABLES:
        table = "mall_sites"
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    if limit not in _MANAGE_LIMIT_CHOICES:
        limit = min(_MANAGE_LIMIT_CHOICES, key=lambda x: abs(x - limit))

    try:
        conn = pymysql.connect(**DB_CONFIG)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM `{table}` ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        columns = list(rows[0].keys()) if rows else []
    except Exception as e:
        return (
            render_template(
                "manage.html",
                error=str(e),
                columns=[],
                rows=[],
                table=table,
                limit=limit,
                allowed_tables=sorted(_MANAGE_ALLOWED_TABLES),
                limits=_MANAGE_LIMIT_CHOICES,
            ),
            500,
        )

    return render_template(
        "manage.html",
        error=None,
        columns=columns,
        rows=rows,
        table=table,
        limit=limit,
        allowed_tables=sorted(_MANAGE_ALLOWED_TABLES),
        limits=_MANAGE_LIMIT_CHOICES,
    )


if __name__ == "__main__":
    port = int(os.getenv("MANAGE_SERVER_PORT", "8001"))
    app.run(host="127.0.0.1", port=port, debug=False)
