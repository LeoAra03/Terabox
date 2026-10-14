@echo off
chcp 65001 >nul
title Renovar cookie de TeraBox
cd /d "%~dp0"
python renovar_cookie.py
echo.
pause
