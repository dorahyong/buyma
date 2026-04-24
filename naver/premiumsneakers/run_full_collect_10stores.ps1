# 네이버 스마트스토어 10개 본수집 (순차) — PowerShell
#
# 선행조건:
#   1) WARP OFF (네이버 DNS 차단 회피)
#   2) 쿠키 갱신: python naver/premiumsneakers/premiumsneakers_collector.py --login
#
# 사용법:
#   powershell -ExecutionPolicy Bypass -File naver/premiumsneakers/run_full_collect_10stores.ps1
#   또는 (PS 프롬프트에서):
#   .\naver\premiumsneakers\run_full_collect_10stores.ps1
#
# 중단: Ctrl+C. 재시작: 동일 명령 — --skip-existing이 중복 스킵.

$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TS = Get-Date -Format "yyyyMMdd_HHmmss"
$Log = Join-Path $ScriptDir "full_collect_${TS}.log"
$Summary = Join-Path $ScriptDir "full_collect_summary_${TS}.txt"

$Stores = @('maniaon','bblue','euroline','unico','kometa','larlashoes','thegrande','upset','luxlimit','pano')

function Write-Log($msg) {
    $line = $msg
    Write-Host $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

Write-Log "=============================================="
Write-Log "=== 네이버 스마트스토어 10개 본수집 ==="
Write-Log "=== 시작: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log "=== 로그: $Log"
Write-Log "=============================================="

"{0,-12} {1,10} {2,10} {3}" -f 'store','start','end','exit' | Out-File -FilePath $Summary -Encoding utf8

foreach ($store in $Stores) {
    Write-Log ""
    Write-Log "##########################################################"
    Write-Log "### [$store] 시작: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Log "##########################################################"

    $startTs = Get-Date -Format 'HH:mm:ss'

    # python 출력을 log 파일과 콘솔 둘 다에 씀
    & python naver/premiumsneakers/premiumsneakers_category_collector.py --source $store --skip-existing 2>&1 | Tee-Object -FilePath $Log -Append
    $exitCode = $LASTEXITCODE

    $endTs = Get-Date -Format 'HH:mm:ss'

    if ($exitCode -eq 0) {
        Write-Log "### [$store] 완료: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    } else {
        Write-Log "### [$store] 실패 (exit=$exitCode): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    }

    "{0,-12} {1,10} {2,10} {3}" -f $store, $startTs, $endTs, $exitCode | Add-Content -Path $Summary -Encoding utf8
}

Write-Log ""
Write-Log "=============================================="
Write-Log "=== 전체 완료: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ==="
Write-Log "=============================================="
Write-Log ""
Write-Log "=== 요약 ==="
Get-Content $Summary | ForEach-Object { Write-Log $_ }
