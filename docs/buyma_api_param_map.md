# BUYMA 상품 API 파라미터 — `run_daily_unified.py` 처리 지도

명세: https://specification.personal-shopper-api.buyma.com/api/products_json/

- **범위**: `run_daily_unified.py` 가 실제로 실행하는 코드만. 수동 도구·일회성 스크립트·`fast_price_updater.py` 는 다루지 않는다.
- **구성**: 위쪽 **요약**만 읽으면 현재 상태를 안다. 아래쪽 **상세**는 근거·실측·판단 이유.
- 코드에서 확인한 것만 적는다. 줄번호는 적지 않는다(코드가 정답).

> **2026-08-04 기준 공통 사항**
> - 등록판정("이 상품이 바이마에 올라갔나")은 `okmall/authority_flag.py` 의 `registered_sql()` 한 정의로
>   모여 있고 **항상 목록(buyma_listings) 기준**이다. ace 기준 옛 갈래와 환경변수 스위치(`USE_LISTING_AUTHORITY`)는 제거했다.
> - **바이마로 나가는 요청서는 몰이 40개여도 만드는 곳은 한 곳이다.** 파라미터 값을 바꾸려고
>   몰별 파일을 돌아다닐 필요가 없다 (아래 각 파라미터의 "바꾸려면 어디를" 참고).
> - 재고동기화의 공용 부분(마진 계산·최저가 조회·DB 반영 등)은 `okmall/stock_common.py` 한 곳에 있다.
>   몰별 파일에 남은 것은 사이트 긁기·옵션 대조·요청 간격처럼 **몰마다 달라야 하는 것**뿐이다.

> ## ★ 게시 후 편집 불가 (명세)
> 아래 7개는 **한 번 게시되면 바꿀 수 없다.** 수정 요청에 넣어도 반영되지 않는다.
> 등록 시점에 틀리면 그 상품은 끝까지 틀린 값으로 남는다 — 새로 올리는 수밖에 없다.
>
> `name` · `brand_id` · `brand_name` · `category_id` · `buying_area_id` · `buying_shop_name` · `shipping_area_id`
>
> **★ 실제로 쏴서 확인한 규칙 (2026-08-04)** — 수정 요청에서 이 값들은
> **빼거나, 바이마가 가진 값과 똑같이** 보내야 한다. **다른 값을 넣으면 요청 전체가 거부된다.**
>
> | 수정 요청에 넣은 방식 | 결과 |
> |---|---|
> | 아예 안 보냄 | ✅ 성공 (가격 변경 반영) |
> | 바이마와 같은 값 | ✅ 성공 |
> | 바이마와 다른 값 | ❌ **거부** `買付先ショップ名は変更できません` — **같이 보낸 가격 변경까지 무산** |
>
> 그래서 우리는 등록 성공 시 이 값들을 `locked_*` 로 굳혀두고, 수정 때는 굳힌 값을 그대로 다시 보낸다.
> (`buying_shop_name` 만은 아예 보내지 않는다 — 굳혀둘 자리가 없어 목록 값을 그대로 쓴다)
>
> ⚠️ 뒤집어 말하면, **굳힌 값이 바이마와 어긋난 상품은 수정이 영구히 실패**한다. 재고·가격도 못 나간다.

---

# 진행 현황 (명세 페이지에 나오는 순서 그대로)

| # | 파라미터 | 상태 | # | 파라미터 | 상태 |
|---|---|---|---|---|---|
| 1 | control | ✅ | 18 | buying_area_id | ✅ |
| 2 | reference_number | ✅ | 19 | buying_shop_name | ✅ |
| 3 | name | ✅ | 20 | shipping_area_id | ✅ |
| 4 | id | ✅ | 21 | buyer_notes | ✅ |
| 5 | status | ✅ (남은 문제 있음) | 22 | duty | ✅ |
| 6 | comments | ✅ | 23 | tags | ✅ |
| 7 | brand_id | ✅ | 24 | images | |
| 8 | brand_name | ✅ | 25 | shipping_methods | ✅ |
| 9 | model_id | ⬜ 다음 | 26 | style_numbers | ✅ |
| 10 | category_id | | 27 | options | |
| 11 | theme_id | ✅ | 28 | size_unit | ✅ |
| 12 | season_id | ✅ | 29 | colorsize_comments | |
| 13 | price | ✅ | 30 | variants | |
| 14 | list_price | ✅ | 31 | order_quantity | ✅ |
| 15 | regular_price | ✅ | 32 | shop_urls | ✅ |
| 16 | reference_price | ✅ | 33 | updated_at | ✅ |
| 17 | available_until | ✅ | 34 | created_at | ✅ |

# 요약

## 1. control (필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 요청서를 만들 때 코드가 넣는 **고정값** |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 `publish` |
| **단계** | REGISTER(신규등록·정지분 부활), STOCK(재고·가격 반영 수정) |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 동일 |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `build_request_json()` 의 `"control"` 한 줄 |
| **커밋** | `611f178` |

## 2. reference_number (id 없으면 필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **등록 직전에 UUID 를 새로 만든다** (상품 하나당 한 번) |
| **어디에 저장되나** | `buyma_listings.reference_number`.<br>전송 성공 시 같은 값을 `locked_reference_number` 에 사본으로 굳힌다 |
| **어떻게 가공되나** | 없음 — 만든 값 그대로 |
| **언제 나가나** | 신규등록 / 수정 / 출품정지(재고 API). 수정·정지 때는 **굳힌 사본 우선**, 없으면 현재 값 |
| **단계** | REGISTER(발급·등록), STOCK(수정·출품정지) |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 같은 규칙 |
| **게시 후 편집** | 가능. 단 `id` 가 함께 나가면 이 값은 무시된다 |
| **바꾸려면 어디를** | `okmall/reconcile_buyma_push.py` → `issue_reference_number()`(발급) / `build_create_request()`·`build_edit_request()`·`execute_retire()`(전송) |
| **커밋** | `b57df2e` |

## 3. name (필수, 상품명)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 몰에서 수집한 상품명을 변환기가 `送料・関税込 \| 브랜드 \| 상품명 모델번호` 형식으로 조립 |
| **어디에 저장되나** | `ace_products.name` (몰별로 하나씩)<br>→ 그룹 대표(seed) ace 의 이름이 `buyma_listings.name`<br>→ 등록 성공 시 `buyma_listings.locked_name` 으로 굳는다 |
| **어떻게 가공되나** | ① 수집기가 몰별 규칙으로 정리(지점명·시즌코드·국내마커 등)<br>② 변환기가 한 번 더 정리한 뒤 형식 조립 + 특수문자 정제<br>③ 번역 배치가 일본어로 바꿈(`ace_products.name` 갱신)<br>④ 전송 직전 60자(반각)로 자르고 끝 공백 제거 |
| **언제 나가나** | 신규등록·수정 요청마다. 수정 때는 굳힌 값(`locked_name`) |
| **단계** | COLLECT(정리) · CONVERT(정리+조립) · TRANSLATE(번역) · REGISTER · STOCK |
| **실행 파일** | 정리 규칙: `okmall/name_rules.py` (한 곳에 모음)<br>COLLECT: 몰별 수집기<br>CONVERT: `okmall/raw_to_ace_converter.py` · `kasina/raw_to_converter_kasina.py`<br>TRANSLATE: `okmall/convert_to_japanese_gemini.py`<br>REGISTER·STOCK: `okmall/reconcile_runner.py` |
| **공용 / 몰별** | **공용** — 규칙은 `okmall/name_rules.py` 한 파일에 모여 있고, 몰 이름으로 갈라 쓴다. 조립 형식·축약도 공용 |
| **게시 후 편집** | **불가** — 등록 시점에 정해지면 끝. 틀리면 새로 올리는 수밖에 없다 |
| **바꾸려면 어디를** | 정리 규칙(몰별·공통 전부): `okmall/name_rules.py` → `MALL_PATTERNS` · `GLOBAL_PATTERNS`<br>조립 형식: 변환기의 `format_buyma_product_name()`<br>축약: `okmall/buyma_new_product_register.py` → `truncate_buyma_name()`<br>등록 전 목록 이름 갱신: `okmall/reconcile_ensure_group.py` → `_refresh_pending_name()` |
| **커밋** | `6093dcb` 축약 끝공백·목록 이름 갱신 · `fddd440` 정리 규칙 공용화 |

