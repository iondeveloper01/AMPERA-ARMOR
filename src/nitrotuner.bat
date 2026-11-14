@echo off
cd /d "%~dp0"

rem === auto-elevar a administrador. LhmServer.exe necesita admin para leer los
rem     MSR del CPU (carga el driver ring0 de LHM). El HttpListener ya NO usa
rem     http://+:8085 sino http://127.0.0.1:8085, que no requiere urlacl. ===
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

rem === Servidor de sensores propio (tools\LHM\LhmServer.exe): sirve la temp de
rem     CPU en http://127.0.0.1:8085/data.json SIN abrir la GUI de Libre Hardware
rem     Monitor y SIN pulsar "Options -> Remote Web Server -> Run" (ese clic
rem     manual era justo lo que fallaba). Si el puerto 8085 YA escucha, no lo
rem     tocamos. Si no, matamos instancias viejas (GUI de LHM incluida, para que
rem     no pelee por el driver ring0) y arrancamos LhmServer limpio. ===
netstat -ano | findstr ":8085" | findstr "LISTENING" >nul
if not errorlevel 1 goto :servidor_listo
taskkill /F /IM LibreHardwareMonitor.exe >nul 2>&1
taskkill /F /IM LhmServer.exe >nul 2>&1
timeout /t 1 /nobreak >nul
start "" "%~dp0tools\LHM\LhmServer.exe"
rem esperar a que el puerto levante (max ~10 s) en vez de un timeout fijo
set /a tries=0
:esperar_puerto
netstat -ano | findstr ":8085" | findstr "LISTENING" >nul
if not errorlevel 1 goto :servidor_listo
set /a tries+=1
if %tries% geq 10 goto :servidor_listo
timeout /t 1 /nobreak >nul
goto :esperar_puerto
:servidor_listo

rem === arrancar la interfaz grafica de la 1.1.0 (PySide6) ===
"%PY%" "%~dp0main.py"
