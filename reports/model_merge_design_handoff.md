# 모델명 중복 → 멀티소스 merge 설계 (작업 보류 / 재개용 핸드오프)

> 작성 2026-06-02. 급한 다른 건으로 **작업 보류**. 이 대화 노드는 폐기 예정이라, 재개 시 같은 논의를 반복하지 않도록 전 흐름을 기록.
>
> **현재 상태**: 설계 논의 거의 완료. **코드 변경 0건**(아직 아무것도 안 고침). 멈춘 지점 = **스키마 A vs B 최종 결정 미정**.
> **관련 코드 위치 인용은 조사 시점(2026-06-02) 기준** — 재개 시 라인번호는 다를 수 있으니 함수명/쿼리로 재확인할 것.

---

## 0. 한 줄 요약

같은 품번(model_no)이 여러 수집처(mall)에 있을 때, **지금은 mall 우선순위로 1개만 살리고 나머지를 죽인다.** 이게 가격/마진/재고를 안 봐서 "비싼 걸 살리고 싼 걸 죽이는" 손실을 낳음. 이를 **"여러 mall을 BUYMA 출품 1건으로 합치는(merge)" 구조**로 바꾸려 한다.

---

## 1. 문제 (왜 보완하나)

현재 중복 처리는 **단순 mall 서열**(okmall 최우선)로 살릴 하나를 정한다. 그 행의 **가격·마진·재고는 전혀 안 본다.**

### 결정적 시나리오 (관통 예시)
- BUYMA 경쟁자(다른 셀러) 최저가 = **8500**
- okmall: 사이즈 **90, 95** / 매입가 **10000**
- 카시나: 사이즈 **90, 100** / 매입가 **8000**
- 밀라니즈: 사이즈 **100** / 매입가 **7500**
- (마진은 배송비·수수료·환율 등 따지지만 예시에선 판매가만으로 단순화)

마진: okmall(10000>8500) ❌ 손해 / 카시나(8000) ✅ / 밀라니즈(7500) ✅ 최대

**지금 구조의 결과**:
1. dedup_corrector가 SOURCE_PRIORITY대로 **okmall을 살림**(is_active=1), 카시나·밀라니즈는 `status='duple', is_active=0`로 죽임.
2. register는 is_active=1만 보는데, okmall은 마진 안 나서 **등록 스킵**.
3. 마진 나는 카시나·밀라니즈는 이미 죽어서 **등록 후보에서 빠짐**.
4. → **팔 수 있었던 상품이 아무것도 안 올라가고 사라짐.**

---

## 2. 현재 구조 (중복 거르는 4개 장치)

| # | 장치 | 자동? | 매칭 키 | 동작 | 파괴성 |
|---|---|---|---|---|---|
| ① | `okmall/dedup_corrector.py` | ✅ 자동 (`run_daily_multisource`/`run_daily_naver` Phase 2, **PRICE 전**) | `canonicalize(model_id)` 정규화+fuzzy+브랜드스코프 | non-primary → `status='duple', is_active=0` | 비파괴 |
| ② | register `get_products_to_register` | ✅ 자동 (등록 쿼리 자체) | `model_no` **문자열 정확일치** | `model_no NOT IN (published)` 제외 | 비파괴 |
| ③ | register `--clean-duplicates` | ❌ 수동 | model_no 정확일치 | published 중복 `MIN(id)`만 남기고 삭제 | 파괴 |
| ④ | `buyma_cleaners/cleanup_duplicates.py` | ❌ 수동 | model_no/model_id 정확일치 | 같은 model_no 그룹 **전량 삭제(하나도 안 남김)** | 강한 파괴 |

### 중요한 사실들
- **okmall 단독 파이프라인(`orchestrator.py`)에는 ①(dedup)이 없음** → okmall 내부 중복은 ②에만 의존.
- **매칭 키 불일치**: ①은 똑똑(`ABC-123`=`ABC123`), ②③④는 문자열 그대로(다르게 봄). ①을 안 돌린 상태(특히 okmall)에선 표기차 중복이 ②에 안 걸려 **이중 등록** 가능.
- ④ `cleanup_duplicates`는 merge와 정면 충돌 → merge 도입 시 **절대 그냥 돌리면 안 됨**(멀티소스 그룹 통째 삭제).
- dedup은 **삭제가 아니라 is_active=0**로 죽이므로 → 죽은 멤버 행(카시나 등)과 그 옵션·source_url·매입가가 **DB에 그대로 보존됨**. merge에 필요한 데이터가 이미 다 있음(새로 수집 불필요).

