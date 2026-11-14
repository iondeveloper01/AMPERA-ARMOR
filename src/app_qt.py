"""Lanzador directo de la interfaz PySide6 de la 1.1.0.

Es equivalente a `py main.py`; existe para abrir la GUI sin pasar por el
selector de interfaces del punto de entrada.

Uso en modo fuente:
    py app_qt.py
    py app_qt.py --tema claro --pagina perfiles
"""

import sys

from gui_qt import ejecutar

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(ejecutar())
