@echo off
chcp 65001 >nul
title Curso de Piano  -  20.63 GB
cd /d "%~dp0"

REM ============ ADONDE VA (cambialo si queres otra carpeta) ============
set DEST=C:\Users\Leo\desktop\Curso de piano\descargas

set URL=https://www.1024tera.com/spanish/sharing/link?surl=4xnpHUw7xDHWwwuJVsU6dQ

REM ============ AJUSTES ============
REM  Archivos chicos (media 84 MB): muchos a la vez, pocas conexiones cada uno.
set OPT=--workers-files 8 -c 16

echo.
echo  ============================================================
echo   CURSO DE PIANO  -  691 archivos  -  20.63 GB
echo   Destino: %DEST%
echo   Tiempo: 2 a 3 horas
echo  ============================================================
echo.
echo   Las 19 carpetas se completan en orden, de la 1 a la 19.
echo   Ctrl+C frena sin perder lo bajado: volve a correr este .bat.
echo.

REM ===== CHEQUEO PREVIO: la cookie sirve? (5 segundos) =====
python renovar_cookie.py --check
if not errorlevel 1 goto pruebafuego
echo.
echo   ------------------------------------------------------------
echo    El chequeo de cookie NO paso.
echo.
echo    Significa que TeraBox pide re-verificacion: el ndus vencio,
echo    o la cookie se genero desde otra red.
echo.
echo    Lo correcto: corre RENOVAR_COOKIE.bat y despues este .bat.
echo   ------------------------------------------------------------
echo.
choice /c SN /m "  Continuar igual? [S/N]"
if errorlevel 2 exit /b 1
goto completar

:pruebafuego
choice /c SN /m "  Hacer la PRUEBA DE FUEGO primero? (57 MB, 2 min)"
if errorlevel 2 goto completar
echo.
echo  --- Prueba de fuego ---
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "\.(pdf|srt|html)$"
if errorlevel 1 (
  echo.
  echo  ############################################################
  echo   [X] LA PRUEBA FALLO. Corre RENOVAR_COOKIE.bat y reintenta.
  echo  ############################################################
  echo.
  pause & exit /b 1
)
echo.
echo   [OK] Prueba superada. Arrancando la descarga completa...
echo.

:completar
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT%

echo.
echo  ============================================================
echo   TERMINADO. Quedo algo a medias?
echo  ============================================================
dir /s /b "%DEST%\*.part*" 2>nul | find /c /v ""
echo   ^(si dice 0, esta todo completo^)
echo.
echo   Si aparecio "Fallos: [... ]", volve a correr este mismo .bat:
echo   retoma solo lo que falta.
echo.
pause
