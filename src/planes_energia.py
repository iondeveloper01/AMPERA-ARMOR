"""Los planes (combinaciones) de energia que existen en ESTE equipo.

Antes la app llevaba una lista fija de tres planes escrita a mano, asi que un
plan creado por el usuario en Windows era invisible. Aqui se leen los planes
reales con `powercfg /list`.

Reglas de diseno (importan, son la razon de que este modulo sea pequeno):
  * Ninguna funcion lanza excepciones. Si powercfg no responde, se devuelve la
    lista minima de siempre y la app hace exactamente lo que hacia antes.
  * No se depende del idioma: solo se busca el GUID y el nombre entre
    parentesis, que es la unica parte de la linea que powercfg escribe igual
    en espanol, ingles, chino o frances.
  * La lectura se cachea un par de segundos para no lanzar un powercfg por
    cada etiqueta de la GUI.
"""
import re
import subprocess
import time

# Alias historicos: asi un perfil guardado con "alto" o "maximo" sigue
# funcionando aunque nadie escriba nunca un GUID en el JSON.
ALIAS = {
    "equilibrado": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "alto": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "maximo": "e9a42b02-d5df-448d-aa00-03f14749eb61",
}
_ALIAS_POR_GUID = {g: a for a, g in ALIAS.items()}

# Oculta la consola al lanzar powercfg cuando la GUI esta congelada en .exe.
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_RE_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_RE_NOMBRE = re.compile(r"\((.+)\)")

CADUCIDAD = 2.0  # segundos que vale la ultima lectura
_cache = {"momento": 0.0, "planes": []}
_cache_activo = {"momento": 0.0, "guid": None}


def _codigos():
    """Codigos que puede estar usando powercfg, del mas probable al ultimo.

    Escribe en la codepage OEM del sistema (cp850 en espanol, cp936 en chino),
    no en UTF-8. Empezar por la del propio equipo es lo que hace que un plan
    llamado "高性能" se lea bien y no como basura."""
    codigos = []
    try:
        import ctypes
        codigos.append("cp%d" % ctypes.windll.kernel32.GetOEMCP())
    except Exception:
        pass
    try:
        import locale
        codigos.append(locale.getpreferredencoding(False))
    except Exception:
        pass
    codigos += ["utf-8", "cp850", "cp1252", "latin-1"]
    return codigos


def _decodificar(bloque):
    """Bytes de powercfg -> texto. La ultima prueba (latin-1) nunca falla."""
    for cod in _codigos():
        try:
            return bloque.decode(cod)
        except Exception:
            continue
    return ""


def _salida(args):
    """Texto de `powercfg <args>` o None. Nunca lanza."""
    try:
        r = subprocess.run(["powercfg", *args], capture_output=True, timeout=12,
                           creationflags=_SIN_VENTANA)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return _decodificar(r.stdout or b"")


def _analizar(texto):
    """Lineas de `powercfg /list` -> [{'guid', 'nombre', 'activo'}, ...].

    El asterisco al final de la linea es el plan en uso, y va despues del
    parentesis de cierre, igual en todos los idiomas."""
    planes = []
    for linea in (texto or "").splitlines():
        m = _RE_GUID.search(linea)
        if not m:
            continue
        resto = linea[m.end():]
        n = _RE_NOMBRE.search(resto)
        if not n:
            continue
        planes.append({
            "guid": m.group(0).lower(),
            "nombre": n.group(1).strip(),
            "activo": "*" in resto[n.end():],
        })
    return planes


def _de_alias():
    """Lista de respaldo con los tres planes de siempre, para el peor caso."""
    return [{"guid": g.lower(), "nombre": a, "activo": False}
            for a, g in ALIAS.items()]


def listar(forzar=False):
    """Todos los planes del equipo. Si no hay lista, devuelve la minima.

    Cada plan trae ademas 'alias' (el nombre corto de toda la vida) cuando
    Windows usa uno de los planes originales de Microsoft."""
    ahora = time.monotonic()
    if not forzar and _cache["planes"] and (ahora - _cache["momento"]) < CADUCIDAD:
        return [dict(p) for p in _cache["planes"]]
    planes = _analizar(_salida(["/list"]))
    if not planes:
        planes = _de_alias()
    for p in planes:
        p["alias"] = _ALIAS_POR_GUID.get(p["guid"])
    _cache["momento"] = ahora
    _cache["planes"] = planes
    return [dict(p) for p in planes]


