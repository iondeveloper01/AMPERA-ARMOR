"""Aviso de probable THERMAL THROTTLING — herramienta de AMPERA.

Vigila la temperatura de CPU (vía LhmServer:8085) y de GPU (vía NVML) con
umbrales INDEPENDIENTES que elige el usuario. Cuando un sensor se mantiene por
encima de su umbral:

  1) lanza 3 avisos separados ~15 s (notificación toast de Windows + una línea
     marcador "@@NOTIF@@..." que la GUI convierte en pop-up con sonido), y
  2) si la opción "proteger" está activa y la temperatura NO baja ("no frenado"),
     FRENA el equipo bajando el techo del CPU por escalones (PROCTHROTTLEMAX).
     En este Acer el límite de la GPU no se puede escribir por NVML, pero bajar
     el CPU enfría el chasis compartido y le da margen a la GPU.
     NUNCA apaga el equipo (a diferencia de guardia_termico.py).

Al salir (Ctrl+C / CTRL_BREAK desde la GUI) restaura el techo de CPU original en
su bloque finally, igual que watchdog.py.

Uso:
    py aviso_termico.py --cpu-umbral 90 --gpu-umbral 88 --proteger
    py aviso_termico.py --avisos 3 --separacion 15
"""
import argparse
import base64
import os
import subprocess
import sys
import time
from datetime import datetime

import pynvml as nvml

from rutas import BASE, logs
from monitor_fase1 import dato, lhm_activo, temp_cpu_lhm
from watchdog import (HISTERESIS, escalones_para, fijar_proc_max,
                      leer_proc_max_ac)

LOGS = logs()
RUTA_LOG = os.path.join(LOGS, "aviso_termico.log")
RUTA_ORIG = os.path.join(LOGS, "aviso_original_ceiling.txt")
MARCADOR = "@@NOTIF@@"          # la GUI detecta este prefijo y muestra pop-up
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NUEVO_GRUPO = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


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
    """Si una ejecución anterior murió sin restaurar (apagón/kill duro), el archivo
    queda con el techo original: lo restauramos al arrancar y lo borramos.
    Devuelve True si había algo pendiente y se restauró."""
    if not os.path.exists(RUTA_ORIG):
        return False
    try:
        with open(RUTA_ORIG, encoding="utf-8") as f:
            pct = int(f.read().strip())
    except Exception:
        _borrar_original()
        return False
    try:
        fijar_proc_max(pct)
    except Exception as e:
        # Fallo temporal: conservar el archivo para reintentar en el próximo arranque.
        registrar(f"No pude restaurar el techo {pct} % ({e}); lo reintentaré.")
        return False
    _borrar_original()
    registrar(f"AUTO-REPARACIÓN: una ejecución anterior no restauró el techo de CPU; "
              f"lo devuelvo a {pct} %.")
    return True


def restaurar_si_pendiente():
    """API para la GUI. En este equipo CTRL_BREAK/terminate matan el proceso SIN
    ejecutar su finally, así que la restauración del techo de CPU NO puede depender
    del hijo: la hace la GUI leyendo el archivo de seguridad. Idempotente;
    devuelve True si hubo que reparar."""
    return _auto_reparar()


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


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def toast_windows(titulo, texto):
    """Notificación nativa de Windows 10/11 (WinRT) sin dependencias externas.
    Se lanza en un PowerShell OCULTO y con -EncodedCommand para evitar problemas
    de comillas/inyección. Si algo falla, no rompe la herramienta."""
    xml = ('<toast><visual><binding template="ToastGeneric">'
           f'<text>{_xml_escape(titulo)}</text><text>{_xml_escape(texto)}</text>'
           '</binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Looping.Alarm2" loop="false"/>'
           '</toast>')
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('{xml.replace(chr(39), chr(39)*2)}');"
        "$n=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AMPERA');"
        "$n.Show([Windows.UI.Notifications.ToastNotification]::new($x));"
    )
    try:
        enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        # No bloqueante y en SU PROPIO grupo de proceso: así el hilo principal no
        # queda atrapado en un wait() cuando llega CTRL_BREAK (que debe poder
        # interrumpir el sleep y ejecutar el finally que restaura el techo de CPU).
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive",
                          "-EncodedCommand", enc],
                         creationflags=(_SIN_VENTANA | _NUEVO_GRUPO),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, close_fds=True)
    except Exception as e:
        registrar(f"(toast no disponible: {e})")


def avisar(titulo, texto):
    """Emite un aviso por las DOS vías: toast de Windows + marcador para la GUI."""
    try:
        print(f"{MARCADOR}{titulo}{MARCADOR}{texto}", flush=True)
    except Exception:
        pass
    toast_windows(titulo, texto)
    registrar(f"AVISO: {titulo} — {texto}")


