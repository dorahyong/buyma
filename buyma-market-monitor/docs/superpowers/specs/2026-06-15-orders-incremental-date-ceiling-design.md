# 주문 증분 수집 — "패턴 + 날짜 상한" 설계

작성일: 2026-06-15

## 배경 / 문제

주문 증분 크롤(`crawl-orders`)은 워터마크(직전 실행의 최신 30건 튜플 블록)를 판매 페이지에서
연속 부분수열로 찾아 그 위쪽만 신규로 수집한다([crawler/orders.py](../../../crawler/orders.py)
`find_watermark_boundary`). 효율적이지만 **패턴(워터마크 블록)을 못 찾으면** 셀러의 전체 이력을
재수집해 **중복 행**을 만든다.

2026-06-15 실제 크롤에서 459명 중 4명(`4664581`, `8739277`, `8018512`, `12802104`)이 이 현상으로
과거(최대 2024년)까지 재수집되어 약 2,600행이 중복 삽입되었다(사후 정리 완료). 직전 커밋
`fix(orders): warn on watermark-miss over-collection`은 이 상황을 **경고로 노출**만 했고,
**예방**은 하지 않는다.

근본 원인: BUYMA 판매 페이지의 각 주문 항목에는 **고유 주문 ID가 없다**(상품링크/수량/판매일만 존재).
따라서 "이 주문을 전에 봤는가"를 ID로 판별할 수 없고, 같은 날 동일상품 동일수량의 별개 주문이
정상적으로 존재한다.

## 목표

워터마크 패턴 방식을 **주(主) 경계 탐지로 유지**하면서, 패턴이 빗나가도 **중복이 생기지 않도록**
한다. 핵심 도구는 판매일의 성질이다:

> **판매일은 과거로 새로 생기지 않는다.** 어떤 날짜의 주문 수는 그날이 지나면 (취소가 없는 한)
> 늘지 않는다.

`prev_max`(직전 실행의 최신 판매일)를 안전 가드로 사용한다. `prev_max`는 워터마크에서 직접
도출된다 — 워터마크 signature는 최신순 정렬이라 `prev_max = max(t[0] for t in signature)`이며,
실측상 `signature[0][0]`와 일치한다(검증 완료). 따라서 **스키마 변경이 없다.**

## 비목표

- 고유 주문 ID 확보(BUYMA가 제공하지 않음).
- 취소로 과거 날짜 개수가 줄어든 경우의 자동 삭제(드문 경우, 별도 과제).
- 워터마크 저장 구조(JSON signature) 변경.

## 설계

### 1. 크롤러 — `crawl_seller_orders` (crawler/orders.py)

워터마크가 비어있지 않으면 두 값을 계산한다:
- `prev_max = max(t[0] for t in watermark)` — 직전 최신 판매일(= 폴백 출력 상한 + 경계대조 날짜).
- `wm_min  = min(t[0] for t in watermark)` — 워터마크 블록의 가장 오래된 판매일(= 조기종료 기준).

판매 페이지를 최신→과거로 훑는 루프에 두 가지를 추가한다.

**(a) 조기 종료** — 패턴을 아직 못 찾았더라도, 누적분의 가장 오래된 판매일이 `wm_min`보다
과거가 되면 루프를 멈춘다. 페이지는 판매일 내림차순이다. 기준을 `prev_max`가 아니라 `wm_min`으로
두는 이유: 워터마크 블록은 `prev_max`부터 `wm_min`까지의 날짜에 걸쳐 있으므로, 패턴이 정상
매칭되려면 누적분이 블록 전체(=`wm_min`까지)를 담아야 한다. `prev_max`에서 멈추면 정상 패턴
매칭을 방해한다. `wm_min`을 한 칸 지나도 패턴이 안 맞으면 블록이 깨진 것 → 폴백. 이 조기종료로
패턴이 빗나가도 **워터마크 날짜 구간(보통 수일~수주)까지만 스캔**하고 몇 년치를 긁지 않는다.
(패턴 매칭 검사는 조기종료 검사보다 먼저 실행되므로 정상 매칭 경로는 영향받지 않는다.)

**(b) 폴백 출력 상한** — 패턴 매칭에 실패한 채 루프가 끝나면(패턴 미스 = 과대수집 상황),
반환 주문을 `sale_date >= prev_max`로 제한한다.

동작 표:

| 상황 | 반환 | overcollected |
|---|---|---|
| 패턴 매칭됨 (정상) | `accumulated[:boundary]` (현행 동일) | False |
| 패턴 미스 (폴백) | `[e for e in accumulated if e.sale_date >= prev_max]` | True |
| 빈 워터마크 (신규 셀러) | 전체 수집 (현행 동일) | False |

