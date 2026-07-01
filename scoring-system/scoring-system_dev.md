1. 바이마에 올릴 대상으로 모니터링하고있는 사이트가 확대됨에 따라, 앞으로는 5만개보다 한참 더 많은 숫자의 상품을 구비하는 것을 목표로 할 예정입니다.
2. 그 상태가 되었을때, 상품에 대해서 평가기준을 만들고 그 기준에 따라서 어떤 상품을 지우고 어떤 상품을 새로 올릴 것인지, 그리고 출품대기중인 상품에 대해서는 어떤 기준으로 먼저 올릴 대상을 선정할지를 정하는 지수에 대한 필요성이 대두되었습니다.
3. 해당 점수를 기준으로 출품중인 상품이 더 높으면 유지, 출품대기중인 상품의 점수가 더 높으면 교체 하게되고 이 행위를 매일 자동으로 반복하게 처리할 예정입니다.
4. 예상일일마진액 이라는 값으로 출품중 상태의 상품들에 대해서 평가를 내리는 기준이 가장 중요한 파트이고 나머지 파트는 바이마데이터모니터페이지 기획이 되고나면 그걸 반영하는게 더 나을 것 같습니다. 예상일일마진액항목은 출품관리 페이지에도 항목추가를 부탁드립니다.


# BUYMA 상품 스코어링 시스템 — 개발 명세서

작성일: 2026-05-27
참조 자료: 기획·의사결정 근거 문서 (별도 페이지)

---

## 목차

1. 개요
2. 상태 모델
3. 점수 공식
4. 시간 감점 함수
5. Swap / Fill 알고리즘
6. 사이클 및 API 운영
7. 데이터 입력
8. 데이터 모델
9. Parameter 명세
10. 컴포넌트 책임 모델
11. Future Work

---

## 1. 개요

### 1.1 목적 (KPI)

**일일 마진액(총 이익) 극대화** — 5만 슬롯 전체가 하루에 발생시키는 순이익의 합을 최대화.

점수 시스템은 본질적으로 각 상품의 "**예상 일일 마진액(원/일)**"을 예측하는 프록시.

### 1.2 제약 조건

| 항목 | 값 |
|---|---|
| BUYMA 동시 출품 슬롯 | 5만 |
| 상품 API (등록/수정/삭제) | 24시간당 2,500회 |
| 전체 API | 시간당 5,000회 |
| 보유 상품 풀 | 현재 약 12만, 미래 100만+ |

### 1.3 핵심 동작 흐름

```
[Fill 단계]  출품중 < 50,000 슬롯
  → 출품대기중 Top N을 빈 슬롯에 즉시 등록 (등록만, 삭제 없음)

[Swap 단계]  출품중 = 50,000 슬롯
  → 점수 역전 시 출품대기 Top ↔ 출품중 Bottom 매칭 swap
```

### 1.4 운영 원칙

| # | 원칙 |
|---|---|
| O1 | 모든 finetune 가능한 숫자는 `parameters` 테이블에서 일괄 관리 (코드 하드코딩 금지) |
| O2 | Fallback 데이터를 가짜로 채우지 않음. 신호 부재 자체가 정보. |
| O3 | 점수는 KPI(일일 마진액)와 같은 단위(원/일)로 해석 가능해야 함 |

**💬 해석 — 시스템 제작 3대 규칙**

- **O1 (유연하게)** — 유예기간·가중치·임계값처럼 나중에 조정할 숫자를 코드에 박지 않고 DB(`parameters` 테이블)에 모아둠. 운영자가 개발자·배포 없이 값만 바꿔 조정(예: "유예기간 30→45일"을 테이블 수정만으로 다음날 적용).
- **O2 (정직하게)** — 찜·조회·판매 기록이 없는 상품에 없는 값을 평균·임의값으로 억지로 메우지 않음. "신호 없음" 자체가 정보(아직 반응 없는 상품)라서 가짜로 덮으면 판단이 왜곡됨. 단, 진짜 부족 시 근거 있는 통계 추정(3.2)은 사용 — '가짜 채우기'와 다름.
- **O3 (해석 가능하게)** — 점수가 추상 점수(70점)가 아니라 실제 '원/일' 단위. ① "점수 1,200 = 하루 1,200원 벌 듯"으로 바로 읽힘, ② 출품중·대기중·retired 3개 풀 점수가 같은 단위라 부등호로 바로 비교(3.4).

**💡 O3 직관 (개념용)** — 같은 '원/일'로 환산하면 자주 안 팔려도 마진 큰 상품이 이길 수 있음:

| 상품 | 가정 | 하루당 환산 |
|---|---|---|
| A | 월 1회 × 3만원 | ≈ 1,000원/일 |
| B | 월 3회 × 5천원 | ≈ 500원/일 |

둘을 '원/일'로 맞췄기에 "A > B" 비교 가능. ⚠️ 단, 시스템은 판매수로 직접 계산하지 않음 — 판매가 희박해(7.3) 찜·조회·마진 신호로 추정. 실제 계산은 **3번 점수 공식**.

---

## 2. 상태 모델

### 2.1 상품 상태

| 상태 | 의미 | 본 시스템 처리 |
|---|---|---|
| **출품중** | BUYMA에 실제 업로드된 상태 | 점수 계산 + Swap-out 대상 |
| **출품대기중 (fresh)** | 자격 있고 자리만 기다리는 상태 | 점수 계산 + Swap-in / Fill 후보 |
| **retired** | Swap-out 된 상태, 모든 값 동결 | 점수 동결. fresh 풀 고갈 시 부활 후보 |
| 확인필요 | 자격 미달 (검수 필요) | 본 시스템 외부 워크플로우 |
| 품절 | 재고 소진 | 본 시스템 외부 워크플로우 |
| 최저가확보불가 | 가격경쟁력 부족 | 본 시스템 외부 워크플로우 |

