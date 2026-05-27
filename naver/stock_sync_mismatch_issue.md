# stock_price_synchronizer_naver false out_of_stock 이슈 — 정리

작성일: 2026-05-20
대상 파일: `naver/stock_price_synchronizer_naver.py`
대표 케이스: `ace_products.id=122028` (t1global / UNDER ARMOUR / model_no=1326413-001)
대표 URL: https://smartstore.naver.com/t1global/products/12284893742

---

## 1. 증상

- `premiumsneakers_category_collector.py` 로 수집 → `raw_to_converter` → `r2_image_uploader` → `buyma_lowest_price_collector` → `convert_to_japanese_gemini` → `buyma_new_product_register` 흐름은 정상 동작 (in_stock 으로 buyma 등록 OK).
- 등록 이후 `stock_price_synchronizer_naver.py` 가 돌면 **mall에는 in_stock인데 DB variants가 전부 out_of_stock 으로 바뀜**.
- 이런 케이스가 다수 발생.

> 사용자 표현: "완전히 같아야 할 옵션, 재고, 최저가 파악 로직이 다르단거임!!"

---

## 2. 122028 실제 데이터

### 2-1. mall raw (raw_scraped_data id=585710)

```json
"options": [
  {"color": "블랙", "tag_size": "L",    "option_code": "51217834259", "status": "in_stock"},
  {"color": "블랙", "tag_size": "XL",   "option_code": "51217834260", "status": "in_stock"},
  {"color": "블랙", "tag_size": "XXL",  "option_code": "51217834261", "status": "in_stock"},
  {"color": "블랙", "tag_size": "XXXL", "option_code": "51217834262", "status": "in_stock"}
]
```

### 2-2. DB variants (`ace_product_variants`)

```
id=4859638 / color_value='ブラック' / size_value='L'    / source_option_code='51217834259' / stock_type='out_of_stock'
id=4859639 / color_value='ブラック' / size_value='XL'   / source_option_code='51217834260' / stock_type='out_of_stock'
id=4859640 / color_value='ブラック' / size_value='XXL'  / source_option_code='51217834261' / stock_type='out_of_stock'
id=4859641 / color_value='ブラック' / size_value='XXXL' / source_option_code='51217834262' / stock_type='out_of_stock'
```

색상이 일본어로 번역되어 있음 (`'블랙' → 'ブラック'`).

---

## 3. 원인 — 옵션 파싱이 아니라 "매칭 단계"

### 3-1. 옵션 파싱 코드는 두 파일 모두 동일

`naver/premiumsneakers/premiumsneakers_collector.py:487-540` 과 `naver/stock_price_synchronizer_naver.py:705-780` 의 옵션 파싱 코드는 **한 글자도 안 다름** (groupName 분류 → optionCombinations 순회 → stockQuantity 로 in_stock/out_of_stock 판정). 둘 다 mall에서 동일한 dict 를 만들어냄.

mall 에서 받은 결과 (둘 다 동일):
```python
[
  {'color': '블랙', 'size': 'L',    'option_code': '51217834259', 'status': 'in_stock'},
  {'color': '블랙', 'size': 'XL',   'option_code': '51217834260', 'status': 'in_stock'},
  {'color': '블랙', 'size': 'XXL',  'option_code': '51217834261', 'status': 'in_stock'},
  {'color': '블랙', 'size': 'XXXL', 'option_code': '51217834262', 'status': 'in_stock'},
]
```

### 3-2. 다른 점은 그 다음

- **collector 흐름**: mall raw → `raw_scraped_data` INSERT → converter 가 ace_variants INSERT. 비교 없음, 새로 채우기만.
- **sync 흐름**: mall raw → DB 의 기존 variants 조회 → **비교** → 차이 있으면 UPDATE. `naver/stock_price_synchronizer_naver.py:882-958` 의 `detect_stock_changes` 가 비교를 담당.