def por_guid(guid):
    """El plan cuyo GUID coincida (da igual mayusculas o llaves), o None."""
    if not guid:
        return None
    limpio = str(guid).strip().strip("{}").lower()
    for p in listar():
        if p["guid"] == limpio:
            return p
    return None


def invalisar():
    """Olvida lo leido: la proxima lectura vuelve a preguntar a Windows.

    Hay que llamarlo despues de cambiar el plan, si no la GUI mostraria el plan
    que estaba en uso antes del cambio."""
    _cache["momento"] = 0.0
    _cache_activo["momento"] = 0.0


def nombre_si_existe(valor):
    """Nombre legible del plan guardado en un perfil, o None si ya no esta.

    Sirve para los carteles: el usuario ve "Acer", no un codigo de 36 simbolos,
    y si borro ese plan en Windows se lo decimos en vez de mentirle."""
    texto = str(valor or "").strip().strip("{}").lower()
    if texto in ALIAS:
        # un perfil viejo dice "alto": hay que traducirlo a su GUID para poder
        # buscar como se llama ese plan en ESTE Windows
        texto = ALIAS[texto]
    p = por_guid(texto)
    if p:
        return p["nombre"]
    if texto:
        for p in listar():
            if p["nombre"].strip().lower() == texto:
                return p["nombre"]
    return None


def resolver(valor):
    """Convierte lo que guardo un perfil en (guid, nombre).

    Acepta un GUID, uno de los alias antiguos ("alto") o el nombre tal como lo
    muestra Windows ("Mi plan turbo"). Si nada coincide, cae en el plan de alto
    rendimiento y, si ese tampoco esta, en cualquiera que exista: el objetivo es
    que aplicar un perfil nunca se quede sin plan que poner.
    """
    texto = str(valor or "").strip().strip("{}").lower()
    if texto in ALIAS:
        texto = ALIAS[texto]
    planos = listar()
    for p in planos:
        if p["guid"] == texto or p["nombre"].strip().lower() == texto:
            return p["guid"], p["nombre"]
    for respaldo in (ALIAS["alto"], ALIAS["equilibrado"]):
        for p in planos:
            if p["guid"] == respaldo.lower():
                return p["guid"], p["nombre"]
    if planos:
        return planos[0]["guid"], planos[0]["nombre"]
    return None, None


def activo():
    """(guid, nombre) del plan en uso ahora mismo, o (None, None)."""
    p = por_guid(_guid_activo())
    if p:
        return p["guid"], p["nombre"]
    return None, None


def _guid_activo():
    ahora = time.monotonic()
    if (ahora - _cache_activo["momento"]) < CADUCIDAD:
        return _cache_activo["guid"]
    guid = None
    m = _RE_GUID.search(_salida(["/getactivescheme"]) or "")
    if m:
        guid = m.group(0).lower()
    _cache_activo["momento"] = ahora
    _cache_activo["guid"] = guid
    return guid


_RE_HEX = re.compile(r"0x([0-9a-fA-F]+)")


def leer_ajuste(subgrupo, ajuste):
    """Valor de CA (0-100) de un ajuste de energia, sin depender del idioma.

    `powercfg -q` traduce las etiquetas, pero los hexadecimales van siempre en
    el mismo orden: minimo, maximo, incremento, CA y CC. El de CA es el
    penultimo. Antes se buscaba "alterna" (espanol) o "current ac" (ingles), asi
    que en un Windows frances o chino no se leia el techo del usuario y se
    restauraba 100 % por error.
    """
    salida = _salida(["-q", "SCHEME_CURRENT", subgrupo, ajuste])
    if not salida:
        return None
    valores = [int(v, 16) for v in _RE_HEX.findall(salida)]
    if len(valores) < 2:
        return None
    ac = valores[-2]
    return ac if 0 <= ac <= 100 else None


def main():
    """Herramienta de consola (NitroTunerCLI --tool planes): lista los planes sin
    cambiar nada. Sirve para comprobar que AMPERA ve el plan que creaste."""
    import sys
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    planos = listar(forzar=True)
    _, nombre = activo()
    print("Planes de energia de este equipo (%d):" % len(planos))
    for p in planos:
        print("  - %-28s %s%s" % (p["nombre"], p["guid"],
                                  "  <--- en uso" if p["activo"] else ""))
    if not any(p["activo"] for p in planos):
        print("En uso: %s" % (nombre or "(no se pudo leer)"))
    return 0


if __name__ == "__main__":
    main()
