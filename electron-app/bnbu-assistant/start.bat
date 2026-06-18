@echo off
cd /d "%~dp0"

set ELECTRON_RUN_AS_NODE=
set NODE_OPTIONS=

echo ========================================
echo BNBU AI Academic Assistant
echo ========================================
echo.

echo [0/3] Cleaning up old OCR server on port 8766...
:: Find PIDs listening on port 8766 and kill them
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8766 " ^| findstr "LISTENING"') do (
    echo   Killing old process PID %%a...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [1/2] Starting OCR Server on port 8766...
:: 自动查找 Python（兼容不同电脑）
where py >nul 2>&1 && set PY=py || set PY=python
start "BNBU-OCR" %PY% ocr_server.py

timeout /t 3 /nobreak >nul

echo [2/2] Starting Electron App...
node launcher.js

echo.
echo App closed. OCR server still running in background.
echo.
pause