### 3-3. detect_stock_changes (라인 882-958) 매칭 키

```python
# 다중 옵션 상품: 기존 로직 (이름으로 매칭)
mall_map = {}
for item in mall_options:
    mc = (item.get('color', '') or '').strip().lower() or 'free'
    ms = (item.get('size',  '') or '').strip().lower() or 'free'
    mall_map[(mc, ms)] = item['status']

for variant in db_variants:
    db_color = (variant.get('color_value') or '').strip().lower() or 'free'
    db_size  = (variant.get('size_value')  or '').strip().lower() or 'free'
    db_status = variant.get('stock_type', 'purchase_for_order')
    db_is_available = db_status != 'out_of_stock'

    key = (db_color, db_size)
    if key in mall_map:
        ...
    else:
        if db_is_available:
            changes.append({'new_status': 'out_of_stock', 'change_type': 'not_found'})
```

### 3-4. 122028 매칭 실패

| 출처 | color (lower) | size (lower) | 키 |
|---|---|---|---|
| mall | `'블랙'` | `'l'` | `('블랙', 'l')` |
| DB   | `'ブラック'` | `'l'` | `('ブラック', 'l')` |

`mall_map = {('블랙','l'),('블랙','xl'),('블랙','xxl'),('블랙','xxxl')}` 에 DB 키 `('ブラック','l')` 은 없음 → `not_found` 분기 → **out_of_stock 처리**. 4개 전부 동일.

---

## 4. 진짜 근본 원인 — 컬럼 하나에 두 역할

`ace_product_variants.color_value` / `size_value` 컬럼이 동시에 두 가지 용도로 쓰임:

| 역할 | 필요한 표기 |
|---|---|
| buyma 등록 (register / stock sync 가 buyma API 로 보내는 값) | 일본어 `'ブラック'` |
| mall sync 매칭 (mall 원본과 비교) | mall 원본 `'블랙'` |

`convert_to_japanese_gemini.py` 가 이 컬럼을 **번역 결과로 덮어쓰기** 때문에 두 번째 역할이 망가짐.

`ace_product_options.value` 도 같은 구조라 동일 버그 잠재.

---

## 5. 라인 1107 주석 — 이전 사고 이력

```python
# 이전에는 all_out_of_stock 시 자동 delete API 호출 → 5,320건+ false delete 사고 발생
# 매칭 키 정규화 버그(빈 color vs 'FREE') 때문에 false out_of_stock이 빈번하게 만들어졌음
# 진짜 단종은 별도 명시 도구로 처리. 여기서는 update 모드로 진행 (variants out_of_stock 그대로 등록 유지)
all_out_of_stock = all(v['stock_type'] == 'out_of_stock' for v in variants)
if all_out_of_stock:
    log(f"  [SAFETY] all_out_of_stock 감지 → delete 차단, update 모드로 진행", "WARNING")
```

- 이전에 5,320건+ false delete 사고가 있어서 자동 삭제를 막아둠.
- 사고 원인이 "매칭 키 정규화 버그"로 명시되어 있음 — 정확히 이번에 풀려는 매칭 키 버그.
- 매칭 키만 정확해지면 자동 삭제 차단도 풀 수 있음.

---

## 6. source_option_code 정체

### 6-1. 어디서 들어오는가

- mall API 옵션 응답의 unique ID. 네이버는 `optionCombinations[].id` (숫자, 예: `51217834259`).
- `naver/stock_price_synchronizer_naver.py:768` 및 `premiumsneakers_collector.py:536` 에서 `'option_code': str(c.get('id', i))` 로 받음.
- `kasina/raw_to_converter_kasina.py:1248`, `okmall/raw_to_ace_converter.py:1023` 가 raw → `ace_product_variants` INSERT 시 `source_option_code` 컬럼에 저장.

### 6-2. 번역/정규화 영향

- mall 의 옵션 자체가 바뀌지 않는 한 고정값.
- 한글/일본어 표기와 무관.
- 사이즈 normalize (`FREE` 치환) 와도 무관.

