# 네이버 + 신규 수집처 프로젝트 전체 개요

> `naver/NEXT_STEPS.md`와 `todo.md` 통합 문서. BUYMA 자동화 파이프라인에 새 수집처를 추가하는 프로젝트 전체 맥락·진행상황·다음작업을 한 파일로 정리.
> 최종 갱신: 2026-04-24 (플랫폼 재분류: 럭스보이 Wisa / 브릭맨션·메종파르코·밀라니즈 Cafe24 확정)

---

## 1. 프로젝트 목표

기존 5개 수집처(okmall, kasina, nextzennpack, labellusso, trendmecca) 외에 **22개 신규 수집처**의 상품을 BUYMA에 자동 등록하도록 파이프라인 확장.

### 파이프라인 단계
```
scan_store_brands (브랜드/카테고리 시드)
    ↓
<site>_collector.py  →  raw_scraped_data
    ↓
kasina/raw_to_converter_kasina.py --source-site <site>  →  ace_products/options/variants/images
    ↓
match_brands + category_cleaner
    ↓
(price → translate → image upload → register)
```

---

## 2. 전체 대상 사이트 (22개, 플랫폼 분류)

### 진행 상태 요약 (2026-04-15)
| 순번 | 사이트 | 플랫폼 | 상태 |
|------|--------|--------|------|
| 1 | 오케이몰 | 자체 | ✅ 완료 |
| 2 | 카시나 | Shopby | ✅ 완료 |
| 3 | 넥스트젠팩 | Cafe24 | ✅ 완료 |
| 4 | 라벨루쏘 | Cafe24 | ✅ 완료 |
| 5 | 트렌드메카 | Cafe24 | ✅ 완료 |
| ✅ 6 | 프리미엄스니커즈 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (1,249 raw / 841 active / 743 BUYMA 등록) |
| ✅ 7 | 팹스타일 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (1,645 raw / 940 active / 763 BUYMA 등록) |
| ✅ 8 | 루티크 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (36 raw / 24 active — 대부분 품절) |
| ✅ 9 | 까르피 | 네이버 브랜드스토어 | 🟢 **brand_store_collector로 수집 가능** (10건 파일럿 완료, 이미지 1장뿐) |
| 10 | 럭스보이 | Wisa (www.luxboy.com) ⚠️ 쇼핑몰 TSV는 `brand.naver.com/luxboy` 지목 — 동일 업체 여부 확인 필요 | ⬜ 대기 |
| ✅ 11 | 디몬트 | 네이버 스마트스토어 | 🟢 **premiumsneakers_category_collector (전체상품)로 수집 가능** (10건 파일럿, 이미지 1장) |
| 12 | 아르떼모아 | 고도몰 | ⬜ 대기 |
| 13 | 구템즈 | Cafe24 | ⬜ 대기 |
| 14 | 무신사부티크 | 무신사 | ⬜ 대기 |
| ✅ 15 | 티원글로벌 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (779 raw / 692 active) |
| ✅ 16 | 뚜또베네 | 네이버 스마트스토어 | 🟢 **premiumsneakers_category_collector (전체상품)로 수집 가능** (10건 파일럿) |
| 17 | 라프리마 | Cafe24 | ⬜ 대기 |
| ✅ 18 | 조하리스토어 | 네이버 브랜드스토어 | 🟢 **brand_store_collector로 수집 가능** (10건 파일럿, 이미지 4~5장) |
| 19 | 매니아온 | MakeShop | ⬜ 대기 |
| ✅ 20 | 논현더팩토리 | 네이버 스마트스토어 | 🟢 **premiumsneakers_category_collector (전체상품)로 수집 가능** (1,169 raw / 전체 1,634개) |
| ✅ 21 | 비비아노 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (328 raw / 221 active) |
| ✅ 22 | 베로샵 | 네이버 스마트스토어 | ✅ **수집+convert+등록 완료** (3,275 raw / 99 active — 대부분 trendmecca와 중복) |

### 플랫폼별 그룹
| 플랫폼 | 사이트 | 전략 |
|--------|-------|------|
| 네이버 스마트스토어/브랜드스토어 | 12개 (순번 6-11, 15-16, 18, 20-22) | 단일 공용 collector (`--source` 분기) |
| Cafe24 | 2개 (순번 13 구템즈, 17 라프리마) | 기존 nextzennpack 패턴 복사 |
| 고도몰 | 1개 (순번 12 아르떼모아) | 신규 유형 — 별도 collector |
| Wisa | 1개 (순번 10 럭스보이) | 신규 유형 — 별도 collector (2026-04-24 재분류) |
| 무신사 | 1개 (순번 14) | 신규 유형 — 별도 collector |
| MakeShop | 1개 (순번 19) | 신규 유형 — 별도 collector |

---

## 3. 수집처별 사전조사 메모

