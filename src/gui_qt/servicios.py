"""Puente entre la interfaz Qt y el backend real de AMPERA.

La GUI no reimplementa nada: llama a `perfil_juego`, `planes_energia`,
`overlay_estilo`, `monitor_fase1` y a los JSON seguros de `datos`. Las
herramientas largas (certificar, watchdog) corren como subproceso con su salida
en vivo, igual que en la interfaz clásica: congelado `NitroTunerCLI.exe --tool`,
en fuente `python main.py --tool`.
"""

from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QThread, Signal

import datos as datos_json
import monitor_fase1
import overlay_estilo
import perfil_juego
import planes_energia
from rutas import BASE, data_path, logs

PERFILES = data_path("perfiles_juegos.json")
CERTS = data_path("perfiles_certificados.json")

_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_GRUPO_NUEVO = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_CTRL_BREAK = getattr(signal, "CTRL_BREAK_EVENT", None)


def _ventana_consola() -> bool:
    """True si ESTE proceso tiene consola (en fuente desde un .bat). Si la GUI
    corre sin consola (el .exe), un hijo de consola abriría una: hay que
    ocultarla. Con consola propia se hereda y Ctrl+Break sigue funcionando."""
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def limpiar_ansi(texto: str) -> str:
    return _RE_ANSI.sub("", texto or "")


def _python_consola() -> str:
    """python.exe con consola, incluso si la GUI corre bajo pythonw.exe."""
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[: -len("pythonw.exe")] + "python.exe"
    return py


def cmd_tool(nombre: str, extra: list[str] | None = None) -> list[str]:
    extra = list(extra or [])
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(sys.executable), "NitroTunerCLI.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(f"No encuentro {exe}")
        return [exe, "--tool", nombre, *extra]
    return [_python_consola(), os.path.join(BASE, "main.py"), "--tool", nombre, *extra]


def abrir_herramienta(nombre: str, extra: list[str] | None = None) -> subprocess.Popen:
    """Lanza una herramienta sin ventana de consola; su salida va a logs/."""
    ruta = os.path.join(logs(), f"{nombre}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    with open(ruta, "w", encoding="utf-8", errors="replace") as salida:
        return subprocess.Popen(cmd_tool(nombre, extra), cwd=BASE,
                                stdin=subprocess.DEVNULL,
                                stdout=salida, stderr=subprocess.STDOUT,
                                creationflags=_SIN_VENTANA)


def abrir_herramienta_admin(nombre: str,
                            extra: list[str] | None = None) -> bool:
    """Lanza la herramienta elevada (UAC): PresentMon necesita administrador
    para leer los FPS. Devuelve False si el usuario cancela el permiso."""
    cmd = cmd_tool(nombre, extra)
    try:
        codigo = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", cmd[0], subprocess.list2cmdline(cmd[1:]),
            BASE, 0)
    except Exception:
        return False
    return codigo > 32


