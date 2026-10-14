@echo off
chcp 65001 >nul
title Preflight - capacidad antes de bajar 100 GB
cd /d "%~dp0"

set URL_DEF=https://www.1024tera.com/spanish/sharing/link?surl=1Mx1GP2rPLypBFF1RlVMog
set DEST=
set MANIF=
set URL=

echo.
echo  ============================================================
echo   PREFLIGHT
echo   Mide si tu disco y tu linea aguantan la descarga ANTES de
echo   dejarla corriendo toda la noche. Tarda ~30 segundos.
echo  ============================================================
echo.
echo   Destino:
echo     [1] C:\TB                       (cursos de ingles, 103,47 GB)
echo     [2] Curso de piano              (20,63 GB)
echo     [3] Otro (lo escribis)
echo.
set /p OPC=  Opcion [1/2/3]: 
if "%OPC%"=="1" goto op1
if "%OPC%"=="2" goto op2
if "%OPC%"=="3" goto op3
echo   Opcion invalida.
pause & exit /b 1

:op1
set DEST=C:\TB
set MANIF=inventarios\manifest_ingles.csv
set /p URL=  Link del share [Enter = el de ingles]: 
if not defined URL set URL=%URL_DEF%
goto correr

:op2
set DEST=C:\Users\Leo\desktop\Curso de piano\descargas
set MANIF=inventarios\manifest_piano.csv
set /p URL=  Link del share [Enter = ninguno]: 
goto correr

:op3
set /p DEST=  Carpeta destino: 
set /p MANIF=  Manifiesto [Enter = inventarios\manifest_ingles.csv]: 
if not defined MANIF set MANIF=inventarios\manifest_ingles.csv
set /p URL=  Link del share [Enter = ninguno]: 
goto correr

:correr
echo.
if defined URL goto con_url
python preflight.py --destino "%DEST%" --manifest "%MANIF%"
goto veredicto

:con_url
python preflight.py --destino "%DEST%" --manifest "%MANIF%" --url "%URL%"

:veredicto
echo.
echo  ============================================================
echo   Lee el veredicto:
echo     - Si el disco NO alcanza: libera espacio y repeti.
echo     - La tabla final dice cuanto tarda de verdad, a cada
echo       velocidad. Comparala con lo que diga terabox_dl.log.
echo  ============================================================
echo.
pause