### 네이버 스마트스토어/브랜드스토어
| 사이트 | 주소 | 특이사항 |
|--------|-----|---------|
| 라프리마 | laprima.co.kr | Cafe24. 모델명: 상품명 끝/상세설명 |
| 루티크 | smartstore.naver.com/loutique | 모델명: 상품명 끝 (간혹 없음) |
| 라벨루쏘 | labellusso.com | 완료 |
| 아르떼모아 | artemoa.com | 고도몰. 모델명: 브랜드 뒤/상품정보 |
| 럭스보이 | www.luxboy.com | **Wisa** (위사, `luxboy.wisacdn.com` / (주)위즈컴퍼니). 모델명: 상품명 끝/상품정보. `brand_list` 페이지에 200+ 브랜드. ⚠️ 쇼핑몰 TSV는 `brand.naver.com/luxboy` 지목 — 동일 업체 여부 확인 필요 |
| 넥스트젠팩 | nextzennpack.com | 완료 |
| 팹스타일 | smartstore.naver.com/fabstyle | 모델명: 상품명 끝/상품정보 |
| 까르피 | brand.naver.com/carpi | 모델명: 상품명 끝/상품정보 |
| 트렌드메카 | trendmecca.co.kr | 완료 |
| 올아이원트 | smartstore.naver.com/aint | **중고/스크래치 제외 필요** |
| 디몬트 | smartstore.naver.com/dmont | 브랜드 축약→제조사 풀네임, '디몬트' 제거, 쿠폰가, 상세이미지 추가 |
| 무신사부티크 | musinsa.com/boutique | 모델명: 상품명 끝/상품정보 |
| 구템즈 | 9tems.com | Cafe24 |
| 밀라니즈 | milaneez.com | **Cafe24** (2026-04-24 확인). 모델명: 브랜드명 옆 |
| 블리에이 | smartstore.naver.com/vely_a | 모음전 제외 (옵션 갯수 필터?) |
| 비블루 | smartstore.naver.com/bblue | 모델명 괄호, 이미지 여부 불명, 모음전 제외 |
| 티원글로벌 | smartstore.naver.com/t1global | 모음전 제외 (품번 없음) |
| 베이지그 | bazig.com | - |
| 더마 | smartstore.naver.com/nilsenco | 모델명 랜덤, 상품정보가 정확 |
| 비비아노 | smartstore.naver.com/vvano | 쿠폰 자주 생김, 쿠폰가 수집 권장 |
| 세토프 | smartstore.naver.com/set-of | **이미지 사용 불가** |
| 메종파르코 | maisonparco.com | **Cafe24** (2026-04-24 확인, `file.cafe24cos.com`). 모델명: 상품명 끝 |
| 뚜또베네 | smartstore.naver.com/tutto-bene | `[국내배송][15%중복쿠폰]` 제거 필수, 쿠폰가 |
| 조하리스토어 | brand.naver.com/joharistore | 모음전 제외, 쿠폰가 |
| 프리미엄스니커즈 | smartstore.naver.com/premiumsneakers | 쿠폰가 |
| 바이스트 | brand.naver.com/buyest | 쿠폰가 |
| 매니아온 | maniaon.com + smartstore/maniaon | 가격: 공홈, 이미지: 스스. `[국내배송]` 제거 |
| 울산명품관 | smartstore.naver.com/ulsanluxury | 첫 썸네일만 (2번째 이후 워터마크) |
| 논현더팩토리 | smartstore.naver.com/thefactor2 | **뒤에서 3번째 이미지까지 제외** |
| 베로샵 | smartstore.naver.com/veroshopmall | 모델명 상품명 중앙 |

### 스토어별 특수 처리 요약 (collector 구현 시 적용)
| 스토어 | 특수 처리 |
|--------|-----------|
| 디몬트 | 브랜드 축약→제조사 풀네임, '디몬트' 제거, 쿠폰가, 상세이미지 |
| 뚜또베네 | `[국내배송][15%중복쿠폰]` 제거, 쿠폰가 |
| 논현더팩토리 | 뒤에서 3번째 이미지까지 제외 |
| 티원글로벌/조하리/블리에이/비블루 | 모음전 제외 (품번 없는 상품) |
| 베로샵 | 모델명 상품명 중앙 |
| 울산명품관 | 첫 썸네일만 사용 (워터마크) |
| 올아이원트 | 중고/스크래치 제외 |
| 세토프 | 이미지 사용 불가 (수집만, register 단계에서 생략) |
| 프리미엄스니커즈/디몬트/뚜또베네/조하리/비비아노/바이스트 | 쿠폰 적용가 수집 |
| 매니아온 | 가격은 공홈, 이미지는 스마트스토어에서 병합 수집 |

---

## 4. 핵심 파일 맵 (naver/)

