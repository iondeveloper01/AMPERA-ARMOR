@echo off
setlocal
cd /d "%~dp0"

rem === Registra la tarea que arranca el sensor de CPU (LhmServer) al iniciar
rem     sesion, elevado y oculto. Necesita admin UNA sola vez (al instalar). ===
fltmc >nul 2>&1
if errorlevel 1 (
  echo Se requieren permisos de administrador. Acepta el aviso de UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar_sensor.ps1"
echo.
pause
