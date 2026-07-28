# -*- coding: utf-8 -*-
"""stock_price_synchronizer_*_merge.py 에 '옵션 동기화' 변경 일괄 적용.

naver/stock_price_synchronizer_naver_merge.py 에서 검증된 4가지를 같은 구조의 몰에 복제한다.
  1) 옵션 목록이 '완전한지' 표시 (options_complete)
  2) 몰 목록에서 사라진 옵션 → 재고0 (행은 안 지움. 지우면 source_offering_options 에 고아가 남음)
  3) 몰에만 있는 새 옵션 → ace_product_variants / ace_product_options 에 추가
  4) 수집과 BUYMA push 사이에 번역 1회 + 그래도 한글이면 그 행만 재고0
  5) 조사 모드 --gone-detect-only (DB·BUYMA 미변경)

대상은 옵션 목록을 JSON 배열로 통째로 받는 몰만 (부분 읽힘이 구조적으로 불가능).
HTML 파싱 몰은 '1순위 경로로 읽었는가' 판정이 따로 필요해 여기 포함하지 않는다.

각 치환은 정확히 1회 발생해야 하며, 0회/2회 이상이면 RuntimeError 로 중단(파일 미수정).

사용법:
    python _apply_option_sync.py --check     # 적용 가능한지만 확인
    python _apply_option_sync.py             # 실제 적용 (원본은 .bak 로 보관)
"""
import os
import re
import sys
import shutil
import argparse

ROOT = os.path.dirname(os.path.abspath(__file__))

TARGETS = [
    ("kasina", "kasina", "stock_price_synchronizer_kasina_merge.py"),
    ("musinsa", "musinsa_boutique", "stock_price_synchronizer_musinsa_merge.py"),
    # lotte 는 실행 중이라 보류. 끝난 뒤 아래 줄을 살려 다시 돌린다.
    #   ★ 적용 전에 _lotte_norm_size 의 순서를 변환기와 맞출 것
    #     (지금: 품절임박 제거 → FREE 확인 / 변환기: FREE 확인 → 품절임박 제거)
    # ("lotte", "lotte", "stock_price_synchronizer_lotte_merge.py"),
]

# ---- 1. 수집 결과에 options_complete 표시 ----
H1_OLD = "result = {'original_price': 0, 'sale_price': 0, 'options': []}"
H1_NEW = ("# options_complete: 옵션 목록을 몰이 배열로 통째로 준 경우에만 True.\n"
          "            #   True 여야만 '몰 목록에 없는 옵션 = 판매자가 내림' 으로 판정할 수 있다.\n"
          "            #   아래 단일상품 fallback 으로 만들어낸 목록은 진짜 목록이 아니므로 False 유지.\n"
          "            result = {'original_price': 0, 'sale_price': 0, 'options': [], 'options_complete': False}")

H2_OLD = "if not result['options']:"
H2_NEW = ("# ★ 여기까지 왔으면 목록은 몰이 준 배열을 통째로 훑은 결과다.\n"
          "            #   응답을 통으로 받거나 아예 못 받거나 둘뿐이라(못 받으면 위에서 이미 return)\n"
          "            #   '일부만 읽힘' 이 성립하지 않는다 → 완전한 목록으로 확정.\n"
          "            if result['options']:\n"
          "                result['options_complete'] = True\n"
          "\n"
          "            if not result['options']:")

# ---- 2. detect_stock_changes 시그니처 + summary ----
H3_OLD = "    def detect_stock_changes(self, db_variants: List[Dict], mall_options: List[Dict]) -> List[Dict]:\n        changes = []"
H3_NEW = '''    def detect_stock_changes(self, db_variants: List[Dict], mall_options: List[Dict],
                             options_complete: bool = False, summary: Dict = None) -> List[Dict]:
        """options_complete=True 면 mall_options 가 몰의 완전한 옵션 목록임이 보장된다.
        그때만 '몰 목록에 없는 DB 옵션 = 판매자가 내림' 으로 판정(change_type='gone')한다.

        summary(dict) 를 주면 이번 대조 결과를 채워준다(추정 아님, 세는 것):
          matched        : 몰 목록에서 짝을 찾은 DB 옵션 수
          in_stock_after : 그중 몰이 '재고있음' 이라고 한 수 = 처리 후 남는 재고 옵션 수
          new_options    : 몰에만 있는 옵션(= DB 에 행이 없는 것)
        """
        changes = []
        if summary is not None:
            summary.setdefault('matched', 0)
            summary.setdefault('in_stock_after', 0)'''

