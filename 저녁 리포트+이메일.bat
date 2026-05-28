@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow - 저녁 결산 + 이메일 발송
echo ========================================
echo.
python app/main.py --report evening --send-email
echo.
echo 완료! 리포트 생성 및 이메일 발송이 완료되었습니다.
echo.
pause