**💬 해석 — 우리 실제 데이터와 매핑**

| spec 상태 | 우리 실제 |
|---|---|
| 출품중 | `buyma_listings.is_published=1` (단일권위 FILL로 53,310건) ✅ |
| 품절 | 품절 처리(재고 API '출품정지중' 전환) ✅ 맞물림 |
| 최저가확보불가 | fast_price "마진 마이너스 → 출품정지" ✅ 맞물림 |
| 음수마진 제외(2.3) | fast_price가 이미 음수마진 처리 중 ✅ 철학 일치 |
| **출품대기중 (fresh)** | draft listing(`is_published=0`)에 대응하나, "자격 검수 통과" 개념은 미정의 ⚠️ |
| **retired** (벤치+값 동결) | 현재 없는 신개념 — `frozen_snapshots` 테이블 신설 필요 ⚠️ |
| **확인필요** | 우리 어디에 대응되는지 확인 필요 ⚠️ |

→ 출품중·품절·최저가확보불가·음수마진은 **이미 우리 운영과 맞물림**. 출품대기중·retired·확인필요는 **새로 정의·구축** 필요.

### 2.2 상태 전이

```
신규 sourcing → 확인필요 (자격 검수)
              ↓ 통과
              fresh 출품대기 (점수 계산 시작)
              ↓ Fill 또는 Swap-in
              출품중
              ↓ Swap-out
              retired_pool (값 동결)
              ↓ fresh 풀 고갈 시 부활
              출품중

출품중 → 품절 / 최저가확보불가 : 본 시스템 외부 처리
```

### 2.3 자동 제외 조건 (모든 풀 공통)

- **음수마진**: 음수 마진액 상품은 모든 후보 풀에서 제외 (`margin_floor_policy = "exclude"`)
- **음수마진 검증 시점**: 신규 진입 / 메인 사이클 / Swap 직전

---

## 3. 점수 공식

모든 점수는 단위 **원/일 (예상 일일 마진액)**.

### 3.1 출품중 (Listed)

```
점수 = (직접신호점수 × 마진액)
     + (α × 마진액 × 시간감점(t))

직접신호점수 = w_찜·일평균_찜수
             + w_조회·일평균_조회수
             + w_장바구니·일평균_장바구니수
             + w_판매·(총_판매수 / 등록기간)

α          = baseline_alpha (P4)
시간감점(t) = piecewise_linear_decay(t)  ← 4번 참조
일평균_X   = 누적_X / 등록기간(일)         ← Future Work(11.1): 주기 스냅샷 + 윈도우 평균 도입 예정
```

**설계 의미:**
- **(1) 직접신호 부분**: 시간감점 면제 → 지속적으로 신호 받는 상품은 영원히 유지
- **(2) 베이스라인 부분**: 시간감점 적용 → 신호 없는 상품은 자연 도태

### 3.2 출품대기중 (Waiting — fresh)

```
점수 = prior(brand, category) × 마진액 × 1.0
```

#### 3.2.1 prior 산출 — 계층적 Bayesian Shrinkage

데이터 가용성에 따라 4단계 fallback:

```python
def prior(brand, category):
    n_bc = count(출품중 + brand + category)
    n_b  = count(출품중 + brand)
    n_c  = count(출품중 + category)

    # ① (브랜드 × 카테고리) 표본 충분
    if n_bc >= min_cohort_samples:
        return shrink(n_bc, avg_bc, kappa_bc, brand_avg)

    # ② brand 표본 충분
    if n_b >= min_brand_samples:
        return shrink(n_b, brand_avg, kappa_b, category_avg)

    # ③ category 표본 충분
    if n_c >= min_category_samples:
        return shrink(n_c, category_avg, kappa_c, global_avg)

    # ④ 모두 부족
    return global_avg


def shrink(n, sample_avg, kappa, parent_avg):
    return (n * sample_avg + kappa * parent_avg) / (n + kappa)
```

**💬 해석 — prior / shrink**

- `prior()` — 대기중 상품의 "팔릴 가능성" 추정. 표본이 충분한 **가장 좁은 단위부터** 시도하고 부족하면 넓은 단위로 물러남: ① 브랜드×카테고리 → ② 브랜드 → ③ 카테고리 → ④ 전체평균.
- `shrink()` — 표본 평균을 그대로 안 믿고 상위 평균과 섞음. 표본 n이 **작을수록 상위평균 쪽으로 당겨짐**(과신 방지), n 크면 자기 평균을 더 신뢰.
- 💡 예: "구찌 신발 2개"처럼 표본 적은 데이터는 구찌 전체평균과 섞어 안정화.

#### 3.2.2 카테고리 정규화

원본 카테고리 컬럼은 한국어·영어·혼합 표기 3가지 체계가 혼재(출품중 기준 1,109개 고유값, depth 2~4 혼재)하므로 정규화 필수.

**정규화 규칙: depth 2 + 다국어 통합**

- 원본: `'MEN > CLOTHING > T-SHIRTS'`, `'패션의류 > 남성의류 > 티셔츠'`, `'MEN > 의류 > 티셔츠 > 반팔 티셔츠'`
- depth 2 추출 후 다국어 통합 → 모두 `'MEN_CLOTHING'`
- 결과: 약 50개 정규화 카테고리 키 (Top 코호트 표본 4,000~5,000개)
- 매핑은 `category_normalization_map`(W10)으로 운영자 관리
- 결측 상품(0.00% — 사실상 없음)은 ②번 brand_prior로 자동 fallback

#### 3.2.3 브랜드 정규화

