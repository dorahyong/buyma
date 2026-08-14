@echo off
REM draft 점수 일배치 (셀 집계 → 채점)
REM 작업 스케줄러에서 매일 실행하거나, 수동: run_daily_draft.bat
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
python -u scoring\run_daily_draft.py --execute
exit /b %ERRORLEVEL%
