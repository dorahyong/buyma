# 죽은 소싱처가 winner 가 되는 문제 — 실제 주문 취소 사례 전 과정

작성 2026-07-22 · 대상: **주문 `34975478`** (취소됨)
관련 문서: `reports/issue_dead_ace_as_winner_20260721.md` (원인 분석 전문)

---

## 0. 무슨 일이 있었나

| | |
|---|---|
| 주문번호 | **34975478** |
| 바이마 상품 | **134126011** (`listing #4471`) |
| 상품명 | OUR LEGACY THIRD CUT DIGITAL DENIM PRINT `M4205TDD` |
| 주문일 | **2026-07-20 19:18:41** |
| 금액 | ¥44,149 × 1 |
| 결과 | **`canceled`** (2026-07-22 03:30 처리) |

**취소 사유**: 주문 들어온 재고가 **fabstyle** 것이었는데, 실제로는 살 수 없는 상태였음.

**왜 살 수 없는 재고가 판매되고 있었나** — 이 문서가 그걸 단계별로 추적한다.

```sql
-- 주문 원본
SELECT * FROM buyma_self_orders WHERE order_id = 34975478;
```

---

## 1. 사전 지식 — 우리 구조

같은 상품을 여러 몰에서 수집해도 **바이마에는 하나만 올린다.**

```
buyma_listings   (바이마 페이지 1개)          ← 판매가·바이마번호·winner
   └ source_offerings  (몰별 소싱처 N개)      ← 몰·매입가·상품주소
        └ source_offering_options (몰별 옵션·재고)
   └ listing_options   (바이마에 실제 올라간 옵션)  ← 소싱처 옵션 중에서 채택된 것
```

- **winner** = 실제로 매입할 곳. `buyma_listings.winner_offering_id`
- winner 의 매입가로 마진을 계산하고 판매가를 정한다
- 각 소싱처는 `ace_products` 의 한 행(상품)을 가리킨다. 그 상품이 **살아있나(`is_active`)** 가 이 문제의 핵심

---

## 2. 이 상품의 실제 데이터

```sql
SET @listing_id = 4471;

-- 상품(목록) 원본
SELECT * FROM buyma_listings WHERE buyma_product_id = 134126011;

-- 소싱처 전부 + 각 소싱처 상품의 생사
SELECT so.*, a.*
FROM source_offerings so
JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id
ORDER BY so.purchase_price_krw;
```

**목록**
```
listing #4471   바이마 134126011
판매가 ¥44,123   경쟁자최저 ¥44,130   최저가여부 1
등록일 2026-06-29 22:00:07     최종갱신 2026-07-20 23:08:41
winner_offering_id = 10815
```

**소싱처 4곳 (매입가 순)**
```
     off   몰                 매입가    마진OK  ace      ace살아있나  상태     ace 최종갱신
★  10815  fabstyle          ₩384,730     1    127889      0        duple    2026-06-12 12:53   ← 6주 전에 멈춤
   150871  premiumsneakers   ₩398,410     1    229224      1        (없음)   2026-07-20 21:07   ← 유일하게 살아있음
   10818  pano              ₩435,810     0    211647      0        duple    2026-07-06 02:09
   10817  carpi             ₩448,470     0    187029      0        duple    2026-07-16 16:32
```

> **winner 가 `fabstyle` 인데, 그 상품은 이미 꺼져 있고 매입가가 6/12 에 얼어붙어 있다.**
> 실제로 살 수 있는 유일한 곳은 `premiumsneakers` ₩398,410 — **차이 ₩13,680**

---

## 3. 단계별 추적 — `run_daily_unified.py` 를 돌리면

### 1단계 · STOCK 이 "누구를 갱신할지" 고른다 → **죽은 것은 여기서 탈락**

`run_daily_unified.py` → `STOCK_REFRESH` → `naver/stock_price_synchronizer_naver_merge.py --source fabstyle`