# ---- 3. 단일옵션 1:1 분기에도 집계 ----
H4_OLD = """            db_is_available = db_status != 'out_of_stock'
            mall_is_available = mall_opt['status'] == 'in_stock'
"""
H4_NEW = """            db_is_available = db_status != 'out_of_stock'
            mall_is_available = mall_opt['status'] == 'in_stock'

            if summary is not None:
                summary['matched'] += 1
                if mall_is_available:
                    summary['in_stock_after'] += 1
"""

# ---- 4. 매칭된 몰 옵션 추적 ----
H5_OLD = "        mall_by_code = {}"
H5_NEW = ("        # 어느 몰 옵션이 DB 와 짝지어졌는지 추적 → 남은 것이 '몰에만 있는 새 옵션'\n"
          "        used_codes, used_kr = set(), set()\n\n"
          "        mall_by_code = {}")

H6_OLD = """            if db_code and db_code in mall_by_code:
                mall_status = mall_by_code[db_code]
            elif (db_color_kr, db_size_kr) in mall_by_kr:
                mall_status = mall_by_kr[(db_color_kr, db_size_kr)]"""
H6_NEW = """            if db_code and db_code in mall_by_code:
                mall_status = mall_by_code[db_code]
                used_codes.add(db_code)
            elif (db_color_kr, db_size_kr) in mall_by_kr:
                mall_status = mall_by_kr[(db_color_kr, db_size_kr)]
                used_kr.add((db_color_kr, db_size_kr))"""

# ---- 5. skip → 사라진 옵션 재고0 ----
H7_OLD = """            if mall_status is None:
                continue

            mall_is_available = mall_status == 'in_stock'
"""
H7_NEW = """            if mall_status is None:
                # "몰 목록에 없다" 는 두 가지가 섞인 상태다:
                #   (가) 판매자가 그 옵션을 내렸다      → 우리도 내려야 함 (문의 7·25)
                #   (나) 이번에 목록을 제대로 못 읽었다 → 건드리면 5,320건 오삭제 재발
                # 이 몰은 옵션 목록을 배열로 통째로 받거나 아예 못 받거나 둘뿐이라,
                # options_complete=True 면 (나)가 성립할 수 없다 → (가)로 확정하고 재고 0 표시.
                # ★ 행은 지우지 않는다. 지우면 source_offering_options 에 짝 잃은 행이 남아
                #   그쪽에서 옛 '재고있음' 이 그대로 BUYMA 로 나간다.
                if options_complete and db_is_available:
                    changes.append({
                        'variant_id': variant['id'],
                        'color': variant.get('color_value'),
                        'size': variant.get('size_value'),
                        'old_status': db_status,
                        'new_status': 'out_of_stock',
                        'change_type': 'gone'
                    })
                continue

            mall_is_available = mall_status == 'in_stock'
            if summary is not None:
                summary['matched'] += 1
                if mall_is_available:
                    summary['in_stock_after'] += 1
"""

# ---- 6. 새 옵션 추려서 summary 에 ----
H8_OLD = """                    'change_type': 'restock'
                })
        return changes
"""
H8_NEW = """                    'change_type': 'restock'
                })

        # ★ 몰에만 있는 옵션(= DB 에 행이 없는 것) 추려서 summary 에 담는다.
        if summary is not None:
            news = []
            for item in mall_options:
                code = (item.get('option_code') or '').strip()
                mc = (item.get('color', '') or '').strip().lower() or 'free'
                ms = (item.get('size', '') or '').strip().lower() or 'free'
                if code and code in used_codes:
                    continue
                if (mc, ms) in used_kr:
                    continue
                news.append(item)
            summary['new_options'] = news
        return changes
"""

