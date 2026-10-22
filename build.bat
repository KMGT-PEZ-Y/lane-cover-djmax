@echo off
REM 더블클릭으로 빌드한다. 결과: dist\CustomLaneCover.exe
REM 폴더 형태로 만들려면:  build.bat --onedir
cd /d "%~dp0"

python build.py %*
if errorlevel 1 (
    echo.
    echo 빌드에 실패했습니다. 위 메시지를 확인하세요.
    pause
    exit /b 1
)

echo.
pause
