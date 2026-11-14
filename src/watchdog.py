"""Watchdog térmico — Fase 2 del proyecto "Afterburner propio".

Vigila la temperatura de GPU (vía NVML) y de CPU (vía Libre Hardware Monitor).
En este Acer el límite de potencia de la GPU NO se puede escribir por NVML,
pero bajar el estado máximo del CPU (PROCTHROTTLEMAX, vía powercfg) sí funciona
y —como CPU y GPU comparten heatpipes— enfría el chasis y le da margen a la GPU.

Mitigación escalonada con histéresis: si se pasa del umbral, baja el techo del
CPU un escalón; si recupera, lo sube de nuevo. Al salir restaura el valor original.

Incluye override temporal explícito (--override MIN) para las pruebas de límites
del usuario: congela la mitigación durante MIN minutos y luego la reanuda sola.

Sin driver propio, sin instalador: usuario + powercfg + NVML + LHM.
Uso:
    py watchdog.py                 # vigilar con umbrales por defecto
    py watchdog.py --override 10   # ignorar guardarraíles 10 min (prueba)
    py watchdog.py --gpu-warn 80 --cpu-warn 85
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime

import pynvml as nvml

from rutas import logs
import planes_energia

from monitor_fase1 import (CPU_CRIT, CPU_WARN, GPU_CRIT, GPU_WARN,
                           dato, lhm_activo, temp_cpu_lhm)

SUB_CPU = "54533251-82be-4824-96c1-47b60b740d00"
PROC_MAX = "bc5038f7-23e0-4960-96da-33abaf5935ec"
ESCALONES = [100, 85, 70, 55]   # techos de CPU %, de más permisivo a más fresco
HISTERESIS = 5                  # °C por debajo del umbral para volver a subir
INTERVALO = 2.0                 # segundos entre lecturas


def escalones_para(original):
    """Escalera propia del equipo: empieza en SU techo y solo puede bajar.

    Con un techo de 40 % la lista fija [100, 85, 70, 55] no tenía ningún escalón
    válido y el frenado podía SUBIR el límite (de 40 a 55 u 85 %). Aquí el primer
    valor es siempre el techo original y el resto solo valores menores.
    """
    original = max(5, min(100, int(original)))
    bajos = [s for s in ESCALONES if s < original]
    if original > 30:
        bajos.append(30)
    return [original] + sorted(bajos, reverse=True)

VERDE, AMARILLO, ROJO, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"

# Red de seguridad: en este equipo un kill duro / terminate NO ejecuta el finally
# que restaura el techo de CPU. Guardamos el valor original en un archivo ANTES de
# tocarlo; si una ejecución muere sin limpiar, la siguiente (o la GUI) lo repara.
RUTA_ORIG = os.path.join(logs(), "watchdog_original_ceiling.txt")


def _guardar_original(pct):
    try:
        with open(RUTA_ORIG, "w", encoding="utf-8") as f:
            f.write(str(int(pct)))
    except Exception:
        pass


def _borrar_original():
    try:
        if os.path.exists(RUTA_ORIG):
            os.remove(RUTA_ORIG)
    except Exception:
        pass


def _auto_reparar():
    """Devuelve True si había un techo pendiente de restaurar y se restauró."""
    if not os.path.exists(RUTA_ORIG):
        return False
    try:
        with open(RUTA_ORIG, encoding="utf-8") as f:
            pct = int(f.read().strip())
    except Exception:
        # El archivo en sí está ilegible: no hay ningún valor que reintentar.
        _borrar_original()
        return False
    try:
        fijar_proc_max(pct)
    except Exception as e:
        # No borrar: un fallo temporal de powercfg debe reintentarse al arrancar.
        print(f"{AMARILLO}No pude restaurar el techo {pct} % ({e}); lo reintentaré.{RESET}")
        return False
    _borrar_original()
    print(f"{AMARILLO}AUTO-REPARACIÓN: un watchdog anterior no restauró el techo "
          f"de CPU; lo devuelvo a {pct} %.{RESET}")
    return True


def restaurar_si_pendiente():
    """API para la GUI: restaura el techo de CPU desde el archivo de seguridad.
    Idempotente; devuelve True si hubo que reparar."""
    return _auto_reparar()


def _powercfg(args):
    # errors="replace": powercfg escribe en la codepage de consola y si el sistema
    # está en UTF-8 el decodeo revienta dejando stdout en None, lo que mataría la
    # lectura del techo de CPU (y con ella la restauración). Solo importa el hex.
    return subprocess.run(["powercfg", *args], capture_output=True, text=True,
                          errors="replace")


def leer_proc_max_ac():
    """Devuelve el PROCTHROTTLEMAX AC actual (0-100) o None si no se puede leer."""
    return planes_energia.leer_ajuste(SUB_CPU, PROC_MAX)


def fijar_proc_max(pct):
    pct = max(5, min(100, int(pct)))
    _powercfg(["-setacvalueindex", "SCHEME_CURRENT", SUB_CPU, PROC_MAX, str(pct)])
    _powercfg(["-setdcvalueindex", "SCHEME_CURRENT", SUB_CPU, PROC_MAX, str(pct)])
    _powercfg(["-setactive", "SCHEME_CURRENT"])
    return pct


def temp_gpu(h):
    return dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Watchdog térmico (fase 2)")
    ap.add_argument("--override", type=float, default=0.0,
                    help="minutos a ignorar los guardarraíles (prueba de límites)")
    ap.add_argument("--gpu-warn", type=int, default=GPU_WARN)
    ap.add_argument("--gpu-crit", type=int, default=GPU_CRIT)
    ap.add_argument("--cpu-warn", type=int, default=CPU_WARN)
    ap.add_argument("--cpu-crit", type=int, default=CPU_CRIT)
    ap.add_argument("--intervalo", type=float, default=INTERVALO)
    a = ap.parse_args()

    nvml.nvmlInit()
    h = nvml.nvmlDeviceGetHandleByIndex(0)

    _auto_reparar()  # por si una ejecución anterior murió sin restaurar
    original_leido = leer_proc_max_ac()
    puede_frenar = original_leido is not None
    # Si no se pudo leer el techo, NO se toca: escribir luego un "100" de consola
    # borraría un límite real del usuario (40/55 %) sin que se entere.
    original = original_leido if puede_frenar else 100
    if not puede_frenar:
        print(f"{AMARILLO}No pude leer el techo de CPU actual; el watchdog solo "
              f"vigilará y NO frenará para no pisar tu límite.{RESET}")
    escalones = escalones_para(original)
    idx = 0  # se arranca en el techo real del usuario, nunca por encima

    carpeta = logs()
    ruta = os.path.join(carpeta, "watchdog_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    f = open(ruta, "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["instante", "gpu_temp_c", "cpu_temp_c", "techo_cpu_pct", "accion", "override"])

    hay_lhm = lhm_activo()
    if not hay_lhm:
        print(f"{AMARILLO}Aviso: LHM no responde en :8085; el watchdog vigilará solo la GPU.{RESET}")
        print("       (arráncalo con aaa_max.bat para proteger también el CPU)")

    t0 = time.time()
    override_hasta = t0 + a.override * 60 if a.override > 0 else 0
    if a.override > 0:
        print(f"{AMARILLO}Override activo: guardarraíles congelados {a.override:.0f} min "
              f"(hasta {datetime.fromtimestamp(override_hasta).strftime('%H:%M:%S')}).{RESET}")

    print(f"{VERDE}Watchdog activo. Techo CPU inicial {escalones[idx]} % (original {original} %). "
          f"Umbrales GPU {a.gpu_warn}/{a.gpu_crit} °C, CPU {a.cpu_warn}/{a.cpu_crit} °C. Ctrl+C para salir.{RESET}")
    beep = False
    try:
        while True:
            time.sleep(a.intervalo)
            ahora = time.time()
            en_override = ahora < override_hasta

            gt = temp_gpu(h)
            if hay_lhm:
                ct = temp_cpu_lhm()
            else:
                ct = None
            if not hay_lhm and int(ahora) % 30 == 0:
                hay_lhm = lhm_activo()

            accion = ""
            umbral_alto = (gt is not None and gt >= a.gpu_crit) or (ct is not None and ct >= a.cpu_crit)
            umbral_warn = (gt is not None and gt >= a.gpu_warn) or (ct is not None and ct >= a.cpu_warn)
            fresco = ((gt is None or gt < a.gpu_warn - HISTERESIS) and
                      (ct is None or ct < a.cpu_warn - HISTERESIS))

            if en_override:
                if umbral_alto and not beep:
                    sys.stdout.write("\a"); beep = True
                elif not umbral_alto:
                    beep = False
                accion = "override (sin mitigar)"
            else:
                if not puede_frenar:
                    if umbral_alto and not beep:
                        sys.stdout.write("\a"); beep = True
                    elif not umbral_alto:
                        beep = False
                    accion = "sin techo legible: solo aviso"
                elif umbral_alto:
                    nuevo = len(escalones) - 1
                    if nuevo != idx:
                        idx = nuevo
                        _guardar_original(original)   # red de seguridad ante kill duro
                        fijar_proc_max(escalones[idx])
                        accion = f"CRÍTICO -> techo CPU {escalones[idx]} %"
                    else:
                        accion = f"CRÍTICO (ya en mínimo {escalones[idx]} %)"
                    if not beep:
                        sys.stdout.write("\a"); beep = True
                elif umbral_warn:
                    if idx < len(escalones) - 1:
                        idx += 1
                        _guardar_original(original)   # red de seguridad ante kill duro
                        fijar_proc_max(escalones[idx])
                        accion = f"caliente -> techo CPU {escalones[idx]} %"
                    else:
                        accion = f"caliente (ya en mínimo {escalones[idx]} %)"
                    beep = False
                elif fresco:
                    if idx > 0:
                        idx -= 1
                        fijar_proc_max(escalones[idx])
                        accion = f"frío -> subo techo CPU {escalones[idx]} %"
                    beep = False
                else:
                    beep = False

            color = ROJO if umbral_alto and not en_override else (AMARILLO if umbral_warn and not en_override else VERDE)
            print(f"{color}GPU {gt if gt is not None else '?'} °C | CPU {ct if ct is not None else 'n/d'} °C | "
                  f"techo {escalones[idx]} %{' | OVERRIDE' if en_override else ''}"
                  f"{(' | ' + accion) if accion else ''}{RESET}   ")

            w.writerow([datetime.now().strftime("%H:%M:%S"), gt, ct if ct is not None else "",
                        escalones[idx], accion, "si" if en_override else "no"])
            f.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if puede_frenar:
            fijar_proc_max(original)
            _borrar_original()
            print(f"\n{VERDE}Watchdog detenido. Techo CPU restaurado a {original} %. Log: {ruta}{RESET}")
        else:
            print(f"\n{AMARILLO}Watchdog detenido. Log: {ruta}{RESET}")
        f.close()
        nvml.nvmlShutdown()


if __name__ == "__main__":
    main()
