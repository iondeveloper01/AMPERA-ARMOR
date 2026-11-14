"""Sesión de juego con overlay + CSV + reporte HTML — Fase 2/GUI.

Qué hace:
  * Muestra una tarjeta overlay "click-through" (siempre encima, no roba el
    ratón) con FPS/frametime, temps de GPU/CPU, RAM, VRAM y commit libre.
    La tarjeta se pinta con Qt y sigue el estilo y la posición elegidos.
  * Graba un CSV por segundo en logs/ (mismo estilo que el monitor de fase 1).
  * Al terminar genera un reporte HTML autocontenido en logs/.

Atajos GLOBALES (funcionan con el juego en foco):
  Shift+Z        mostrar / ocultar el overlay
  Shift+M        poner un MARCADOR en ese instante (anota las temps del momento)
  Ctrl+Shift+L   entrar/salir del modo mover: arrastra la tarjeta y guárdala
  Ctrl+Shift+Q   terminar la sesión (genera el reporte)

También se puede terminar con Ctrl+C en la consola, o con el botón "Detener"
de la GUI (que manda SIGBREAK): en todos los casos se genera el reporte.

Si el proceso muere de golpe (kill duro / apagón) y su finally no llega a correr,
el CSV ya está en disco y la GUI reconstruye el reporte HTML con
restaurar_si_pendiente(); los marcadores se leen de su sidecar .jsonl.

Uso:  py sesion.py [--minutos N]
"""
import argparse
import ctypes
import ctypes.wintypes
import csv
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime

import psutil

from monitor_fase1 import (COMMIT_WARN, CPU_CRIT, CPU_WARN, GPU_CRIT, GPU_WARN,
                           commit_libre_gb, dato, lhm_activo, potencia_w,
                           procesos_conflicto, temp_cpu_lhm)
import reporte_html
import overlay_estilo
import fps_presentmon

try:
    import pynvml as nvml
except Exception:
    nvml = None

from rutas import logs
LOGS = logs()

# Red de seguridad. El CSV se va grabando en disco muestra a muestra, así que los
# DATOS sobreviven a un kill duro; lo que se pierde es el reporte HTML (se genera en
# el finally) y los marcadores (vivían solo en memoria). Para no perderlos:
#   * cada marcador se persiste al instante en un sidecar .jsonl;
#   * la GUI (o la siguiente sesión) puede RECONSTRUIR el reporte desde el CSV con
#     restaurar_si_pendiente(), sin depender de que el proceso hijo sobreviva.
ABANDONO_S = 180        # una sesión viva escribe 1 muestra/seg: si el CSV lleva más
                        # de así sin tocarse, es que la sesión ya no existe
RESCATE_DIAS = 3        # no rescatamos sesiones abandonadas hace semanas

# Columnas del CSV de sesión: (nombre en el archivo, clave de muestra que espera
# reporte_html). Es la MISMA lista para escribir y para leer, así la recuperación no
# puede desincronizarse del grabado. "marcador" no tiene clave: los marcadores viajan
# en su sidecar .jsonl.
COLUMNAS = [
    ("instante", "t"),
    ("gpu_temp_c", "gpu_temp"),
    ("gpu_clock_mhz", "gpu_clk"),
    ("gpu_power_w", "gpu_pow"),
    ("gpu_uso_pct", "gpu_uso"),
    ("cpu_temp_c", "cpu_temp"),
    ("cpu_uso_pct", "cpu_uso"),
    ("ram_pct", "ram_pct"),
    ("ram_libre_gb", "ram_libre"),
    ("vram_gb", "vram"),
    ("commit_libre_gb", "commit_libre"),
    ("nivel", "nivel"),
    ("alerta", "alerta"),
    ("marcador", None),
]
CABECERA = [nombre for nombre, _ in COLUMNAS]
_CABECERA_A_CLAVE = {nombre: clave for nombre, clave in COLUMNAS if clave}
_TEXTO = {"t", "alerta"}

# --- Win32 hotkeys ---
WM_HOTKEY = 0x0312
MOD_SHIFT = 0x0004
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_Z, VK_M, VK_Q, VK_L = 0x5A, 0x4D, 0x51, 0x4C
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

user32 = ctypes.windll.user32