`get_products_to_sync()` (okmall merge 604~608행, 전 몰 동일 패턴):
```sql
WHERE (바이마에 등록된 것)
  AND ap.source_product_url IS NOT NULL
  AND ap.is_active = 1          -- ★ 여기서 갈린다
  AND ap.source_site = 'fabstyle'
```

**확인 쿼리 — winner 인 ace 127889 가 대상에 들어오는가 (0건이면 갱신 안 됨)**
```sql
SELECT * FROM ace_products ap
WHERE ap.id = 127889
  AND EXISTS (SELECT 1 FROM source_offerings so
              JOIN buyma_listings bl ON bl.id = so.listing_id AND bl.is_active = 1
              WHERE so.ace_product_id = ap.id AND so.is_active = 1
                AND bl.is_published = 1 AND bl.buyma_product_id IS NOT NULL)
  AND ap.source_product_url IS NOT NULL
  AND ap.is_active = 1
  AND ap.source_site = 'fabstyle';
-- → 0건.  ap.is_active = 1 조건 때문에 탈락
```

**대조**
```sql
SELECT id, source_site, is_active, status, purchase_price_krw, updated_at
FROM ace_products WHERE id = 127889;
-- → is_active=0, status='duple', updated_at 2026-06-12 12:53:29
```

**이 상품의 소싱처 중 몇 개가 갱신 대상인가**
```sql
SELECT a.is_active, COUNT(*) AS 소싱처수,
       MIN(a.updated_at) AS 가장_오래된_갱신, MAX(a.updated_at) AS 가장_최근_갱신
FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id AND so.is_active = 1
GROUP BY a.is_active;
-- → is_active=0 이 3개(6/12·7/06·7/16 에 멈춤) / is_active=1 이 1개(7/20 최신)
```

> **fabstyle 사이트에 접속조차 하지 않는다.** 그래서 ₩384,730 과 그 재고는 **6/12 상태로 냉동**된다.

---

### 2단계 · STOCK 이 끝나고 reconcile 을 부른다

```python
# stock_price_synchronizer_*_merge.py  (12개 파일 공통)
self._reconcile_published(products)
  → rr.process_one_group(model_no, brand_id, dry_run=False, scope='published')
```

stock 은 **바이마를 직접 건드리지 않는다.** DB만 갱신하고 push 는 reconcile 담당.

---

### 3단계 · 그룹을 다시 짠다 → **죽은 것이 계속 멤버로 남는다**

`reconcile_ensure_group.compute_group_members()` — 기존 listing 이 있으므로 **88~96행 경로**:

```sql
SELECT a.*
FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id AND so.is_active = 1;
```
**ace 가 살았는지 죽었는지 보는 조건이 한 줄도 없다.**

**대조 — 신규 그룹 경로(110~111행 → `_load_brand_aces` 36행)에는 필터가 있다**
```sql
SELECT * FROM ace_products
WHERE brand_id = 3818
  AND (is_active = 1 OR status = 'duple')     -- ★ 신규에만 있음
  AND model_no IS NOT NULL AND model_no <> '';
```

> **같은 함수 안에서 신규는 거르고 기존은 안 거른다.** 한 번 멤버가 되면 어떤 이유로 죽든 영구 멤버.

**규칙 위반(duple 도 아닌데 멤버) 찾기**
```sql
SELECT so.id, so.source_site, a.id AS ace_id, a.is_active, a.status
FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id AND so.is_active = 1
  AND a.is_active = 0 AND (a.status IS NULL OR a.status <> 'duple');
-- 이 상품은 0건 (전부 duple) → 설계상 '의도된' 케이스
```

---

### 4단계 · 소싱 연결을 무조건 되살린다

`reconcile_ensure_group._upsert_offerings()` **269행**:
```sql
ON DUPLICATE KEY UPDATE
    ace_product_id=VALUES(...), source_product_url=VALUES(...),
    purchase_price_krw=VALUES(...), is_active=1,          -- ★ 무조건 1
    updated_at=CURRENT_TIMESTAMP
```

