# luvgrande·bobu 일일 자동화 편입 — 검증 보고 (2026-07-27)

## 결론

`run_daily_unified.py`에 두 몰을 추가하고 `--plan`으로 검증 완료. **커밋·push는 안 했음.**
내일 오전에 아래 대조표만 확인하고 커밋/push 하면 됨.

---

## 1. 무엇을 수정했나

`run_daily_unified.py` 3곳 (아직 커밋 안 됨 — `git diff`로 확인 가능)

| 위치 | 변경 |
|---|---|
| `NAVER` | `'luvgrande', 'bobu'` 추가 (22 → 24몰) |
| `NAVER_SPLIT` | bobu → PC1, luvgrande → PC2 |
| `NAVER_COLLECTOR` | 둘 다 `_NV_CATEGORY` (전체상품 수집기) |

**PC 배정 근거**: PC3가 trendmecca(raw 8,263)·carpi·larlashoes로 가장 무거워서 제외.
PC1이 가장 가벼워 큰 쪽(bobu 1,285)을, PC2에 작은 쪽(luvgrande 203)을 배정.
→ 결과적으로 8 / 8 / 8 로 균등. 필요하면 조정 가능.

**이미 커밋된 것** (`4b95ac5`): 수집기 URL·상품명 패턴, 재고동기화 2개 파일.

---

## 2. 검증 — 오늘 수동 실행 vs `--plan` 출력

두 몰 모두 오늘 전 구간을 수동으로 돌려 정상 동작을 확인했고,
`--plan`이 출력한 명령이 그것과 일치하는지 대조함. **전부 일치.**

```
python3 run_daily_unified.py --only luvgrande --plan
python3 run_daily_unified.py --only bobu     --plan
```

| 단계 | `--plan` 출력 (luvgrande 기준) | 오늘 수동 실행 |
|---|---|---|
| COLLECT | `premiumsneakers_category_collector.py --source luvgrande --skip-existing` | 일치 |
| CONVERT | `raw_to_converter_kasina.py --source-site luvgrande --skip-translation` | 일치 |
| PRICE | `buyma_lowest_price_collector.py --source luvgrande --new-only` | 일치 |
| TRANSLATE | `convert_to_japanese_gemini.py --source luvgrande --price-checked-only` | 일치 |
| IMAGE | `r2_image_uploader.py --source luvgrande` | 일치 |
| THUMBNAIL | `thumbnail_generator.py --source luvgrande` | (수동 미실행) |
| REGISTER | `reconcile_runner.py --mode auto --scope new --source luvgrande --limit 100000 --execute --confirm-live` | 일치 |
| STOCK | `stock_price_synchronizer_naver_merge.py --source luvgrande` | 일치 |

- `--limit 100000` 확인함. (reconcile_runner 기본 limit=3 이라 안 주면 3건만 등록되는 함정)
- STOCK이 `_merge` 버전인 것 확인함. reconcile(buyma_listings) 경로와 일치.

**기타 확인**
- PC 배정: PC1 8몰(bobu 포함) / PC2 8몰(luvgrande 포함) / PC3 8몰
- 전체 몰 수: 33 → **35**
- 문법 검사 통과

---

## 3. 수동 실행 결과 (검증 근거)

| | luvgrande | bobu |
|---|---|---|
| raw 수집 | 203 | 1,285 |
| ace 변환 | 188 | 1,283 |
| 최저가 조사 | 148 | 1,249 |
| listing | 131 | 934 |
| 바이마 등록 | 46 | 234 |
| 게시중 | 43 | 183 |

---

## 4. 내일 할 일

1. `git diff run_daily_unified.py` 로 변경 확인
2. 커밋
3. push → **main push 시 EC2 자동 배포(1~2분)**

---

## 5. 이번에 넣지 않은 것 — 담당자 공유 필요

### dedup (`okmall/dedup_corrector.py`, `_merge.py`) — 넣으면 안 됨

품번 추출 결함으로 색상코드가 model_no 자리에 들어간 상품이 있고,
이 값들이 이미 여러 몰에 퍼져 있음:

```
V0029 : 7개 몰 69건 (luvgrande 22, thefactor2 15, fabstyle 14, thegrande 10 ...)
F0002 : 7개 몰 62건 (luxlimit 23, bobu 12, thefactor2 11 ...)
T8013 : 6개 몰 32건
```

dedup은 브랜드+품번이 같으면 같은 상품으로 묶으므로, **서로 무관한 수십 개 상품이
한 그룹이 되어 대표 1개만 남고 나머지가 중복 처리됨.** 품번 교정 후로 미뤄야 함.
(이미 편입된 몰들끼리는 지금도 발생 중일 가능성 있음 — 별건 확인 필요)

### `run_daily_naver.py` — 편입 불필요

구세대 경로(`buyma_new_product_register.py`, 비-merge 재고동기화)를 부름.
오늘 검증한 것은 신세대(reconcile + _merge) 경로이므로 검증 범위 밖.

### 네이버 model_id 추출 결함 (별건)

네이버 25몰 raw 454,428건 중 **377건**이 색상코드·치수·제품애칭을 품번으로 저장.
공통 함수 `is_valid_model_id()` 문제라 단독 수정 보류하기로 함(2026-07-27).
보강 시 367건 복구 / shinsegae 10건 유실 예상.
