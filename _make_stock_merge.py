# -*- coding: utf-8 -*-
"""stock_price_synchronizer_*.py → *_merge.py 로 5가지 [MERGE] 변경 일괄 적용.
kasina 에서 검증된 패턴을 동일 구조의 7개 몰에 복제. 각 치환은 정확히 1회 발생해야 하며,
0회 또는 2회 이상이면 RuntimeError 로 즉시 중단(파일 미생성)."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# (folder, mall_label, src_filename)
TARGETS = [
    ("nextzennpack", "nextzennpack", "stock_price_synchronizer_nextzennpack.py"),
    ("labellusso",   "labellusso",   "stock_price_synchronizer_labellusso.py"),
    ("9tems",        "9tems",        "stock_price_synchronizer_9tems.py"),
    ("brickmansion", "brickmansion", "stock_price_synchronizer_brickmansion.py"),
    ("loromoda",     "loromoda",     "stock_price_synchronizer_loromoda.py"),
    ("milaneez",     "milaneez",     "stock_price_synchronizer_milaneez.py"),
    ("maisonparco",  "maisonparco",  "stock_price_synchronizer_maisonparco.py"),
    ("musinsa_boutique", "musinsa",   "stock_price_synchronizer_musinsa.py"),
    ("naver",        "naver",        "stock_price_synchronizer_naver.py"),
]

# ---- Hunk 1: win32 stdout wrap 제거 + reconcile import (okmall/ 경로 추가) ----
H1_OLD = """# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)"""
H1_NEW = """# [MERGE] reconcile 엔진(okmall/)을 여기서 import.
#   buyma_new_product_register 가 win32 stdout/stderr utf-8 wrap 까지 처리하므로,
#   여기서 또 감싸면 안 됨 — 이중 wrap → 버퍼 닫힘(I/O operation on closed file) 버그.
#   stdout wrap 은 bnpr 한 곳만, import 도 모듈 로드 시 한 번만.
#   reconcile 모듈들은 okmall/ 에 있으므로 sys.path 에 추가 후 import.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'okmall'))
import reconcile_runner  # noqa: E402  (stdout utf-8 wrap 부수효과 포함)"""

# ---- Hunk 2: _mark_all_out_of_stock + _reconcile_published 메서드 추가 ----
H2_OLD = """                \"\"\", (ace_product_id,))
                conn.commit()
        finally:
            conn.close()

    def update_product_after_api_call(self, ace_product_id: int, request_data: Dict, response: Dict) -> None:"""
def h2_new(mall):
    return """                \"\"\", (ace_product_id,))
                conn.commit()
        finally:
            conn.close()

    # ====================================================
    # [MERGE] 재고0 표시 + reconcile push (BUYMA 직접 건드리지 않음)
    # ====================================================
    def _mark_all_out_of_stock(self, ace_product_id: int) -> None:
        \"\"\"%(mall)s 품절/삭제/흠집 → 이 ace 의 옵션 전부 out_of_stock 표시.
        BUYMA 직접 삭제 대신(다른 몰 있으면 winner 이동) → reconcile 이 판단.\"\"\"
        conn = self.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(\"\"\"
                    UPDATE ace_product_variants
                    SET stock_type='out_of_stock', source_stock_status='out_of_stock'
                    WHERE ace_product_id=%%s
                \"\"\", (ace_product_id,))
                conn.commit()
        finally:
            conn.close()

    def _reconcile_published(self, products: List[Dict]) -> None:
        \"\"\"이번 회차에 refresh 한 상품들의 그룹만 reconcile 이 BUYMA push (옵션합침+싼몰).
        ★ 이번 synced 상품에 한정 (%(mall)s published 전체 아님) → --limit/--id 테스트 안전.
        그룹락으로 multi-PC 안전. push 결정(edit/retire)은 reconcile 이 담당.\"\"\"
        import reconcile_runner as rr
        import reconcile_buyma_push as push
        from dedup_corrector_merge import canonicalize
        model_nos = [p['model_no'] for p in products if p.get('model_no')]
        if not model_nos:
            return
        conn = push.get_connection()
        try:
            with conn.cursor() as cur:
                fmt = ','.join(['%%s'] * len(model_nos))
                cur.execute(f"SELECT DISTINCT model_no, brand_id FROM ace_products WHERE model_no IN ({fmt})",
                            model_nos)
                rows = cur.fetchall()
            seen, groups = set(), []
            for r in rows:
                key = (r['brand_id'], canonicalize(r['model_no']))
                if key in seen:
                    continue
                seen.add(key)
                groups.append((r['model_no'], r['brand_id']))
            log(f"[MERGE] reconcile push 대상(이번 refresh 그룹): {len(groups)}건")
            ok = err = skip = 0
            # 로그용 몰이름 — 이미 조회된 products 에서 꺼냄(추가 쿼리·JOIN 없음).
            _mall = (products[0].get('source_site') or '?') if products else '?'
            _total = len(groups)
            for _i, (model_no, brand_id) in enumerate(groups, 1):
                res = rr.process_one_group(conn, model_no, brand_id, dry_run=False, scope='published',
                                           tag=f"[{_mall} {_i}/{_total}] ")
                resp = res.get('response') or {}
                if res.get('skipped'):
                    skip += 1
                elif resp.get('success'):
                    ok += 1
                elif resp:
                    err += 1
                time.sleep(0.4)
            log(f"[MERGE] reconcile 완료: 성공 {ok} / 실패 {err} / 스킵 {skip}")
        finally:
            conn.close()

    def update_product_after_api_call(self, ace_product_id: int, request_data: Dict, response: Dict) -> None:""" % {"mall": mall}

