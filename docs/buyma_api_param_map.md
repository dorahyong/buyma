# BUYMA 상품 API 파라미터 — 우리 자동화에서의 실제 처리

명세: https://specification.personal-shopper-api.buyma.com/api/products_json/

**다루는 범위**: `run_daily_unified.py`(매일 자동화 40몰) 와 `run_fast_price_loop.bat`(가격 갱신) 두 갈래만.
수동 정리 도구(`buyma_cleaners/*`, `migrations/*`)와 자동화에서 호출되지 않는 코드는 다루지 않는다.

**작성 원칙**: 코드에서 확인한 것만 적는다. 줄번호는 작성 시점 기준이므로, 어긋나면 코드가 정답이다.

| 작성일 | 내용 |
|---|---|
| 2026-08-04 | 1. control |

---

## 1. control (*)

### 명세

필수. 값은 `publish` / `draft` / `suspend` / `delete` 네 가지.
- 한 번 `publish` 된 상품은 `draft` 로 되돌릴 수 없다.
- 삭제는 `control: "delete"` 와 `id` 또는 `reference_number` 만 있으면 된다.

### 우리가 보내는 값 — 언제나 `publish` 하나뿐

| 동작 | 만드는 곳 | 보내는 값 |
|---|---|---|
| 신규등록(CREATE) | `okmall/buyma_new_product_register.py` `build_request_json()` 의 상수<br>← `okmall/reconcile_buyma_push.py` `build_create_request()` 가 호출 | `publish` |
| 수정(EDIT) | 같은 빌더 + `req['product']['id']` 추가<br>← `reconcile_buyma_push.py` `build_edit_request()` | `publish` |
| 출품정지(품절·마진X) | `reconcile_buyma_push.py` `execute_retire()` | **보내지 않음** (아래 참조) |
| 삭제 | — | **쓰지 않음** (영구 폐기 방침) |

값은 소스코드 안 상수다. DB에서 읽지 않는다.

### `suspend` 를 안 쓰는 이유

출품정지는 상품 API가 아니라 **재고 API** 로 만든다.

```
reconcile_runner.py   판정: winner(마진O) 없거나 재고 있는 옵션이 0개 → 출품 불가
  → reconcile_buyma_push.py  execute_retire()
      → buyma_new_product_register.py  call_buyma_variants_soldout()
          → POST api/v1/products/variants.json
             (전 옵션 out_of_stock + order_quantity 0)
```

- **재고 API에는 `control` 파라미터 자체가 없다.**
- 상품을 '출품정지중' 으로 바꾸는 주체는 BUYMA다. 우리는 재고를 0으로 만들 뿐이고,
  BUYMA가 `buyer_suspended` 웹훅으로 결과를 알려준다.
- 삭제하지 않는 이유: `buyma_product_id` 와 게시일수(등록 이후 누적)를 잃지 않기 위해서다.
  재입고되면 같은 상품 번호로 되살린다.

### 상태는 어디에 남는가 — `control` 이 아니라 `status` + `is_published`

`control` 은 이 흐름 어디에도 관여하지 않는다.

| 시점 | 기록되는 곳 | 값 |
|---|---|---|
| CREATE 전송 성공 | `buyma_listings` | `status='pending'` + `locked_*` 백업 |
| CREATE 전송 실패 | `buyma_listings` | `status='api_error'` |
| 등록 확정(웹훅) | `ace_products` · `buyma_listings` | `buyma_product_id`, `is_published=1`, `status='success'` |
| 출품정지 요청 전송 | **아무 데도 안 남는다** | — |
| 출품정지 확정(웹훅 `buyer_suspended`) | `ace_products` · `buyma_listings` | `is_published=0`, `status='soldout'` (buyma_id 유지) |
| 삭제 확정(웹훅 `buyer_deleted`) | 〃 | `is_published=0`, `status='deleted'` |

`status='soldout'` 은 실제로 읽힌다 — `reconcile_runner.py` 가 "정지분(`is_published=0` · `status='soldout'` · buyma_id 있음)" 을
register 담당으로 넘겨 같은 번호로 다시 살린다.

