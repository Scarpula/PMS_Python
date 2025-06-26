@echo off
echo ====================================
echo PMS GUI 일반 빌드 (모든 장비 포함)
echo ====================================

echo 빌드 실행 중...
powershell -ExecutionPolicy Bypass -File build_gui_fixed.ps1

echo.
echo 빌드 완료!
pause 