```
naver/
├── OVERVIEW.md                        # 이 파일 (통합 가이드)
├── NEXT_STEPS.md                      # (레거시, gitignore됨)
├── naver_cookies.json                 # 네이버 로그인 쿠키 (scan + collector 공용, gitignore됨)
├── scan_store_brands.py               # 브랜드/카테고리 스캔 → mall_brands/mall_categories INSERT
└── premiumsneakers/
    ├── premiumsneakers_collector.py   # 네이버 공용 상품 수집기 (--source 로 스토어 전환)
    ├── premiumsneakers_collect.md     # API 스펙 + 운영 메모 (하단 "헤맸던 것들" 섹션 포함)
    ├── premiumsneakers_product.json   # 상품 API 응답 샘플
    ├── premiumsneakers_product_benefits.json  # 혜택(쿠폰가) API 샘플
    ├── premiumsneakers_product.html
    ├── premiumsneakers_product_price.html
    └── premiumsneakers_list.html
```

**중요**: premiumsneakers_collector.py가 네이버 공용 collector입니다. `--source <store_id>`로 어느 네이버 스토어든 수집 가능.

## 4-1. 네이버 수집기 3종

| collector | 파일 | 대상 | Phase 1 순회 단위 | 특징 |
|---|---|---|---|---|
| **smartstore (브랜드)** | `premiumsneakers_collector.py` | 브랜드 리스트 있는 smartstore.naver.com 스토어 | mall_brands | 기본. 6개 mall 검증 완료 |
| **smartstore (전체상품)** | `premiumsneakers_category_collector.py` | 브랜드 리스트 없거나 카테고리 필터가 동작 안 하는 스마트스토어 | 전체상품 URL 1개 (`/category/ALL` 등) | **brand/category auto-INSERT**. 상세: `naver/premiumsneakers/category_collector.md` |
| **brandstore** | `brand_store_collector.py` | `brand.naver.com/{store}` 도메인 | mall_brands | API 경로만 `/i/v2/` → `/n/v2/` 차이. 상세: `naver/premiumsneakers/brand_store_collector.md` |

**세 collector 모두 `premiumsneakers_collector.py`의 공유 컴포넌트(map_to_row, save_rows, fetch_detail 등)를 import 재사용**. 브랜드 collector 원본은 0 수정.

---

## 5. scan_store_brands.py 스토어 설정 플래그

| 플래그 | 용도 | 예시 |
|--------|------|------|
| `brands_at_top: True` | 최상위가 곧 브랜드 | loutique, t1global |
| `brand_range: (start, end)` | 메뉴 순서 범위 필터 | loutique `('Thom Browne', 'Balenciaga')` |
| `brand_prefix: 'str'` | 특정 prefix의 top만 브랜드 그룹 | vvano `'Brand '` |
| `brand_parent: 'str'` | 단일 브랜드 부모 (자음그룹 2단 호버) | fabstyle `'BRAND'`, carpi `'BRAND'` |
| `brand_parents: [...]` | 다중 브랜드 부모 (각 top이 그룹) | veroshopmall `['#ㄱ', ..., '#ㅎ']` |
| `brand_parent_prefix: 'str'` | prefix 매칭 다중 부모 | joharistore `'Brand ['` |
| `category_roots: {...}` | 카테고리 root 화이트리스트 | thefactor2, dmont |
| `category_root_parents: {...}` | 최상위 껍데기 → 자식을 root로 | tutto-bene `{'패션의류', '패션잡화'}` |

### 실행 예시
```bash
python naver/scan_store_brands.py --store t1global --brands-only --dry-run
python naver/scan_store_brands.py --store thefactor2 --categories-only
python naver/scan_store_brands.py --store tutto-bene --categories-only --insert-site
python naver/scan_store_brands.py --store <store> --insert-site     # 브랜드+카테고리 전체 INSERT
```

---

## 6. premiumsneakers_collector.py 핵심 설계

**사무실 PC에서만 실행** (WARP OFF). AWS는 네이버 DNS 차단.

- Playwright `headless=False` 필수
- **Phase 1**: 카테고리 페이지 순회, 페이지 버튼 클릭 방식 (URL param 무시됨)
- **Phase 2**: `/products/{pno}?withWindow=false` + `/product-benefits/{pno}` XHR 캡처
- `DETAIL_DELAY = (1.0, 3.0)` + 재시도 2회
- 품절 상품은 **리스트에서 사전 필터**: `<span>품절</span>` 뱃지 감지
- 브랜드명은 `mall_brands.mall_brand_name_en`을 authoritative source로 사용

자세한 트러블슈팅·헤맸던 점은 `naver/premiumsneakers/premiumsneakers_collect.md` 하단 "운영 메모" 섹션 참조.