- 원본 데이터에 **브랜드명 컬럼 존재** (출품중 결측률 0.26%)
- 기본 정규화: `upper().strip()` → `ADIDAS = adidas = Adidas`
- **라인은 분리 유지**: `GUCCI ≠ GUCCI KIDS`, `MAISON MARGIELA ≠ MM6 MAISON MARGIELA`, `ADIDAS ≠ ADIDAS X WALES BONNER` (라인별 가격대·소비자층 차이가 prior에 그대로 반영되도록)
- 변형 표기 매핑(`MAISONKITSUNE` ↔ `MAISON KITSUNE` 등)은 `brand_normalization_map`(W4)으로 점진 추가
- 일본어/한국어 잔존 표기는 거의 없음 (출품중 기준 1개)

### 3.3 retired_pool

```
점수 = swap-out 시점의 동결 스냅샷
```

- 등록기간, 누적 신호, 마진액, 시간감점, brand_prior 모두 동결
- 부활 시에도 동결값 그대로 (등록일 reset 없음)
- 마진액이 음수로 변하면 자동 제외 (`margin_floor_policy`)

### 3.4 비교 단위 보장

3개 풀 모두 단위가 **원/일**이므로 단순 부등식 비교로 swap 판정 가능. 별도 정규화 불필요.

---

## 4. 시간 감점 함수

### 4.1 곡선 정의 (조각선형)

```python
def 시간감점(t):
    if t <= grace_period_days:           # 30일
        return 1.0
    if t <= decay_threshold_days:        # 60일
        # 1.0 → P3a 선형 하락
        return 1.0 - (1.0 - decay_threshold_value) * (t - grace_period_days) / (decay_threshold_days - grace_period_days)
    if t <= decay_zero_days:             # 90일
        # P3a → 0 급경사 선형 하락
        return decay_threshold_value * (decay_zero_days - t) / (decay_zero_days - decay_threshold_days)
    return 0.0                           # 베이스라인 보호 해제
```

**💬 해석 — 시간감점(t)**

- 등록 후 경과일 `t`에 따라 **베이스라인을 깎는 배율** 반환 (1.0 = 안 깎음, 0 = 완전 제거).
- 0~30일 `1.0`(유예) → 30~60일 `1.0→0.70` 완만 하락 → 60~90일 `0.70→0` 급경사 → 90일+ `0`.
- 3단계 = **보호 → 관찰 → 결단**. 직접신호 부분엔 미적용(4.3).

### 4.2 일자별 값 (기본 parameter 기준)

| 등록기간 t | 시간감점 | 의미 |
|---|---|---|
| 0 ~ 30일 | 1.0 | 유예기간 (신규 데이터 수집 보호) |
| 60일 | 0.70 | 임계점 진입 |
| 75일 | 0.35 | 급경사 진행 중 |
| 90일+ | 0 | 베이스라인 보호 해제 (직접신호로만 평가) |

### 4.3 적용 범위

- **적용**: 베이스라인 α 부분
- **미적용**: 직접신호점수 부분 (지속 성과 = 영원 유지)

---

## 5. Swap / Fill 알고리즘

**💬 해석 — 5번 전체 (점수 → 실제 행동)**

대표님 의도 line 3("높으면 유지·낮으면 교체, 매일 자동")의 실체.

- **흐름**: 매일 ① 점수 갱신 → ② API 잔량 확인(부족하면 그날 skip) → ③ 빈 슬롯 있으면 **Fill**(등록만), 꽉 찼으면 **Swap**(1:1 교체).

**왜 이렇게 설계?**
- **Fill/Swap 분리** — 자리 비었으면 삭제 없이 등록만 → 삭제 API 절약. 꽉 차야 1:1 교체.
- **min_swap_margin(S1)** — 조금 높다고 바꾸면 진동(churn)→API 낭비. 일정 차이 이상일 때만.
- **cooldown(S2=14일)** — 방금 바꾼 걸 또 바꾸는 핑퐁 방지.
- **점수 역전 시에만** — 매일 조금씩 더 나은 5만으로 수렴.

**📎 우리 실제**: swap-out/in = BUYMA 삭제/등록(reconcile·PS API 가능). 단 "listings 권위" 전제 → 현재 dual-state라 코드전환(step6) 후라야 listings 기반 swap이 안전.

### 5.1 매 사이클 실행 순서

```python
def main_cycle():
    # 1. 점수 갱신 (API 호출 없음)
    update_brand_category_priors()       # 일 1회 일괄 재계산
    recompute_scores_listed()
    recompute_scores_fresh()

    # 2. API 잔량 확인 — 본 시스템은 최저 우선순위 (6.3 참조)
    budget = get_remaining_budget_for_scoring()    # 잔량 (호출 수)
    if budget < min_swap_batch_size * 2:           # A9 — 최소 보장 미달
        log("API budget too low, skipping cycle")
        sns.alert("scoring main cycle skipped: api budget low")
        return

    # 3. Fill 또는 Swap (잔량 안에서)
    open_slots = 50000 - count_listed()
    if open_slots > 0:
        # 1 fill = 1 API call (등록만)
        max_fills = min(open_slots, budget)
        fill_open_slots(max_fills)
    else:
        # 1 swap = 2 API calls (등록 + 삭제)
        max_pairs = min(swap_batch_size, budget // 2)
        swap_top_k(max_pairs)
```

**💬 해석 — main_cycle (매일 1회)**

- ① **점수 갱신**(출품중·대기중, API 호출 0) ② **API 잔량 확인** — 너무 적으면 그날 **skip + 알림** ③ 빈 슬롯 있으면 **Fill**(1건 = API 1회), 꽉 찼으면 **Swap**(1쌍 = API 2회). 항상 **남은 잔량 안에서만**.

### 5.2 Fill 단계

