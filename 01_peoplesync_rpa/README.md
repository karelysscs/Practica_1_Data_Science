# 1. RPA — PeopleSync (registro masivo de colaboradores)

Un solo script (`peoplesync_bot.py`) que registra los 50 empleados del dataset
en el formulario **PeopleSync HRIS** con Python + Selenium.

- Formulario: https://the-paul2002.github.io/Proyecto-IA-/Homework1/
- Dataset: `../Ingreso_Personal_Agosto - Hoja 1.csv` (o la hoja de Google, cambiando `USAR_GOOGLE_SHEETS = True`)

## Instalación

```bash
cd 01_peoplesync_rpa
pip install -r requirements.txt      # necesita Google Chrome instalado
```

## Uso

```bash
python peoplesync_bot.py
```

Config arriba del archivo: `HEADLESS`, `USAR_GOOGLE_SHEETS`, `WAIT_TIMEOUT`, rutas.

## Qué hace

1. Lee el dataset.
2. Abre el formulario y lee las opciones reales de cada `<select>`.
3. Valida cada registro (DNI 8 dígitos, teléfono, correo, fechas, y que
   género/área/puesto/contrato/sede/modalidad estén entre las opciones del form).
   Los inválidos se **saltan** sin detener el proceso.
4. Para cada registro válido llena el formulario, lo envía y verifica que el
   contador *"Ingresos registrados"* haya subido (sin recargar la página).
5. Imprime el resumen (total / cargados / fallidos + detalle), guarda
   `resultado_registros.csv` y toma una captura `evidencia_registros.png`
   de la tabla *"Ingresos Registrados en esta Sesión"* con todos los altas,
   como prueba de que quedaron registrados.

> El dataset trae 26 registros con género `No binario` / `Prefiero no indicar`,
> que el formulario (solo Masculino/Femenino) no puede recibir: se reportan como
> fallidos con su motivo. Se cargan los 24 restantes.

## Task Scheduler

`run_peoplesync.bat` ejecuta el bot y guarda la salida en `salida_ejecucion.txt`.
Registrar la tarea (PowerShell):

```powershell
$bat = "$PWD\run_peoplesync.bat"
schtasks /create /tn "PeopleSync_RPA" /tr "$bat" /sc daily /st 08:00 /f
schtasks /run /tn "PeopleSync_RPA"        # probar ahora
schtasks /query /tn "PeopleSync_RPA" /v   # ver "Último resultado" (0 = OK)
```
