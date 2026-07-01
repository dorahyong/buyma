# -*- coding: utf-8 -*-
"""
brand_removal_log.csv → 보기 좋은 HTML 리포트.

규칙은 단순: '스캔에 없는 브랜드 = 삭제 후보'. 수집처별로 묶어서 보여준다.
CSV는 매 reconcile마다 append되므로 (mall, category_key)별 '최신 1건'만 표시.

사용법:
    python brand_removal_report.py            # naver/brand_removal_report.html 생성
    python brand_removal_report.py --open       # 생성 후 브라우저로 열기
"""
import os, csv, html, argparse, webbrowser
from collections import defaultdict
import pymysql
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_CSV = os.path.join(HERE, 'brand_removal_log.csv')
OUT_HTML = os.path.join(HERE, 'brand_removal_report.html')
BRANDSTORE = {'carpi', 'joharistore'}  # brand.naver.com (나머지는 smartstore.naver.com)

load_dotenv(os.path.join(os.path.dirname(HERE), '.env'))
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}


def full_url(mall, url):
    if not url:
        return ''
    base = 'https://brand.naver.com' if mall in BRANDSTORE else 'https://smartstore.naver.com'
    return base + url if url.startswith('/') else url


def attach_counts(rows):
    """각 삭제후보에 그 몰·브랜드로 수집된 ace 상품 수(collected)와 바이마 게시 수(registered)를 붙인다.
    매칭은 카테고리 해시(mall_brand_no) 우선, 없으면 브랜드명. DB 접속 실패 시 None으로 두고 리포트는 그대로 생성."""
    for r in rows:
        r['collected'] = r['registered'] = None
    malls = sorted({r['mall'] for r in rows})
    if not malls:
        return
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except Exception as e:
        print(f"[경고] DB 접속 실패 — 상품수 생략: {e}")
        return
    try:
        with conn.cursor() as cur:
            fmt = ','.join(['%s'] * len(malls))
            # (몰, 해시)·(몰, 이름) → buyma_brand_id
            cur.execute(f"SELECT mall_name, mall_brand_no, mall_brand_name_en, raw_brand_name, buyma_brand_id "
                        f"FROM mall_brands WHERE mall_name IN ({fmt})", malls)
            bid_by_hash, bid_by_name = {}, {}
            for m in cur.fetchall():
                if m['buyma_brand_id'] is None:
                    continue
                bid = int(m['buyma_brand_id'])
                if m['mall_brand_no']:
                    bid_by_hash[(m['mall_name'], m['mall_brand_no'])] = bid
                for nm in (m['mall_brand_name_en'], m['raw_brand_name']):
                    if nm:
                        bid_by_name.setdefault((m['mall_name'], nm), bid)
            # (몰, brand_id) → 수집수 / 바이마 게시수
            cur.execute(f"SELECT source_site, brand_id, COUNT(*) c, SUM(is_published=1) p "
                        f"FROM ace_products WHERE source_site IN ({fmt}) AND brand_id IS NOT NULL "
                        f"GROUP BY source_site, brand_id", malls)
            cnt = {(a['source_site'], int(a['brand_id'])): (a['c'], int(a['p'] or 0)) for a in cur.fetchall()}
    finally:
        conn.close()
    for r in rows:
        bid = bid_by_hash.get((r['mall'], r.get('category_key') or '')) or bid_by_name.get((r['mall'], r['brand_name']))
        if bid is None:
            continue
        r['collected'], r['registered'] = cnt.get((r['mall'], bid), (0, 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--open', action='store_true', help='생성 후 브라우저로 열기')
    args = ap.parse_args()

    if not os.path.exists(LOG_CSV):
        print(f"로그 없음: {LOG_CSV} — 먼저 reconcile 돌려주세요."); return

    # (mall, category_key)별 최신 1건만
    latest = {}
    with open(LOG_CSV, 'r', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            key = (row['mall'], row.get('category_key') or row['brand_name'])
            cur = latest.get(key)
            if not cur or row['detected_at'] > cur['detected_at']:
                latest[key] = row

    rows = list(latest.values())
    attach_counts(rows)
    by_mall = defaultdict(list)
    for r in rows:
        by_mall[r['mall']].append(r)
    for mall in by_mall:
        by_mall[mall].sort(key=lambda r: r['brand_name'])

    total = len(rows)
    css = """
    body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;margin:24px;background:#f6f7f9;color:#222}
    h1{font-size:20px} h2{font-size:16px;margin:22px 0 8px;border-left:4px solid #c0392b;padding-left:8px}
    .sum{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:12px 16px;margin-bottom:6px;display:inline-block}
    .sum b{font-size:18px;color:#c0392b}
    table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e3e6ea;border-radius:8px;overflow:hidden}
    th,td{padding:8px 12px;text-align:left;font-size:13px;border-bottom:1px solid #eef0f2}
    th{background:#fafbfc;font-size:12px;color:#666}
    a{color:#2d6cdf;text-decoration:none} a:hover{text-decoration:underline}
    .muted{color:#999;font-size:12px}
    .tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#eef0f2;color:#666}
    """
    p = [f"<!doctype html><html><head><meta charset='utf-8'><title>삭제 브랜드 후보</title><style>{css}</style></head><body>"]
    p.append("<h1>🗑️ 삭제 후보 브랜드 (스캔에 없어진 것 · 담당자 확인용)</h1>")
    mall_counts = ' &nbsp; '.join(f"{html.escape(m)} {len(v)}" for m, v in sorted(by_mall.items()))
    p.append(f"<div class='sum'>총 <b>{total}</b>개 후보 &nbsp;|&nbsp; <span class='muted'>{mall_counts}</span></div>")
    # 바이마에 아직 게시된 후보 = 가장 급함(삭제된 브랜드인데 출품 살아있음)
    urgent = [r for r in rows if r.get('registered')]
    if urgent:
        reg_total = sum(r['registered'] for r in urgent)
        p.append(f"<div class='sum' style='border-color:#c0392b'>⚠️ 바이마에 아직 출품된 후보 "
                 f"<b>{len(urgent)}</b>개 (상품 <b>{reg_total}</b>개) — 우선 확인</div>")
    p.append("<div class='muted'>※ 수집처 브랜드 메뉴 스캔에 더 이상 없는 브랜드입니다. "
             "카테고리 클릭 시 사이트에서 직접 확인 → 진짜 사라졌으면 삭제. "
             "<b>수집</b>=우리 DB에 이 브랜드로 들어온 상품 수, <b>바이마</b>=그중 현재 출품 중인 수.</div>")

    for mall in sorted(by_mall):
        items = by_mall[mall]
        p.append(f"<h2>{html.escape(mall)} <span class='muted'>({len(items)}개)</span></h2>")
        p.append("<table><tr><th>브랜드</th><th>카테고리(사이트 확인)</th>"
                 "<th>수집</th><th>바이마</th><th>감지일</th><th>상태</th></tr>")
        for r in items:
            url = full_url(r['mall'], r.get('mall_brand_url', ''))
            ck = (r.get('category_key') or '')[:12]
            cat = f"<a href='{html.escape(url)}' target='_blank'>{html.escape(ck)}…</a>" if url else html.escape(ck)
            col, reg = r.get('collected'), r.get('registered')
            col_cell = f"<td class='muted'>-</td>" if col is None else f"<td>{col}</td>"
            if reg is None:
                reg_cell = "<td class='muted'>-</td>"
            elif reg:  # 출품 살아있음 → 강조
                reg_cell = f"<td><b style='color:#c0392b'>{reg}</b></td>"
            else:
                reg_cell = f"<td class='muted'>0</td>"
            p.append(f"<tr><td><b>{html.escape(r['brand_name'])}</b></td>"
                     f"<td>{cat}</td>"
                     f"{col_cell}{reg_cell}"
                     f"<td class='muted'>{html.escape(r['detected_at'][:10])}</td>"
                     f"<td><span class='tag'>{html.escape(r.get('action','로그만'))}</span></td></tr>")
        p.append("</table>")

    p.append("</body></html>")
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(''.join(p))
    print(f"리포트 생성: {OUT_HTML} (후보 {total}개)")
    if args.open:
        webbrowser.open('file://' + OUT_HTML)


if __name__ == '__main__':
    main()