```python
def fill_open_slots(n):
    # 빈 슬롯 채우기 (등록만, 삭제 없음). 1 fill = 1 API call.
    # 사이클 진행 중에도 잔량을 다시 확인하여 사이클 중간 중단 가능 (6.4)
    candidates = fresh_pool.top_k(n, exclude_cooldown=False)
    for product in candidates[:n]:
        if get_remaining_budget_for_scoring() < cycle_remaining_threshold:  # A10
            break  # 잔량 부족 → 부분 결과 commit 후 중단
        buyma_api.register(product)
        product.status = '출품중'
        # swap_trigger 미적용. 음수마진 / 자격 미달만 제외.
```

**💬 해석 — fill_open_slots**

- 빈 슬롯을 **대기중 Top으로 채움(등록만, 삭제 없음)**. 진행 중에도 잔량 떨어지면 **중단(부분 결과 저장)**. 자리가 비어 있으니 점수 비교(swap_trigger) 없이 채우되 **음수마진·자격미달만 제외**.

### 5.3 Swap 단계

#### 5.3.1 Trigger

```python
def swap_trigger(out_score, in_score, out_product, in_product):
    return (
        in_score >= out_score + min_swap_margin    # S1
        and not in_cooldown(out_product, swap_cooldown_days)   # S2
        and not in_cooldown(in_product, swap_cooldown_days)
    )
```

**💬 해석 — swap_trigger (교체 가부 판정)**

- 교체하려면 **둘 다** 충족: ① 들어올 점수 ≥ 나갈 점수 **+ 5원(S1)** ② 나갈·들어올 상품이 **둘 다 cooldown 아님(S2)**.
- = **"확실히 더 나을 때만, 핑퐁 아닐 때만"** 바꾼다.

#### 5.3.2 Top-K 배치 매칭

```python
def swap_top_k(max_pairs):
    """max_pairs = main_cycle에서 잔량 기반으로 조정된 상한 (5.1, 6.3.4)."""
    bottom_listed = listed_pool.bottom_k(max_pairs * 2)   # 여유분 포함
    top_waiting  = waiting_candidates(max_pairs * 2)

    pairs_done = 0
    for out_p, in_p in greedy_match(bottom_listed, top_waiting):
        if pairs_done >= max_pairs:
            break
        if get_remaining_budget_for_scoring() < cycle_remaining_threshold:  # A10
            break  # 사이클 도중 잔량 소진 → 중단
        if not swap_trigger(out_p.score, in_p.score, out_p, in_p):
            continue
        execute_swap(out_p, in_p)
        pairs_done += 1


def waiting_candidates(n):
    # fresh 풀 우선
    fresh = fresh_pool.top_k(n)
    # fresh 부족 시 retired_pool 부활 (S6 — fallback_trigger_multiplier)
    if len(fresh) < swap_batch_size * fallback_trigger_multiplier:
        fresh += retired_pool.top_k(n - len(fresh))
    return fresh
```

**💬 해석 — swap_top_k / waiting_candidates**

- `swap_top_k` — **출품중 최하위 ↔ 대기중 최상위**를 greedy 매칭, `swap_trigger` 통과분만 교체, 잔량 소진 시 중단.
- `waiting_candidates` — 후보는 **fresh 우선**, fresh가 부족하면 **retired 부활분 추가**(S6).

#### 5.3.3 Swap 실행

```python
def execute_swap(out_p, in_p):
    # A 상품 (출품중 → retired_pool, 모든 값 동결)
    out_p.frozen_snapshot = take_snapshot(out_p)
    out_p.status = 'retired_pool'
    buyma_api.delete(out_p)

    # B 상품 (출품대기 → 출품중)
    if in_p.status == 'retired_pool':
        # 부활 — 동결값 유지, 등록일 reset 없음
        pass
    else:
        # fresh — 등록일 = 오늘, 신호 = 0, 유예기간 시작
        in_p.registered_at = today()
    in_p.status = '출품중'
    buyma_api.register(in_p)

    # Cooldown 등록
    record_cooldown(out_p, in_p, swap_cooldown_days)
```

**💬 해석 — execute_swap (실제 교체 실행)**

- **나가는 상품**: 현재값을 스냅샷으로 **동결** → `retired` → **BUYMA 삭제**.
- **들어오는 상품**: `출품중` → **BUYMA 등록**. (fresh면 **등록일=오늘·신호0**으로 유예 시작 / retired 부활이면 **동결값 유지, 등록일 reset 없음**.)
- 마지막에 둘 다 **cooldown 등록**(당분간 재교체 금지).

---

## 6. 사이클 및 API 운영

### 6.1 메인 사이클

- **스케줄**: 매일 04:00 (`C1 = "0 4 * * *"`)
- **트리거**: BUYMA 데이터 ETL 완료 직후
- **타임아웃**: 30분 (`C3`)
- **순서**: 5.1 참조

### 6.2 Incremental 업데이트

메인 사이클 외 다음 이벤트 발생 시 즉시 처리 (`C2 = true`):

| 트리거 | 동작 |
|---|---|
| 신규 sourcing 진입 (확인필요 통과) | 그 상품만 점수 계산 → fresh 풀 등록 |
| 운영자 수동 트리거 | 강제 swap / parameter 변경 등 |
| BUYMA "품절" 알림 | 해당 슬롯 비움 → 다음 사이클에 fill 후보 |

→ 메인 사이클과 충돌 방지: lock/queue (SQS) 사용.

### 6.3 API 예산 배분 — 외부 우선 + 본 시스템 잔량 기반

**핵심 원칙:** BUYMA 상품 API (24h 2,500회)는 **여러 시스템이 공유**하는 자원. 본 스코어링 시스템은 **최저 우선순위**로 동작하며, 외부 작업이 먼저 소진한 뒤 남은 잔량 안에서만 swap/fill을 수행한다.

#### 6.3.1 우선순위 모델

