@echo off
echo ====================================
echo PMS GUI DCDC 비활성화 빌드
echo ====================================

echo DCDC 맵 파일 임시 백업 중...
if exist "config\dcdc_map.json" (
    ren "config\dcdc_map.json" "dcdc_map.json.backup"
    echo ✅ DCDC 맵 파일 백업 완료
) else (
    echo ⚠️ DCDC 맵 파일이 이미 없습니다
)

echo.
echo 빌드 실행 중...
powershell -ExecutionPolicy Bypass -File build_gui_fixed.ps1

echo.
echo DCDC 맵 파일 복원 중...
if exist "config\dcdc_map.json.backup" (
    ren "config\dcdc_map.json.backup" "dcdc_map.json"
    echo ✅ DCDC 맵 파일 복원 완료
)

echo.
echo ====================================
echo ✅ DCDC 비활성화 빌드 완료!
echo    실행 파일에서 DCDC가 비활성화됩니다.
echo ====================================
pause 