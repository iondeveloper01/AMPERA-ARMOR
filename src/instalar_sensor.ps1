# Registra la tarea programada "Tuner Sensor": arranca LhmServer OCULTO y
# ELEVADO al iniciar sesion, para que la temp de CPU este siempre disponible.
# Portable: usa $PSScriptRoot (su propia carpeta), sirve en cualquier PC/ruta.
$ErrorActionPreference = 'Stop'

$base = $PSScriptRoot
$vbs  = Join-Path $base 'ocultar_sensor.vbs'
$lhm  = Join-Path $base 'tools\LHM\LhmServer.exe'

if (-not (Test-Path $lhm)) { Write-Host "[ERROR] No encuentro tools\LHM\LhmServer.exe en: $base" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $vbs)) { Write-Host "[ERROR] No encuentro ocultar_sensor.vbs en: $base" -ForegroundColor Red; exit 1 }

$user      = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action    = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('"' + $vbs + '"')
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
# CLAVE en un portatil: arrancar en bateria, no parar al pasar a bateria, y SIN
# limite de tiempo de ejecucion (el servidor corre indefinidamente).
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'Tuner Sensor' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "[OK] Tarea 'Tuner Sensor' creada." -ForegroundColor Green
Write-Host "     - Se ejecuta al iniciar sesion (usuario: $user)" -ForegroundColor DarkGray
Write-Host "     - Elevada (maximos privilegios) y oculta (sin ventana negra)" -ForegroundColor DarkGray
Write-Host "     - Arranca en bateria y sin limite de tiempo" -ForegroundColor DarkGray
Write-Host ""

# Probar ahora sin reiniciar
Start-ScheduledTask -TaskName 'Tuner Sensor'
Write-Host "[..] Lanzando el sensor y esperando el puerto 8085 (hasta 10 s)..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8085/data.json' -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}
if ($ok) {
    Write-Host "[OK] Sensor respondiendo en :8085 -> la temp de CPU ya funciona sin abrir nada." -ForegroundColor Green
} else {
    Write-Host "[AVISO] 8085 no responde aun. Puede tardar unos segundos. Si sigue sin responder," -ForegroundColor Yellow
    Write-Host "        comprueba que tu cuenta de usuario sea administrador (el driver ring0 lo necesita)." -ForegroundColor Yellow
}
