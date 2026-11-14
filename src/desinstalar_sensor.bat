@echo off
setlocal
cd /d "%~dp0"

rem === Quita la tarea "Tuner Sensor" (solo la nuestra, no toca nada mas).
rem     Necesita admin UNA sola vez. ===
fltmc >nul 2>&1
if errorlevel 1 (
  echo Se requieren permisos de administrador. Acepta el aviso de UAC...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Desregistrando la tarea "Tuner Sensor"...
schtasks /Query /TN "Tuner Sensor" >nul 2>&1
if errorlevel 1 (
  echo [INFO] La tarea no existe; no hay nada que quitar.
) else (
  schtasks /Delete /TN "Tuner Sensor" /F
  echo [OK] Tarea eliminada. Ya no se arrancara el sensor al iniciar sesion.
  echo      (Si LhmServer sigue corriendo ahora, se cerrara al cerrar sesion,
  echo       o matandolo en el Administrador de tareas.)
)
echo.
pause
