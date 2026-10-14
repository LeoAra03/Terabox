@echo off
chcp 65001 >nul
title Verificar integridad de lo descargado
cd /d "%~dp0"

echo.
echo  ============================================================
echo   VERIFICAR LO DESCARGADO  (bytes + md5 contra el manifiesto)
echo  ============================================================
echo.
echo   Que queres verificar?
echo     [1] Curso de piano    C:\Users\Leo\desktop\Curso de piano\descargas
echo     [2] Cursos de ingles  C:\TB
echo     [3] Los dos
echo.
set /p OPC=  Opcion [1/2/3]: 

echo.
choice /c SN /m "  Moves los archivos corruptos a _cuarentena/ para rebajarlos? [S/N]"
if errorlevel 2 (set CUAR=) else (set CUAR=--cuarentena)

if "%OPC%"=="1" goto piano
if "%OPC%"=="2" goto ingles
if "%OPC%"=="3" goto losdos
echo   Opcion invalida.
pause & exit /b 1

:piano
echo.
echo  --- Curso de piano: chequeo completo con md5 (lee los 22 GB: ~1-2 min) ---
python verificar_integridad.py --manifest inventarios\manifest_piano.csv --dir "C:\Users\Leo\desktop\Curso de piano\descargas" %CUAR% --json informe_piano.json
goto fin

:ingles
echo.
echo  --- Cursos de ingles: el manifiesto NO tiene md5, solo se puede verificar por tamano ---
echo      Para tener md5 la proxima vez: python inventario_md5.py "<link>" -o inventarios\manifest_ingles_md5.csv
python verificar_integridad.py --manifest inventarios\manifest_ingles.csv --dir C:\TB --solo-tamano %CUAR% --json informe_ingles.json
goto fin

:losdos
echo.
echo  --- Curso de piano ---
python verificar_integridad.py --manifest inventarios\manifest_piano.csv --dir "C:\Users\Leo\desktop\Curso de piano\descargas" %CUAR% --json informe_piano.json
echo.
echo  --- Cursos de ingles ---
python verificar_integridad.py --manifest inventarios\manifest_ingles.csv --dir C:\TB --solo-tamano %CUAR% --json informe_ingles.json
goto fin

:fin
echo.
echo  ============================================================
echo   Si aparecieron archivos a rehacer, quedaron en:
echo     rehacer.txt        (una linea por archivo, con el motivo)
echo     rehacer_fsids.txt  (lista para --fsids)
echo.
echo   Ejemplo de relanzada (reemplaza la URL por la del share):
echo     python terabox_v2_fixed.py "URL" -o "C:\TB" --workers-files 4 -c 24 --fsids 123,456
echo  ============================================================
echo.
pause
