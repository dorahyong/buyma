@echo off
chcp 65001 >nul
cd /d "C:\Users\hyong\OneDrive\원블록스\buyma"
:loop
echo fast_price_updater start
python fast_price_updater.py
echo fast_price_updater done, restarting...
goto loop
