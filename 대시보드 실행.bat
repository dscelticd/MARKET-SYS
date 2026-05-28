@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow - 대시보드 실행
echo ========================================
echo.
echo 잠시 후 브라우저가 자동으로 열립니다.
echo 열리지 않으면 직접 접속: http://localhost:8501
echo 대시보드를 종료하려면 이 창을 닫으세요.
echo.

REM 3초 후 브라우저 강제 실행 (백그라운드)
start /b cmd /c "timeout /t 3 /nobreak > nul && start http://localhost:8501"

REM streamlit 실행 (python -m 방식 - 권한 문제 우회)
python -m streamlit run app/dashboard.py --server.headless false

pause
