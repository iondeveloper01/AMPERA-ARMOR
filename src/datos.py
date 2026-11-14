"""Lectura, validacion y escritura segura de los JSON de AMPERA.

Un archivo truncado, con la raiz equivocada o con un valor del tipo equivocado
no puede tumbar la aplicacion: se devuelve un valor por defecto, el archivo
danado se aparta en logs/ (no se borra) y la app sigue funcionando. Todas las
escrituras usan temp + os.replace para que un corte de luz no deje un JSON a
medias.
"""
import json
import os
import time

# Nombre de ejecutable aceptable para taskkill: sin rutas, comodines ni opciones
# (un perfil manipulado no puede convertir el nombre en un argumento extra).
_CARACTERES_PROHIBIDOS = set('\\/:*?"<>|')


def dic(valor):
    """El valor si es un diccionario; si no, uno vacio."""
    return valor if isinstance(valor, dict) else {}


def texto(valor, defecto=""):
    """Cadena ya recortada, o `defecto` si esta vacia o no es texto."""
    if isinstance(valor, str):
        limpio = valor.strip()
        if limpio:
            return limpio
    return defecto


def entero(valor, defecto, minimo=None, maximo=None):
    """Entero acotado; `defecto` si no se puede convertir."""
    try:
        n = int(valor)
    except (TypeError, ValueError, OverflowError):
        return defecto
    if minimo is not None:
        n = max(minimo, n)
    if maximo is not None:
        n = min(maximo, n)
    return n


def lista_textos(valor):
    """Lista de cadenas recortadas y no vacias; [] si no es una lista."""
    if not isinstance(valor, list):
        return []
    return [x.strip() for x in valor if isinstance(x, str) and x.strip()]


def nombre_app(valor):
    """Nombre de ejecutable seguro para taskkill, o "" si no sirve."""
    s = texto(valor)
    if not s or len(s) > 120 or s.startswith(("/", "-")):
        return ""
    if any(c in _CARACTERES_PROHIBIDOS or ord(c) < 32 for c in s):
        return ""
    return s


def perfil(crudo):
    """Configuracion de un perfil saneada, conservando claves desconocidas."""
    c = dic(crudo)
    limpio = dict(c)
    limpio["plan"] = texto(c.get("plan"), "alto")
    limpio["cpu_max"] = entero(c.get("cpu_max"), 100, 5, 100)
    limpio["cerrar"] = [n for n in (nombre_app(x) for x in lista_textos(c.get("cerrar"))) if n]
    limpio["prioridad"] = texto(c.get("prioridad"), "normal").lower()
    if limpio["prioridad"] not in ("alta", "normal", "baja"):
        limpio["prioridad"] = "normal"
    for clave in ("nota", "fans", "xtu", "exe"):
        limpio[clave] = texto(c.get(clave))
    return limpio


def perfiles(crudo):
    """Diccionario nombre -> perfil saneado, descartando entradas invalidas."""
    limpios = {}
    for nombre, cfg in dic(crudo).items():
        if isinstance(nombre, str) and nombre.strip() and isinstance(cfg, dict):
            limpios[nombre] = perfil(cfg)
    return limpios


def perfiles_defecto():
    """Perfil minimo de fabrica, para arrancar aunque no haya ningun archivo.

    La nota va vacia a proposito: la GUI la escribe en el idioma elegido."""
    return {
        "fabrica": perfil({
            "plan": "alto",
            "cpu_max": 100,
            "cerrar": [],
            "fans": "",
            "exe": "",
            "prioridad": "alta",
            "nota": "",
        })
    }


def leer(ruta, defecto=None):
    """Devuelve (datos, error). Un archivo ausente no es un error."""
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return defecto, None
    except Exception as e:
        return defecto, e


def cuarentena(ruta, carpeta_logs):
    """Aparta un archivo ilegible con marca de tiempo; devuelve la ruta nueva.

    Nunca borra: el usuario puede recuperar a mano lo que hubiera dentro.
    """
    if not os.path.exists(ruta):
        return None
    marca = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.splitext(os.path.basename(ruta))[0]
    destino = os.path.join(carpeta_logs, f"{base}.danado-{marca}.json")
    try:
        os.makedirs(carpeta_logs, exist_ok=True)
        os.replace(ruta, destino)
        return destino
    except Exception:
        return None


def escribir(ruta, datos_json):
    """Escritura atomica. Lanza la excepcion si no pudo."""
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    tmp = ruta + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos_json, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ruta)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
