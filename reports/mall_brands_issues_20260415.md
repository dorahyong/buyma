# mall_brands 이슈 정리 (2026-04-15)

신규 6개 mall 보정은 완료. **기존 mall(okmall/kasina/trendmecca/labellusso)은 미처리** — 추후 작업 필요.

---

## 1. 현황

### 1-1. ace_products ↔ mall_brands 불일치 (is_active=1, locked 제외)

**데이터(행) 단위**

| mall | total | diff_id | diff_name | no_mall_brand |
|---|---:|---:|---:|---:|
| okmall | 28,942 | 517 | 2,604 | 1,056 |
| kasina | 6,409 | 475 | 524 | 475 |
| trendmecca | 1,996 | 55 | 55 | 0 |
| labellusso | 2,879 | 2 | 2 | 0 |
| 신규 6개 mall | — | 0 | 0 | 0 |

**고유 브랜드 단위**

| mall | 고유 브랜드 | diff_id | diff_name | no_mall_brand |
|---|---:|---:|---:|---:|
| okmall | 334 | 1 | 44 | 8 |
| kasina | 165 | 1 | 2 | 1 |
| trendmecca | 138 | 6 | 6 | 0 |
| labellusso | 127 | 1 | 1 | 0 |

실제 수정 필요한 고유 브랜드 수는 ~54개 수준.

---

## 2. 불일치 원인 (샘플 분석으로 확인)

### 원인 A: 특수문자/표기 차이로 mall_brands JOIN 실패
raw의 `brand_name_en`과 mall_brands의 `mall_brand_name_en`이 완전히 같지 않음 (UPPER 비교해도 매칭 실패).

| raw.brand_name_en | mall_brands 표기 | 건수 |
|---|---|---:|
| `` Arc`teryx `` (백틱) | `ARC'TERYX(アークテリクス)` (아포스트로피) | 517 |
| `STUSSY` | (mall_brands에 매칭 없음) | 475 |

이유: 수집 경로가 다름.
- **mall_brands**: 브랜드 리스트 페이지(공식 표기)에서 등록
- **raw.brand_name_en**: 상품 상세 페이지 JSON(판매자 입력)에서 추출
- 같은 브랜드라도 표기가 일치하지 않을 수 있음

### 원인 B: convert 당시 mall_brands가 비어있어 ace에 빈 값 저장
converter 로직(`kasina/raw_to_converter_kasina.py:954-958`):
- `mall_brands.buyma_brand_id IS NULL` → fallback으로 `raw.brand_name_en` 사용
- 그런데 과거 시점엔 일부 행이 정상 fallback조차 안 돼서 `ace.brand_name=''` (빈 문자열) 저장됨
- 이후 mall_brands는 채워졌지만 ace_products는 **자동 재반영 안 됨**

| raw.brand_name_en | ace (id, name) | mall_brands (id, name) | 건수 |
|---|---|---|---:|
| Ortovox | (0, '') | (0, 'Ortovox') | 410 |
| Lundhags | (0, '') | (0, 'Lundhags') | 252 |
| RIEDEL | (0, '') | (0, 'RIEDEL') | 142 |
| La Sportiva | (0, '') | (0, 'La Sportiva') | 136 |

---

## 3. 완료된 조치 (2026-04-15)

1. **brand_update.xlsx 반영**: mall_brands 71건 UPDATE (buyma_brand_id, buyma_brand_name)
2. **trendmecca 4개 브랜드 수동 입력**: RIEDEL / Valkyrie / Paul Brial / Flik flak → `buyma_brand_id=0, buyma_brand_name=영문명, is_active=1`
3. **미사용 브랜드 비활성화**: carpi / NO BRAND (raw 0건), premiumsneakers / OTHER BRAND (raw 10건) — *아직 미적용*
4. **신규 6개 mall의 ace_products brand 보정**: 663건 UPDATE (raw → mall_brands → ace 체인으로 재정렬). 결과 diff=0
5. **mall_brands에 created_at 컬럼 추가** (2026-04-14로 백필)

---

## 4. 기존 mall 추후 작업 (미처리)

### 4-1. 원인 A 해결 (표기 차이)
- 수동 작업: 같은 브랜드가 두 표기로 나뉜 케이스 병합 (예: `` Arc`teryx `` → `ARC'TERYX`)
- mall_brands에 raw 표기 variant를 추가로 INSERT하거나, raw 쪽 문자열을 정규화
- 단발로 해결되지 않음 — 수집 시점에 raw.brand_name_en을 mall_brands로 lookup해서 정규화하는 로직이 최선

### 4-2. 원인 B 해결 (과거 convert 시점 값 보정)
신규 6개 mall에 적용한 SQL과 동일, WHERE 필터만 기존 mall로 변경:
```sql
UPDATE ace_products a
JOIN raw_scraped_data r ON r.id = a.raw_data_id
LEFT JOIN mall_brands mb ON mb.mall_name=r.source_site
                        AND UPPER(mb.mall_brand_name_en)=UPPER(r.brand_name_en)
                        AND mb.is_active=1
SET a.brand_id = COALESCE(mb.buyma_brand_id, 0),
    a.brand_name = COALESCE(NULLIF(mb.buyma_brand_name,''), r.brand_name_en)
WHERE a.is_active=1
  AND (a.is_buyma_locked=0 OR a.is_buyma_locked IS NULL)
  AND a.source_site IN ('okmall','kasina','trendmecca','labellusso','nextzennpack')
  AND (COALESCE(a.brand_id,0) <> COALESCE(mb.buyma_brand_id,0)
       OR COALESCE(a.brand_name,'') <> COALESCE(NULLIF(mb.buyma_brand_name,''), r.brand_name_en, ''))
```

### 4-3. 주의
- 이미 BUYMA에 등록된 상품(`is_published=1`)도 영향 받음 → 다음 PS API 호출 시 BUYMA 쪽 brand 변경 가능
- `is_buyma_locked=1`은 건드리지 않음
- 표기 차이(원인 A)는 위 SQL로 해결 안 됨 — mall_brands JOIN 실패 행은 그대로 `mb=NULL` → fallback(raw.brand_name_en)으로 업데이트

### 4-4. 구조적 개선안 (장기)
- `buyma_cleaners/brand_cleaner.py` (category_cleaner.py 패턴) 작성: register → match → apply
- raw에서 새 brand_name_en 발견 시 mall_brands에 자동 INSERT
- match 단계에서 표기 variant를 찾아 통합 또는 보조 컬럼(`aliases`) 추가

---

## 5. 참고
- converter 브랜드 매칭 로직: `kasina/raw_to_converter_kasina.py:870-884, 954-958, 1156`
- 브랜드 입력 CLI: `buyma_master_data/brands.csv` (BUYMA 마스터 20,583개)
- 이번 작업 세션 전체 흐름: 본 리포트와 `reports/worklog_20260414.md` 참조