**등록 전에는 대표 ace 이름을 계속 따라간다.** 등록된 뒤에는 어떤 경로로도 바꾸지 않는다.

**정리 규칙은 두 번 돌려도 결과가 같아야 한다(멱등).** 수집 때와 변환 때 두 번 적용하기 때문이다.
덕분에 규칙을 고치면 **재수집 없이 재변환만으로** 이미 모아 둔 상품에도 반영된다.

## 4. id (읽기 전용, reference_number 없으면 필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **바이마가 발급한다** (우리가 만들지 않음) |
| **어디에 저장되나** | `buyma_listings.buyma_product_id` (웹훅이 기록) |
| **어떻게 가공되나** | 없음 — 받은 숫자 그대로 |
| **언제 나가나** | **수정 요청에만** — 어느 상품을 고칠지 지목.<br>신규등록에는 없고, 출품정지(재고 API)에는 넣지 않는다 |
| **단계** | REGISTER(등록 후 회수·판정), STOCK(수정 시 사용) |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | — (읽기 전용) |
| **바꾸려면 어디를** | 요청서: `okmall/reconcile_buyma_push.py` → `build_edit_request()` 마지막 줄<br>등록판정: `okmall/authority_flag.py` → `registered_sql()` |
| **커밋** | `6435ef4` |

## 5. status (읽기 전용 — 바이마가 알려주는 상품 상태)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **바이마가 정한다** |
| **어디에 저장되나** | `buyma_listings.status` (웹훅이 기록) |
| **어떻게 가공되나** | 바이마 값 → 우리 값으로 번역.<br>`buyer_deleted`→`deleted` / `buyer_suspended`→`soldout` / 등록성공→`success`<br>우리가 직접 쓰는 값: `pending`(전송 접수) · `api_error`(전송 실패) · `fail`(바이마 실패 통지) |
| **언제 나가나** | **나가지 않는다** |
| **단계** | 웹훅에서 기록 → REGISTER·STOCK 이 읽어서 갈래를 가름 |
| **실행 파일** | 기록: 웹훅 서버(`okmall_reference/server.py`) / 읽기: `okmall/reconcile_runner.py` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | — (읽기 전용) |
| **바꾸려면 어디를** | 번역 규칙: 웹훅 서버 / 그 값으로 무엇을 할지: `okmall/reconcile_runner.py` |
| **커밋** | — |

**무엇을 가르나**: `deleted` → 재등록 안 함 / `soldout` → 같은 번호로 되살림 / `pending`·`fail`·`success` → 신규등록 대상에서 제외

## 6. comments (필수, 상품 설명문)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 요청서를 만들 때 세 조각을 붙여 즉석에서 만든다 |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | `상품명(자르지 않은 원본)` + `모델번호 3가지 표기` + `고정 안내문 1,142자` |
| **언제 나가나** | 신규등록·수정 요청마다 매번 새로 만들어 보냄 |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 같은 안내문 |
| **게시 후 편집** | 가능 — 안내문을 고치면 다음 수정부터 반영된다 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `build_request_json()` 안의 `fixed_comments` 상수<br>⚠️ `fast_price_updater.py` 에 같은 조립식이 한 벌 더 있다(미가동) |
| **커밋** | `6435ef4` |

**한도** 3,000자 — 실측 1,217~1,281자

## 7. brand_id (필수, 브랜드 번호)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | `mall_brands` 매핑표 — 몰 브랜드를 바이마 브랜드에 사람이 손으로 연결한다 |
| **어디에 저장되나** | `mall_brands.buyma_brand_id` → `ace_products.brand_id` → `buyma_listings.brand_id`<br>→ 등록 접수 성공 시 `buyma_listings.locked_brand_id` 로 굳는다 |
| **어떻게 가공되나** | 그대로 정수로 보낸다. 매핑이 없으면 **0** 을 보내고 대신 `brand_name` 을 같이 보낸다 |
| **언제 나가나** | 신규등록·수정 요청마다. 수정 때는 굳힌 값(`locked_brand_id`) |
| **단계** | CONVERT(매핑 조회) · REGISTER · STOCK |
| **실행 파일** | CONVERT: `okmall/raw_to_ace_converter.py` · `kasina/raw_to_converter_kasina.py`<br>REGISTER·STOCK: `okmall/reconcile_runner.py` |
| **공용 / 몰별** | **몰별** — 매핑표가 몰마다 한 줄씩. 보내는 방식은 공용 |
| **게시 후 편집** | **불가** — 다르게 보내면 요청 전체가 거부된다 |
| **바꾸려면 어디를** | 매핑: `mall_brands.buyma_brand_id`<br>보내는 자리: `okmall/buyma_new_product_register.py` → `build_request_json()`<br>굳힌 값 읽기: `okmall/reconcile_buyma_push.py` → `build_edit_request()` 의 `L('locked_brand_id','brand_id')` |
| **커밋** | (조사만 함, 코드 변경 없음) |

**0 은 "브랜드 없음"이 아니라 "바이마에 그 브랜드가 없음"** 이다. 게시중 82,011건 중 6,329건이 0.
0 일 때는 `style_numbers`(품번)를 안 보낸다 — 바이마가 "품번은 브랜드를 지정해야 넣을 수 있다"며 거부하기 때문.

## 8. brand_name (선택, 브랜드 이름)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | `mall_brands.buyma_brand_name` — 사람이 매핑할 때 적는 `영문(일본어읽기)` 형식 |
| **어디에 저장되나** | `ace_products.brand_name` → `buyma_listings.brand_name`. **굳히는 칸이 없다** |
| **어떻게 가공되나** | 손대지 않고 그대로 보낸다 |
| **언제 나가나** | **`brand_id` 가 0 일 때만.** 0 이 아니면 아예 안 보낸다 |
| **단계** | CONVERT · REGISTER · STOCK |
| **실행 파일** | `brand_id` 와 같다 |
| **공용 / 몰별** | **몰별** — 매핑표를 따라간다 |
| **게시 후 편집** | **불가** |
| **바꾸려면 어디를** | 값: `mall_brands.buyma_brand_name`<br>보내는 조건·자리: `okmall/buyma_new_product_register.py` → `build_request_json()` 의 `if not api_brand_id` 분기 |
| **커밋** | (조사만 함, 코드 변경 없음) |

**굳히지 않는데도 지금은 안 터진다** — 값이 바뀌면 수정이 거부될 수 있지만, 바이마 값과 대조에서 불일치 0건이었다.

## 11. theme_id (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 코드에 박힌 **고정값 `98`** |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 `98` |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `BUYMA_FIXED_VALUES` |
| **커밋** | — |

## 12. season_id (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 만들지 않는다 |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** — 요청서에 이 항목이 없다 |
| **단계** | 해당 없음 |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | 보내려면 `okmall/buyma_new_product_register.py` → `build_request_json()` 에 항목 추가 |
| **커밋** | — |

