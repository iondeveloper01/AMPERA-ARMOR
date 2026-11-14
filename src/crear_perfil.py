"""Asistente para crear TUS propios perfiles de NitroTuner, en consola.

Se abre desde la GUI (boton "+ Crear perfil") en su propia ventana cmd/PowerShell,
o directamente con:  py crear_perfil.py

NO necesita administrador: cambiar el plan de energia y el techo de CPU con
powercfg funciona con usuario normal (verificado en este equipo). Guarda el
perfil nuevo en perfiles_juegos.json (reordenado A-Z) y, si quieres, lo aplica
en el momento reutilizando perfil_juego.aplicar().
"""
import os
import sys

from rutas import data_path
import datos as datos_json
RUTA_CFG = data_path("perfiles_juegos.json")

from perfil_juego import aplicar, nombres_ordenados  # noqa: E402
import planes_energia  # noqa: E402


def pedir(texto, por_defecto=""):
    sufijo = f" [{por_defecto}]" if por_defecto else ""
    s = input(f"{texto}{sufijo}: ").strip()
    return s if s else por_defecto


def leer_perfiles():
    crudo, error = datos_json.leer(RUTA_CFG, {})
    if error is not None:
        print(f"[AVISO] No pude leer perfiles_juegos.json ({error}); empiezo de cero.")
    return datos_json.perfiles(crudo)


def guardar(perfiles):
    ordenado = {n: perfiles[n] for n in nombres_ordenados(perfiles)}
    datos_json.escribir(RUTA_CFG, ordenado)


def elegir_plan():
    """Muestra los planes de energia que hay en ESTE equipo y guarda el GUID.

    Asi un plan creado a mano por el usuario (NitroSense, Acer, "mi turbo")
    aparece en la lista sin escribir nada raro. Si Windows no responde, la
    lista de respaldo trae los tres planes de siempre.
    """
    planes = planes_energia.listar(forzar=True)
    print("Plan de energia (los que tiene tu Windows):")
    for i, p in enumerate(planes, 1):
        marca = "   <-- en uso ahora" if p["activo"] else ""
        print(f"  {i}. {p['nombre']}{marca}")
    por_defecto = 1
    for i, p in enumerate(planes, 1):
        if p["activo"]:
            por_defecto = i
            break
    while True:
        s = input(f"Elige un numero [1-{len(planes)}] "
                  f"(Enter = {planes[por_defecto - 1]['nombre']}): ").strip()
        if not s:
            return planes[por_defecto - 1]["guid"]
        if s.isdigit() and 1 <= int(s) <= len(planes):
            return planes[int(s) - 1]["guid"]
        print("  Numero no valido.")


def elegir_prioridad():
    while True:
        s = input("Prioridad del juego: alta / normal / baja (Enter = normal): ").strip().lower()
        if not s:
            return "normal"
        if s in ("alta", "normal", "baja"):
            return s
        print("  Usa: alta, normal o baja.")


def elegir_cpu_max():
    while True:
        s = input("Techo de CPU en % (5-100, 100 = sin limite) (Enter = 100): ").strip()
        if not s:
            return 100
        if s.isdigit() and 5 <= int(s) <= 100:
            return int(s)
        print("  Escribe un numero entero entre 5 y 100.")


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("=" * 62)
    print("   NitroTuner - Crear un perfil nuevo (no requiere administrador)")
    print("=" * 62)
    perfiles = leer_perfiles()

    nombre = "".join(pedir("Nombre del perfil (sin espacios, ej. mi_juego)").split())
    if not nombre:
        print("[ERROR] El nombre no puede estar vacio. Cancelado.")
        input("\nPulsa ENTER para cerrar...")
        return
    if nombre in perfiles:
        r = input(f"Ya existe '{nombre}'. Lo sobrescribo? (s/n): ").strip().lower()
        if r != "s":
            print("Cancelado: no se toco nada.")
            input("\nPulsa ENTER para cerrar...")
            return
    print()

    plan = elegir_plan()
    cpu_max = elegir_cpu_max()
    prioridad = elegir_prioridad()

    cerrar = []
    s = input("Apps a cerrar al aplicar (.exe separados por coma, Enter = ninguna): ").strip()
    if s:
        cerrar = [x.strip() for x in s.split(",") if x.strip()]

    fans = pedir("Aviso de ventiladores (Auto/Max; se pone a mano en NitroSense)", "Auto")

    exe = ""
    s = input("Ruta del .exe del juego (Enter = no arrancar nada): ").strip().strip('"')
    if s:
        exe = s
        if not os.path.exists(exe):
            print(f"  [AVISO] No encuentro '{exe}'. Lo guardo, pero no podre arrancarlo.")

    nota = pedir("Nota / descripcion del perfil", f"Perfil personalizado {nombre}")

    cfg = {
        "plan": plan,
        "cpu_max": cpu_max,
        "cerrar": cerrar,
        "fans": fans,
        "exe": exe,
        "prioridad": prioridad,
        "nota": nota,
    }
    perfiles[nombre] = cfg
    try:
        guardar(perfiles)
    except Exception as e:
        print(f"[ERROR] No pude guardar el perfil: {e}")
        input("\nPulsa ENTER para cerrar...")
        return

    print(f"\n[OK] Perfil '{nombre}' guardado en perfiles_juegos.json (orden A-Z).")
    print("     Resumen:")
    for k, v in cfg.items():
        if k == "plan":
            # el JSON guarda el GUID (es lo unico que entiende powercfg), pero al
            # usuario se le muestra el nombre de su plan, no un codigo larguisimo
            print(f"       plan: {planes_energia.nombre_si_existe(v) or v}")
        else:
            print(f"       {k}: {v}")

    r = input("\nQuieres aplicarlo AHORA? (s/n): ").strip().lower()
    if r == "s":
        print()
        try:
            aplicar(nombre, cfg)
        except Exception as e:
            print(f"[ERROR] No pude aplicar el perfil: {e}")
    else:
        print("Listo. Podras aplicarlo desde la GUI (boton Aplicar) cuando quieras.")

    input("\nPulsa ENTER para cerrar esta ventana...")


if __name__ == "__main__":
    main()
