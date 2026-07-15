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
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

sys.path.insert(0, str(Path(__file__).resolve().parent))
from products_api import build_payload, get_sources, get_images  # noqa: E402
import products_cache  # noqa: E402
import products2_cache  # noqa: E402
from auth import configure_auth, register_auth_routes, require_login  # noqa: E402
import brands_api  # noqa: E402
import categories_api  # noqa: E402
import tabs_api  # noqa: E402
import tasks_api  # noqa: E402
import inquiries_api  # noqa: E402

load_dotenv()

_BASE_DIR = Path(__file__).resolve().parent
_STATS_DIR = _BASE_DIR.parent / "buyma_stats"
app = Flask(__name__, template_folder=str(_BASE_DIR / "templates"))
app.url_map.strict_slashes = False  # /manage 와 /manage/ 둘 다 허용

configure_auth(app)
register_auth_routes(app)

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# 부팅 시 캐시 워밍업 시작 (gunicorn import 시점)
# [임시차단 2026-07-15] products(레거시) 캐시 새로고침 중단.
#   원인: build_payload 가 raw_scraped_data 75만행을 통째 GROUP BY(LIMIT/인덱스 없음) → 100분+
#   → 5분마다 자동 재실행되며 안 끝나고 쌓여 DB 를 상시 짓누름(파이프라인·인덱스 지연).
#   products2(신규, buyma_listings 인덱스 기반)는 정상이므로 그대로 둠.
#   ▶ 되돌리기: 아래 products_cache.start(...) 주석만 해제 후 관리서버 재시작.
#   ▶ 근본해결(별도): products_api.build_payload 최적화(페이징/사전집계) 후 재개.
# products_cache.start({k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'})
products2_cache.start({k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'})

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
@require_login
def manage_products_view():
    # [임시차단 2026-07-15] 레거시 products 페이지 점검 안내.
    #   raw_scraped_data 75만행 집계로 DB 과부하 → 신규 products2 로 안내.
    #   ▶ 되돌리기: 아래 return 문(안내) 지우고 send_from_directory(...products.html) 복원.
    html = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>점검 중 · 상품 관리</title></head>"
        "<body style='font-family:system-ui,\"Malgun Gothic\",sans-serif;"
        "max-width:560px;margin:14vh auto;padding:0 24px;color:#222;line-height:1.7;text-align:center'>"
        "<div style='font-size:44px;margin-bottom:12px'>&#128295;</div>"
        "<h1 style='font-size:22px;margin:0 0 12px'>상품 관리 페이지 점검 중입니다</h1>"
        "<p style='color:#555;margin:0'>이 페이지는 데이터 처리 성능 개선을 위해 "
        "일시적으로 중단되었습니다.</p>"
        "</body></html>"
    )
    return Response(html, mimetype='text/html'), 503
    # return send_from_directory(_STATS_DIR, "products.html")  # 원복용


@app.route("/manage/products/data.json")
@require_login
def manage_products_data():
    """products.html이 fetch로 받아 가는 데이터.

    캐시에 미리 만들어둔 JSON / gzip bytes 를 그대로 송신한다 (직렬화/압축 비용 0).
    """
    j, gz = products_cache.get()
    if j is None:
        return jsonify({
            "items": [], "count": 0, "loading": True,
            "message": "데이터 준비 중입니다. 잠시 후 다시 시도해주세요.",
        }), 503
    accept = request.headers.get('Accept-Encoding', '')
    if gz is not None and 'gzip' in accept.lower():
        return Response(
            gz,
            mimetype='application/json',
            headers={
                'Content-Encoding': 'gzip',
                'Content-Length': str(len(gz)),
                'Vary': 'Accept-Encoding',
            },
        )
    return Response(
        j,
        mimetype='application/json',
        headers={
            'Content-Length': str(len(j)),
            'Vary': 'Accept-Encoding',
        },
    )


@app.route("/manage/products/sources.json")
@require_login
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
@require_login
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
@require_login
def manage_products_assets(filename):
    return send_from_directory(_STATS_DIR, filename)


# ── products2 (buyma_listings 기준, 신규) ──
@app.route("/manage/products2/")
@require_login
def manage_products2_view():
    return send_from_directory(_STATS_DIR, "products2.html")


@app.route("/manage/products2/data.json")
@require_login
def manage_products2_data():
    j, gz = products2_cache.get()
    if j is None:
        return jsonify({"items": [], "count": 0, "loading": True,
                        "message": "데이터 준비 중입니다. 잠시 후 다시 시도해주세요."}), 503
    accept = request.headers.get('Accept-Encoding', '')
    if gz is not None and 'gzip' in accept.lower():
        return Response(gz, mimetype='application/json',
                        headers={'Content-Encoding': 'gzip', 'Content-Length': str(len(gz)), 'Vary': 'Accept-Encoding'})
    return Response(j, mimetype='application/json',
                    headers={'Content-Length': str(len(j)), 'Vary': 'Accept-Encoding'})


@app.route("/manage/products2/<path:filename>")
@require_login
def manage_products2_assets(filename):
    return send_from_directory(_STATS_DIR, filename)


@app.route("/manage/brands")
@require_login
def manage_brands_view():
    return render_template("brands.html")


@app.route("/manage/brands/data.json")
@require_login
def manage_brands_data():
    mall_name = (request.args.get('mall_name') or '').strip() or None
    is_active = request.args.get('is_active')
    is_mapped = request.args.get('is_mapped')
    unmapped_only = request.args.get('unmapped_only', '').lower() in ('1', 'true', 'yes')
    search = (request.args.get('search') or '').strip() or None
    try:
        limit = int(request.args.get('limit', 500))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 5000))

    def _bool_or_none(v):
        if v is None or v == '':
            return None
        return 1 if str(v).lower() in ('1', 'true', 'yes') else 0

    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        rows = brands_api.get_brands(
            db_cfg,
            mall_name=mall_name,
            is_active=_bool_or_none(is_active),
            is_mapped=_bool_or_none(is_mapped),
            unmapped_only=unmapped_only,
            search=search,
            limit=limit,
        )
        mall_names = brands_api.get_mall_names(db_cfg)
    except Exception as e:
        return jsonify({"error": str(e), "rows": [], "mall_names": []}), 500
    return jsonify({
        "rows": rows,
        "count": len(rows),
        "mall_names": mall_names,
        "editable_columns": sorted(brands_api.EDITABLE_COLUMNS),
        "select_columns": brands_api.SELECT_COLUMNS,
    })


