@echo off
echo ====================================
echo PMS Application 빌드 시작
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

:: PyInstaller로 빌드 실행
echo.
echo 빌드 실행 중...
pyinstaller build_config.spec

:: 빌드 완료 확인
if exist "dist\PMS_Application.exe" (
    echo.
    echo ====================================
    echo 빌드 성공!
    echo 실행 파일: dist\PMS_Application.exe
    echo ====================================
    
    :: 설정 파일들을 dist 폴더에 복사
    echo 설정 파일 복사 중...
    if not exist "dist\config" mkdir "dist\config"
    copy "config\*.yml" "dist\config\" >nul
    copy "config\*.json" "dist\config\" >nul
    
    echo.
    echo 배포 준비 완료:
    echo - dist\PMS_Application.exe
    echo - dist\config\
    echo.
    
) else (
    echo.
    echo ====================================
    echo 빌드 실패!
    echo 로그를 확인해주세요.
    echo ====================================
)

pause 