# BUYMA 자동화 시스템

OkMall 상품을 수집하여 BUYMA에 자동으로 등록/관리하는 전체 자동화 시스템입니다.

---

## 목차

1. [시스템 개요](#시스템-개요)
2. [전체 프로세스](#전체-프로세스)
3. [파일 구조](#파일-구조)
4. [환경 설정](#환경-설정)
5. [자동 실행 설정](#자동-실행-설정)
6. [수동 실행](#수동-실행)
7. [데이터베이스](#데이터베이스)
8. [트러블슈팅](#트러블슈팅)

---

## 시스템 개요

### 핵심 2가지 파이프라인

```
┌─────────────────────────────────────────────┐
│  A. 신규 상품 등록 파이프라인 (매일 1회)      │
│     orchestrator.py                         │
│     → 새 상품 발굴 + 바이마 등록              │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  B. 재고/가격 동기화 (매일 1회)               │
│     stock_price_synchronizer.py             │
│     → 기존 상품 실시간 재고/가격 체크         │
│     → 품절/삭제/가격변동 바이마 반영          │
└─────────────────────────────────────────────┘
```

### 왜 2개로 분리되었나?

| 역할 | 파이프라인 A | 파이프라인 B |
|------|-------------|-------------|
| 대상 | 신규 상품 | 바이마 등록된 상품 (40,000+개) |
| 처리 | 수집 → 변환 → 이미지 → 등록 | 실시간 재고/가격만 확인 |
| 속도 | 느림 (이미지 수집 포함) | 빠름 (HTML만 파싱) |
| OkMall 요청 | 신규만 (적음) | 전체 (많음, 차단 위험) |

---

## 전체 프로세스

### A. 신규 상품 등록 파이프라인 (orchestrator.py)

```
1. COLLECT    okmall_all_brands_collector.py
   ↓          OkMall에서 브랜드별 상품 목록 수집
   ↓          → raw_scraped_data 저장
   ↓          --skip-existing 옵션: 이미 DB에 있는 상품은 스킵
   ↓          model_id 없는 상품은 스킵

2. CONVERT    raw_to_ace_converter.py --skip-translation
   ↓          raw_scraped_data → ace_products 변환
   ↓          브랜드/카테고리 매핑, 가격 계산 (번역은 스킵)

3. PRICE      buyma_lowest_price_collector.py
   ↓          바이마에서 경쟁사 최저가 수집
   ↓          경쟁자 없으면 마진 20% 가격 역산
   ↓          마진율 계산

4. TRANSLATE  convert_to_japanese_gemini.py --price-checked-only
   ↓          최저가 확보된 상품만 일본어 번역
   ↓          (불필요한 Gemini API 호출 절약)

5. IMAGE      image_collector_parallel.py --price-checked-only
   ↓          최저가 확보된 상품만 이미지 수집
   ↓          r2_image_uploader.py → Cloudflare R2 업로드

6. REGISTER   buyma_new_product_register.py
              바이마 API로 상품 등록
              (is_published=0, model_no 있고 중복 아닌 것만)
```

### B. 재고/가격 동기화 (stock_price_synchronizer.py)

```
바이마 등록된 상품 조회 (is_published=1)
    ↓
각 상품의 source_product_url로 OkMall 직접 접속
    ↓
실시간 재고/가격 수집
    ↓
변동 감지 시:
  - 일부 옵션 품절 → 바이마 재고 수정 API
  - 전체 품절 → 바이마 삭제 API
  - OkMall에서 삭제됨 → 바이마 삭제 API
  - 마진 손해 → 바이마 삭제 API
  - 가격 변동 → 바이마 가격 수정 API
```

---

## 파일 구조

### 핵심 실행 파일 (자동화)

| 파일 | 역할 | 자동 실행 주기 |
|------|------|--------------|
| `server.py` | ⚠️ **바이마 Webhook 서버 (24시간 상시 실행 필수)** | 24/7 상시 실행 |
| `orchestrator.py` | 신규 상품 등록 파이프라인 총괄 | 매일 1회 (오전 6시) |
| `stock_price_synchronizer.py` | 기존 상품 재고/가격 동기화 | 매일 1회 (새벽 3시) |

### 워커 스크립트 (orchestrator가 호출)

| 단계 | 파일 | 역할 |
|------|------|------|
| COLLECT | `okmall_all_brands_collector.py` | OkMall 상품 수집 (model_id 없으면 스킵) |
| CONVERT | `raw_to_ace_converter.py` | 데이터 변환 (번역 제외, `--skip-translation`) |
| PRICE | `buyma_lowest_price_collector.py` | 바이마 최저가 수집 |
| TRANSLATE | `convert_to_japanese_gemini.py` | 최저가 확보분만 일본어 번역 (`--price-checked-only`) |
| IMAGE | `image_collector_parallel.py` | 최저가 확보분만 이미지 수집 (`--price-checked-only`) |
| IMAGE | `r2_image_uploader.py` | R2 이미지 업로드 |
| REGISTER | `buyma_new_product_register.py` | 바이마 상품 등록 |

### 유틸리티

| 파일 | 역할 |
|------|------|
| `convert_to_japanese_gemini.py` | Gemini API 일본어 번역 (raw_to_ace_converter가 import) |
| `sync_categories.py` | 카테고리 동기화 도구 |
| `colors.csv` | 색상 매핑 데이터 |
| `size_details2.csv` | 사이즈 상세 매핑 데이터 |

### 인프라

| 파일 | 역할 |
|------|------|
| `server.py` | 바이마 Webhook 서버 (24시간 상시 실행 필수) |

### 설정 파일

| 파일 | 역할 |
|------|------|
| `.env` | 환경변수 (DB, API 키 등) |
| `ace_tables_create.sql` | DB 테이블 생성 스크립트 |

---

## 환경 설정

### 1. Python 패키지 설치

```bash
pip install requests beautifulsoup4 pymysql sqlalchemy python-dotenv boto3 playwright google-generativeai
playwright install chromium
```

### 2. 환경변수 설정

프로젝트 루트(`buyma/`)에 `.env` 파일 생성:

```bash
# DB 연결
DB_HOST=your_db_host
DB_PORT=3306
DB_USER=your_db_user
DB_PASSWORD=your_db_password
DB_NAME=buyma

# Buyma API
BUYMA_MODE=1
BUYMA_ACCESS_TOKEN=your_buyma_access_token
BUYMA_BUYER_ID=your_buyma_buyer_id

# Cloudflare R2
R2_ACCESS_KEY_ID=your_r2_access_key
R2_SECRET_ACCESS_KEY=your_r2_secret_key
R2_ENDPOINT_URL=https://your_account_id.r2.cloudflarestorage.com
R2_BUCKET_NAME=buyma-images
R2_PUBLIC_URL=https://pub-xxxxx.r2.dev

# Gemini API
GEMINI_API_KEY=your_gemini_api_key
```

### 3. 데이터베이스 초기 설정

```bash
# MySQL 접속 후
mysql> source okmall/ace_tables_create.sql

# 브랜드 매핑 데이터 (mall_brands) 입력 필요
# 카테고리 매핑 데이터 (mall_categories) 입력 필요
# 배송 설정 (shipping_config) 입력 필요
```

---

## 자동 실행 설정

### ⚠️ 1) Webhook 서버 (24시간 상시 실행 필수!)

**중요:** `server.py`가 실행되지 않으면 바이마 등록/수정/삭제 결과를 받을 수 없습니다!

```bash
# 서버 실행
cd C:\Users\hyong\OneDrive\원블록스\buyma\okmall
python server.py
```

**포트:** 기본 8000번 (server.py 129줄에서 변경 가능)

**외부 접근 설정 (필수):**
```
1. 공인 IP 또는 도메인 확보
2. 포트 포워딩: 외부 포트 → 127.0.0.1:8000
3. 바이마 API 설정에 Webhook URL 등록:
   예) http://your-server.com:8000/webhook/buyma
```

**자동 재시작 설정:**
- Windows 서비스로 등록 또는
- Task Scheduler로 시작 시 자동 실행 설정
- 또는 screen/nohup 사용 (Linux 서버인 경우)

**서버 상태 확인:**
```bash
# 웹 브라우저 또는 curl로 접속
curl http://127.0.0.1:8000/
# 응답: {"status": "ok", "message": "Buyma Webhook Server is running"}
```

---

### Windows Task Scheduler 등록

#### 2) 재고/가격 동기화 (새벽 3시)

```xml
작업 이름: BUYMA_Stock_Sync
트리거: 매일 새벽 3:00
작업: python C:\Users\hyong\OneDrive\원블록스\buyma\okmall\stock_price_synchronizer.py
시작 위치: C:\Users\hyong\OneDrive\원블록스\buyma
```

#### 3) 신규 상품 등록 (오전 6시)

```xml
작업 이름: BUYMA_New_Products
트리거: 매일 오전 6:00
작업: python C:\Users\hyong\OneDrive\원블록스\buyma\okmall\orchestrator.py --mode FULL
시작 위치: C:\Users\hyong\OneDrive\원블록스\buyma
```

**시간 분리 이유:** OkMall 동시 접속 시 차단 위험 방지

---

## 수동 실행

### Webhook 서버 (필수!)

```bash
# Webhook 서버 실행 (24시간 켜두기)
cd okmall
python server.py

# 또는 백그라운드 실행 (Linux/Mac)
nohup python server.py > server.log 2>&1 &
```

### 전체 자동화 실행

```bash
# 신규 상품 파이프라인 전체
python okmall/orchestrator.py

# 재고/가격 동기화
python okmall/stock_price_synchronizer.py
```

### 단계별 개별 실행

#### 1. 상품 수집

```bash
# 전체 브랜드 (신규만)
python okmall/okmall_all_brands_collector.py --skip-existing

# 특정 브랜드 (전체 재수집)
python okmall/okmall_all_brands_collector.py --brand NIKE

# 특정 브랜드 (신규만)
python okmall/okmall_all_brands_collector.py --brand NIKE --skip-existing

# 상품 수 제한 (테스트용)
python okmall/okmall_all_brands_collector.py --brand NIKE --limit 10 --dry-run
```

#### 2. 데이터 변환

```bash
# 신규 데이터만 변환
python okmall/raw_to_ace_converter.py --brand NIKE

# 기존 데이터도 재변환 (코드 수정 후 1회)
python okmall/raw_to_ace_converter.py --brand NIKE --upsert

# 테스트
python okmall/raw_to_ace_converter.py --brand NIKE --limit 5 --dry-run
```

#### 3. 이미지 수집/업로드

```bash
# 이미지 수집
python okmall/image_collector_parallel.py --brand NIKE

# R2 업로드
python okmall/r2_image_uploader.py --brand NIKE

# 병렬 워커 수 조정
python okmall/image_collector_parallel.py --brand NIKE --workers 8
```

#### 4. 최저가 수집

```bash
# 전체
python okmall/buyma_lowest_price_collector.py

# 특정 브랜드
python okmall/buyma_lowest_price_collector.py --brand NIKE

# 테스트
python okmall/buyma_lowest_price_collector.py --brand NIKE --limit 10 --dry-run
```

#### 5. 상품 등록

```bash
# 신규 상품 등록
python okmall/buyma_new_product_register.py --brand NIKE

# 중복 model_no 상품 삭제
python okmall/buyma_new_product_register.py --clean-duplicates

# model_no 없는 상품 삭제
python okmall/buyma_new_product_register.py --clean-no-model

# 특정 상품 1개 테스트
python okmall/buyma_new_product_register.py --product-id 12345 --dry-run
```

#### 6. 재고/가격 동기화

```bash
# 전체 동기화
python okmall/stock_price_synchronizer.py

# 특정 상품만
python okmall/stock_price_synchronizer.py --product-id 12345

# 테스트
python okmall/stock_price_synchronizer.py --limit 10 --dry-run
```

### orchestrator 옵션

```bash
# 기본 (전체 브랜드, 전체 단계)
python okmall/orchestrator.py

# 특정 브랜드만
python okmall/orchestrator.py --brand NIKE

# 특정 브랜드 제외
python okmall/orchestrator.py --exclude NIKE ADIDAS

# 특정 단계까지만 (예: PRICE까지, REGISTER 제외)
python okmall/orchestrator.py --until PRICE

# 모드 선택
python okmall/orchestrator.py --mode FULL      # 전체 수집
python okmall/orchestrator.py --mode PARTIAL   # CONVERT, IMAGE 스킵
```

---

## 데이터베이스

### 주요 테이블

| 테이블 | 역할 | 데이터 수 |
|--------|------|----------|
| `raw_scraped_data` | OkMall 원본 데이터 | ~42,000 |
| `ace_products` | 바이마용 가공 데이터 | ~40,000 |
| `ace_product_images` | 상품 이미지 | ~59,000 |
| `ace_product_options` | 색상/사이즈 옵션 | ~150,000 |
| `ace_product_variants` | 재고 조합 | ~109,000 |
| `mall_brands` | 브랜드 매핑 | 381 |
| `mall_categories` | 카테고리 매핑 | 396 |
| `buyma_master_categories_data` | 배송비 매핑 | 599 |
| `shipping_config` | 배송 설정 | 1 |
| `pipeline_batches` | 배치 실행 이력 | - |
| `pipeline_control` | 단계별 진행 상황 | - |

### 주요 컬럼 설명 (ace_products)

| 컬럼 | 의미 |
|------|------|
| `is_published` | 0: 미등록, 1: 바이마 등록 완료 |
| `buyma_product_id` | 바이마가 발급한 상품 ID |
| `model_no` | 모델 번호 (중복 방지 기준) |
| `control` | publish/draft/suspend/delete |
| `is_buyma_locked` | 바이마에서 수동 수정된 상품 (자동 업데이트 금지) |
| `buyma_lowest_price` | 경쟁사 최저가 |
| `is_lowest_price` | 내가 최저가인지 여부 |
| `margin_rate` | 마진율 (%) |
| `colorsize_comments_jp` | 사이즈/색상 코멘트 (일본어) |

---

## 파일별 상세 설명

### orchestrator.py (파이프라인 총괄)

**핵심 기능:**
- 6단계 파이프라인 자동 실행 (COLLECT → CONVERT → PRICE → TRANSLATE → IMAGE → REGISTER)
- 파이프라인 병렬 처리 (브랜드 A가 CONVERT 하는 동안 브랜드 B는 COLLECT)
- 중간에 멈춰도 재실행 시 이어서 진행
- 날짜 기반 배치 초기화 (어제 배치는 무시)

**주요 옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--exclude NIKE ADIDAS`: 특정 브랜드 제외
- `--until PRICE`: 특정 단계까지만 (COLLECT/CONVERT/PRICE/TRANSLATE/IMAGE/REGISTER)
- `--mode PARTIAL`: CONVERT, TRANSLATE, IMAGE 스킵 (재수집용)

---

### okmall_all_brands_collector.py (상품 수집)

**역할:**
- OkMall 브랜드 페이지에서 상품 목록 크롤링
- 각 상품의 상세 정보, 옵션, 재고, 실측, 혼용률 수집
- `raw_scraped_data` 테이블에 저장

**핵심 로직:**
- `--skip-existing`: 이미 `raw_scraped_data`에 있는 `mall_product_id`는 상세 페이지 접속 스킵 → OkMall 요청 최소화

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--limit 10`: 브랜드당 최대 수집 개수
- `--skip-existing`: 신규만 수집 (orchestrator에서 기본 사용)
- `--dry-run`: 테스트 모드

---

### raw_to_ace_converter.py (데이터 변환)

**역할:**
- `raw_scraped_data` → `ace_products` 변환
- 브랜드/카테고리 매핑 (mall_brands, mall_categories 참조)
- 가격 계산 (원화 → 엔화, 마진 적용)
- Gemini API로 상품명/설명 일본어 번역
- 배송비 계산, 구매 기한 설정

**주요 옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--upsert`: 이미 변환된 데이터도 업데이트 (코드 수정 후 1회만 사용)
- `--dry-run`: 테스트 모드

**중요:**
- 기본 모드: `raw_data_id`가 이미 `ace_products`에 있으면 **스킵**
- `--upsert` 모드: 기존 것도 **업데이트** (colorsize_comments, options, variants)

---

### image_collector_parallel.py (이미지 수집)

**역할:**
- W컨셉에서 상품 이미지 URL 수집
- Playwright 기반 브라우저 자동화
- 멀티프로세싱 병렬 처리 (기본 4개)
- `ace_product_images` 테이블에 저장

**특징:**
- 이미 이미지가 있는 상품은 스킵
- W컨셉 검색 결과 없으면 추천 상품 수집 안 함

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--workers 8`: 동시 처리 워커 수
- `--headless false`: 브라우저 표시 (디버깅용)

---

### r2_image_uploader.py (이미지 업로드)

**역할:**
- `ace_product_images`에서 미업로드 이미지 조회
- 원본 URL에서 다운로드
- Cloudflare R2에 업로드
- `buyma_image_path` 컬럼에 경로 저장

**특징:**
- `is_uploaded=0`인 것만 처리
- 멀티스레드 병렬 업로드 (기본 10개)

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--limit 100`: 최대 업로드 개수

---

### buyma_lowest_price_collector.py (최저가 수집)

**역할:**
- 바이마에서 `model_no` 기반으로 경쟁사 최저가 검색
- `buyma_lowest_price`, `is_lowest_price` 컬럼 업데이트
- 마진율 재계산

**특징:**
- 내 상품(BUYMA_BUYER_ID)은 제외하고 경쟁자 최저가만 수집
- 멀티스레드 병렬 처리 (기본 5개)

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--limit 100`: 최대 처리 개수
- `--dry-run`: 테스트 모드

---

### buyma_new_product_register.py (상품 등록)

**역할:**
- `ace_products`에서 신규 상품만 선별하여 바이마 API 호출
- 조건: `is_published=0`, `model_no` 있음, `model_no` 중복 아님
- 등록 성공 시 `is_published=1`, `buyma_product_id` 업데이트

**추가 기능:**
- `--clean-duplicates`: 중복 model_no 상품 삭제
- `--clean-no-model`: model_no 없는 상품 삭제

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--limit 100`: 최대 처리 개수
- `--product-id 12345`: 특정 상품만
- `--dry-run`: API 호출 없이 테스트

**중요:**
- 바이마 API는 **비동기**입니다. 등록 결과는 Webhook으로 수신됩니다.
- `is_published=1` 설정은 Webhook 수신 후 처리됩니다.

---

### stock_price_synchronizer.py (재고/가격 동기화)

**역할:**
- 바이마 등록된 상품(`is_published=1`) 전체 대상
- OkMall 실시간 재고/가격 수집
- 변동 감지 시 바이마 API 호출

**주요 로직:**

| OkMall 상태 | 조치 |
|------------|------|
| 일부 옵션 품절 | 바이마 재고 수정 API |
| 전체 옵션 품절 | 바이마 삭제 API |
| 상품 자체 삭제됨 | 바이마 삭제 API |
| 흠집 상품으로 변경 | 바이마 삭제 + DB 삭제 |
| 마진 손해 (마진<=0) | 바이마 삭제 API |
| 가격 변동 | 바이마 가격 수정 API |
| 경쟁자 없음 | 매입가 기반 20% 마진 가격 재계산 |

**최저가 수집 시 바이마 중고 상품은 제외** (product_used_tag 필터링)

**차단 방지 기능:**
- 30개 요청마다 새 세션 + 메인 페이지 방문
- 타임아웃 연속 3회 시 차단 감지 → 자동 중단
- User-Agent 로테이션
- 요청 간 랜덤 딜레이

**옵션:**
- `--brand NIKE`: 특정 브랜드만
- `--limit 100`: 최대 처리 개수
- `--product-id 12345`: 특정 상품만
- `--dry-run`: API 호출 없이 테스트

**⚠️ 가장 중요한 스크립트:** 매일 반드시 실행해야 합니다.

---

### convert_to_japanese_gemini.py (일본어 번역)

**역할:**
- Gemini API로 일본어 번역
- `raw_to_ace_converter.py`에서 import하여 사용
- 독립 실행도 가능 (미번역 상품 일괄 번역)

**특징:**
- 배치 처리 (최대 50개씩)
- `colorsize_comments_jp IS NULL`인 것만 처리

```bash
# 미번역 상품 일괄 번역
python okmall/convert_to_japanese_gemini.py
```

---

### server.py (Webhook 서버) ⚠️ 필수!

**역할:**
- 바이마 API의 비동기 결과를 수신하는 Flask 웹 서버
- 상품 등록/수정/삭제 성공/실패 이벤트를 DB에 반영

**왜 필수인가?**
```
buyma_new_product_register.py로 상품 등록
    ↓ (API 호출 후 즉시 반환, 비동기)
바이마 서버가 상품 처리
    ↓ (수초~수분 후)
바이마 → server.py로 Webhook 전송
    ↓
server.py가 DB 업데이트:
  - is_published = 1
  - buyma_product_id = 12345
  - is_buyma_locked = 1
```

**server.py가 꺼져있으면:**
- ❌ 등록했는데 `is_published=0`으로 남음
- ❌ `buyma_product_id`가 NULL로 남음
- ❌ 다음날 또 등록 시도 → 중복 등록
- ❌ 실패한 상품도 알 수 없음

**처리 이벤트:**

| 이벤트 | DB 업데이트 내용 |
|--------|------------------|
| `product/create` | `is_published=1`, `buyma_product_id` 저장, `is_buyma_locked=1` |
| `product/update` | 동일 |
| `product/fail_to_create` | `is_published=0`, `is_buyma_locked=0`, 에러 메시지 저장 |
| `product/fail_to_update` | 에러 종류에 따라 `is_active=0` 또는 재등록 대상 처리 |

**실행 방법:**
```bash
cd C:\Users\hyong\OneDrive\원블록스\buyma\okmall
python server.py
```

**로그 위치:**
```
/home/ubuntu/buyma/buyma/webhook/webhook.log
```

**포트 및 엔드포인트:**
- 기본 포트: `8000`
- Health Check: `http://127.0.0.1:8000/`
- Webhook 수신: `http://127.0.0.1:8000/webhook/buyma`

**바이마 설정에 등록할 URL:**
```
http://your-server.com:8000/webhook/buyma
```

**⚠️ 주의:**
- **24시간 상시 실행 필수**
- 서버 재시작 시 꼭 다시 실행
- Windows 서비스 또는 자동 실행 등록 권장

---

### sync_categories.py (카테고리 동기화)

**역할:**
- `raw_scraped_data`의 `category_path`를 `mall_categories`에 동기화
- 신규 카테고리 발견 시 자동 INSERT

```bash
python okmall/sync_categories.py              # 실제 실행
python okmall/sync_categories.py --dry-run    # 미리보기
```

---

## 트러블슈팅

### 1. server.py가 실행 중이 아님

**증상:**
- 상품 등록 완료 후 `is_published`가 0으로 남음
- `buyma_product_id`가 NULL
- 다음날 같은 상품이 또 등록 시도됨

**확인:**
```bash
# 서버 실행 여부 확인
curl http://127.0.0.1:8000/
# 응답이 없으면 서버가 꺼진 상태

# 로그 확인
tail -f /home/ubuntu/buyma/buyma/webhook/webhook.log
# 최근 Webhook 수신 내역 확인
```

**해결:**
```bash
cd C:\Users\hyong\OneDrive\원블록스\buyma\okmall
python server.py
```

**근본 해결:**
- Windows 서비스로 등록하여 자동 재시작
- 또는 PM2 등의 프로세스 매니저 사용 (Linux 서버)

---

### 2. OkMall 접속 차단됨

**증상:** `stock_price_synchronizer` 실행 시 "타임아웃 차단 감지" 또는 403 에러

**해결:**
```
1. 비행기 모드 ON → 5초 대기 → 비행기 모드 OFF (IP 갱신)
2. 30분~1시간 대기 후 재실행
3. 동시에 여러 스크립트 실행하지 않기
```

### 3. Gemini API 할당량 초과

**증상:** `convert_to_japanese_gemini.py` 실행 시 429 에러

**해결:**
```
1. 다음 날 재실행 (일일 할당량 리셋)
2. 배치 크기 줄이기 (코드 내 BATCH_SIZE 조정)
```

### 4. 바이마 API 오류

**증상:** `buyma_new_product_register.py` 실행 시 400/500 에러

**확인 사항:**
```
1. BUYMA_ACCESS_TOKEN 만료 여부 확인
2. 상품 데이터 검증 (model_no, category_id, brand_id)
3. api_response_json 컬럼에서 에러 메시지 확인
4. server.py가 실행 중인지 확인
```

### 5. orchestrator 중간에 멈춤

**현상:** 한 브랜드에서 에러 발생 시 해당 브랜드만 중단

**확인:**
```sql
-- 에러 발생한 브랜드/단계 확인
SELECT brand_name, stage, status, error_msg
FROM pipeline_control
WHERE status = 'ERROR'
ORDER BY updated_at DESC;

-- 배치 전체 상태 확인
SELECT * FROM pipeline_batches
ORDER BY start_time DESC
LIMIT 5;
```

**재시작:**
```bash
# 같은 배치 이어서 실행 (DONE된 것은 스킵)
python okmall/orchestrator.py

# 새 배치로 시작 (날짜가 바뀌면 자동으로 새 배치 생성)
```

### 6. 이미지 수집 실패

**증상:** W컨셉에서 이미지 못 찾음

**원인:** 상품이 W컨셉에 없거나 검색 결과 없음

**확인:**
```sql
SELECT id, name, model_no 
FROM ace_products 
WHERE is_image_uploaded = 0
LIMIT 10;
```

### 7. DB 비밀번호 오류

**증상:** `Access denied for user`

**해결:**
```
.env 파일 확인
DB_PASSWORD 값 정확한지 체크
```

### 8. Webhook이 수신되지 않음

**증상:** server.py는 실행 중인데 DB가 업데이트 안 됨

**확인:**
```bash
# Webhook 로그 확인
tail -f /home/ubuntu/buyma/buyma/webhook/webhook.log

# 바이마 설정에서 Webhook URL 확인
# http://your-server.com:8000/webhook/buyma
```

**원인:**
- 포트 포워딩 미설정
- 방화벽 차단
- 바이마에 등록된 URL이 잘못됨
- 공인 IP 변경됨

**해결:**
```bash
# 외부에서 접근 테스트
curl http://your-server.com:8000/

# 포트 포워딩 확인
# 공유기 설정에서 8000번 포트 → 내부 IP:8000
```

---

## 데이터 흐름도

```
OkMall 상품 페이지
    ↓ (COLLECT) model_id 없으면 스킵
raw_scraped_data
    ↓ (CONVERT) 번역 제외, 브랜드/카테고리 매핑만
ace_products + ace_product_options + ace_product_variants
    ↓ (PRICE) 바이마 검색 (중고 제외)
buyma_lowest_price 업데이트, 경쟁자 없으면 20% 마진 역산
    ↓ (TRANSLATE) 최저가 확보분만
Gemini API 일본어 번역
    ↓ (IMAGE) 최저가 확보분만
W컨셉 검색 → ace_product_images → R2 업로드
    ↓ (REGISTER)
바이마 API → buyma_product_id 발급, is_published=1
    ↓
[매일 실행]
stock_price_synchronizer → OkMall 재수집 → 바이마 업데이트/삭제
```

---

## 모니터링 쿼리

### 진행 상황 확인

```sql
-- 전체 통계
SELECT 
    COUNT(*) as total,
    SUM(is_published) as published,
    SUM(is_image_uploaded) as has_images,
    SUM(CASE WHEN buyma_lowest_price IS NOT NULL THEN 1 ELSE 0 END) as has_price
FROM ace_products
WHERE is_active = 1;

-- 브랜드별 등록 현황
SELECT 
    brand_name,
    COUNT(*) as total,
    SUM(is_published) as published,
    ROUND(AVG(margin_rate), 2) as avg_margin
FROM ace_products
WHERE is_active = 1
GROUP BY brand_name
ORDER BY published DESC;

-- 최근 등록된 상품
SELECT id, name, brand_name, price, margin_rate, buyma_registered_at
FROM ace_products
WHERE is_published = 1
ORDER BY buyma_registered_at DESC
LIMIT 20;
```

---

## 주의사항

### ⚠️ 필수 실행 사항

1. **server.py는 24시간 상시 실행 필수!**
   - ❌ 꺼지면: 상품 등록해도 DB에 반영 안 됨
   - ❌ 결과: `is_published=0`로 남아서 중복 등록 시도
   - ✅ 해결: Windows 서비스 등록 또는 자동 재시작 설정
   - 확인 방법: `curl http://127.0.0.1:8000/` 응답 확인

2. **stock_price_synchronizer는 반드시 매일 실행**
   - 재고 미동기화 시 쇼퍼 삭제 위험
   - 가장 중요한 스크립트 중 하나

### 🔒 보안 및 운영

3. **OkMall과 바이마는 동시 접근 금지**
   - orchestrator와 stock_price_synchronizer 실행 시간 분리
   - 권장: stock 새벽 3시, orchestrator 오전 6시

4. **환경변수는 절대 커밋 금지**
   - `.env` 파일은 `.gitignore` 처리됨
   - `buyma_cookies.json`, `get_token.py`도 커밋 금지

### ⚙️ 운영 주의사항

5. **upsert는 신중하게**
   - `raw_to_ace_converter.py --upsert`는 코드 수정 시에만 1회 실행
   - 평상시에는 사용하지 않음 (중복 처리, 속도 저하)

6. **Webhook URL 외부 접근 설정**
   - 바이마가 접근 가능한 공인 IP 또는 도메인 필요
   - 포트 포워딩 설정 필수 (외부 → 내부 8000번)
   - ngrok 같은 터널링 서비스 사용 가능

---

## 시스템 요구사항

- Python 3.9+
- MySQL 5.7+ (utf8mb4 지원)
- Windows 10/11 (PowerShell)
- Playwright Chromium
- 안정적인 네트워크 (OkMall, 바이마 접속)

---

## 서버 정보

| 항목 | 값 |
|------|-----|
| 고정 IP | 43.200.228.173 |
| 도메인 | buyma-api.oneblocks.co.kr |
| Callback URL | https://buyma-api.oneblocks.co.kr/oauth/callback |
| Webhook URL | https://buyma-api.oneblocks.co.kr/webhook/buyma |

---

## 참조 문서

- **[REFERENCE.md](REFERENCE.md)** - 바이마 API 스펙, 마진 계산, 수집 URL 패턴, DB DDL 등 기술 레퍼런스

---

## 라이센스 및 주의

본 시스템은 개인 사업용 자동화 도구입니다. OkMall, W컨셉, BUYMA의 서비스 약관을 준수하여 사용하시기 바랍니다.

- 과도한 크롤링 자제 (차단 위험)
- 저작권 있는 이미지는 적절한 권한 확보 후 사용
- API 호출 제한 준수

---

**최종 수정일: 2026-03-03**
