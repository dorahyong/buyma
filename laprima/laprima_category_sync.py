# -*- coding: utf-8 -*-
"""
라프리마 mall_categories 3-way 동기화 (추가 / 삭제 / 유지)

라프리마 라이브 카테고리 메뉴(aside#category 트리)와 DB(mall_categories, mall_name='laprima')를
cate_no(=category_id, 숫자) 기준으로 비교한다.

  - 추가된 것 (사이트에 있고 DB에 없음)      → INSERT (buyma_category_id=NULL, is_active=1)
  - 삭제된 것 (DB에 있고 사이트에 없음)      → ★소프트 삭제 (is_active=0). 하드 DELETE 안 함.
                                              (수동 매핑한 buyma_category_id 보존 위함)
  - 그대로인 것 (양쪽 다 있음)               → 손대지 않음 (buyma_category_id 그대로 유지)
  - 이름만 바뀐 것 (같은 cate_no, full_path 다름) → RENAME 후보로만 표시(자동수정 안 함, 매핑 영향 커서)

수집 대상은 3-depth 리프('WOMEN > 가방 > 숄더백')뿐이라, 사이트 리프만 비교 대상으로 삼는다.
(NEW IN·오늘출발 같은 자식 없는 top, depth2-only 는 수집기가 어차피 순회 안 함 → 비교 제외)

사용법:
    python laprima_category_sync.py --dry-run          # 범위만 출력, DB 변경 없음 (기본, 안전)
    python laprima_category_sync.py --dry-run --from-file  # 라이브 대신 로컬 categories.html 파싱
    python laprima_category_sync.py --execute          # 실제 반영 (소프트삭제+추가). 확인 후에만.
"""

import os
import re
import sys
import argparse
from typing import Dict, List, Tuple, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', 3306)}/{os.getenv('DB_NAME')}?charset=utf8mb4"
)
engine = create_engine(DATABASE_URL, echo=False)

BASE_URL = 'https://laprima.co.kr'
SOURCE_SITE = 'laprima'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


# ===========================================
# 1) 라이브 카테고리 트리 파싱
# ===========================================
def _cate_no_from(el) -> str:
    """li/a 의 cate 속성 또는 href 에서 cate_no 숫자 추출."""
    for attr in ('cate', 'href'):
        v = el.get(attr) or ''
        m = re.search(r'cate_no=(\d+)', v)
        if m:
            return m.group(1)
    return ''


def _gender_of(top_name: str) -> str:
    up = (top_name or '').strip().upper()
    if up == 'WOMEN':
        return 'female'
    if up == 'MEN':
        return 'male'
    return ''


def parse_category_tree(html: str) -> List[Dict]:
    """aside 카테고리 메뉴에서 모든 노드(top·depth2·leaf) 추출.

    mall_categories 는 상위 노드(예: 'WOMEN', 'WOMEN > 가방')도 각자 cate_no 로 보관하므로
    리프만 비교하면 상위 노드가 '삭제'로 오판된다 → 메뉴의 모든 cate_no 노드를 담는다.
    (수집기가 실제로 순회하는 건 3-depth 리프뿐이지만, 삭제/유지 판정은 전체 노드 기준)

    Returns: [{cate_no, full_path, gender, depth1, depth2, depth3, level, url}, ...]
    """
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.select_one('#category ul.category_wrap')
    if root is None:
        # 폴백: 클래스 조합이 다를 때
        root = soup.select_one('ul.category_wrap')
    nodes: List[Dict] = []
    if root is None:
        return nodes

    def add(cate_no, full_path, gender, d1, d2, d3, level):
        if not cate_no:
            return
        nodes.append({
            'cate_no': cate_no, 'full_path': full_path, 'gender': gender,
            'depth1': d1, 'depth2': d2, 'depth3': d3, 'level': level,
            'url': f'{BASE_URL}/product/list.html?cate_no={cate_no}',
        })

    for top_li in root.select(':scope > li.category_li'):
        top_a = top_li.select_one(':scope > a')
        if not top_a:
            continue
        top_name = top_a.get_text(strip=True)
        gender = _gender_of(top_name)
        top_no = _cate_no_from(top_li) or _cate_no_from(top_a)

        # top 노드 자체
        add(top_no, top_name, gender, top_name, '', '', 1)

        sub_ul = top_li.select_one(':scope > ul.category_sub')
        if sub_ul is None:
            continue  # 자식 없는 top (NEW IN, 오늘출발 등)

        for sub_li in sub_ul.select(':scope > li.category_sub_li'):
            d2_a = sub_li.select_one(':scope > a')
            if not d2_a:
                continue
            d2_name = d2_a.get_text(strip=True)
            d2_no = _cate_no_from(d2_a) or _cate_no_from(sub_li)

            # depth2 노드 자체
            add(d2_no, f'{top_name} > {d2_name}', gender, top_name, d2_name, '', 2)

            child_ul = sub_li.select_one(':scope > ul.category_sub_child')
            if child_ul is None:
                continue  # depth2 리프(자식 없음)

            for leaf_li in child_ul.select(':scope > li.category_sub_child_li'):
                leaf_a = leaf_li.select_one('a')
                if not leaf_a:
                    continue
                leaf_name = leaf_a.get_text(strip=True)
                leaf_no = _cate_no_from(leaf_a) or _cate_no_from(leaf_li)
                add(leaf_no, f'{top_name} > {d2_name} > {leaf_name}',
                    gender, top_name, d2_name, leaf_name, 3)
    return nodes