class Tarea(QThread):
    """Herramienta del backend en un hilo, con su salida línea a línea."""

    linea = Signal(str)
    terminada = Signal(int)

    def __init__(self, nombre: str, extra: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.nombre = nombre
        self.extra = list(extra or [])
        self.proc: subprocess.Popen | None = None

    def run(self) -> None:
        codigo = -1
        try:
            entorno = dict(os.environ, PYTHONUNBUFFERED="1")
            flags = _GRUPO_NUEVO if _ventana_consola() else _GRUPO_NUEVO | _SIN_VENTANA
            self.proc = subprocess.Popen(
                cmd_tool(self.nombre, self.extra),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, cwd=BASE, env=entorno,
                creationflags=flags,
            )
            if self.proc.stdout is not None:
                for linea in self.proc.stdout:
                    self.linea.emit(linea.rstrip("\r\n"))
            codigo = self.proc.wait()
        except Exception as e:
            self.linea.emit(f"[ERROR] {e}")
        finally:
            if self.proc is not None and self.proc.stdout is not None:
                try:
                    self.proc.stdout.close()
                except Exception:
                    pass
            # La señal de Qt es un int de 32 bits: los códigos de terminación de
            # Windows (0xC000013A) no caben; se resumen como -1.
            if not 0 <= codigo <= 128:
                codigo = -1
            self.terminada.emit(codigo)

    def detener(self, gracia: float = 2.0) -> None:
        """CTRL_BREAK (deja limpiar el `finally`) y, si no cede, terminate/kill."""
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return

        def _parar() -> None:
            if _CTRL_BREAK is not None:
                try:
                    os.kill(proc.pid, _CTRL_BREAK)
                except Exception:
                    pass
            try:
                proc.wait(timeout=gracia)
                return
            except Exception:
                pass
            for fn in (proc.terminate, proc.kill):
                try:
                    fn()
                    proc.wait(timeout=2)
                    return
                except Exception:
                    continue

        threading.Thread(target=_parar, daemon=True).start()


class Hilo(QThread):
    """Ejecuta una función corta fuera del hilo de la interfaz."""

    listo = Signal(object)
    fallo = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.listo.emit(self._fn())
        except Exception as e:
            self.fallo.emit(str(e))


def reparar_pendientes() -> bool:
    """Restaura el techo de CPU que dejó una herramienta muerta sin `finally`."""
    reparado = False
    for modulo in ("watchdog", "certificar", "aviso_termico"):
        try:
            if __import__(modulo).restaurar_si_pendiente():
                reparado = True
        except Exception:
            pass
    if reparado:
        planes_energia.invalisar()
    return reparado


def es_certificado(nombre: str) -> bool:
    crudo, _ = datos_json.leer(CERTS, {})
    return bool(datos_json.dic(datos_json.dic(crudo).get(nombre)).get("certificado"))


def aplicar_perfil(nombre: str, cfg: dict, cerrar: bool = False) -> dict:
    """Aplica plan y techo de CPU; cierra las apps del perfil si se pidió."""
    cfg = datos_json.perfil(cfg)
    cerradas: list[str] = []
    if cerrar:
        for app in cfg.get("cerrar", []):
            subprocess.run(["taskkill", "/im", app, "/f"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL,
                           creationflags=_SIN_VENTANA)
            cerradas.append(app)
    plan = perfil_juego.fijar_plan(cfg.get("plan", "alto"))
    pct = perfil_juego.fijar_cpu_max(cfg.get("cpu_max", 100))
    planes_energia.invalisar()
    activo = planes_energia.activo()[1]
    return {
        "plan": activo or plan,
        "pct": pct,
        "cerradas": cerradas,
        "fans": cfg.get("fans", ""),
        "xtu": cfg.get("xtu", ""),
        "certificado": es_certificado(nombre),
    }


def _escribir_perfiles(perfiles: dict) -> None:
    ordenado = {n: perfiles[n] for n in perfil_juego.nombres_ordenados(perfiles)}
    datos_json.escribir(PERFILES, ordenado)


def _quitar_certificado(nombre: str) -> bool:
    crudo, error = datos_json.leer(CERTS, {})
    if error is not None:
        return False
    certs = datos_json.dic(crudo)
    if certs.pop(nombre, None) is None:
        return False
    try:
        datos_json.escribir(CERTS, certs)
    except Exception:
        return False
    return True


def guardar_perfil(nombre: str, cfg: dict, es_nuevo: bool = False) -> bool:
    """Guarda un perfil conservando claves desconocidas. Devuelve True si retiró
    la certificación (plan o techo de CPU cambiaron)."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El perfil necesita un nombre.")
    crudo, error = datos_json.leer(PERFILES, {})
    actuales = dict(crudo) if error is None and isinstance(crudo, dict) else {}
    if es_nuevo and nombre in actuales:
        raise ValueError(f"Ya existe un perfil llamado «{nombre}».")
    viejo = datos_json.perfil(actuales.get(nombre) or {})
    nuevo = datos_json.perfil(cfg)
    cambio = (str(viejo.get("cpu_max", 100)) != str(nuevo.get("cpu_max", 100))
              or planes_energia.resolver(viejo.get("plan", "alto"))[0]
              != planes_energia.resolver(nuevo.get("plan", "alto"))[0])
    actuales[nombre] = nuevo
    _escribir_perfiles(actuales)
    if cambio and not es_nuevo:
        return _quitar_certificado(nombre)
    return False


def borrar_perfil(nombre: str) -> None:
    crudo, error = datos_json.leer(PERFILES, {})
    if error is not None or not isinstance(crudo, dict):
        raise RuntimeError(f"No pude leer {os.path.basename(PERFILES)}: "
                           f"{error or 'estructura inválida'}")
    crudo.pop(nombre, None)
    _escribir_perfiles(crudo)
    _quitar_certificado(nombre)


def aplicar_plan(guid: str) -> dict:
    perfil_juego.fijar_plan(guid)
    planes_energia.invalisar()
    guid_activo, nombre = planes_energia.activo()
    return {"guid": guid_activo, "nombre": nombre,
            "planes": planes_energia.listar(forzar=True)}


def nombre_plan(valor) -> str:
    """Nombre real del plan que usaría un perfil en este equipo."""
    try:
        return planes_energia.resolver(valor)[1] or ""
    except Exception:
        return ""


def estilos() -> tuple[list[str], str]:
    return overlay_estilo.listar(), overlay_estilo.nombre_activo()


def fijar_estilo(nombre: str) -> str:
    return overlay_estilo.fijar_activo(nombre)


def posicion_overlay() -> dict:
    return overlay_estilo.ventana()


def fijar_posicion_overlay(ancla: str) -> dict:
    return overlay_estilo.fijar_ancla(ancla)


_MEMREDUCT: str | None | bool = False


def memreduct() -> str | None:
    """Ruta de memreduct.exe, o None si no está instalado. Se busca una vez."""
    global _MEMREDUCT
    if _MEMREDUCT is not False:
        return _MEMREDUCT
    local = os.environ.get("LOCALAPPDATA", "")
    candidatos = [
        # Primero la copia que viaja con AMPERA: version fija y en una carpeta
        # que (instalada) solo escribe el administrador. Asi no se ejecuta un
        # memreduct.exe dejado en una ruta escribible por el usuario.
        os.path.join(BASE, "tools", "MemReduct", "memreduct.exe"),
        r"C:\Program Files\Mem Reduct\memreduct.exe",
        r"C:\Program Files (x86)\Mem Reduct\memreduct.exe",
        os.path.join(local, "Mem Reduct", "memreduct.exe"),
    ]
    _MEMREDUCT = next((c for c in candidatos if c and os.path.exists(c)), None)
    return _MEMREDUCT


def ram_libre() -> float | None:
    """RAM disponible en GB, o None si psutil no responde."""
    try:
        import psutil
        return psutil.virtual_memory().available / 2 ** 30
    except Exception:
        return None


def limpiar_ram(profunda: bool = False) -> dict:
    """Lanza Mem Reduct y mide la RAM liberada. Bloquea ~5 s: usar en `Hilo`."""
    exe = memreduct()
    if not exe:
        raise FileNotFoundError("Mem Reduct")
    import psutil
    modo = "-clean:full" if profunda else "-clean"
    antes = psutil.virtual_memory().available
    subprocess.run([exe, modo], creationflags=_SIN_VENTANA, timeout=25,
                   capture_output=True, stdin=subprocess.DEVNULL)
    # La limpieza la hace la instancia de bandeja de Mem Reduct: es asíncrona.
    time.sleep(4)
    despues = psutil.virtual_memory().available
    return {"antes": antes / 2 ** 30, "despues": despues / 2 ** 30,
            "liberado": (despues - antes) / 2 ** 30}


# Solo procesos conocidos que no guardan trabajo del usuario. Cualquier otro
# (servicios, Windows, apps con ventana) queda fuera: la lista es la garantía.
_PROCESOS_SEGUROS = {
    "gamebar.exe": "proc_overlay",
    "gamebarftserver.exe": "proc_overlay",
    "xboxgamebarwidgets.exe": "proc_overlay",
    "xboxpcappft.exe": "proc_overlay",
    "widgetboard.exe": "proc_overlay",
    "spotifyxboxgamebarwebview.exe": "proc_overlay",
    "googleupdate.exe": "proc_update",
    "googleupdateonstartup.exe": "proc_update",
    "microsoftedgeupdate.exe": "proc_update",
    "microsoftedgeupdatem.exe": "proc_update",
    "jusched.exe": "proc_update",
    "jucheck.exe": "proc_update",
    "yourphone.exe": "proc_telefono",
    "phoneexperiencehost.exe": "proc_telefono",
}


def _pids_visibles() -> set[int]:
    """PIDs con alguna ventana visible: si el usuario la ve, no se toca."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pids: set[int] = set()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _visitar(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(pid.value)
            return True

        user32.EnumWindows(_visitar, 0)
        return pids
    except Exception:
        return set()


def escanear_procesos() -> dict:
    """Clasifica procesos ocultos: solo devuelve candidatos de la lista segura.

    Nunca incluye servicios (corren como SYSTEM), procesos de Windows ni
    procesos con ventana visible.
    """
    import psutil
    visibles = _pids_visibles()
    candidatos: list[dict] = []
    protegidos = 0
    for proc in psutil.process_iter(["pid", "name", "exe", "username",
                                     "memory_info"]):
        try:
            info = proc.info
            pid = info.get("pid")
            nombre = (info.get("name") or "").strip()
            if not pid or pid in (0, 4) or pid == os.getpid() or not nombre:
                continue
            usuario = str(info.get("username") or "").lower()
            exe = str(info.get("exe") or "").lower()
            motivo = _PROCESOS_SEGUROS.get(nombre.lower())
            protegido = (pid in visibles or not motivo
                         or usuario.startswith("nt authority")
                         or usuario.startswith("window manager")
                         or exe.startswith("c:\\windows\\"))
            if protegido:
                protegidos += 1
                continue
            memoria = info.get("memory_info")
            mb = (memoria.rss / 2 ** 20) if memoria is not None else 0.0
            candidatos.append({"pid": pid, "nombre": nombre,
                               "mb": round(mb, 1), "motivo": motivo})
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    candidatos.sort(key=lambda c: c["mb"], reverse=True)
    return {"candidatos": candidatos, "protegidos": protegidos,
            "total_mb": round(sum(c["mb"] for c in candidatos), 1)}


def cerrar_procesos(pids: list[int]) -> dict:
    """Termina los PIDs indicados (ya filtrados por `escanear_procesos`)."""
    import psutil
    cerrados: list[str] = []
    fallos: list[str] = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            nombre = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
            cerrados.append(nombre)
        except Exception as e:
            fallos.append(f"{pid}: {e}")
    return {"cerrados": cerrados, "fallos": fallos}


def _gpu() -> tuple[int | None, str]:
    nvml = None
    try:
        import pynvml as nvml
        nvml.nvmlInit()
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        temp = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
        nombre = nvml.nvmlDeviceGetName(h)
        return temp, (nombre.decode() if isinstance(nombre, bytes) else nombre or "")
    except Exception:
        return None, ""
    finally:
        if nvml is not None:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass


def leer_estado(completo: bool = False) -> dict:
    """Estado del sensor y de los planes. `completo` prueba también ACPI."""
    r: dict = {"lhm": False, "cpu": None, "cpu_acpi": None,
               "gpu": None, "gpu_nombre": "", "plan": None, "plan_guid": None,
               "planes": []}
    r["lhm"] = monitor_fase1.lhm_activo()
    if r["lhm"]:
        r["cpu"] = monitor_fase1.temp_cpu_lhm()
    elif completo:
        r["cpu_acpi"] = monitor_fase1.temp_cpu()
    r["gpu"], r["gpu_nombre"] = _gpu()
    r["plan_guid"], r["plan"] = planes_energia.activo()
    r["planes"] = planes_energia.listar(forzar=completo)
    return r


_CPU_LISTO = False
_ACPI = {"valor": None, "fuente": "", "instante": 0.0}


def _cpu_uso() -> float | None:
    """La primera medida no tiene referencia previa: se mide en 100 ms."""
    global _CPU_LISTO
    try:
        import psutil
        if not _CPU_LISTO:
            psutil.cpu_percent(None)
            valor = psutil.cpu_percent(0.1)
            _CPU_LISTO = True
            return valor
        return psutil.cpu_percent(None)
    except Exception:
        return None


def _temp_cpu_alterna() -> tuple[float | None, str]:
    """ACPI como respaldo, con caché de 30 s: PowerShell tarda varios segundos."""
    ahora = time.monotonic()
    if ahora - _ACPI["instante"] >= 30:
        valor = monitor_fase1.temp_cpu()
        _ACPI.update(valor=valor, fuente="acpi" if valor is not None else "",
                     instante=ahora)
    return _ACPI["valor"], _ACPI["fuente"]


def _gpu_rendimiento() -> dict:
    r = {"gpu": None, "gpu_nombre": "", "gpu_uso": None, "gpu_w": None,
         "gpu_reloj": None, "gpu_mem_reloj": None}
    nvml = None
    try:
        import pynvml as nvml
        nvml.nvmlInit()
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        nombre = monitor_fase1.dato(nvml.nvmlDeviceGetName, h)
        if isinstance(nombre, bytes):
            nombre = nombre.decode()
        r["gpu_nombre"] = (nombre or "").strip()
        r["gpu"] = monitor_fase1.dato(
            nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)
        uso = monitor_fase1.dato(nvml.nvmlDeviceGetUtilizationRates, h)
        r["gpu_uso"] = uso.gpu if uso is not None else None
        limite = monitor_fase1.dato(nvml.nvmlDeviceGetEnforcedPowerLimit, h)
        r["gpu_w"] = monitor_fase1.potencia_w(h, limite)
        r["gpu_reloj"] = monitor_fase1.dato(
            nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_GRAPHICS)
        r["gpu_mem_reloj"] = monitor_fase1.dato(
            nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_MEM)
    except Exception:
        pass
    finally:
        if nvml is not None:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
    return r


def leer_rendimiento() -> dict:
    """Lecturas de la pestaña Estado: CPU, GPU, RAM y disco del sistema."""
    import psutil

    r: dict = {"cpu_temp": None, "cpu_fuente": "", "cpu_uso": None,
               "cpu_reloj": None, "ram_pct": None, "ram_usada": None,
               "ram_total": None, "rom_pct": None, "rom_usada": None,
               "rom_total": None, "rom_unidad": os.environ.get("SystemDrive", "C:")}
    r.update(_gpu_rendimiento())

    temp = monitor_fase1.temp_cpu_lhm(timeout=1.5)
    if temp is not None:
        r["cpu_temp"], r["cpu_fuente"] = temp, "lhm"
    else:
        r["cpu_temp"], r["cpu_fuente"] = _temp_cpu_alterna()

    r["cpu_uso"] = _cpu_uso()
    reloj = monitor_fase1.dato(psutil.cpu_freq)
    r["cpu_reloj"] = round(reloj.current) if reloj is not None else None

    try:
        vm = psutil.virtual_memory()
        r["ram_pct"] = vm.percent
        r["ram_usada"] = round(vm.used / 2 ** 30, 1)
        r["ram_total"] = round(vm.total / 2 ** 30, 1)
    except Exception:
        pass

    for raiz in (r["rom_unidad"] + os.sep, os.path.abspath(os.sep)):
        try:
            disco = psutil.disk_usage(raiz)
        except Exception:
            continue
        r["rom_pct"] = disco.percent
        r["rom_usada"] = round(disco.used / 2 ** 30, 1)
        r["rom_total"] = round(disco.total / 2 ** 30, 1)
        break
    return r


# ---------- tipo de equipo y perfil recomendado ----------

_CHASIS_PORTATIL = {8, 9, 10, 14, 30, 31, 32}
_CHASIS_TORRE = {3, 4, 6, 7, 13, 15, 16, 17, 24}
_CHASIS_MINI = {5, 34, 35, 36}
_CHASIS_SERVIDOR = {23, 25, 27, 28, 29}


def _consulta_wmi() -> list[str]:
    """Chasis, CPU, fabricante/modelo y GPU con una sola llamada a PowerShell."""
    consulta = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$ch=(Get-CimInstance Win32_SystemEnclosure).ChassisTypes -join ',';"
        "$cpu=(Get-CimInstance Win32_Processor).Name;"
        "$cs=Get-CimInstance Win32_ComputerSystem;"
        "$vc=(Get-CimInstance Win32_VideoController).Name -join '/';"
        "$ch+'|'+$cpu+'|'+$cs.Manufacturer+'|'+$cs.Model+'|'+$vc"
    )
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", consulta],
            capture_output=True, text=True, encoding="oem", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=_SIN_VENTANA, timeout=15)
        linea = (salida.stdout or "").strip().lstrip("\ufeff")
        partes = linea.split("|")
        return (partes + [""] * 5)[:5]
    except Exception:
        return ["", "", "", "", ""]


