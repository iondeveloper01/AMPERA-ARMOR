"""Interfaz de AMPERA 1.1.0 con PySide6.

Lanzar desde la raíz del proyecto:
    py main.py
o:
    py app_qt.py
"""


def ejecutar(argv=None):
    from .ventana import main
    return main(argv)