@app.route("/manage/brands/update", methods=['POST', 'PATCH'])
@require_login
def manage_brands_update():
    payload = request.get_json(silent=True) or {}
    changes = payload.get('changes') or []
    if not isinstance(changes, list) or not changes:
        return jsonify({"error": "changes must be a non-empty list", "updated": 0}), 400
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        updated = brands_api.apply_updates(db_cfg, changes)
    except Exception as e:
        return jsonify({"error": str(e), "updated": 0}), 500
    return jsonify({"updated": updated})


@app.route("/manage/brands/search_buyma")
@require_login
def manage_brands_search_buyma():
    q = (request.args.get('q') or '').strip()
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    results = brands_api.search_buyma_brands(q, limit=limit)
    return jsonify({"q": q, "count": len(results), "results": results})


@app.route("/manage/categories")
@require_login
def manage_categories_view():
    return render_template("categories.html")


@app.route("/manage/categories/data.json")
@require_login
def manage_categories_data():
    mall_name = (request.args.get('mall_name') or '').strip() or None
    is_active = request.args.get('is_active')
    unmapped_only = request.args.get('unmapped_only', '').lower() in ('1', 'true', 'yes')
    search = (request.args.get('search') or '').strip() or None
    try:
        limit = int(request.args.get('limit', 500))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 5000))

    def _bool_or_none(v):
        if v is None or v == '':
            return None
        return 1 if str(v).lower() in ('1', 'true', 'yes') else 0

    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        rows = categories_api.get_categories(
            db_cfg,
            mall_name=mall_name,
            is_active=_bool_or_none(is_active),
            unmapped_only=unmapped_only,
            search=search,
            limit=limit,
        )
        mall_names = categories_api.get_mall_names(db_cfg)
    except Exception as e:
        return jsonify({"error": str(e), "rows": [], "mall_names": []}), 500
    return jsonify({
        "rows": rows,
        "count": len(rows),
        "mall_names": mall_names,
        "editable_columns": sorted(categories_api.EDITABLE_COLUMNS),
        "select_columns": categories_api.SELECT_COLUMNS,
    })


