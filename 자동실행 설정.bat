@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo   Market Flow - Windows 자동실행 등록
echo   (컴퓨터가 꺼져있어도 켜지면 자동 실행)
echo ========================================
echo.

set PROJ=%~dp0
set PYTHON=python

REM 아침 리포트 (매일 07:00, 이메일 포함)
schtasks /create ^
  /tn "MarketFlow\아침_브리핑" ^
  /tr "\"%PROJ%아침 리포트+이메일.bat\"" ^
  /sc daily /st 07:00 /f > nul 2>&1

if %errorlevel% equ 0 (
    echo [등록 완료] 아침 브리핑  - 매일 07:00
) else (
    echo [등록 실패] 아침 브리핑 - 관리자 권한으로 실행해 주세요
)

REM 저녁 리포트 (매일 18:30, 이메일 포함)
schtasks /create ^
  /tn "MarketFlow\저녁_결산" ^
  /tr "\"%PROJ%저녁 리포트+이메일.bat\"" ^
  /sc daily /st 18:30 /f > nul 2>&1

if %errorlevel% equ 0 (
    echo [등록 완료] 저녁 결산    - 매일 18:30
) else (
    echo [등록 실패] 저녁 결산 - 관리자 권한으로 실행해 주세요
)

echo.
echo ----------------------------------------
echo 등록된 작업 확인:
schtasks /query /fo LIST /tn "MarketFlow\아침_브리핑" 2>nul | findstr "작업 이름\|다음 실행"
schtasks /query /fo LIST /tn "MarketFlow\저녁_결산"   2>nul | findstr "작업 이름\|다음 실행"
echo ----------------------------------------
echo.
echo 등록 취소하려면 아래 명령을 실행하세요:
echo   schtasks /delete /tn "MarketFlow\아침_브리핑" /f
echo   schtasks /delete /tn "MarketFlow\저녁_결산" /f
echo.
pause
