# BUYMA 상품 API 파라미터 — `run_daily_unified.py` 처리 지도

명세: https://specification.personal-shopper-api.buyma.com/api/products_json/

- **범위**: `run_daily_unified.py` 가 실제로 실행하는 코드만. 수동 도구·일회성 스크립트·`fast_price_updater.py` 는 다루지 않는다.
- **구성**: 위쪽 **요약**만 읽으면 현재 상태를 안다. 아래쪽 **상세**는 근거·실측·판단 이유.
- 코드에서 확인한 것만 적는다. 줄번호는 적지 않는다(코드가 정답).

> **2026-08-04 기준 공통 사항**
> - 등록판정("이 상품이 바이마에 올라갔나")은 `okmall\authority_flag.py` 의 `registered_sql()` 한 정의로
>   모여 있고 **항상 목록(buyma_listings) 기준**이다. ace 기준 옛 갈래와 환경변수 스위치(`USE_LISTING_AUTHORITY`)는 제거했다.
> - **바이마로 나가는 요청서는 몰이 40개여도 만드는 곳은 한 곳이다.** 파라미터 값을 바꾸려고
>   몰별 파일을 돌아다닐 필요가 없다 (아래 각 파라미터의 "바꾸려면 어디를" 참고).
> - 재고동기화의 공용 부분(마진 계산·최저가 조회·DB 반영 등)은 `okmall\stock_common.py` 한 곳에 있다.
>   몰별 파일에 남은 것은 사이트 긁기·옵션 대조·요청 간격처럼 **몰마다 달라야 하는 것**뿐이다.

---

# 요약

## 1. control (필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 아무 데서도 안 생긴다. 요청서를 만들 때 코드가 넣는 **고정값** |
| **어디에 저장되나** | 저장 안 함 (DB를 거치지 않는다) |
| **어떻게 가공되나** | 가공 없음 |
| **언제 나가나** | **신규등록·수정 요청마다 항상 `publish`** |
| **단계** | REGISTER(신규등록·정지분 부활), STOCK(재고·가격 반영 수정) |
| **실행 파일** | REGISTER: `okmall\reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>\stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 전부 같은 값. 몰별 차이 없음 |
| **바꾸려면 어디를** | `okmall\buyma_new_product_register.py` → `build_request_json()` 의 `"control"` 한 줄. **파일 1개** (몰 수와 무관) |
| **안 쓰는 값** | `draft`·`suspend` 안 씀 / `delete` 영구 폐기. 하차는 재고 API(출품정지)로 하고 그 요청엔 이 파라미터가 없음 |
| **남은 처리** | `ace_products.control`·`buyma_listings.control` DROP (지금 가능) |
| **커밋** | `611f178` |

## 2. reference_number (id 없으면 필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **등록 직전에 UUID 를 새로 만든다** (상품 하나당 한 번, 이미 있으면 그대로 씀) |
| **어디에 저장되나** | `buyma_listings.reference_number`.<br>전송이 성공하면 같은 값을 `buyma_listings.locked_reference_number` 에 사본으로 굳힌다 |
| **어떻게 가공되나** | 가공 없음 — 만든 값이 그대로 나간다 |
| **언제 나가나** | 신규등록 / 수정 / 출품정지(재고 API). 수정·정지 때는 **굳힌 사본을 우선** 쓰고 없으면 현재 값 |
| **단계** | REGISTER(발급·등록), STOCK(수정·출품정지). 수집·가격·번역·이미지·썸네일 단계는 이 값을 만지지 않는다 |
| **실행 파일** | REGISTER: `okmall\reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>\stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 전부 같은 규칙(UUID 1개). 몰별 차이 없음 |
| **바꾸려면 어디를** | 발급 규칙: `okmall\reconcile_buyma_push.py` → `issue_reference_number()`<br>요청서에 넣는 방식: 같은 파일 `build_create_request()` · `build_edit_request()` · `execute_retire()`. **파일 1개** |
| **되돌아오는 값** | 바이마가 웹훅으로 이 번호를 돌려주고, 그걸로 우리 행을 찾아 상품번호·게시상태를 기록한다 |
| **남은 처리** | `ace_products.reference_number` DROP — unified 기준 읽는 곳 0, **지금 가능** |
| **커밋** | `b57df2e` |

## 3. name (필수)

*(작성 예정 — 몰마다 처리가 갈리는 첫 파라미터)*