### 6-3. NULL 통계 (전체 495,447 variants)

| source_site | total | NULL | NULL% |
|---|---|---|---|
| t1global | 3,335 | 0 | **0%** |
| bblue / larlashoes / dmont / upset / maniaon / pano / kometa / tuttobene / carpi / thegrande | (각) | 0 | 0% |
| kasina | 71,542 | 517 | 0.7% |
| luxlimit | 8,079 | 8 | 0.1% |
| thefactor2 | 3,634 | 6 | 0.2% |
| veroshopmall | 6,712 | 2 | 0.0% |
| fabstyle | 4,269 | 1 | 0.0% |
| premiumsneakers | 2,773 | 45 | 1.6% |
| vvano | 1,493 | 1 | 0.1% |
| loutique | 82 | 2 | 2.4% |
| laprima | 54,936 | 3,980 | 7.2% |
| labellusso | 53,346 | 4,695 | 8.8% |
| joharistore | 3,703 | 270 | 7.3% |
| **okmall** | 134,871 | 23,556 | **17.5%** |
| **unico** | 12,360 | 3,646 | **29.5%** |
| **euroline** | 1,493 | 553 | **37.0%** |
| **nextzennpack** | 13,624 | 8,186 | **60.1%** |
| 전체 | 495,447 | 52,010 | 10.5% |

- 신규 naver mall들은 0% NULL → source_option_code 매칭으로 완벽.
- okmall / nextzennpack / euroline / unico 는 옛 데이터가 많아 NULL 비율 높음 → source_option_code 매칭만으로는 부족, fallback(color/size) 필수.

---

## 7. 122028 케이스 — source_option_code 매칭 시뮬레이션

| mall option_code | DB source_option_code | 매칭 | mall status | DB 현재 | 처리 |
|---|---|---|---|---|---|
| `51217834259` | `51217834259` (id=4859638) | ✅ | in_stock | out_of_stock | restock |
| `51217834260` | `51217834260` (id=4859639) | ✅ | in_stock | out_of_stock | restock |
| `51217834261` | `51217834261` (id=4859640) | ✅ | in_stock | out_of_stock | restock |
| `51217834262` | `51217834262` (id=4859641) | ✅ | in_stock | out_of_stock | restock |

결과: 4개 전부 정상 `restock` 처리 → buyma 에 `purchase_for_order` 로 sync. 번역/정규화 영향 0.

---

## 8. 해법 비교

### 해법 1: `_jp` 별도 컬럼 추가 (가장 근본)

- `ace_product_variants` 에 `color_value_jp`, `size_value_jp` 컬럼 신설.
- `ace_product_options` 에 `value_jp` 컬럼 신설.
- `color_value` / `size_value` / `value` 는 **항상 mall raw 한국어** 보존.
- `convert_to_japanese_gemini.py` 는 `_jp` 컬럼만 채움. 원본 덮어쓰기 X.
- register / stock_sync 가 buyma 에 보낼 땐 `_jp` 우선 (없으면 원본).
- 매칭은 항상 원본 컬럼.

| 장단점 | |
|---|---|
| ✅ 데이터 모델 깨끗 — 컬럼 책임이 분리됨 | |
| ✅ mall sync 매칭이 영구히 정확 | |
| ✅ 다른 mall 의 동일 매칭 키 버그도 한 번에 해결 | |
| ✅ `ace_product_options.value` 동일 패턴 적용 가능 | |
| ⚠️ 마이그레이션 필요 — 컬럼 추가 + 기존 일본어 데이터 `_jp` 로 이동 + **원본 한글 복원** | |
| ⚠️ converter / register / sync / convert_to_japanese 전 mall 파일 수정 | |
| ⚠️ 원본 한글 복원 방법: `options_json` 컬럼 또는 raw_scraped_data 역추적 — 가능 여부 별도 조사 필요 | |

