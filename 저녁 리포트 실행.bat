@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow - 저녁 결산 리포트 생성
echo ========================================
echo.
python app/main.py --report evening
echo.
echo 완료! 리포트가 data\reports 폴더에 저장되었습니다.
echo.
pause