---

## 3. 중복이 왜 생기나 (구조 이해)

converter(`raw_to_ace_converter.py`, `kasina/raw_to_converter_kasina.py`)는 **raw 1행 = ace 1행**(`raw_data_id`로 1:1, `get_existing_ace_product`). 한 raw 안의 여러 색상은 **그 한 ace의 variant/option으로 결합**.

→ 같은 model_no가 ace에 **여러 행**으로 생기는 원인은 변환 이전:
1. **멀티소스** (okmall·카시나·밀라니즈 등 여러 수집처에 같은 상품)
2. **한 몰 내 복수 리스팅** (색상별/카테고리별 별도 mall_product_id)
3. **표기차** (`ABC-123` vs `ABC123` vs `25FW-ABC123`)

---

## 4. 목표 (merge = option B)

같은 품번의 여러 mall을 **하나의 BUYMA 출품으로 묶는다.** 합의된 3규칙:

| 규칙 | 내용 |
|---|---|
| **① 메인(소싱처)** | 마진 나는 mall 중 **매입가 최저**를 메인으로 |
| **② 이미지** | 중복 mall **전부** 합쳐서 보냄 (단, 다른 몰 자체이미지가 있으면 **okmall W컨셉 추가수집은 생략** — 비용 절감) |
| **③ 옵션(색/사이즈)** | **마진 나는 mall만** 모아 합집합. 옵션별 출처는 그 옵션을 가진 최저가 mall |

### 예시 결과
- 메인 = 밀라니즈(7500)
- 옵션 = **90**(카시나) + **100**(밀라니즈·카시나 중 싼 밀라니즈) → **95는 okmall만 가져서 탈락**
- 이미지 = okmall+카시나+밀라니즈 전부
- BUYMA = 출품 1건 (90, 100)

### 소싱처의 진짜 의미 (사용자 설명)
- **BUYMA에 소싱처 판매가를 보내지 않음.**
- 주문 들어오면 **그때 최저가인 아무 데서나** 떼옴 (수집 안 하는 11번가 포함 가능).
- 그래서 "소싱처"는 구속력 있는 구매처가 아니라 **내부 기준점**: ①올릴 옵션 ②마진/판매가 계산 기준 매입가 ③stock sync가 재크롤할 대상.
- → 비싼 okmall을 메인으로 잡으면 마진 게이트가 "손해"로 오판해 **팔 수 있는 걸 안 올림**. 매입가 최저를 메인으로 = 마진 게이트가 정확해짐.

---

## 5. 재설계 파이프라인 (collector ~ register)

핵심 통찰: **경쟁자 최저가는 같은 품번이면 mall 무관 동일** → 그룹당 BUYMA 크롤 **1회**면 됨. mall별 마진은 각자 매입가로 **산수**만(추가 크롤 0). 그래서 "죽이는 시점"을 PRICE **뒤**(RESOLVE)로 옮긴다.

| 순서 | 단계 | 대상 | 오늘 대비 |
|---|---|---|---|
| 1 | COLLECT | mall별 raw (비-okmall은 자체이미지 포함) | 동일 |
| 2 | CONVERT | raw→ace, variant+매입가 | 동일 |
| 3 | **GROUP** | 중복 그룹 **식별만**(group_key 부여, 안 죽임) | **변경** (기존 dedup의 즉시 kill 제거) |
| 4 | **PRICE(그룹 1회)** | 그룹당 경쟁자 최저가 1회 크롤 → 판매가 결정 | **변경** |
| 5 | **RESOLVE(신규)** | mall별 마진 산수 → 마진O만 / winner=마진O 중 매입가 최저 / 옵션=마진O union / 이미지=union / 나머지 is_active=0 | **신규** (kill 시점 여기로) |
| 6 | IMAGE | winner 병합이미지 업로드. **중복이면 okmall W컨셉 스크랩 skip** (단 멤버 중 자체이미지 가진 곳 하나도 없으면 어쩔 수 없이 스크랩) | 변경 |
| 7 | TRANSLATE | **winner만** | 대상 동일 |
| 8 | REGISTER | winner 병합 출품 1건. **정체성(이름/브랜드/카테고리)은 대표행 기준, buyma_product_id는 대표행에만** | 변경 |