### 해법 2: 런타임 번역 (DB는 한글 원본만)

- DB 는 항상 mall raw 한국어 보존, 일본어는 DB 에 안 둠.
- register / sync 시점에 메모리에서 KOREAN_TO_JAPANESE 매핑 + 누락된 것만 Gemini API.

| 장단점 | |
|---|---|
| ✅ DB 가 단일 진실 (source of truth) | |
| ✅ 매칭 항상 정확 | |
| ⚠️ 기존 일본어 DB 데이터 한글로 복원 필요 (해법 1 보다 더 어려움 — 매핑이 1:N 일 수 있음) | |
| ⚠️ register 시점마다 번역 비용 (캐시 테이블로 완화 가능하지만 추가 인프라) | |
| ⚠️ 번역 흐름 전체 재설계 | |

### 해법 3: source_option_code 우선 매칭 (응급처치)

- DB 스키마 변경 0.
- `detect_stock_changes` 의 매칭 키만 `(source_option_code)` → fallback `(color, size)` 로 변경.

| 장단점 | |
|---|---|
| ✅ 최소 코드 변경 | |
| ✅ 즉시 효과 | |
| ✅ 마이그레이션 0 | |
| ⚠️ source_option_code NULL 인 옛 데이터 (okmall 17.5%, nextzennpack 60%, euroline 37%, unico 30%) 는 fallback (color/size) 으로 비교 → 같은 버그 남음 | |
| ⚠️ 응급처치 성격 — 컬럼 책임 분리 라는 진짜 문제는 그대로 | |

### 해법 4: ace_product_variants 를 캐시로 보고 raw_scraped_data 에서 매번 재구성

- raw_scraped_data 가 진실, ace 는 매번 변환 결과.
- sync 마다 raw 재변환 + 번역 캐시.

| 장단점 | |
|---|---|
| ✅ 단방향 데이터 흐름 — 가장 단순한 모델 | |
| ⚠️ 변환 비용 매번 | |
| ⚠️ buyma_product_id / locked_* 등 등록 후 메타데이터 관계 재설계 매우 큼 | |
| ⚠️ 가장 큰 아키텍처 변경 | |

### 비교표

| 항목 | 해법 1 (_jp 컬럼) | 해법 2 (런타임 번역) | 해법 3 (c안) | 해법 4 (raw 재변환) |
|---|---|---|---|---|
| 근본성 | ★★★★ | ★★★★ | ★★ | ★★★★★ |
| 변경 범위 | 중 (스키마 + 다수 파일) | 큼 (번역 흐름 재설계) | 작음 (1~5개 파일) | 매우 큼 |
| 마이그레이션 위험 | 있음 (한글 복원 까다로움) | 큼 (한글 복원이 더 까다로움) | 없음 | 큼 |
| 즉시성 | 마이그레이션 후 | 마이그레이션 후 | 즉시 | 재설계 후 |
| 매칭 정확도 (정착 시) | 1:1 | 1:1 | mall ID 있는 것은 1:1, NULL은 기존 버그 | 1:1 |

---

## 9. 추천 안

**해법 1 (`_jp` 별도 컬럼)** 이 가장 근본적이면서 현실적.

이유:
1. **컬럼 하나에 두 역할** 이라는 진짜 원인을 분리.
2. 다른 mall sync 의 매칭 키 버그(라인 1107 주석의 "빈 color vs 'FREE'" 사고)도 동시 해결.
3. `ace_product_options` 도 같은 문제 잠재 — 일관된 패턴으로 정리 가능.
4. 한 번 해두면 미래에 번역 변경 / 한글 raw 복원 등 자유로움.
5. `options_json` 컬럼이 이미 raw 보존 용도라 마이그레이션 시 일부 복원 출처로 활용 가능.

