"""FPS y frametime reales con PresentMon (Intel, licencia MIT).

PresentMon captura los "presents" de las aplicaciones D3D y escribe CSV. Aquí se
lanza sin consola, se lee su salida y se calcula el FPS del proceso que está en
primer plano (el juego). Necesita privilegios de administrador o pertenecer al
grupo "Performance Log Users"; si no hay permiso, el medidor queda inactivo y el
overlay oculta la cifra sin romper la sesión.
"""
from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
import threading
import time
from collections import defaultdict, deque

from rutas import resource_path

_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_IGNORAR = {
    "explorer.exe", "dwm.exe", "applicationframehost.exe", "searchhost.exe",
    "textinputhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "lockapp.exe", "nitrotuner.exe", "nitrotunercli.exe", "python.exe",
    "pythonw.exe", "ampera.exe",
}


def _pid_primer_plano():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


class MedidorFPS:
    def __init__(self):
        self.exe = os.path.join(resource_path("tools"), "PresentMon", "PresentMon.exe")
        self.disponible = os.path.isfile(self.exe)
        self.motivo = "" if self.disponible else "PresentMon no está instalado"
        self.proc = None
        self._parar = threading.Event()
        self._lock = threading.Lock()
        self._datos = defaultdict(lambda: deque(maxlen=300))
        self._nombres = {}
        self._ultimo_pid = None
        self._hilos = []

    # ---------- ciclo de vida ----------
    def iniciar(self):
        if not self.disponible:
            return False
        args = [self.exe, "--v1_metrics", "--output_stdout",
                "--no_console_stats", "--session_name", f"AMPERA_{os.getpid()}",
                "--stop_existing_session"]
        try:
            self.proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA)
        except Exception as e:
            self.motivo = f"no pude lanzar PresentMon: {e}"
            self.disponible = False
            return False
        for objetivo in (self._leer_csv, self._leer_errores):
            hilo = threading.Thread(target=objetivo, daemon=True)
            hilo.start()
            self._hilos.append(hilo)
        return True

    def detener(self):
        self._parar.set()
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    # ---------- lectura ----------
    def _leer_errores(self):
        try:
            for linea in io.TextIOWrapper(self.proc.stderr, encoding="utf-8",
                                          errors="replace"):
                texto = linea.strip().lower()
                if "access denied" in texto or "administrative privileges" in texto:
                    with self._lock:
                        self.motivo = "sin permiso (abre AMPERA como administrador)"
                        self.disponible = False
        except Exception:
            pass

    def _leer_csv(self):
        campos = None
        idx_pid = idx_ms = idx_app = None
        try:
            flujo = io.TextIOWrapper(self.proc.stdout, encoding="utf-8",
                                     errors="replace", newline="")
            for linea in flujo:
                if self._parar.is_set():
                    break
                fila = next(csv.reader([linea]))
                if not fila:
                    continue
                if campos is None:
                    if fila[0].strip() != "Application":
                        continue
                    campos = {nombre.strip(): i for i, nombre in enumerate(fila)}
                    idx_app = campos.get("Application")
                    idx_pid = campos.get("ProcessID")
                    idx_ms = campos.get("MsBetweenPresents")
                    if idx_pid is None or idx_ms is None:
                        return
                    continue
                try:
                    pid = int(fila[idx_pid])
                    ms = float(fila[idx_ms])
                except (ValueError, IndexError):
                    continue
                if ms <= 0:
                    continue
                ahora = time.monotonic()
                with self._lock:
                    self._nombres[pid] = fila[idx_app]
                    self._datos[pid].append((ahora, ms))
                    while self._datos[pid] and self._datos[pid][0][0] < ahora - 4:
                        self._datos[pid].popleft()
        except Exception:
            pass

    # ---------- consulta ----------
    def _elegir_pid(self, ahora):
        frente = _pid_primer_plano()
        with self._lock:
            if frente in self._datos and self._nombres.get(frente, "").lower() not in _IGNORAR:
                self._ultimo_pid = frente
                return frente
            if self._ultimo_pid in self._datos:
                reciente = [t for t, _ in self._datos[self._ultimo_pid] if t > ahora - 1.5]
                if reciente:
                    return self._ultimo_pid
            mejor, mejor_n = None, 0
            for pid, serie in self._datos.items():
                nombre = self._nombres.get(pid, "").lower()
                if nombre in _IGNORAR:
                    continue
                n = sum(1 for t, _ in serie if t > ahora - 0.8)
                if n > mejor_n:
                    mejor, mejor_n = pid, n
            self._ultimo_pid = mejor
            return mejor

    def valor(self):
        """(fps, frametime_ms) del proceso objetivo, o (None, None)."""
        ahora = time.monotonic()
        pid = self._elegir_pid(ahora)
        if pid is None:
            return None, None
        with self._lock:
            serie = [ms for t, ms in self._datos.get(pid, ()) if t > ahora - 1.2]
        if len(serie) < 2:
            return None, None
        media = sum(serie) / len(serie)
        if media <= 0:
            return None, None
        return round(1000.0 / media, 1), round(media, 2)

    def estado(self):
        return {"disponible": self.disponible, "motivo": self.motivo}
