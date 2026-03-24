# 멀티 소스 → ACE 변환기 설계 컨텍스트

## 현재 상태 (2026-03-05)

### 파이프라인 구조
```
COLLECT → CONVERT(번역 제외) → PRICE → TRANSLATE(최저가분만) → IMAGE(최저가분만) → REGISTER
```
- 각 단계별 스크립트가 자체 skip/dedup 로직 보유
- orchestrator.py가 전체 흐름 관리

### 수집처(COLLECT) 현황
| 수집처 | 스크립트 | 대상 | raw 수량 | 비고 |
|--------|---------|------|---------|------|
| okmall | `okmall/okmall_all_brands_collector.py` | 전 브랜드 | 42,029개 | HTML 크롤링 (Playwright) |
| kasina | `kasina/kasina_collector.py` | NIKE만 | 661개 | Shopby API 호출 |

### DB 통계
```
okmall: total=42,029, in_stock=41,859, out_of_stock=170
kasina: total=661,    in_stock=327,    out_of_stock=334
model_id 중복(양쪽 다 있음): 6개
kasina에만 있는 model_id: 639개
```

---

## 핵심 이슈: 멀티 소스 → 단일 ACE

**ace_products 테이블은 1개.** 같은 model_id의 상품이 여러 수집처에 있을 때, **raw_price가 가장 낮은 수집처의 raw 데이터**를 ace로 변환해야 마진 극대화.

### 중복 6개 상품 가격/재고 비교
| model_id | kasina price | okmall price | kasina 재고 | okmall 재고 |
|----------|-------------|-------------|------------|------------|
| AR3566-002 | 153,300 | 179,000 | **전체 품절** | 재고 있음 |
| HJ4334-500 | 90,350 | 118,000 | 부분 재고 | 재고 있음 |
| HQ7540-002 | 127,200 | 135,000 | **전체 품절** | 재고 있음 |
| HQ9148-201 | 104,300 | 128,000 | **전체 품절** | 재고 있음 |
| IM3078-002 | 135,200 | 149,000 | **전체 품절** | 재고 있음 |
| IO4482-001 | 167,200 | 198,000 | **전체 품절** | 재고 있음 |

→ 카시나가 전부 더 저렴하지만, 6개 중 5개가 카시나에서 전체 품절
→ **가격뿐 아니라 재고(stock_status)도 고려 필요**

---

## raw_scraped_data 테이블 구조

```sql
CREATE TABLE raw_scraped_data (
  id INT AUTO_INCREMENT PRIMARY KEY,
  source_site VARCHAR(50),          -- 'okmall' | 'kasina'
  mall_product_id VARCHAR(100),     -- 수집처별 상품 고유 ID
  brand_name_en VARCHAR(100),
  brand_name_kr VARCHAR(100),
  product_name VARCHAR(255),
  p_name_full TEXT,
  model_id VARCHAR(100),            -- 브랜드 스타일코드 (매칭 키)
  category_path VARCHAR(255),
  original_price DECIMAL(15,2),     -- 정가
  raw_price DECIMAL(15,2),          -- 판매가 (할인 적용)
  stock_status VARCHAR(20),         -- 'in_stock' | 'out_of_stock'
  raw_json_data LONGTEXT,           -- JSON (옵션, 이미지 등)
  product_url TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  UNIQUE KEY uk_source_mall_product (source_site, mall_product_id)
);
```

### raw_json_data 내 options 구조 비교

**okmall:**
```json
{
  "options": [
    {"color": "Free", "tag_size": "단일사이즈", "real_size": "-", "option_code": "406726", "status": "in_stock"}
  ],
  "season": "25FW",
  "measurements": {"XS": {"shoulder": "45cm", ...}, "S": {...}},
  "composition": {"outer": "100% 폴리에스터", ...},
  "ld_json_product": {...},
  "rating": {...}
}
```

**kasina:**
```json
{
  "options": [
    {"color": "STADIUM GREEN", "tag_size": "250", "option_code": "NK...", "stock_count": -999, "status": "in_stock", "buy_price": 169000.0}
  ],
  "color": "STADIUM GREEN/BRT CRIMSON",
  "images": ["https://..."],
  "list_images": ["https://..."],
  "kasina_brand_no": 40331479,
  "duty_info": "...",
  "categories": [{...}],
  "gender": "남성",
  "register_date": "...",
  "sale_start": "...",
  "sale_end": "...",
  "like_count": 0
}
```

### options 공통 필드 (양쪽 동일)
| 필드 | 설명 |
|------|------|
| `color` | 색상명 |
| `tag_size` | 사이즈 (okmall: "S", "M" / kasina: "250", "260") |
| `option_code` | 옵션 고유코드 |
| `status` | `"in_stock"` or `"out_of_stock"` |

### options 차이점
| 필드 | okmall | kasina |
|------|--------|--------|
| `real_size` | 있음 ("-" or 실측값) | 없음 |
| `stock_count` | 없음 | 있음 (-999=무한재고, 0=품절) |
| `buy_price` | 없음 | 있음 (옵션별 매입가) |

