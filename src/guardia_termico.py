"""Guardia térmico PASIVO para dejar la máquina sola (p. ej. mientras duermes).

NO es un test ni un benchmark ni estresa nada: solo LEE temperaturas (GPU por
NVML y CPU por Libre Hardware Monitor en :8085 si está activo; si no, intenta
ACPI). No genera carga ni calor.

Comportamiento:
  - Sondea cada --intervalo segundos.
  - Si la temperatura máxima se mantiene >= --umbral (89 °C por defecto) durante
    --sostenido segundos SEGUIDOS (30 s por defecto), entonces:
      1) deja constancia en logs/guardia_termico.log y en un archivo de evento,
      2) programa un apagado con 60 s de gracia para que las apps guarden.
  - Un pico suelto NO apaga: hace falta que siga por encima todo el tiempo
    sostenido (por eso el requisito de lecturas consecutivas).

Cancelar:
  - Antes de que empiece la cuenta: crear el archivo  logs/guardia_off.flag
    (o matar el proceso cuyo PID está en logs/guardia.pid).
  - Si ya empezó el apagado:  shutdown /a

Uso:  py guardia_termico.py [--umbral 89] [--sostenido 30] [--intervalo 5]
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

from rutas import BASE, logs
sys.path.insert(0, BASE)
LOGS = logs()
RUTA_LOG = os.path.join(LOGS, "guardia_termico.log")
RUTA_FLAG_OFF = os.path.join(LOGS, "guardia_off.flag")
RUTA_PID = os.path.join(LOGS, "guardia.pid")

from monitor_fase1 import dato, lhm_activo, temp_cpu, temp_cpu_lhm  # noqa: E402

try:
    import pynvml as nvml
except Exception:
    nvml = None


def registrar(mensaje):
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {mensaje}"
    try:
        with open(RUTA_LOG, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            print(linea, flush=True)
    except Exception:
        pass


_acpi_intentos = 0
_acpi_agotado = False


def leer_temps(h):
    """Devuelve (gpu_temp, cpu_temp). Cualquiera puede ser None si no se puede leer."""
    global _acpi_intentos, _acpi_agotado
    gpu_t = cpu_t = None
    if h is not None and nvml is not None:
        gpu_t = dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)
    if lhm_activo():
        cpu_t = temp_cpu_lhm()
    elif not _acpi_agotado:
        cpu_t = temp_cpu()  # ACPI (en este Acer suele devolver None)
        _acpi_intentos += 1
        if cpu_t is None and _acpi_intentos >= 3:
            _acpi_agotado = True
            registrar("ACPI no da la temp de CPU en este equipo; dejo de sondearla "
                      "(solo GPU hasta que LhmServer:8085 este activo).")
    return gpu_t, cpu_t


def guardar_progreso(gpu_t, cpu_t, umbral, sostenido):
    """Deja constancia del apagado y da margen para que las apps guarden."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    evento = os.path.join(LOGS, f"APAGADO_TERMICO_{ts}.txt")
    texto = (
        "APAGADO TÉRMICO DE SEGURIDAD (guardia_termico.py)\n"
        f"Fecha: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"Motivo: temperatura >= {umbral} °C sostenida {sostenido} s seguidos.\n"
        f"GPU temp: {gpu_t if gpu_t is not None else 'n/d'} °C\n"
        f"CPU temp: {cpu_t if cpu_t is not None else 'n/d'} °C\n"
        "Los CSV/log de monitor y sesión se guardan de forma continua (ya están en logs/).\n"
    )
    try:
        with open(evento, "w", encoding="utf-8") as f:
            f.write(texto)
    except Exception:
        pass
    registrar("Progreso guardado / constancia escrita en " + evento)


def apagar(umbral):
    registrar("Programando apagado de seguridad en 60 s (cancela con: shutdown /a).")
    try:
        msg = (f"NitroTuner: temperatura critica sostenida (>={float(umbral):g}C). "
               "Apagado de seguridad. Cancela con: shutdown /a")
    except (TypeError, ValueError):
        msg = "NitroTuner: temperatura critica sostenida. Apagado de seguridad."
    try:
        subprocess.run(["shutdown", "/s", "/f", "/t", "60", "/c", msg])
    except Exception as e:
        registrar(f"NO pude programar el apagado: {e}")