## 13. price (필수, 판매가)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 가격 단계에서 계산한다 — 경쟁자 최저가가 있으면 **언더컷**, 없으면 **목표마진 30% 역산** |
| **어디에 저장되나** | `buyma_listings.price` (엔) |
| **어떻게 가공되나** | 경쟁자 있음: 현재가가 `[경쟁자-9, 경쟁자-1]` 안이면 유지, 아니면 `경쟁자 - 랜덤(1~9)`<br>경쟁자 없음: `(매입가 + 배송비 - 부가세환급) / (1 - 수수료 0.055 - 0.30)` → ÷ 9.2 로 엔 환산 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 |
| **단계** | PRICE(경쟁자 최저가 수집) → REGISTER·STOCK(계산·전송) |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 계산식 한 곳 |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/resolve_merge.py` → `calculate_target_price_jpy()`(무경쟁 마진율) / 언더컷 규칙 |
| **커밋** | — |

## 14. list_price (읽기 전용, 출품가)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **바이마가 계산한다** — `판매가 + 가장 싼 배송료` |
| **어디에 저장되나** | 저장하지 않는다 (웹훅 응답에만 들어옴) |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** (보낼 수 없는 값) |
| **단계** | — |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | — (읽기 전용) |
| **바꾸려면 어디를** | — |
| **커밋** | — |

실측: 우리 상품은 `price` 와 같은 값으로 돌아온다(배송료가 상품가에 포함돼 있어 추가 배송료가 0).

## 15. regular_price (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 만들지 않는다 |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** |
| **단계** | 해당 없음 |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | 쓰려면 `okmall/buyma_new_product_register.py` → `build_request_json()` 에 항목 추가 |
| **커밋** | — |

명세: `設定するには 14 日以上の販売実績が必要です` — 판매 이력 14일 이상이어야 설정할 수 있다.

## 16. reference_price (선택, 참고가=정가)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 이 목록에 묶인 **멤버들의 정가 중 최댓값** |
| **어디에 저장되나** | 저장하지 않는다. 재료는 `ace_products.original_price_jpy`(원화 정가 ÷ 10) |
| **어떻게 가공되나** | 멤버 정가 최댓값을 고른 뒤, **판매가보다 클 때만** 요청서에 넣는다(작으면 바이마가 거부) |
| **언제 나가나** | 신규등록·수정 요청 (조건 충족 시) |
| **단계** | CONVERT(정가 산출) → REGISTER·STOCK(전송) |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 규칙 한 곳 |
| **게시 후 편집** | 가능 — 그래서 이미 등록된 상품도 다음 수정 때 채워진다 |
| **바꾸려면 어디를** | 값 고르는 규칙: `okmall/reconcile_buyma_push.py` → `_reference_price_jpy()`<br>붙이는 조건: `okmall/buyma_new_product_register.py` → `build_request_json()` |
| **커밋** | — |

**왜 최댓값인가**: 같은 상품이면 정가도 하나여야 맞지만, 멤버가 2개 이상인 목록 36,945건 중 **85%가 몰마다 정가를 다르게** 적어놨다(세일가를 정가로 올린 몰이 섞여 있음). winner 값을 쓰면 소싱이 바뀔 때마다 화면의 정가가 흔들리므로 최댓값으로 고정한다.

## 17. available_until (필수, 판매 가능 기한)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 요청서를 만들 때 **그 자리에서 계산** — `오늘 + 90일` |
| **어디에 저장되나** | 저장하지 않는다 (요청서용으로는 DB를 읽지 않는다) |
| **어떻게 가공되나** | `YYYY/MM/DD` 형식으로 변환 |
| **언제 나가나** | 신규등록·수정 요청마다 항상. **요청이 나갈 때마다 만료일이 오늘 기준으로 다시 설정된다** |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 40개 몰 동일 |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `build_request_json()` 의 90일 계산 한 줄 |
| **커밋** | — |

명세상 최대 90일(`買付・販売が可能な期間（最大 90 日）`). 재고·가격이 바뀌어 수정이 나가는 상품은 그때마다 자동 연장된다.

## 18. buying_area_id (필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 코드에 박힌 **고정값 `2002003000`**(한국) |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 같은 값 |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | **불가** |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `BUYMA_FIXED_VALUES` |
| **커밋** | — |

## 19. buying_shop_name (선택, 매입처 이름)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 목록을 만들 때 브랜드명으로 만든다 — `<브랜드>正規販売店` |
| **어디에 저장되나** | `buyma_listings.buying_shop_name` |
| **어떻게 가공되나** | 일본어 괄호 제거 → 30자(반각) 초과 시 `正規販売店`→`正規店`, 그래도 넘으면 `BRAND 正規販売店`.<br>**축약까지 끝낸 값을 저장**한다(저장값 = 보낼 값) |
| **언제 나가나** | **신규등록에만.** 수정 요청에는 넣지 않는다 |
| **단계** | REGISTER |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>` |
| **공용 / 몰별** | 공용 규칙, 값은 브랜드마다 다름 |
| **게시 후 편집** | **불가** — 소싱 몰이 바뀌어도 바이마에 보이는 이름은 등록 당시 그대로 |
| **바꾸려면 어디를** | `okmall/reconcile_ensure_group.py` → `make_buying_shop_name()` **한 곳**<br>(축약은 `buyma_new_product_register.truncate_buying_shop_name()` 를 가져다 쓴다) |
| **커밋** | `d8de90e` |

**정체성 취급**: 값이 있으면 덮어쓰지 않는다. 비어 있고 아직 등록 전인 목록만 채운다.

## 20. shipping_area_id (필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 코드에 박힌 **고정값 `2002003000`**(한국) |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 같은 값 |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | **불가** |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `BUYMA_FIXED_VALUES` |
| **커밋** | — |

## 21. buyer_notes (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 만들지 않는다 |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** |
| **단계** | 해당 없음 |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | 가능 (우리만 보는 출품자 메모) |
| **바꾸려면 어디를** | 쓰려면 `okmall/buyma_new_product_register.py` → `build_request_json()` 에 항목 추가 |
| **커밋** | — |

## 22. duty (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 코드에 박힌 **고정값 `included`**(관세 포함) |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 `included` |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `BUYMA_FIXED_VALUES` (`none`/`included`/`refundable`) |
| **커밋** | — |

## 23. tags (선택)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 만들지 않는다 |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** |
| **단계** | 해당 없음 |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | 쓰려면 `okmall/buyma_new_product_register.py` → `build_request_json()` 에 항목 추가 |
| **커밋** | — |

## 25. shipping_methods (필수)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 코드에 박힌 **고정값 `[1063035]`** |
| **어디에 저장되나** | 저장하지 않는다 |
| **어떻게 가공되나** | `[{"shipping_method_id": 1063035}]` 형태로 감싼다 |
| **언제 나가나** | 신규등록·수정 요청마다 항상 같은 값 |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `BUYMA_FIXED_VALUES` |
| **커밋** | — |

## 26. style_numbers (선택, 품번)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 목록의 모델번호(`buyma_listings.model_no`)에서 매번 만든다 |
| **어디에 저장되나** | 저장하지 않는다 (모델번호에서 파생) |
| **어떻게 가공되나** | 모델번호 하나를 **3가지 표기**로 늘린다 — 원본 / 기호를 공백으로 / 영문숫자만.<br>같은 값은 하나로 합치고 `[{"number": ..., "memo": ""}]` 형태로 감싼다 |
| **언제 나가나** | 신규등록·수정 요청마다 새로 조립해 보냄. **단 브랜드 미등록(`brand_id=0`)이면 아예 안 보낸다** |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | **공용** — 변형 규칙 한 곳 |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | 변형 규칙: `okmall/buyma_new_product_register.py` → `generate_model_no_variants()`<br>브랜드 없을 때 제외: 같은 파일 `build_request_json()` |
| **커밋** | — |

**브랜드 없으면 못 보낸다 (2026-08-05 실측)**: `brand_id=0` 인 게시중 상품에 품번을 붙여 수정 요청을 보냈더니 바이마가 거부했다.

```
errors: style_numbers[0].number
   "品番はブランドを指定した場合に設定できます。"
```

## 28. size_unit (선택, 치수 단위)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 만들지 않는다 |
| **어디에 저장되나** | `buyma_listings.size_unit` 컬럼은 있으나 **전 행(145,541건) 비어 있다** |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** — 컬럼은 있지만 요청서에 넣지 않는다 |
| **단계** | 해당 없음 |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | 쓰려면 `okmall/buyma_new_product_register.py` → `build_request_json()` 에 항목 추가 |
| **커밋** | — |

**단위 없이 숫자만 나가고 있다**: 사이즈 옵션의 상세치수(`options[].details`)는 보낸다 —
`肩幅 40.5 / 胸囲 91.0 / 袖丈 50.5 / 着丈 66.0` 같은 값이 `ace_product_options.details_json` 에
92,584건 있고, 카테고리 허용 키만 걸러 나간다. 단위를 안 보내므로 바이마도 `size_unit: null` 로
들고 있다(웹훅 34,695건 전부 null, 그중 상세치수까지 들어간 것 1,376건). 값 자체는 한국 몰에서 온 cm 기준이다.

명세: 설정 가능한 단위는 카테고리마다 다르고 `units.csv` 에 있다. `cm` · `inch` · `号` 는 전 카테고리 공통,
`g` · `mg` · `ml` · `%` 는 화장품·와인·전자담배 등 일부 카테고리 전용.

