@echo off
cd /d "%~dp0"
pythonw --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH do sistema!
    pause
    exit /b 1
)
start "" pythonw bot_gui.py
exit