## 4. id (읽기 전용, reference_number 없으면 필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **우리가 만들지 않는다.** 바이마가 상품을 만들면서 발급한다 |
| **어디에 저장되나** | 바이마가 웹훅으로 알려주면 `buyma_listings.buyma_product_id` 에 기록<br>(`ace_products.buyma_product_id` 는 2026-08-04부터 기록하지 않음) |
| **어떻게 가공되나** | 가공 없음 — 받은 숫자 그대로 |
| **언제 나가나** | **수정(EDIT) 요청에만.** "어느 상품을 고칠지" 지목하는 용도.<br>신규등록에는 없고(아직 발급 전), 출품정지(재고 API)에는 지금 안 넣는다 — **넣을 수는 있다**(§상세 실측) |
| **단계** | REGISTER(등록 후 회수·판정), STOCK(수정 시 사용) |
| **실행 파일** | REGISTER: `okmall\reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>\stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 전부 동일 |
| **바꾸려면 어디를** | 요청서에 넣는 곳: `okmall\reconcile_buyma_push.py` → `build_edit_request()` 의 마지막 줄(`req['product']['id']`)<br>판정 기준: `okmall\authority_flag.py` → `registered_sql()`. **각각 파일 1개** |
| **또 다른 쓰임** | 전송 말고 **판정**에 쓰인다: 이 값이 있으면 "이미 바이마에 있음" → 신규등록 차단(중복 방지), 수정 갈래로 보냄 |
| **남은 처리** | `ace_products.buyma_product_id` DROP — unified 기준 읽는 곳 0, **지금 가능**<br>(unified 밖 청소도구·fast_price 는 그때 깨짐 — 나중 정리) |
| **커밋** | (작성 시점 미커밋) |

---

# 상세

## 1. control

### 명세
필수. `publish` / `draft` / `suspend` / `delete`. 한 번 publish 된 상품은 draft 로 되돌릴 수 없다.
삭제는 `control: "delete"` + `id` 또는 `reference_number` 만으로 가능.

### 우리 처리
요청서를 만드는 곳은 `reg.build_request_json` 한 곳이고 거기에 `publish` 가 상수로 박혀 있다.
CREATE 는 그대로, EDIT 는 같은 요청서에 `id` 를 덧붙인다. 따라서 **등록도 수정도 항상 `publish`.**

두 단계 모두 이 한 경로로 모인다.

```
REGISTER  reconcile_runner --scope new      → execute_create  (신규 CREATE)
                                            → execute_edit    (출품정지분 부활)
STOCK     stock_..._merge.py  재고 갱신 후
            _reconcile_published()
              → reconcile_runner.process_one_group(scope='published')
                                            → execute_edit    (재고·가격 반영) ← 수정의 대부분
                                            → execute_retire  (품절·마진X → 재고 API, control 없음)
```

### `suspend` 를 안 쓰는 이유
출품정지는 상품 API가 아니라 **재고 API(`variants.json`)** 로 만든다. 전 옵션을 `out_of_stock` +
`order_quantity: 0` 으로 보내면 BUYMA 가 스스로 '출품정지중' 으로 바꾸고 `buyer_suspended` 웹훅으로 알려준다.
재고 API에는 `control` 파라미터 자체가 없다. 삭제하지 않는 이유는 `buyma_product_id` 와 게시일수를 지키기 위해서다.

### 상태는 `status` + `is_published` 에 남는다
| 시점 | 기록 |
|---|---|
| CREATE 전송 성공 / 실패 | `status='pending'` / `'api_error'` |
| 등록 확정(웹훅) | `buyma_product_id`, `is_published=1`, `status='success'` |
| 출품정지 요청 전송 | **기록 없음** |
| 출품정지 확정(웹훅) | `is_published=0`, `status='soldout'` (상품번호 유지) |

`status='soldout'` 은 실제로 읽힌다 — 정지분을 register 가 같은 번호로 되살린다.

### DB 컬럼을 없앤 근거 (2026-08-04)
- 실측: `buyma_listings` 144,398건 전부 `draft` / `ace_products` `publish` 747,341 + `suspend` 38
  (`suspend` 를 쓰는 코드는 저장소에 없음 — 손으로 만든 흔적)
- 트리거·뷰 참조 0건, 인덱스는 `ace_products.idx_control` 하나
- 판단에 쓰는 코드 0건
- **DB에 두지 않기로 한 이유**: `suspend` 는 우리가 보낼 수 있는 값이 아니라(재고 API엔 없고 상품 API는 거부),
  BUYMA 파라미터 이름으로 내부 상태를 적으면 "이 값이 API로 나간다"는 오해를 부른다.
  상태를 담는 자리는 이미 `status` + `is_published` 로 있다.

---

## 2. reference_number

### 명세
`id` 가 없으면 필수. 문자열. **같은 번호를 여러 상품에 쓸 수 없다.**
`id` 와 함께 주면 `id` 가 우선하고 이 값은 무시된다. 삭제 때는 이 값만으로 상품을 특정할 수 있다.

### 우리 처리 — 등록 직전 한 번, 목록에만

```
REGISTER  reconcile_runner --scope new
  ├ execute_create
  │   issue_reference_number()   UUID 발급 → buyma_listings.reference_number (비어 있을 때만)
  │   build_create_request()     요청서에 그 번호
  │   call_buyma_api()           전송
  │   record_after_create()      성공 → status='pending' + locked_reference_number 에 사본
  └ execute_edit                 정지분 부활 (아래 EDIT 와 같은 경로)

