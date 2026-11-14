import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

import psutil
import pynvml as nvml

from rutas import logs
import ctypes

GPU_WARN, GPU_CRIT = 85, 92
CPU_WARN, CPU_CRIT = 90, 95
COMMIT_WARN = 4.0
LHM_URL = "http://127.0.0.1:8085/data.json"

# Oculta la consola al lanzar procesos hijos (powershell) en Windows, para que
# las llamadas periodicas no hagan parpadear una ventana. 0 fuera de Windows.
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_libre_gb():
    st = _MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return round(st.ullAvailPageFile / 2 ** 30, 1)
    return ""
CONFLICTOS = {"msiafterburner.exe", "rtss.exe", "rtsserver.exe", "nitrosense.exe"}
VERDE, AMARILLO, ROJO, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def dato(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def potencia_w(h, lim_mw):
    raw = dato(nvml.nvmlDeviceGetPowerUsage, h)
    if raw is None:
        return None
    w = raw / 1000
    tope = (lim_mw / 1000 * 1.35 + 5) if lim_mw else 150.0
    if w < 0 or w > min(tope, 150.0):
        return None
    return w


def fan_gpu(h):
    for args in ((h,), (h, 0)):
        v = dato(nvml.nvmlDeviceGetFanSpeed, *args)
        if v is not None:
            return v
    return None


def temp_cpu():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/wmi -ClassName "
             "MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
             "Measure-Object CurrentTemperature -Maximum).Maximum"],
            capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)
        return round(float(out.stdout.strip()) / 10 - 273.15, 1)
    except Exception:
        return None


def _num(texto):
    """Extrae el primer numero de un string tipo '65,0 °C' (coma o punto decimal)."""
    m = re.search(r"-?\d+(?:[.,]\d+)?", texto or "")
    if not m:
        return None
    return float(m.group().replace(",", "."))


def temp_cpu_lhm(timeout: float = 3):
    """Temperatura del CPU via el servidor web de Libre Hardware Monitor (fase 1.5).
    Acer no expone el sensor por ACPI/Windows; LHM lo lee directo del MSR/EC.
    Formato v0.9.6: arbol Text/Children con hojas {Type, SensorId, Value='NN,N °C'}.

    Bajo carga total LHM compite por la CPU y su primera respuesta puede tardar
    varios segundos: quien pueda esperar (la certificación) pasa un `timeout` mayor.
    """
    try:
        with urllib.request.urlopen(LHM_URL, timeout=timeout) as r:
            root = json.loads(r.read().decode("utf-8-sig"))
    except Exception:
        return None

    temps = {}
    pila = [root] if isinstance(root, dict) else list(root)
    while pila:
        n = pila.pop()
        if not isinstance(n, dict):
            continue
        pila.extend(n.get("Children", []))
        if n.get("Type") != "Temperature":
            continue
        sid = (n.get("SensorId") or "").lower()
        if "cpu" not in sid:  # /intelcpu/... o /amdcpu/...
            continue
        val = _num(n.get("Value"))
        if val is not None:
            temps[(n.get("Text") or "").lower()] = val

    if not temps:
        return None
    for pref in ("cpu package", "core average", "core max", "average", "package"):
        if pref in temps:
            return round(temps[pref], 1)
    return round(max(temps.values()), 1)


