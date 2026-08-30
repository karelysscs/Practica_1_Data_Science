@echo off
REM ===================================================================
REM  SUNAT Tipo de Cambio - lanzador para Windows Task Scheduler
REM  (no necesita navegador: usa el metodo 'api' por HTTP)
REM ===================================================================
setlocal enableextensions

REM PASO 1: pararse en la carpeta de este .bat.
set "PROJ=%~dp0"
cd /d "%PROJ%"

REM PASO 2: elegir Python (venv del proyecto si existe; si no, el global).
set "PY=%PROJ%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM PASO 3: ejecutar el scraper (enero 2024 -> mes actual, CSV + Excel).
REM         Todo se agrega a salida\task_scheduler.out.
if not exist "%PROJ%salida" mkdir "%PROJ%salida"
echo [%date% %time%] Iniciando SUNAT scraper con "%PY%" >> "%PROJ%salida\task_scheduler.out"
"%PY%" "%PROJ%sunat_tipo_cambio.py" --formato ambos >> "%PROJ%salida\task_scheduler.out" 2>&1

REM PASO 4: propagar el codigo de salida (0 = OK).
set "RC=%ERRORLEVEL%"
echo [%date% %time%] Finalizado con codigo %RC% >> "%PROJ%salida\task_scheduler.out"
exit /b %RC%
