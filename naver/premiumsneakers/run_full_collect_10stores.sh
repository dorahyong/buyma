#!/usr/bin/env bash
# 네이버 스마트스토어 10개 본수집 (순차)
#
# 선행조건:
#   1) WARP OFF (네이버 DNS 차단 회피)
#   2) 쿠키 갱신: python naver/premiumsneakers/premiumsneakers_collector.py --login
#
# 사용법:
#   bash naver/premiumsneakers/run_full_collect_10stores.sh
#
# 중단 시: Ctrl+C → 현재 스토어 중단. 재시작 시 동일 명령으로 --skip-existing이 중복 스킵.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS=$(date '+%Y%m%d_%H%M%S')
LOG="$SCRIPT_DIR/full_collect_${TS}.log"
SUMMARY="$SCRIPT_DIR/full_collect_summary_${TS}.txt"

STORES=(maniaon bblue euroline unico kometa larlashoes thegrande upset luxlimit pano)

echo "==============================================" | tee -a "$LOG"
echo "=== 네이버 스마트스토어 10개 본수집 ===" | tee -a "$LOG"
echo "=== 시작: $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOG"
echo "=== 로그: $LOG" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"

printf "%-12s %10s %10s %s\n" "store" "start" "end" "exit" > "$SUMMARY"

for store in "${STORES[@]}"; do
  echo "" | tee -a "$LOG"
  echo "##########################################################" | tee -a "$LOG"
  echo "### [$store] 시작: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
  echo "##########################################################" | tee -a "$LOG"

  start_ts=$(date '+%H:%M:%S')

  python naver/premiumsneakers/premiumsneakers_category_collector.py \
    --source "$store" \
    --skip-existing 2>&1 | tee -a "$LOG"
  exit_code=${PIPESTATUS[0]}

  end_ts=$(date '+%H:%M:%S')

  if [ "$exit_code" -eq 0 ]; then
    echo "### [$store] 완료: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
  else
    echo "### [$store] 실패 (exit=$exit_code): $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
  fi

  printf "%-12s %10s %10s %s\n" "$store" "$start_ts" "$end_ts" "$exit_code" >> "$SUMMARY"
done

echo "" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"
echo "=== 전체 완료: $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$LOG"
echo "==============================================" | tee -a "$LOG"
echo "" | tee -a "$LOG"
echo "=== 요약 ===" | tee -a "$LOG"
cat "$SUMMARY" | tee -a "$LOG"
