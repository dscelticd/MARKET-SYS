@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow - 자동 스케줄러 실행
echo ========================================
echo.
echo  아침 07:00 - 브리핑 리포트 + 이메일 자동 발송
echo  저녁 18:30 - 결산 리포트 + 이메일 자동 발송
echo.
echo  이 창을 열어두면 매일 자동으로 실행됩니다.
echo  종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo.
python app/scheduler.py
pause
