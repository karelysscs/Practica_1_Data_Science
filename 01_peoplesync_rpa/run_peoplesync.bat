@echo off
REM Lanzador para Windows Task Scheduler.
REM PASO 1: ir a la carpeta de este .bat.
cd /d "%~dp0"
REM PASO 2: ejecutar el bot y guardar la salida.
python peoplesync_bot.py >> "%~dp0salida_ejecucion.txt" 2>&1
