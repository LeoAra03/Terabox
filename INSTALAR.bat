@echo off
chcp 65001 >nul
title Instalacion - TeraBox
cd /d "%~dp0"
echo.
echo  ============================================================
echo   INSTALACION (una sola vez)
echo  ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo   [X] Python no esta instalado.
  echo.
  echo       Te abro la pagina de descarga. En el instalador MARCA
  echo       la casilla "Add Python to PATH".
  echo.
  echo       Despues cerra esta ventana y volve a correr INSTALAR.bat
  echo.
  start "" https://www.python.org/downloads/
  pause & exit /b 1
)
echo   [OK] Python encontrado
python --version
echo.
echo   Instalando librerias (requests, tqdm, psutil)...
python -m pip install --upgrade pip -q
python -m pip install requests tqdm psutil -q
if errorlevel 1 (
  echo   [X] Fallo la instalacion. Copia el error de arriba y avisame.
  pause & exit /b 1
)
echo   [OK] Librerias listas
echo.
echo  ============================================================
echo   Paso 2: LA COOKIE
echo  ============================================================
echo.
echo   Ahora se abre el asistente. Va a:
echo     1. pedirte el ndus nuevo
echo     2. PROBARLO de verdad (pide un enlace y baja 1 KB)
echo.
echo   Si no sabes de donde sacar el ndus: en el navegador entra a
echo   1024tera.com, F12 - Application - Cookies - copia "ndus".
echo.
pause
python renovar_cookie.py
echo.
echo  ============================================================
echo   LISTO. Ahora hace doble clic en PIANO.bat o INGLES.bat
echo  ============================================================
echo.
pause