@app.route("/manage/categories/update", methods=['POST', 'PATCH'])
@require_login
def manage_categories_update():
    payload = request.get_json(silent=True) or {}
    changes = payload.get('changes') or []
    if not isinstance(changes, list) or not changes:
        return jsonify({"error": "changes must be a non-empty list", "updated": 0}), 400
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        updated = categories_api.apply_updates(db_cfg, changes)
    except Exception as e:
        return jsonify({"error": str(e), "updated": 0}), 500
    return jsonify({"updated": updated})


@app.route("/manage/categories/search_buyma")
@require_login
def manage_categories_search_buyma():
    q = (request.args.get('q') or '').strip()
    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    results = categories_api.search_buyma_categories(q, limit=limit)
    return jsonify({"q": q, "count": len(results), "results": results})


@app.route("/manage/products/tabs", methods=['GET'])
@require_login
def manage_products_tabs_list():
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        tabs = tabs_api.list_tabs(db_cfg)
    except Exception as e:
        return jsonify({"error": str(e), "tabs": []}), 500
    return jsonify({"tabs": tabs})


@app.route("/manage/products/tabs", methods=['POST'])
@require_login
def manage_products_tabs_create():
    payload = request.get_json(silent=True) or {}
    name = payload.get('name')
    filter_obj = payload.get('filter')
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        tab = tabs_api.create_tab(db_cfg, name, filter_obj)
    except tabs_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"tab": tab}), 201


@app.route("/manage/products/tabs/<tab_id>", methods=['PUT'])
@require_login
def manage_products_tabs_update(tab_id):
    payload = request.get_json(silent=True) or {}
    name = payload.get('name')
    filter_obj = payload.get('filter')
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        tab = tabs_api.update_tab(db_cfg, tab_id, name, filter_obj)
    except tabs_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if tab is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"tab": tab})