| Priority | 호출 주체 | 예시 |
|---|---|---|
| 1 (높음) | **외부 시스템** | 최저가 업데이트, 품절 삭제, 가격 재조정, 정합성 보정 등 |
| 2 (중간) | 운영자 수동 작업 | 강제 swap, 강제 retire 등 |
| 3 (낮음) | **본 스코어링 시스템 자동 swap/fill** | 메인 사이클·incremental |

→ 본 시스템은 외부·수동 작업이 모두 끝났다고 가정한 뒤 남은 잔량만큼만 동작.

#### 6.3.2 통합 API 사용량 트래킹

모든 BUYMA API 호출은 공유 카운터 `api_usage_log`에 기록 (외부 시스템도 동일 카운터 사용).

```sql
CREATE TABLE api_usage_log (
    id            BIGINT PRIMARY KEY,
    called_at     DATETIME,
    caller        VARCHAR(64),    -- 'external_lowest_price', 'external_stockout',
                                  -- 'scoring_main_cycle', 'scoring_incremental',
                                  -- 'manual_admin', ...
    api_method    VARCHAR(32),    -- 'product_register', 'product_update', 'product_delete'
    product_id    BIGINT,
    success       BOOLEAN,
    INDEX (called_at, caller)
);
```

→ 외부 시스템이 별도면 공유 DB 또는 메시지 큐로 통합. 통합 불가 시 BUYMA 응답 헤더의 잔량 정보 활용.

#### 6.3.3 잔량 계산 로직

```python
def get_remaining_budget_for_scoring():
    """본 스코어링 시스템이 안전하게 사용 가능한 API 호출 수."""
    used_today = api_usage_log.count_today()                # 외부 + 본 시스템 통합
    hard_remaining = api_daily_limit_product - used_today   # 절대 잔량

    # 안전 마진: 외부 시스템의 일 후반 추가 사용분 예약
    safety_reserve = api_daily_limit_product * api_safety_margin_pct
    external_reserve = expected_external_usage_remaining()  # A8 — 외부 시스템 예상 잔여 사용량

    available = hard_remaining - safety_reserve - external_reserve
    return max(0, available)


def expected_external_usage_remaining():
    """오늘 남은 시간 동안 외부 시스템이 추가로 사용할 것으로 예상되는 호출 수.

    구현: 최근 7일 동안의 외부 시스템 시간대별 평균 사용량 × 남은 시간.
    """
    hour_now = datetime.now().hour
    daily_avg_external = api_usage_log.weekly_avg_by_caller_prefix('external_')
    remaining_share = daily_avg_external * (24 - hour_now) / 24
    return remaining_share
```

#### 6.3.4 동적 batch size 조정

메인 사이클·incremental은 잔량을 보고 `swap_batch_size`를 동적으로 축소.

```python
def main_cycle():
    budget = get_remaining_budget_for_scoring()

    # 1 swap = 2 API calls (등록 + 삭제)
    # 1 fill  = 1 API call  (등록만)
    max_swap_pairs = budget // 2

    actual_batch = min(swap_batch_size, max_swap_pairs)   # S3 vs 잔량

    if actual_batch < min_swap_batch_size:                # A9 — 최소 보장량
        log("API budget too low, skipping cycle")
        sns.alert("scoring main cycle skipped: api budget low")
        return

    swap_top_k(actual_batch)
```

#### 6.3.5 표준 운영 시 예상 호출량 (참고)

(외부 시스템 평균 사용량 가정치 — 실측 후 조정)

| 호출 주체 | 일 평균 호출 (가정) | 비고 |
|---|---|---|
| 외부 — 최저가 업데이트 | 800 | 가격 변경 발생분 |
| 외부 — 품절 처리 | 200 | 재고 소진 알림 |
| 외부 — 기타 정합성 보정 | 200 | DB 동기화 등 |
| **외부 소계** | **1,200** | 약 48% |
| 운영자 수동 | 100 | 가변 |
| 안전 마진 (A4=40%) | 1,000 | |
| **잔량 — 본 시스템 가용** | **약 200** | swap_batch_size ≤ 100쌍 |

→ 외부 사용량 측정 후 안전 마진(A4) 또는 본 시스템 swap_batch_size를 finetune.

### 6.4 Rate Limiting & Retry

| 시나리오 | 동작 |
|---|---|
| 매 사이클 시작 전 잔량 체크 (6.3.3) | budget < `min_swap_batch_size × 2` 시 사이클 skip + 알림 |
| 사이클 도중 잔량 < `cycle_remaining_threshold` (A10) | 남은 매칭 중단, 부분 결과 commit |
| 시간당 5,000 한도 근접 | token bucket으로 호출 지연 |
| 일 2,500 한도 근접 (외부 포함) | 본 시스템 모든 신규 호출 중단. 다음날 재개. |
| API 호출 실패 (네트워크) | 지수 백오프 retry (`A5 = 3회`, `A6 = 2초 시작`) |
| API 호출 거부 (한도 초과) | 큐잉 후 다음날 처리. cooldown은 시도일자 기준 부여. |

---

## 7. 데이터 입력

### 7.1 출처

- 본 시스템은 **회사 내부 원본 DB**의 상품 마스터 테이블을 직접 읽어 동작.
- 구체적 DB 스키마·접근 방식은 개발자가 자체 환경에 맞게 결정.
- 기획 단계에서 활용한 비개발자 모니터링 페이지는 본 명세서의 데이터 출처가 아님 (참고용).

### 7.2 가용 컬럼