비싼 꼬리(번역·이미지업로드·등록)는 여전히 winner 1개에만 → 원래 노리던 효율 유지.

**실제 코드가 바뀌는 건 ③④⑤⑧** (①②⑥⑦은 거의 그대로).

---

## 6. stock sync 설계 (3단계 / 맨 나중 / 최고 위험)

등록된 출품마다 주기적으로 "그룹 reconcile"을 다시 돈다:

1. 그룹 멤버 모으기(winner + is_active=0 형제, group_key로 연결) — 각자 source_site/URL 보유
2. 멤버 mall **전부** 재수집(각 mall 기존 수집 로직 재사용) → 옵션별 현재 재고 + 매입가
3. 경쟁자 최저가 1회 크롤 → 판매가
4. 멤버별 마진 재산수
5. 판정: 마진O 멤버 없음 → 삭제 / winner'=마진O 중 매입가 최저 → 바뀌면 **메인 교체** / 옵션=마진O union
6. 현재 BUYMA와 diff → 바뀐 것만 edit push

→ **신규등록 / winner교체 / stock 동기화가 전부 같은 동작** = "그룹 reconcile → BUYMA와 비교 → 없으면 만들고 있으면 수정". **엔진 하나로** 구현.

### 안전 필수 (사용자 강조)
- **메인 교체는 행을 옮기지 않고 필드만 갱신**: buyma_product_id 가진 그 ace 행은 고정, `source_site`/`source_product_url`/`purchase_price`/`buying_shop`만 새 winner 값으로 UPDATE. (buyma_product_id를 행 사이로 옮기는 위험 회피)
- **BUYMA 불변 필드(name/brand/category)는 절대 안 건드림**: 다른 값 보내면 오류남. 이미 `locked_name`/`locked_brand_id`/`locked_category_id` 백업 필드 있음 → 수정 시 잠긴 값 그대로 재전송. winner 바뀌어도 이름은 손 안 댐.
- **매칭 키는 절대 일본어 안 씀**: `color_value_original`/`size_value_original` + `source_option_code`로만. (과거 5,320건 false-delete 근본 원인)
- 멤버 일시적 수집 실패 → 그 옵션 죽이지 말고 직전 상태 유지.
- 이미지는 sync에서 재병합 안 함(등록 set 유지) — 범위 최소화.

---

## 7. 영향 지도 (★ 이미 전수 조사 완료 — 재조사 금지)

ace_products를 읽는 28개 파일을 6개 "가정"에 대해 조사함. **merge가 깨뜨리는 가정**:

| # | 현재 코드가 믿는 가정 |
|---|---|
| 1 | ace 행 1개 = BUYMA 출품 1개 |
| 2 | is_active=0 = 죽은 것 (대부분 is_active=1만 조회) |
| 3 | model_no가 중복 판정 키 |
| 4 | 상품 1개 = source_site 1개 (각 stock sync가 자기 source만 처리) |
| 5 | stock 매칭 키 = color_value_original/size_value_original + source_option_code |
| 6 | buyma_product_id/is_published를 그 ace 행에 직접 write |

### 🔴 위험지대 (merge 전 반드시 손봐야 함)

