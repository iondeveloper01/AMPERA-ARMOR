"""Estilos del overlay de la sesión de juego.

Un "estilo" es un conjunto de colores + fuente + tamaño + opacidad con el que se
pinta el overlay sobre el juego. Las LETRAS (rótulos GPU/CPU/RAM/COMMIT) usan un
color y los NÚMEROS (las lecturas) otro, y los números cambian a aviso/crítico
cuando una temperatura sube.

Hay estilos de serie (PRESETS) y estilos que cada usuario crea a su gusto, que se
guardan en `overlay_estilos.json` en la carpeta de datos escribible (DATOS: junto
al exe si es portable, si no %LOCALAPPDATA%\\AMPERA). Este módulo lo usan tanto
`sesion.py` (para pintar) como `app.py` (selector + editor).

Diseño a prueba de fallos: si falta una clave se usa la del estilo por defecto; si
el JSON está corrupto se ignora y se vuelve a los presets. Nunca revienta.
"""
import json
import os

from rutas import data_path
import datos as datos_json

RUTA = data_path("overlay_estilos.json")

# Claves y valores por defecto de un estilo. Todo estilo se "normaliza" contra
# esto para que nunca falte un campo aunque el JSON lo traiga incompleto.
DEFECTO = {
    "nombre": "Verde clásico",
    "fondo": "#000000",      # fondo del overlay
    "texto": "#5fd08a",      # color de las LETRAS (rótulos)
    "numeros": "#b6ffcf",    # color de los NÚMEROS cuando todo va bien
    "aviso": "#e8c15a",      # números cuando una temp es alta
    "critico": "#ff6b5e",    # números cuando una temp es crítica
    "pista": "#888888",      # texto de la línea de atajos
    "acento": "#00b4d8",     # filo y barra de la tarjeta
    "fuente": "Consolas",
    "tamano": 12,
    "opacidad": 0.88,        # 0.3 (casi transparente) .. 1.0 (opaco)
}

# Posiciones de la tarjeta: anclas a los bordes/mitades de la pantalla, o "libre"
# cuando el usuario la arrastró. El margen se aplica a las anclas.
POSICIONES = ("arriba-izquierda", "arriba", "arriba-derecha",
              "izquierda", "centro", "derecha",
              "abajo-izquierda", "abajo", "abajo-derecha", "libre")

VENTANA_DEFECTO = {
    "ancla": "arriba-izquierda",
    "margen": 16,
    "x": 16,
    "y": 16,
    "monitor": 0,
}

# Estilos de serie. El primero es el que se usa si no hay ninguno activo.
PRESETS = {
    "Verde clásico": {
        "fondo": "#000000", "texto": "#5fd08a", "numeros": "#b6ffcf",
        "aviso": "#e8c15a", "critico": "#ff6b5e", "pista": "#888888",
        "acento": "#5fd08a", "fuente": "Consolas", "tamano": 12, "opacidad": 0.88,
    },
    "Cian neón": {
        "fondo": "#05080f", "texto": "#7fe9ff", "numeros": "#00e5ff",
        "aviso": "#ffd24a", "critico": "#ff4d6d", "pista": "#5f7d8c",
        "acento": "#00e5ff", "fuente": "Consolas", "tamano": 13, "opacidad": 0.90,
    },
    "Ámbar retro": {
        "fondo": "#0a0703", "texto": "#ffb347", "numeros": "#ffd27f",
        "aviso": "#ff8c1a", "critico": "#ff3b30", "pista": "#8a7350",
        "acento": "#ffb347", "fuente": "Consolas", "tamano": 12, "opacidad": 0.88,
    },
    "Blanco limpio": {
        "fondo": "#101418", "texto": "#e6edf3", "numeros": "#ffffff",
        "aviso": "#f0c000", "critico": "#ff5c5c", "pista": "#9aa4b2",
        "acento": "#00b4d8", "fuente": "Segoe UI", "tamano": 12, "opacidad": 0.92,
    },
    "Rosa gamer": {
        "fondo": "#12060f", "texto": "#ff7ad9", "numeros": "#ff9de2",
        "aviso": "#ffc857", "critico": "#ff2e63", "pista": "#9c6b8a",
        "acento": "#ff7ad9", "fuente": "Bahnschrift", "tamano": 13, "opacidad": 0.90,
    },
}

FUENTES = ["Consolas", "Bahnschrift", "Bahnschrift SemiBold", "Segoe UI",
           "Segoe UI Variable Display", "Arial", "Arial Rounded MT Bold",
           "Impact", "Lucida Console", "Verdana", "Tahoma", "Trebuchet MS",
           "Calibri", "Candara", "Franklin Gothic Medium", "Century Gothic",
           "Cascadia Code", "Cascadia Mono", "Courier New"]

_datos = None  # caché: {"activo": nombre, "personalizados": {nombre: estilo}}


