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
    
    # Copy and modify yml files based on DCDC option
    if ($DisableDCDC) {
        Write-Host "DCDC Disabled: Modifying config.yml to remove DCDC device" -ForegroundColor Yellow
        
        # Read the original config.yml
        $configContent = Get-Content "config\config.yml" -Raw
        
        # Remove DCDC device entry using regex
        # This removes the entire DCDC device block including all its properties
        $modifiedConfig = $configContent -replace '(?s)\s*-\s+name:\s*"[^"]*DCDC[^"]*".*?(?=\s*-\s+name:|\s*#|\Z)', ''
        
        # Write modified config to dist directory
        $modifiedConfig | Out-File "dist\config\config.yml" -Encoding UTF8
        
        Write-Host "Modified config.yml: DCDC device entry removed" -ForegroundColor Green
    } else {
        # Copy original config.yml
        Copy-Item "config\config.yml" "dist\config\" -ErrorAction SilentlyContinue
        Write-Host "Copied: Original config.yml (all devices enabled)" -ForegroundColor Green
    }
    
    # Copy json files (with DCDC option)
    if ($DisableDCDC) {
        Write-Host "DCDC Disabled: Excluding dcdc_map.json" -ForegroundColor Yellow
        Copy-Item "config\bms_map.json" "dist\config\" -ErrorAction SilentlyContinue
        Copy-Item "config\pcs_map.json" "dist\config\" -ErrorAction SilentlyContinue
        Write-Host "Copied: bms_map.json, pcs_map.json" -ForegroundColor Green
    } else {
        Copy-Item "config\*.json" "dist\config\" -ErrorAction SilentlyContinue
        Write-Host "Copied: All JSON files (including dcdc_map.json)" -ForegroundColor Green
    }
    
    Write-Host "Build completed successfully!" -ForegroundColor Green
    if ($DisableDCDC) {
        Write-Host "NOTE: DCDC device is DISABLED in this build" -ForegroundColor Red
        Write-Host "      - dcdc_map.json file excluded" -ForegroundColor Yellow
        Write-Host "      - DCDC device entry removed from config.yml" -ForegroundColor Yellow
        Write-Host "      - Only BMS and PCS devices will be available" -ForegroundColor Yellow
    } else {
        Write-Host "NOTE: All devices (BMS, DCDC, PCS) are enabled" -ForegroundColor Green
    }
} else {
    Write-Host "Build FAILED!" -ForegroundColor Red
}

Write-Host "Press any key to continue..."
Read-Host 