# ---- Hunk 3: 삭제/종료 분기 → 재고0 표시 ----
H3_OLD = """                # 상품 삭제(404) 또는 판매 종료 → 바이마에서도 삭제
                add_log(f"  → 수집처에서 상품 삭제/종료됨 → 바이마 삭제 요청")

                # 바이마 삭제 API 호출
                if not dry_run:
                    api_data = self.get_product_data_for_api(product['id'])
                    request_json = self.build_buyma_request(api_data, is_delete=True)

                    add_log(f"  바이마 API 호출 중... (삭제)")
                    result = self.call_buyma_api(request_json)
                    self.update_product_after_api_call(product['id'], request_json, result)

                    with stats_lock:
                        if result.get('success'):
                            add_log(f"  API 성공 (삭제)")
                            stats['api_called'] += 1
                            stats['deleted'] += 1
                        else:
                            add_log(f"  API 실패: {result.get('error', 'Unknown')}", "ERROR")
                            stats['failed'] += 1
                else:
                    add_log(f"  [DRY-RUN] 삭제 API 호출 예정")
                    with stats_lock:
                        stats['deleted'] += 1"""
def h3_new(mall):
    return """                # [MERGE] 바이마 직접 삭제 안 함 — %(mall)s 옵션만 재고0 표시.
                #   %(mall)s만 품절/삭제이어도 다른 몰 있으면 winner 이동, 없으면 reconcile 이 retire.
                add_log(f"  → 수집처 삭제/종료 → %(mall)s 재고0 표시 (BUYMA 반영은 reconcile)")
                if not dry_run:
                    self._mark_all_out_of_stock(product['id'])
                    self.update_sync_time_only(product['id'])
                else:
                    add_log(f"  [DRY-RUN] %(mall)s 재고0 표시 예정")
                with stats_lock:
                    stats['skipped'] += 1""" % {"mall": mall}

# ---- Hunk 4: step7/8 → refresh-only ----
H4_OLD = """            # 7. DB 업데이트
            if not is_delete:
                # is_lowest_price: 경쟁자 없으면 1, 있으면 내 가격 <= 최저가일 때 1
                if not new_lowest_price:
                    calc_is_lowest = 1
                else:
                    calc_is_lowest = 1 if new_price_jpy <= new_lowest_price else 0
                # purchase_price_jpy: 매입가(원) → 엔화 변환
                calc_purchase_price_jpy = round(new_purchase_price_krw / EXCHANGE_RATE) if new_purchase_price_krw else None

                self.update_ace_products_price(
                    product['id'], new_original_price, int(new_purchase_price_krw),
                    new_price_jpy, new_original_price_jpy, new_lowest_price,
                    margin_info['margin_rate'],
                    margin_amount_krw=margin_info['margin_krw'],
                    is_lowest_price=calc_is_lowest,
                    purchase_price_jpy=calc_purchase_price_jpy
                )
                if stock_changes:
                    self.update_ace_variants_stock(stock_changes)

            # 8. API 호출 여부 결정
            if need_api_call:
                api_data = self.get_product_data_for_api(product['id'])
                request_json = self.build_buyma_request(api_data, is_delete=is_delete)

                add_log(f"  바이마 API 호출 중... ({'삭제' if is_delete else '수정'})")
                result = self.call_buyma_api(request_json)
                self.update_product_after_api_call(product['id'], request_json, result)

                with stats_lock:
                    if result.get('success'):
                        add_log(f"  API 성공")
                        stats['api_called'] += 1
                        if is_delete:
                            stats['deleted'] += 1
                    else:
                        add_log(f"  API 실패: {result.get('error', 'Unknown')}", "ERROR")
                        stats['failed'] += 1
                    stats['success'] += 1

                log_batch(logs)  # 로그 한 번에 출력
                time.sleep(API_CALL_DELAY)
                random_delay()
            else:
                self.update_sync_time_only(product['id'])
                add_log(f"  변경 없음, API 호출 생략")
                with stats_lock:
                    stats['skipped'] += 1
                log_batch(logs)  # 로그 한 번에 출력"""