def lhm_activo():
    try:
        with urllib.request.urlopen(LHM_URL, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def procesos_conflicto():
    nombres = set()
    for p in psutil.process_iter(["name"]):
        try:
            nombres.add((p.info["name"] or "").lower())
        except Exception:
            pass
    return sorted(nombres & CONFLICTOS)


def fmt_seg(s):
    m = int(s // 60)
    ss = int(s % 60)
    return f"{m}m {ss:02d}s"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    minutos = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
    nvml.nvmlInit()
    h = nvml.nvmlDeviceGetHandleByIndex(0)
    nombre = dato(nvml.nvmlDeviceGetName, h)
    if isinstance(nombre, bytes):
        nombre = nombre.decode()

    carpeta = logs()
    ruta_csv = os.path.join(carpeta, "monitor_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    f = open(ruta_csv, "w", newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["instante", "gpu_temp_c", "gpu_clock_mhz", "gpu_mem_mhz",
                "gpu_power_w", "gpu_fan_pct", "gpu_uso_pct",
                "cpu_temp_c", "cpu_uso_pct", "cpu_clock_mhz",
                "ram_uso_pct", "ram_libre_gb", "vram_uso_gb", "commit_libre_gb", "alerta"])

    conflicto = procesos_conflicto()
    hay_lhm = lhm_activo()
    psutil.cpu_percent(None)
    muestras = []
    t0 = time.time()
    tick = 0
    cpu_t = None
    ult_pow = None
    beep_activo = False

    print("\033[2J\033[H", end="")
    try:
        while True:
            time.sleep(1.0)
            tick += 1
            if hay_lhm:
                cpu_t = temp_cpu_lhm()
            else:
                if tick % 30 == 0:
                    hay_lhm = lhm_activo()
                if not hay_lhm and (tick % 10 == 1 or cpu_t is None):
                    cpu_t = temp_cpu()

            g_temp = dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)
            g_clk = dato(nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_GRAPHICS)
            g_mem = dato(nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_MEM)
            g_lim = dato(nvml.nvmlDeviceGetEnforcedPowerLimit, h)
            g_pow = potencia_w(h, g_lim)
            if g_pow is None:
                g_pow = ult_pow
            else:
                ult_pow = g_pow
            g_fan = fan_gpu(h)
            g_uso = dato(nvml.nvmlDeviceGetUtilizationRates, h)
            c_uso = psutil.cpu_percent(None)
            c_clk = dato(psutil.cpu_freq)
            c_clk = round(c_clk.current) if c_clk else None
            vm = psutil.virtual_memory()
            ram_pct = vm.percent
            ram_lib = round(vm.available / 2 ** 30, 1)
            vinfo = dato(nvml.nvmlDeviceGetMemoryInfo, h)
            vram_gb = round(vinfo.used / 2 ** 30, 2) if vinfo else ""
            c_lib = commit_libre_gb()

            alertas = []
            if g_temp is not None and g_temp >= GPU_CRIT:
                alertas.append(f"GPU {g_temp} C CRITICO")
            elif g_temp is not None and g_temp >= GPU_WARN:
                alertas.append(f"GPU {g_temp} C ALTA")
            if cpu_t is not None and cpu_t >= CPU_CRIT:
                alertas.append(f"CPU {cpu_t} C CRITICO")
            elif cpu_t is not None and cpu_t >= CPU_WARN:
                alertas.append(f"CPU {cpu_t} C ALTA")
            if ram_lib < 1.5:
                alertas.append(f"RAM libre baja: {ram_lib} GB")
            if vram_gb != "" and vram_gb >= 5.5:
                alertas.append(f"VRAM casi llena: {vram_gb} GB")
            if c_lib != "" and c_lib < COMMIT_WARN:
                alertas.append(f"COMMIT libre bajo: {c_lib} GB")
            nivel = 2 if any("CRITICO" in a for a in alertas) else (1 if alertas else 0)

            if nivel == 2 and not beep_activo:
                sys.stdout.write("\a")
                beep_activo = True
            elif nivel < 2:
                beep_activo = False

            w.writerow([datetime.now().strftime("%H:%M:%S"), g_temp, g_clk, g_mem,
                        round(g_pow, 1) if g_pow is not None else "", g_fan,
                        g_uso.gpu if g_uso else "", cpu_t, c_uso, c_clk,
                        ram_pct, ram_lib, vram_gb, c_lib,
                        "; ".join(alertas)])
            f.flush()
            muestras.append((g_temp, cpu_t, g_pow, nivel))

            color = {0: VERDE, 1: AMARILLO, 2: ROJO}[nivel]
            estado = {0: "[OK]", 1: "[AVISO]", 2: "[CRITICO]"}[nivel]
            trans = time.time() - t0
            print("\033[2J\033[H", end="")
            print(f"=== MONITOR FASE 1 - {nombre} ===   Ctrl+C para terminar")
            print(f"GPU  temp {g_temp} C   clock {g_clk} MHz   mem {g_mem} MHz   "
                  f"power {f'{g_pow:.1f}' if g_pow is not None else '?'} W   "
                  f"fan {g_fan if g_fan is not None else 'n/d'} %   uso {g_uso.gpu if g_uso else '?'} %")
            print(f"CPU  temp {cpu_t if cpu_t is not None else 'n/d'} C   uso {c_uso} %   clock {c_clk} MHz")
            print(f"RAM  uso {ram_pct} %   libre {ram_lib} GB   COMMIT libre {c_lib} GB   VRAM en uso {vram_gb} GB")
            if cpu_t is None:
                print("CPU temp n/d: LHM no responde en el puerto 8085 (arrancalo con aaa_max.bat o a mano)")
            if g_lim:
                print(f"Limite de potencia GPU aplicado: {g_lim / 1000:.0f} W")
            print(f"{color}{estado} {('; '.join(alertas)) if alertas else 'temperaturas dentro de rango'}{RESET}")
            if conflicto:
                print(f"{AMARILLO}Ojo: detectado software que tambien toca la GPU: {', '.join(conflicto)}{RESET}")
            print(f"muestras: {len(muestras)}   tiempo: {fmt_seg(trans)}   log: {ruta_csv}")
            if minutos and trans >= minutos * 60:
                break
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        nvml.nvmlShutdown()

    print("\n=== RESUMEN DE LA SESION ===")
    if not muestras:
        print("Sin muestras.")
        return
    gt = [m[0] for m in muestras if m[0] is not None]
    ct = [m[1] for m in muestras if m[1] is not None]
    pw = [m[2] for m in muestras if m[2] is not None]
    if gt:
        print(f"GPU temp: min {min(gt)} C / max {max(gt)} C / media {sum(gt) / len(gt):.1f} C")
    if ct:
        print(f"CPU temp: min {min(ct)} C / max {max(ct)} C / media {sum(ct) / len(ct):.1f} C")
    if pw:
        print(f"GPU power: pico {max(pw):.1f} W / media {sum(pw) / len(pw):.1f} W")
    avisos = sum(1 for m in muestras if m[3] == 1)
    crits = sum(1 for m in muestras if m[3] == 2)
    print(f"Muestras en AVISO: {avisos}   en CRITICO: {crits}   de {len(muestras)}")
    print(f"Log guardado en: {ruta_csv}")


if __name__ == "__main__":
    main()