def _muestras_desde_csv(ruta_csv):
    """Reconstruye la lista de muestras que espera reporte_html a partir del CSV
    grabado durante la sesión. Un valor vacío o no numérico se ignora (equivale a
    'no disponible', p. ej. la temp de CPU sin LHM)."""
    muestras = []
    try:
        with open(ruta_csv, newline="", encoding="utf-8") as f:
            for fila in csv.DictReader(f):
                d = {}
                for col, clave in _CABECERA_A_CLAVE.items():
                    v = (fila.get(col) or "").strip()
                    if not v:
                        continue
                    if clave in _TEXTO:
                        d[clave] = v
                    else:
                        try:
                            num = float(v)
                        except ValueError:
                            continue
                        d[clave] = int(num) if clave == "nivel" else num
                if "t" in d:
                    muestras.append(d)
    except Exception:
        return []
    return muestras


def _marcadores_desde(ruta_marc):
    """Lee el sidecar JSONL de marcadores (uno por línea, escritos al instante)."""
    out = []
    try:
        with open(ruta_marc, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    m = json.loads(linea)
                except Exception:
                    continue
                if m.get("t") and m.get("texto"):
                    out.append({"t": m["t"], "texto": m["texto"]})
    except Exception:
        pass
    return out


def restaurar_si_pendiente(incluir_recientes=False):
    """Reconstruye los reportes HTML de sesiones que murieron sin ejecutar su finally.

    Una sesión en marcha aún no tiene reporte, así que por defecto solo se interviene
    cuando el CSV lleva más de ABANDONO_S segundos sin escribir (si no, le estaríamos
    generando el reporte a una sesión viva). La GUI pasa incluir_recientes=True cuando
    el proceso de sesión YA murió: ahí la espera no tiene sentido y el reporte se
    recupera al momento. Idempotente: en cuanto existe el .html deja de contar.
    Devuelve la lista de reportes recuperados."""
    reparados = []
    try:
        nombres = os.listdir(LOGS)
    except Exception:
        return reparados
    ahora = time.time()
    for nombre in nombres:
        if not (nombre.startswith("sesion_") and nombre.endswith(".csv")):
            continue
        ts = nombre[len("sesion_"):-len(".csv")]
        ruta_csv = os.path.join(LOGS, nombre)
        ruta_html = os.path.join(LOGS, f"reporte_{ts}.html")
        if os.path.exists(ruta_html):
            continue
        try:
            edad = ahora - os.path.getmtime(ruta_csv)
        except OSError:
            continue
        if edad > RESCATE_DIAS * 86400:
            continue
        if edad < ABANDONO_S and not incluir_recientes:
            continue
        muestras = _muestras_desde_csv(ruta_csv)
        if not muestras:
            continue
        try:
            reporte_html.generar(muestras, _marcadores_desde(ruta_csv[:-4] + ".marcadores.jsonl"),
                                 ruta_html, titulo=f"Sesión {ts} (recuperada)")
        except Exception:
            continue
        reparados.append(ruta_html)
    return reparados


class Sesion:
    def __init__(self, minutos=0.0, estilo=None):
        self.minutos = minutos
        self.estilo = overlay_estilo.obtener(estilo)
        self.muestras = []
        self.marcadores = []
        self.lock = threading.Lock()
        self.ultima = {}
        self.parar = threading.Event()
        self.visible = threading.Event()
        self.visible.set()
        self.mover = threading.Event()
        self.fps = None
        self.overlay = None
        self.rows = {}      # clave de métrica -> Label del valor (números)
        self.hint = None
        self.t0 = time.time()
        self.tsv = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(LOGS, exist_ok=True)
        self.ruta_csv = os.path.join(LOGS, f"sesion_{self.tsv}.csv")
        self.ruta_html = os.path.join(LOGS, f"reporte_{self.tsv}.html")
        self.ruta_marc = os.path.join(LOGS, f"sesion_{self.tsv}.marcadores.jsonl")

    # ---------- sensores ----------
    def _loop_sensores(self):
        h = None
        if nvml:
            try:
                nvml.nvmlInit()
                h = nvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                h = None
        psutil.cpu_percent(None)
        f = open(self.ruta_csv, "w", newline="", encoding="utf-8")
        w = csv.writer(f)
        w.writerow(CABECERA)
        hay_lhm = lhm_activo()
        try:
            while not self.parar.is_set():
                d = {}
                if h is not None:
                    d["gpu_temp"] = dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)
                    d["gpu_clk"] = dato(nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_GRAPHICS)
                    lim = dato(nvml.nvmlDeviceGetEnforcedPowerLimit, h)
                    d["gpu_pow"] = potencia_w(h, lim)
                    uso = dato(nvml.nvmlDeviceGetUtilizationRates, h)
                    d["gpu_uso"] = uso.gpu if uso else None
                    vinfo = dato(nvml.nvmlDeviceGetMemoryInfo, h)
                    d["vram"] = round(vinfo.used / 2 ** 30, 2) if vinfo else None
                if hay_lhm:
                    d["cpu_temp"] = temp_cpu_lhm()
                else:
                    hay_lhm = lhm_activo()
                    d["cpu_temp"] = None
                d["cpu_uso"] = psutil.cpu_percent(None)
                vm = psutil.virtual_memory()
                d["ram_pct"] = vm.percent
                d["ram_libre"] = round(vm.available / 2 ** 30, 1)
                d["commit_libre"] = commit_libre_gb()

                alertas = []
                gt, ct = d.get("gpu_temp"), d.get("cpu_temp")
                if gt is not None and gt >= GPU_CRIT:
                    alertas.append(f"GPU {gt}C CRITICO")
                elif gt is not None and gt >= GPU_WARN:
                    alertas.append(f"GPU {gt}C ALTA")
                if ct is not None and ct >= CPU_CRIT:
                    alertas.append(f"CPU {ct}C CRITICO")
                elif ct is not None and ct >= CPU_WARN:
                    alertas.append(f"CPU {ct}C ALTA")
                if isinstance(d.get("commit_libre"), (int, float)) and d["commit_libre"] < COMMIT_WARN:
                    alertas.append(f"COMMIT libre bajo {d['commit_libre']}GB")
                nivel = 2 if any("CRITICO" in a for a in alertas) else (1 if alertas else 0)

                d["t"] = datetime.now().strftime("%H:%M:%S")
                d["nivel"] = nivel
                d["alerta"] = "; ".join(alertas)
                with self.lock:
                    self.muestras.append(d)
                    self.ultima = d
                w.writerow([d["t"], gt, d.get("gpu_clk"),
                            round(d["gpu_pow"], 1) if isinstance(d.get("gpu_pow"), float) else "",
                            d.get("gpu_uso"), ct, d.get("cpu_uso"), d.get("ram_pct"),
                            d.get("ram_libre"), d.get("vram"), d.get("commit_libre"),
                            nivel, d["alerta"], ""])
                f.flush()

                if self.minutos and (time.time() - self.t0) >= self.minutos * 60:
                    self.parar.set()
                    break
                self.parar.wait(1.0)
        finally:
            f.close()
            if h is not None:
                try:
                    nvml.nvmlShutdown()
                except Exception:
                    pass

    # ---------- hotkeys ----------
    def _loop_hotkeys(self):
        ok1 = user32.RegisterHotKey(None, 1, MOD_SHIFT | MOD_NOREPEAT, VK_Z)
        ok2 = user32.RegisterHotKey(None, 2, MOD_SHIFT | MOD_NOREPEAT, VK_M)
        ok3 = user32.RegisterHotKey(None, 3, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_Q)
        ok4 = user32.RegisterHotKey(None, 4, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_L)
        if not (ok1 and ok2 and ok3 and ok4):
            print("[aviso] algún atajo global no se pudo registrar (¿ya lo usa otra app?)")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == 1:
                    self._toggle_overlay()
                elif msg.wParam == 2:
                    self._marcador()
                elif msg.wParam == 3:
                    self.parar.set()
                elif msg.wParam == 4:
                    self._toggle_mover()
            if self.parar.is_set():
                break
        for i in (1, 2, 3, 4):
            user32.UnregisterHotKey(None, i)

    def _toggle_overlay(self):
        if self.visible.is_set():
            self.visible.clear()
        else:
            self.visible.set()

    def _toggle_mover(self):
        if self.mover.is_set():
            self.mover.clear()
        else:
            self.mover.set()

    def _marcador(self):
        with self.lock:
            u = dict(self.ultima)
        texto = (f"GPU {u.get('gpu_temp','?')}°C · CPU {u.get('cpu_temp','n/d')}°C · "
                 f"RAM libre {u.get('ram_libre','?')}GB · commit {u.get('commit_libre','?')}GB")
        m = {"t": datetime.now().strftime("%H:%M:%S"), "texto": texto}
        with self.lock:
            self.marcadores.append(m)
        # Se persiste al instante (no solo en memoria) para que un kill duro de la
        # sesión no borre los marcadores: la recuperación relee este sidecar.
        try:
            with open(self.ruta_marc, "a", encoding="utf-8") as f:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---------- overlay ----------
    def _datos_overlay(self):
        with self.lock:
            d = dict(self.ultima)
        d["elapsed"] = int(time.time() - self.t0)
        if self.fps is not None:
            fps, ms = self.fps.valor()
            d["fps"], d["frametime"] = fps, ms
        return d

    def _estado_overlay(self):
        return (self._datos_overlay(), self.visible.is_set(),
                self.mover.is_set(), self.parar.is_set())

    def _loop_gui(self):
        from PySide6.QtWidgets import QApplication
        import overlay_qt
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        self.overlay = overlay_qt.Overlay(self.estilo, self._estado_overlay)
        self.overlay.show()
        app.exec()
        self.parar.set()

    # ---------- orquestación ----------
    def correr(self):
        def _sigbreak(signum, frame):
            self.parar.set()
        try:
            signal.signal(signal.SIGBREAK, _sigbreak)
        except Exception:
            pass

        t_sens = threading.Thread(target=self._loop_sensores, daemon=True)
        t_hot = threading.Thread(target=self._loop_hotkeys, daemon=True)
        t_sens.start()
        t_hot.start()
        self.fps = fps_presentmon.MedidorFPS()
        fps_ok = self.fps.iniciar()
        print(f"Sesión iniciada. CSV: {self.ruta_csv}")
        print("Atajos: Shift+Z ocultar · Shift+M marcador · Ctrl+Shift+L mover · "
              "Ctrl+Shift+Q terminar")
        print("También puedes terminar con Ctrl+C aquí.")
        if fps_ok:
            print("FPS: PresentMon iniciado (puede tardar unos segundos en dar cifras).")
        else:
            print(f"FPS no disponible: {self.fps.motivo}")
        try:
            self._loop_gui()
        except KeyboardInterrupt:
            self.parar.set()
        finally:
            self.parar.set()
            if self.fps is not None:
                self.fps.detener()
            t_sens.join(timeout=4)
            self._generar_reporte()

    def _generar_reporte(self):
        with self.lock:
            muestras = list(self.muestras)
            marcadores = list(self.marcadores)
        if not muestras:
            print("Sin muestras; no genero reporte.")
            return
        try:
            reporte_html.generar(muestras, marcadores, self.ruta_html,
                                 titulo=f"Sesión {self.tsv}")
        except Exception as e:
            print(f"No pude generar el reporte HTML: {e}")
            return
        inicio = muestras[0].get("t", "?")
        fin = muestras[-1].get("t", "?")
        gt = [m["gpu_temp"] for m in muestras if isinstance(m.get("gpu_temp"), (int, float))]
        ct = [m["cpu_temp"] for m in muestras if isinstance(m.get("cpu_temp"), (int, float))]
        avisos = sum(1 for m in muestras if m.get("nivel") == 1)
        crits = sum(1 for m in muestras if m.get("nivel") == 2)
        print("\n=== RESUMEN DE LA SESION ===")
        print(f"Horario: {inicio} -> {fin}  |  {len(muestras)} muestras (~{len(muestras)} s)")
        print(f"Marcadores: {len(marcadores)}  |  Avisos: {avisos}  |  Criticos: {crits}")
        if gt:
            print(f"GPU temp: max {max(gt)} C | media {sum(gt)/len(gt):.1f} C")
        if ct:
            print(f"CPU temp: max {max(ct)} C | media {sum(ct)/len(ct):.1f} C")
        print(f"Reporte HTML: {self.ruta_html}")
        print(f"CSV: {self.ruta_csv}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Sesión de juego con overlay + CSV + reporte")
    ap.add_argument("--minutos", type=float, default=0.0,
                    help="duración máxima (0 = hasta que la cierres)")
    ap.add_argument("--estilo", default=None,
                    help="nombre del estilo del overlay (ver overlay_estilos.json)")
    a = ap.parse_args()
    recuperados = restaurar_si_pendiente()
    for ruta in recuperados:
        print(f"Recuperé el reporte de una sesión interrumpida: {os.path.basename(ruta)}")
    ses = Sesion(minutos=a.minutos, estilo=a.estilo)
    print(f"Estilo del overlay: {ses.estilo['nombre']}")
    ses.correr()


if __name__ == "__main__":
    main()