**증거 — 상품은 6/12 에 멈췄는데 연결은 7/20 에 새로 찍혔다**
```sql
SELECT so.id, so.source_site, so.is_active AS off_active, so.updated_at AS off_updated,
       a.id AS ace_id, a.is_active AS ace_active, a.status,
       a.updated_at AS ace_updated,
       TIMESTAMPDIFF(DAY, a.updated_at, so.updated_at) AS 차이_일
FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id
ORDER BY 차이_일 DESC;
```
```
off 10815  fabstyle   ace갱신 2026-06-12 12:53  →  연결갱신 2026-07-20 21:26   차이 38일
```
**38일 동안 그 몰에 한 번도 안 가봤는데, 연결만 계속 "최신"으로 갱신됐다.**

**전체 규모 — 끄는 장치가 사실상 안 쓰인다**
```sql
SELECT is_active, COUNT(*) FROM source_offerings GROUP BY is_active;
-- is_active=0 은 극소수
```

---

### 5단계 · winner 를 뽑는다 → **오직 "싼가"만 본다**

`resolve_merge.resolve_listing()` **196~201행**:
```python
ok_offerings = [o for o in offerings if margins[o['id']][2]]   # 마진 > 0 인 것만
if not ok_offerings: ...
winner = min(ok_offerings, key=lambda o: float(o['purchase_price_krw'] or 1e18))
```
게이트는 **마진 하나뿐**. `is_active` 도 `status` 도 안 본다.

**코드와 똑같은 순서로 줄 세우기**
```sql
SELECT so.id AS offering_id, so.source_site, so.purchase_price_krw, so.is_margin_ok,
       a.is_active AS ace_active, a.status,
       IF(so.id = bl.winner_offering_id, '★실제WINNER','') AS win
FROM source_offerings so
JOIN ace_products a ON a.id = so.ace_product_id
JOIN buyma_listings bl ON bl.id = so.listing_id
WHERE so.listing_id = @listing_id AND so.is_active = 1 AND so.is_margin_ok = 1
ORDER BY so.purchase_price_krw;
```
```
★ 10815  fabstyle         384,730  마진OK=1  ace살아있나=0  duple    ← 맨 위 = 실제 winner
  150871  premiumsneakers  398,410  마진OK=1  ace살아있나=1
```
**맨 위가 실제 winner** → 생사를 안 본다는 증명.

**"살아있는 것만으로 뽑았다면" — 부풀려진 금액**
```sql
SELECT
  (SELECT so.purchase_price_krw FROM source_offerings so
    WHERE so.id = bl.winner_offering_id)                     AS 현재_winner_가격,
  (SELECT MIN(so2.purchase_price_krw) FROM source_offerings so2
    JOIN ace_products a2 ON a2.id = so2.ace_product_id
    WHERE so2.listing_id = bl.id AND so2.is_active = 1
      AND so2.is_margin_ok = 1 AND a2.is_active = 1)         AS 살아있는것중_최저
FROM buyma_listings bl WHERE bl.id = @listing_id;
-- → 384,730  vs  398,410   =  ₩13,680 부풀림
```

---

### 6단계 · 옵션·재고도 죽은 쪽 것을 쓴다 → **취소의 직접 원인**

`resolve_merge.resolve_listing()` **203~212행**:
```python
for off in ok_offerings:
    pp = float(off['purchase_price_krw'] or 1e18)
    for opt in options_by_offering.get(off['id'], []):
        if opt['stock_type'] == 'out_of_stock':
            continue                                  # 품절이면 제외
        key = (opt['color_value'], opt['size_value'])
        if key not in union or pp < union[key][1]:    # 매입가 싼 쪽이 이김
            union[key] = (opt, pp, off)
```
**매입가가 싼 쪽이 이기므로, 냉동된 fabstyle 이 옵션까지 다 가져간다.**