| 컬럼명 | 활용 |
|---|---|
| 상태 | 풀 분류 |
| 상품명(일본어) | 메타 |
| **브랜드명** | 브랜드 정규화 입력 (대소문자 통합, 결측 0.26%) |
| **카테고리** | 코호트 prior의 ①번 레이어 (depth 2 정규화, 결측 0.00%) |
| 총 조회수 / 장바구니수 / 찜수 | 직접신호 누적값 |
| 일평균 조회수 / 장바구니수 / 찜수 | 일평균 (사이트가 사전 계산) |
| 바이마 최저가 / 출품가능 최저가 / 바이마 출품가 | 가격 (JPY) |
| **기대마진** | 별도 로직으로 이미 검증된 값. 원 DB의 정규화된 마진액(원)·마진율(%) 컬럼을 그대로 사용. |
| 총 판매수 / 총 판매금액 | 실제 판매 결과 |
| 상품출처 | 소싱 다양성 |
| 바이마 등록일 / 등록기간(일) | 시간 감점 입력 |
| 바이마 구매기한 | 만료 관리 |

### 7.3 데이터 sparsity 특성 (운영 가이드)

현재 시점 출품중 50K 기준 신호 가용성:

| 신호 | 비-결측·비-0 비율 | 비고 |
|---|---|---|
| 마진액 / 마진율 | 100% | 항상 사용 가능 |
| **브랜드명** | **99.74%** | prior 코호트 키 (대소문자 정규화) |
| **카테고리** | **100.00%** | prior 코호트 키 (depth 2 정규화) |
| 조회수 > 0 | ~50% | 주력 demand 신호 |
| 찜수 > 0 | ~5% | 강한 신호이지만 sparse |
| 장바구니수 > 0 | ~0.4% | 변별력 낮으나 강한 신호 |
| 판매수 > 0 | ~0.5% | 직접 KPI 신호 |
| 인기순 순위, 동일품번 컬럼, 최근7일 조회수 | **0% (컬럼만 존재)** | 현재 미사용 |

**중요**: 월 판매 약 150건 (상품당 0.0001건/일 수준) → 매우 sparse한 환경. ML 회귀 모델 부적합. 본 시스템의 곱셈 구조 + 베이스라인 보호가 sparse 환경 최적화.

**💬 해석 — 우리 실제 컬럼별 보유 현황**

| 입력 | 우리 보유? |
|---|---|
| 상태 | `is_published` 등 ✅ |
| 브랜드명 / 카테고리 | `brand_name` / `category_id` ✅ (단 depth2 정규화는 신규) |
| 기대마진(마진액·율) | ✅ 보유 |
| 가격(최저가·출품가능가·출품가) | ✅ fast_price가 관리 |
| 등록일 / 등록기간 | ✅ 게시일수 트래킹 |
| 상품출처 | `source_site` ✅ |
| 구매기한 | ✅ 출품 유효기간 관리 운영중 |
| **찜·조회·장바구니·판매 (+일평균)** | ⚠️ BUYMA 성과신호 = ETL/모니터페이지 의존(**미구축**). 운영 DB 적재 여부 확인 필요 |

→ 마진·가격·브랜드·카테고리·등록기간·출처·구매기한은 **이미 보유** → 점수의 "마진 부분"은 지금도 가능. **찜·조회·판매 성과신호만 미구축 ETL에 의존.** (7.3의 가용률은 기획 시점 측정값 — 현재 DB에서 동일한지 별도 확인 필요)

---

## 8. 데이터 모델

### 8.1 핵심 테이블

#### `products` (마스터)

```sql
CREATE TABLE products (
    id                        BIGINT PRIMARY KEY,
    buyma_product_id          VARCHAR(64) UNIQUE,
    name_ja                   TEXT,
    name_ko                   TEXT,
    brand_raw                 VARCHAR(128),
    brand_normalized          VARCHAR(128),
    category                  VARCHAR(64),       -- 도입 예정
    status                    ENUM('출품중', '출품대기중_fresh', 'retired_pool',
                                   '확인필요', '품절', '최저가확보불가'),
    registered_at             DATE,
    registration_days         INT,               -- 등록기간(일)
    cum_likes                 INT,               -- 누적 찜수
    cum_views                 INT,
    cum_carts                 INT,
    cum_sales                 INT,
    cum_sales_amount          BIGINT,
    buyma_lowest_price_jpy    INT,
    available_lowest_price_jpy INT,
    listed_price_jpy          INT,
    margin_krw                INT,               -- 마진액 (원)
    margin_pct                DECIMAL(5,2),      -- 마진율 (%)
    source_count              INT,
    price_updated_at          DATETIME,
    sourcing_updated_at       DATETIME,
    last_signal_update_at     DATETIME,
    created_at                DATETIME,
    updated_at                DATETIME,
    INDEX (status),
    INDEX (brand_normalized, category)
);
```

#### `score_index_listed` / `score_index_fresh` / `score_index_retired`

```sql
CREATE TABLE score_index_listed (
    product_id    BIGINT PRIMARY KEY REFERENCES products(id),
    score         DECIMAL(12, 4),    -- 원/일
    calculated_at DATETIME,
    INDEX (score DESC)
);
-- 동일 구조로 _fresh, _retired 테이블 별도
```

→ Top-K / Bottom-K 조회를 O(log N)에 가능.

#### `cohort_priors`

```sql
CREATE TABLE cohort_priors (
    brand_normalized  VARCHAR(128),
    category          VARCHAR(64),
    cohort_level      ENUM('bc', 'b', 'c', 'global'),
    sample_count      INT,
    sample_avg        DECIMAL(12, 8),    -- 평균 일평균 판매수
    smoothed_prior    DECIMAL(12, 8),
    calculated_at     DATETIME,
    PRIMARY KEY (brand_normalized, category, cohort_level)
);
-- 메인 사이클에서 일 1회 재계산
```

#### `swap_history`