def fetch_live_html() -> str:
    sess = requests.Session()
    sess.headers.update({'User-Agent': UA, 'Referer': 'https://www.google.com/'})
    resp = sess.get(f'{BASE_URL}/index.html', timeout=20)
    resp.raise_for_status()
    return resp.text


DEFAULT_HTML_FILE = 'laprima_category_3depth.html'


def load_local_html(filename: str = DEFAULT_HTML_FILE) -> str:
    path = filename if os.path.isabs(filename) else os.path.join(os.path.dirname(__file__), filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# ===========================================
# 2) DB 현재 상태
# ===========================================
def load_db_categories() -> List[Dict]:
    """mall_categories(laprima) 전체 행."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT category_id, full_path, gender, buyma_category_id, is_active
            FROM mall_categories
            WHERE mall_name = 'laprima'
        """)).fetchall()
    return [{
        'category_id': str(r[0]) if r[0] is not None else '',
        'full_path': r[1] or '',
        'gender': r[2] or '',
        'buyma_category_id': r[3],
        'is_active': r[4],
    } for r in rows]


# ===========================================
# 3) 비교
# ===========================================
def _is_leaf_path(fp: str) -> bool:
    return fp.count(' > ') >= 2


def diff(live: List[Dict], db: List[Dict]) -> Dict:
    live_by_no = {c['cate_no']: c for c in live}
    live_nos = set(live_by_no)

    # DB 분류: 숫자 category_id 리프 / 비숫자(컨버터가 full_path 를 id로 넣은 이상행)
    db_numeric: Dict[str, Dict] = {}
    db_anomaly: List[Dict] = []
    for c in db:
        cid = c['category_id']
        if cid.isdigit():
            db_numeric[cid] = c
        else:
            db_anomaly.append(c)
    db_nos = set(db_numeric)

    added = [live_by_no[n] for n in sorted(live_nos - db_nos, key=int)]
    removed_nos = db_nos - live_nos
    removed = [db_numeric[n] for n in sorted(removed_nos, key=int)]

    kept, renamed = [], []
    for n in sorted(live_nos & db_nos, key=int):
        lv, dv = live_by_no[n], db_numeric[n]
        if lv['full_path'] != dv['full_path']:
            renamed.append({'cate_no': n, 'db_path': dv['full_path'],
                            'live_path': lv['full_path'],
                            'buyma_category_id': dv['buyma_category_id']})
        else:
            kept.append(dv)

    return {
        'added': added, 'removed': removed, 'kept': kept,
        'renamed': renamed, 'anomaly': db_anomaly,
        'live_count': len(live), 'db_count': len(db),
    }


# ===========================================
# 4) 실행 (execute 시에만 — dry-run 은 아무것도 안 함)
# ===========================================
def apply_changes(d: Dict):
    """추가 INSERT + 삭제 소프트비활성(is_active=0). 유지/이름변경은 손대지 않음."""
    with engine.begin() as conn:
        for c in d['added']:
            conn.execute(text("""
                INSERT INTO mall_categories
                  (mall_name, category_id, gender, depth1, depth2, depth3,
                   full_path, buyma_category_id, is_active, mall_category_url)
                VALUES
                  (:mn, :cid, :g, :d1, :d2, :d3, :fp, NULL, 1, :url)
            """), {'mn': SOURCE_SITE, 'cid': c['cate_no'], 'g': c['gender'],
                   'd1': c['depth1'], 'd2': c['depth2'], 'd3': c['depth3'],
                   'fp': c['full_path'], 'url': c['url']})
        for c in d['removed']:
            conn.execute(text("""
                UPDATE mall_categories SET is_active = 0
                WHERE mall_name = 'laprima' AND category_id = :cid
            """), {'cid': c['category_id']})


