@echo off
chcp 65001 > nul
echo ============================================================
echo adidas 브랜드 처리 시작
echo ============================================================

echo [1/5] 상품 수집 중...
python okmall_all_brands_collector.py --brand=adidas
if %errorlevel% neq 0 (
    echo 상품 수집 실패!
    pause
    exit /b 1
)

echo [2/5] ACE 변환 중...
python raw_to_ace_converter.py --brand=adidas
if %errorlevel% neq 0 (
    echo ACE 변환 실패!
    pause
    exit /b 1
)

echo [3/5] 이미지 수집 + 최저가 수집 동시 시작...
start "최저가수집" cmd /c "python buyma_lowest_price_collector.py --brand="adidas(アディダス)" && echo 최저가 수집 완료"
python image_collector_parallel.py --brand="adidas(アディダス)"
if %errorlevel% neq 0 (
    echo 이미지 수집 실패!
    pause
    exit /b 1
)

echo [4/5] R2 이미지 업로드 중...
python r2_image_uploader.py --brand="adidas(アディダス)"
if %errorlevel% neq 0 (
    echo R2 업로드 실패!
    pause
    exit /b 1
)

echo ============================================================
echo adidas 브랜드 처리 완료!
echo ============================================================
pause
