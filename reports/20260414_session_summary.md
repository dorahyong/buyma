# 2026-04-14 작업 세션 정리

## 1. scan_store_brands.py — 네이버 12개 스토어 브랜드/카테고리 스캔 확장

여러 네이버 스토어의 네비게이션 구조가 달라서, 스토어별 config 플래그로 분기 처리하도록 대대적 리팩토링.

### 추가된 모드 (STORES dict의 각 스토어별 설정 옵션)

| 플래그 | 역할 | 적용 스토어 |
|--------|------|------------|
| `brands_at_top` | 최상위 메뉴 자체가 브랜드 (호버 없이 수집) | loutique, t1global |
| `brand_range: (start, end)` | 메뉴 순서 기준 범위 필터 | loutique(Thom Browne..Balenciaga), t1global(아디다스..컨버스) |
| `brand_prefix` | 특정 prefix로 시작하는 top만 브랜드 그룹으로 호버 | vvano(`Brand `) |
| `brand_parents: [list]` | 다중 부모 (각 top이 브랜드 그룹) | veroshopmall(`#ㄱ`~`#ㅎ`) |
| `brand_parent_prefix` | prefix 매칭 다중 부모 | joharistore(`Brand [`) |
| `category_roots: {set}` | 카테고리 root 허용 목록 (스토어별 override) | fabstyle, carpi, thefactor2, dmont |
| `category_root_parents: {set}` | 최상위 껍데기 → 자식을 root로 사용 | tutto-bene(`패션의류`, `패션잡화`) |

### 공통 유틸
- 이모지 제거: 범위 regex로 브랜드명 정제 (t1global `🌈크록스` → `크록스`)
- hover sleep: 0.6 → 0.8 (호버 타이밍 안정화)
- `--brands-only` / `--categories-only` CLI 플래그

### 실제 수집 완료
- loutique: 19 브랜드 (Thom Browne..Balenciaga)
- t1global: 15 브랜드 (아디다스..컨버스|반스)
- vvano: 49 브랜드 (Brand A..Y)
- veroshopmall: 97 한글 브랜드 (#ㄱ~#ㅎ)
- thefactor2: 35 카테고리
- dmont: 170 카테고리
- tutto-bene: 108 카테고리 (패션의류/패션잡화 껍데기 제외)
- carpi: 107 브랜드 (dry-run만, insert 미실행 여부 확인 필요)
- joharistore: 타이밍 이슈로 44 네비 수집됐으나 hasChild 이슈로 분류 불완전(아래 `NEXT_STEPS.md` 참조)

## 2. naver/premiumsneakers/premiumsneakers_collector.py — 상품 수집기 완성

네이버 스마트스토어의 SPA 봇 감지를 우회하기 위해 **Playwright 단일 경로 + XHR 가로채기** 방식으로 전면 재작성.

### 핵심 설계
- **Phase 1 (리스트)**: 브랜드별 카테고리 페이지에서 `data-shp-contents-id` 속성으로 channelProductNo 수집
- **Phase 2 (상세)**: `page.goto` → 브라우저가 자동 발사하는 두 XHR을 `page.on('response')`로 캡처
  - `GET /i/v2/channels/{channelUid}/products/{pno}?withWindow=false` → 완전한 상품 JSON
  - `POST /i/v2/channels/{channelUid}/product-benefits/{pno}` → 쿠폰 적용 최종가
- **매핑**: `map_to_row`에서 product JSON + benefits JSON → `raw_scraped_data` row

### 시행착오 기록
1. `requests` 세션 + iPhone UA 모바일 URL → 첫 요청부터 429 (쿠키 도메인 불일치)
2. Playwright headless=True → 빈 SPA shell 반환 (봇 감지) → **headless=False 필수**
3. `page.request.get` 직접 호출 → 429 (브라우저가 자동 호출하는 XHR만 허용되는 듯)
4. `page.on('response')`를 async handler로 등록 → 제대로 동작 안 함 → **sync handler로 response 객체만 저장 후 나중에 `await resp.json()`**
5. URL 매칭이 느슨해서 `/products/{pno}/review` 등 엉뚱한 XHR 캡처 → **정규식 엄격화**
6. naver `productImages` 배열에 **동일 URL 중복 포함** 케이스 → dedup 추가
7. `'보안 확인'` 단순 문자열 매치 → 정상 페이지에도 포함 → **title 기반 캡챠 감지**로 변경

### 검증된 raw_json_data 포맷 (kasina converter 호환)
```json
{
  "channel_no": "100712533",
  "brand_name": "BALENCIAGA",
  "model_name": "780561 W4SA1 1000",
  "options": [
    {"color": "", "tag_size": "EU 39-40", "option_code": "...", "status": "in_stock"}
  ],
  "images": ["https://shop-phinf.pstatic.net/..."],
  "category": "패션잡화 > 남성신발 > 부츠"
}
```

### 관련 새 디렉토리 구조
```
naver/
├── naver_cookies.json       # scan_store_brands + collector 공용
├── scan_store_brands.py
└── premiumsneakers/
    ├── premiumsneakers_collector.py
    ├── premiumsneakers_collect.md  # API/페이지 스펙 문서
    ├── premiumsneakers_*.html       # 페이지 샘플
    └── premiumsneakers_*.json       # API 응답 샘플
```

## 3. AWS 서버에서 바이마 API 스크립트 실행 세팅

사무실 IP가 buyma에 차단됐고, WARP는 smartstore.naver.com DNS를 막아서 **역할 분리**:
- 사무실 (WARP off): 네이버 수집 전용
- AWS 서버 `43.200.228.173`: 바이마 API 호출 전용 (`fast_price_updater`, `run_daily`, `run_daily_multisource`)

### AWS 세팅 완료 상태
- 경로: `~/buyma/buyma` (GitHub `dorahyong/buyma` repo)
- venv: `~/buyma/buyma/venv` (webhook과 공용)
- 패키지: requests, sqlalchemy, pymysql, python-dotenv, beautifulsoup4, lxml 전부 설치됨
- `.env`: 로컬에서 scp로 전송 완료

### 현재 실행 중 (tmux 세션)
- `price` 세션: `fast_price_updater.py` 무한루프

### 추가 커밋
- `a3dab34`: price 필터(--new-only) + gap 재조정 + trendbe 제거
- `3b920ed`: fast_price_updater API 실패 로그에 status_code + body 포함

### 발견된 이슈 및 해결
- **401 Unauthorized 반복**: `.env`의 `BUYMA_ACCESS_TOKEN`이 AWS에 예전 값이었음 → 로컬 최신 .env scp로 해결

## 4. 기타 수정 사항 (커밋 포함)
- `fast_price_updater.py`: REQUEST_DELAY 랜덤화, DEFAULT_WORKERS 3, gap>9엔이면 "경쟁자-1~9엔"으로 가격 인하
- `okmall/buyma_lowest_price_collector.py`: `--new-only` 플래그
- `okmall/orchestrator.py`: PRICE 단계에 `--new-only` 기본 적용
- `run_daily_multisource.py`: phase3 price에 `--new-only` 적용
