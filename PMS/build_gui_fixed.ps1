# PMS GUI Build Script
param(
    [switch]$DisableDCDC = $false
)

Write-Host "====================================" -ForegroundColor Green
Write-Host "PMS GUI Application Build" -ForegroundColor Green
if ($DisableDCDC) {
    Write-Host "DCDC Device Disabled Mode" -ForegroundColor Red
}
Write-Host "====================================" -ForegroundColor Green

# Clean previous build
if (Test-Path "dist") {
    Write-Host "Cleaning previous build..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "dist"
}

if (Test-Path "build") {
    Write-Host "Cleaning temp files..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "build"
}

# Check dependencies
Write-Host "Checking dependencies..." -ForegroundColor Cyan
try {
    python -c "import tkinter" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "tkinter: OK" -ForegroundColor Green
    } else {
        Write-Host "tkinter: MISSING" -ForegroundColor Red
    }
} catch {
    Write-Host "tkinter: MISSING" -ForegroundColor Red
}

try {
    python -c "import pymodbus" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "pymodbus: OK" -ForegroundColor Green
    } else {
        Write-Host "pymodbus: MISSING" -ForegroundColor Red
    }
} catch {
    Write-Host "pymodbus: MISSING" -ForegroundColor Red
}

try {
    python -c "import paho.mqtt.client" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "paho-mqtt: OK" -ForegroundColor Green
    } else {
        Write-Host "paho-mqtt: MISSING" -ForegroundColor Red
    }
} catch {
    Write-Host "paho-mqtt: MISSING" -ForegroundColor Red
}

# Build with PyInstaller
Write-Host "Building application..." -ForegroundColor Cyan
Write-Host "Using spec file: build_gui_config.spec" -ForegroundColor Yellow
pyinstaller --clean build_gui_config.spec

# Check build result
if (Test-Path "dist\PMS_GUI_Application.exe") {
    Write-Host "Build SUCCESS!" -ForegroundColor Green
    
    # Copy config files
    Write-Host "Copying config files..." -ForegroundColor Yellow
    if (!(Test-Path "dist\config")) {
        New-Item -ItemType Directory -Path "dist\config" | Out-Null
    }
    
    # Copy yml files
    Copy-Item "config\*.yml" "dist\config\" -ErrorAction SilentlyContinue
    
    # Copy json files (with DCDC option)
    if ($DisableDCDC) {
        Write-Host "DCDC Disabled: Excluding dcdc_map.json" -ForegroundColor Yellow
        Copy-Item "config\bms_map.json" "dist\config\" -ErrorAction SilentlyContinue
        Copy-Item "config\pcs_map.json" "dist\config\" -ErrorAction SilentlyContinue
    } else {
        Copy-Item "config\*.json" "dist\config\" -ErrorAction SilentlyContinue
    }
    
    Write-Host "Build completed successfully!" -ForegroundColor Green
    if ($DisableDCDC) {
        Write-Host "DCDC device will be disabled in exe" -ForegroundColor Red
    }
} else {
    Write-Host "Build FAILED!" -ForegroundColor Red
}

Write-Host "Press any key to continue..."
Read-Host 