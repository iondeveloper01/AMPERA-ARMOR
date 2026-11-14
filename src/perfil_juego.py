import json
import os
import subprocess
import sys
import unicodedata

from rutas import data_path
import datos as datos_json
import planes_energia
RUTA_CFG = data_path("perfiles_juegos.json")
RUTA_CERTS = data_path("perfiles_certificados.json")
# Los nombres cortos de siempre siguen valiendo, pero la lista real de planes se
# lee del equipo (ver planes_energia.py): asi un plan creado a mano por el
# usuario aparece solo, sin escribir GUIDs en ningun archivo.
PLANES = planes_energia.ALIAS
SUB_CPU = "54533251-82be-4824-96c1-47b60b740d00"
PROC_MAX = "bc5038f7-23e0-4960-96da-33abaf5935ec"
PRIORIDADES = {"alta": "/high", "normal": "/normal", "baja": "/low"}
# Banderas de prioridad de Windows (equivalente a `start /high`, pero sin shell).
_PRIO_FLAGS = {
    "alta": getattr(subprocess, "HIGH_PRIORITY_CLASS", 0x00000080),
    "normal": getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0x00000020),
    "baja": getattr(subprocess, "IDLE_PRIORITY_CLASS", 0x00000040),
}
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(cmd):
    """Ejecuta una orden SIN shell: la lista de argumentos evita que un nombre
    de perfil manipulado se interprete como una orden de cmd.exe."""
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)


def fijar_plan(nombre_plan):
    """Pone el plan de energia y devuelve como quedo (el nombre real de Windows).

    Acepta un GUID, uno de los alias de siempre ("alto") o el nombre tal como lo
    muestra Windows ("Mi plan turbo"). El GUID que se escribe siempre sale de la
    lista del propio equipo, nunca del texto del perfil.
    """
    guid, nombre = planes_energia.resolver(nombre_plan)
    if not guid:
        print("[AVISO] No pude leer los planes de energia; dejo el plan actual.")
        return None
    run(["powercfg", "/setactive", guid])
    planes_energia.invalisar()
    return nombre


def fijar_cpu_max(pct):
    pct = max(5, min(100, int(pct)))
    run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_CPU, PROC_MAX, str(pct)])
    run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB_CPU, PROC_MAX, str(pct)])
    run(["powercfg", "/setactive", "SCHEME_CURRENT"])
    return pct


def restaurar():
    plan = fijar_plan("equilibrado")
    fijar_cpu_max(100)
    print(f"[OK] Modo normal restaurado: plan {plan or '(sin cambiar)'}, CPU al 100 %.")
    print("     Apps cerradas no se reabren solas: ábrelas tú si las necesitas.")


def certificado(nombre):
    """True si el perfil superó la prueba de estrés de certificar.py."""
    crudo, _ = datos_json.leer(RUTA_CERTS, {})
    return bool(datos_json.dic(datos_json.dic(crudo).get(nombre)).get("certificado"))


def nombres_ordenados(perfiles):
    """Nombres de perfil de la A a la Z, sin distinguir mayúsculas ni acentos."""
    def clave(n):
        sin_tildes = "".join(
            c for c in unicodedata.normalize("NFD", n)
            if unicodedata.category(c) != "Mn")
        return sin_tildes.lower()
    return sorted((n for n in perfiles if isinstance(n, str)), key=clave)


def aplicar(nombre, cfg):
    cfg = datos_json.perfil(cfg)
    print(f"=== Aplicando perfil: {nombre} ===")
    if cfg.get("nota"):
        print("   ", cfg["nota"])
    if not certificado(nombre):
        print("[SIN CERTIFICAR] Este perfil aún no superó la prueba de estrés.")
        print("                 Para validarlo: py certificar.py " + nombre)
    plan = fijar_plan(cfg.get("plan", "alto"))
    print(f"[OK] Plan de energia: {plan or '(no pude cambiarlo)'}")
    pct = fijar_cpu_max(cfg.get("cpu_max", 100))
    print(f"[OK] Tope de CPU: {pct} % (rueda de poder)")
    cerrar = cfg.get("cerrar", [])
    if cerrar:
        print("Apps configuradas para cerrar:", ", ".join(cerrar))
        r = input("¿Las cierro ahora? (s/n): ").strip().lower()
        if r == "s":
            for app in cerrar:
                run(["taskkill", "/im", app, "/f"])
                print("   cerrada:", app)
        else:
            print("   ok, no cierro nada.")
    fans = cfg.get("fans")
    if fans:
        print(f"[AVISO MANUAL] Pon NitroSense > Contr. vent. en: {fans}")
    xtu = cfg.get("xtu")
    if xtu:
        print(f"[AVISO MANUAL] XTU: aplica {xtu} (o deja tu perfil guardado)")
    exe = cfg.get("exe", "")
    if exe:
        if os.path.isfile(exe) and exe.lower().endswith(".exe"):
            prioridad = cfg.get("prioridad", "normal")
            print("Arrancando el juego con prioridad", prioridad, "...")
            subprocess.Popen([exe], creationflags=_PRIO_FLAGS.get(prioridad, _PRIO_FLAGS["normal"]))
        else:
            print("[AVISO] No encuentro el exe configurado:", exe)
            print("        Edita perfiles_juegos.json con la ruta correcta, o arranca el juego tú.")
    print()
    print("Perfil aplicado. Para volver a la normalidad: doble clic en modo_normal.bat")


def main():
    crudo, error = datos_json.leer(RUTA_CFG, {})
    if error is not None:
        print("[ERROR] No pude leer perfiles_juegos.json:", error)
        return
    perfiles = datos_json.perfiles(crudo)
    if len(sys.argv) > 1:
        nombre = sys.argv[1]
        if nombre == "restore":
            restaurar()
            return
        if nombre in perfiles:
            aplicar(nombre, perfiles[nombre])
            return
        print("No conozco el perfil:", nombre)
    print("Perfiles disponibles (A-Z):")
    nombres = nombres_ordenados(perfiles)
    for i, n in enumerate(nombres, 1):
        print(f"  {i}. {n}  -  {perfiles[n].get('nota', '')}")
    print(f"  {len(nombres) + 1}. restore  -  volver al modo normal")
    ele = input("Elige un numero: ").strip()
    if ele.isdigit() and 1 <= int(ele) <= len(nombres):
        n = nombres[int(ele) - 1]
        aplicar(n, perfiles[n])
    else:
        restaurar()


if __name__ == "__main__":
    main()