STOCK     stock_..._merge.py  재고 갱신 → _reconcile_published()
            → reconcile_runner.process_one_group(scope='published')
  ├ execute_edit   → build_edit_request()   locked_reference_number 우선, 없으면 reference_number
  └ execute_retire → reg.call_buyma_variants_soldout(ref, 옵션들)   ← 재고 API 요청서에 이 번호

판정      reconcile_runner  이 번호가 있으면 "이미 바이마에 있음"으로 보고 CREATE/EDIT 갈래를 가름
웹훅      server.py         이 번호로 우리 행을 찾아 상품번호·게시상태 기록
```

**전수 확인 결과**: COLLECT·PRICE·TRANSLATE·IMAGE·THUMBNAIL 단계의 스크립트와 그 하위 import 어디에도
이 값을 만지는 코드가 없다. CONVERT 에는 이제 안 쓰는 `generate_reference_number()` 정의만 남아 있다.

- 번호 형식은 UUID(예: `a57320b8-c50e-4db3-88ee-5fb218e26034`).
- **`locked_reference_number`** 는 "BUYMA 가 아는 번호"를 잃지 않으려는 사본이다. 등록 성공 시 한 번만 채워진다.

### 데이터 흐름 — ace 를 거치지 않는다 (2026-08-04 변경)

| | 이전 | 지금 |
|---|---|---|
| 변환(CONVERT) | 모든 ace 행에 UUID 발급 (74만 건) | **발급 안 함** |
| 등록(REGISTER) | 목록에도 따로 발급 | 목록에만 발급 |
| 수정·정지 | 게시된 ace 의 번호를 우선 사용 | **목록 번호만 사용** |

이전 방식의 문제는 실측으로 드러났다 — ace 74만 행 중 번호 보유 747,442건인데 실제 게시중은 48,146건.
**93.6% 가 BUYMA 가 모르는 헛번호**였다.

### 실측 (2026-08-04)

| 항목 | 값 |
|---|---|
| 유니크 제약 | `ace_products`·`buyma_listings` 양쪽에 `uk_reference_number` ✅ 명세와 일치 |
| 게시중 목록 | 82,145건 — **번호 없는 것 0건**, 상품번호 없는 것 0건 |
| 목록 ↔ 게시중 ace 번호 비교 | 같음 46,182 (56.2%) / **다름 118 (0.1%)** / 짝지어진 게시 ace 없음 35,845 (43.6%) |
| '다름' 중 상품번호까지 다른 것 | 104건 (번호 문제가 아니라 정체성이 어긋난 행) |
| 잠긴 번호 ≠ 현재 번호 (게시중) | **83건** |
| `MG` 예비값 유출 | **0건** |

### 고친 것 (2026-08-04)

| 파일 | 내용 |
|---|---|
| `raw_to_ace_converter.py` · `raw_to_converter_kasina.py` | 번호 발급 제거, ace INSERT·SELECT 에서 제거 |
| `reconcile_buyma_push.py` | CREATE 예비값(`MG{listing_id}`) 제거 / EDIT·출품정지가 목록 번호만 보게 / `published_member` SELECT 에서 ace 번호 제거 |
| 재고동기화 `_merge` 14개 | ace 번호를 조회하던 2곳씩(총 28곳) 제거 |
| `buyma_new_product_register.py` | 부품만 남기고 실행부·삭제 API·ace 조회 제거 (1,438줄 → 709줄) |

### 어긋난 83건 정리 (2026-08-04 완료)

굳힌 사본과 현재 값이 다른 게시중 목록 83건을, **바이마가 웹훅으로 돌려준 번호**(`buyma_listing_events`)에 맞춰 두 칸을 통일했다.

- 현재 값이 진짜였던 것 70건 / 굳힌 사본이 진짜였던 것 13건 / 판정 못 한 것 0건
- 스크립트 `migrations/fix_locked_ref_mismatch.py` (미리보기 기본, `--execute` 로 반영, 원본 JSON 백업)
- 반영 후 어긋남 0건 확인
- **왜 방치하면 안 되는가**: 출품정지는 재고 API로 나가는데 그 요청에는 상품번호가 없고 이 번호만 있다.
  값이 틀리면 엉뚱한 상품에 나가거나 아무 데도 안 나간다. 수정도 굳힌 사본을 우선 쓰므로 반영이 안 될 수 있다
  (전례: `migrations/fix_locked_reference_number.py`, 실사례 buyma 133376384).

### 웹훅 정리 (2026-08-04 완료)

웹훅(`okmall_reference/server.py`)이 이 번호로 `ace_products` 를 갱신하던 6곳과 ace 로그 조회 1곳을 제거했다.
이제 웹훅은 `buyma_listings` 만 갱신한다. → `ace_products.reference_number` DROP 가능.

### 과거 사고의 원인 (해소)

`migrations/fix_locked_reference_number.py` 에 "관리번호가 틀려 수정이 201 받고도 반영 안 됨"으로 기록된 건이 있었다.
아래 §4 실측으로 설명된다 — **`id` 가 있으면 관리번호는 무시**되므로, 그 건은 `id` 가 비어 있어 관리번호로만 상품을 찾던 상황이었다.

---

## 3. name

*(작성 예정)*

---

## 4. id

### 명세
바이마가 발급하는 상품 ID (`BUYMA が発行する商品 ID です`). **읽기 전용 — 우리가 정할 수 없다.**
`id` 나 `reference_number` 중 하나는 반드시 넣어야 하고, **둘 다 주면 `id` 가 우선**하고 관리번호는 무시된다.
신규등록에는 넣을 수 없고(아직 없으므로), 수정·삭제 때 상품을 지목하는 데 쓴다.

### 우리 처리 — 받아서 저장하고, 수정할 때 되돌려준다

```
등록  요청서에 id 없음 (관리번호만)
        ↓ 바이마가 상품을 만들고 번호를 발급
