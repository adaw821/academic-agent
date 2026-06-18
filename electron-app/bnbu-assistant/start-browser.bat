@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   BNBU AI 智能学术助手 - 浏览器模式
echo ============================================
echo.
echo 正在启动本地服务器...
echo 打开浏览器访问: http://localhost:8765
echo 按 Ctrl+C 停止服务器
echo.
start "" http://localhost:8765
python -m http.server 8765
pause
