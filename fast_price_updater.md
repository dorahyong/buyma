# 빠른 최저가 업데이트 (fast_price_updater.py)

## 한 줄
STOCK과 같은 최저가 확보·출품정지를, **소싱처 방문 없이** DB 소싱값 + 바이마 시세만으로 상시 수행한다.

## 고유 / 공유
| | fast | STOCK |
|--|------|--------|
| 소싱처 방문 | X | O |
| 경쟁자 없을 때 가격 인상 | X (안 함) | O (30% 목표) |
| 적응형 스케줄 | O (`buyma_competitor_prices` × `group_key`) | 없음 |
| 판매가·winner·옵션·edit/retire | **`reconcile_runner.process_one_group` 공유** | 동일 |

## 처리 단위
- `buyma_listings` 출품중 (`is_published=1`, `buyma_product_id` 있음, `exception_reason` 없음)
- 스케줄 키: `group_key`
- 시세 숫자: `canonicalize(model_no)` 행 (`save_competitor_price` — STOCK과 공유)

## 케이스
- 경쟁자 없음 → 스킵(인상 안 함), 주기 늘림
- 이미 최저(밴드 내) → 스킵, 주기 늘림
- gap/뺏김 → `process_one_group(scope=published)` → EDIT 또는 retire
- 조회 오류 → 30분 후 재시도

## 실행
```bat
run_fast_price_loop.bat
```
```bash
python fast_price_updater.py
python fast_price_updater.py --dry-run --limit 20
python fast_price_updater.py --id 123
python fast_price_updater.py --count --limit 100
```

## 로그·운영 파라미터 (구버전 동작 유지)
- 콘솔: 전부 / 파일: `logs/fast_price_YYYYMMDD.log` (`[스킵]` 제외)
- 주기: min 2 · max 360 · 유지×2 · 뺏김÷2 · next_check ±15% jitter
- 레이트 4/초 · 배치 400 · 워커 3 · 검색딜레이 0.1~0.3s · API후 0.1s