### raw_json_data 차이점 (options 외)
| 필드 | okmall | kasina |
|------|--------|--------|
| `measurements` | 있음 (사이즈별 실측 데이터) | **없음** |
| `composition` | 있음 (소재/혼용률) | **없음** (duty_info에 일부) |
| `season` | 있음 ("25FW" 등) | **없음** |
| `images` | **없음** (별도 수집) | 있음 (CDN URL) |
| `gender` | **없음** | 있음 |

---

## raw_to_ace_converter.py 현재 구조

### 사이트별 하드코딩 위치 (변경 필요)

| 파일:라인 | 내용 | 변경 방향 |
|-----------|------|----------|
| `raw_to_ace_converter.py:659` | `WHERE mall_name = 'okmall'` (브랜드 매핑) | source_site 기반으로 변경 |
| `raw_to_ace_converter.py:686` | `WHERE mc.mall_name = 'okmall'` (카테고리 매핑) | source_site 기반으로 변경 |
| `raw_to_ace_converter.py:784` | `mall_name = 'okmall'` (미매핑 카테고리 등록) | source_site 기반으로 변경 |
| `raw_to_ace_converter.py:791` | `VALUES ('okmall', ...)` (미매핑 카테고리 INSERT) | source_site 기반으로 변경 |
| `raw_to_ace_converter.py:946` | `raw_data.get('source_site', 'okmall')` | 기본값 제거 |

### convert_single_raw_to_ace() 에서 raw_json_data 사용하는 부분
- `json_data.get('options', [])` → **양쪽 동일 구조** (color, tag_size, status)
- `json_data.get('measurements', {})` → **okmall만 있음** (카시나는 빈 딕셔너리)
- `json_data.get('composition', {})` → **okmall만 있음**
- `json_data.get('season')` → **okmall만 있음**

→ measurements/composition/season이 없으면 해당 부분은 자연스럽게 빈 값으로 처리됨
→ **분기 처리 없이도 카시나 데이터가 통과 가능**

### mall_brands / mall_categories 테이블
- `mall_name` 컬럼으로 수집처별 매핑 데이터 분리
- okmall: 41개 브랜드, 다수 카테고리 매핑 완료
- kasina: NIKE 1개 브랜드, 카테고리 매핑 **미완료** (신규 경로 자동 등록 후 수동 매핑 필요)

---

## 설계 선택지

### 옵션 1: raw_to_ace_converter.py 하나로 통합
- `'okmall'` 하드코딩 → `source_site` 기반으로 변경
- mall_brands, mall_categories 조회 시 `mall_name` = raw의 `source_site` 사용
- measurements/composition/season 없으면 자연스럽게 빈 값 → 분기 불필요
- **model_id 중복 시 최저가 선택 로직** 추가 필요 (fetch_raw_data 단계 또는 별도 전처리)

### 옵션 2: 수집처별 converter 분리
- `kasina_raw_to_ace_converter.py` 별도 생성
- 각자 독립적으로 ace에 저장
- **model_id 중복 처리가 어려움** (각각 독립 실행이라 누가 먼저 들어갔는지 모름)

### 핵심 고려사항: model_id 중복 시 최저가 선택
```sql
-- 같은 model_id의 raw 데이터 중 최저 raw_price (+ 재고 있는 것 우선)
SELECT r.*
FROM raw_scraped_data r
INNER JOIN (
    SELECT model_id, MIN(raw_price) as min_price
    FROM raw_scraped_data
    WHERE stock_status = 'in_stock'
    GROUP BY model_id
) best ON r.model_id = best.model_id AND r.raw_price = best.min_price
WHERE r.stock_status = 'in_stock'
```
→ 재고 있는 것 중 최저가 선택. 전부 품절이면 품절 중 최저가 선택.

---

## 다음 단계 TODO

1. **raw_to_ace_converter.py 구조 결정** (옵션 1 vs 2)
2. **model_id 중복 시 최저가 선택 로직** 구현
3. **kasina 카테고리 매핑** (mall_categories에 kasina 경로 등록 + buyma_category_id 수동 매핑)
4. orchestrator.py에 kasina COLLECT 라우팅 추가
5. (향후) stock_price_synchronizer.py에 카시나 대응

---

## 참조 파일 경로
| 파일 | 위치 |
|------|------|
| kasina 수집기 | `buyma/kasina/kasina_collector.py` |
| okmall 수집기 | `buyma/okmall/okmall_all_brands_collector.py` |
| 현재 converter | `buyma/okmall/raw_to_ace_converter.py` |
| orchestrator | `buyma/okmall/orchestrator.py` |
| DB 스키마 | `buyma/okmall/ace_tables_create.sql` |
| .env | `buyma/.env` |
| kasina API 참조 | `buyma/kasina/kasina_collect.md` |
| kasina 구현 계획 | `buyma/kasina/PLAN_kasina_collector.md` |
