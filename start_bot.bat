@echo off
chcp 65001 >nul
title Fluxer AutoBot - Running...

:: Переходим в папку, где лежит батник
cd /d "%~dp0"

:: Проверяем, что Python вообще доступен в системе
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python не найден в PATH!
    echo Переустанови Python и поставь галочку "Add Python to PATH"
    pause
    exit /b 1
)

echo Starting bot...
python main.py

:: Окно не закроется, если бот упадёт — увидишь ошибку
pause
