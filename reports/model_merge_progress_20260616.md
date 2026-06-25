# 모델 중복 merge — 진행 상태 & 재개 핸드오프 (2026-06-16)

> **이 문서만 읽으면 어디까지 했고 다음에 뭘 할지 바로 알 수 있게 작성.**
> 원 설계 배경은 `reports/model_merge_design_handoff.md` 참조 (스키마 A/B 논의·영향조사 등).
> 메모리: `memory/project_model_merge_design.md`

---

## 0. 한 줄 상태

같은 품번이 여러 mall에 있을 때 **최저가 mall을 winner로 살리고 마진 나는 옵션을 합쳐 BUYMA 출품 1건으로 묶는** 구조. **1~5단계(기존 데이터 일회성 백필) 완료. 다음은 6단계 register(실제 BUYMA push).**

---

## 1. 확정된 핵심 결정 (바꾸지 말 것)

1. **스키마 A (테이블 분리)** — 새 테이블 5개, 기존 `ace_products`는 안 건드림.
2. **운영 파일 절대 안 건드림.** 코드 수정은 원본 copy → 접미사 `_merge` 사본만. (오케스트레이터는 자식을 경로문자열+subprocess로 부르므로 사본에선 경로상수만 교체)
3. **멤버 조회 범위 = `is_active=1 OR status='duple'`** — 옛 dedup이 죽인 duple 형제(다른 mall)가 진짜 merge 멤버라서 반드시 포함.
4. **경쟁자 최저가 = 기존 `ace_products.buyma_lowest_price` 재사용** (새 크롤 안 함, 97% 커버). 없으면 20% 목표가. 신선도 갱신은 7단계 reconcile에서.
5. **마진 게이트 = 마진액 > 0** (`is_margin_ok`).
6. **`control='suspend'` 절대 금지** (BUYMA가 거부). 출품불가 listing은 `control='draft'` 유지 + `winner_offering_id=NULL`로 표시.
7. **이미지 선택: winner 이미지 >5장이면 winner, ≤5장이면 멤버 중 이미지 최다로 폴백, 최대 20장.**
8. **성능: ace/variants/images는 1쿼리로 메모리 인덱스 프리로드.** 그룹마다 원격 DB 왕복하면 10분+ 걸림.

---

## 2. 새 테이블 5개 (이미 생성됨)

DDL: `okmall_reference/merge_tables_create.sql` (재실행 안전, IF NOT EXISTS)

```
buyma_listings            출품 정체성(BUYMA 1:1). buyma_product_id/is_published/locked_*는 여기에만.
  ├ source_offerings      수집처별 상품(그룹 멤버). is_active=0이어도 보존.
  │    └ source_offering_options   수집처별 색/사이즈/재고/매입가
  ├ listing_options       실제 출품 옵션(마진O union) + sourced_offering_option_id 소싱포인터
  └ listing_images        출품 이미지
```
- FK 4개: offerings/options/images → listings, offering_options → offerings (ON DELETE CASCADE, 평소 soft-delete라 무해)
- **변경됨: `source_offerings.margin_rate`를 DECIMAL(5,2)→(10,2)로 확장** (비싼 멤버 음수 마진율 -5858% 폭주 대비). SQL 파일 반영됨.

---

## 3. 만든 스크립트 (전부 `okmall/`, 기본 DRY-RUN, `--execute`로 실제 적재)

실행 순서대로:

| # | 스크립트 | 하는 일 | 적재 테이블 |
|---|---|---|---|
| 2 | `dedup_corrector_merge.py` | GROUP — 중복 그룹 식별(원본 dedup 매칭 동일), 죽이지 않고 적재 | buyma_listings, source_offerings |
| 3 | `offering_options_loader_merge.py` | 옵션·재고 투영(ace_product_variants→) | source_offering_options |
| 4 | `resolve_merge.py` | winner 선정 + 마진계산 + 출품옵션 union | source_offerings(마진), buyma_listings(winner/가격), listing_options |
| 5 | `image_union_loader_merge.py` | 이미지 선택·투영 | listing_images |

