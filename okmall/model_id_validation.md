# model_id 유효성 검증 규칙

> 적용일: 2026-03-27
> 적용 파일: `okmall_all_brands_collector.py` (`_is_valid_model_id`, `extract_product_name`)

## 배경

okmall 상품명에서 model_id를 괄호 `()` 안의 텍스트로 추출하는데,
일부 브랜드에서 괄호 안에 모델번호가 아닌 색상명/사이즈/한글명이 들어있는 문제 발생.

### 문제 사례

| p_name_full | 기존 model_id (잘못됨) | 올바른 model_id |
|---|---|---|
| `Twill Short Pants (M) - Black (5745182063-010)` | `M` (사이즈) | `5745182063-010` |
| `DIXON-BX (BLACK-GOLD) (딕슨-BX)` | `BLACK-GOLD` (색상) | 없음 |
| `폴리오/로프백 (블루) (CB-SAC-FB)` | `블루` (한글 색상) | `CB-SAC-FB` |
| `STUSSY WORLD TOUR CREW` | `-` (하이픈) | 없음 |

### 영향 범위 (수정 전)

| 수집처 | is_active=0 | 유형 |
|--------|----------:|------|
| okmall | 583건 | 색상명, 한글, 3자 이하 |
| kasina | 96건 | 3자 이하 (STUSSY `-`) |
| 합계 | 679건 | |

---

## 수정 내용

### 1. 추출 방식 변경

**기존**: 첫 번째 괄호만 사용
```python
model_match = re.search(r'\(([^)]+)\)', prd_name_text)
```

**변경**: 모든 괄호를 검사하여 유효한 첫 번째 것 사용
```python
all_matches = re.findall(r'\(([^)]+)\)', prd_name_text)
for candidate in all_matches:
    if _is_valid_model_id(candidate):
        model_id = candidate.strip()
        break
```

### 2. 유효성 검증 규칙 (`_is_valid_model_id`)

하나라도 해당하면 **탈락** (유효하지 않음):

| # | 규칙 | 예시 (탈락) |
|---|------|-----------|
| 1 | 3자 이하 | `M`, `PS`, `-`, `088` |
| 2 | 한글 포함 | `블루`, `노다 001A`, `왼쪽지퍼` |
| 3-1 | 전부 색상 단어 | `BLACK`, `NAVY`, `OLIVE` |
| 3-3 | 색상 제거 후 3자 이하 | `BLACK-GOLD`→`""`, `558 BLACK`→`558`, `Fig Green`→`FIG` |

### 3. 색상 단어 목록

```
BLACK, WHITE, NAVY, GREY, GRAY, RED, BLUE, GREEN, BROWN,
BEIGE, PINK, CREAM, KHAKI, ORANGE, YELLOW, IVORY, CAMEL,
CHARCOAL, SILVER, GOLD, BURGUNDY, OLIVE, TAN, SAND, NATURAL,
DARK, LIGHT, MOSS, INK, FOG
```

### 4. Fallback: 깨진 괄호

모든 괄호에서 유효한 model_id를 못 찾으면, 괄호 안에 중첩된 괄호에서 추출 시도.

예: `(Dioriviera Walk'n'Dior 플랫폼 스니커즈 (KCK385TJE 68H)` → `KCK385TJE 68H`

---

## 검증 결과

- is_active=1 (정상) 45,964건: **기존과 결과 동일 (변화 0건)**
- is_active=0 (문제) 679건: **289건 복구 가능, 나머지는 원본에 model_id 없음**

## 통과 예시

| model_id | 결과 | 이유 |
|---|---|---|
| `DM3977-010-BLACK` | 통과 | 색상 1개, 제거 후 `DM3977010` 7자 |
| `PXAWV F61404 CAD` | 통과 | 색상 0개 |
| `UGT-102889 LIGHT ARCHIVAL WHITE` | 통과 | 색상 제거 후 `UGT102889ARCHIVAL` 15자 |
| `8C00032 829H8 001` | 통과 | 색상 0개 |