**바이마에 올라간 옵션이 어느 소싱처에서 왔나**
```sql
SELECT lo.*, so.source_site, so.purchase_price_krw,
       a.is_active AS ace_active, a.status, a.updated_at AS ace_updated
FROM listing_options lo
LEFT JOIN source_offering_options soo ON soo.id = lo.sourced_offering_option_id
LEFT JOIN source_offerings so ON so.id = soo.offering_id
LEFT JOIN ace_products a ON a.id = so.ace_product_id
WHERE lo.listing_id = @listing_id AND lo.is_active = 1
ORDER BY lo.size_value, lo.color_value;
```
```
28(26SS)     fabstyle  ace127889 살아있나=0 duple  6/12
29(26SS)     fabstyle  ace127889 살아있나=0 duple  6/12
30(26SS)     fabstyle  ...
30X32(25SS)  fabstyle
31(25FW)     fabstyle
31(26SS)     fabstyle
31X32(25SS)  fabstyle
32           fabstyle
32(25FW)     fabstyle
32(26SS)     fabstyle
33(26SS)     fabstyle
33/32        premiumsneakers  ace229224 살아있나=1  7/20    ← 살아있는 건 2개뿐
34(26SS)     fabstyle
34/32        premiumsneakers  ace229224 살아있나=1  7/20
36(26SS)     fabstyle
```
**총 15개 중 13개가 죽은 fabstyle 에서 왔다.**

**죽은 소싱처인데 "재고 있음"으로 남아 있는 옵션**
```sql
SELECT so.source_site, soo.color_value, soo.size_value, soo.stock_type, soo.stocks,
       soo.updated_at AS 옵션갱신, a.is_active AS ace_active, a.updated_at AS ace갱신
FROM source_offering_options soo
JOIN source_offerings so ON so.id = soo.offering_id
JOIN ace_products a ON a.id = so.ace_product_id
WHERE so.listing_id = @listing_id
  AND a.is_active = 0 AND soo.stock_type <> 'out_of_stock';
-- → 21건 (fabstyle 13 · carpi 5 · pano 3), 전부 stocks=1 / purchase_for_order
```

> 갱신이 없으니 `out_of_stock` 으로 바뀔 일이 없고, **207~208행의 품절 필터를 그냥 통과한다.**
> 손님 눈에는 **"재고 있음"** 으로 보인다. → **주문 → 실제로 못 삼 → 취소**

---

### 7단계 · DB 에 쓴다

`_write_resolve()` **281~284행**:
```sql
UPDATE buyma_listings
SET price=?, buyma_lowest_price=?, is_lowest_price=1,
    winner_offering_id=10815, buying_shop_name=?, control='draft'
WHERE id = 4471;
```
```sql
SELECT id, buyma_product_id, price, winner_offering_id, buying_shop_name,
       control, status, is_published, updated_at
FROM buyma_listings WHERE id = @listing_id;
```

---

### 8단계 · 바이마로 보낸다

`reconcile_buyma_push.execute_edit()` → 그 가격·그 옵션·그 재고로 상품 수정.
**죽은 데이터가 그대로 라이브에 반영된다.**

---

## 4. 타임라인 — 어느 단계가 왜 그렇게 처리했나

> 아래는 **DB 값 + 실제 실행 로그**(`logs/unified_onlynaver_20260720-pc1.log`, UTF-16)로 확인한 것이다.
> 배치 `20260720_194744` (`UNIFIED_NAVER`, PC1, 유닛 14개 = NEW 7 / STOCK 7)

### ① 2026-04-15 10:17 — fabstyle 상품이 처음 수집됨
`run_daily_unified.py` **NEW 트랙 → COLLECT**
```
ace 127889  fabstyle  M4205TDD  ₩384,730   created 2026-04-15 10:17:18
```
이때는 정상적으로 살아있는 소싱처였다.

---

### ② 2026-06-12 12:53:29 — **dedup 이 일괄로 꺼버림** (냉동의 시작)

당시 파이프라인은 **PRICE 단계 앞에서 `dedup_corrector.py` 를 매일 돌리고 있었다.**
(상시 실행에서 빠진 건 2026-06-25, 커밋 `b706a8e`)