웹훅  buyma_listings.buyma_product_id 에 기록 + is_published=1, status='success'
        ↓
수정  요청서에 id 를 넣어 "이 상품을 고쳐라" 로 보냄     ← 넣는 곳은 코드 전체에서 한 곳뿐
출품정지  재고 API는 관리번호만 쓴다 → id 안 나감
```

### 전송 말고 '판정' 에 더 많이 쓰인다

값이 실제로 요청서에 들어가는 곳은 한 곳인데, **이 값이 있느냐 없느냐로 갈래를 가르는 곳**은 전 단계에 퍼져 있다.

| 어디 | 무엇을 가르나 |
|---|---|
| REGISTER | 이 값이 있으면 신규등록을 **차단**한다(같은 상품 두 번 올리는 사고 방지). 락을 잡은 뒤 한 번 더 확인 |
| REGISTER | 없으면 CREATE, 있으면 EDIT 로 갈래를 가름 |
| PRICE(`--new-only`) | 아직 바이마에 없는 것만 최저가 조회 대상으로 |
| CONVERT | 이미 등록된 상품인지 확인해 재변환 범위를 정함 |
| STOCK | 등록된 상품만 재고 동기화 대상으로 |

이 판정은 `authority_flag.registered_sql()` 이라는 **공용 정의 한 개**로 모여 있다 —
"그 상품이 속한 목록이 게시중이고 상품번호를 갖고 있는가". **항상 목록 기준이다.**

### 실제 호출로 확인한 규칙 (2026-08-04, 운영 계정)

게시중 상품 1개로 상품 API(수정)와 재고 API에 조합별로 실제 요청을 보냈다.
'틀린 값' 은 **존재하지 않는 값**만 썼다(다른 실제 상품 번호는 쓰지 않음).

| 보낸 조합 | 상품 API | 재고 API |
|---|---|---|
| 관리번호만 (정확) | 201 접수 | 201 접수 |
| id 만 (정확) | 201 접수 | 201 접수 |
| id 정확 + 관리번호 틀림 | 201 접수 | 201 접수 |
| **id 틀림 + 관리번호 정확** | 201 접수 → **웹훅에서 실패** | 201 접수 |
| 둘 다 정확 | 201 접수 | 201 접수 |
| **둘 다 없음** | **422 거부** | **422 거부** |

422 응답: `{"errors":{"identity_requirements":["商品IDまたは商品管理番号を入力してください。"]}}`

**확정된 것**
1. **둘 중 하나는 반드시 필요하다.** 없으면 즉시 422.
2. **`id` 가 우선이다.** id 가 틀리면 관리번호가 맞아도 실패한다(웹훅으로 실패 통지 → 우리 DB의 상품번호가 지워지고 `status='fail'` 이 됨).
3. **201 은 성공이 아니라 접수다.** 성패는 웹훅으로만 알 수 있다.
4. **재고 API도 `id` 를 받는다.** 명세: `id の入力がある場合はそちらを優先して商品を検索する`.

**개선 여지**: 출품정지(재고 API)는 지금 관리번호만 보낸다. `id` 를 함께 보내면 관리번호가 어긋나도 안전하다.

> ⚠️ 이 검증 중 '틀린 id' 요청의 실패 웹훅 때문에 대상 상품의 DB 기록이 미등록 상태로 초기화됐다(수동 복구함).
> 같은 검증을 다시 할 때는 **틀린 id 조합을 운영 계정에서 쓰지 말 것.**