### DB 컬럼 `control` — 제거함 (2026-08-04)

`ace_products.control` 과 `buyma_listings.control` 이 있었으나 **아무도 판단에 쓰지 않았다.**

제거 전 실측:

```
buyma_listings   draft     144,398건   (전부 draft — 다른 값이 되는 코드 경로가 없었음)
ace_products     publish   747,341건
ace_products     suspend        38건   (okmall, 7/21 15:03~15:11. 이 값을 쓰는 코드는 저장소에 없음)
```

- 트리거·뷰 참조 **없음** (이 DB의 트리거 3개 모두 무관), 인덱스는 `ace_products.idx_control` 하나뿐
- 읽는 코드 없음. `reconcile_runner.py` 의 진단 출력은 DB가 아니라 **API 요청 딕트**의 control 을 찍는 것이라 무관

**코드에서 걷어낸 곳 (7지점 / 5파일)**

| 파일 | 내용 |
|---|---|
| `okmall/raw_to_ace_converter.py` | ace INSERT 컬럼·값, 변환 결과 dict 의 `'control': 'publish'` |
| `kasina/raw_to_converter_kasina.py` | 위와 동일 (okmall 외 39몰 담당) |
| `okmall/reconcile_ensure_group.py` | listing INSERT, UPDATE 2곳의 `control='draft'` |
| `okmall/resolve_merge.py` | UPDATE, 값 튜플 2곳 |
| `okmall/dedup_corrector_merge.py` | listing INSERT |

**DB 정리 (코드 반영 후 하루 관찰하고 실행)**

```sql
ALTER TABLE ace_products   DROP COLUMN control;   -- idx_control 도 함께 사라짐
ALTER TABLE buyma_listings DROP COLUMN control;
```

컬럼이 남아 있는 동안 새로 만들어지는 `ace_products` 행은 기본값 `draft` 로 들어간다(예전엔 `publish`). 읽는 코드가 없어 무해하다.

### 왜 DB에 두지 않기로 했나

"재고 API를 보냈다(=출품정지시켰다)"를 `control='suspend'` 로 기록하는 안을 검토했으나 채택하지 않았다.

1. `suspend` 는 우리가 보낼 수 있는 값이 아니다. 재고 API에는 그 파라미터가 없고, 상품 API로 보내면 BUYMA가 거부한다.
   → BUYMA 파라미터 이름을 빌려 우리 내부 사정을 적는 꼴이 되어, 나중에 읽는 사람이 "이 값이 API로 나간다"고 오해한다.
   `ace_products` 의 `suspend` 38건이 실제로 그 혼란의 표본이다(게시중인데 suspend 로 적혀 있고 아무 일도 안 일어남).
2. 정지된 상품을 되살릴 때 우리는 EDIT 을 `publish` 로 보낸다. `control` 은 항상 `publish` 여야 그 흐름과 맞는다.
3. 상태를 담는 자리는 이미 `status` + `is_published` 로 있다. 같은 것을 두 곳에 두면 어긋난다.

### 남은 문제 (control 과 별개, 기존부터 비어 있던 자리)

1. **출품정지 요청을 보냈다는 기록이 없다.** CREATE 는 `status='pending'` 으로 접수를 남기지만 retire 에는 그게 없다.
   웹훅이 안 오면 "보냈는데 안 내려간 것"과 "애초에 안 보낸 것"을 구분할 수 없다.
   (2026-07-28 실측: 게시중 72,503건 중 5,185건이 '팔 옵션 0개인데 판매중')
2. **"우리가 원하는 상태"를 저장하지 않는다.** 매 사이클 출품 가능 여부를 계산하고 버린다.
   저장한다면 `원하는 상태='내림' AND is_published=1` 한 줄로 미하차분이 드러난다.
3. `fast_price_updater.py` 는 같은 재고 API를 보내고 **웹훅을 기다리지 않고 스스로** `status='soldout'`·`is_published=0` 을 쓴다.
   같은 동작에 기록 방식이 두 갈래다. 통합할 때 한쪽으로 정해야 한다.