실행 예: `PYTHONIOENCODING=utf-8 python -u okmall/resolve_merge.py --execute`
- 전부 멱등(재실행 시 upsert로 최신화). 4번은 executemany 한줄씩이라 ~14분 걸림(나머지 ~1분).
- 1번(GROUP 매칭)은 `dedup_corrector.py`(원본)와 동일 로직. 2~5 전부 ace_products·BUYMA 0 영향(읽기+merge테이블 쓰기만).

---

## 4. 현재 DB 적재 상태 (2026-06-16 기준, 검증 완료)

| 테이블 | 건수 | 비고 |
|---|---|---|
| `buyma_listings` | 16,745 | **winner 설정 12,188(출품가능)** / winner NULL 4,557(출품불가=마진 없음) |
| `source_offerings` | 41,335 | is_margin_ok=1 → 22,440 |
| `source_offering_options` | 136,018 | 옵션 매입가는 offering 단위 상속 |
| `listing_options` | 43,063 | 마진O 옵션 union |
| `listing_images` | 66,943 | 12,188 listing 100% 커버, 최대 20장 |

- winner 분포: **okmall 5,331 / non-okmall 6,857** ← merge의 핵심 가치(싼 mall로 살아난 출품)
- 경쟁자가 기준 11,651 / 20%목표가 537

---

## 5. 다음 할 일 (재개 시 여기부터)

### ⚠️ 6단계 — register (사본, **실제 BUYMA push 시작**)
- 원본 `okmall/buyma_new_product_register.py` → 사본 `_merge`로.
- merge 테이블(winner 있는 buyma_listings + listing_options + listing_images) 읽어 BUYMA API push.
- **여기서 처음으로 BUYMA에 실제 상품 생성됨** → 반드시 **소량(1~5건) 테스트 → 확인 → 점진 확대**.
- buyma_product_id를 `buyma_listings`에 받아써야 함(웹훅/정체성). locked_* 백업도.
- 정체성(이름/브랜드/카테고리)은 buyma_listings 값 사용. 지금 seed는 우선순위 최상위 멤버 ace에서 임시로 넣은 값이라, register 전에 정체성 적절한지 점검 필요.
- 시작 전: 기존 register가 BUYMA에 어떻게 올리는지 읽고 소량 테스트 계획부터 보고.

### 7단계 — stock sync 사본 (가장 위험, dry-run 필수)
- 5개 sync의 삭제 판정 분모를 "본 source_site 옵션만"으로 한정 (5,320건 false-delete 사고 메커니즘).
- 그룹 reconcile = 멤버 재수집→마진 재계산→winner 교체(행 안 옮기고 필드만)→BUYMA diff push.

### 8단계 — 운영 전환 (이게 "매일 돌게" 만드는 단계)
- **현재 merge는 1회성 백필일 뿐. 운영 중복검증은 여전히 옛 `dedup_corrector.py`가 매일 돌며 죽이는 중.**
- 8단계 = 2~4 스크립트를 일일 파이프라인(run_daily_*)에 편입 + 옛 dedup-kill 교체 + 파괴적 cleaner(cleanup_duplicates 등)에 "merge 그룹 건드리지 마" 가드.
- register/stock 검증된 뒤 전환(그 전에 바꾸면 매일 돌던 게 깨질 위험).

---

## 6. 알아둘 함정/이슈 (이미 해결한 것 포함)

- SQL 파일 파싱 시 `-- ①` 주석 줄이 CREATE 앞에 붙어 통째 스킵된 적 있음 → 주석줄 제거 후 실행.
- margin_rate 컬럼 overflow(위 2번) 해결됨.
- `ace_product_variants.source_raw_price`는 사실상 비어있음(535,845중 2건) → 옵션별 매입가 못 씀, offering 단위 상속.
- DB 접속: `.env` (DB_HOST=54.180.248.182, DB_NAME=buyma). pymysql, charset utf8mb4.
- Windows: subprocess/실행에 `PYTHONIOENCODING=utf-8` 필수.
- listing_options dry-run 44,167 vs 실제 43,063 — 동일 (color,size) union 병합 차이(정상).