# ---- 7. 신규 옵션 추가 메서드 ----
H9_OLD = "    def update_ace_products_price(self, ace_product_id: int, original_price_krw: int,"
H9_NEW = '''    # ------------------------------------------------------------------
    # 몰에만 있는 옵션 → ace_product_variants / ace_product_options 에 추가
    # ------------------------------------------------------------------
    def _color_master_id(self, cur, color_display: str) -> int:
        """색상 표시값으로 master_id 조회.
        converter 는 okmall/colors.csv 매핑으로 정하는데, 그 결과가 이미
        ace_product_options 에 100만 건 쌓여 있으므로 거기서 같은 값을 찾아 쓴다.
        (사이즈는 converter 도 전부 0 이라 조회 불필요) 못 찾으면 converter 와 같은 기본값 99."""
        if not color_display:
            return 99
        cur.execute("""SELECT master_id FROM ace_product_options
                       WHERE option_type='color' AND value=%s AND master_id<>0 LIMIT 1""",
                    (color_display,))
        r = cur.fetchone()
        return (r['master_id'] if r else 99)

    def insert_new_variants(self, ace_product_id: int, new_opts: List[Dict],
                            db_variants: List[Dict]) -> List[Dict]:
        """새 옵션 행 추가. 표시용(color_value/size_value)은 같은 상품의 기존 행에서
        같은 원본을 가진 것의 값을 재사용하고, 없으면 몰 원본(한글) 그대로 넣는다.
        (한글로 남으면 BUYMA 가 요청 전체를 거부하므로, run() 에서 번역 후 남은 것만 재고0 처리)"""
        if not new_opts:
            return []

        color_disp, size_disp = {}, {}
        for v in db_variants:
            co = (v.get('color_value_original') or '').strip()
            so = (v.get('size_value_original') or '').strip()
            if co and v.get('color_value'):
                color_disp.setdefault(co, v['color_value'])
            if so and v.get('size_value'):
                size_disp.setdefault(so, v['size_value'])

        added = []
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                for o in new_opts:
                    c_org = (o.get('color', '') or '').strip() or 'FREE'
                    s_org = (o.get('size', '') or '').strip() or 'FREE'
                    c_disp = color_disp.get(c_org, c_org)
                    s_disp = size_disp.get(s_org, s_org)
                    in_stock = (o.get('status') == 'in_stock')
                    stock_type = 'purchase_for_order' if in_stock else 'out_of_stock'
                    options_json = json.dumps(
                        [{'type': 'color', 'value': c_disp}, {'type': 'size', 'value': s_disp}],
                        ensure_ascii=False)

                    cur.execute("""
                        INSERT IGNORE INTO ace_product_variants
                            (ace_product_id, color_value, size_value,
                             color_value_original, size_value_original, options_json,
                             stock_type, stocks, source_option_code, source_stock_status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (ace_product_id, c_disp, s_disp, c_org, s_org, options_json,
                          stock_type, 1 if in_stock else 0,
                          (o.get('option_code') or None), o.get('status')))
                    if cur.rowcount == 0:
                        continue
                    vid = cur.lastrowid

                    for otype, val in (('color', c_disp), ('size', s_disp)):
                        cur.execute("""SELECT 1 FROM ace_product_options
                                       WHERE ace_product_id=%s AND option_type=%s AND value=%s LIMIT 1""",
                                    (ace_product_id, otype, val))
                        if cur.fetchone():
                            continue
                        cur.execute("""SELECT COALESCE(MAX(position),0)+1 AS pos
                                       FROM ace_product_options
                                       WHERE ace_product_id=%s AND option_type=%s""",
                                    (ace_product_id, otype))
                        pos = cur.fetchone()['pos']
                        mid = self._color_master_id(cur, val) if otype == 'color' else 0
                        cur.execute("""INSERT INTO ace_product_options
                                       (ace_product_id, option_type, value, master_id, position,
                                        details_json, source_option_value)
                                       VALUES (%s,%s,%s,%s,%s,NULL,%s)""",
                                    (ace_product_id, otype, val, mid, pos,
                                     c_org if otype == 'color' else s_org))
                    added.append({'variant_id': vid, 'color': c_disp, 'size': s_disp,
                                  'stock_type': stock_type})
                conn.commit()
        finally:
            conn.close()
        return added

    def update_ace_products_price(self, ace_product_id: int, original_price_krw: int,'''