@app.route("/manage/products/tabs/<tab_id>", methods=['DELETE'])
@require_login
def manage_products_tabs_delete(tab_id):
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        ok = tabs_api.delete_tab(db_cfg, tab_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not ok:
        return jsonify({"error": "not found"}), 404
    return ('', 204)


@app.route("/manage/tasks")
@require_login
def manage_tasks_view():
    return render_template("tasks.html")


@app.route("/manage/tasks/list")
@require_login
def manage_tasks_list():
    return jsonify({"tasks": tasks_api.list_tasks()})


@app.route("/manage/tasks/run", methods=["POST"])
@require_login
def manage_tasks_run():
    payload = request.get_json(silent=True) or {}
    task_id = payload.get("task_id")
    mode = payload.get("mode")
    options = payload.get("options") or {}
    try:
        meta = tasks_api.start_run(task_id, mode, options)
    except tasks_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(meta), 201


@app.route("/manage/tasks/stop", methods=["POST"])
@require_login
def manage_tasks_stop():
    payload = request.get_json(silent=True) or {}
    job_id = (payload.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    try:
        result = tasks_api.stop_run(job_id)
    except tasks_api.ValidationError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/manage/tasks/status")
@require_login
def manage_tasks_status():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    meta = tasks_api.get_run(job_id)
    if meta is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(meta)


@app.route("/manage/tasks/log")
@require_login
def manage_tasks_log():
    job_id = (request.args.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    offset = request.args.get("offset", 0)
    return jsonify(tasks_api.read_log(job_id, offset))


@app.route("/manage/tasks/recent")
@require_login
def manage_tasks_recent():
    return jsonify({"runs": tasks_api.list_recent_runs()})


@app.route("/manage/tasks/cleanup", methods=["POST"])
@require_login
def manage_tasks_cleanup():
    try:
        result = tasks_api.cleanup_runs()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/manage/tasks/cleanup_artifacts", methods=["POST"])
@require_login
def manage_tasks_cleanup_artifacts():
    try:
        # 스캔 생성 파일(json·csv)만 삭제. 쿠키는 별도(delete_cookie)로 관리.
        result = tasks_api.cleanup_artifacts(include_cookie=False)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


# --- 바이마 로그인 쿠키 (다운로드 / 업로드 / 삭제) ----------------------------

@app.route("/manage/tasks/login_tool")
@require_login
def manage_tasks_login_tool():
    """① PC에서 실행할 로그인 도구(.py) 다운로드."""
    p = tasks_api.login_tool_path()
    if not p.exists():
        return jsonify({"error": "login tool not found"}), 404
    return send_from_directory(
        str(p.parent), p.name, as_attachment=True, download_name="buyma_login.py"
    )


@app.route("/manage/tasks/cookie_status")
@require_login
def manage_tasks_cookie_status():
    return jsonify(tasks_api.cookie_status())


@app.route("/manage/tasks/scan_status")
@require_login
def manage_tasks_scan_status():
    return jsonify(tasks_api.scan_status())


@app.route("/manage/tasks/upload_cookie", methods=["POST"])
@require_login
def manage_tasks_upload_cookie():
    """④ PC에서 만든 buyma_cookies.json 업로드."""
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "파일이 없습니다."}), 400
    raw = f.read()
    if not raw:
        return jsonify({"error": "빈 파일입니다."}), 400
    if len(raw) > 2 * 1024 * 1024:
        return jsonify({"error": "파일이 너무 큽니다(2MB 초과)."}), 400
    try:
        result = tasks_api.save_cookie(raw)
    except tasks_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/manage/tasks/delete_cookie", methods=["POST"])
@require_login
def manage_tasks_delete_cookie():
    """⑤ 서버의 로그인 쿠키 삭제(보안)."""
    try:
        result = tasks_api.delete_cookie()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route("/manage/inquiries")
@require_login
def manage_inquiries_view():
    return render_template("inquiries.html")


@app.route("/manage/inquiries/data.json")
@require_login
def manage_inquiries_data():
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        rows = inquiries_api.list_inquiries(db_cfg)
    except Exception as e:
        return jsonify({"error": str(e), "rows": []}), 500
    return jsonify({"rows": rows, "count": len(rows)})


@app.route("/manage/inquiries", methods=["POST"])
@require_login
def manage_inquiries_create():
    payload = request.get_json(silent=True) or {}
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        row = inquiries_api.create_inquiry(db_cfg, payload)
    except inquiries_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"row": row}), 201


@app.route("/manage/inquiries/<int:inquiry_id>", methods=["PUT", "PATCH"])
@require_login
def manage_inquiries_update(inquiry_id):
    payload = request.get_json(silent=True) or {}
    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        row = inquiries_api.update_inquiry(db_cfg, inquiry_id, changes)
    except inquiries_api.ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"row": row})


@app.route("/manage/inquiries/<int:inquiry_id>", methods=["DELETE"])
@require_login
def manage_inquiries_delete(inquiry_id):
    db_cfg = {k: v for k, v in DB_CONFIG.items() if k != 'cursorclass'}
    try:
        ok = inquiries_api.delete_inquiry(db_cfg, inquiry_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not ok:
        return jsonify({"error": "not found"}), 404
    return ('', 204)


@app.route("/manage")
@require_login
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
