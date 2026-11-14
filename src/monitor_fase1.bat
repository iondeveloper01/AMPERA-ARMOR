@echo off
cd /d "%~dp0"
set PY=
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe
if not defined PY (where py >nul 2>&1 && set PY=py)
if not defined PY (
  echo No encuentro Python en este equipo.
  pause
  exit /b 1
)
%PY% -c "import pynvml, psutil" >nul 2>&1
if errorlevel 1 (
  echo Falta instalar las dependencias una sola vez. Ejecuta:
  echo   %PY% -m pip install nvidia-ml-py psutil
  pause
  exit /b 1
)
%PY% monitor_fase1.py %*
pause