함수 반환에 `overcollected: bool` 플래그를 추가한다. 기존 반환은
`(new_orders, new_watermark, warnings, pages_scanned)` → `(new_orders, new_watermark, warnings,
pages_scanned, overcollected)`. 기존 경고(watermark not matched ...)는 유지한다.

판매일 비교는 문자열 비교를 사용한다. 형식이 `YYYY/MM/DD`로 고정폭이라 사전식 비교가 곧
시간순 비교다.

### 2. 전달 경로 — `crawl_all_orders_with_factory` / `on_seller_done`

`on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings)` 콜백에
`overcollected`를 추가한다 →
`on_seller_done(sid, new_orders, new_watermark, pages_scanned, warnings, overcollected)`.
`crawl_all_orders_with_factory`가 크롤러 반환의 플래그를 콜백에 전달한다.

### 3. 저장 레이어 — `storage/orders_repo.py` 헬퍼 + `main.py`

신규 헬퍼:

```
insert_orders_bounded(conn, seller_id, new_orders, prev_max, overcollected) -> int
```

- `overcollected=False` → `insert_orders(conn, new_orders)` 그대로 호출(현행 동작). 삽입 수 반환.
- `overcollected=True` → 경계날만 개수대조:
  - `sale_date > prev_max`인 주문 → 전부 삽입(확실히 신규).
  - `sale_date == prev_max`인 주문 → 그 개수 `C`와 DB 기존 개수
    `E = SELECT COUNT(*) FROM orders WHERE seller_id=? AND sale_date=?`를 비교해
    `max(0, C - E)`건만 삽입(중복 0·누락 0).
  - `sale_date < prev_max`인 주문은 크롤러가 이미 제외했으므로 없음(방어적으로 무시).

`main.py`의 `on_seller_done`은 `overcollected`를 받아 `prev_max`(보유 워터마크에서 도출)와 함께
이 헬퍼를 호출한다. 멀티워커 쓰기는 기존처럼 `db_lock`으로 직렬화한다.

### 4. 데이터 흐름

```
crawl-orders
  → 워커: crawl_seller_orders(watermark) → (new_orders, new_wm, warnings, pages, overcollected)
  → on_seller_done(... overcollected):
      with db_lock:
        insert_orders_bounded(conn, sid, new_orders, prev_max, overcollected)
        upsert_watermark(conn, sid, new_wm, ...)
```

### 5. 에러 처리

- 빈 워터마크(신규 셀러·`--full-rescan`)는 `overcollected=False`로 처리되어 경계대조를 타지 않는다
  (전체 수집이 정상).
- DB 쓰기는 기존과 동일하게 autocommit(WAL) — 부분 적재로 깨지지 않음.

### 6. 테스트 (TDD)

**크롤러 (tests/test_orders_watermark.py 확장):**
- 폴백 시 반환이 `sale_date >= prev_max`로 제한되고 `overcollected=True`인지.
- 누적 최오래 판매일이 `prev_max` 미만이 되면 조기 종료(불필요한 페이지 미수집)하는지.
- 패턴 정상 매칭 시 `overcollected=False`이고 반환 불변인지.
- 빈 워터마크 시 전체 수집 + `overcollected=False`인지.

**저장 헬퍼 (tests/test_orders_repo.py 확장):**
- `overcollected=False` → 전량 삽입.
- `overcollected=True`, 경계날 DB 기존 5건·이번 7건 → 2건만 삽입(중복 0).
- `overcollected=True`, `sale_date > prev_max`는 전량 삽입(누락 0).
- 경계날 이번 수집 ≤ DB 기존 → 0건 삽입.

## 영향 범위 요약

- `crawler/orders.py` — 루프 조기종료 + 폴백 상한 + `overcollected` 반환
- `crawler/orders.py` `crawl_all_orders_with_factory` + `on_seller_done` 시그니처 — 플래그 1개 추가
- `main.py` — `on_seller_done`에서 `insert_orders_bounded` 호출(폴백 시 경계대조)
- `storage/orders_repo.py` — `insert_orders_bounded` 헬퍼
- 스키마 변경 없음

## 효과

- 패턴 정상: **완전히 동일**(회귀 없음).
- 패턴 미스: 과거 전체 재수집 → **경계날 한 칸으로 축소, 중복 0·누락 0**, 페이지 스캔도 경계까지만.