def _normalizar(estilo, nombre=None):
    """Devuelve una copia completa del estilo, rellenando lo que falte."""
    base = dict(DEFECTO)
    if isinstance(estilo, dict):
        base.update({k: v for k, v in estilo.items() if k in DEFECTO})
    if nombre:
        base["nombre"] = nombre
    base.setdefault("nombre", nombre or DEFECTO["nombre"])
    try:
        base["tamano"] = max(8, min(28, int(base["tamano"])))
    except Exception:
        base["tamano"] = DEFECTO["tamano"]
    try:
        base["opacidad"] = max(0.3, min(1.0, float(base["opacidad"])))
    except Exception:
        base["opacidad"] = DEFECTO["opacidad"]
    return base


def _normalizar_ventana(v):
    base = dict(VENTANA_DEFECTO)
    if isinstance(v, dict):
        base.update({k: v[k] for k in VENTANA_DEFECTO if k in v})
    if base["ancla"] not in POSICIONES:
        base["ancla"] = VENTANA_DEFECTO["ancla"]
    for clave in ("margen", "x", "y", "monitor"):
        try:
            base[clave] = int(base[clave])
        except Exception:
            base[clave] = VENTANA_DEFECTO[clave]
    base["margen"] = max(0, min(400, base["margen"]))
    base["monitor"] = max(0, base["monitor"])
    return base


def _leer():
    global _datos
    if _datos is not None:
        return _datos
    try:
        with open(RUTA, encoding="utf-8") as f:
            crudo = json.load(f)
        activo = crudo.get("activo")
        pers = crudo.get("personalizados")
        _datos = {
            "activo": activo if isinstance(activo, str) else None,
            "personalizados": pers if isinstance(pers, dict) else {},
            "ventana": _normalizar_ventana(crudo.get("ventana")),
        }
    except Exception:
        _datos = {"activo": None, "personalizados": {},
                  "ventana": dict(VENTANA_DEFECTO)}
    return _datos


def _escribir():
    try:
        datos_json.escribir(RUTA, _leer())
    except Exception:
        pass


def listar():
    """Nombres de todos los estilos disponibles: presets + los del usuario."""
    d = _leer()
    nombres = list(PRESETS.keys())
    for n in d["personalizados"]:
        if n not in nombres:
            nombres.append(n)
    return nombres


def es_personalizado(nombre):
    return nombre in _leer()["personalizados"]


def obtener(nombre=None):
    """Devuelve el estilo normalizado. nombre=None => el activo (o el 1º preset)."""
    d = _leer()
    if nombre is None:
        nombre = d["activo"] or (list(PRESETS.keys())[0])
    if nombre in d["personalizados"]:
        return _normalizar(d["personalizados"][nombre], nombre)
    if nombre in PRESETS:
        return _normalizar(PRESETS[nombre], nombre)
    return _normalizar(PRESETS[list(PRESETS.keys())[0]], nombre)


def nombre_activo():
    d = _leer()
    n = d["activo"]
    if n and (n in PRESETS or n in d["personalizados"]):
        return n
    return list(PRESETS.keys())[0]


def fijar_activo(nombre):
    if nombre in listar():
        _leer()["activo"] = nombre
        _escribir()
    return nombre_activo()


def guardar_personalizado(nombre, estilo):
    """Crea/actualiza un estilo del usuario y lo deja activo. Devuelve el nombre."""
    nombre = (nombre or "").strip()
    if not nombre:
        nombre = "Mi estilo"
    d = _leer()
    # No dejar que un estilo personal pise el nombre de un preset: se guarda igual
    # (los personalizados tienen prioridad al obtener), pero así el usuario decide.
    d["personalizados"][nombre] = _normalizar(estilo, nombre)
    d["activo"] = nombre
    _escribir()
    return nombre


def borrar_personalizado(nombre):
    d = _leer()
    if nombre in d["personalizados"]:
        del d["personalizados"][nombre]
        if d["activo"] == nombre:
            d["activo"] = None
        _escribir()
        return True
    return False


def ventana():
    """Posición guardada de la tarjeta del overlay."""
    return dict(_leer()["ventana"])


def guardar_ventana(ancla=None, x=None, y=None, monitor=None, margen=None):
    """Actualiza solo los campos indicados y guarda."""
    v = _leer()["ventana"]
    if ancla is not None:
        if ancla not in POSICIONES:
            ancla = VENTANA_DEFECTO["ancla"]
        v["ancla"] = ancla
    if x is not None:
        v["x"] = int(x)
    if y is not None:
        v["y"] = int(y)
    if monitor is not None:
        v["monitor"] = max(0, int(monitor))
    if margen is not None:
        v["margen"] = max(0, min(400, int(margen)))
    _escribir()
    return dict(v)


def fijar_ancla(ancla):
    """Vuelve a una ancla de fábrica y olvida la posición libre."""
    if ancla not in POSICIONES:
        ancla = VENTANA_DEFECTO["ancla"]
    v = _leer()["ventana"]
    v["ancla"] = ancla
    if ancla != "libre":
        v["x"] = VENTANA_DEFECTO["x"]
        v["y"] = VENTANA_DEFECTO["y"]
    _escribir()
    return dict(v)