**현실적 보완**:
- 마이그레이션 시 기존 일본어 → 한글 복원이 까다로움. `options_json` 백업이 충분한지, raw_scraped_data 에서 역추적 가능한지 별도 조사 필요.
- 모든 mall converter / register / sync / convert_to_japanese 다 수정 (대규모).
- 짧은 시간엔 **해법 3 (응급처치)** 로 막고 **해법 1 (정착)** 로 가는 것도 합리적.

---

## 10. all_out_of_stock 자동 삭제 차단 — 별개 이슈

- `naver/stock_price_synchronizer_naver.py:1106-1111` 에서 현재 차단 중.
- 사고 원인이 매칭 키 버그(이번 해법으로 정리되는 부분)이므로, 매칭 안정화 이후 차단 풀 수 있음.

**제안 순서** (위험 최소화):

| 순서 | 변경 | 검증 |
|---|---|---|
| Step 1 | 매칭 키 안정화 (해법 3 또는 해법 1) | 소량 `--limit` / 단건 `--id` 검증 |
| Step 2 | 1~2일 운영하며 false out_of_stock 비율 추이 모니터링 | DB sync 후 out_of_stock 추이 |
| Step 3 | all_out_of_stock 자동 삭제 차단 풀기 | 처음 dry-run, 그 다음 운영 |

세 가지를 한 번에 풀면 위험. 매칭 안정화 검증 전에 삭제 풀면 운나쁘면 사고 재발.

추가 고려:
- 진짜 단종 vs 일시 결품 (mall 이 잠시 옵션 빼두는 케이스) 구분. naver 는 `statusType` 같은 필드로 단종 표시함 (현재 코드는 `('SALE','ONSALE','READY')` 가 아니면 판매 종료 취급 — 라인 700-703).
- 사고 방지용으로 "최근 N일 연속 all_out_of_stock 일 때만 삭제" 같은 안전장치도 고려할 만함.

---

## 11. 결정 미진행 사항

- 해법 1 / 2 / 3 / 4 중 어느 것으로 갈지.
- 해법 1 로 가는 경우, 기존 일본어 DB 데이터에서 한글 원본 복원 가능성 사전 조사 (`options_json` 백업 / raw_scraped_data 역추적).
- 다른 mall (okmall / kasina / labellusso / nextzennpack) 의 stock_price_synchronizer 도 동일 매칭 키 버그 있는지 점검 후 일괄 수정 여부.
- all_out_of_stock 자동 삭제 차단 풀기 — 매칭 안정화 이후 별도 진행.

---

## 12. 관련 파일/위치

- 매칭 로직: `naver/stock_price_synchronizer_naver.py:882-958` (`detect_stock_changes`)
- 옵션 파싱: `naver/stock_price_synchronizer_naver.py:705-780`
- 자동 삭제 차단: `naver/stock_price_synchronizer_naver.py:1106-1111`
- 옵션 파싱 (collector 측, 동일): `naver/premiumsneakers/premiumsneakers_collector.py:487-540`
- source_option_code INSERT (converter): `kasina/raw_to_converter_kasina.py:1248`, `okmall/raw_to_ace_converter.py:1023`
- 번역 (덮어쓰기): `okmall/convert_to_japanese_gemini.py:563-573`
- 동일 패턴 잠재: `naver/stock_price_synchronizer_naver.py` 외 `okmall/stock_price_synchronizer.py`, `kasina/stock_price_synchronizer_kasina.py`, `labellusso/stock_price_synchronizer_labellusso.py`, `nextzennpack/stock_price_synchronizer_nextzennpack.py`

---

## 13. 해결 기록 (2026-05-21)

### 적용 해법
**해법 1 (`_jp` 컬럼 분리) 의 변형 + 해법 3 (`source_option_code` 우선 매칭) 혼합.**