```sql
CREATE TABLE swap_history (
    id                     BIGINT PRIMARY KEY,
    cycle_id               VARCHAR(32),
    swapped_at             DATETIME,
    product_in_id          BIGINT,
    product_in_score       DECIMAL(12, 4),
    product_out_id         BIGINT,
    product_out_score      DECIMAL(12, 4),
    score_margin           DECIMAL(12, 4),
    api_calls_used         INT,
    INDEX (cycle_id),
    INDEX (swapped_at)
);
-- 월별 파티셔닝 + 90일 이후 S3 아카이브
```

#### `swap_cooldown`

```sql
CREATE TABLE swap_cooldown (
    product_id   BIGINT PRIMARY KEY,
    locked_until DATE
);
```

#### `frozen_snapshots`

```sql
CREATE TABLE frozen_snapshots (
    product_id              BIGINT PRIMARY KEY,
    frozen_at               DATETIME,
    registration_days       INT,
    cum_likes               INT,
    cum_views               INT,
    cum_carts               INT,
    cum_sales               INT,
    margin_krw              INT,
    time_decay              DECIMAL(5, 4),
    direct_signal_score     DECIMAL(12, 8),
    frozen_score            DECIMAL(12, 4)
);
-- retired_pool 상품의 동결 데이터
```

#### `parameters`

```sql
CREATE TABLE parameters (
    `key`           VARCHAR(64) PRIMARY KEY,    -- P1, W2, S3, C1, A4 등
    value           TEXT,                        -- JSON 가능
    type            VARCHAR(32),                 -- 'int', 'float', 'str', 'bool', 'cron'
    default_value   TEXT,
    description     TEXT,
    last_updated_by VARCHAR(64),
    last_updated_at DATETIME
);
```

→ 운영자가 코드 배포 없이 parameter 변경. 다음 사이클부터 자동 적용.

### 8.2 인덱스 전략

- `score_index_*`: `score DESC` 인덱스로 Top-K / Bottom-K 빠른 조회
- `products`: `status` + `(brand_normalized, category)` 인덱스로 풀 필터 + 코호트 조회
- 100만+ 풀에서도 메인 사이클이 30분 내 완료 가능

---

## 9. Parameter 명세

총 **40개 활성 + 3개 예약**.

### 9.1 점수 공식 — 출품중 (P1~P9)

| Key | 의미 | 초기값 |
|---|---|---|
| P1 `grace_period_days` | 유예기간 (일) | 30 |
| P2 `decay_threshold_days` | 시간감점 임계점 (일) | 60 |
| P3a `decay_threshold_value` | 임계점 시점 잔여 점수 | 0.70 |
| P3b `decay_zero_days` | 베이스라인 보호 해제일 (일) | 90 |
| P4 `baseline_alpha` | 베이스라인 α | 0.001544 |
| P5 `w_찜` | 일평균 찜수 가중치 | 0.05644 |
| P6 `w_조회` | 일평균 조회수 가중치 | 0.00177 |
| P7 `w_장바구니` | 일평균 장바구니 가중치 | 0.9 |
| P8 `w_판매` | 일평균 판매수 가중치 | 1.0 |
| P9 `margin_floor_policy` | 음수마진 처리 | `"exclude"` |

### 9.2 출품대기 prior (W1~W10)

| Key | 의미 | 초기값 |
|---|---|---|
| W1 `waiting_pool_statuses` | 대기 풀 상태 | `["출품대기중"]` |
| W2 `kappa_bc` | (브랜드 × 카테고리) shrinkage 강도 | 100 |
| W3 `global_avg_daily_sales` | 전체 평균 일평균 판매 (fallback) | 0.000089 |
| W4 `brand_normalization_map` | 브랜드 대소문자/변형 표기 통합 매핑 (라인 분리 유지) | `upper().strip()` 기본 + 변형 점진 수동 |
| W5 `min_cohort_samples` | (브랜드 × 카테고리) 최소 표본 | 10 |
| W6 `kappa_b` | brand 평균 shrinkage | 50 |
| W7 `min_brand_samples` | brand 평균 최소 표본 | 10 |
| W8 `kappa_c` | category 평균 shrinkage | 50 |
| W9 `min_category_samples` | category 평균 최소 표본 | 30 |
| W10 `category_normalization_map` | 카테고리 정규화 매핑 (다국어·계층 → depth 2 통합 키) | 운영자 관리 (초기 자동 매핑 + 점진 보정) |

### 9.3 Swap 정책 (S1~S6)

| Key | 의미 | 초기값 |
|---|---|---|
| S1 `min_swap_margin` | swap 트리거 최소 마진 차이 (원/일) | 5 |
| S2 `swap_cooldown_days` | swap 후 잠금 기간 (일) | 14 |
| S3 `swap_batch_size` | 매 사이클 swap 쌍 수 | 200 |
| S4 `swapout_state_policy` | swap-out 동결 정책 | `"freeze"` |
| S5 `retired_pool_enabled` | retired_pool 부활 활성화 | `true` |
| S6 `fallback_trigger_multiplier` | fresh 풀 부족 판단 배수 | 3 |

### 9.4 사이클 (C1~C3)

| Key | 의미 | 초기값 |
|---|---|---|
| C1 `main_cycle_schedule` | 메인 사이클 cron | `"0 4 * * *"` |
| C2 `incremental_enabled` | Incremental 활성화 | `true` |
| C3 `cycle_timeout_minutes` | 사이클 최대 시간 | 30 |

### 9.5 API (A1~A10)

