@echo off
chcp 65001 >nul
title Elegir carpetas - TeraBox
cd /d "%~dp0"
python elegir_carpetas.py
echo.
pause