### 실행 예시
```bash
# 쿠키 갱신 (스토어 무관 공용)
python naver/premiumsneakers/premiumsneakers_collector.py --login

# 네이버 어느 스토어든 --source로 전환
python naver/premiumsneakers/premiumsneakers_collector.py --source premiumsneakers --brand "Balenciaga" --limit 3 --dry-run --dump
python naver/premiumsneakers/premiumsneakers_collector.py --source fabstyle --brand "DIESEL"
python naver/premiumsneakers/premiumsneakers_collector.py --source loutique --brand "Parajumpers" --skip-existing
python naver/premiumsneakers/premiumsneakers_collector.py --source fabstyle    # 전체 브랜드
```

---

## 7. 새 네이버 스토어 collector 추가 절차

네이버 스마트스토어는 HTML/API 구조가 거의 동일 → 샘플 파일 없이도 바로 추가 가능.

1. **WARP OFF** + **쿠키 갱신**: `python naver/premiumsneakers/premiumsneakers_collector.py --login`
2. `mall_brands`에 해당 스토어 시드 확인 (없으면 scan):
   ```sql
   SELECT COUNT(*) FROM mall_brands WHERE mall_name='<store>';
   ```
   ```bash
   python naver/scan_store_brands.py --store <store> --insert-site
   ```
3. `mall_brand_name_en`이 한글이면 `mall_brand_name_ko`에 복사 후 영문 보정:
   - 다른 mall에서 같은 ko값의 영문 매칭 시도
   - 남은 건 `okmall/buyma_master_data_*/brands.csv`에서 정확한 영문 표기 확인 후 UPDATE
4. 소량 dry-run:
   ```bash
   python naver/premiumsneakers/premiumsneakers_collector.py --source <store> --brand "<test>" --limit 3 --dry-run --dump
   ```
5. 본 수집:
   ```bash
   python naver/premiumsneakers/premiumsneakers_collector.py --source <store> --skip-existing
   ```
6. 변환:
   ```bash
   python kasina/raw_to_converter_kasina.py --source-site <store>
   ```
7. 브랜드 매칭: `python kasina/match_brands.py` (CWD=buyma/)
8. 카테고리 매칭: `python buyma_cleaners/category_cleaner.py register → match → apply`

---

## 8. 운영 메모 / 헤맸던 것들

### 환경
- **WARP 충돌**: buyma용 Cloudflare WARP이 켜져 있으면 네이버 DNS 실패 → 네이버 작업 시 WARP OFF 필수
- **쿠키 수명**: 몇 시간. 실행 전 `--login` 권장
- **Playwright `headless=False`**: 네이버 봇 감지 회피

### 네이버 페이지네이션 (중요)
- `?page=N`/`?cp=N` URL은 **서버가 무시하고 p1만 반환** (302 리다이렉트 또는 200 + 동일 HTML)
- **해결**: DOM의 페이지 숫자 버튼 클릭 + `wait_for_function`으로 첫 상품 ID 변화 감지
- 총 갯수는 `"총 N개"` 텍스트 매칭으로 추출 (클래스명은 해시라 불안정)

### brand_name 일관성
- 네이버 상품 메타의 `manufacturerName`/`brandName`은 판매자가 제각각 입력 (예: `PARAJUMPERS` vs `파라점퍼스`)
- **해결**: `mall_brands.mall_brand_name_en`을 authoritative source로 사용 (collector line 422-425)

### 품절 처리
- 리스트 카드의 `<span>품절</span>` 뱃지 감지해서 Phase 1에서 사전 스킵
- 옵션 단위는 `optionCombinations[].stockQuantity > 0`로 판정
- 상품 전체는 옵션 중 하나라도 in_stock이면 in_stock

### 가격
- `benefits.optimalDiscount.totalDiscountResult.summary.totalPayAmount`가 1순위 (쿠폰/멤버십/포인트 스택된 실결제가)
- fallback: `product.benefitsView.discountedSalePrice`

### 기타
- 이미지: `productImages` 배열에 동일 URL 중복 포함될 수 있음 → dedup 필수
- 캡챠: `page.title()`에 `보안`/`captcha` 포함 감지 시 즉시 중단 + `--login` 요청
- `reference_price`(참고가)는 `original_price_jpy > price`일 때만 BUYMA 요청에 포함 (`okmall/buyma_new_product_register.py:744-747`)

---

## 9. raw_json_data 스키마 (converter 호환)

`kasina/raw_to_converter_kasina.py --source-site <store>`가 기대하는 형태:

```json
{
  "channel_no": "...",
  "brand_name": "...",
  "brand_id": 0,
  "model_name": "...",
  "options": [
    {"color": "", "tag_size": "EU 39-40", "option_code": "...", "status": "in_stock"}
  ],
  "images": ["https://shop-phinf.pstatic.net/..."],
  "category": "패션잡화 > 남성신발 > 부츠"
}
```

