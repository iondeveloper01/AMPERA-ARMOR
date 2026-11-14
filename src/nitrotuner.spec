# -*- mode: python ; coding: utf-8 -*-
# Un solo Analysis genera DOS exes que comparten la carpeta _internal:
#   NitroTuner.exe    (GUI, sin consola)
#   NitroTunerCLI.exe (consola, para monitor / crear_perfil y las herramientas)
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []

# Modulos locales que el dispatcher importa dinamicamente (__import__),
# por eso PyInstaller no los detecta solos.
hiddenimports = [
    "dispatcher",
    "certificar", "watchdog", "sesion", "monitor_fase1", "crear_perfil",
    "perfil_juego", "guardia_termico", "reporte_html", "rutas", "i18n",
    "aviso_termico", "overlay_estilo", "planes_energia", "datos",
    "pynvml", "psutil",
]

for pkg in ("customtkinter",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow solo se uso en build-time para generar el .ico; la app no lo importa.
    excludes=["PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="NitroTuner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="AMPERA.ico",
)

exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="NitroTunerCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon="AMPERA.ico",
)

coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NitroTuner",
)
