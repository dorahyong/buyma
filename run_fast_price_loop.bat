@echo off
chcp 65001 >nul
cd /d "%~dp0"
:loop
echo fast_price_updater start
python fast_price_updater.py
echo fast_price_updater done, restarting...
goto loop
