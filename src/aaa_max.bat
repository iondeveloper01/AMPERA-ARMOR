@echo off
cd /d "%~dp0"

rem === auto-elevar a administrador (LHM necesita admin para leer la temp del CPU) ===
fltmc >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe
if not defined PY (where py >nul 2>&1 && set PY=py)
if not defined PY (
  echo No encuentro Python en este equipo.
  pause
  exit /b 1
)

echo [1/3] Aplicando perfil aaa_max...
%PY% perfil_juego.py aaa_max

echo [2/3] Arrancando Libre Hardware Monitor (servidor web, puerto 8085)...
rem IMPORTANTE: varias instancias de LHM a la vez devuelven lecturas basura
rem (temp/potencia falsas). Cerramos todas y arrancamos exactamente una.
taskkill /F /IM LibreHardwareMonitor.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start "" "%~dp0tools\LHM\LibreHardwareMonitor.exe"
timeout /t 10 /nobreak >nul

echo [3/3] Arrancando el monitor en otra ventana...
start "Monitor Fase 1" cmd /k ""%PY%" "%~dp0monitor_fase1.py""

echo.
echo Listo. Ya puedes abrir el juego.
echo IMPORTANTE: conecta el cargador antes de jugar.
echo (En bateria el firmware limita el CPU a ~2400 MHz y la GPU a ~30 W.)
pause
