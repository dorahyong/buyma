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

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_CSV = os.path.join(HERE, 'brand_removal_log.csv')
OUT_HTML = os.path.join(HERE, 'brand_removal_report.html')
BRANDSTORE = {'carpi', 'joharistore'}  # brand.naver.com (나머지는 smartstore.naver.com)


def full_url(mall, url):
    if not url:
        return ''
    base = 'https://brand.naver.com' if mall in BRANDSTORE else 'https://smartstore.naver.com'
    return base + url if url.startswith('/') else url


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
    p.append("<div class='muted'>※ 수집처 브랜드 메뉴 스캔에 더 이상 없는 브랜드입니다. "
             "카테고리 클릭 시 사이트에서 직접 확인 → 진짜 사라졌으면 삭제.</div>")

    for mall in sorted(by_mall):
        items = by_mall[mall]
        p.append(f"<h2>{html.escape(mall)} <span class='muted'>({len(items)}개)</span></h2>")
        p.append("<table><tr><th>브랜드</th><th>카테고리(사이트 확인)</th><th>감지일</th><th>상태</th></tr>")
        for r in items:
            url = full_url(r['mall'], r.get('mall_brand_url', ''))
            ck = (r.get('category_key') or '')[:12]
            cat = f"<a href='{html.escape(url)}' target='_blank'>{html.escape(ck)}…</a>" if url else html.escape(ck)
            p.append(f"<tr><td><b>{html.escape(r['brand_name'])}</b></td>"
                     f"<td>{cat}</td>"
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