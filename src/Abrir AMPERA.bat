@echo off
cd /d "%~dp0"

rem Interfaz de AMPERA 1.1.0 (PySide6). Se abre sin administrador: las acciones
rem que tocan el plan de energia piden elevacion aparte cuando haga falta.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
  )
)
if not defined PY (
  echo No encuentro Python en este equipo.
  echo Instala Python 3 y ejecuta: pip install -r requirements.txt
  pause
  exit /b 1
)

"%PY%" "%~dp0main.py" %*
if errorlevel 1 pause