- `color=""`: 사이즈만 있는 상품 → converter가 FREE로 자동 fallback
- `tag_size`: 범위 그대로 저장 (`"EU 39-40"`). BUYMA 수용 여부는 실등록에서 확인
- `status`: `stockQuantity > 0` → `"in_stock"`, else `"out_of_stock"`
- measurements/composition 없음: converter가 graceful 처리, colorsize_comments가 빈약

---

## 10. 완료 / 미완료 / 다음 작업

### ✅ 완료
- `scan_store_brands.py`: 9개 스토어 브랜드/카테고리 DB INSERT 완료
- `premiumsneakers_collector.py`: 완성, 네이버 전체 공용 (`--source`)
- premiumsneakers: BALENCIAGA 3건 INSERT + convert 검증 (ace 121147~121149)
- MONCLER 1페이지 40건 수집 성공 (페이지네이션 클릭 방식 검증)
- fabstyle DIESEL 6건 수집+convert+R2 업로드 검증
- loutique Parajumpers 4건 수집 검증 (전량 품절)
- brand_name authoritative source를 mall_brands로 변경
- 리스트 단계 품절 스킵 구현
- `mall_brands` 한글 en 값 → ko 복사 + 다른 mall/BUYMA csv 기반 영문 보정
- **2026-04-15: 신규 6개 mall 전수 수집 → convert → 등록 완료** (섹션 14 참조)

### ⚠️ 확인 필요
- **joharistore**: brands 76건 INSERT됐으나 buyma_brand_id 매핑은 11건뿐 — match_brands 레벨 실패가 많음 (표기 차이 가능성)
- **carpi/joharistore/loutique**: categories 미수집 (brands만 있음) → scan_store_brands `--categories-only` 돌려야 함

### 🔴 미완료 (우선순위)
1. BUYMA 고아/유령 상품 정리 (`buyma_orphan_cleaner.py --login → --scan → --delete → --clean-ghost`)
2. ace.name 본문 한글 잔재 정리 (Gemini 재번역, 총 1,192건 — vvano 221, trendmecca 309 등)
3. 기존 mall(okmall/kasina 등) brand 보정 (`reports/mall_brands_issues_20260415.md` 참조)
4. Cafe24 2개 (라프리마, 구템즈) collector 작성
5. 고도몰 (아르떼모아, 럭스보이) collector 신규 작성 (2개 공용 가능)
6. 무신사부티크, 매니아온 (MakeShop) collector 신규 작성
7. 미매핑 카테고리 568건 수동 매핑 (okmall 448, labellusso 41, kasina 29, nextzennpack 24, trendmecca 23, veroshopmall 3)

### 다음 실행 체크리스트 (새 세션에서)
1. **WARP OFF** 확인
2. 쿠키 갱신: `python naver/premiumsneakers/premiumsneakers_collector.py --login`
3. DB 조회: 각 스토어별 brands/categories 건수, 매핑률 확인
   ```sql
   SELECT mall_name, COUNT(*) n, SUM(buyma_brand_id IS NOT NULL) mapped FROM mall_brands GROUP BY mall_name;
   SELECT mall_name, COUNT(*) n FROM mall_categories GROUP BY mall_name;
   ```
4. 이 문서의 진행상태 표 갱신

---

## 11. 커밋 금지 파일 (gitignore 처리됨)
- `naver/naver_cookies.json` — 인증 쿠키
- `naver/nav_dump.json`, `naver/nav_tree.json` — 대용량 덤프
- `naver/__pycache__/`, `naver/**/__pycache__/`

(현재 `.gitignore` 실제 엔트리는 위 5개뿐입니다.)

---

## 12. 알려진 이슈 상세 (기존 NEXT_STEPS.md에서 보존)

### joharistore
- `Brand [A~Z]`, `Brand [ㄱ]~[ㅎ]` 각 그룹 아래 브랜드가 `hasChild=True`로 나와서 중간노드 취급 → 실제 브랜드로 분류 안 됨
- `effective_brand_parents` 모드에서 hasChild 체크 제거하는 수정 했지만 여전히 10개만 수집됨
- 타이밍 이슈 + 브랜드스토어(brand.naver.com) 특유 구조 때문으로 추정
- 재검증 필요

### carpi
- dry-run에서 107 브랜드 정상 추출 확인됨
- 실제 `--insert-site`로 DB INSERT 진행했는지 기록 불명확 → DB 조회 필요:
  ```sql
  SELECT COUNT(*) FROM mall_brands WHERE mall_name='carpi';
  ```

---

## 13. 전체 후보 사이트 백로그 (메타데이터 불명 + 우선순위 낮음)

todo.md 원본에 있던 추가 후보 사이트 리스트. 22개 우선 대상(섹션 2) 완료 후 검토용.