def main():
    ap = argparse.ArgumentParser(description="Guardia térmico pasivo con apagado de seguridad")
    ap.add_argument("--umbral", type=float, default=89.0, help="°C a partir de los cuales vigilar")
    ap.add_argument("--sostenido", type=float, default=30.0, help="seguidos por encima antes de apagar")
    ap.add_argument("--intervalo", type=float, default=5.0, help="segundos entre lecturas")
    ap.add_argument("--minutos", type=float, default=0.0, help="duración máxima (0 = indefinido)")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # si quedó un flag de cancelación viejo, lo ignoramos al arrancar
    try:
        if os.path.exists(RUTA_FLAG_OFF):
            os.remove(RUTA_FLAG_OFF)
    except Exception:
        pass
    try:
        with open(RUTA_PID, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    h = None
    if nvml is not None:
        try:
            nvml.nvmlInit()
            h = nvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            h = None

    registrar(f"=== Guardia térmico iniciado (PID {os.getpid()}) ===")
    registrar(f"Umbral {a.umbral:.0f} °C · sostenido {a.sostenido:.0f} s · cada {a.intervalo:.0f} s")
    registrar(f"GPU por NVML: {'disponible' if h is not None else 'NO disponible'} · "
              f"CPU por LHM(8085): {'activo' if lhm_activo() else 'sin 8085 (solo GPU cubierto)'}")
    if h is None and not lhm_activo():
        registrar("AVISO: no puedo leer ninguna temperatura. El guardia no podrá protegerte.")

    sobre_desde = None
    t0 = time.time()
    ultimo_hb = t0
    try:
        while True:
            if os.path.exists(RUTA_FLAG_OFF):
                registrar("Flag de cancelación detectado (logs/guardia_off.flag). Salgo sin apagar.")
                break

            gpu_t, cpu_t = leer_temps(h)
            candidatos = [t for t in (gpu_t, cpu_t) if isinstance(t, (int, float))]
            max_t = max(candidatos) if candidatos else None
            ahora = time.time()

            # heartbeat cada 5 min para dejar constancia de que sigue vivo
            if ahora - ultimo_hb >= 300:
                registrar(f"OK · GPU {gpu_t if gpu_t is not None else 'n/d'} °C · "
                          f"CPU {cpu_t if cpu_t is not None else 'n/d'} °C")
                ultimo_hb = ahora

            if max_t is not None and max_t >= a.umbral:
                if sobre_desde is None:
                    sobre_desde = ahora
                    registrar(f"ALERTA: {max_t:.0f} °C >= {a.umbral:.0f} °C "
                              f"(GPU {gpu_t} / CPU {cpu_t}). Cuenta sostenida iniciada.")
                elif ahora - sobre_desde >= a.sostenido:
                    registrar(f"SOSTENIDO {int(ahora - sobre_desde)} s por encima de "
                              f"{a.umbral:.0f} °C (última {max_t:.0f} °C). Apagando por seguridad.")
                    guardar_progreso(gpu_t, cpu_t, a.umbral, a.sostenido)
                    apagar(a.umbral)
                    break
            else:
                if sobre_desde is not None:
                    registrar(f"Temp {max_t if max_t is not None else 'n/d'} °C bajó de "
                              f"{a.umbral:.0f} °C; cuenta sostenida reiniciada.")
                    sobre_desde = None

            if a.minutos and (ahora - t0) >= a.minutos * 60:
                registrar("Duración máxima alcanzada. Salgo sin apagar.")
                break

            time.sleep(a.intervalo)
    except KeyboardInterrupt:
        registrar("Interrumpido manualmente. Salgo sin apagar.")
    finally:
        try:
            if h is not None and nvml is not None:
                nvml.nvmlShutdown()
        except Exception:
            pass
        try:
            if os.path.exists(RUTA_PID):
                os.remove(RUTA_PID)
        except Exception:
            pass
        registrar("=== Guardia térmico terminado ===")


if __name__ == "__main__":
    main()