**1. stock sync false-delete — 가장 치명적 (5,320건 사고와 동일 메커니즘)**
- 5개 sync(okmall/kasina/nextzennpack/labellusso/naver)의 `all_out_of_stock = all(v['stock_type']=='out_of_stock' for v in variants)`가 **ace_product_id의 전체 variant**(다른 mall 옵션 포함)로 계산됨. 그런데 각 sync는 **자기 mall 옵션만** 갱신(다른 mall 옵션은 매칭 실패→skip, 갱신 안 됨). → okmall분만 품절이면 카시나 재고가 있어도 **출품 통째 delete**. (조사 시점 라인: okmall `all_out_of_stock` ~L1113, kasina ~L906, nextzennpack ~L1077, labellusso ~L1141, naver ~L1121)
- collect 실패(404/품절) → `build_buyma_request(is_delete=True)`로 **출품 자체 삭제** (okmall ~L1413, kasina ~L1206, naver ~L1424). merge에서 메인 페이지 내려가면 다른 mall 재고 멀쩡해도 통째 삭제.
- `_delete_from_db`(okmall ~L1376)가 ace_product_id로 variants/options/images/ace + raw까지 cascade DELETE → 한 트리거로 모든 mall 데이터 소실.
- → **보강 방향**: 삭제 판정 분모를 "이번 sync가 본 source_site 옵션만"으로 한정. collect 실패 시 출품 삭제가 아니라 해당 source 옵션만 품절/비활성. 삭제는 "모든 소싱처 동시 소멸"일 때만.

**2. `cleanup_duplicates.py` (가장 위험한 정리도구)**
- Step1: `WHERE model_no ... is_active=1 GROUP BY model_no HAVING COUNT(*)>1` 그룹을 **전 멤버 hard-delete + published는 BUYMA delete** (대상선정 ~L126-147, `delete_ace_from_db` ~L75-81). merge 정의(같은 품번 멀티소스)와 정면 충돌 → 돌리면 그룹 즉시 파괴.

**3. register**
- `get_products_to_register`의 `model_no NOT IN (published)` (~L357-366) + `--clean-duplicates`(같은 model_no published 중 MIN만 남기고 삭제, ~L872-917)가 merge 의도와 충돌.

**4. converter** (1:1 사슬 발원지)
- `raw_data_id` 1:1 매핑(~L1070-1086), 단일 `source_site` INSERT(~L1156), variants **DELETE 후 재생성**(~L1125-1144) → 다른 mall variant 덮어씀. **variant가 출처(source)를 안 들고 있음** → 합치려면 variant에 출처 컬럼 필요.

**5. 웹훅 `okmall_reference/server.py`**
- 전 분기가 `WHERE reference_number = %s`로 write-back. reference_number는 **ace 행마다 생성**(converter ~L900, `generate_reference_number`). merge로 한 출품이 여러 행이면 **등록에 쓴 1개 행만** buyma_product_id/is_published 갱신, 나머지 멤버 불일치.

**6. inactive/unpublished cleaner**
- `buyma_inactive_mapping_cleaner.py`: 한 source 매핑만 is_active=0이어도 그 멤버 ace **hard-delete** + buyma_id 있으면 BUYMA delete.
- `buyma_unpublished_cleaner.py`: `is_published=0 AND buyma_id IS NOT NULL`을 유령으로 보고 BUYMA delete. merge 멤버가 buyma_id 잔존하면 살아있는 출품 오삭제.

### 🟢 안전지대 (안 깨짐 — 안심)
- **manage_server 목록·통계, `build_merged_dataset.py`**: 이미 **model_id 단위로 묶어서** 표시(`GROUP BY r.model_id`, `by_model`). 출품 수를 is_published 행 수로 세는 코드 없음 → 카운트 안 틀어짐. 단 대표행 1개(`published[0] else ace_list[0]`)만 보여줘서 멤버 출품 정보는 누락(표시 이슈일 뿐).
- **`buyma_low_view_cleaner.py`**: `is_active=1 AND is_published=1`만 대상 → 멤버(is_active=0) 안 건드림. hard-delete 없음. 무해.
- **r2_image_uploader, 번역(gemini)**: image-row/표시값 단위라 영향 작음. gemini는 `*_original` 안 건드리도록 이미 방어됨.
- 주의(표시 깨짐 수준): `buyma_available_until_updater.py`는 `UPDATE ... WHERE buyma_product_id=%s` → 같은 buyma_id 여러 행이면 다 갱신. `buyma_self_stats_collector.py`는 buyma_id→ace.id dict 덮어쓰기로 비결정적 매핑. (정체성 대표행에만 두면 대부분 해소)