### 네이버 스마트스토어 (추가 후보)
| 사이트 | URL | 메모 |
|--------|-----|------|
| DLC스토어 | https://smartstore.naver.com/dlc | - |
| 굿앤굿 | https://smartstore.naver.com/goodngood | - |
| 엠톤 | https://smartstore.naver.com/mton | - |
| 세멜리아 | https://smartstore.naver.com/cemelia | - |
| 바이스트 (브랜드스토어) | https://brand.naver.com/buyest | 쿠폰가 |
| 비아델루쏘 | https://smartstore.naver.com/viadellusso | - |
| 갤러리아백화점 | https://smartstore.naver.com/galleria | - |
| 주식회사 울산명품관 | https://smartstore.naver.com/ulsanluxury | 첫 썸네일만 (워터마크) |
| 롯데 청주 럭셔리에비뉴 | https://smartstore.naver.com/lottej127 | - |
| 유로라인 | https://smartstore.naver.com/euroline | - |
| 신세계백화점 | https://smartstore.naver.com/ssg01 | - |
| 코메타LX | https://smartstore.naver.com/shinsegaieul01 | - |
| 아르마디오 | https://smartstore.naver.com/lottegh94 | - |
| 더그란데 | https://smartstore.naver.com/thegrande | - |
| 롯데탑스 파주점 | https://smartstore.naver.com/lottepaju120 | - |
| BOBU | https://smartstore.naver.com/comteforest | - |
| 플루비아랩스 | https://smartstore.naver.com/pluvialabs | - |
| 비아인터내셔날 | https://smartstore.naver.com/benunonline | - |
| 업셋 | https://smartstore.naver.com/upset | - |
| 우아맘의 신발가게 | https://smartstore.naver.com/lvshoesz | - |
| 럭스리밋 | https://smartstore.naver.com/luxlimit | - |
| 파노 | https://smartstore.naver.com/panokorea | - |
| 랄라슈즈 | https://smartstore.naver.com/larlashoes | - |
| 올아이원트 | https://smartstore.naver.com/aint | 중고/스크래치 제외 |
| 더마 | https://smartstore.naver.com/nilsenco | 모델명 랜덤, 상품정보가 정확 |
| 블리에이 | https://smartstore.naver.com/vely_a | 모음전 제외 |
| 비블루 | https://smartstore.naver.com/bblue | 모음전 제외, 이미지 여부 불명 |
| 밀라니즈 | https://milaneez.com | 모델명: 브랜드명 옆 |

### 자체/Cafe24/기타 도메인 (추가 후보)
| 사이트 | URL | 플랫폼 추정 | 메모 |
|--------|-----|-----|------|
| 브릭맨션 | https://brickmansion.co.kr | **Cafe24** (2026-04-24 확인) | 심플렉스인터넷 호스팅, echosting.cafe24.com |
| 웍스아웃 | https://worksout.co.kr | 자체 (미확인) | - |
| 메종파르코 | https://www.maisonparco.com | **Cafe24** (2026-04-24 확인) | `file.cafe24cos.com` CDN. 모델명: 상품명 끝 |
| 꼬르소밀라노 | https://www.corsomilano.com | 자체 (미확인) | - |
| 하하몰 | https://hahamall.co.kr | 자체 (미확인) | - |
| 베이지그 | https://bazig.com | 자체 (미확인) | - |
| 유니코 | https://e-unico.co.kr | 자체 (미확인) | - |
| 셉템 | https://septem.kr | 자체 (미확인) | - |
| 라스트센스 | — | — | URL 미기재 |
| 로로모다 | https://loromoda.net | **Cafe24** (2026-04-24 확인) | echosting.cafe24.com + ec-base- 클래스 |
| 밀라니즈 | https://milaneez.com | **Cafe24** (2026-04-24 확인) | milaneez.com/web/product/ 패턴 |

### 대형 플랫폼 (장기 검토 대상)
| 사이트 | URL |
|--------|-----|
| 무신사 | https://www.musinsa.com |
| 29cm | https://www.29cm.co.kr |
| W컨셉 | https://display.wconcept.co.kr |
| 한섬 EQL | https://www.eqlstore.com |
| HAGO | https://www.hago.kr |
| 폴더 | https://www.folderstyle.com |
| 슈마커 | https://www.shoemarker.co.kr |
| ABC마트 | https://abcmart.a-rt.com |
| ABC마트 그랜드스테이지 | https://grandstage.a-rt.com |
| 훕시티 | https://www.hoopcity.co.kr |
| SSF | https://www.ssfshop.com |
| 아이엠샵 | https://iamshop-online.com |

---

## 14. 2026-04-15 작업 로그 (신규 6개 mall 본 수집 → BUYMA 등록 완료)

### 수집 결과 (raw_scraped_data, 6개 mall)
| mall | raw 건수 | distinct model_id |
|---|---:|---:|
| veroshopmall | 3,275 | 3,258 |
| fabstyle | 1,645 | 1,514 |
| premiumsneakers | 1,249 | 1,225 |
| t1global | 779 | 776 |
| vvano | 328 | 328 |
| loutique | 36 | 33 |
| **합계** | **7,312** | **7,134** |

