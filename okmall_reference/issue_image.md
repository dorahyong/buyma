# 이미지 이슈 정리

작성일: 2026-03-16

---

## 문제 1: wconcept/trenbe에서 이미지 수집 실패가 너무 많음

현재 파이프라인(IMAGE 단계)에서 `image_collector_parallel.py`가 wconcept/trenbe에서 model_no로 검색하여 이미지를 수집하는데, 수집되지 않는 상품이 많음.

---

## 문제 2: 같은 model_no 색상별 상품의 이미지 중복

### 현상
- okmall에서 같은 model_id인데 색상이 다른 상품이 별도 행으로 수집됨 (mall_product_id 다름, model_id 같음)
- okmall 자체에는 색상별 고유 이미지가 각 상품 페이지에 존재
- 그런데 image_collector가 wconcept에서 **model_no로 검색**하기 때문에, 같은 model_no인 상품들은 **전부 같은 이미지**가 수집됨
- 바이마에 등록 시: 상세 내용(색상 등)은 다르지만 모델명 동일 + 이미지까지 동일 → **중복 상품처럼 보임**

### 영향 범위
- 바이마 등록 okmall 상품: **15,374건**
- 같은 model_no로 2건 이상 등록된 모델: **15개** (총 35건)

### 해당 model_no 목록
```
QV357, A25SS03TN, QV362, A24SS02FW, A25SS02FD, Beluga,
FKC687A Y1029 415, HAT50315, HAT52204, OT200, OT235,
PU274, QV145, QV197, XXW54C0JP30UMO B607
```
- NEEDLES 8개, AURALEE 3개, HELEN KAMINSKI 2개, Rab/Thom Browne/TOD'S 각 1개
- 대부분 색상 변형이 원인

### 가격은 문제없음
- buyma_lowest_price_collector가 같은 model_no로 검색 → 3개 다 같은 최저가 수집
- 판매가는 비슷하지만, 원가(okmall 색상별 가격)가 달라서 마진만 다름
- 예: QV362 — Black(마진 20.1%), White(24.5%), Dk.Purple(28.1%)

---

## 해결 방안 검토

### 방안 1: okmall 자체 이미지 + 워터마크 제거

okmall 이미지 특징:
- URL 패턴: `okimg.okmall.com/{숫자}/img{숫자}_big.JPG`
- "OKmall" 반투명 텍스트 워터마크 — 위치(중앙), 텍스트, 투명도 모두 고정
- 크기: 590x684 (세로형, ratio 0.863)

비상품 이미지 필터링 (제외 대상):
- `other` 포함 URL → 세탁 라벨, 디테일 사진 등
- `okst.okmall.com` 도메인 → 사이즈 가이드

워터마크 제거 테스트 결과 (OpenCV inpainting):
- 어두운 옷 (검정, 카키): 거의 안 보여서 제거 불필요 수준
- 밝은 옷/흰 배경 (흰 셔츠, 스니커즈): 깔끔하게 제거됨
- **중간톤 + 텍스처 (회색 리브 탱크탑 등): 네모난 자국 남음 — 실패**

**결론: OpenCV 기본 inpainting으로는 품질 불안정. AI 모델(lama-cleaner 등) 필요.**

### 방안 2: AI 이미지 생성 (Gemini img2img)

Gemini `gemini-2.5-flash-image` 모델로 테스트:

| 테스트 | 입력 | 결과 |
|---|---|---|
| 정면 유지 + 모델 변경 (NEEDLES 티셔츠) | 사람 있는 정면 사진 | 성공 — 정면 유지, 서양인 모델로 변경, 워터마크 없음 |
| 누끼 → 모델 추가 (Stone Island 니트) | 사람 없는 사진 | 성공 — 모델 착용 사진으로 변환, 뱃지 유지 |
| 측면 앵글 변경 (NEEDLES 티셔츠) | 사람 있는 정면 사진 | 성공 — 3/4 앵글, 워터마크 없음 |
| 신발 착용 (VERSACE 스니커즈) | 누끼 사진 | 성공 — 착용 사진으로 변환 |

**장점**: 워터마크 문제 자체 없음, 색상별 각각 다른 이미지 생성 가능
**단점**: 제품 디테일이 달라질 수 있음 (로고 모양, 패턴 배치, 텍스트 왜곡 등) → 클레임 위험

### 방안 3: Google Lens 역이미지 검색으로 다른 사이트 이미지 수집

아이디어: okmall 이미지로 Google Lens 검색 → 같은 상품의 워터마크 없는 이미지 수집

**장점**: 실제 상품 사진이라 디테일 정확
**단점**:
- Google Lens 공식 API 없음 (SerpAPI 유료 월 $50+)
- 봇 차단 심함 (프로그래밍 검색 실패 확인됨)
- "같은 상품" 자동 판단 로직 복잡
- 속도 느림 + rate limit

### 방안 4: 해외 편집샵 직접 검색 (SSENSE, Farfetch, END 등)

model_no로 해외 사이트 검색 테스트 결과:
- 6개 사이트 x 10개 model_no → **히트율 0%**
- 전부 봇 차단 (403, 타임아웃)
- Google 이미지 검색도 봇 차단

**결론: 프로그래밍 방식으로 불가**

---

## 참고: yiangb 네이버 스마트스토어 사례

`https://smartstore.naver.com/yiangb` — okmall 전 상품이 동일 상품명으로 등록된 스토어.
이미지는 okmall 이미지가 아닌 **AI 생성 이미지**로 추정 (Google 역이미지 검색 시 같은 제품 안 나옴).
자체 워터마크 "Rhia"를 우측 하단에 추가.

---

## 현실적 우선순위 (제안)

1. **okmall 자체 이미지 활용 + 워터마크 제거 개선** — 정확도 최고, AI 모델(lama-cleaner) 도입 검토
2. **AI 이미지 생성 (Gemini)** — 워터마크 문제 없지만 디테일 리스크, 보조 수단으로 활용
3. **병행 전략** — 워터마크 제거 가능한 이미지는 okmall 원본 사용, 불가능한 경우 AI 생성으로 대체