**이게 개별 처리가 아니라 일괄 작업이었다는 증거** — 똑같은 초에 갱신된 상품 수:
```sql
SELECT status, is_active, COUNT(*) n FROM ace_products
WHERE updated_at = '2026-06-12 12:53:29' GROUP BY status, is_active ORDER BY n DESC;
```
```
status=duple    is_active=0    7,880건     ← 한 초에 7,880개가 같이 꺼짐
status=(없음)   is_active=1      614건
status=fail     is_active=0      200건
...
6/12 하루 전체 갱신 8,724건
```

**왜 문제가 되나** — dedup 은 상품을 끄기만 하고 **소싱 연결(`source_offerings`)은 안 끊는다.**
(끊는 정책은 `migrations/collapse_true_dup.py:12` 에 확립돼 있지만 **상시 코드엔 미반영**)

> **이 순간부터 ace 127889 는 "STOCK 대상에서 빠지지만 소싱처로는 계속 쓰이는" 상태가 된다.**
> 매입가 ₩384,730 은 여기서 **영구 냉동**.

---

### ③ 2026-06-29 22:00:07 — 바이마 등록
`run_daily_unified.py` **NEW 트랙 → REGISTER**
```python
reconcile_runner.py --mode auto --scope new --source premiumsneakers --limit 100000 --execute
```
등록 시점에 이미 fabstyle 은 죽어 있었지만, `resolve_merge.py:201` 이 **매입가만 보고** 골랐으므로
처음부터 **죽은 fabstyle 이 winner** 로 등록됐다.

---

### ④ 2026-06-12 ~ 07-20 (38일) — 매일 돌았지만 fabstyle 은 한 번도 안 감

매일 **STOCK 트랙**이 돌았다. 그런데 `get_products_to_sync()` 의 `ap.is_active = 1` 때문에
ace 127889 는 **매번 대상에서 빠졌다.**

그런데도 그 상품의 **소싱 연결과 옵션은 계속 갱신 시각이 찍혔다.**
`_upsert_offerings()` **269행**이 `is_active=1, updated_at=CURRENT_TIMESTAMP` 를 무조건 쓰기 때문.

```
ace 127889   최종갱신 2026-06-12 12:53   ← 진짜 데이터는 38일 전
offering 10815 갱신 2026-07-20 21:26     ← 껍데기만 매일 최신
```
> **"매일 갱신되는 것처럼 보이지만 실제로는 6주 전 데이터"** 인 상태가 이렇게 만들어진다.

---

### ⑤ 2026-07-20 19:18:41 — **주문 발생** ¥44,149

손님 눈엔 재고가 있었다. 실제로 바이마에 올라간 옵션 15개 중 **13개가 죽은 fabstyle 것**이고,
전부 `purchase_for_order`(재고 있음)로 남아 있었다. 갱신이 없으니 품절로 바뀔 수가 없었다.

---

### ⑥ 2026-07-20 20:46:23 — STOCK 시작

로그:
```
[premiumsneakers/_ALL_/STOCK] [STOCK_REFRESH] 실행: stock_price_synchronizer_naver_merge.py
  옵션: source=premiumsneakers, id=None, brand=None, limit=None, dry_run=False, force=False
```

---

### ⑦ 2026-07-20 21:07:29 — ★ **STOCK 은 제대로 계산했다**

로그 (같은 상품):
```
[368/520] OUR LEGACY - 送料・関税込 | OUR LEGACY | ... (상품번호: M4205TDD)
  - 경쟁자 최저가: ¥44,130 → 내 가격: ¥44,128
  - 판매가: ¥44,128 (₩405,978)
  - 매입가: ₩398,410, 배송비: ₩11,350        ← 살아있는 premiumsneakers 값
```

**STOCK 은 살아있는 상품만 다루므로 ₩398,410(정상)로 계산했다.** 여기까진 옳다.
그리고 stock 은 바이마를 직접 안 건드리고 DB만 갱신한 뒤 reconcile 에 넘긴다.

---

### ⑧ 2026-07-20 21:15:58 — reconcile 시작