### 1. dedup_corrector 하이브리드 우선순위 적용
- `okmall/dedup_corrector.py` 수정: `SOURCE_PRIORITY`/`IMAGE_PRIORITY`에 신규 6개 mall 뒤에 추가
  - loutique > vvano > veroshopmall > fabstyle > premiumsneakers > t1global
- `load_raw_model_ids` WHERE 절에 6개 mall 추가
- 실제 실행: 중복 그룹 6,899개 / duple 처리 4,480건
- BUYMA 등록된 duple 9건 수동 삭제 (샘플: labellusso/trendmecca의 Burberry/HELEN KAMINSKI)
- veroshopmall duple 3,141건 — 대부분 trendmecca(2,042)와 겹침

### 2. mall_brands 정리
- **brand_update.xlsx**: 71건 수동 매핑 반영 (buyma_brand_id, buyma_brand_name)
- **trendmecca 4 브랜드 수동 채움**: RIEDEL / Valkyrie / Paul Brial / Flik flak → `buyma_brand_id=0, buyma_brand_name=영문명, is_active=1`
- **NO BRAND / OTHER BRAND 비활성화**: carpi/NO BRAND (raw 0건), premiumsneakers/OTHER BRAND (raw 10건) → `is_active=0`, 해당 ace_products 10건 `is_active=0, buyma_product_id=NULL, status='deleted'` 처리
- **`created_at` 컬럼 추가**: mall_brands에 TIMESTAMP 컬럼 신규 추가 + 기존 1,696행 `2026-04-14`로 백필

### 3. ace_products brand 보정 (raw ↔ mall_brands ↔ ace)
원인: convert 시점에 mall_brands가 비어 있거나 buyma_brand_id/name이 나중에 채워진 경우, ace의 brand_id/name이 과거 값 그대로 남음.
- 신규 6개 mall 한정으로 보정: **663건 UPDATE**
- 기존 mall(okmall/kasina/trendmecca/labellusso)은 미처리 → `reports/mall_brands_issues_20260415.md`
- 신규 6개 mall 최종 결과: brand_id/brand_name 불일치 **0건**
- name 본문의 브랜드는 raw.brand_name_en과 100% 일치 — 브랜드 자체 오류 없음 (name의 일본어 병기 부재는 별도 이슈)

### 4. 카테고리 매핑
- `buyma_cleaners/category_cleaner.py register`: 미등록 경로 0건 (raw→mall_categories 이미 전부 등록됨)
- `match` (Gemini): 483건 → 429/452 성공
- `import` (수동 매핑 48건 반영): 2건 `is_active=0`, 46건 `buyma_category_id`/`is_active=1`
- `apply` → ace_products.category_id 7,259건 업데이트
- 신규 6개 mall 최종 미매핑: veroshopmall 3건만 남음

### 5. comments 빌드 로직 수정 (한글 원본 보존용은 아님 — 상품명 풀버전 보존)
이유: BUYMA 전송 시 `name`은 `truncate_buyma_name`으로 60자 잘림. 잘린 부분은 어디에도 남지 않았음.
- **수정 파일 8개**:
  - `okmall/buyma_new_product_register.py:714`
  - `okmall/stock_price_synchronizer.py:1286`
  - `kasina/stock_price_synchronizer_kasina.py:1079`
  - `nextzennpack/stock_price_synchronizer_nextzennpack.py:1250`
  - `labellusso/stock_price_synchronizer_labellusso.py:1314`
  - `trendmecca/stock_price_synchronizer_trendmecca.py:1288`
  - `v2_multisource/stock_price_synchronizer_v2.py:1277`
  - `fast_price_updater.py:629`
- 변경: `"comments": f"{model_no_text}\n\n{fixed_comments}"` → `f"{api_name}\n{model_no_text}\n\n{fixed_comments}"` (자르기 전 `api_name`을 comments 최상단에 삽입)

### 6. BUYMA 등록 실행
- 6개 mall active 대상 총 **2,817건** 중 duple/미완료 제외하고 **1,506+99+...** 등록 (fabstyle 763 / premiumsneakers 743 / t1global / veroshopmall 등)
- `is_published=1` 기준: fabstyle 763, premiumsneakers 743 (확인 시점)
- veroshopmall 99건만 대상 — 3,141건이 trendmecca 등과 중복이라 duple 처리됨 (정상)

### 7. 미해결/후속
- BUYMA 고아·유령 상품 정리 (`buyma_orphan_cleaner.py`) — WARP + 쿠키 재로그인 후 진행
- ace.name 본문 한글 잔재 정리 (Gemini 재번역, 총 1,192건)
- 기존 mall brand 보정 (별도 리포트 `reports/mall_brands_issues_20260415.md`)
- 미매핑 카테고리 568건 수동 매핑

---

