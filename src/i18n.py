"""Pack de idiomas de NitroTuner.

Carga catalogos JSON desde la carpeta `idiomas/` (uno por idioma) y expone:
  t(clave, **fmt)  -> texto traducido (con .format opcional)
  usar(codigo)     -> cambia el idioma activo
  inicializar()    -> lee el idioma guardado en config.json (o el del sistema)

Diseno sencillo a proposito: si falta una clave en el idioma activo, se usa la
del espanol (idioma base); si falta tambien ahi, se devuelve la propia clave,
asi nunca revienta la GUI por una traduccion ausente.
"""
import json
import os

from rutas import data_path, resource_path
import datos as datos_json

IDIOMAS_DIR = resource_path("idiomas")
CONFIG = data_path("config.json")

# Codigo -> nombre nativo (se muestra tal cual en el selector, sin traducir).
IDIOMAS = {
    "es": "Español",
    "en": "English",
    "zh": "中文",
    "fr": "Français",
}
BASE_LANG = "es"
_actual = BASE_LANG
_catalogo = {}


def _ruta(codigo):
    return os.path.join(IDIOMAS_DIR, f"{codigo}.json")


def _cargar(codigo):
    if codigo in _catalogo:
        return _catalogo[codigo]
    try:
        with open(_ruta(codigo), encoding="utf-8") as f:
            _catalogo[codigo] = json.load(f)
    except Exception:
        _catalogo[codigo] = {}
    return _catalogo[codigo]


def usar(codigo):
    """Activa un idioma si existe su pack."""
    global _actual
    if codigo in IDIOMAS:
        _cargar(codigo)
        _actual = codigo
    return _actual


def idioma_actual():
    return _actual


def nombre_actual():
    return IDIOMAS.get(_actual, _actual)


def t(clave, **fmt):
    """Traduce `clave`. Aplica .format(**fmt) solo si se pasan argumentos."""
    texto = _cargar(_actual).get(clave)
    if texto is None:
        texto = _cargar(BASE_LANG).get(clave, clave)
    if fmt:
        try:
            texto = texto.format(**fmt)
        except Exception:
            pass
    return texto


def leer_config():
    crudo, _ = datos_json.leer(CONFIG, {})
    return datos_json.dic(crudo)


def guardar_idioma(codigo):
    cfg = leer_config()
    cfg["idioma"] = codigo
    try:
        datos_json.escribir(CONFIG, cfg)
    except Exception:
        pass


def _idioma_sistema():
    """Mejor esfuerzo: deduce el idioma de Windows y lo mapea a un pack."""
    try:
        import ctypes
        loc = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        pref = {0x040A: "es", 0x080A: "es", 0x0409: "en", 0x0804: "zh",
                0x040C: "fr", 0x0C0C: "fr", 0x100C: "fr"}
        return pref.get(loc)
    except Exception:
        return None


def inicializar():
    """Idioma guardado > idioma del sistema > espanol."""
    cfg = leer_config()
    codigo = cfg.get("idioma")
    if codigo not in IDIOMAS:
        codigo = _idioma_sistema() or BASE_LANG
    usar(codigo)
    return _actual
