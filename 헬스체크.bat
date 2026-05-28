@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow -- 시스템 헬스체크
echo ========================================
echo.
python app/healthcheck.py
echo.
pause