## 15. 2026-04-17 작업 로그 (네이버 브랜드스토어 + 전체상품 수집기)

### 배경
OVERVIEW.md에 "⏸️ 스킵" 표기된 5개 스토어(까르피, 조하리스토어, 디몬트, 뚜또베네, 논현더팩토리) 재분석. 스킵 사유가 두 유형:
- **브랜드스토어 도메인 이슈 (carpi, joharistore)**: `brand.naver.com` 도메인이라 기존 collector의 `/i/v2/` XHR 패턴 매칭 안 됨
- **브랜드 리스트 없음 (dmont, tuttobene, thefactor2/논현더팩토리)**: 수집 순회 기점이 없음

### 1. 네이버 브랜드스토어 collector 신설 (`brand_store_collector.py`)
- 기존 `premiumsneakers_collector.py` 0 수정, 공유 컴포넌트 import 재사용
- **핵심 차이 1곳**: XHR regex `/i/v2/` → `/n/v2/`
- `fetch_detail_brand_store()` 로컬 정의, 나머지(collect_product_list, map_to_row, save_rows, get_brands)는 import
- carpi/joharistore 파일럿 각 10건 성공
- 상세: `naver/premiumsneakers/brand_store_collector.md`

### 2. 전체상품 페이지 collector 신설 (`premiumsneakers_category_collector.py`)
- 브랜드 리스트가 없거나 카테고리 필터링이 동작 안 하는 스토어용
- 처음엔 `mall_categories` 순회로 만들었으나 dmont/tuttobene에서 카테고리 필터가 동작 안 함 (모든 카테고리가 동일 상품 반환) → **전체상품 URL 1개 순회로 전환**
- 스토어별 전체상품 URL 하드코딩:
  ```python
  STORE_ALL_PRODUCT_URLS = {
      'dmont':      'https://smartstore.naver.com/dmont/category/ALL?...&size=80&filters=oa',
      'thefactor2': 'https://smartstore.naver.com/thefactor2/category/{hash}?...&size=80&filters=oa',
      'tuttobene':  'https://smartstore.naver.com/tutto-bene/category/ALL?...&size=80&filters=oa',
  }
  ```
- `filters=oa`로 품절 제외, `size=80`으로 페이지 적게
- **상품 상세에서 brand/category 자동 추출 → mall_brands/mall_categories에 auto-INSERT**
  - `ensure_mall_brand(brand_name_en)`: 없으면 `is_active=1, buyma_brand_id=NULL`
  - `ensure_mall_category(full_path)`: 없으면 `is_active=NULL, buyma_category_id=NULL` (category_cleaner 매칭 대기)
- 스토어별 상품명 정리 패턴 (예: dmont의 `디몬트 ` prefix, tuttobene의 `[국내배송]` 태그) — `NAME_CLEANUP_PATTERNS` dict
- 상세: `naver/premiumsneakers/category_collector.md`

### 3. 수집 현황 (2026-04-17 기준)

| mall | collector | 총 상품 | raw 저장 | 상태 |
|---|---|---:|---:|---|
| **thefactor2** | category_collector | 1,634 | 1,169 | 부분 수집, 전수 진행 예정 |
| **dmont** | category_collector | ? | 52 | 파일럿 |
| **tuttobene** | category_collector | ? | 134 | 파일럿 |
| **carpi** | brand_store_collector | — | 10 | 파일럿 (106 brands) |
| **joharistore** | brand_store_collector | — | 10 | 파일럿 (76 brands) |

### 4. 딜레이 단축
기존 `premiumsneakers_collector.py`의 `LIST_DELAY=(1~2), DETAIL_DELAY=(1~3)`을 건드리지 않고, 새 collector에서는 로컬 override:
```python
LIST_DELAY = (0.5, 1.0)
DETAIL_DELAY = (0.8, 1.5)
```

### 5. mall_categories 신규 스캔
`scan_store_brands.py --categories-only --insert-site`로 카테고리 선수집:
- dmont: 170건 INSERT
- tuttobene: 109건 INSERT
- thefactor2: 기존 35건 (모두 매핑+active)

(단 dmont/tuttobene는 카테고리 필터 미작동이라 실질적으로 사용 안 함. 전체상품 URL 방식으로 전환)

### 6. 남은 이슈
- **한글 브랜드 매핑**: dmont 4개 + tuttobene 8개 mall_brands에 auto-INSERT됨, 전부 `buyma_brand_id=NULL`. xlsx 패턴으로 수동 매핑 필요
- **carpi/dmont 이미지 1장 문제**: 상품 JSON productImages가 대표 1장만. 상세 HTML에서 추가 이미지 추출 로직 필요
- **신규 카테고리 매핑**: 수집 도중 auto-INSERT된 mall_categories는 `is_active=NULL` → `category_cleaner.py match` 돌려야 함
- **thefactor2 전수 수집 완료 필요** (현재 1,169/1,634)
