"""Punto de entrada unico de AMPERA 1.1.0.

- Sin argumentos: abre la interfaz nueva (PySide6), que es la de esta version.
- Con '--clasica': abre la interfaz anterior (CustomTkinter), que sigue
  disponible como respaldo mientras se conectan las acciones nuevas.
- Con '--tool <nombre> <args...>': ejecuta esa herramienta en modo consola y sale.

PyInstaller genera DOS .exe desde este mismo archivo:
    NitroTuner.exe     (--windowed) -> la GUI, sin consola detras
    NitroTunerCLI.exe  (--console)  -> las herramientas que se lanzan en ventana
                                      aparte (monitor, crear perfil) o cuya salida
                                      se captura por pipe (certificar, watchdog, sesion)

Uso en modo fuente:
    py main.py                      # GUI de la 1.1.0 (PySide6)
    py main.py --clasica            # GUI anterior (CustomTkinter)
    py main.py --tool monitor       # monitor en consola
"""
import sys


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        from dispatcher import ejecutar
        ejecutar(sys.argv[2], sys.argv[3:])
        return
    if "--clasica" in sys.argv[1:]:
        import customtkinter as ctk
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        from app import App
        App().mainloop()
        return
    from gui_qt import ejecutar
    raise SystemExit(ejecutar(sys.argv[1:]))


if __name__ == "__main__":
    main()