로그:
```
[MERGE] reconcile push 대상(이번 refresh 그룹): 540건
```
`_reconcile_published()` → 이번에 훑은 그룹들을 `process_one_group(scope='published')` 로 하나씩.

---

### ⑨ 2026-07-20 21:26:33 — ★★ **reconcile 이 죽은 데이터로 덮어썼다**

로그:
```
[premiumsneakers 443/540] 'M4205TDD' 멤버5(병합) listing#4471 buyma#134126011
API 응답 코드: 201
```

여기서 벌어진 일:
1. `ensure_group` 이 그룹을 다시 짬 → **88~96행 경로라 ace 생사를 안 봄** → 죽은 fabstyle 포함 **멤버 5개**
2. `_upsert_offerings` 269행 → 죽은 연결도 **`is_active=1` 로 되살림**
3. `resolve_listing` 201행 → `winner = min(마진OK, key=매입가)` → **fabstyle ₩384,730 당첨**
   (STOCK 이 계산한 ₩398,410 을 **덮어씀**)
4. 옵션 합집합(203~212행) → 매입가 싼 fabstyle 옵션이 이김 → **13/15 가 죽은 옵션**
5. `execute_edit` → **바이마로 전송, 201 성공**

> **STOCK 이 20분에 걸쳐 올바르게 계산한 값을, reconcile 이 마지막에 죽은 데이터로 되돌렸다.**
> 이게 이 사고의 정확한 메커니즘이다.

---

### ⑩ 2026-07-20 21:28:52 — reconcile 종료
```
[MERGE] reconcile 완료: 성공 463 / 실패 0 / 스킵 77
```
우리 상품은 **"성공 463" 중 하나**다. 시스템 입장에선 **정상 처리**로 기록됐다.

---

### ⑪ 2026-07-20 23:08:41 — 목록 최종 갱신
다른 몰의 STOCK 이 같은 그룹을 다시 건드림(같은 배치 내 다른 유닛).

---

### ⑫ 2026-07-21 14:41 — okmall 에서 같은 상품이 새로 수집됨
```
ace 526795  okmall  ₩435,000  is_active=1   created 2026-07-21 14:41:46
```
살아있는 소싱처가 하나 더 생겼지만 **주문 다음날**이라 이미 늦었다.

---

### ⑬ 2026-07-22 03:30:20 — **주문 취소 처리**

---

### 한눈에

| 시각 | 단계 | 무슨 일 | 왜 |
|---|---|---|---|
| 04-15 10:17 | NEW·COLLECT | fabstyle 수집 | 정상 |
| **06-12 12:53** | **PRICE 앞 dedup** | **7,880건 일괄 `duple` 처리** | 상품만 끄고 **소싱 연결은 안 끊음** |
| 06-29 22:00 | NEW·REGISTER | 바이마 등록 | winner 를 **매입가만** 보고 골라 죽은 fabstyle 채택 |
| 06-12~07-20 | STOCK (매일) | fabstyle **미방문** | `ap.is_active=1` 조건에 탈락 |
| 〃 | reconcile (매일) | 연결·옵션 **갱신 시각만 찍힘** | 269행 무조건 `is_active=1` |
| **07-20 19:18** | — | **주문 발생** | 죽은 재고가 "있음"으로 보임 |
| 07-20 20:46 | STOCK 시작 | premiumsneakers 훑기 | |
| **07-20 21:07** | **STOCK** | **₩398,410 로 정상 계산** | 살아있는 것만 보므로 옳음 |
| 07-20 21:15 | reconcile 시작 | 540그룹 | |
| **07-20 21:26** | **reconcile** | **₩384,730(죽은 값)으로 덮어쓰고 바이마 전송(201)** | ensure_group 필터 없음 + winner=최저가 |
| 07-20 21:28 | reconcile 종료 | "성공 463" | **사고가 성공으로 기록됨** |
| 07-22 03:30 | 사람 | **주문 취소** | |

---

## 5. 피해

**① 주문 취소** — 매출 손실 + 바이마 셀러 평가 하락(취소율)

