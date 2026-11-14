@echo off
cd /d "%~dp0"

rem Interfaz anterior (CustomTkinter). Se conserva como respaldo.
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

"%PY%" "%~dp0main.py" --clasica %*
if errorlevel 1 pause
