"""Dispatcher de herramientas del backend.

Cuando la app esta congelada con PyInstaller no existe python.exe, asi que el
propio ejecutable se relanza con '--tool <nombre> <args...>'. Este modulo fija
sys.argv exactamente como si se hubiera invocado el script original y llama a su
main(). Tambien se usa en modo fuente (py main.py --tool X) para que el camino
sea el mismo dentro y fuera del .exe.
"""
import sys

# nombre -> (modulo, funcion). Los imports van dentro para que cada herramienta
# cargue solo sus propias dependencias (p. ej. la GUI no se importa aqui).
HERRAMIENTAS = {
    "certificar": ("certificar", "main"),
    "watchdog": ("watchdog", "main"),
    "sesion": ("sesion", "main"),
    "monitor": ("monitor_fase1", "main"),
    "crear_perfil": ("crear_perfil", "main"),
    "planes": ("planes_energia", "main"),
    "guardia": ("guardia_termico", "main"),
    "aviso": ("aviso_termico", "main"),
}


def ejecutar(nombre, args):
    entrada = HERRAMIENTAS.get(nombre)
    if entrada is None:
        sys.stderr.write(f"Herramienta desconocida: {nombre}\n")
        sys.stderr.write("Disponibles: " + ", ".join(sorted(HERRAMIENTAS)) + "\n")
        raise SystemExit(2)
    modulo, funcion = entrada
    sys.argv = [nombre] + list(args)
    m = __import__(modulo)
    getattr(m, funcion)()
