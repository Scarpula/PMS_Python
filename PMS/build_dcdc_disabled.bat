@echo off
echo ====================================
echo PMS GUI DCDC 비활성화 빌드
echo ====================================

echo.
echo DCDC 비활성화 모드로 빌드 실행 중...
powershell -ExecutionPolicy Bypass -File build_gui_fixed.ps1 -DisableDCDC

echo.
echo ====================================
echo ✅ DCDC 비활성화 빌드 완료!
echo    실행 파일에서 DCDC가 비활성화됩니다.
echo ====================================
pause 