def _tipo_equipo(chasis: list[int], bateria: bool, nucleos: int,
                 ram_total: float) -> str:
    if any(c in _CHASIS_PORTATIL for c in chasis) or bateria:
        return "portatil"
    if any(c in _CHASIS_SERVIDOR for c in chasis):
        return "servidor"
    if any(c in _CHASIS_MINI for c in chasis):
        return "mini"
    if any(c in _CHASIS_TORRE for c in chasis):
        return "torre"
    if ram_total >= 64 and nucleos >= 16:
        return "servidor"
    return "torre"


def info_equipo() -> dict:
    """Tipo de equipo y componentes. Bloquea ~1 s (PowerShell): usar en `Hilo`."""
    import platform
    import psutil

    r: dict = {"tipo": "torre", "fuente": "estimado", "chasis": [],
               "cpu": "", "nucleos": 0, "hilos": 0, "ram_total": 0.0,
               "gpu": "", "modelo": "", "bateria": False, "pct": None,
               "enchufado": None, "win": platform.release(),
               "build": 0}
    chasis_txt, cpu_txt, marca, modelo, gpu_txt = _consulta_wmi()
    try:
        r["chasis"] = [int(x) for x in chasis_txt.split(",") if x.strip()]
    except ValueError:
        r["chasis"] = []
    r["cpu"] = cpu_txt.strip()
    r["modelo"] = " ".join(x.strip() for x in (marca, modelo) if x.strip())
    r["gpu"] = gpu_txt.strip()
    r["fuente"] = "wmi" if r["chasis"] else "estimado"

    try:
        r["nucleos"] = psutil.cpu_count(logical=False) or 0
        r["hilos"] = psutil.cpu_count() or 0
        r["ram_total"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            r["bateria"] = True
            r["pct"] = round(bat.percent)
            r["enchufado"] = bool(bat.power_plugged)
    except Exception:
        pass

    try:
        build = sys.getwindowsversion().build
    except Exception:
        build = 0
    r["build"] = build
    if build >= 22000:
        r["win"] = "Windows 11"
    r["tipo"] = _tipo_equipo(r["chasis"], r["bateria"], r["nucleos"],
                             r["ram_total"])
    return r


_OBJETIVO = {
    "portatil": {"plan": "equilibrado", "pct": 80},
    "torre": {"plan": "alto", "pct": 100},
    "mini": {"plan": "equilibrado", "pct": 80},
    "servidor": {"plan": "alto", "pct": 100},
}
_AFINES = {"equilibrado": {"alto"}, "alto": {"equilibrado", "maximo"},
           "maximo": {"alto"}}


def objetivo_perfil(info: dict) -> dict:
    """Plan y techo de CPU que encajan con este equipo."""
    objetivo = dict(_OBJETIVO.get(info.get("tipo", "torre"), _OBJETIVO["torre"]))
    ram = float(info.get("ram_total") or 0)
    if ram and ram <= 8:
        objetivo["pct"] -= 10
    if ram and ram <= 4:
        objetivo["pct"] -= 10
    if info.get("tipo") in ("portatil", "mini") and not info.get("enchufado"):
        objetivo["pct"] -= 10
    objetivo["pct"] = max(30, min(100, objetivo["pct"]))
    return objetivo


def recomendar_perfil(info: dict, perfiles: dict,
                      certs: dict | None = None) -> dict:
    """Elige el perfil existente que mejor encaja, o propone uno nuevo."""
    certs = certs or {}
    objetivo = objetivo_perfil(info)
    nombre_plan = planes_energia.resolver(objetivo["plan"])[1] or objetivo["plan"]
    mejor_nombre, mejor_cfg, mejor_puntos = "", None, -1
    for nombre, cfg in (perfiles or {}).items():
        real = planes_energia.resolver(cfg.get("plan", "alto"))[1] or ""
        pct = int(cfg.get("cpu_max", 100) or 100)
        brecha = abs(pct - objetivo["pct"])
        # Un perfil a mas de 20 puntos del techo recomendado no es "apto":
        # mejor crear uno nuevo que aplicar algo que no encaja con el equipo.
        if brecha > 20:
            continue
        puntos = 3 if real == nombre_plan else (
            1 if real.lower() in {p.lower() for p in _AFINES.get(objetivo["plan"], ())}
            else 0)
        puntos += (4 if brecha <= 5 else 3 if brecha <= 10
                   else 2 if brecha <= 15 else 1)
        if cfg.get("cerrar"):
            puntos -= 1
        if (certs.get(nombre) or {}).get("certificado"):
            puntos += 2
        if puntos > mejor_puntos:
            mejor_nombre, mejor_cfg, mejor_puntos = nombre, cfg, puntos
    if mejor_cfg is not None and mejor_puntos >= 5:
        return {"crear": False, "nombre": mejor_nombre, "cfg": mejor_cfg,
                "puntos": mejor_puntos, "objetivo": objetivo}
    nuevo = {"plan": objetivo["plan"], "cpu_max": objetivo["pct"],
             "cerrar": [], "fans": "Auto", "exe": "", "prioridad": "normal",
             "nota": ""}
    return {"crear": True, "nombre": "", "cfg": nuevo, "puntos": 0,
            "objetivo": objetivo}


def optimizar_bateria() -> dict:
    """Plan equilibrado y techo de CPU bajo para estirar la pila."""
    plan = perfil_juego.fijar_plan("equilibrado")
    pct = perfil_juego.fijar_cpu_max(60)
    planes_energia.invalisar()
    return {"plan": plan or "", "pct": pct}


def modo_normal() -> dict:
    """Devuelve el equipo al plan equilibrado y al 100 % de CPU."""
    plan = perfil_juego.fijar_plan("equilibrado")
    pct = perfil_juego.fijar_cpu_max(100)
    planes_energia.invalisar()
    return {"plan": plan or "", "pct": pct}
