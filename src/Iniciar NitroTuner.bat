@echo off
cd /d "%~dp0"

rem === auto-elevar a administrador. LhmServer.exe necesita admin para leer los
rem     MSR del CPU (carga el driver ring0 de LHM). El HttpListener usa
rem     http://127.0.0.1:8085, que no requiere urlacl. ===
fltmc >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

rem === Servidor de sensores propio (tools\LHM\LhmServer.exe): sirve la temp de
rem     CPU en http://127.0.0.1:8085/data.json sin abrir la GUI de LHM. Si el
rem     puerto 8085 YA escucha, no lo tocamos. Si no, matamos instancias viejas
rem     y arrancamos LhmServer limpio. ===
netstat -ano | findstr ":8085" | findstr "LISTENING" >nul
if not errorlevel 1 goto :servidor_listo
taskkill /F /IM LibreHardwareMonitor.exe >nul 2>&1
taskkill /F /IM LhmServer.exe >nul 2>&1
timeout /t 1 /nobreak >nul
start "" "%~dp0tools\LHM\LhmServer.exe"
set /a tries=0
:esperar_puerto
netstat -ano | findstr ":8085" | findstr "LISTENING" >nul
if not errorlevel 1 goto :servidor_listo
set /a tries+=1
if %tries% geq 10 goto :servidor_listo
timeout /t 1 /nobreak >nul
goto :esperar_puerto
:servidor_listo

rem === arrancar la interfaz grafica congelada ===
start "" "%~dp0NitroTuner.exe"
