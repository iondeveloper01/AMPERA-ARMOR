# AMPERA — código fuente (1.1.0)

Interfaz **PySide6** + backend de monitorización y ajuste para Windows 11.
La interfaz clásica en CustomTkinter sigue disponible con `--clasica`.

© 2026 ion-developer · Licencia MIT (ver [`../LICENSE`](../LICENSE)).

## Requisitos

- Windows 11 x64 y **Python 3.13**.
- Dependencias: `py -3 -m pip install -r requirements.txt`
  (`pynvml`, `psutil`, `customtkinter`, `PySide6`).
- GPU NVIDIA para métricas de GPU (NVML).
- Permiso de administrador para temperatura de CPU (LHM) y FPS reales (PresentMon).

## Ejecutar desde el código

```bat
py -3 main.py
py -3 main.py --clasica
py -3 main.py --tool monitor
```

## Herramientas de terceros

El repositorio **no** incluye los binarios de terceros (van dentro del instalador).
Para ejecutar desde fuente, colócalos en `tools\` junto a este README:

- **LibreHardwareMonitor** (GPL-3.0) → `tools\LHM\`. El puente propio es
  [`../LhmServer.cs`](../LhmServer.cs), que carga `LibreHardwareMonitorLib.dll` y expone
  `http://127.0.0.1:8085/data.json`.
- **Mem Reduct** 3.5.2 (GPL-3.0, Henry++) → `tools\MemReduct\` (opcional).
- **PresentMon** 2.6.0 (MIT, Intel) → `tools\PresentMon\` (FPS y frametime reales).

Licencias y forma de redistribución: [`../LICENCIAS-TERCEROS.md`](../LICENCIAS-TERCEROS.md).

## Compilar el ejecutable

PyInstaller en modo `--onedir` con dos ejecutables que comparten `_internal`:

```bat
py -3 -m PyInstaller --noconfirm nitrotuner.spec
```

Genera `NitroTuner.exe` (GUI) y `NitroTunerCLI.exe` (consola). El icono es `AMPERA.ico`.
Los datos del usuario se guardan en `%LOCALAPPDATA%\AMPERA`, nunca junto al ejecutable.