---

## 8. 스키마 결정 (★ 여기서 멈춤 — 미정)

| | 무엇 | 평가 |
|---|---|---|
| **A** | 테이블 분리: `buyma_listings`(출품 정체성) + `source_offerings`(수집처별) + `source_offering_options`(수집처별 재고) + `listing_options`(출품 옵션+현재 소싱 포인터) + `listing_images` | **가장 좋은 스키마**. 도메인을 정확히 모델링, false-delete 같은 버그 종류가 구조적으로 사라짐. 하지만 거의 모든 파일이 새 테이블 배워야 함 = 대규모 |
| **B** | 기존 `ace_products` 유지 + 컬럼만 추가: `group_key`(묶는 키) + `ace_product_variants`에 **출처 컬럼**(source_site/url/매입가) + "정체성은 대표행 1개에만" 규칙 | **가장 안전**. 통계·목록이 이미 model_id 단위라 안 깨짐. 별도 테이블이 가지려는 정체성을 기존 ace 행이 이미 보유 → 중복 제작 회피 |

### 공통 핵심 결정 (둘 다 적용)
**buyma_product_id / reference_number / is_published / 잠긴이름을 "대표 행(또는 listings) 1개에만" 둔다. 멤버 행엔 절대 buyma_product_id/is_published=1 안 둠.** → 웹훅·통계·cleaner 위험 상당수가 자동 해소.

### 멈춘 질문
"**best(A)로 제대로 갈지** / **safe(B)로 가되 나중에 A 이관 가능하게 설계할지**" — 사용자 답변 전에 다른 급한 건으로 보류됨.

---

## 9. 절대 지켜야 할 안전 원칙 (사용자 강조)
- **기존에 잘 되던 기능이 안 되는 건 절대 안 됨.**
- 모든 단계 **dry-run 먼저** → 영향 건수 보고 → 승인 후 실제 실행.
- 스키마는 **컬럼 추가(가산적)**만, 기존 컬럼/로직 제거 X.
- 2·3단계는 **기존 단일-소스 경로를 남긴 채 신규 경로 병행**, 검증되면 전환.
- 단계마다 멈춰서 확인 (한 번에 다 안 함).
- (메모리 규칙) 파일 생성/수정/삭제·DB 쿼리·설정 변경 전 반드시 사용자 확인.

---

## 10. 재개 시 다음 액션 (순서)

1. **스키마 A vs B 확정** (+ "정체성 대표행에만" 확정)
2. **(안전 먼저) 파괴적 cleaner 가드** — `cleanup_duplicates.py` 등에 "merge 그룹 건드리지 마" 가드. 사고 예방이 최우선.
3. **converter: variant에 출처(source) 컬럼 추가** (가산적, 안 깨짐)
4. **register 쪽 RESOLVE(병합) 구현**
5. **stock sync 삭제 범위 수정** (품절/삭제 판정을 "본 mall 옵션만"으로) — 가장 위험, 맨 끝, **dry-run 필수**

---

## 11. 참고 — 관련 파일 목록
- 중복 처리: `okmall/dedup_corrector.py`, `okmall/buyma_new_product_register.py`(get_products_to_register / --clean-duplicates), `buyma_cleaners/cleanup_duplicates.py`
- converter: `okmall/raw_to_ace_converter.py`, `kasina/raw_to_converter_kasina.py`(공용)
- PRICE: `okmall/buyma_lowest_price_collector.py`
- stock sync: `okmall/stock_price_synchronizer.py`(원형) + `kasina/`/`nextzennpack/`/`labellusso/`/`naver/` 4종 + `fast_price_updater.py`
- 웹훅: `okmall_reference/server.py`
- 통계/관리: `buyma_stats/build_merged_dataset.py`, `manage_server/products_api.py`
- 일일 진입점: `run_daily_multisource.py`(Phase2에 dedup), `run_daily_naver.py`(Phase2에 dedup), `okmall/orchestrator.py`(dedup 없음)
- 전체 개요: `PROJECT_OVERVIEW.md`