# ---- 8. 호출부: options_complete 전달 + 신규옵션 추가 + 조사모드 ----
H10_OLD = "stock_changes = self.detect_stock_changes(db_variants, mall_options)"
H10_NEW = ("_sum = {}\n"
           "                stock_changes = self.detect_stock_changes(db_variants, mall_options,\n"
           "                                                          options_complete, _sum)")

H11_OLD = """            if stock_changes:
                add_log(f"  - [변경] 재고 변동 {len(stock_changes)}건")"""
H11_NEW = """            # ★ 몰에만 있는 새 옵션 → ace_product_variants / ace_product_options 에 추가.
            #   목록이 완전할 때만(= 배열을 통으로 받았을 때만) 한다.
            new_added = []
            if options_complete and not dry_run:
                new_added = self.insert_new_variants(product['id'],
                                                     _sum.get('new_options') or [], db_variants)
                if new_added:
                    add_log(f"  - [신규옵션] {len(new_added)}개 추가: "
                            + ', '.join(f"{a['color']}/{a['size']}" for a in new_added[:8]))
                    with stats_lock:
                        stats['new_option_added'] = stats.get('new_option_added', 0) + len(new_added)
                        self._new_variant_ids.extend(a['variant_id'] for a in new_added)
                    need_api_call = True
            elif options_complete and dry_run and (_sum.get('new_options') or []):
                add_log(f"  - [DRY-RUN] 신규옵션 {len(_sum['new_options'])}개 추가 예정")

            if stock_changes:
                add_log(f"  - [변경] 재고 변동 {len(stock_changes)}건")"""

H12_OLD = """                    ct = "품절" if change['change_type'] in ['soldout', 'not_found'] else "재입고\""""
H12_NEW = """                    if change['change_type'] == 'gone':
                        ct = "몰목록에서사라짐"
                    elif change['change_type'] in ['soldout', 'not_found']:
                        ct = "품절"
                    else:
                        ct = "재입고\""""

# ---- 9. __init__ 상태 ----
H13_OLD = "        self.is_blocked = False"
H13_NEW = ("        self.is_blocked = False\n"
           "        # 이번 회차에 새로 추가한 옵션 행 (run 끝의 번역·한글가드 대상)\n"
           "        self._new_variant_ids = []\n"
           "        self.gone_detect_only = False")

# ---- 10. 번역 + 한글 가드 메서드 ----
H14_OLD = "    def _reconcile_published(self, products: List[Dict]) -> None:"
H14_NEW = '''    def _translate_and_guard(self, products: List[Dict]) -> None:
        """이번 회차에 새 옵션을 추가한 뒤, BUYMA push 전에 번역을 한 번 돌린다.

        - 번역은 기존 배치 그대로 사용 (상품별 호출 아님. 중복 제거 후 묶어서 호출)
        - 번역이 안 된 채 남은 행은 재고0 으로 눌러 둔다. 한글이 하나라도 섞이면
          그 상품의 BUYMA 요청 전체가 실패한다(= 재고·가격도 함께 못 올라감).
        """
        sites = sorted({p.get('source_site') for p in products if p.get('source_site')})
        log(f"[TRANSLATE] 신규 옵션 {len(self._new_variant_ids)}개 → 번역 실행 (몰: {', '.join(sites)})")
        try:
            from convert_to_japanese_gemini import run_batch_translation
            for site in sites:
                run_batch_translation(source=site)
        except Exception as e:
            log(f"[TRANSLATE] 번역 실패 — 한글 남은 옵션은 재고0 처리: {e}", "WARNING")

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                fmt = ','.join(['%s'] * len(self._new_variant_ids))
                cur.execute(f"""
                    UPDATE ace_product_variants
                    SET stock_type='out_of_stock', stocks=0
                    WHERE id IN ({fmt})
                      AND (color_value REGEXP '[가-힣]' OR size_value REGEXP '[가-힣]')
                """, self._new_variant_ids)
                n = cur.rowcount
                conn.commit()
            if n:
                log(f"[TRANSLATE] 번역 안 된 신규 옵션 {n}개 → 재고0 (다음 회차에 올라감)", "WARNING")
            else:
                log("[TRANSLATE] 신규 옵션 전부 일본어 확보 — 이번 회차에 BUYMA 반영")
        finally:
            conn.close()

    def _reconcile_published(self, products: List[Dict]) -> None:'''

