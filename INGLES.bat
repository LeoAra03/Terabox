@echo off
chcp 65001 >nul
title Cursos de Ingles  -  96.36 GB
cd /d "%~dp0"

REM ============ ADONDE VA ============
REM  NO uses "Curso de piano\descargas": las carpetas largas de este
REM  share dejan solo 5 caracteres antes del limite de 260 de Windows.
set DEST=C:\TB

set URL=https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog

REM ============ AJUSTES ============
REM  Archivos GRANDES (media 309 MB, el mayor 1.28 GB): pocos a la vez,
REM  mas conexiones cada uno. Con los ajustes del piano (workers-files 8),
REM  el archivo de 1.28 GB bajaria con 3 conexiones.
set OPT=--workers-files 4 -c 24

echo.
echo  ============================================================
echo   CURSOS DE INGLES  -  356 archivos  -  96.36 GB
echo   Destino: %DEST%
echo   Tiempo: 7 a 14 horas  (dejalo de noche)
echo  ============================================================
echo.
echo   ANTES DE IRTE A DORMIR: desactiva la suspension de Windows
echo   (Panel de control - Energia - "Nunca" suspender).
echo.
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
echo    En una descarga de 8 horas esto es la diferencia entre que
echo    funcione o perder la noche. Corre RENOVAR_COOKIE.bat.
echo   ------------------------------------------------------------
echo.
choice /c SN /m "  Continuar igual? [S/N]"
if errorlevel 2 exit /b 1
goto completar

:pruebafuego
choice /c SN /m "  Hacer la PRUEBA DE FUEGO primero? (596 MB, 4 min)"
if errorlevel 2 goto completar
echo.
echo  --- Prueba de fuego ---
python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "\.(pdf|txt|pptx|rar|url)$"
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
echo   Si aparecio "Fallos: [... ]", volve a correr este mismo .bat.
echo.
pause

REM ==================================================================
REM   BAJAR POR CURSO (opcional). El C1 es el mas pesado: hace ese
REM   primero, con la cookie fresca. Sacale el REM a la que quieras.
REM ==================================================================
REM python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "Inglés C1 para Hispanohablantes"
REM python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "B2\.2 Plus"
REM python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "B1\.2_B2\.1"
REM python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "A2-B1\.1"
REM python terabox_v2_fixed.py "%URL%" -o "%DEST%" %OPT% --only "Inglés A1 para Hispanohablantes"
