<#
    Registra (o actualiza) la tarea programada de Windows para el scraper SUNAT.

    Ejecutar en PowerShell, dentro de la carpeta del proyecto:
        powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1

    Parametros:
        -Time  "07:00"        hora diaria (por defecto 07:00)
        -TaskName "..."       nombre de la tarea (por defecto SUNAT_TipoCambio_Diario)

    PASOS:
        PASO 1: ubicar run_sunat.bat.
        PASO 2: definir accion (ejecutar el .bat) y disparador (diario).
        PASO 3: opciones (arrancar aunque se haya pasado la hora, limite 1 h).
        PASO 4: registrar (-Force reemplaza si ya existe).
#>
param(
    [string]$Time = "07:00",
    [string]$TaskName = "SUNAT_TipoCambio_Diario"
)

$ErrorActionPreference = "Stop"

# PASO 1
$proj = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $proj "run_sunat.bat"
if (-not (Test-Path $bat)) { throw "No se encontro $bat" }

# PASO 2
$action  = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $proj
$trigger = New-ScheduledTaskTrigger -Daily -At $Time

# PASO 3
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# PASO 4
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Descarga el tipo de cambio oficial de SUNAT" -Force

Write-Host "Tarea '$TaskName' registrada (diaria a las $Time)." -ForegroundColor Green
Write-Host "Probar ahora:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Ver resultado: Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Eliminar:      Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