# ---- 11. run(): 번역 호출 ----
H15_OLD = """        if not dry_run:
            try:
                self._reconcile_published(products)"""
H15_NEW = """        # ★ refresh 끝 → reconcile 사이에 번역을 한 번 끼운다.
        #   새로 추가한 옵션의 표시용 값이 한글이면 BUYMA 가 요청 전체를 거부한다.
        #   collector~register 의 순서(변환 → 번역 → 등록)를 등록 상품에 재현하는 것.
        if not dry_run and self._new_variant_ids:
            self._translate_and_guard(products)

        if not dry_run:
            try:
                self._reconcile_published(products)"""

# ---- 12. stats ----
H16_OLD = "            'blocked': 0"
H16_NEW = ("            'blocked': 0,\n"
           "            'new_option_added': 0,      # 몰에만 있어 새로 추가한 옵션 수")


# ---- 13. 호출부에서 options_complete 꺼내기 + 조사 모드 ----
H17_OLD = "            mall_options = mall_data.get('options', [])\n"
H17_NEW = '''            mall_options = mall_data.get('options', [])
            options_complete = bool(mall_data.get('options_complete'))

            # ★ --gone-detect-only: 몰 목록에서 사라진/새로 생긴 옵션 실태만 기록.
            #   DB·BUYMA 아무것도 안 바꾸고, 최저가 크롤도 안 돈다(불필요한 BUYMA 조회 방지).
            if self.gone_detect_only:
                db_variants = self.get_current_variants(product['id'])
                summary = {}
                changes = self.detect_stock_changes(db_variants, mall_options,
                                                    options_complete, summary)
                gone = [c for c in changes if c.get('change_type') == 'gone']
                news = summary.get('new_options') or []
                in_stock_after = summary.get('in_stock_after', 0)
                add_log(f"  [DETECT] DB옵션 {len(db_variants)} / 몰옵션 {len(mall_options)}"
                        f" / 사라짐 {len(gone)} / 신규 {len(news)}"
                        f" / 처리후재고 {in_stock_after}"
                        f" / 목록완전 {'Y' if options_complete else 'N'}"
                        + ("  ★처리후 팔 옵션 0개 → 출품정지됨" if in_stock_after == 0 else ""))
                for c in gone:
                    add_log(f"      [사라짐] {c.get('color', '')} / {c.get('size', '')} (DB={c['old_status']})")
                if news:
                    add_log("      [새옵션] "
                            + ', '.join(f"{(o.get('color') or '')}/{(o.get('size') or '')}" for o in news[:8]))
                with stats_lock:
                    stats['detect_products'] += 1
                    stats['detect_gone'] += len(gone)
                    stats['detect_new'] += len(news)
                    if not options_complete:
                        stats['detect_incomplete'] += 1
                    if in_stock_after == 0:
                        stats['detect_all_gone'] += 1
                        stats['detect_all_gone_ids'].append(product['id'])
                log_batch(logs)
                return
'''

# ---- 14. 조사 모드 CLI + run 인자 ----
H18_OLD = "    parser.add_argument('--force', action='store_true', help='변경 없어도 강제 API 호출')"
H18_NEW = ("    parser.add_argument('--force', action='store_true', help='변경 없어도 강제 API 호출')\n"
           "    parser.add_argument('--gone-detect-only', action='store_true',\n"
           "                        help='사라진/새 옵션 실태만 기록 (DB·BUYMA 아무것도 안 바꿈)')")

H19_OLD = """    def run(self, limit: int = None, brand: str = None, product_id: int = None, dry_run: bool = False, force: bool = False) -> Dict:
        log("=" * 60)"""
H19_NEW = """    def run(self, limit: int = None, brand: str = None, product_id: int = None, dry_run: bool = False,
            force: bool = False, gone_detect_only: bool = False) -> Dict:
        self.gone_detect_only = gone_detect_only
        log("=" * 60)"""

# ---- 15. main() 에서 인자 전달 ----
H22_OLD = "            force=args.force\n        )"
H22_NEW = "            force=args.force,\n            gone_detect_only=args.gone_detect_only,\n        )"