**② 마진 과대계산** (배송비 ₩11,350 — 7/20 로그의 실제값, 판매가 ¥44,123)

| | winner(fabstyle, 냉동) | 실제 가능(premiumsneakers) |
|---|---:|---:|
| 매입가 | ₩384,730 | ₩398,410 |
| 배송비 | ₩11,350 | ₩11,350 |
| 부가세 환급 | ₩34,975 | ₩36,219 |
| **마진** | **약 ₩22,500** | **약 ₩10,100** |

→ **마진이 약 ₩12,400 부풀려져 있었다.** 판매가는 그 부풀린 값 기준으로 정해졌다.
(같은 model_no 에 fabstyle ₩348,690 짜리 ace 127892 도 있는데, 몰당 대표 1개만 소싱처가 되므로 여기엔 안 들어갔다)

**③ 옵션 오염** — 사이즈가 `32` · `32(25FW)` · `32(26SS)` · `30X32(25SS)` 처럼 시즌코드가 붙은 채 올라감
(문의 #20 의 원인. 이 문제와는 **별건**이지만 같은 상품에서 겹쳐 보임)

---

## 6. 고장난 곳 — 4군데가 맞물려야 발생

| | 위치 | 무엇이 |
|---|---|---|
| ① | `stock_price_synchronizer_*_merge.py` 대상 조회 `ap.is_active = 1` | 죽은 것은 **갱신 안 함** → 가격·재고 냉동 |
| ② | `reconcile_ensure_group.py:88~96` | 기존 그룹은 **거르는 조건 없음** (신규는 36행에서 거름) |
| ③ | `reconcile_ensure_group.py:269` | 껐어도 **무조건 `is_active=1` 로 되살림** |
| ④ | `resolve_merge.py:201` | winner 를 **오직 매입가로만** 선정 |

①이 냉동을 만들고 · ②③이 죽은 것을 계속 멤버로 유지하고 · ④가 그걸 뽑는다.

**⑤ 설계 구멍**: `duple` 을 소싱으로 쓰기로 정해놓고(의도), **그것들을 갱신하는 장치를 안 만들었다.**

---

## 7. 전체 규모 (2026-07-22 실측)

```sql
-- 라이브 winner 가 죽은 상품인 것 (사유별)
SELECT COALESCE(a.status,'(NULL)') AS 사유, COUNT(*) AS 건수
FROM buyma_listings bl
JOIN source_offerings so ON so.id = bl.winner_offering_id
JOIN ace_products a ON a.id = so.ace_product_id
WHERE bl.is_active = 1 AND bl.buyma_product_id IS NOT NULL AND a.is_active = 0
GROUP BY 사유 ORDER BY 건수 DESC;
```
```
duple      2,464     ← 설계상 '의도'된 것. 다만 갱신 장치가 없음
success      143  ┐
(NULL)        55  ├ 199건 = 규칙 위반 = 명백한 버그
fail           1  ┘
합계       2,663
```

```sql
-- 새로 생기는 추이
SELECT DATE(so.created_at) AS 날짜, COUNT(*) AS 건수
FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
WHERE a.is_active = 0 AND so.is_active = 1
  AND so.created_at >= NOW() - INTERVAL 14 DAY
GROUP BY 날짜 ORDER BY 날짜;
-- 7/13 18 · 7/15 1 · 7/16 23 · 7/17 1 · 7/20 49 · 7/21 541
```

```sql
-- ★ 착수 전 영향분석 (무거움) — 살아있는 것으로 바꾸면 매입가가 얼마나 오르나
SELECT COUNT(*) AS 대상건수,
       SUM(alive_min - win_price) AS 총_부풀림액,
       AVG(alive_min - win_price) AS 평균_부풀림액,
       SUM(alive_min IS NULL)     AS 살아있는소싱_없음
FROM (
  SELECT bl.id,
         (SELECT so.purchase_price_krw FROM source_offerings so
           WHERE so.id = bl.winner_offering_id) AS win_price,
         (SELECT MIN(so2.purchase_price_krw) FROM source_offerings so2
           JOIN ace_products a2 ON a2.id = so2.ace_product_id
           WHERE so2.listing_id = bl.id AND so2.is_active = 1
             AND so2.is_margin_ok = 1 AND a2.is_active = 1) AS alive_min
  FROM buyma_listings bl
  JOIN source_offerings sw ON sw.id = bl.winner_offering_id
  JOIN ace_products aw ON aw.id = sw.ace_product_id
  WHERE bl.is_active = 1 AND bl.buyma_product_id IS NOT NULL AND aw.is_active = 0
) t;
```
`살아있는소싱_없음` = winner 를 바꿀 수 없는 건 → **별도 처리 대상**

---

## 8. 처방 — 정해야 할 것

### A. 급한 것 · 규칙 위반 199건
`duple` 이 아닌 사유(deleted/fail/success)로 죽은 상품이 winner 인 건 **확정 규칙 위반**.
→ 멤버·winner 후보에서 제외 + 연결 정리 + winner 재선정.

### B. 근본 · 로직 4곳
1. `reconcile_ensure_group.py:88~96` 에 신규경로와 **동일한 필터** 추가 → 비대칭 해소
2. 상품을 끄는 상시 5곳에서 **`source_offerings.is_active=0` 동반 처리**
   (`dedup_corrector.py:380`, `buyma_suspended_cleaner.py:298`, `buyma_unpublished_cleaner.py:140`,
    `buyma_low_view_cleaner.py:142`, `buyma_new_product_register.py:1046`)
3. `reconcile_ensure_group.py:269` 의 무조건 되살림 조건화 — **2번만 고치면 무효가 된다**
4. `resolve_merge.py:201` winner 후보에 생사 게이트 추가 여부

### C. 정책 결정 · duple 2,464건 (제일 큼)
- **(C-1) duple 도 재고동기화 대상에 포함** → 가격·재고 최신화, 소싱처로 계속 사용. 동기화 부하 증가
- **(C-2) duple 을 winner 후보에서 제외** → 살아있는 곳만. **판매가 상승 가능성**

⚠️ **C-2 는 라이브 2,464건의 매입처가 한꺼번에 바뀐다.** 위 영향분석 쿼리를 먼저 돌려
"총 부풀림액 / 평균 상승폭 / 대체 소싱 없는 건수"를 확인한 뒤 결정할 것.

### D. 별건이지만 같은 상품에서 보임 · 문의 #20 옵션 정규화
사이즈 값에 시즌코드 제거 규칙 없음(상품명엔 `SEASON_PATTERN` 있음).
`30X32`↔`30/32` 통일은 안전 / `30`↔`30X32` 는 위험 / `インチ` 는 두는 게 나음.

---

## 부록 · 재현용 쿼리 한 벌

```sql
SET @listing_id = 4471;

-- 주문
SELECT * FROM buyma_self_orders WHERE order_id = 34975478;
-- 목록
SELECT * FROM buyma_listings WHERE id = @listing_id;
-- 소싱처 + 생사
SELECT so.*, a.* FROM source_offerings so JOIN ace_products a ON a.id = so.ace_product_id
 WHERE so.listing_id = @listing_id ORDER BY so.purchase_price_krw;
-- 바이마 옵션의 출처
SELECT lo.*, so.source_site, a.is_active, a.status, a.updated_at
 FROM listing_options lo
 LEFT JOIN source_offering_options soo ON soo.id = lo.sourced_offering_option_id
 LEFT JOIN source_offerings so ON so.id = soo.offering_id
 LEFT JOIN ace_products a ON a.id = so.ace_product_id
 WHERE lo.listing_id = @listing_id AND lo.is_active = 1;
-- 죽었는데 재고 있음
SELECT so.source_site, soo.* FROM source_offering_options soo
 JOIN source_offerings so ON so.id = soo.offering_id
 JOIN ace_products a ON a.id = so.ace_product_id
 WHERE so.listing_id = @listing_id AND a.is_active = 0 AND soo.stock_type <> 'out_of_stock';
```
