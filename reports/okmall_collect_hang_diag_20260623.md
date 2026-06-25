# okmall 신규수집 정지 진단 (2026-06-23)

## ⭐ 한 줄 요약 (원인 확정 + 수정 완료)
okmall 신규 상품 수집이 **5/20부터 정지**. **확정 원인 = DB 연결 끊김**: 첫 "큰 브랜드"(A.P.C, 수집에 10분+)가 도는 동안 orchestrator의 **DB 연결 1개가 유휴 타임아웃으로 끊김** → A.P.C 완료(DONE) 기록 실패(=RUNNING 박제) → 이후 모든 브랜드가 죽은 연결로 줄줄이 실패 → 크래시. 그래서 매일 **앞 3개 브랜드만** 돌고 신규 0건.
**수정 완료**: `okmall/orchestrator.py` DB 접근부에 `ping(reconnect=True)`(끊긴 연결 자동 복구) 추가.

> ※ 초기엔 "A.P.C hang 버그"로 추정했으나, A.P.C 단독 실행은 13분 만에 정상 완료(exit 0) → **정정**. 실제 orchestrator 실행으로 "A.P.C 후 전 브랜드 `(0,'')` 연결끊김 에러" 확인 → 진짜 원인은 **연결 유휴 타임아웃**.

---

## 📣 비개발자용 보고 설명 (그대로 전달 가능)

**무슨 일이 있었나요?**
okmall에서 새 상품을 가져오는 자동수집이 **약 한 달 전(5월 20일)부터 멈춰** 있었습니다. 그동안 okmall의 **새 상품이 바이마에 거의 등록되지 못했습니다.** (기존 상품의 재고·가격 갱신은 정상이라, 영향은 "신규 등록"에 한정됩니다.)

**왜 멈췄나요? (비유)**
수집기는 브랜드를 **한 줄로 세워 하나씩** 처리합니다. 마치 **계산대가 하나뿐인 마트** 같아요. 그런데 okmall은 브랜드가 **353개**라, 한 바퀴 다 도는 데 **수 시간~수십 시간**이 걸립니다.
현실적으로 그렇게 오래 켜둘 수 없으니, **매번 앞쪽 몇 개 브랜드만 처리되고 중단**됩니다. 그래서 **맨 앞 3개 브랜드(ALYX, 7MESH, A BATHING APE)만** 반복 처리되고, 그 뒤 350개 브랜드는 한 달째 손도 못 댄 것입니다. (A.P.C는 4번째이자 첫 "큰 브랜드"라, 매번 그 근처에서 멈춰 보였습니다.)

**핵심 원인 (확정)**
- ❌ "특정 브랜드(A.P.C) 고장" 아님 (A.P.C 단독 13분 정상 완료).
- ✅ **DB 연결 끊김**: 수집기는 브랜드를 **하나씩** 처리하는데, 첫 큰 브랜드(A.P.C)가 **10분 넘게** 걸리는 동안 프로그램의 DB 연결이 **유휴 상태로 방치돼 끊깁니다.** 그 직후 "A.P.C 완료" 기록을 못 써서 멈춘 것처럼 보이고(그래서 매번 A.P.C에서 멈춤), 뒤 브랜드도 전부 실패합니다.

**어떻게 고쳤나요?**
- okmall 수집 프로그램이 **DB에 쓸 때마다 연결이 살아있는지 확인하고, 끊겼으면 자동으로 다시 연결**하도록 수정했습니다. → 큰 브랜드가 오래 걸려도 더 이상 멈추지 않습니다. (즉시 적용 가능)
- 근본적으로는 **통합 자동화 시스템**으로 옮기면(전체 브랜드 한 번에 수집 + 이어서 진행 + 다른 몰과 동시) 이 구조 자체가 사라집니다.

**현황 요약**
- 최근 30일간 okmall 353개 브랜드 중 **실제 수집된 건 3개뿐**, 신규 등록 ~0건.
- 다른 몰(kasina 등)은 정상.

---

## 🔍 증거 (DB 데이터, 2026-06-23 확인)

