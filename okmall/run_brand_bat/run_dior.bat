@echo off
chcp 65001 > nul
echo ============================================================
echo Dior 브랜드 처리 시작
echo ============================================================

echo [1/2] R2 이미지 업로드 중...
python r2_image_uploader.py --brand="Dior(ディオール)"
if %errorlevel% neq 0 (
    echo R2 업로드 실패!
    pause
    exit /b 1
)

echo [2/2] BUYMA 상품 등록 중...
python buyma_product_register.py --brand="Dior(ディオール)"
if %errorlevel% neq 0 (
    echo 상품 등록 실패!
    pause
    exit /b 1
)

echo ============================================================
echo Dior 브랜드 처리 완료!
echo ============================================================
pause
