@echo off
title EcoBytes Delhi NCR AQI & Alert Platform
cd /d "%~dp0"

echo ================================================================
echo   EcoBytes - Weather-Aware AQI & SMS Alert System (Delhi NCR)
echo   Smart India Hackathon (SIH) Prototype
echo ================================================================
echo.
echo [1/2] Starting EcoBytes Multi-Threaded Server on port 3000...
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:3000"
python server.py

pause