H20_OLD = "            'new_option_added': 0,      # 몰에만 있어 새로 추가한 옵션 수"
H20_NEW = ("            'new_option_added': 0,      # 몰에만 있어 새로 추가한 옵션 수\n"
           "            # --gone-detect-only 집계\n"
           "            'detect_products': 0,\n"
           "            'detect_gone': 0,\n"
           "            'detect_new': 0,\n"
           "            'detect_incomplete': 0,\n"
           "            'detect_all_gone': 0,\n"
           "            'detect_all_gone_ids': [],")

H21_OLD = """        # ★ refresh 끝 → reconcile 사이에 번역을 한 번 끼운다."""
H21_NEW = """        if gone_detect_only:
            log("=" * 60)
            log("GONE-DETECT-ONLY 결과 (아무것도 변경하지 않음)")
            log(f"  판정한 상품:               {stats['detect_products']}건")
            log(f"  ★ 몰에서 사라진 옵션:      {stats['detect_gone']}개")
            log(f"  ★ 몰에만 있는 새 옵션:     {stats['detect_new']}개")
            log(f"  목록 불완전(판정 못함):    {stats['detect_incomplete']}건")
            log(f"  ★ 처리 후 팔 옵션 0개:     {stats['detect_all_gone']}건 (켜면 출품정지됨)")
            if stats['detect_all_gone_ids']:
                _ids = stats['detect_all_gone_ids']
                log(f"     해당 ace_products.id: {_ids[:50]}"
                    + (f" … 외 {len(_ids)-50}건" if len(_ids) > 50 else ""))
            log("=" * 60)
            return stats

        # ★ refresh 끝 → reconcile 사이에 번역을 한 번 끼운다."""

HUNKS = [
    ("options_complete 표시", H1_OLD, H1_NEW),
    ("완전목록 확정", H2_OLD, H2_NEW),
    ("detect 시그니처", H3_OLD, H3_NEW),
    ("단일옵션 집계", H4_OLD, H4_NEW),
    ("매칭추적 초기화", H5_OLD, H5_NEW),
    ("매칭추적 기록", H6_OLD, H6_NEW),
    ("사라진옵션 재고0", H7_OLD, H7_NEW),
    ("새옵션 추려담기", H8_OLD, H8_NEW),
    ("신규옵션 추가 메서드", H9_OLD, H9_NEW),
    ("호출부 인자전달", H10_OLD, H10_NEW),
    ("호출부 신규옵션", H11_OLD, H11_NEW),
    ("변경로그 표기", H12_OLD, H12_NEW),
    ("__init__ 상태", H13_OLD, H13_NEW),
    ("번역+한글가드", H14_OLD, H14_NEW),
    ("run 번역호출", H15_OLD, H15_NEW),
    ("stats", H16_OLD, H16_NEW),
    ("호출부 options_complete + 조사모드", H17_OLD, H17_NEW),
    ("조사모드 CLI", H18_OLD, H18_NEW),
    ("run 시그니처", H19_OLD, H19_NEW),
    ("조사모드 stats", H20_OLD, H20_NEW),
    ("조사모드 요약", H21_OLD, H21_NEW),
    ("main 인자전달", H22_OLD, H22_NEW),
]


def apply(src, path, check_only):
    for name, old, new in HUNKS:
        n = src.count(old)
        if n != 1:
            raise RuntimeError(f"[{path}] '{name}' 치환 대상 {n}회 (1회여야 함) → 중단")
        src = src.replace(old, new, 1)
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='적용 가능 여부만 확인')
    args = ap.parse_args()

    for mall, folder, fname in TARGETS:
        path = os.path.join(ROOT, folder, fname)
        src = open(path, encoding='utf-8').read()
        try:
            out = apply(src, f"{mall}", args.check)
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)
        if args.check:
            print(f"✅ {mall:10} 적용 가능 ({len(HUNKS)}개 변경)")
            continue
        shutil.copy2(path, path + '.bak')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
        print(f"✅ {mall:10} 적용 완료 (원본 → {os.path.basename(path)}.bak)")


if __name__ == '__main__':
    main()