| Key | 의미 | 초기값 |
|---|---|---|
| A1 `api_daily_limit_product` | 상품 API 일 한도 | 2,500 |
| A2 `api_hourly_limit_total` | 전체 API 시간 한도 | 5,000 |
| A3 `main_cycle_api_budget` | 메인 사이클 swap 예산 상한 (호출 수). 잔량(6.3.3)과 비교해 작은 값 적용 | 400 |
| A4 `api_safety_margin_pct` | 안전 마진 (%) — 외부 시스템 후반 사용 + 예외 대응 예약 | 40 |
| A5 `api_retry_max_attempts` | 재시도 최대 | 3 |
| A6 `api_retry_backoff_seconds` | 백오프 초기값 (초) | 2 |
| A7 `scoring_system_priority` | 본 시스템 API 우선순위 (다른 시스템 대비) | `"lowest"` |
| A8 `expected_external_usage_method` | 외부 시스템 사용량 추정 방식 | `"weekly_avg_by_hour"` |
| A9 `min_swap_batch_size` | 사이클 실행 최소 보장 batch (이 미만이면 skip) | 10 |
| A10 `cycle_remaining_threshold` | 사이클 도중 중단 임계 (호출 수) | 20 |

### 9.6 예약 — Future Work (P10~P12, 미활성)

| Key | 의미 | 도입 시 초기값 |
|---|---|---|
| P10 `recent_window_days` | 최근 윈도우 평균 크기 (일) | 90 |
| P11 `snapshot_etl_enabled` | 일별 스냅샷 ETL 활성화 | `false` → 도입 시 `true` |
| P12 `window_fallback_mode` | 윈도우 데이터 부족 시 fallback | `"cumulative"` |

→ 12번 Future Work 참조.

---

## 10. 컴포넌트 책임 모델

> 인프라/배포/CI/CD 등 구체 환경은 개발자가 결정. 본 절은 비즈니스 로직상 필요한 컴포넌트 책임만 명세.

### 10.1 컴포넌트 책임

| 컴포넌트 | 책임 |
|---|---|
| **Score Recomputer** | 출품중·fresh·retired 풀 점수 계산. cohort_priors 일괄 재계산. |
| **Top-K Matcher** | Fill / Swap 후보 매칭. swap_trigger 검증 + 잔량 기반 batch 조정. |
| **BUYMA API Caller** | BUYMA 등록/수정/삭제 호출. rate limiting + retry. `api_usage_log` 기록. |
| **Cycle Orchestrator** | 메인 사이클 순서 제어 (5.1). 잔량 체크 → 점수 갱신 → 매칭 → 실행. |
| **Incremental Processor** | 외부 이벤트 단건 처리 (신규 sourcing 진입 등). 메인 사이클과 lock/queue로 충돌 방지. |

### 10.2 데이터 흐름

```
[메인 사이클 — 일 1회]
  원본 DB (상품 마스터)
    → Score Recomputer
    → score_index_listed / fresh / retired 갱신
    → cohort_priors 갱신
    → Top-K Matcher
    → BUYMA API Caller (등록 / 삭제)
    → swap_history + api_usage_log 기록

[이벤트 기반 — Incremental]
  외부 이벤트 (신규 sourcing, 검수 통과 등)
    → Incremental Processor
    → 단일 상품 점수 계산 → score_index 갱신
```

### 10.3 확장성 고려

| 규모 | 대응 |
|---|---|
| 출품대기 풀 100만+ | Incremental 계산 위주. 전수 재계산은 cohort_priors 갱신 시만. |
| Top-K / Bottom-K 조회 | `score DESC` 인덱스로 O(log N) |
| 메인 사이클 시간 제약 | 데이터 규모 증가 시 단계 병렬화 (구현 책임은 개발자) |
| swap_history / api_usage_log 누적 | 주기적 파티셔닝 + 오래된 데이터 아카이브 (정책은 운영자 결정) |

---

## 11. Future Work

### 11.1 주기적 신호 스냅샷 + 최근 윈도우 평균 (현재 미도입)

#### 문제
현재 직접신호점수는 누적 신호 기반 일평균. 한 번 잘 팔린 후 신호가 멈춰도 일평균이 천천히 떨어져 도태가 늦음.

#### 해결안
직접신호점수를 **최근 N일 윈도우 평균**으로 전환:

```
일평균_X = (오늘_누적_X − N일전_누적_X) / N

기본 N = 90일 (P10)
```

#### 도입 전제 조건
- **주기적 신호 스냅샷 테이블** 신설 — **주 1회 또는 10일 1회 스냅샷**으로 충분 (DB 부담 감소, 윈도우 평균 정확도는 유사 수준 유지)
- 시스템 가동 후 윈도우 크기 N일 누적되어야 정확. 이전엔 현재 누적 일평균으로 fallback (P12)

#### 신설 예정 테이블

```sql
CREATE TABLE buyma_signal_snapshots (
    product_id     BIGINT,
    snapshot_date  DATE,                    -- 주 1회 또는 10일 1회만 기록
    cum_likes      INT,
    cum_views      INT,
    cum_carts      INT,
    cum_sales      INT,
    PRIMARY KEY (product_id, snapshot_date)
);

-- 윈도우 평균 계산 시: snapshot_date가 N일 전에 가장 가까운 행을 SELECT
-- 정확히 N일 전 스냅샷이 없어도 직전 스냅샷으로 근사 (오차 최대 스냅샷 주기)
```

#### 도입 트리거
- "오래된 인기 상품의 사실상 도태 안 됨" 이슈 관찰
- 또는 운영자 명시적 결정
- + 주기 스냅샷 인프라 준비 완료

### 11.2 카테고리 컬럼 도입 후 prior 본격화

- 현재: 3.2.1 ②번 레이어 (brand_prior) 동작
- 도입 후: ①번 레이어 (brand × category prior) 자동 활성화
- 코드 변경 없이 데이터 도입만으로 업그레이드

### 11.3 운영 개선 후보

- "확인필요" 상태 정상화 워크플로우 (외부)
- 가격 변동에 따른 음수마진 자동 감지/제외
- 시즌성 보정 (계절·트렌드 기반 가중치)
