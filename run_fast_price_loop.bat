@echo off
chcp 65001 >nul
cd /d "%~dp0"
:loop
echo fast_price_updater start
echo (파일 로그: logs\fast_price_YYYYMMDD.log — 스킵 제외, 콘솔은 전부)
python -u fast_price_updater.py
echo fast_price_updater done, restarting...
goto loop