## 31. order_quantity (조건부 필수, 주문 가능 수량)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 요청서를 만들 때 즉석에서 만든다. `random.randint(90, 100)` |
| **어디에 저장되나** | **저장하지 않는다** — DB 컬럼이 없다 |
| **어떻게 가공되나** | 가공 없음. 정상 등록·수정은 90~100 난수, 하차는 `0` |
| **언제 나가나** | 신규등록·수정 요청마다 새 난수. 하차(재고 API)는 `0` |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py` |
| **공용 / 몰별** | **공용** — 33몰 같은 방식 |
| **게시 후 편집** | 가능 |
| **바꾸려면 어디를** | `okmall/buyma_new_product_register.py` → `build_request_json()` 의 `random.randint(90, 100)`<br>하차값: 같은 파일 `call_buyma_variants_soldout()` 의 `order_quantity: 0`<br>⚠️ `fast_price_updater.py` 에 같은 조립식이 한 벌 더 있다(789·868행) - fast_price |
| **커밋** | (조사만 함, 코드 변경 없음) |

**"팔 물건이 있다"는 표시일 뿐이다.** 우리는 전 옵션을 `purchase_for_order`(주문 후 매입)로 올려
개별 재고수를 안 보내므로, 상품 전체의 주문 가능 수량을 이 값으로 준다.
실제 하차는 이 값이 아니라 재고 API 의 `0` 이 한다.

**주문이 들어오면 바이마가 1씩 깎고, 우리는 다음 수정 때 새 난수로 되돌린다.**

## 32. shop_urls (선택, 매입처 주소)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | 그 목록에 딸린 **소싱처 전부**(`source_offerings`)에서 매번 만든다 |
| **어디에 저장되나** | 저장하지 않는다 (소싱처 정보로 매번 조립) |
| **어떻게 가공되나** | winner 맨 앞 + 나머지는 매입가 싼 순.<br>label=`몰 ₩매입가`, description=재고 있는 옵션.<br>**15칸 상한** — 초과 시 422 거부라 비싼 몰부터 자른다 |
| **언제 나가나** | 신규등록·수정 요청마다 새로 조립해 보냄 |
| **단계** | REGISTER, STOCK |
| **실행 파일** | REGISTER: `okmall/reconcile_runner.py --mode auto --scope new --source <몰>`<br>STOCK: `<몰폴더>/stock_price_synchronizer_*_merge.py --source <몰>` |
| **공용 / 몰별** | 공용 규칙, 내용은 목록마다 다름 |
| **게시 후 편집** | **가능** — 매입처 이름은 못 바꾸므로 이 값이 실질 소싱 정보 |
| **바꾸려면 어디를** | `okmall/reconcile_buyma_push.py` → `_shop_urls()` / 상한은 `reg.MAX_SHOP_URLS` |
| **커밋** | — |

## 33. updated_at (읽기 전용)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **바이마가 기록한다** |
| **어디에 저장되나** | 저장하지 않는다 (웹훅 응답 안에 들어옴) |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** |
| **단계** | — |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | — (읽기 전용) |
| **바꾸려면 어디를** | — |
| **커밋** | — |

## 34. created_at (읽기 전용)

| 항목 | 내용 |
|---|---|
| **어디서 생기나** | **바이마가 기록한다** (상품이 처음 만들어진 시각) |
| **어디에 저장되나** | 저장하지 않는다. 우리 게시일수는 `buyma_listing_days` 에서 따로 관리 |
| **어떻게 가공되나** | 없음 |
| **언제 나가나** | **나가지 않는다** |
| **단계** | — |
| **실행 파일** | — |
| **공용 / 몰별** | — |
| **게시 후 편집** | — (읽기 전용) |
| **바꾸려면 어디를** | — |
| **커밋** | — |

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

### 명세
필수(`商品名`). **게시 후 편집 불가**(`公開後に編集できません` 목록에 포함).
길이 제한·금지문자는 명세에 명시돼 있지 않다. 우리는 반각 60자로 자른다.

### 값이 흐르는 길 (실제 값)

`listing#17` / `ace#547698` / buyma `133558230` / 몰 bblue

| 단계 | 테이블·컬럼 | 값 |
|---|---|---|
| ① COLLECT | `raw_scraped_data.product_name` | `아디다스 골프 남성 얼티밋365 테이퍼드 팬츠 (IT7859)` |
| | `raw_scraped_data.brand_name_en` / `model_id` | `아디다스` / `IT7859` |
| ② CONVERT ③ TRANSLATE | `ace_products.name` | `送料・関税込 \| adidas \| アディダス ゴルフ メンズ アルティメット365 テーパードパンツ (IT7859) IT7859` |
| ④ REGISTER | `buyma_listings.name` | `送料・関税込 \| adidas \| メンズ アルティメット365 テーパード パンツ 32 股下 IT7859` |
| | `buyma_listings.locked_name` | 위와 같은 값 (등록 성공 시 굳힘) |
| ⑤ 전송 | 저장하지 않음 | 60자로 자른 값 |
| ⑥ 바이마 | 웹훅 응답 `name` | 우리가 보낸 값 그대로 |

②③ 값과 ④ 값이 다른 이유는 **④가 다른 몰의 ace 이기 때문**이다. 목록 이름은 그룹의 **대표(seed)** 에서 온다.

### 대표(seed)는 어떻게 정하나

