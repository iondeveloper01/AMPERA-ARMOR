"""Certificador de perfiles — Fase 2 del proyecto "Afterburner propio".

Idea central del diseño acordado: NUNCA confiar en un perfil que no haya
superado una prueba. Este script aplica el perfil, lo somete a una carga
multi-núcleo acotada (unos segundos), vigila GPU (NVML) y CPU (LHM) y:

  * si las temperaturas se mantienen bajo los guardarraíles -> PERFIL CERTIFICADO
    (se guarda un registro con fecha, picos y duración en perfiles_certificados.json);
  * si se pasa del umbral crítico -> AUTO-ROLLBACK inmediato (mata la carga,
    restaura el plan y el techo de CPU originales) y NO certifica.

Al terminar SIEMPRE restaura la configuración previa: certificar es una prueba,
no una sesión de juego. Y si el proceso muere de golpe sin ejecutar ese rollback,
la GUI restaura desde logs/certificar_pendiente.json (ver restaurar_si_pendiente).

En batería el firmware limita el CPU/GPU, así que la prueba no es representativa:
se avisa y solo sigue con --forzar.

Uso:
    py certificar.py aaa_max              # certifica con 30 s de carga
    py certificar.py aaa_max --segundos 45
    py certificar.py --estado             # qué perfiles están certificados
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import pynvml as nvml

from monitor_fase1 import (CPU_CRIT, GPU_CRIT, dato, lhm_activo, temp_cpu_lhm)
from perfil_juego import SUB_CPU, PROC_MAX, fijar_cpu_max, fijar_plan
import planes_energia
import datos as datos_json

from rutas import data_path, logs, resource_path
PERFILES = data_path("perfiles_juegos.json")
CERTS = data_path("perfiles_certificados.json")
TURBO = resource_path("turbo_test.py")

VERDE, AMARILLO, ROJO, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"

# Red de seguridad. Certificar es la herramienta que MÁS peligro tiene si muere sin
# limpiar: deja la laptop con el plan y el techo de CPU del perfil probado (a menudo
# 100 %), no con los tuyos. Como en este equipo un kill duro NO ejecuta el finally,
# anotamos el estado previo en un archivo ANTES de tocar nada y lo borramos al
# restaurar; si la ejecución muere a medias, la GUI (o la siguiente certificación)
# lo lee y devuelve el equipo a su estado original.
RUTA_PENDIENTE = os.path.join(logs(), "certificar_pendiente.json")

# Oculta la consola al lanzar procesos hijo (powercfg/powershell) en Windows,
# para que no destelle una ventana negra. 0 fuera de Windows.
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd):
    """powercfg escribe en la codepage de la consola y en español trae bytes que no
    son UTF-8; si el sistema está en modo UTF-8 el decodeo revienta y stdout queda
    en None (y sin plan previo no habría forma de hacer el rollback). errors="replace"
    hace que el decodeo nunca falle: solo necesitamos los GUID, no el texto."""
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)


def plan_actual():
    """GUID del plan de energía activo (para poder restaurarlo)."""
    out = _run(["powercfg", "/getactivescheme"]).stdout or ""
    for tok in out.replace("\r", " ").split():
        if len(tok) == 36 and tok.count("-") == 4:
            return tok
    return None


def proc_max_actual():
    return planes_energia.leer_ajuste(SUB_CPU, PROC_MAX)


def en_bateria():
    """True si el equipo está descargando (sin corriente AC).

    Usa la API de Windows GetSystemPowerStatus (ctypes) en vez de abrir un
    PowerShell: es instantáneo, no crea subproceso y no destella ninguna ventana
    (la GUI lo llama ~1 vez por segundo)."""
    try:
        import ctypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),        # 0=batería, 1=AC, 255=desconocido
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        st = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return st.ACLineStatus == 0
        return False
    except Exception:
        return False


def _guardar_pendiente(plan, cpu_max):
    """Escribe el estado a restaurar. Usa temp + os.replace para que un kill a
    medias no pueda dejar un JSON truncado que la reparación no pueda leer."""
    try:
        tmp = RUTA_PENDIENTE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"plan": plan, "cpu_max": cpu_max,
                       "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                      f, ensure_ascii=False)
        os.replace(tmp, RUTA_PENDIENTE)
    except Exception:
        pass


def _borrar_pendiente():
    try:
        if os.path.exists(RUTA_PENDIENTE):
            os.remove(RUTA_PENDIENTE)
    except Exception:
        pass


def restaurar_si_pendiente():
    """API para la GUI (y para las certificaciones nuevas): si una ejecución anterior
    murió sin restaurar, devuelve el plan y el techo de CPU guardados.
    Idempotente; True si hubo que reparar.

    NO matamos los procesos de carga por PID: quedan huérfanos al morir el
    certificador, pero cada turbo_test.py se autolimita a sus propios segundos y
    las PID de Windows se reutilizan, así que disparar contra una PID guardada
    podría cerrar una ventana ajena del usuario."""
    try:
        if not os.path.exists(RUTA_PENDIENTE):
            return False
        try:
            with open(RUTA_PENDIENTE, encoding="utf-8") as f:
                datos = json.load(f)
        except Exception:
            _borrar_pendiente()   # archivo ilegible: no hay nada que restaurar
            return False
        plan = datos.get("plan")
        cpu_max = datos.get("cpu_max")
        if plan:
            _run(["powercfg", "/setactive", plan])
        if cpu_max is not None:
            fijar_cpu_max(cpu_max)
        # Solo borramos si el restore no lanzó: si falla, dejamos el archivo para
        # que el próximo arranque lo reintente (protege al equipo de quedarse sin tope).
        _borrar_pendiente()
        # El archivo guarda el GUID; al usuario le hablamos con el nombre de su plan.
        nombre = planes_energia.nombre_si_existe(plan) or plan
        print(f"{AMARILLO}AUTO-REPARACIÓN: una certificación anterior no restauró el estado "
              f"(plan {nombre}, techo CPU {cpu_max if cpu_max is not None else '?'} %). "
              f"Lo devuelvo al original.{RESET}")
        return True
    except Exception:
        return False


def cargar_certs():
    contenido, _ = datos_json.leer(CERTS, {})
    return datos_json.dic(contenido)


def guardar_certs(registros):
    datos_json.escribir(CERTS, datos_json.dic(registros))


def lanzar_carga(segundos, nucleos):
    """Arranca `nucleos` procesos de turbo_test.py y devuelve los Popen."""
    procs = []
    for _ in range(nucleos):
        procs.append(subprocess.Popen(
            [sys.executable, TURBO, str(segundos)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return procs


def matar(procs):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


def certificar(nombre, cfg, segundos):
    if not isinstance(nombre, str) or not nombre.strip():
        print(f"{ROJO}No certifico: el perfil no tiene nombre.{RESET}")
        return False
    try:
        segundos = float(segundos)
    except (TypeError, ValueError):
        segundos = 0.0
    if not 5.0 <= segundos <= 600.0:
        print(f"{ROJO}Duración no válida: {segundos:g} s. Usa entre 5 y 600 segundos.{RESET}")
        return False
    cfg = datos_json.perfil(cfg)

    print(f"{VERDE}=== Certificando perfil: {nombre} ==={RESET}")
    if cfg.get("nota"):
        print("   ", cfg["nota"])

    # Primero reparar lo que pudo dejar colgado una ejecución anterior: si no,
    # el "estado previo" que capturaríamos abajo sería el del perfil muerto,
    # y restauraríamos a ese valor en vez de al tuyo.
    restaurar_si_pendiente()

    if en_bateria():
        print(f"{ROJO}Estás en BATERÍA: el firmware limita CPU/GPU y la prueba no vale.{RESET}")
        print("      Conecta el cargador (BatteryStatus debe ser 2 = AC) y reintenta.")
        return False

    hay_lhm = lhm_activo()
    if not hay_lhm:
        print(f"{AMARILLO}Aviso: LHM no responde en :8085; no podré leer la temp del CPU.{RESET}")
        print("       La certificación vigilará solo la GPU. Arranca LHM para una prueba completa.")

    # NVML puede fallar en un equipo sin GPU NVIDIA o con el driver a medias. Antes
    # se llamaba DESPUÉS de aplicar el perfil y dejaba el techo del perfil puesto
    # hasta la reparación del siguiente arranque; ahora se comprueba antes.
    nvml_listo = False
    h = None
    try:
        nvml.nvmlInit()
        nvml_listo = True
        h = nvml.nvmlDeviceGetHandleByIndex(0)
    except Exception as e:
        print(f"{AMARILLO}Aviso: NVML no disponible ({e}); la GPU no se vigilará.{RESET}")
    if h is None and not hay_lhm:
        print(f"{ROJO}No certifico: no hay ningún sensor de temperatura disponible.{RESET}")
        print("      Arranca el servidor de sensores (:8085) o revisa el driver NVIDIA.")
        return False

    # Guardar estado previo para el auto-rollback
    plan_prev = plan_actual()
    cpu_prev = proc_max_actual()
    print(f"Estado previo: plan {plan_prev}, techo CPU {cpu_prev if cpu_prev is not None else '?'} %")

    # Dejar constancia del estado original ANTES de aplicar el perfil: si este proceso
    # muere sin finally, la GUI restaurará desde este archivo.
    _guardar_pendiente(plan_prev, cpu_prev)

    # Aplicar el perfil
    plan = fijar_plan(cfg.get("plan", "alto"))
    pct = fijar_cpu_max(cfg.get("cpu_max", 100))
    print(f"[OK] Perfil aplicado: plan {plan}, techo CPU {pct} %")

    nucleos = os.cpu_count() or 8
    print(f"Lanzando carga en {nucleos} hilos durante {segundos:.0f} s "
          f"(guardarraíl: GPU <{GPU_CRIT} °C, CPU <{CPU_CRIT} °C)...")

    pico_gpu, pico_cpu = None, None
    fallo = None
    procs = []
    t0 = time.time()
    try:
        procs = lanzar_carga(segundos, nucleos)
        try:
            while time.time() - t0 < segundos:
                time.sleep(2.0)
                gt = (dato(nvml.nvmlDeviceGetTemperature, h,
                           nvml.NVML_TEMPERATURE_GPU) if h is not None else None)
                # Con la carga a tope, LHM puede tardar ~9 s en contestar la
                # primera consulta; con 3 s se perdía justo la muestra inicial.
                ct = temp_cpu_lhm(timeout=12) if hay_lhm else None
                if gt is not None:
                    pico_gpu = gt if pico_gpu is None else max(pico_gpu, gt)
                if ct is not None:
                    pico_cpu = ct if pico_cpu is None else max(pico_cpu, ct)
                trans = time.time() - t0
                color = ROJO if ((gt and gt >= GPU_CRIT) or (ct and ct >= CPU_CRIT)) else VERDE
                print(f"{color}  t+{trans:4.1f}s  GPU {gt if gt is not None else '?'} °C  "
                      f"CPU {ct if ct is not None else 'n/d'} °C{RESET}")
                if gt is not None and gt >= GPU_CRIT:
                    fallo = f"GPU alcanzó {gt} °C (>= {GPU_CRIT})"
                    break
                if ct is not None and ct >= CPU_CRIT:
                    fallo = f"CPU alcanzó {ct} °C (>= {CPU_CRIT})"
                    break
        except Exception as e:
            fallo = f"error durante la prueba: {e}"
    finally:
        matar(procs)
        if nvml_listo:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
        # AUTO-ROLLBACK: siempre volver al estado previo
        if plan_prev:
            _run(["powercfg", "/setactive", plan_prev])
        if cpu_prev is not None:
            fijar_cpu_max(cpu_prev)
        _borrar_pendiente()
        print(f"{AMARILLO}Configuración previa restaurada.{RESET}")

    if fallo:
        print(f"{ROJO}NO CERTIFICADO: {fallo}. Perfil rechazado (auto-rollback aplicado).{RESET}")
        return False

    if pico_gpu is None and pico_cpu is None:
        print(f"{ROJO}NO CERTIFICADO: la prueba no obtuvo ninguna lectura de temperatura.{RESET}")
        return False

    certs = cargar_certs()
    certs[nombre] = {
        "certificado": True,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "segundos": int(round(segundos)),
        "pico_gpu_c": pico_gpu,
        "pico_cpu_c": pico_cpu,
        "plan": plan,
        "cpu_max": pct,
    }
    try:
        guardar_certs(certs)
    except Exception as e:
        print(f"{ROJO}La prueba salió bien, pero no pude guardar el registro: {e}{RESET}")
        return False
    print(f"{VERDE}PERFIL CERTIFICADO. Picos: GPU {pico_gpu if pico_gpu is not None else '?'} °C, "
          f"CPU {pico_cpu if pico_cpu is not None else 'n/d'} °C.{RESET}")
    print(f"Registro guardado en {os.path.basename(CERTS)}")
    return True


def estado():
    perfiles = {}
    crudo, error = datos_json.leer(PERFILES, {})
    if error is not None:
        print(f"{ROJO}No pude leer {os.path.basename(PERFILES)}: {error}{RESET}")
    perfiles = datos_json.perfiles(crudo)
    certs = cargar_certs()
    print(f"{VERDE}=== Estado de certificación ==={RESET}")
    for nombre in perfiles:
        c = datos_json.dic(certs.get(nombre))
        if c.get("certificado"):
            gpu = c.get("pico_gpu_c")
            cpu = c.get("pico_cpu_c")
            print(f"  {VERDE}[CERTIFICADO]{RESET} {nombre:<16} el {c.get('fecha')}  "
                  f"(pico GPU {gpu if gpu is not None else '?'} °C / CPU {cpu if cpu is not None else 'n/d'} °C)")
        else:
            print(f"  {AMARILLO}[SIN CERTIFICAR]{RESET} {nombre}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Certificador de perfiles (fase 2)")
    ap.add_argument("perfil", nargs="?", help="nombre del perfil a certificar")
    ap.add_argument("--segundos", type=float, default=30.0, help="duración de la carga (def. 30)")
    ap.add_argument("--estado", action="store_true", help="mostrar qué perfiles están certificados")
    a = ap.parse_args()

    if a.estado or not a.perfil:
        estado()
        if not a.perfil:
            return

    crudo, error = datos_json.leer(PERFILES, {})
    if error is not None:
        print(f"{ROJO}No pude leer {os.path.basename(PERFILES)}: {error}{RESET}")
        sys.exit(1)
    perfiles = datos_json.perfiles(crudo)
    if a.perfil not in perfiles:
        print(f"{ROJO}No conozco el perfil: {a.perfil}{RESET}")
        print("Disponibles:", ", ".join(perfiles))
        return
    ok = certificar(a.perfil, perfiles[a.perfil], a.segundos)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