H4_NEW = """            # 7. DB 업데이트 (refresh) — [MERGE] 항상 수행 (no-margin이어도 reconcile 이 판단하도록 최신화)
            if not new_lowest_price:
                calc_is_lowest = 1
            else:
                calc_is_lowest = 1 if new_price_jpy <= new_lowest_price else 0
            calc_purchase_price_jpy = round(new_purchase_price_krw / EXCHANGE_RATE) if new_purchase_price_krw else None

            self.update_ace_products_price(
                product['id'], new_original_price, int(new_purchase_price_krw),
                new_price_jpy, new_original_price_jpy, new_lowest_price,
                margin_info['margin_rate'],
                margin_amount_krw=margin_info['margin_krw'],
                is_lowest_price=calc_is_lowest,
                purchase_price_jpy=calc_purchase_price_jpy
            )
            if stock_changes:
                self.update_ace_variants_stock(stock_changes)

            # 8. [MERGE] BUYMA push 생략 — refresh 만. push(수정/삭제/옵션합침/싼몰)는 run 끝 reconcile 담당.
            self.update_sync_time_only(product['id'])
            add_log(f"  refresh 완료 (BUYMA 반영은 reconcile)")
            with stats_lock:
                stats['success'] += 1
            log_batch(logs)  # 로그 한 번에 출력"""

# ---- Hunk 5: run() 끝에 reconcile push ----
H5_OLD = """        log(f"  오류: {stats['errors']}건")
        log("=" * 60)

        return stats"""
H5_NEW = """        log(f"  오류: {stats['errors']}건")
        log("=" * 60)

        # [MERGE] refresh 끝 → reconcile 이 BUYMA push (옵션합침+싼몰+수정/삭제 판단)
        #   이번 회차 synced 상품(products)의 그룹만 대상.
        if not dry_run:
            try:
                self._reconcile_published(products)
            except Exception as e:
                log(f"[MERGE] reconcile push 오류: {e}", "ERROR")
        else:
            log("[MERGE] [DRY-RUN] reconcile push 단계 생략")

        return stats"""


# 일부 몰은 run() 끝 구조가 달라 H5 를 몰별로 override (naver: '차단(중단)' 로그 줄이 더 있음).
H5_OVERRIDE = {
    'naver': (
        """        log(f"  오류: {stats['errors']}건")
        log(f"  차단(중단): {stats['blocked']}건")
        log("=" * 60)

        return stats""",
        """        log(f"  오류: {stats['errors']}건")
        log(f"  차단(중단): {stats['blocked']}건")
        log("=" * 60)

        # [MERGE] refresh 끝 → reconcile 이 BUYMA push (옵션합침+싼몰+수정/삭제 판단)
        #   이번 회차 synced 상품(products)의 그룹만 대상. (Playwright 세션은 위 finally 에서 이미 정리됨)
        if not dry_run:
            try:
                self._reconcile_published(products)
            except Exception as e:
                log(f"[MERGE] reconcile push 오류: {e}", "ERROR")
        else:
            log("[MERGE] [DRY-RUN] reconcile push 단계 생략")

        return stats""",
    ),
}


def apply_one(old, new, text, label, fname):
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"[{fname}] {label}: 치환 대상 {n}회 발견 (정확히 1회여야 함)")
    return text.replace(old, new)


for folder, mall, fname in TARGETS:
    src = os.path.join(ROOT, folder, fname)
    dst = os.path.join(ROOT, folder, fname.replace(".py", "_merge.py"))
    with open(src, encoding="utf-8") as f:
        t = f.read()
    t = apply_one(H1_OLD, H1_NEW, t, "Hunk1 win32->import", fname)
    t = apply_one(H2_OLD, h2_new(mall), t, "Hunk2 methods", fname)
    t = apply_one(H3_OLD, h3_new(mall), t, "Hunk3 delete-branch", fname)
    t = apply_one(H4_OLD, H4_NEW, t, "Hunk4 step7/8", fname)
    h5_old, h5_new = H5_OVERRIDE.get(mall, (H5_OLD, H5_NEW))
    t = apply_one(h5_old, h5_new, t, "Hunk5 run-end", fname)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(t)
    print(f"OK  {dst}")

print("\nALL DONE")