`reconcile_ensure_group._seed_ace()` — **몰 우선순위 → 살아있는 것 우선 → 낮은 번호** 순으로 하나를 고른다.
위 예에서 그룹 멤버는 okmall(ace#20447)과 bblue(ace#547698) 둘인데, okmall 우선순위가 높아 okmall 이름이 쓰였다.

- **winner 와 다르다.** winner 는 가장 싼 소싱처(가격·재고 판단용, 매 사이클 바뀔 수 있음),
  seed 는 정체성 대표(이름·브랜드·카테고리). 가격 때문에 winner 가 바뀌어도 이름은 흔들리지 않는다.
- 등록된 뒤에는 대표가 바뀌어도 목록 이름을 갱신하지 않는다(게시 후 편집 불가).

### 이름 정리 — 무엇을 지우나

규칙은 **`okmall/name_rules.py` 한 파일**에 모여 있다. 수집기와 변환기가 `clean_product_name(몰이름, 이름)` 한 줄로 부른다.

| 몰 | 지우는 것 |
|---|---|
| shinsegae | `(신세계 강남점)` 같은 백화점 지점명, `[신세계백화점]` 태그 |
| luxlimit | `(국내백화점)` `(국내매장판)` `(관부가세포함)` |
| larlashoes | `(국내매장판)` 및 오타 `국냄매장판` |
| wardrobe | `[워드로브]` 스토어 태그 |
| thesogno | `19FW` 같은 2010년대 시즌코드 |
| artemoa | `6F` `5S` 같은 축약 시즌코드 |
| gimpooutlet | 맨 앞 괄호 토큰 전부 (단 `스크래치` 든 괄호는 남긴다 — 제외 규칙이 걸러야 하므로) |
| milanosangin | `(당일)` |
| dmont·tuttobene·maniaon·unico·luvgrande·pano | 각자 스토어 태그 |
| 9tems | 홍보 문구 `럭키찬스` |
| brickmansion | 대괄호 태그 (단 콜라보 `[A x B]` 는 대괄호만 벗기고 내용은 남긴다) |
| loromoda | `[로로모다]` 접두어 |
| laprima | HTML에서 딸려오는 `상품명` 라벨 |
| labellusso·nextzennpack | 앞 `[브랜드명]` (변환기가 브랜드를 따로 붙이므로 중복 방지) |

**전 몰 공통 2개**: 국내판매 마커(`[국내...]`·`관부가세포함`) 제거, 시즌코드(`26SS`·`25FW` 등) 제거.

★단 **자체몰 6개(9tems·brickmansion·loromoda·laprima·labellusso·nextzennpack)에는 공통 규칙을 적용하지 않는다**(`GLOBAL_SKIP`).
이 몰들은 **정리된 상품명에서 모델번호를 뽑아내는데**, 시즌코드 규칙이 모델번호 안의 색상코드(`20F`·`23S`·`27S`)까지
같이 지워 모델번호를 깨뜨리기 때문이다. (2026-08-05 실측: 적용했다면 23건이 깨짐 — 예 `IGELONG-1C00006-20F` → `IGELONG-1C00006-`)
네이버 계열은 모델번호를 상품명이 아닌 다른 항목에서 뽑아 이 문제가 없다.

**조립 형식은 공용**이다: `送料・関税込 | 브랜드 | 상품명 모델번호` (`format_buyma_product_name()`).

### 규칙을 한 곳으로 모은 작업 (2026-08-05)

전에는 같은 성격의 규칙이 수집기 20개에 흩어져 있었다. 규칙 하나 고치려면 파일을 여러 개 뒤져야 했다.

- `okmall/name_rules.py` 를 만들어 몰별 규칙 20개 + 공통 규칙 2개를 담았다.
- 수집기 7개(네이버 계열 1 + 자체몰 6)가 이 함수를 부르도록 바꿨다.
- **변환기 2개**(`raw_to_ace_converter.py`·`raw_to_converter_kasina.py`)도 부르게 했다.
  → 규칙을 고치면 **재수집 없이 재변환만으로** 이미 모아 둔 상품에 반영된다.

**동작 대조**: 저장된 상품명 468,144건에 옛 규칙과 새 규칙을 각각 돌려 비교했다.
다른 것은 **45건뿐이고 전부 "이중 공백 → 한 칸"** 이다(laprima 32·labellusso 12·nextzennpack 1).

**멱등 확인**: 468,144건 전부 두 번 돌려도 결과가 같다.
처음엔 3건이 깨졌는데(`[메종 키츠네] [23SS]` 처럼 대괄호가 연달아 붙은 경우 하나만 지워짐),
labellusso·nextzennpack 규칙을 반복 적용되게 고쳐 해결했다.

**옮기지 않은 것**: 모델번호·브랜드를 뽑기 위해 임시로 쓰는 정규식(brickmansion·maisonparco·milaneez)과
okmall 의 "괄호 앞까지만 쓴다"(파싱에 가깝다)는 그대로 뒀다.

### 실측 (2026-08-04)

| 항목 | 값 |
|---|---|
| 이름 없는 목록 | 0건 |
| 굳힌 이름 보유 | 게시중 82,079건 중 82,014건 |
| **60자 넘어 잘리는 이름** | 145,556건 중 **133,404건 (92%)** |
| 바이마 값과 대조 | 일치 30,469 / **불일치 4,217** / 웹훅에 이름 없어 판정 불가 14,372 |

불일치 4,217건은 전부 같은 원인이었다 — **자른 자리가 공백이라 끝에 공백이 남고, 바이마는 저장하며 지운다.**
`truncate_buyma_name()` 이 자른 뒤 끝 공백을 제거하도록 고쳐, 다음 수정 요청부터 값이 일치한다.

### 등록 전 목록이 옛 이름에 갇히던 문제

목록 이름은 그룹을 처음 만들 때 한 번 복사된다. 그런데 번역 배치는 `ace_products` 만 갱신하므로,
**그룹이 번역보다 먼저 만들어지면 목록에 한국어 이름이 굳는다.**
등록 대상 선정에 "이름에 한글 있으면 제외" 조건이 있어 그 목록은 영원히 등록되지 않았다.

```
미등록 목록 중 이름에 한글 있는 것          4,313건
그중 대표 ace 는 이미 번역된 것             3,739건   ← 갇힘
```

`_refresh_pending_name()` 을 넣어 **등록 전 목록은 매 회차 대표 ace 이름을 다시 읽도록** 했다.
번역이 언제 끝나든 그 다음 회차에 자동으로 풀린다. 등록된 목록은 건드리지 않는다.

표본 30건 실행 결과: 바뀜 25 / 그대로 5 / 오류 0.
"그대로" 5건은 **대표 ace 가 아직 번역 전**인 경우로, 번역되면 다음 회차에 풀린다.

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

---

## 5. status

### 명세
`商品ステータス` — **읽기 전용**. 요청서에 넣는 값이 아니라 바이마가 정해서 알려준다. 값은 9가지.

| 값 | 뜻 |
|---|---|
| `public` | 출품중 |
| `soldout` | 매진 |
| `draft` | 임시저장 |
| `buyer_suspended` | 출품정지중 (셀러가 내림) |
| `admin_suspended` | 사무국 정지 |
| `in_review` | 심사중 |
| `not_approved` | 비승인 |
| `buyer_deleted` | 삭제 (셀러가 지움) |
| `admin_deleted` | 사무국 삭제 |

### 우리 처리 — 바이마 값을 그대로 안 쓰고 번역한다

`buyma_listings.status` 에는 **우리 값 6가지**가 들어간다. 바이마 값과 이름이 겹치는 게 있어 헷갈리기 쉽다.

| 우리 값 | 누가 넣나 | 뜻 |
|---|---|---|
| `pending` | 전송 직후(우리) | 신규등록 요청을 보냈고 결과 대기 |
| `api_error` | 전송 실패(우리) | 요청 자체가 안 나감 |
| `success` | 웹훅 | 등록 확정 (+ 상품번호·게시중) |
| `soldout` | 웹훅 (`buyer_suspended`) | 출품정지중 — 상품번호는 유지 |
| `deleted` | 웹훅 (`buyer_deleted`) | 삭제됨 |
| `fail` | 웹훅 (등록·수정 실패) | 바이마가 거부 |

### 이 값으로 무엇이 갈리나

| 어디 | 판단 |
|---|---|
| 신규등록 대상 선정 | `pending`·`fail`·`success` 는 **제외** (다시 안 올림) |
| 정지분 되살리기 | `soldout` + 상품번호 있음 → 같은 번호로 재게시 |
| 삭제분 | `deleted` 는 재수집·재변환으로 풀리기 전까지 등록 안 함 |
| 이미 게시중 판정 | `pending`·`success` 이거나 게시중이면 stock 담당으로 넘김 |

### 실측 (2026-08-04)

| 우리 값 | 건수 | 그중 게시중 |
|---|---|---|
| `success` | 63,404 | 63,292 |
| (없음) | 38,616 | 0 |
| **`fail`** | **30,425** | **18,803** |
| `soldout` | 9,087 | 0 |
| `api_error` | 2,901 | 0 |
| `deleted` | 30 | 0 |

### 남은 문제

**① 바이마 9값 중 4개를 안 본다.**
웹훅은 `buyer_deleted`·`buyer_suspended` 만 따로 처리하고, 나머지는 "상품번호가 있으면 성공" 으로 넘긴다.
그래서 `admin_suspended`(사무국 정지)·`not_approved`(비승인)·`in_review`(심사중)·`admin_deleted`(사무국 삭제) 가 오면
**`success` + 게시중으로 기록된다.** 실제로는 안 팔리는 상품이 우리 장부에는 정상으로 남는다.
(지금은 `buyma_cleaners/buyma_suspended_cleaner.py` 가 웹을 긁어 따로 잡고 있다 — unified 밖)

**② `fail` 30,425건이 방치돼 있다.**

| 형태 | 건수 | 상태 |
|---|---|---|
| `fail` + 게시중 + 상품번호 있음 | 18,800 | 상품은 살아 있는데 **마지막 수정이 실패**한 채로 남음 |
| `fail` + 미게시 + 상품번호 없음 | 11,188 | 등록이 거부됐고 **신규등록 대상에서 영구 제외**(`fail` 이라서) |
| `fail` + 미게시 + 상품번호 있음 | 434 | 상품은 있는데 우리 장부는 미게시 |
| `api_error` | 2,901 | 전송 자체가 실패, 역시 재시도 안 됨 |

`fail`·`api_error` 는 한 번 찍히면 스스로 풀리지 않는다. 실패 이유(한글 옵션·이미지 없음·계정 제한 등)를 고친 뒤에도
그 값이 남아 있어 재시도 대상이 되지 않는다. **실패 사유별로 나눠 되살리는 절차가 필요하다.**

---

## 6. comments

### 명세
**필수**, 최대 **3,000자(UTF-8)**. `商品の詳細について説明文を入力します`
HTML 서식은 안 되고, 이모지·특수문자는 2~4자로 계산된다. 등록 후에도 수정할 수 있다(이름·브랜드·카테고리와 달리 불변이 아님).

※ 색·사이즈 보충 설명은 별도 파라미터 `colorsize_comments` 다. 이름이 비슷해 섞이기 쉬우니 주의.

### 우리 처리 — 저장하지 않고 매번 조립한다

`comments` 라는 DB 컬럼은 없다. 전송 직전에 세 조각을 이어붙인다.

```
comments =  상품명(일본어, 자르지 않은 원본)
            + 모델번호 3가지 표기          예: 5125-MNL / 5125 MNL / 5125MNL
            + 고정 안내문 (코드 상수, 1,142자 / 42줄)
```

고정 안내문에는 정품 구매처 안내, 안심플러스 반품 보증, 주문~배송 흐름,
**배송사(SAGAWA)와 소요일**, 불량 판정 기준 등이 들어 있다.

### 어디를 거쳐 나가나 — 부르는 곳은 두 줄뿐

```
REGISTER  reconcile_runner --scope new
            → execute_create → build_create_request → reg.build_request_json   (신규)
            → execute_edit   → build_edit_request   → reg.build_request_json   (정지분 부활)

STOCK     stock_..._merge.py  재고 갱신 → _reconcile_published()
            → reconcile_runner.process_one_group(scope='published')
              → execute_edit → build_edit_request → reg.build_request_json     (재고·가격 반영)
```

**재고동기화 파일은 이 함수를 직접 부르지 않는다.** 예전엔 몰별 파일이 각자 요청서를 만들었으나
(2026-08-04에 호출 0건인 죽은 함수로 확인돼 삭제), 지금은 재고만 갱신하고 전송은 reconcile 이 맡는다.
따라서 안내문을 고치면 **등록·수정 양쪽에 동시에 반영**되고, 몰별로 달라지지 않는다.

### 실측 (2026-08-04)

게시중 상품 7건의 요청서를 실제로 만들어 길이를 쟀다.

| | 값 |
|---|---|
| 고정 안내문 | 1,142자 (42줄) |
| 실제 나가는 comments | **1,217 ~ 1,281자** |
| 한도 | 3,000자 → 여유 1,700자 이상 |

### 알아둘 점

1. **상품명이 두 번 나간다** — `name` 필드에도, `comments` 첫 줄에도.
2. `name` 은 60자로 자르지만 `comments` 안의 상품명은 **자르지 않은 원본**이다. 긴 이름은 상세설명에서만 온전히 보인다.
3. 안내문에 **배송사·소요일 같은 운영 정보가 하드코딩**돼 있다. 2026-07-22 배송사를 OCS→SAGAWA 로 바꾼 것이 이 상수 수정이었다.
4. `fast_price_updater.py` 에 같은 조립식이 한 벌 더 있다(현재 미가동). 안내문을 고칠 때 함께 고쳐야 어긋나지 않는다.

---

# 부록: 안 쓰는 컬럼 정리 (2026-08-04)

`run_daily_unified.py` 실행 코드에서 읽는 곳이 0이 된 컬럼 5개를 지웠다.

| 테이블 | 컬럼 | 지운 이유 |
|---|---|---|
| `ace_products` | `control` | 항상 코드 상수 `publish` 로 보냄. DB 값은 판단에 안 씀 |
| `ace_products` | `reference_number` | 등록 직전 `buyma_listings` 에만 발급. ace 값은 93.6%가 헛번호였음 |
| `ace_products` | `buyma_product_id` | 웹훅이 목록에만 기록. 등록판정도 목록 기준으로 일원화됨 |
| `buyma_listings` | `control` | 전 행 `draft` 고정, 읽는 코드 0 |
| `buyma_listings` | `comments` | 전 행 비어 있음, 읽는 코드 0 (요청서는 매번 조립) |
| `ace_products` | `size_unit` | 전 행 비어 있음(752,106건 전부 NULL), 읽는 코드 0. 단위는 `buyma_listings` 쪽 컬럼만 남긴다 (2026-08-05) |
| `ace_product_api_logs` | 테이블 통째 | 웹훅 응답 53,040건을 목록당 최신 1건씩 `buyma_listing_api_logs` 로 옮기고 DROP. 소속 없어 못 옮긴 1,579건은 버림. 읽는 코드 0, unified·웹훅 미사용 (2026-08-05) |

**실행**: `migrations/drop_ace_identity_columns.py --execute`

- 지우기 전 `bak_ace_identity_20260804` 에 748,287행 백업(ace 3컬럼 + 게시상태)
- 인덱스 먼저 제거 → 컬럼은 `ALGORITHM=INSTANT` 로 삭제. **테이블 잠금 없이 즉시 완료**
- `idx_published_active` 는 `(is_published, is_active, buyma_product_id)` 였다 → 앞 두 컬럼으로 재생성

**삭제 후 검증**: 재고동기화 dry-run 2/2 성공 · 신규등록 빌드 1/1 · 수정 빌드 1/1 ·
변환 INSERT 구문 정상(실행 후 롤백) · 가격 대상 조회 정상 · 가동 중인 자동화 로그에 오류 0

**주의**: unified 밖 도구(`buyma_cleaners/*`, `fast_price_updater.py`, `buyma_stats/*`,
`thumbnail_buyma_apply.py`)는 아직 `ace_products.buyma_product_id` 를 읽는다. 실행하면 깨진다.

---

## 7·8. brand_id · brand_name

### 명세
`brand_id` **필수**, `brand_name` 선택. **둘 다 게시 후 편집 불가**(`公開後に編集できません` 목록).

### 값이 흐르는 길

```
mall_brands.buyma_brand_id / buyma_brand_name      ← 사람이 손으로 매핑
   ↓ 변환기
ace_products.brand_id / brand_name
   ↓ 그룹 대표(seed)
buyma_listings.brand_id / brand_name
   ↓ 등록 접수 성공 시
buyma_listings.locked_brand_id                     ← brand_name 은 굳히는 칸이 없다
```

재고 API(variants.json)에는 브랜드가 **안 들어간다.** 그룹을 묶는 열쇠로만 쓴다.

### 실측 (2026-08-05)

| 항목 | 값 |
|---|---|
| 게시중 목록 | 82,011 |
| `brand_id`=0 (→ `brand_name` 을 같이 보냄) | 6,329 |
| `locked_brand_id` 없음 | 65 |
| `locked_brand_id` ≠ 현재 `brand_id` | 2,543 |
| `mall_brands` 매핑 행 / 그중 미매핑 | 7,424 / 2,080 |
| `ace_products` 활성 / 그중 `brand_id`=0 | 739,191 / 42,289 |

**바이마 값과 대조** (웹훅 응답의 최상위 `brand_id`·`brand_name`)

| | 일치 | 불일치 | 웹훅에 없음 |
|---|---|---|---|
| `brand_id` | 31,245 | **1** | 4,607 |
| `brand_name` | 2,290 | **0** | 33,563 |

`locked_brand_id`≠현재값이 2,543건이지만 **수정 때 굳힌 값을 보내므로 문제되지 않는다.**

### 0 을 0 으로 안 보내는 곳이 있다

두 곳이 같은 자리를 다르게 판정한다.

```python
# reconcile_buyma_push.py — 맞음. 0 도 그대로 보낸다
return v if (locked and v not in (None, '')) else pub.get(fb)

# fast_price_updater.py:764 — 틀림. 0 이 거짓이라 현재값으로 넘어간다
api_brand_id = product.get('locked_brand_id') or product['brand_id']
```

`brand_id` 는 편집 불가라, 바이마엔 0인데 다른 값을 보내면 **요청 전체가 거부**된다. → 할 일 참조.

### 브랜드 때문에 등록이 막힌 것 — 1,004건

웹훅 `errors.brand_name` 원문으로 갈랐다.

| 유형 | 건수 | 뜻 |
|---|---|---|
| A. 브랜드명만 거부 | 676 | 브랜드명 자체 문제 |
| B. 다른 항목도 같은 문자로 거부 | 31 | 상품 전체 문자 문제 |
| C. 브랜드명 + 무관한 다른 에러 | 297 | 브랜드명 문제 + 별개 사유 |

**A-1 「選択できないブランド」 541건 — 브랜드 2종뿐**

```
533  THOM BROWNE(トムブラウン)
  8  AJO AJOBYAJO(アジョ バイ アジョ)
```
글자 문제가 아니라 **바이마에서 그 브랜드를 고를 수 없다**는 뜻이다.

**A-2 「使用できない文字」 135건 — 브랜드 6종**

```
50  'C.A_ART PROJECT'    특수문자 . _
42  'I.ENOMOTO'          특수문자 .
27  'i.el’d'             특수문자 . ’
 8  'B.EAUTIFUL'         특수문자 .
 6  'N.(エヌドット)'      특수문자 . ( )
 2  'adidas(アディダス)'  특수문자 ( )
```
6종 중 5종에 마침표가 있다. 다만 바이마가 어느 글자인지 알려주지 않아 **단정하지 않는다.**
괄호는 6종 중 2종뿐이고 그중 하나는 2건이라 **괄호를 원인으로 볼 근거는 없다.**

**B 31건** — `comments`(28)·`name`(28)·`buying_shop_name`(19) 이 같이 거부됐고,
바이마가 지목한 문자는 전부 ` `(줄바꿈 없는 공백) 였다. 브랜드명 탓이 아니다.

### 굳힌 브랜드ID가 바이마와 달라 영구 수정 불가였던 12건 (2026-08-05 정리)

「ブランドIDは変更できません」 로 **가격·재고 포함 모든 수정이 거부**되던 목록 12건.
바이마 화면에서 직접 삭제하고 DB 는 raw 까지 통째로 지웠다(1,582행).

```
백업: migrations/delete_brandid_mismatch_12_backup_20260805_132829.json
```

딸려 있던 미등록 목록 3건(ROA 티셔츠)도 같이 지웠다 — 그 목록의 소싱이 이 ace 하나뿐이라
안 지우면 빈 껍데기만 남기 때문. 바이마 미등록이라 영향 없음.

지우기 전에 재고 API 로 출품정지를 시도했으나 **12건 전부 거부**됐다.
사유는 브랜드가 아니라 옵션 문제였고, 같은 사유로 1,628건이 하차에 실패하고 있다 → 할 일 참조.

---

## 31. order_quantity

### 명세
`purchase_for_order`(주문 후 매입)를 쓰면 **필수**. 상품 전체의 주문 가능 수량.

### 왜 필요한가

우리는 재고가 있으면 무조건 `purchase_for_order` 로 올린다(`raw_to_ace_converter.py:1035`).
이 방식은 옵션별 재고수(`stocks`)를 보내면 거부되므로, 대신 상품 단위 수량을 이 값으로 준다.

### 값은 두 가지뿐

| 상황 | 값 | 보내는 곳 |
|---|---|---|
| 정상 등록·수정 | `random.randint(90, 100)` | `build_request_json()` (register 540행) |
| 하차(출품정지) | `0` | `call_buyma_variants_soldout()` (register 675행) |

하차 때 **0 이 필수**다. 전 변이가 `out_of_stock` 이면 팔 수 있는 변이가 0개인데
기존 수량이 남아 있으면 충돌해 요청이 거부된다.

### 실측 (2026-08-05, 웹훅 응답 표본 25,000건 중 값이 있는 15,495건)

```
  0 :     10건   ← 전부 하차된 상품 (바이마 status='soldout')
 89 :      2건   ← 아래 참조
 90 :  1,407     95 :  1,477
 91 :  1,359     96 :  1,377
 92 :  1,367     97 :  1,417
 93 :  1,418     98 :  1,397
 94 :  1,460     99 :  1,390      100 : 1,414
```

90~100 이 고르게 나온다 — 난수 그대로다.

### 바이마가 이 값을 깎는다

주문이 들어오면 바이마가 1씩 깎는다. 우리 코드는 90~100 만 만드는데 웹훅에 **89** 가 돌아온 것으로 확인했다.

```
listing#3027   {"id":133936219, "status":"public", "order_quantity":89, …}   주문 1건(7/19 판매완료)
listing#30561  {"id":132310662, "status":"public", "order_quantity":89, …}   주문 1건(8/1 취소)
```

우리는 재고 동기화가 돌 때마다 새 난수(90~100)를 보내 되돌린다. 90개나 팔릴 상품이 없어 실질 영향은 없다.

**우리 DB 에는 이 값이 없지만** 웹훅 응답 원문이 `buyma_listing_api_logs.api_response_json` 에
통째로 저장돼 있어, 바이마 쪽 현재값을 그 안에서 볼 수 있다.

---

## 19. buying_shop_name

### 명세
매입처(買付先ショップ名). 반각 30자. **게시 후 변경 불가.**

### 우리 처리 — 목록이 단독 보관, 등록 후엔 손대지 않는다

```
목록 생성   make_buying_shop_name(브랜드명)  →  buyma_listings.buying_shop_name
등록(CREATE) 그 값을 요청서에 넣어 보냄 (30자 초과 시 축약)
수정(EDIT)  ★ 넣지 않는다 — 편집 불가 값이라 다르면 요청 전체가 거부되므로
```

- 값이 이미 있으면 **절대 덮어쓰지 않는다.** 비어 있고 아직 등록 전인 목록만 채운다.
- 2026-08-04 이전에는 매 사이클 winner(가장 싼 소싱몰)의 ace 값으로 목록을 덮어썼다.
  바이마 값은 등록 당시 그대로인데 우리 값만 바뀌어 장부가 어긋났다. 그 동작을 멈췄다.

### 왜 재생성하지 않고 저장된 값을 쓰나 (실측)

게시중 82,080건을 지금 규칙으로 다시 만들어보니 **12,769건(15%)이 저장값과 달랐다.**

```
listing=1  brand='A.P.C.(アーペーセー)'  저장='A.P.C正規販売店'   재생성='A.P.C.正規販売店'
listing=2  같은 브랜드                    저장='A.P.C.正規販売店'  ← 같은 브랜드인데 저장값이 서로 다름
```

과거 규칙으로 만들어진 값이 섞여 있다. 재생성하면 바이마와 어긋난 값으로 덮어쓰게 되므로,
**저장된 값을 진실로 삼는다.**

### 바이마 값과 대조 (2026-08-04)

웹훅이 돌려준 상품 JSON(`buyma_listing_api_logs.api_response_json`)에 이 값이 들어 있어 직접 대조했다.

| 결과 | 건수 |
|---|---|
| 일치 | 33,992 |
| **어긋남** | **690** → 바이마 값으로 정정(689건, 삭제분 1건 제외) |
| 웹훅에 값이 없어 판정 불가 | 14,373 |

어긋난 유형: 대소문자(`Nike` vs `NIKE`), 점 유무(`A.P.C.` vs `A.P.C`), 그리고 **브랜드 자체가 다른 것**.
브랜드가 다른 1건(listing#2, A.P.C. 상품인데 매입처가 `HERNO正規販売店`)은 매입처를 고칠 수 없으므로 **삭제**했다.

- 정정 스크립트: `migrations/fix_buying_shop_name_from_webhook.py`
- ace 컬럼 제거·백필: `migrations/move_buying_shop_name_to_listings.py`

### 편집 불가 규칙 실측 (listing#62 / buyma 135117241)

가격을 1엔씩 바꿔가며 세 가지 방식으로 실제 수정 요청을 보냈다.

| 매입처를 | 응답 | 웹훅 결과 |
|---|---|---|
| 안 보냄 | 201 | ✅ 가격 반영 (9,040→9,041, 9,041→9,039 두 번 재현) |
| 바이마와 같은 값 | 201 | ✅ 가격 반영 |
| **다른 값** | 201 | ❌ `{'buying_shop_name': ['買付先ショップ名は変更できません。']}` — **가격 변경까지 무산** |

**201 은 접수일 뿐이고 성패는 웹훅으로만 알 수 있다**는 것도 다시 확인됐다.

### 길이 축약을 생성 시점으로 옮김 (2026-08-04)

예전에는 **저장은 원본, 전송 직전에만 축약**했다. 그래서 30자를 넘는 브랜드는 DB 값과 BUYMA 값이 달랐다.

```
DB      'PLEATS PLEASE ISSEY MIYAKE正規販売店'   (36자)
BUYMA   축약본
```

정체성으로 삼는 값이 실제로 보낸 값과 다르면 안 되므로, `make_buying_shop_name()` 이 **축약까지 끝낸 값**을 돌려주도록 바꿨다.

실측 180건의 처리:

| 분류 | 건수 | 처리 |
|---|---|---|
| 미등록(상품번호 없음) | 38 | 아직 안 보냈으므로 축약값으로 정정 (백업 `fix_shopname_len_backup_*.json`) |
| 등록됨 + 웹훅에 바이마 값 있음 | 0 | 해당 없음(689건 보정 때 이미 맞춰짐) |
| **등록됨 + 바이마 값 모름** | **142** | **손대지 않음** — 바이마가 어떤 축약본을 갖고 있는지 알 수 없다. 추측으로 덮으면 또 어긋난다 |

142건은 **수정 요청에 이 값을 안 보내는 한 무해**하다. 넣기 시작하면 그 상품들은 전부 거부된다.

---

# 할 일 (요약과 분리)

| 대상 | 내용 |
|---|---|
| `status` | 바이마가 주는 9가지 중 **4개를 안 본다** (`admin_suspended`·`not_approved`·`in_review`·`admin_deleted`) → 정지·비승인 상품이 `success`·게시중으로 기록될 수 있다 |
| `status` | `fail` 30,425건이 스스로 안 풀린다. 그중 11,188건은 신규등록 대상에서 영구 제외 상태 |
| unified 밖 | 청소도구·`fast_price_updater.py`·`buyma_stats/*` 가 사라진 ace 컬럼을 읽는다 — 실행하면 깨진다 |
| `buying_shop_name` | 게시중인데 값이 빈 76건 / 저장값이 30자를 넘는 등록분 142건. 수정 요청에 이 값을 안 보내므로 지금은 무해하다 |
| `brand_id` | `fast_price_updater.py:764` 가 `locked_brand_id or brand_id` 로 판정해 **0 을 거짓으로 취급**한다. 바이마엔 0인데 현재값을 보내 요청 전체가 거부된다. `reconcile_buyma_push.py` 는 `v not in (None,'')` 로 맞게 판정 — 두 곳을 같게 맞춰야 한다 |
| `brand_name` | 브랜드 때문에 등록 못 하는 1,004건. ① 「選択できないブランド」541건 = `THOM BROWNE` 533 + `AJO AJOBYAJO` 8 → 바이마가 그 브랜드를 안 받는다 ② 「使用できない文字」135건 = 브랜드 6종(`C.A_ART PROJECT`·`I.ENOMOTO`·`i.el’d`·`B.EAUTIFUL`·`N.(エヌドット)`·`adidas(アディダス)`) — 어느 글자인지는 바이마가 안 알려줘 미확정 ③ 31건은 브랜드가 아니라 상품 텍스트의 ` `(줄바꿈 없는 공백) 문제로, `comments`·`name`·`buying_shop_name` 도 같이 거부됨. 전부 `status='fail'` 이라 스스로 안 풀린다 |
| 하차(출품정지) | **재고 API 로는 안 내려간다 — 1,628건 실패**(게시중 647). 재고 API 는 `variants` 만 보내 **옵션 목록을 못 바꾸므로** 바이마에 등록된 옵션을 전부 덮어야 통과한다. 그런데 하차 코드는 정상 수정(`_build_ov`)이 하는 ①표기 통합(`Free`↔`FREE`) ②빠진 색×사이즈 격자 채우기 를 **둘 다 안 한다**. 실측: 격자 모자람 241 / 표기 다름 29 / 나머지 377은 바이마가 우리에게 없는 옵션 보유(읽기 API 없어 확인 불가). **해법: 하차를 상품 API 로 하면 `options` 를 다시 정의하므로 전부 풀린다**(`control:publish` 유지 → 삭제 아님, 상품번호·게시일수 유지) |
| 웹훅 | 2026-08-04 16:51~18:21 수신분 유실(서버가 옛 코드로 돌다 멈춤). 그 사이 등록·수정 결과가 DB에 반영되지 않았다 |
| 병합 | 26 조사 중 발견 — 한 목록에 **모델번호가 아예 다른 멤버**가 섞인 경우 2,079건(브랜드 있는 게시중 75,688건의 2.7%). 예: `699296 92TCG 8563` 목록에 `699296-UKMBG-2572` 멤버. 앞 번호만 같고 뒤 코드가 다르면 다른 상품이라 과병합 의심. style_numbers 와는 무관하며 실제 오병합 규모는 미확인 |

---

## 13~16. price · list_price · regular_price · reference_price

### 명세

| # | 파라미터 | 명세 원문 | 성격 |
|---|---|---|---|
| 13 | `price` | `配送料金を含まない商品価格を入力します` | **필수**, 쓰기 가능 |
| 14 | `list_price` | `商品価格と最も安い配送料の合計額が出品価格となります` | **읽기 전용** |
| 15 | `regular_price` | `設定するには 14 日以上の販売実績が必要です` | 선택, 조건부 |
| 16 | `reference_price` | `参考価格` | 선택 |

### price — 어떻게 정해지나

```
경쟁자 최저가 있음
   현재가가 [경쟁자-9, 경쟁자-1] 안 → 그대로 유지 (불필요한 수정 방지)
   아니면                        → 경쟁자 - 랜덤(1~9)엔

경쟁자 없음 (목표마진 30% 역산)
   판매가(원) = (매입가 + 배송비 - 매입가/11) / (1 - 0.055 - 0.30)
   판매가(엔) = 판매가(원) / 9.2
```

계산은 `okmall/resolve_merge.py` 한 곳. 결과를 `buyma_listings.price` 에 쓰고 요청서에 넣는다.

### reference_price — 2026-08-04 복구

**한동안 나가지 않고 있었다.** 빌더에는 "정가가 판매가보다 크면 붙인다"는 조건이 살아 있었는데,
reconcile 로 옮기면서 상품 정보에 `original_price_jpy` 를 담지 않아 조건이 항상 거짓이었다.
옛 등록 경로(`get_products_to_register`)는 이 값을 조회해 넘겼으므로, **이관 과정의 누락**이다.

확인한 사실:
- `ace_products` 활성 739,260건 중 **739,242건에 정가가 있다** — 데이터 문제가 아니었다
- 웹훅 응답 3,000건 중 참고가가 있는 건 7건뿐(옛 경로로 등록된 상품)

**멤버 정가가 제각각이라 최댓값을 쓴다**

| | |
|---|---|
| 멤버 2개 이상인 목록 | 36,945건 |
| 정가가 서로 **다름** | 31,718건 (85%) |
| 정가가 같음 | 5,227건 (14%) |

예: `listing=2` 멤버 7개의 정가가 14,891 ~ 41,304 (7종). 세일가를 정가로 올린 몰이 섞여 있다.
winner 값을 쓰면 소싱이 바뀔 때마다 화면의 정가가 오르내리므로 **멤버 중 최댓값으로 고정**한다.

복구 후 빌드 결과:
```
price  51,405 → reference_price 81,800
price  53,498 → reference_price 70,000
price 142,388 → reference_price 없음      ← 정가 < 판매가라 생략(의도한 동작)
```

참고가는 편집 가능한 값이라, 이미 등록된 상품도 **다음 수정 요청 때 자연히 채워진다**. 별도 보정이 필요 없다.

---

## 17. available_until

### 명세
필수. `YYYY/MM/DD` 문자열. `買付・販売が可能な期間（最大 90 日）です` — 최대 90일.
게시 후 편집 제한 목록에는 없다(수정 가능).

### 우리 처리 — DB를 읽지 않고 매번 새로 계산

```
build_request_json()  available_until = (오늘 + 90일).strftime('%Y/%m/%d')
```

`ace_products.available_until` 컬럼이 있지만 변환 시점에 찍어둔 값일 뿐, 요청서는 이 값을 쓰지 않는다.

그래서 **요청이 나갈 때마다 만료일이 오늘 기준 90일로 다시 설정된다.**
재고·가격이 바뀌어 수정이 나가는 상품은 자동으로 연장되므로, 별도 연장 작업이 필요 없다.

### 연장 배치는 unified 밖

`buyma_expiry_extender.py` 는 "아무 변화가 없어 수정이 안 나가는 상품"의 만료일만 밀어주는 별도 배치다.
`run_daily_unified.py` 에 속하지 않으며, 현재 서버 크론에서 주석 처리돼 있다.