def temp_gpu(h):
    return dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Aviso de probable thermal throttling")
    ap.add_argument("--cpu-umbral", type=float, default=90.0, help="°C de CPU que disparan el aviso")
    ap.add_argument("--gpu-umbral", type=float, default=90.0, help="°C de GPU que disparan el aviso")
    ap.add_argument("--proteger", action="store_true",
                    help="si la temp no baja tras los avisos, frena el CPU (nunca apaga)")
    ap.add_argument("--avisos", type=int, default=3, help="número de avisos (1-5)")
    ap.add_argument("--separacion", type=float, default=15.0, help="segundos entre avisos")
    ap.add_argument("--intervalo", type=float, default=2.0, help="segundos entre lecturas")
    ap.add_argument("--sostenido", type=int, default=2,
                    help="lecturas seguidas por encima para confirmar (evita picos sueltos)")
    a = ap.parse_args()
    a.avisos = max(1, min(5, a.avisos))

    nvml.nvmlInit()
    h = nvml.nvmlDeviceGetHandleByIndex(0)

    _auto_reparar()  # por si una ejecución anterior murió sin restaurar

    original_leido = leer_proc_max_ac()
    puede_frenar = original_leido is not None
    original = original_leido if puede_frenar else 100
    if a.proteger and not puede_frenar:
        # Sin lectura fiable del techo, restaurar "100" borraría un límite real
        # del usuario (40/55 %). Mejor avisar y no tocar nada.
        a.proteger = False
        registrar("No pude leer el techo de CPU actual; la protección queda "
                  "desactivada para no pisar tu límite (solo se avisará).")
    escalones = escalones_para(original)  # nunca por encima del techo del usuario
    tocado = False  # ¿hemos bajado el techo nosotros? (para restaurar al salir)

    hay_lhm = lhm_activo()
    registrar("=== Aviso térmico iniciado ===")
    registrar(f"Umbrales: CPU {a.cpu_umbral:.0f} °C · GPU {a.gpu_umbral:.0f} °C · "
              f"proteger={'SI' if a.proteger else 'NO'} · {a.avisos} avisos c/{a.separacion:.0f}s")
    registrar(f"GPU por NVML: OK · CPU por LHM(8085): {'activo' if hay_lhm else 'SIN 8085 (solo GPU)'}")
    if not hay_lhm:
        print("Aviso: LhmServer no responde en :8085; solo se vigilará la GPU.", flush=True)

    consec = 0        # lecturas seguidas por encima del umbral
    alertando = False # ya disparamos la secuencia de avisos (no repetir hasta re-armar)
    try:
        while True:
            time.sleep(a.intervalo)
            gt = temp_gpu(h)
            ct = temp_cpu_lhm() if hay_lhm else None
            if not hay_lhm and int(time.time()) % 30 == 0:
                hay_lhm = lhm_activo()

            caliente_cpu = ct is not None and ct >= a.cpu_umbral
            caliente_gpu = gt is not None and gt >= a.gpu_umbral
            caliente = caliente_cpu or caliente_gpu

            # re-armar cuando se enfría con histéresis
            fresco = ((ct is None or ct < a.cpu_umbral - HISTERESIS) and
                      (gt is None or gt < a.gpu_umbral - HISTERESIS))
            if fresco and (alertando or consec):
                registrar(f"Temperatura bajo control (CPU {ct} / GPU {gt} °C). Aviso re-armado.")
                if tocado:
                    fijar_proc_max(original)
                    tocado = False
                    _borrar_original()
                    registrar(f"Techo de CPU restaurado a {original} %.")
                alertando = False
                consec = 0
                continue

            if caliente:
                consec += 1
            else:
                consec = 0

            if caliente and consec >= a.sostenido and not alertando:
                alertando = True
                cuales = []
                if caliente_cpu:
                    cuales.append(f"CPU {ct:.0f}°C ≥ {a.cpu_umbral:.0f}°C")
                if caliente_gpu:
                    cuales.append(f"GPU {gt:.0f}°C ≥ {a.gpu_umbral:.0f}°C")
                motivo = " · ".join(cuales)
                registrar(f"UMBRAL SUPERADO ({motivo}). Lanzando {a.avisos} avisos.")
                # --- 3 avisos separados ~15 s ---
                for k in range(1, a.avisos + 1):
                    titulo = "AMPERA: probable thermal throttling"
                    texto = f"{motivo}  (aviso {k}/{a.avisos})"
                    avisar(titulo, texto)
                    if k < a.avisos:
                        time.sleep(a.separacion)

                # --- protección: solo frenar, nunca apagar ---
                if a.proteger:
                    gt2 = temp_gpu(h)
                    ct2 = temp_cpu_lhm() if hay_lhm else None
                    sigue = ((ct2 is not None and ct2 >= a.cpu_umbral) or
                             (gt2 is not None and gt2 >= a.gpu_umbral))
                    if sigue:
                        registrar("La temperatura NO bajó tras los avisos: FRENO el equipo "
                                  "(bajo el techo de CPU; nunca apago).")
                        avisar("AMPERA: frenando el equipo",
                               "La temperatura no bajó; reduzco el techo de CPU para enfriar. "
                               "No se apagará el equipo.")
                        freno = escalones[-1]
                        _guardar_original(original)   # red de seguridad ante kill duro
                        fijar_proc_max(freno)
                        tocado = True
                        registrar(f"Techo de CPU -> {freno} % (original {original} %).")
                    else:
                        registrar("La temperatura bajó sola tras los avisos; no hace falta frenar.")
            elif caliente and alertando and a.proteger and tocado:
                # ya frenado y sigue caliente: mantener el techo mínimo (nada más)
                pass

            time.sleep(0)  # punto de interrupción para CTRL_BREAK
    except KeyboardInterrupt:
        registrar("Interrumpido manualmente. Salgo.")
    finally:
        try:
            if tocado and puede_frenar:
                fijar_proc_max(original)
                _borrar_original()
                registrar(f"Techo de CPU restaurado a {original} %.")
        except Exception:
            pass
        try:
            nvml.nvmlShutdown()
        except Exception:
            pass
        registrar("=== Aviso térmico terminado ===")


if __name__ == "__main__":
    main()