1. **okmall 신규 raw 적재: 5/20 이후 0건** (마지막 신규 5/20 18:21). 오늘: 신규 0 / 갱신 301 (= 수집기는 돌지만 기존 상품만, 새 상품 0).
2. **최근 30일간 실제 수집된 브랜드 = 단 3개** (okmall 활성 353개 중). 356개 브랜드 30일+ 미갱신.
3. **모든 최근 배치가 동일 패턴** (pipeline_control): DONE = 앞 3개(ALYX/7MESH/A BATHING APE), RUNNING(미완) = A.P.C.

   | 배치 | COLLECT 완료(DONE) | 미완(RUNNING) |
   |---|---|---|
   | 06-23 | ALYX, A BATHING APE, 7MESH | A.P.C |
   | 06-19 | ALYX, 7MESH, A BATHING APE | A.P.C |
   | 06-18 | ALYX, A BATHING APE, 7MESH | A.P.C, Acne Studios |
   | 06-17 | ALYX, 7MESH | A.P.C |
   | 06-16 | ALYX, 7MESH, A BATHING APE | A.P.C |

## 🧪 재현 (A.P.C 직접 실행)

**(1) A.P.C 단독 실행** `okmall_all_brands_collector.py --brand "A.P.C" --skip-existing`:
- 결과: 280개 전부 추출 완료 → exit 0, 약 13분. → **A.P.C 자체는 정상** ("hang 버그" 추정 정정).

**(2) 실제 orchestrator 실행** `orchestrator.py --source okmall --until COLLECT` (COLLECT만, BUYMA 부작용 없음):
- 앞 3개 스킵 → A.P.C COLLECT 정상 완료(~9분) → **직후 나머지 ~349개 브랜드 전부가 같은 순간 즉시 실패**:
  ```
  ✗ [zamberlan] 예외 발생: (0, '')
  오케스트레이터 치명적 오류: (0, '')
  ```
- `(0, '')` = pymysql **연결 끊김** 에러. → **확정 원인: A.P.C 수집(10분+) 동안 DB 연결이 유휴 타임아웃으로 끊겼고, A.P.C 완료 기록(update_stage_status)부터 죽은 연결로 실패 → A.P.C가 RUNNING 박제 + 이후 전 브랜드 연쇄 실패 → 크래시.**

**(3) 수정 검증** (연결 죽인 뒤 ping 복구 테스트):
- 죽은 연결 쿼리 → InterfaceError → `ping(reconnect=True)` → 재연결 → 쿼리 성공. **수정 유효 확인.**

## ⚙️ 메커니즘 (기술, 확정)

- okmall: `run_daily.py --source okmall` → `orchestrator.py`가 **DB 연결 1개(`self.conn`)를 시작에 열고 끝까지 보유**.
- COLLECT 는 브랜드별 subprocess(`okmall_all_brands_collector.py --brand X --skip-existing`)로 실행. 이 subprocess 가 도는 동안 `self.conn` 은 **유휴**.
- 앞 3개 브랜드는 빨리 끝나 연결 생존. **A.P.C(첫 큰 브랜드, 10분+)** 가 MySQL/MariaDB `wait_timeout`(통상 600초)을 넘김 → **연결 끊김**.
- A.P.C subprocess 종료 후 `update_stage_status(A.P.C, DONE)`가 죽은 `self.conn` 으로 실패 → **A.P.C 가 RUNNING 으로 남음**(매 배치 동일 증상의 정체).
- 이후 모든 브랜드의 `get_stage_status`/`update_stage_status` 가 죽은 연결로 즉시 실패 → orchestrator 크래시(과거 로그의 exit 3221225786 = 사용자가 멈춘 경우, 이번 재현 = `(0,'')` 연결끊김).

## 🛠 해결

1. ✅ **즉시 수정(적용 완료)**: `okmall/orchestrator.py` 의 DB 접근부 6곳에 `self.conn.ping(reconnect=True)` 추가(끊긴 연결 자동 재연결). 핵심 = `get_stage_status`·`update_stage_status`. → A.P.C 가 오래 걸려도 연결 복구되어 DONE 기록·다음 브랜드 진행 정상. (통합 전환 전까지 `run_daily.py --source okmall` 그대로 사용 가능)
2. **근본(통합)**: `run_daily_unified.py`(통합 엔진)는 애초에 **DB 작업마다 새 연결**(`_db()`)이라 이 문제 구조적으로 없음 + okmall 전체를 한 번에 수집 + resume + 병렬.

## 비고
- 본 진단은 코드/DB 재검증 기반. okmall 운영은 사용자가 별도 관리(`run_daily.py --source okmall`).
- 재현 로그: `reports/apc_hang_diag_20260623.log`.
