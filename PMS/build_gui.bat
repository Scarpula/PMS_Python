@echo off
echo ====================================
echo PMS GUI 통합 애플리케이션 빌드 시작
echo  - GUI 인터페이스 + 백그라운드 서버
echo  - 터미널 창 + GUI 창 동시 실행
echo ====================================

:: 이전 빌드 결과물 정리
if exist "dist" (
    echo 이전 빌드 결과물 정리 중...
    rmdir /s /q "dist"
)

if exist "build" (
    echo 임시 빌드 파일 정리 중...
    rmdir /s /q "build"
)

:: 필수 의존성 확인
echo.
echo 의존성 확인 중...
python -c "import tkinter; print('tkinter: OK')" 2>nul || echo tkinter: MISSING
python -c "import pymodbus; print('pymodbus: OK')" 2>nul || echo pymodbus: MISSING
python -c "import paho.mqtt.client; print('paho-mqtt: OK')" 2>nul || echo paho-mqtt: MISSING
python -c "import yaml; print('PyYAML: OK')" 2>nul || echo PyYAML: MISSING

:: 문법 오류 체크
echo.
echo Python 문법 체크 중...
python -m py_compile pms_app/gui/main_window.py || (
    echo 문법 오류가 있습니다. 수정 후 다시 시도하세요.
    pause
    exit /b 1
)
echo 문법 체크 완료!

:: PyInstaller로 빌드 실행
echo.
echo GUI 통합 애플리케이션 빌드 실행 중...
pyinstaller build_gui_config.spec

:: 빌드 완료 확인
if exist "dist\PMS_GUI_Application.exe" (
    echo.
    echo ====================================
    echo 빌드 성공!
    echo 실행 파일: dist\PMS_GUI_Application.exe
    echo ====================================
    
    :: 설정 파일들을 dist 폴더에 복사
    echo 설정 파일 복사 중...
    if not exist "dist\config" mkdir "dist\config"
    copy "config\*.yml" "dist\config\" >nul 2>nul
    copy "config\*.json" "dist\config\" >nul 2>nul
    
    echo.
    echo 배포 준비 완료:
    echo - dist\PMS_GUI_Application.exe (GUI + 서버 통합)
    echo - dist\config\ (설정 파일들)
    echo.
    echo 💡 실행 방법:
    echo   1. dist\PMS_GUI_Application.exe 실행
    echo   2. 터미널 창에서 서버 상태 확인
    echo   3. GUI 창에서 데이터 모니터링 및 제어
    echo.
    echo ✅ 빌드 완료! 실행해보세요.
    
) else (
    echo.
    echo ====================================
    echo 빌드 실패!
    echo 로그를 확인해주세요.
    echo ====================================
)

pause 