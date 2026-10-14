@echo off
chcp 65001 >nul
title Supervisor de descarga - una sola cuenta
cd /d "%~dp0"

set URL_DEF=https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog
set DEST=
set URL=
set TECHO=

echo.
echo  ============================================================
echo   SUPERVISOR DE DESCARGA
echo   Baja los 103 GB con UNA cuenta y arregla la sesion solo cuando
echo   TeraBox pide re-verificacion (need verify_v2 / 400310).
echo   Lo bajado nunca se pierde: reanuda desde los .part.json.
echo  ============================================================
echo.
echo   Destino:
echo     [1] C:\TB                       (cursos de ingles)
echo     [2] Curso de piano
echo     [3] Otro
echo.
set /p OPC=  Opcion [1/2/3]: 
if "%OPC%"=="1" goto op1
if "%OPC%"=="2" goto op2
if "%OPC%"=="3" goto op3
echo   Opcion invalida.
pause & exit /b 1

:op1
set DEST=C:\TB
set /p URL=  Link del share [Enter = el de ingles]: 
if not defined URL set URL=%URL_DEF%
goto techo

:op2
set DEST=C:\Users\Leo\desktop\Curso de piano\descargas
set /p URL=  Link del share (piano): 
goto techo

:op3
set /p DEST=  Carpeta destino: 
set /p URL=  Link del share: 
goto techo

:techo
if exist informe_techo.json (
  choice /c SN /m "  Usar la configuracion optima de informe_techo.json? [S/N]"
  if not errorlevel 2 set TECHO=--techo-json informe_techo.json
)

echo.
echo   Dejalo corriendo. Si TeraBox pide re-verificacion, el supervisor:
echo     1) prueba refrescar la cookie solo
echo     2) si no alcanza, te dice que la renueves y ESPERA 15 min
echo     3) si no aparece nada, para y lo deja en supervisor.log
echo.
python supervisor.py --url "%URL%" -o "%DEST%" %TECHO% --esperar-cookie 900
set RC=%ERRORLEVEL%
echo.
echo  ============================================================
if "%RC%"=="0" echo   Terminado sin fallos. Siguiente paso: VERIFICAR.bat
if "%RC%"=="1" echo   Termino con archivos pendientes. Corre VERIFICAR.bat y relanza con --fsids
if "%RC%"=="2" echo   FRENADO: errno -6 / ip_blacklist. No reintentes: espera 6-24 h
if "%RC%"=="3" echo   La sesion no se pudo arreglar. Corre RENOVAR_COOKIE.bat y relanza
echo   Detalle: supervisor.log
echo  ============================================================
echo.
pause
