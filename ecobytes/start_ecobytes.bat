@echo off
chcp 65001 >nul 2>&1
title EcoBytes - Delhi NCR AQI Platform Launcher
color 0A

echo.
echo  ============================================================
echo   EcoBytes: Weather-Aware Delhi NCR AQI ^& Alerting Platform
echo   Launcher - Starting Backend Server
echo  ============================================================
echo.

:: Navigate to the project directory
cd /d "%~dp0"

:: Check Python is available
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Show version
echo  [INFO] Python version:
python --version
echo.

:: Clean up stale process on port 3000
echo  [INIT] Cleaning up stale process on port 3000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

echo.
echo  ============================================================
echo   Starting EcoBytes Python Server on http://localhost:3000
echo  ============================================================
echo.

:: Auto-open browser after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:3000"

:: Run server directly
python server.py

pause

