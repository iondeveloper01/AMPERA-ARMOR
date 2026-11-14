"""Resolucion de rutas, consciente de frozen (PyInstaller) y de si la carpeta
del .exe es escribible.

Dos raices:
  BASE  = SOLO-LECTURA. El .exe, _internal/, idiomas/, tools/ y los scripts .py.
          Se instala en Program Files (no escribible por un usuario normal).
  DATOS = ESCRIBIBLE. config.json, perfiles_*.json, overlay_estilos.json y logs/.

Regla (auto-detect):
  * Si la carpeta del .exe ES escribible (zip portable / desarrollo en fuente)
    -> DATOS = BASE. Todo sigue junto al .exe, como siempre; el portable
    continua autocontenido y el comportamiento de desarrollo no cambia.
  * Si NO es escribible (instalado en Program Files)
    -> DATOS = %LOCALAPPDATA%\\AMPERA (fallback %APPDATA%\\AMPERA).
    La primera vez se SIEMBRAN/MIGRAN los JSON que existan junto al .exe, para
    que el usuario no pierda perfiles, estilos ni configuracion.
"""
import os
import shutil
import sys

APP_DIR_NAME = "AMPERA"

# Archivos de datos (escribibles) que se siembran/migran desde BASE hacia DATOS.
_ARCHIVOS_DATOS = (
    "config.json",
    "perfiles_juegos.json",
    "perfiles_certificados.json",
    "overlay_estilos.json",
)


def _base():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = _base()


def _es_escribible(d):
    """True si se puede crear la carpeta y escribir/borrar un archivo de prueba."""
    try:
        os.makedirs(d, exist_ok=True)
        prueba = os.path.join(d, ".__w_test__")
        with open(prueba, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(prueba)
        return True
    except Exception:
        return False


def _carpeta_usuario():
    """Carpeta escribible del usuario: %LOCALAPPDATA%\\AMPERA (fallback %APPDATA%)."""
    for var in ("LOCALAPPDATA", "APPDATA"):
        root = os.environ.get(var)
        if root:
            cand = os.path.join(root, APP_DIR_NAME)
            if _es_escribible(cand):
                return cand
    cand = os.path.join(os.path.expanduser("~"), "." + APP_DIR_NAME)
    _es_escribible(cand)
    return cand


def _datos():
    # Auto-detect: si la carpeta del .exe es escribible, los datos viven ahi
    # (portable/desarrollo). Si no, van a la carpeta del usuario.
    if _es_escribible(BASE):
        return BASE
    return _carpeta_usuario()


DATOS = _datos()


def _sembrar():
    """Copia los JSON de datos desde BASE a DATOS cuando faltan en DATOS.

    Solo hace algo si DATOS != BASE (instalado en Program Files): los perfiles,
    estilos y config que venian junto al .exe aparecen la primera vez y el
    usuario no los pierde. Es idempotente: NUNCA pisa un archivo ya existente.
    """
    if DATOS == BASE:
        return
    try:
        os.makedirs(DATOS, exist_ok=True)
    except Exception:
        return
    for nombre in _ARCHIVOS_DATOS:
        origen = os.path.join(BASE, nombre)
        destino = os.path.join(DATOS, nombre)
        if not os.path.exists(destino) and os.path.exists(origen):
            try:
                shutil.copy2(origen, destino)
            except Exception:
                pass


_sembrar()


def data_path(nombre):
    """Ruta ESCRIBIBLE de un archivo de datos (config / perfiles / estilos)."""
    return os.path.join(DATOS, nombre)


def resource_path(*partes):
    """Ruta de SOLO-LECTURA junto al .exe (idiomas/, tools/, scripts .py)."""
    return os.path.join(BASE, *partes)


def logs():
    """Carpeta de logs escribible (DATOS/logs), creada si no existe."""
    d = os.path.join(DATOS, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d
