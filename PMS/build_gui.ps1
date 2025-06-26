# PMS GUI 통합 애플리케이션 빌드 스크립트 (PowerShell용)
# 사용법: 
#   .\build_gui.ps1                    # 모든 장비 포함
#   .\build_gui.ps1 -DisableDCDC       # DCDC 비활성화

param(
    [switch]$DisableDCDC = $false
)

Write-Host "====================================" -ForegroundColor Green
Write-Host "PMS GUI 통합 애플리케이션 빌드 시작" -ForegroundColor Green
Write-Host " - GUI 인터페이스 + 백그라운드 서버" -ForegroundColor Yellow
Write-Host " - 터미널 창 + GUI 창 동시 실행" -ForegroundColor Yellow
if ($DisableDCDC) {
    Write-Host " - DCDC 장비 비활성화 모드" -ForegroundColor Red
}
Write-Host "====================================" -ForegroundColor Green

# 이전 빌드 결과물 정리
if (Test-Path "dist") {
    Write-Host "이전 빌드 결과물 정리 중..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "dist"
}

if (Test-Path "build") {
    Write-Host "임시 빌드 파일 정리 중..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "build"
}

# 필수 의존성 확인
Write-Host "`n의존성 확인 중..." -ForegroundColor Cyan
try {
    python -c "import tkinter; print('tkinter: OK')" 2>$null
    Write-Host "✅ tkinter: OK" -ForegroundColor Green
} catch {
    Write-Host "❌ tkinter: MISSING" -ForegroundColor Red
}

try {
    python -c "import pymodbus; print('pymodbus: OK')" 2>$null
    Write-Host "✅ pymodbus: OK" -ForegroundColor Green
} catch {
    Write-Host "❌ pymodbus: MISSING" -ForegroundColor Red
}

try {
    python -c "import paho.mqtt.client; print('paho-mqtt: OK')" 2>$null
    Write-Host "✅ paho-mqtt: OK" -ForegroundColor Green
} catch {
    Write-Host "❌ paho-mqtt: MISSING" -ForegroundColor Red
}

try {
    python -c "import yaml; print('PyYAML: OK')" 2>$null
    Write-Host "✅ PyYAML: OK" -ForegroundColor Green
} catch {
    Write-Host "❌ PyYAML: MISSING" -ForegroundColor Red
}

# 문법 오류 체크
Write-Host "`nPython 문법 체크 중..." -ForegroundColor Cyan
$syntaxCheck = python -m py_compile pms_app/gui/main_window.py 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 문법 오류가 있습니다:" -ForegroundColor Red
    Write-Host $syntaxCheck -ForegroundColor Red
    Write-Host "수정 후 다시 시도하세요." -ForegroundColor Red
    pause
    exit 1
}
Write-Host "✅ 문법 체크 완료!" -ForegroundColor Green

# PyInstaller로 빌드 실행
Write-Host "`nGUI 통합 애플리케이션 빌드 실행 중..." -ForegroundColor Cyan
pyinstaller build_gui_config.spec

# 빌드 완료 확인
if (Test-Path "dist\PMS_GUI_Application.exe") {
    Write-Host "`n====================================" -ForegroundColor Green
    Write-Host "빌드 성공!" -ForegroundColor Green
    Write-Host "실행 파일: dist\PMS_GUI_Application.exe" -ForegroundColor Green
    Write-Host "====================================" -ForegroundColor Green
    
    # 설정 파일들을 dist 폴더에 복사
    Write-Host "설정 파일 복사 중..." -ForegroundColor Yellow
    if (!(Test-Path "dist\config")) {
        New-Item -ItemType Directory -Path "dist\config" | Out-Null
    }
    
    # DCDC 비활성화 모드 (명령줄 매개변수에서 설정됨)
    
    # 기본 설정 파일 복사
    Copy-Item "config\*.yml" "dist\config\" -ErrorAction SilentlyContinue
    
    # 장비별 맵 파일 복사 (DCDC 제외 옵션 적용)
    if ($DisableDCDC) {
        Write-Host "⚠️ DCDC 비활성화 모드: dcdc_map.json 제외" -ForegroundColor Yellow
        Copy-Item "config\bms_map.json" "dist\config\" -ErrorAction SilentlyContinue
        Copy-Item "config\pcs_map.json" "dist\config\" -ErrorAction SilentlyContinue
    } else {
        Copy-Item "config\*.json" "dist\config\" -ErrorAction SilentlyContinue
    }
    
    # DCDC 핸들러 파일도 제거 (완전한 비활성화)
    if ($DisableDCDC -and (Test-Path "dist\_internal\pms_app\devices\dcdc_handler.py")) {
        Write-Host "DCDC 핸들러 파일 제거 중..." -ForegroundColor Yellow
        Remove-Item "dist\_internal\pms_app\devices\dcdc_handler.py*" -ErrorAction SilentlyContinue
    }
    
    Write-Host "`n배포 준비 완료:" -ForegroundColor Green
    Write-Host "- dist\PMS_GUI_Application.exe (GUI + 서버 통합)" -ForegroundColor White
    Write-Host "- dist\config\ (설정 파일들)" -ForegroundColor White
    if ($DisableDCDC) {
        Write-Host "- DCDC 장비 비활성화됨" -ForegroundColor Red
    }
    Write-Host "`n💡 실행 방법:" -ForegroundColor Cyan
    Write-Host "  1. dist\PMS_GUI_Application.exe 실행" -ForegroundColor White
    Write-Host "  2. 터미널 창에서 서버 상태 확인" -ForegroundColor White
    Write-Host "  3. GUI 창에서 데이터 모니터링 및 제어" -ForegroundColor White
    Write-Host "`n✅ 빌드 완료! 실행해보세요." -ForegroundColor Green
    
} else {
    Write-Host "`n====================================" -ForegroundColor Red
    Write-Host "빌드 실패!" -ForegroundColor Red
    Write-Host "로그를 확인해주세요." -ForegroundColor Red
    Write-Host "====================================" -ForegroundColor Red
}

Write-Host "`n아무 키나 눌러 계속하세요..." -ForegroundColor Gray
pause