# ===========================================
# 출력
# ===========================================
def _fmt_buyma(v) -> str:
    if v is None:
        return 'buyma=NULL(미매핑)'
    return f'buyma={v}★매핑됨'


def report(d: Dict, source: str, execute: bool):
    print('=' * 70)
    print(f'라프리마 mall_categories 3-way 비교  (source={source}, mode={"EXECUTE" if execute else "DRY-RUN"})')
    print('=' * 70)
    print(f'라이브 노드(top+depth2+leaf): {d["live_count"]}개 / DB laprima 행: {d["db_count"]}개')
    print(f'  → 추가 {len(d["added"])} · 삭제 {len(d["removed"])} · 유지 {len(d["kept"])} · '
          f'이름변경 {len(d["renamed"])} · 이상행(비숫자 id) {len(d["anomaly"])}')

    print(f'\n[추가] 사이트에 있고 DB에 없음 → INSERT 예정 ({len(d["added"])}개)')
    for c in d['added']:
        print(f'  + cate_no={c["cate_no"]:>4}  {c["full_path"]}  ({c["gender"] or "-"})')

    print(f'\n[삭제] DB에 있고 사이트에 없음 → is_active=0 소프트삭제 예정 ({len(d["removed"])}개)')
    mapped = [c for c in d['removed'] if c['buyma_category_id'] is not None]
    for c in d['removed']:
        flag = '  ⚠️매핑소실주의' if c['buyma_category_id'] is not None else ''
        print(f'  - cate_no={c["category_id"]:>4}  {c["full_path"]}  '
              f'[{_fmt_buyma(c["buyma_category_id"])}] is_active={c["is_active"]}{flag}')
    if mapped:
        print(f'  ※ 삭제대상 중 buyma_category_id 매핑된 것 {len(mapped)}개 — 소프트삭제라 값은 보존됨.')

    if d['renamed']:
        print(f'\n[이름변경] 같은 cate_no, full_path 다름 → 자동수정 안 함(검토 필요) ({len(d["renamed"])}개)')
        for c in d['renamed']:
            print(f'  ~ cate_no={c["cate_no"]:>4}  DB:"{c["db_path"]}"  →  LIVE:"{c["live_path"]}"  '
                  f'[{_fmt_buyma(c["buyma_category_id"])}]')

    if d['anomaly']:
        print(f'\n[이상행] category_id 가 숫자가 아님(컨버터가 full_path 를 id로 INSERT한 흔적) ({len(d["anomaly"])}개)')
        for c in d['anomaly'][:50]:
            print(f'  ? category_id="{c["category_id"]}"  full_path="{c["full_path"]}"  '
                  f'[{_fmt_buyma(c["buyma_category_id"])}] is_active={c["is_active"]}')
        if len(d['anomaly']) > 50:
            print(f'  ... 외 {len(d["anomaly"]) - 50}개')
        print('  ※ 이 행들은 숫자 cate_no 매칭이 안 됨 → 삭제/유지 판정에서 제외됨. 별도 검토 필요.')

    print('\n' + '=' * 70)
    if not execute:
        print('DRY-RUN — DB 변경 없음. 반영하려면 --execute (승인 후).')
    print('=' * 70)


def main():
    ap = argparse.ArgumentParser(description='라프리마 mall_categories 3-way 동기화')
    ap.add_argument('--dry-run', action='store_true', help='범위만 출력, DB 변경 없음(기본)')
    ap.add_argument('--execute', action='store_true', help='실제 반영(소프트삭제+추가)')
    ap.add_argument('--from-file', nargs='?', const=DEFAULT_HTML_FILE, default=None,
                    help=f'라이브 대신 로컬 HTML 파싱 (기본 {DEFAULT_HTML_FILE}, 경로 지정 가능)')
    args = ap.parse_args()

    execute = args.execute and not args.dry_run

    if args.from_file:
        html = load_local_html(args.from_file)
        source = f'local {args.from_file}'
    else:
        try:
            html = fetch_live_html()
            source = 'live laprima.co.kr'
        except Exception as e:
            print(f'⛔ 라이브 페이지 수집 실패: {e}')
            print('   → --from-file 로 로컬 categories.html 로 대신 확인 가능')
            return

    live = parse_category_tree(html)
    if not live:
        print('⛔ 카테고리 트리 파싱 결과 0개 — 페이지 구조 변경 의심. 반영 중단.')
        return

    db = load_db_categories()
    d = diff(live, db)
    report(d, source, execute)

    if execute:
        apply_changes(d)
        print(f'\n✅ 반영 완료: 추가 {len(d["added"])} INSERT · 삭제 {len(d["removed"])} soft-off')


if __name__ == '__main__':
    main()