- `ace_product_variants` 에 `color_value_original` / `size_value_original` (한글 원본) 컬럼 신설
- 기존 `color_value` / `size_value` 는 일본어 번역 그대로 유지 (buyma 등록용, 영향 0)
- 매칭 키 우선순위:
  1. `source_option_code` (mall 옵션 ID, 번역 영향 0)
  2. `(color_value_original, size_value_original)` 한글 원본
  3. **매칭 실패 시 skip** (기존 `not_found → out_of_stock` 분기 제거 → false out_of_stock 신규 생성 0)

### 변경 파일

| 종류 | 파일 |
|---|---|
| DB 스키마 | `ace_product_variants` ADD `color_value_original VARCHAR(100)`, `size_value_original VARCHAR(100)` |
| converter (신규 수집 자동 채움) | `kasina/raw_to_converter_kasina.py` (kasina/nextzennpack/labellusso/naver 공용), `okmall/raw_to_ace_converter.py` |
| 번역 가드 | `okmall/convert_to_japanese_gemini.py:563` 주석 추가 — 새 컬럼 절대 건드리지 말 것 |
| 5개 stock sync 매칭 로직 교체 | `naver/stock_price_synchronizer_naver.py`, `okmall/stock_price_synchronizer.py`, `kasina/stock_price_synchronizer_kasina.py`, `labellusso/stock_price_synchronizer_labellusso.py`, `nextzennpack/stock_price_synchronizer_nextzennpack.py` (SELECT + detect_stock_changes 동시 수정) |
| naver SAFETY GUARD 해제 | `naver/stock_price_synchronizer_naver.py:1111-1118` 의 all_out_of_stock 자동 delete 차단 해제 → 5개 mall 동일 동작 (진짜 품절 시 buyma delete) |

### 백필 결과

- 출처: `ace_product_variants.options_json` (converter INSERT 시점부터 한글 원본 보존, 번역 스크립트가 안 건드림 — 사전 조사로 확인)
- 49.6만 row UPDATE / 청크 5000 / 약 34.9초
- NULL 잔여 0건, 일본어 잔류 0건
- 122028 대표 케이스: `color_value='ブラック'` 옆에 `color_value_original='블랙'` 정상 채워짐

### 검증

| 항목 | 결과 |
|---|---|
| 122028 단건 dry-run (`--id 122028`) | 4 variants 모두 restock 정상 검출 (기존엔 false out_of_stock) |
| okmall `--limit 10 --dry-run` | 10/10 성공, 삭제 0 |
| kasina `--limit 10 --dry-run` | 10/10 성공, 삭제 0 |
| labellusso `--limit 10 --dry-run` | 10/10 성공, 삭제 0 |
| nextzennpack `--limit 10 --dry-run` | 6 성공 + 4 정상 delete (모두 mall 수집 실패 = 진짜 판매 종료) |
| naver `--limit 10 --dry-run` | 9 성공 + 1 정상 delete (bblue Nike, 판매 종료) |

### 진짜/false 구분 시그널

`source_stock_status` 컬럼이 converter INSERT 시점의 mall 응답을 보존 — 그 후 sync는 stock_type 만 바꿈.

- `source_stock_status='in_stock'` + `stock_type='out_of_stock'` → false 의심 (매칭 버그로 덮였을 가능성)
- `source_stock_status='out_of_stock'` → 진짜 의심

표본 (status='fail' + buyma 등록 + all_OOS 345개 상품) 분석 결과: **100% 진짜 품절** (source_stock_status 전부 out_of_stock). false 0건.

### 운영 sync 중 관찰 (미완 분석)

- 새 로직 패치 후 운영 sync 약 30분간 buyma delete 157건 발생 (bblue 집중, 전부 API success)
- 진짜/false 시그널 분류는 백그라운드 작업 중 인터럽트로 미완료
- 후속: `source_stock_status` 기준 분류로 안전성 사후 검증 필요

### 임시 파일 (정리 대상)

- `_alter_add_original_cols.py` — 스키마 추가
- `_backfill_original_cols.py` — 백필 스크립트
- `_check_options_json.py` — 조사용 (여러 단계 재사용)
