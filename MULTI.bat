@echo off
cd /d "%~dp0"
echo ================================================================
echo   DESCARGA MULTI-CUENTA
echo   Poné una cookie por archivo en cookies\acc1.txt, acc2.txt, ...
echo ================================================================
echo.
if not exist cookies\*.txt (
  echo [X] No hay cookies en la carpeta cookies\
  echo     Crea cookies\acc1.txt con el ndus de la cuenta 1, etc.
  pause
  exit /b 2
)
set /p URL="Pega el link del share: "
set /p DEST="Carpeta destino (ej. C:\TB): "
if "%DEST%"=="" set DEST=C:\TB
echo.
python orquestar.py --url "%URL%" -o "%DEST%" --verificar --esperar
pause
