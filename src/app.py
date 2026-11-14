"""Interfaz gráfica del proyecto "Afterburner propio" — CustomTkinter.

Orquesta el backend Python ya probado (monitor_fase1, perfil_juego, certificar,
watchdog) sin reescribirlo: los sensores se leen en un hilo de fondo y las
herramientas largas (certificar / watchdog) corren como subprocesos cuya salida
se vuelca en vivo a la consola de abajo.

Detalle importante de seguridad: watchdog y certificar restauran la configuración
en su bloque `finally` (techo de CPU / auto-rollback). En Windows `terminate()`
NO ejecuta ese finally, así que aquí los paramos con CTRL_BREAK_EVENT sobre un
grupo de proceso nuevo, que sí provoca KeyboardInterrupt en el hijo y deja que
se limpie solo.

Uso:  py app.py
"""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk

import customtkinter as ctk
import psutil

import i18n
from i18n import t
from monitor_fase1 import (CPU_CRIT, CPU_WARN, GPU_CRIT, GPU_WARN, dato,
                           fan_gpu, lhm_activo, potencia_w, procesos_conflicto,
                           temp_cpu_lhm)
from perfil_juego import fijar_cpu_max, fijar_plan, nombres_ordenados
import planes_energia
import datos as datos_json
from certificar import en_bateria
import overlay_estilo

i18n.inicializar()

try:
    import pynvml as nvml
except Exception:
    nvml = None

from rutas import BASE, data_path, logs

PERFILES = data_path("perfiles_juegos.json")
CERTS = data_path("perfiles_certificados.json")

# Oculta la consola al lanzar procesos hijo en Windows (0 fuera de Windows).
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _buscar_memreduct():
    """Localiza memreduct.exe. Mem Reduct es single-instance: si su icono de
    bandeja ya corre, '-clean' le delega la limpieza y el proceso sale solo."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidatos = [
        r"C:\Program Files\Mem Reduct\memreduct.exe",
        r"C:\Program Files (x86)\Mem Reduct\memreduct.exe",
        os.path.join(local, "Mem Reduct", "memreduct.exe"),
        os.path.join(BASE, "tools", "MemReduct", "memreduct.exe"),
    ]
    for c in candidatos:
        if c and os.path.exists(c):
            return c
    return None


MEMREDUCT = _buscar_memreduct()

VERDE, AMARILLO, ROJO, GRIS = "#2e8b57", "#c9a227", "#c0392b", "#888888"
ACENTO = "#00b4d8"          # cian "gamer" para el logo y detalles (personalizable)

# Fuentes: Bahnschrift (técnica/industrial, viene con Windows 10/11) para la UI,
# Consolas para datos alineados y la consola. Si faltan, Tk sustituye sola.
UI_FONT = "Bahnschrift"
MONO_FONT = "Consolas"
ESC = 1.0                   # escala de tamaño de letra de la UI (personalizable)

# Variantes del acento para botones/seleccionados; se recalculan con _aplicar_acento.
ACENTO_SEL = "#0e7490"
ACENTO_SEL_HOVER = "#0b5c73"

# Texto de los botones huecos (claro, oscuro): legible sobre el fondo de cada tema.
TEXTO_BOTON = ("#101418", "#f2f6fa")

# Opciones que ofrece la pestaña Apariencia. Todas vienen con Windows 10/11 y se
# verificaron con Tk en este equipo; si alguna faltara, Tk usa su sustituta.
UI_FUENTES = [
    "Bahnschrift", "Bahnschrift SemiBold", "Bahnschrift Light",
    "Segoe UI", "Segoe UI Variable Display",
    "Arial", "Arial Rounded MT Bold", "Verdana", "Tahoma", "Trebuchet MS",
    "Calibri", "Candara", "Corbel", "Franklin Gothic Medium",
    "Century Gothic", "Georgia", "Impact", "Palatino Linotype",
    "Cascadia Code", "Cascadia Mono", "Consolas", "Courier New",
]
ACENTOS_PRESET = [
    ("Cian", "#00b4d8"),
    ("Verde", "#2ecc71"),
    ("Rosa", "#ff5da2"),
    ("Ámbar", "#ffb020"),
    ("Morado", "#9b5de5"),
    ("Rojo", "#ff4d4d"),
]
ESCALAS = [("S", 0.9), ("M", 1.0), ("L", 1.15)]
CONFIG_RUTA = data_path("config.json")


def _sombreado(hexcolor, factor):
    """Aclara (factor>1) u oscurece (factor<1) un color #rrggbb, saturando a 0-255."""
    try:
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r = max(0, min(255, int(r * factor)))
        g = max(0, min(255, int(g * factor)))
        b = max(0, min(255, int(b * factor)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hexcolor


def _tinte_hover(hexcolor):
    """Fondo suave para el hover de un botón hueco: claro en tema día y oscuro
    en tema noche, siempre derivado del color del borde."""
    return (_sombreado(hexcolor, 1.78), _sombreado(hexcolor, 0.30))


def _color_valido(hx):
    """True si hx es un #rrggbb usable como acento (se rechaza el negro puro,
    que volvería invisibles los detalles sobre el tema oscuro)."""
    return (isinstance(hx, str) and len(hx) == 7 and hx[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in hx[1:])
            and hx.lower() != "#000000")


def _aplicar_acento(hexcolor):
    """Fija el acento global y sus variantes oscuras para botones/seleccionados.
    Si el color es inválido o negro (p. ej. un config.json corrupto), se sana al
    cian por defecto en vez de dejar la interfaz sin acento visible."""
    global ACENTO, ACENTO_SEL, ACENTO_SEL_HOVER
    if not _color_valido(hexcolor):
        hexcolor = "#00b4d8"
    ACENTO = hexcolor
    ACENTO_SEL = _sombreado(hexcolor, 0.62)
    ACENTO_SEL_HOVER = _sombreado(hexcolor, 0.48)


def F(size, weight="normal", mono=False):
    return ctk.CTkFont(family=(MONO_FONT if mono else UI_FONT),
                       size=max(8, int(round(size * ESC))), weight=weight)


def _leer_apariencia():
    cfg, _ = datos_json.leer(CONFIG_RUTA, {})
    return datos_json.dic(datos_json.dic(cfg).get("apariencia"))


def _guardar_apariencia(apariencia):
    cfg, _ = datos_json.leer(CONFIG_RUTA, {})
    cfg = datos_json.dic(cfg)
    cfg["apariencia"] = datos_json.dic(apariencia)
    try:
        datos_json.escribir(CONFIG_RUTA, cfg)
    except Exception:
        pass


def _cargar_apariencia():
    """Aplica la apariencia guardada a los globales antes de construir la UI."""
    a = _leer_apariencia()
    _aplicar_acento(a.get("acento") or ACENTO)
    global UI_FONT, ESC
    if a.get("fuente") in UI_FUENTES:
        UI_FONT = a["fuente"]
    esc = a.get("escala")
    if isinstance(esc, (int, float)) and 0.8 <= esc <= 1.3:
        ESC = float(esc)
    return a


class Estado:
    """Estado compartido entre el hilo de sensores y la GUI."""
    def __init__(self):
        self.lock = threading.Lock()
        self.d = {}

    def set(self, **kw):
        with self.lock:
            self.d.update(kw)

    def get(self, k, default=None):
        with self.lock:
            return self.d.get(k, default)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(t("app.title"))
        self.geometry("920x700")
        self.minsize(840, 620)

        self.estado = Estado()
        self.cola = queue.Queue()
        self.proc = None
        self.proc_tag = None
        self.monitor_procs = []
        self.parar_sensores = threading.Event()

        # Auto-limpieza de RAM (Mem Reduct)
        self._auto_on = False
        self._auto_intervalo_ms = 15 * 60 * 1000
        self._auto_umbral_gb = 0.0

        # Tema inicial: noche (oscuro). col_mudo/col_fuerte se usan para los
        # textos con color propio que CustomTkinter no recolorea solo.
        self.tema = "dark"
        self.col_mudo = "#9aa4b2"
        self.col_fuerte = "#f0f6fc"
        self._mudos = []

        self._apariencia_lista = False   # veta cambios mientras se construye la UI
        _cargar_apariencia()   # acento / fuente / escala guardados en config.json
        self._construir()
        self._reparar_pendientes()   # por si una herramienta anterior murió sin limpiar
        self._arrancar_sensores()
        try:
            self._perfiles_mtime = os.path.getmtime(PERFILES)
        except OSError:
            self._perfiles_mtime = 0
        self.after(200, self._drenar_cola)
        self.after(1000, self._refrescar_panel)
        self.after(1500, self._vigilar_perfiles)
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    # ---------------- tema día / noche ----------------
    def _cambiar_tema(self, valor):
        dia = (valor == t("theme.day"))
        self.tema = "light" if dia else "dark"
        self.col_mudo = "#5a6472" if dia else "#9aa4b2"
        self.col_fuerte = "#0d1117" if dia else "#f0f6fc"
        ctk.set_appearance_mode(self.tema)
        self._log(t("log.theme_changed", val=valor.lower()), ACENTO)
        # Los CTkScrollableFrame no repintan su lienzo al cambiar de modo y
        # dejarían restos del tema anterior: se reconstruye la interfaz.
        # Se difiere para no destruir el selector dentro de su propio callback.
        self.after(0, self._reconstruir)

    # ---------------- idioma ----------------
    def _cambiar_idioma(self, valor):
        codigo = next((c for c, n in i18n.IDIOMAS.items() if n == valor), None)
        if not codigo or codigo == i18n.idioma_actual():
            return
        i18n.usar(codigo)
        i18n.guardar_idioma(codigo)
        self._reconstruir()
        self._log(t("log.language_changed", val=valor), ACENTO)

    def _reconstruir(self):
        """Redibuja toda la interfaz en el idioma activo. Conserva el texto de la
        consola, el estado de los botones de herramienta y el interruptor de
        auto-limpieza. Los bucles .after (sensores/log/perfiles) siguen vivos y
        pasan a usar los nuevos widgets automaticamente."""
        texto_log = ""
        try:
            texto_log = self.log.get("1.0", "end")
        except Exception:
            pass
        campos = self._leer_campos_tools()
        # conserva la pestaña activa (por posición: su nombre cambia con el idioma)
        pestana = 0
        try:
            pestana = list(self.tabs._tab_dict).index(self.tabs.get())
        except Exception:
            pass
        for w in self.winfo_children():
            w.destroy()
        self._mudos.clear()
        self._construir()
        self._restaurar_campos_tools(campos)
        try:
            self.tabs.set(list(self.tabs._tab_dict)[pestana])
        except Exception:
            pass
        self.title(t("app.title"))
        if texto_log.strip():
            self.log.configure(state="normal")
            self.log.insert("end", texto_log.rstrip("\n") + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        try:
            self._actualizar_boton_proc()
        except Exception:
            pass
        try:
            if self._auto_on:
                self.sw_auto.select()
        except Exception:
            pass

    def _leer_campos_tools(self):
        """Ajustes escritos a mano en Herramientas, para no perderlos al
        reconstruir la interfaz (idioma, tema, acento o fuente)."""
        campos = {}
        for clave, attr in (("override", "ent_override"),
                            ("profunda", "chk_profunda"),
                            ("auto", "sw_auto"),
                            ("intervalo", "ent_intervalo"),
                            ("umbral", "ent_umbral"),
                            ("aviso_cpu", "ent_aviso_cpu"),
                            ("aviso_gpu", "ent_aviso_gpu"),
                            ("proteger", "sw_proteger")):
            try:
                campos[clave] = getattr(self, attr).get()
            except Exception:
                pass
        return campos

    def _restaurar_campos_tools(self, campos):
        for clave, attr, tipo in (
                ("override", "ent_override", "texto"),
                ("intervalo", "ent_intervalo", "texto"),
                ("umbral", "ent_umbral", "texto"),
                ("aviso_cpu", "ent_aviso_cpu", "texto"),
                ("aviso_gpu", "ent_aviso_gpu", "texto"),
                ("profunda", "chk_profunda", "bool"),
                ("auto", "sw_auto", "bool"),
                ("proteger", "sw_proteger", "bool")):
            if clave not in campos:
                continue
            try:
                w = getattr(self, attr)
                if tipo == "texto":
                    w.delete(0, "end")
                    w.insert(0, str(campos[clave]))
                elif campos[clave]:
                    w.select()
                else:
                    w.deselect()
            except Exception:
                pass

    def _mudo(self, padre, texto, **kw):
        lbl = ctk.CTkLabel(padre, text=texto, text_color=self.col_mudo, font=F(11), **kw)
        self._mudos.append(lbl)
        return lbl

    def _entrada(self, padre, valor="", placeholder=None):
        """Caja de texto del editor de perfiles (fuente de la UI, alto comodo)."""
        ent = ctk.CTkEntry(padre, font=F(13), height=32,
                           placeholder_text=placeholder or "")
        if valor:
            ent.insert(0, str(valor))
        return ent

    def _marco(self, padre):
        """Marco de tarjeta con el mismo borde, radio y fondo en ambos temas."""
        return ctk.CTkFrame(padre, corner_radius=12, border_width=1,
                            border_color=("gray78", "gray20"),
                            fg_color=("gray92", "#1b2129"))

    def _boton(self, padre, texto=None, *, color=None, ancho=120, alto=34,
               fuente=None, **kw):
        """Botón hueco con borde del acento activo y esquinas de píldora.

        El relleno es transparente: el color solo aparece en el contorno y en el
        tintado suave del hover. `color` permite conservar el significado de los
        botones destructivos (rojo) y de los secundarios (gris)."""
        color = color or ACENTO
        return ctk.CTkButton(
            padre, text=texto, width=ancho, height=alto,
            corner_radius=alto // 2,
            fg_color="transparent", hover_color=_tinte_hover(color),
            border_width=2, border_color=color,
            text_color=TEXTO_BOTON,
            text_color_disabled=("#8a949e", "#5f6a75"),
            font=fuente or F(13, "bold"),
            **kw)

    # ---------------- construcción ----------------
    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(2, weight=2)

        self._construir_header()

        self.tabs = ctk.CTkTabview(
            self, corner_radius=12,
            segmented_button_fg_color=("#e6ebf1", "#161c24"),
            segmented_button_unselected_color=("#e6ebf1", "#161c24"),
            segmented_button_unselected_hover_color=("#d3dbe4", "#232d3a"),
            segmented_button_selected_color=(ACENTO_SEL, ACENTO_SEL),
            segmented_button_selected_hover_color=(ACENTO_SEL_HOVER, ACENTO_SEL_HOVER),
            text_color=("#0f2027", "#eaf2f6"),
            text_color_disabled=("#8a97a3", "#5c6773"),
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=(6, 4))
        try:
            self.tabs._segmented_button.configure(font=F(14, "bold"))
        except Exception:
            pass
        self.tab_panel = self.tabs.add(t("tab.panel"))
        self.tab_perfiles = self.tabs.add(t("tab.profiles"))
        self.tab_tools = self.tabs.add(t("tab.tools"))
        self.tab_apariencia = self.tabs.add(t("tab.appearance"))

        self._construir_panel(self.tab_panel)
        self._construir_perfiles(self.tab_perfiles)
        self._construir_tools(self.tab_tools)
        self._construir_apariencia(self.tab_apariencia)

        marco_log = self._marco(self)
        marco_log.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 10))
        marco_log.grid_rowconfigure(1, weight=1)
        marco_log.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(marco_log, text=t("console.title"), anchor="w",
                     font=F(14, "bold"), text_color=ACENTO).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        self.log = ctk.CTkTextbox(marco_log, wrap="word", font=F(12, mono=True),
                                  corner_radius=8, state="disabled")
        self.log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(2, 10))
        for linea in getattr(self, "_log_pendiente", []):
            self._log(linea)
        self._log_pendiente = []

    def _construir_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="AMPERA", font=F(30, "bold"),
                     text_color=ACENTO, anchor="w").grid(row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(header, anchor="w", text=t("header.subtitle"),
                           text_color=self.col_mudo, font=F(12))
        self._mudos.append(sub)
        sub.grid(row=1, column=0, sticky="w")

        caja_der = ctk.CTkFrame(header, fg_color="transparent")
        caja_der.grid(row=0, column=1, rowspan=2, sticky="e", padx=6)

        fila_idioma = ctk.CTkFrame(caja_der, fg_color="transparent")
        fila_idioma.pack(anchor="e")
        lbl_idioma = ctk.CTkLabel(fila_idioma, text=t("header.language"), font=F(11),
                                  text_color=self.col_mudo)
        self._mudos.append(lbl_idioma)
        lbl_idioma.pack(side="left", padx=(0, 6))
        self.sel_idioma = ctk.CTkOptionMenu(
            fila_idioma, values=list(i18n.IDIOMAS.values()),
            command=self._cambiar_idioma, width=118, font=F(13, "bold"),
            corner_radius=8, fg_color=("#dfe5ec", "#232d3a"),
            button_color=(ACENTO_SEL, ACENTO_SEL),
            button_hover_color=(ACENTO_SEL_HOVER, ACENTO_SEL_HOVER),
            text_color=("#0f2027", "#eaf2f6"))
        self.sel_idioma.set(i18n.nombre_actual())
        self.sel_idioma.pack(side="left")

        fila_tema = ctk.CTkFrame(caja_der, fg_color="transparent")
        fila_tema.pack(anchor="e", pady=(6, 0))
        lbl_tema = ctk.CTkLabel(fila_tema, text=t("header.theme"), font=F(11),
                                text_color=self.col_mudo)
        self._mudos.append(lbl_tema)
        lbl_tema.pack(side="left", padx=(0, 6))
        self.sel_tema = ctk.CTkSegmentedButton(
            fila_tema, values=[t("theme.day"), t("theme.night")],
            command=self._cambiar_tema,
            font=F(13, "bold"), corner_radius=8,
            selected_color=(ACENTO_SEL, ACENTO_SEL),
            selected_hover_color=(ACENTO_SEL_HOVER, ACENTO_SEL_HOVER),
            unselected_color=("#dfe5ec", "#232d3a"),
            fg_color=("#dfe5ec", "#232d3a"))
        self.sel_tema.set(t("theme.day") if self.tema == "light" else t("theme.night"))
        self.sel_tema.pack(side="left")

        # subrayado cian bajo la cabecera (toque "pro")
        ctk.CTkFrame(header, height=2, corner_radius=0, fg_color=ACENTO).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _construir_panel(self, p):
        p.grid_columnconfigure((0, 1, 2), weight=1)
        p.grid_rowconfigure((0, 1), weight=1)
        self.lbl_gpu = self._tarjeta(p, t("panel.gpu"), 0, 0)
        self.lbl_cpu = self._tarjeta(p, t("panel.cpu"), 0, 1)
        self.lbl_ram = self._tarjeta(p, t("panel.ram"), 0, 2)
        self.lbl_estado = self._tarjeta(p, t("panel.status"), 1, 0, colspan=3)

    def _tarjeta(self, padre, titulo, fila, col, colspan=1):
        m = self._marco(padre)
        m.grid(row=fila, column=col, columnspan=colspan, sticky="nsew", padx=6, pady=6)
        ctk.CTkLabel(m, text=titulo, font=F(16, "bold"),
                     text_color=ACENTO).pack(anchor="w", padx=12, pady=(10, 0))
        ctk.CTkFrame(m, height=1, corner_radius=0,
                     fg_color=("gray75", "#2c3542")).pack(fill="x", padx=12, pady=(6, 0))
        lbl = ctk.CTkLabel(m, text="...", justify="left", anchor="w",
                           font=F(13, mono=True))
        lbl.pack(anchor="w", padx=12, pady=(6, 12), fill="both", expand=True)
        return lbl

    def _construir_perfiles(self, p):
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(p, text=t("profiles.title"),
                     font=F(16, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        # Barra con los planes de energia que hay en ESTE equipo (los lee Windows
        # en el momento, no una lista fija): que el usuario vea su propio plan.
        self.lbl_planes = self._mudo(p, "...", anchor="w", justify="left")
        self.lbl_planes.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
        self.marco_perfiles = ctk.CTkScrollableFrame(p)
        self.marco_perfiles.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        self.marco_perfiles.grid_columnconfigure(0, weight=1)
        barra = ctk.CTkFrame(p, fg_color="transparent")
        barra.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 8))
        # Todo dentro de 'barra' va con grid (mezclar pack y grid en el mismo
        # marco Tcl lo prohibe, y aqui el desplegado necesita ocultarse con grid_remove).
        barra.grid_columnconfigure(2, weight=1)
        # UN solo boton con las acciones de la pestaña (crear / refrescar / modo
        # normal). Con los botones de cada perfil siempre a la vista, la pestaña
        # se llenaba de botones y costaba encontrar lo que uno buscaba.
        self.btn_barra = self._boton(barra, t("profiles.actions") + " ▾",
                                     ancho=160, command=self._alternar_barra)
        self.btn_barra.grid(row=0, column=0, padx=4, pady=2)
        self.barra_extras = ctk.CTkFrame(barra, fg_color=("gray86", "#232d3a"),
                                         corner_radius=10)
        self.barra_extras.grid(row=0, column=1, padx=(8, 0))
        self.barra_extras.grid_remove()
        for texto, orden, funcion, color in (
                (t("profiles.create"), 0, self._crear_perfil, ACENTO),
                (t("profiles.refresh"), 1, self._cargar_perfiles, None),
                (t("profiles.normal"), 2, self._modo_normal, GRIS)):
            self._boton(self.barra_extras, texto, ancho=150, color=color,
                        command=funcion).grid(row=0, column=orden, padx=4, pady=6)
        self._barra_abierta = False
        self._cargar_perfiles()

    def _alternar_barra(self, abrir=None):
        """Muestra u oculta el desplegado de acciones de la pestaña Perfiles."""
        nuevo = (not self._barra_abierta) if abrir is None else abrir
        self._barra_abierta = bool(nuevo)
        if nuevo:
            self.barra_extras.grid()
        else:
            self.barra_extras.grid_remove()
        self.btn_barra.configure(text=t("profiles.actions") +
                                 (" ▴" if self._barra_abierta else " ▾"))

    def _refrescar_planes(self):
        """Vuelve a leer powercfg y pinta la barra de planes de la pestaña."""
        planos = planes_energia.listar(forzar=True)
        # El asterisco de `powercfg /list` ya dice cual esta en uso; solo si no
        # aparece se pregunta otra vez (la lista de respaldo no trae marca).
        en_uso = next((p["nombre"] for p in planos if p["activo"]), None)
        if en_uso is None:
            en_uso = planes_energia.activo()[1]
        nombres = " · ".join(p["nombre"] for p in planos)
        if en_uso:
            texto = t("plans.bar", n=len(planos), lista=nombres, actual=en_uso)
        else:
            texto = t("plans.bar_sin_activo", n=len(planos), lista=nombres)
        try:
            self.lbl_planes.configure(text=texto)
        except Exception:
            pass

    def _perfiles_iniciales(self):
        """Semilla limpia junto al exe o, si no hay, un perfil de fabrica."""
        semilla = os.path.join(BASE, "perfiles_juegos.json")
        if os.path.abspath(semilla) != os.path.abspath(PERFILES):
            crudo, error = datos_json.leer(semilla, None)
            if error is None and isinstance(crudo, dict) and crudo:
                return datos_json.perfiles(crudo)
        return datos_json.perfiles_defecto()

    def _cargar_perfiles(self):
        for w in self.marco_perfiles.winfo_children():
            w.destroy()
        self._filas = {}
        self._refrescar_planes()
        crudo, error = datos_json.leer(PERFILES, None)
        aviso = ""
        if error is not None or not isinstance(crudo, dict):
            motivo = error or "estructura inesperada"
            apartado = datos_json.cuarentena(PERFILES, logs())
            perfiles = self._perfiles_iniciales()
            try:
                datos_json.escribir(PERFILES, perfiles)
            except Exception:
                pass
            aviso = t("profiles.repaired",
                      archivo=os.path.basename(apartado or PERFILES), err=motivo)
            self._log(aviso, AMARILLO)
        else:
            perfiles = datos_json.perfiles(crudo)
        certs, _ = datos_json.leer(CERTS, {})
        certs = datos_json.dic(certs)
        aumento = 1 if aviso else 0
        if aviso:
            self._mudo(self.marco_perfiles, aviso, anchor="w", justify="left",
                       wraplength=760).grid(row=0, column=0, sticky="ew",
                                            padx=8, pady=(4, 8))
        for i, nombre in enumerate(nombres_ordenados(perfiles), start=aumento):
            cfg = perfiles[nombre]
            fila = self._marco(self.marco_perfiles)
            fila.grid(row=i, column=0, sticky="ew", padx=4, pady=4)
            fila.grid_columnconfigure(1, weight=1)
            cert = datos_json.dic(certs.get(nombre))
            ok = cert.get("certificado")
            badge = t("profiles.certified") if ok else t("profiles.uncertified")
            color = VERDE if ok else AMARILLO
            ctk.CTkLabel(fila, text=badge, width=130,
                         text_color=color,
                         font=F(12, "bold")).grid(
                row=0, column=0, rowspan=3, padx=8, pady=8)
            ctk.CTkLabel(fila, text=nombre, font=F(16, "bold"),
                         anchor="w").grid(row=0, column=1, sticky="ew", padx=4, pady=(6, 0))
            self._mudo(fila, cfg.get("nota", ""), anchor="w", justify="left").grid(
                row=1, column=1, sticky="ew", padx=4, pady=(0, 2))
            # El plan se muestra con el nombre que usa Windows, no con el codigo.
            guardado = cfg.get("plan", "alto")
            visible = planes_energia.nombre_si_existe(guardado)
            if visible:
                plan_txt = t("profiles.plan", plan=visible)
            else:
                _, caera = planes_energia.resolver(guardado)
                plan_txt = t("profiles.plan_missing",
                             plan=guardado, usara=caera or t("plans.unknown"))
            self._mudo(fila, plan_txt, anchor="w", justify="left").grid(
                row=2, column=1, sticky="ew", padx=4, pady=(0, 6))
            # UN solo boton por perfil: al pulsarlo se despliegan Aplicar /
            # Certificar / Ver-editar / Borrar dentro de la misma tarjeta.
            btn = self._boton(fila, t("profiles.actions") + " ▾", ancho=120,
                              command=lambda n=nombre: self._alternar_acciones(n))
            btn.grid(row=0, column=2, rowspan=3, padx=8, pady=8)
            panel = ctk.CTkFrame(fila, fg_color=("gray86", "#232d3a"), corner_radius=10)
            panel.grid(row=4, column=0, columnspan=3, sticky="ew", padx=6, pady=(0, 8))
            panel.grid_remove()
            for texto, orden, funcion, color in (
                    (t("profiles.apply"), 0, lambda n=nombre, c=cfg: self._aplicar(n, c),
                     ACENTO),
                    (t("profiles.certify"), 1, lambda n=nombre: self._certificar(n),
                     None),
                    (t("profiles.edit"), 2, lambda n=nombre: self._editar_perfil(n),
                     None),
                    (t("profiles.delete"), 3, lambda n=nombre: self._borrar_perfil(n),
                     ROJO)):
                self._boton(panel, texto, ancho=112, color=color,
                            command=lambda f=funcion: self._accion(f)).grid(
                    row=0, column=orden, padx=4, pady=6)
            self._filas[nombre] = {"btn": btn, "panel": panel, "abierto": False}
            if ok and cert.get("fecha"):
                ctk.CTkLabel(fila, text=t("profiles.cert_date", fecha=cert['fecha'],
                                          pico=cert.get('pico_gpu_c', '?')),
                             font=F(10), text_color=GRIS).grid(
                    row=3, column=1, sticky="w", padx=4, pady=(0, 6))

    def _plegar_acciones(self):
        """Deja cada tarjeta de perfil otra vez en una linea corta."""
        for fila in self._filas.values():
            fila["abierto"] = False
            fila["panel"].grid_remove()
            fila["btn"].configure(text=t("profiles.actions") + " ▾")

    def _alternar_acciones(self, nombre, abrir=None):
        """Despliega las acciones de UN perfil y pliega las de los demas."""
        fila = self._filas.get(nombre)
        if fila is None:
            return
        nuevo = (not fila["abierto"]) if abrir is None else abrir
        self._plegar_acciones()
        self._alternar_barra(abrir=False)
        if nuevo:
            fila["abierto"] = True
            fila["panel"].grid()
            fila["btn"].configure(text=t("profiles.actions") + " ▴")
            self.after(20, lambda f=fila: self._ver_panel_acciones(f))

    def _ver_panel_acciones(self, fila):
        """Desplaza la lista lo justo para que el desplegado quede a la vista."""
        try:
            self.update_idletasks()
            canvas = self.marco_perfiles._parent_canvas
            alto_total = max(1, self.marco_perfiles.winfo_height())
            alto_visible = max(1, canvas.winfo_height())
            y_fin = (fila["btn"].master.winfo_y() + fila["panel"].winfo_y()
                     + fila["panel"].winfo_height())
            if y_fin > alto_visible:
                canvas.yview_moveto(min(1.0, (y_fin - alto_visible + 12) / alto_total))
        except Exception:
            pass

    def _accion(self, funcion):
        """Ejecuta la accion elegida y pliega el desplegado."""
        funcion()
        self._plegar_acciones()

    def _construir_tools(self, p):
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        p = scroll  # las tarjetas siguientes se construyen dentro del scroll
        caja = self._marco(p)
        caja.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(caja, text=t("tools.watchdog.title"), font=F(16, "bold"),
                     text_color=ACENTO, anchor="w").pack(anchor="w", padx=10, pady=(8, 0))
        self._mudo(caja, t("tools.watchdog.desc"),
                   anchor="w", justify="left").pack(anchor="w", padx=10, pady=(0, 6))
        fila = ctk.CTkFrame(caja, fg_color="transparent")
        fila.pack(anchor="w", padx=10, pady=(0, 10), fill="x")
        self.btn_watch = self._boton(fila, t("tools.watchdog.start"), ancho=150,
                                     command=self._toggle_watchdog)
        self.btn_watch.pack(side="left", padx=4)
        ctk.CTkLabel(fila, text=t("tools.watchdog.override"), font=F(12)).pack(side="left", padx=(12, 4))
        self.ent_override = ctk.CTkEntry(fila, width=60, font=F(13))
        self.ent_override.insert(0, "0")
        self.ent_override.pack(side="left")

        caja2 = self._marco(p)
        caja2.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(caja2, text=t("tools.monitor.title"), font=F(16, "bold"),
                     text_color=ACENTO, anchor="w").pack(anchor="w", padx=10, pady=(8, 0))
        self._mudo(caja2, t("tools.monitor.desc"),
                   anchor="w", justify="left").pack(anchor="w", padx=10, pady=(0, 6))
        fila2 = ctk.CTkFrame(caja2, fg_color="transparent")
        fila2.pack(anchor="w", padx=10, pady=(0, 10))
        self._boton(fila2, t("tools.monitor.open"), ancho=230,
                    command=self._abrir_monitor).pack(side="left", padx=4)

        caja3 = self._marco(p)
        caja3.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(caja3, text=t("tools.session.title"),
                     font=F(16, "bold"), text_color=ACENTO, anchor="w").pack(
            anchor="w", padx=10, pady=(8, 0))
        self._mudo(caja3, t("tools.session.desc"),
                   anchor="w", justify="left").pack(anchor="w", padx=10, pady=(0, 6))
        fila3 = ctk.CTkFrame(caja3, fg_color="transparent")
        fila3.pack(anchor="w", padx=10, pady=(0, 4))
        self.btn_sesion = self._boton(fila3, t("tools.session.start"), ancho=150,
                                      command=self._toggle_sesion)
        self.btn_sesion.pack(side="left", padx=4)
        ctk.CTkLabel(fila3, text=t("tools.session.needs_lhm"),
                     text_color=GRIS, font=F(11)).pack(side="left", padx=8)

        fila3b = ctk.CTkFrame(caja3, fg_color="transparent")
        fila3b.pack(anchor="w", padx=10, pady=(0, 10), fill="x")
        ctk.CTkLabel(fila3b, text=t("tools.session.style"), font=F(12)).pack(side="left", padx=(4, 6))
        self.opt_estilo = ctk.CTkOptionMenu(fila3b, values=overlay_estilo.listar(),
                                            command=self._cambiar_estilo, width=170, font=F(12))
        self.opt_estilo.set(overlay_estilo.nombre_activo())
        self.opt_estilo.pack(side="left", padx=4)
        self._boton(fila3b, t("tools.session.create_style"), ancho=130,
                    fuente=F(12, "bold"), command=self._crear_estilo).pack(
            side="left", padx=(12, 4))
        self.btn_borrar_estilo = self._boton(fila3b, t("tools.session.delete_style"),
                                             ancho=80, color=ROJO, fuente=F(12),
                                             command=self._borrar_estilo)
        self.btn_borrar_estilo.pack(side="left", padx=4)
        self._refrescar_borrar_estilo()

        caja4 = self._marco(p)
        caja4.grid(row=3, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(caja4, text=t("tools.mem.title"), font=F(16, "bold"),
                     text_color=ACENTO, anchor="w").pack(anchor="w", padx=10, pady=(8, 0))
        self._mudo(caja4, t("tools.mem.desc"),
                   anchor="w", justify="left").pack(anchor="w", padx=10, pady=(0, 6))
        if not MEMREDUCT:
            self._mudo(caja4, t("tools.mem.notfound"),
                       anchor="w", justify="left", text_color=AMARILLO).pack(
                anchor="w", padx=10, pady=(0, 8))
        fila4 = ctk.CTkFrame(caja4, fg_color="transparent")
        fila4.pack(anchor="w", padx=10, pady=(0, 4), fill="x")
        self.btn_limpiar = self._boton(fila4, t("tools.mem.clean_now"), ancho=150,
                                       command=self._limpiar_ram,
                                       state=("normal" if MEMREDUCT else "disabled"))
        self.btn_limpiar.pack(side="left", padx=4)
        self.chk_profunda = ctk.CTkCheckBox(
            fila4, text=t("tools.mem.deep"), font=F(12),
            state=("normal" if MEMREDUCT else "disabled"))
        self.chk_profunda.pack(side="left", padx=(14, 4))
        fila4b = ctk.CTkFrame(caja4, fg_color="transparent")
        fila4b.pack(anchor="w", padx=10, pady=(2, 10), fill="x")
        self.sw_auto = ctk.CTkSwitch(fila4b, text=t("tools.mem.auto"), font=F(13, "bold"),
                                     command=self._toggle_auto_limpieza,
                                     state=("normal" if MEMREDUCT else "disabled"))
        self.sw_auto.pack(side="left", padx=4)
        ctk.CTkLabel(fila4b, text=t("tools.mem.every"), font=F(12)).pack(side="left", padx=(14, 4))
        self.ent_intervalo = ctk.CTkEntry(fila4b, width=52, font=F(13))
        self.ent_intervalo.insert(0, "15")
        self.ent_intervalo.pack(side="left")
        ctk.CTkLabel(fila4b, text=t("tools.mem.if_free"), font=F(12)).pack(side="left", padx=(12, 4))
        self.ent_umbral = ctk.CTkEntry(fila4b, width=52, font=F(13))
        self.ent_umbral.insert(0, "3")
        self.ent_umbral.pack(side="left")
        ctk.CTkLabel(fila4b, text=t("tools.mem.gb_always"), font=F(12),
                     text_color=GRIS).pack(side="left", padx=(4, 0))

        caja5 = self._marco(p)
        caja5.grid(row=4, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkLabel(caja5, text=t("tools.aviso.title"), font=F(16, "bold"),
                     text_color=ACENTO, anchor="w").pack(anchor="w", padx=10, pady=(8, 0))
        self._mudo(caja5, t("tools.aviso.desc"),
                   anchor="w", justify="left").pack(anchor="w", padx=10, pady=(0, 6))
        fila5 = ctk.CTkFrame(caja5, fg_color="transparent")
        fila5.pack(anchor="w", padx=10, pady=(0, 4), fill="x")
        self.btn_aviso = self._boton(fila5, t("tools.aviso.start"), ancho=150,
                                     command=self._toggle_aviso)
        self.btn_aviso.pack(side="left", padx=4)
        ctk.CTkLabel(fila5, text=t("tools.aviso.cpu_umbral"), font=F(12)).pack(side="left", padx=(12, 4))
        self.ent_aviso_cpu = ctk.CTkEntry(fila5, width=52, font=F(13))
        self.ent_aviso_cpu.insert(0, "90")
        self.ent_aviso_cpu.pack(side="left")
        ctk.CTkLabel(fila5, text=t("tools.aviso.gpu_umbral"), font=F(12)).pack(side="left", padx=(10, 4))
        self.ent_aviso_gpu = ctk.CTkEntry(fila5, width=52, font=F(13))
        self.ent_aviso_gpu.insert(0, "90")
        self.ent_aviso_gpu.pack(side="left")
        fila5b = ctk.CTkFrame(caja5, fg_color="transparent")
        fila5b.pack(anchor="w", padx=10, pady=(2, 4), fill="x")
        self.sw_proteger = ctk.CTkSwitch(fila5b, text=t("tools.aviso.protect"), font=F(12, "bold"))
        self.sw_proteger.select()
        self.sw_proteger.pack(side="left", padx=4)
        ctk.CTkLabel(fila5b, text=t("tools.aviso.needs_lhm"),
                     text_color=GRIS, font=F(11)).pack(side="left", padx=8)

    # ---------------- sensores en hilo ----------------
    def _arrancar_sensores(self):
        t = threading.Thread(target=self._loop_sensores, daemon=True)
        t.start()

    def _loop_sensores(self):
        h = None
        if nvml:
            try:
                nvml.nvmlInit()
                h = nvml.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                h = None
        psutil.cpu_percent(None)
        while not self.parar_sensores.is_set():
            d = {}
            if h is not None:
                d["gpu_temp"] = dato(nvml.nvmlDeviceGetTemperature, h, nvml.NVML_TEMPERATURE_GPU)
                d["gpu_clk"] = dato(nvml.nvmlDeviceGetClockInfo, h, nvml.NVML_CLOCK_GRAPHICS)
                lim = dato(nvml.nvmlDeviceGetEnforcedPowerLimit, h)
                d["gpu_pow"] = potencia_w(h, lim)
                d["gpu_lim"] = round(lim / 1000) if lim else None
                uso = dato(nvml.nvmlDeviceGetUtilizationRates, h)
                d["gpu_uso"] = uso.gpu if uso else None
                vinfo = dato(nvml.nvmlDeviceGetMemoryInfo, h)
                d["vram"] = round(vinfo.used / 2 ** 30, 2) if vinfo else None
            d["cpu_temp"] = temp_cpu_lhm() if lhm_activo() else None
            d["cpu_uso"] = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            d["ram_pct"] = vm.percent
            d["ram_libre"] = round(vm.available / 2 ** 30, 1)
            d["ac"] = not en_bateria()
            d["lhm"] = lhm_activo()
            d["conflictos"] = procesos_conflicto()
            self.estado.set(**d)
            self.parar_sensores.wait(1.0)
        if h is not None:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass

    def _refrescar_panel(self):
        e = self.estado
        g = lambda k, d="?" : e.get(k, d)
        def temp_color(t, warn, crit):
            if t is None or t == "?":
                return None
            return ROJO if t >= crit else (AMARILLO if t >= warn else VERDE)

        gt = g("gpu_temp")
        pow_val = g("gpu_pow")
        self.lbl_gpu.configure(text=t(
            "panel.gpu.body",
            temp=(gt if gt is not None else "?"),
            clk=g("gpu_clk"),
            pow=(f"{pow_val:.1f}" if isinstance(pow_val, float) else "?"),
            lim=g("gpu_lim"), uso=g("gpu_uso"), vram=g("vram")))
        self.lbl_gpu.configure(text_color=temp_color(gt, GPU_WARN, GPU_CRIT) or self.col_fuerte)

        ct = g("cpu_temp")
        lhm_txt = t("panel.cpu.lhm_ok") if g("lhm") else t("panel.cpu.lhm_no")
        self.lbl_cpu.configure(text=t(
            "panel.cpu.body",
            temp=(ct if ct is not None else t("common.nd")),
            uso=g("cpu_uso"), lhm=lhm_txt))
        self.lbl_cpu.configure(text_color=temp_color(ct, CPU_WARN, CPU_CRIT) or self.col_fuerte)

        self.lbl_ram.configure(text=t("panel.ram.body", pct=g("ram_pct"), libre=g("ram_libre")))

        ac = g("ac")
        conf = g("conflictos") or []
        lineas = [
            t("panel.status.power_ac") if ac else t("panel.status.power_bat"),
            t("panel.status.lhm_yes") if g("lhm") else t("panel.status.lhm_no"),
            t("panel.status.conf", val=", ".join(conf) if conf else t("panel.status.conf_none")),
        ]
        self.lbl_estado.configure(text="\n".join(lineas))
        self.lbl_estado.configure(text_color=GRIS if (ac and g("lhm") and not conf) else AMARILLO)

        self.after(1000, self._refrescar_panel)

    # ---------------- acciones ----------------
    def _aplicar(self, nombre, cfg):
        cfg = datos_json.perfil(cfg)
        if self.proc and self.proc.poll() is None:
            self._log(t("profiles.busy"), AMARILLO)
            return
        certs, _ = datos_json.leer(CERTS, {})
        certs = datos_json.dic(certs)
        if not datos_json.dic(certs.get(nombre)).get("certificado"):
            self._log(t("profiles.not_certified", nombre=nombre), AMARILLO)
        cerrar = cfg.get("cerrar", [])
        if cerrar:
            ok = self._preguntar(t("profiles.close_title"),
                                 t("profiles.close_ask", apps=", ".join(cerrar)))
            if ok:
                for app in cerrar:
                    # Lista de argumentos, sin shell: un nombre de perfil no puede
                    # convertirse en una orden extra de cmd.exe.
                    subprocess.run(["taskkill", "/im", app, "/f"],
                                   capture_output=True, text=True,
                                   creationflags=_SIN_VENTANA)
                    self._log(t("profiles.closed", app=app))
        plan = fijar_plan(cfg.get("plan", "alto"))
        pct = fijar_cpu_max(cfg.get("cpu_max", 100))
        self._log(t("profiles.applied", nombre=nombre,
                    plan=plan or t("plans.unknown"), pct=pct), VERDE)
        self._refrescar_planes()
        if cfg.get("fans"):
            self._log(t("profiles.fans_manual", val=cfg['fans']), AMARILLO)
        if cfg.get("xtu"):
            self._log(t("profiles.xtu_manual", val=cfg['xtu']), AMARILLO)

    def _modo_normal(self):
        plan = fijar_plan("equilibrado")
        fijar_cpu_max(100)
        self._log(t("profiles.normal_done", plan=plan or t("plans.unknown")), VERDE)
        self._refrescar_planes()

    def _certificar(self, nombre):
        self._lanzar("certificar", [nombre], f"certificar:{nombre}")

    def _borrar_perfil(self, nombre):
        """Borra un perfil de perfiles_juegos.json (y su certificación) tras confirmar."""
        if not self._preguntar(t("profiles.delete_title"),
                               t("profiles.delete_ask", nombre=nombre)):
            return
        crudo, error = datos_json.leer(PERFILES, None)
        if error is not None or not isinstance(crudo, dict):
            self._log(t("profiles.read_error", err=error or "estructura inválida"), ROJO)
            return
        perfiles = crudo
        perfiles.pop(nombre, None)
        err = self._escribir_perfiles(perfiles)
        if err is not None:
            self._log(t("profiles.delete_error", err=err), ROJO)
            return
        self._quitar_certificado(nombre)
        try:
            self._perfiles_mtime = os.path.getmtime(PERFILES)
        except OSError:
            self._perfiles_mtime = 0
        self._log(t("profiles.deleted", nombre=nombre), VERDE)
        self._cargar_perfiles()

    # Claves de perfiles_juegos.json que esta ventana puede editar; las demas se
    # guardan tal cual (si no, "xtu" y compania desapareceria al salvar).
    CAMPOS_EDITOR = ("plan", "cpu_max", "cerrar", "fans", "exe", "prioridad", "nota")

    def _escribir_perfiles(self, perfiles):
        """Guarda perfiles_juegos.json ordenado A-Z con escritura atomica
        (primero el .tmp y despues os.replace), para que un corte de luz o un
        cierre brusco no deje el archivo a medias. Devuelve None si pudo, o el
        error si no."""
        ordenado = {n: perfiles[n] for n in nombres_ordenados(perfiles)}
        tmp = PERFILES + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(ordenado, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, PERFILES)
            return None
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return e

    def _quitar_certificado(self, nombre):
        """Borra la certificación de un perfil. Devuelve True si la habia."""
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

    def _editar_perfil(self, nombre):
        """Visor y editor de la configuracion de UN perfil.

        Quien no sabe de informatica no deberia abrir perfiles_juegos.json con el
        bloc de notas: aqui cada ajuste tiene su nombre, los menus ofrecen los
        planes que existen en el equipo y el techo de CPU se mueve con un cursor.
        Las claves que esta ventana no muestra (por ejemplo "xtu") se conservan.
        """
        from tkinter import filedialog

        crudo, error = datos_json.leer(PERFILES, None)
        if error is not None or not isinstance(crudo, dict):
            self._log(t("profiles.read_error", err=error or "estructura inválida"), ROJO)
            return
        cfg = datos_json.perfil(crudo.get(nombre))
        crudo_certs, _ = datos_json.leer(CERTS, {})
        cert = datos_json.dic(datos_json.dic(crudo_certs).get(nombre))

        top = ctk.CTkToplevel(self)
        top.title(t("profiles.edit_title", nombre=nombre))
        top.geometry("620x640")
        top.resizable(False, False)
        top.transient(self)
        top.grab_set()

        cab = ctk.CTkFrame(top, fg_color="transparent")
        cab.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(cab, text=t("profiles.edit_title", nombre=nombre),
                     font=F(17, "bold"), text_color=ACENTO,
                     anchor="w").pack(anchor="w")
        if cert.get("certificado"):
            estado = t("profiles.certified") + " · " + t(
                "profiles.cert_date", fecha=cert.get("fecha", "?"),
                pico=cert.get("pico_gpu_c", "?"))
            color = VERDE
        else:
            estado, color = t("profiles.uncertified"), AMARILLO
        ctk.CTkLabel(cab, text=estado, font=F(12), text_color=color,
                     anchor="w").pack(anchor="w", pady=(2, 0))

        cuerpo = ctk.CTkScrollableFrame(top, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=18, pady=(4, 4))

        def linea(etiqueta, nota=None):
            """Fila del editor: etiqueta a la izquierda y sitio libre a la derecha.

            El control se crea DESPUES con la misma fila como padre; dentro de un
            CTkScrollableFrame todo va con pack (el grid es solo dentro de cada
            fila), y un widget no puede colocarse en un marco distinto del suyo.
            """
            f = ctk.CTkFrame(cuerpo, fg_color="transparent")
            f.pack(fill="x", pady=(0, 8))
            f.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(f, text=etiqueta, font=F(12), width=140,
                         anchor="w").grid(row=0, column=0, sticky="w", pady=(4, 0))
            if nota:
                self._mudo(f, nota, anchor="w", justify="left").grid(
                    row=1, column=1, sticky="ew", pady=(2, 0))
            return f

        def en(f, widget):
            widget.grid(row=0, column=1, sticky="ew")
            return widget

        f_nota = linea(t("profiles.f_note"))
        ent_nota = en(f_nota, self._entrada(f_nota, cfg.get("nota", "")))

        # --- plan: la lista real del equipo, no tres nombres inventados ---
        planes = planes_energia.listar(forzar=True)
        por_nombre = {p["nombre"]: p["guid"] for p in planes}
        valores = list(por_nombre)
        guardado = cfg.get("plan", "alto")
        guid_guardado, nombre_guardado = planes_energia.resolver(guardado)
        if nombre_guardado and nombre_guardado not in por_nombre:
            valores.append(nombre_guardado)
            por_nombre[nombre_guardado] = guid_guardado
        if not valores:
            # Windows no contesto: se deja el valor guardado como unico option,
            # para que editar OTRO campo no cambie el plan por accidente.
            valores = [nombre_guardado or str(guardado)]
        f_plan = linea(t("profiles.f_plan"))
        opt_plan = ctk.CTkOptionMenu(f_plan, values=valores, font=F(13))
        if nombre_guardado:
            opt_plan.set(nombre_guardado)
        en(f_plan, opt_plan)
        if not planes_energia.nombre_si_existe(guardado):
            self._mudo(f_plan, t("profiles.plan_missing", plan=guardado,
                                 usara=nombre_guardado or t("plans.unknown")),
                       anchor="w", justify="left").grid(row=1, column=1, sticky="ew",
                                                       pady=(2, 0))

        # --- techo de CPU: cursor + numero, sin que el usuario escriba codigos ---
        try:
            cpu_ini = int(cfg.get("cpu_max", 100))
        except (TypeError, ValueError):
            cpu_ini = 100
        cpu_ini = max(5, min(100, cpu_ini))
        f_cpu = linea(t("profiles.f_cpu"))
        caja_cpu = ctk.CTkFrame(f_cpu, fg_color="transparent")
        sld_cpu = ctk.CTkSlider(caja_cpu, from_=5, to=100, number_of_steps=95,
                                width=260, fg_color=("#cfd8e3", "#2c3542"),
                                progress_color=ACENTO, button_color=ACENTO,
                                button_hover_color=ACENTO_SEL_HOVER)
        sld_cpu.set(cpu_ini)
        sld_cpu.pack(side="left")
        lbl_cpu = ctk.CTkLabel(caja_cpu, text=t("profiles.cpu_pct", pct=cpu_ini),
                               font=F(12), width=190, anchor="w")
        lbl_cpu.pack(side="left", padx=(10, 0))
        sld_cpu.configure(command=lambda v: lbl_cpu.configure(
            text=t("profiles.cpu_pct", pct=int(round(float(v))))))
        en(f_cpu, caja_cpu)

        PRIORIDADES = {t("profiles.prio_alta"): "alta",
                       t("profiles.prio_normal"): "normal",
                       t("profiles.prio_baja"): "baja"}
        seg_prio = ctk.CTkSegmentedButton(linea(t("profiles.f_prio")),
                                          values=list(PRIORIDADES), font=F(12),
                                          width=260)
        seg_prio.set(next((k for k, v in PRIORIDADES.items()
                           if v == cfg.get("prioridad", "normal")),
                          t("profiles.prio_normal")))
        seg_prio.grid(row=0, column=1, sticky="ew")

        f_cerrar = linea(t("profiles.f_close"))
        ent_cerrar = en(f_cerrar, self._entrada(
            f_cerrar, ", ".join(cfg.get("cerrar", []) or []),
            placeholder=t("profiles.placeholder_close")))

        f_exe = linea(t("profiles.f_exe"))
        caja_exe = ctk.CTkFrame(f_exe, fg_color="transparent")
        caja_exe.grid_columnconfigure(0, weight=1)
        ent_exe = self._entrada(caja_exe, cfg.get("exe", ""),
                                placeholder=t("profiles.exe_none"))
        ent_exe.grid(row=0, column=0, sticky="ew")
        lbl_exe = self._mudo(caja_exe, "", anchor="w")
        lbl_exe.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        btn_exe = self._boton(caja_exe, t("profiles.browse"), ancho=110,
                              color="#556070", fuente=F(12))
        btn_exe.grid(row=0, column=2, padx=(4, 0))

        def _estado_exe(*_):
            ruta = ent_exe.get().strip().strip('"')
            if not ruta:
                lbl_exe.configure(text="", font=F(11))
            elif os.path.exists(ruta):
                lbl_exe.configure(text="✔ " + t("profiles.exe_ok"), text_color=VERDE)
            else:
                lbl_exe.configure(text="⚠ " + t("profiles.exe_missing"),
                                  text_color=AMARILLO)

        def _elegir_exe():
            ruta = filedialog.askopenfilename(
                parent=top, title=t("profiles.f_exe"),
                filetypes=[("Ejecutables", "*.exe"), ("todos", "*.*")])
            if ruta:
                ent_exe.delete(0, "end")
                ent_exe.insert(0, ruta)
                _estado_exe()

        btn_exe.configure(command=_elegir_exe)
        ent_exe.bind("<KeyRelease>", _estado_exe)
        _estado_exe()
        en(f_exe, caja_exe)

        ventiladores = ["Auto", "Max"]
        actual_fans = str(cfg.get("fans", "") or "")
        if actual_fans and actual_fans not in ventiladores:
            ventiladores.append(actual_fans)
        f_fans = linea(t("profiles.f_fans"))
        opt_fans = ctk.CTkOptionMenu(f_fans, values=ventiladores + [t("profiles.fans_none")],
                                     font=F(13), width=140)
        opt_fans.set(actual_fans if actual_fans else t("profiles.fans_none"))
        en(f_fans, opt_fans)

        extras = sorted(k for k in cfg if k not in self.CAMPOS_EDITOR)
        if extras:
            self._mudo(cuerpo, t("profiles.f_extra", lista=", ".join(extras)),
                       anchor="w", justify="left").pack(anchor="w", pady=(2, 4))
        self._mudo(cuerpo, t("profiles.rename_hint"),
                   anchor="w", justify="left").pack(anchor="w", pady=(0, 4))

        pie = ctk.CTkFrame(top, fg_color="transparent")
        pie.pack(fill="x", padx=18, pady=(4, 14))

        def _recoger():
            nuevo = dict(cfg)   # lo que el editor no muestra (xtu, plan sin cambiar) sigue
            nuevo["nota"] = ent_nota.get().strip()
            nuevo["cpu_max"] = int(round(float(sld_cpu.get())))
            nuevo["prioridad"] = PRIORIDADES.get(seg_prio.get(), "normal")
            nuevo["cerrar"] = [x.strip() for x in ent_cerrar.get().split(",") if x.strip()]
            nuevo["exe"] = ent_exe.get().strip().strip('"')
            nuevo["fans"] = ("" if opt_fans.get() == t("profiles.fans_none")
                             else opt_fans.get())
            gui = por_nombre.get(opt_plan.get())
            if gui:
                nuevo["plan"] = gui
            return nuevo

        def _guardar():
            nuevo = _recoger()
            # Releo el archivo: el asistente de consola pudo crear perfiles
            # mientras esta ventana estaba abierta; no quiero pisarle el trabajo.
            crudo_actual, error_actual = datos_json.leer(PERFILES, {})
            actuales = (dict(crudo_actual) if error_actual is None
                        and isinstance(crudo_actual, dict) else {nombre: cfg})
            viejo = datos_json.perfil(actuales.get(nombre) or cfg)
            cambio = (str(viejo.get("cpu_max", 100)) != str(nuevo["cpu_max"])
                      or planes_energia.resolver(viejo.get("plan", "alto"))[0]
                      != planes_energia.resolver(nuevo.get("plan", "alto"))[0])
            actuales[nombre] = datos_json.perfil(nuevo)
            err = self._escribir_perfiles(actuales)
            if err is not None:
                self._log(t("profiles.save_error", err=err), ROJO)
                return
            try:
                self._perfiles_mtime = os.path.getmtime(PERFILES)
            except OSError:
                self._perfiles_mtime = 0
            self._log(t("profiles.saved", nombre=nombre), VERDE)
            # La certificacion es de UNA configuracion concreta: si se mueve el
            # plan o el techo de CPU, hay que volver a pasar la prueba de estrés.
            if cambio and self._quitar_certificado(nombre):
                self._log(t("profiles.cert_dropped", nombre=nombre), AMARILLO)
            self._cargar_perfiles()
            top.destroy()

        self._boton(pie, t("profiles.save"), ancho=170,
                    command=_guardar).pack(side="left", padx=4)
        self._boton(pie, t("profiles.cancel"), ancho=120, color="#556070",
                    fuente=F(13), command=top.destroy).pack(side="left", padx=4)

    def _toggle_watchdog(self):
        if self.proc and self.proc.poll() is None and (self.proc_tag or "").startswith("watchdog"):
            self._parar_proc()
            return
        if self.proc and self.proc.poll() is None:
            self._log(t("log.tool_running"), AMARILLO)
            return
        extra = []
        ov = self.ent_override.get().strip()
        try:
            if float(ov) > 0:
                extra += ["--override", ov]
        except ValueError:
            pass
        self._lanzar("watchdog", extra, "watchdog")

    def _abrir_monitor(self):
        p = subprocess.Popen(self._cmd_tool("monitor", [], consola=True),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.monitor_procs = [m for m in self.monitor_procs if m.poll() is None]
        self.monitor_procs.append(p)
        self._log(t("log.monitor_opened"))

    def _python_consola(self):
        """python.exe (con consola) incluso si la GUI corre bajo pythonw.exe."""
        py = sys.executable
        if py.lower().endswith("pythonw.exe"):
            py = py[: -len("pythonw.exe")] + "python.exe"
        return py

    def _crear_perfil(self):
        """Abre el asistente crear_perfil en su propia ventana de consola.
        No necesita admin: powercfg escribe con usuario normal."""
        try:
            p = subprocess.Popen(self._cmd_tool("crear_perfil", [], consola=True),
                                 creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=BASE)
            self.monitor_procs = [m for m in self.monitor_procs if m.poll() is None]
            self.monitor_procs.append(p)
            self._log(t("log.wizard_opened"), VERDE)
        except Exception as e:
            self._log(t("log.wizard_error", err=e), ROJO)

    def _vigilar_perfiles(self):
        """Refresca la lista solo cuando cambia perfiles_juegos.json en disco
        (p. ej. al guardar desde el asistente de consola)."""
        try:
            m = os.path.getmtime(PERFILES)
        except OSError:
            m = 0
        if m != self._perfiles_mtime:
            self._perfiles_mtime = m
            if m:
                self._cargar_perfiles()
        self.after(1500, self._vigilar_perfiles)

    # ---------------- estilos del overlay ----------------
    def _refrescar_estilos(self):
        """Recarga la lista del selector y deja marcado el estilo activo."""
        nombres = overlay_estilo.listar()
        try:
            self.opt_estilo.configure(values=nombres)
            self.opt_estilo.set(overlay_estilo.nombre_activo())
        except Exception:
            pass
        self._refrescar_borrar_estilo()

    def _refrescar_borrar_estilo(self):
        """El botón Borrar solo tiene sentido sobre estilos creados por el usuario."""
        try:
            act = overlay_estilo.nombre_activo()
            personalizado = overlay_estilo.es_personalizado(act)
            self.btn_borrar_estilo.configure(
                state=("normal" if personalizado else "disabled"),
                border_color=(ROJO if personalizado else "#5a636d"))
        except Exception:
            pass

    def _cambiar_estilo(self, nombre):
        overlay_estilo.fijar_activo(nombre)
        self._refrescar_borrar_estilo()
        self._log(t("log.style_changed", nombre=nombre), ACENTO)

    def _borrar_estilo(self):
        act = overlay_estilo.nombre_activo()
        if not overlay_estilo.es_personalizado(act):
            self._log(t("tools.session.style_only_custom"), AMARILLO)
            return
        if not self._preguntar(t("tools.session.delete_style"),
                               t("tools.session.style_delete_ask", nombre=act)):
            return
        overlay_estilo.borrar_personalizado(act)
        self._refrescar_estilos()
        self._log(t("tools.session.style_deleted", nombre=act), VERDE)

    def _crear_estilo(self):
        """Editor de estilos: cada usuario se hace el suyo (colores, fuente, tamaño,
        opacidad) con vista previa en vivo. Al guardar queda activo y se usa en la
        próxima sesión."""
        from tkinter import colorchooser

        top = ctk.CTkToplevel(self)
        top.title(t("overlay.editor.title"))
        top.geometry("560x520")
        top.resizable(False, False)
        top.transient(self)
        top.grab_set()

        base = overlay_estilo.obtener(overlay_estilo.nombre_activo())
        vals = {
            "fondo": base["fondo"], "texto": base["texto"], "numeros": base["numeros"],
            "aviso": base["aviso"], "critico": base["critico"],
            "fuente": base["fuente"], "tamano": base["tamano"], "opacidad": base["opacidad"],
        }

        def _color(clave):
            ini = vals[clave]
            rgb, hx = colorchooser.askcolor(color=ini, parent=top,
                                            title=t("overlay.editor.title"))
            if hx:
                vals[clave] = hx
                botones[clave].configure(fg_color=hx, hover_color=hx)
                _preview()

        cuerpo = ctk.CTkFrame(top, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        fila_nombre = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_nombre.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(fila_nombre, text=t("overlay.editor.name"), font=F(13)).pack(side="left")
        ent_nombre = ctk.CTkEntry(fila_nombre, width=240, font=F(13))
        ent_nombre.pack(side="left", padx=(8, 0))

        botones = {}
        campos = [("fondo", "overlay.editor.bg"), ("texto", "overlay.editor.text"),
                  ("numeros", "overlay.editor.numbers"), ("aviso", "overlay.editor.warn"),
                  ("critico", "overlay.editor.critical")]
        grid = ctk.CTkFrame(cuerpo, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 8))
        for i, (clave, etik) in enumerate(campos):
            ctk.CTkLabel(grid, text=t(etik), font=F(12), width=90,
                         anchor="w").grid(row=i, column=0, sticky="w", pady=3)
            b = ctk.CTkButton(grid, text="", width=60, height=24,
                              fg_color=vals[clave], hover_color=vals[clave],
                              border_width=1, border_color=("gray60", "gray30"),
                              command=lambda k=clave: _color(k))
            b.grid(row=i, column=1, sticky="w", padx=(6, 0), pady=3)
            botones[clave] = b

        fila_fuente = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_fuente.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(fila_fuente, text=t("overlay.editor.font"), font=F(12),
                     width=90, anchor="w").pack(side="left")
        opt_fuente = ctk.CTkOptionMenu(fila_fuente, values=overlay_estilo.FUENTES,
                                       width=180, font=F(12))
        opt_fuente.set(vals["fuente"])
        opt_fuente.pack(side="left", padx=(6, 0))

        fila_tam = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_tam.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(fila_tam, text=t("overlay.editor.size"), font=F(12),
                     width=90, anchor="w").pack(side="left")
        ent_tam = ctk.CTkEntry(fila_tam, width=60, font=F(12))
        ent_tam.insert(0, str(vals["tamano"]))
        ent_tam.pack(side="left", padx=(6, 0))

        fila_op = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_op.pack(fill="x", pady=(4, 4))
        ctk.CTkLabel(fila_op, text=t("overlay.editor.opacity"), font=F(12),
                     width=90, anchor="w").pack(side="left")
        sld = ctk.CTkSlider(fila_op, from_=0.3, to=1.0, number_of_steps=14, width=200)
        sld.set(vals["opacidad"])
        sld.pack(side="left", padx=(6, 0))

        # --- vista previa en vivo ---
        ctk.CTkLabel(cuerpo, text=t("overlay.editor.preview"), font=F(12, "bold"),
                     text_color=ACENTO, anchor="w").pack(anchor="w", pady=(8, 2))
        prev = tk.Frame(cuerpo, padx=10, pady=8)
        prev.pack(fill="x")
        prev_cap = tk.Label(prev, text="GPU", width=7, anchor="w",
                            font=(vals["fuente"], vals["tamano"], "bold"))
        prev_cap.grid(row=0, column=0, sticky="w", padx=(0, 6))
        prev_val = tk.Label(prev, text="72°C   45%   4.2GB VRAM", anchor="w",
                            font=(vals["fuente"], vals["tamano"] + 1, "bold"))
        prev_val.grid(row=0, column=1, sticky="w")

        def _preview():
            try:
                tam = int(ent_tam.get().strip() or vals["tamano"])
            except ValueError:
                tam = vals["tamano"]
            vals["fuente"] = opt_fuente.get()
            vals["tamano"] = max(8, min(28, tam))
            vals["opacidad"] = round(float(sld.get()), 2)
            prev.configure(bg=vals["fondo"])
            prev_cap.configure(bg=vals["fondo"], fg=vals["texto"],
                               font=(vals["fuente"], vals["tamano"], "bold"))
            prev_val.configure(bg=vals["fondo"], fg=vals["numeros"],
                               font=(vals["fuente"], vals["tamano"] + 1, "bold"))

        opt_fuente.configure(command=lambda *_: _preview())
        sld.configure(command=lambda *_: _preview())
        ent_tam.bind("<KeyRelease>", lambda *_: _preview())
        _preview()

        pie = ctk.CTkFrame(top, fg_color="transparent")
        pie.pack(fill="x", padx=16, pady=(4, 16))

        def _guardar():
            _preview()
            nombre = ent_nombre.get().strip()
            if not nombre:
                self._log(t("overlay.editor.bad_name"), ROJO)
                return
            estilo = {k: vals[k] for k in
                      ("fondo", "texto", "numeros", "aviso", "critico",
                       "fuente", "tamano", "opacidad")}
            overlay_estilo.guardar_personalizado(nombre, estilo)
            self._refrescar_estilos()
            self._log(t("tools.session.style_saved", nombre=nombre), VERDE)
            top.destroy()

        self._boton(pie, t("overlay.editor.save"), ancho=160,
                    command=_guardar).pack(side="left", padx=4)
        self._boton(pie, t("overlay.editor.cancel"), ancho=110, color="#555f6b",
                    fuente=F(13), command=top.destroy).pack(side="left", padx=4)

    # ---------------- apariencia de la GUI ----------------
    def _construir_apariencia(self, p):
        """Personalización superficial: color de acento, tipografía y tamaño de
        letra. Se guarda en config.json y se aplica al vuelo reconstruyendo la
        interfaz (los colores/fuente/escala son globales leídos al construir)."""
        self._apariencia_lista = False
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(0, weight=1)

        cuerpo = ctk.CTkScrollableFrame(p, fg_color="transparent")
        cuerpo.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        cuerpo.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(cuerpo, text=t("appearance.title"), font=F(18, "bold"),
                     text_color=ACENTO, anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(4, 2))
        self._mudo(cuerpo, t("appearance.subtitle"), anchor="w",
                   justify="left").grid(row=1, column=0, sticky="ew", pady=(0, 8))

        # --- color de acento ---
        caja_acento = self._marco(cuerpo)
        caja_acento.grid(row=2, column=0, sticky="ew", pady=6)
        ctk.CTkLabel(caja_acento, text=t("appearance.accent"), font=F(14, "bold"),
                     anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        fila_sw = ctk.CTkFrame(caja_acento, fg_color="transparent")
        fila_sw.pack(anchor="w", padx=12, pady=(2, 12))
        borde_sel = "#0d1117" if self.tema == "light" else "#f0f6fc"
        for _nombre, hexc in ACENTOS_PRESET:
            sel = (hexc.lower() == ACENTO.lower())
            ctk.CTkButton(fila_sw, text="", width=34, height=34, corner_radius=17,
                          fg_color=hexc, hover_color=hexc,
                          border_width=(3 if sel else 1), border_color=borde_sel,
                          command=lambda h=hexc: self._cambiar_acento(h)).pack(
                side="left", padx=4)
        self._boton(fila_sw, t("appearance.custom"), ancho=130, fuente=F(12, "bold"),
                    command=self._elegir_acento).pack(side="left", padx=(10, 4))

        # --- tipografía ---
        caja_fuente = self._marco(cuerpo)
        caja_fuente.grid(row=3, column=0, sticky="ew", pady=6)
        ctk.CTkLabel(caja_fuente, text=t("appearance.font"), font=F(14, "bold"),
                     anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        fila_f = ctk.CTkFrame(caja_fuente, fg_color="transparent")
        fila_f.pack(anchor="w", padx=12, pady=(2, 12))
        self.opt_fuente_ui = ctk.CTkOptionMenu(
            fila_f, values=UI_FUENTES, width=240, font=F(13),
            command=self._cambiar_fuente_ui, corner_radius=10,
            fg_color=("#dfe5ec", "#232d3a"),
            button_color=(ACENTO_SEL, ACENTO_SEL),
            button_hover_color=(ACENTO_SEL_HOVER, ACENTO_SEL_HOVER),
            text_color=("#0f2027", "#eaf2f6"))
        self.opt_fuente_ui.set(UI_FONT if UI_FONT in UI_FUENTES else UI_FUENTES[0])
        self.opt_fuente_ui.pack(side="left")

        # --- tamaño de letra ---
        caja_esc = self._marco(cuerpo)
        caja_esc.grid(row=4, column=0, sticky="ew", pady=6)
        ctk.CTkLabel(caja_esc, text=t("appearance.size"), font=F(14, "bold"),
                     anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        fila_e = ctk.CTkFrame(caja_esc, fg_color="transparent")
        fila_e.pack(anchor="w", padx=12, pady=(2, 12))
        self.seg_escala = ctk.CTkSegmentedButton(
            fila_e, values=[lbl for lbl, _ in ESCALAS], font=F(13, "bold"),
            command=self._cambiar_escala,
            selected_color=(ACENTO_SEL, ACENTO_SEL),
            selected_hover_color=(ACENTO_SEL_HOVER, ACENTO_SEL_HOVER),
            unselected_color=("#dfe5ec", "#232d3a"),
            fg_color=("#dfe5ec", "#232d3a"))
        self.seg_escala.set(self._etiqueta_escala())
        self.seg_escala.pack(side="left")

        # --- vista previa ---
        caja_prev = self._marco(cuerpo)
        caja_prev.grid(row=5, column=0, sticky="ew", pady=6)
        ctk.CTkLabel(caja_prev, text=t("appearance.preview"), font=F(14, "bold"),
                     anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(caja_prev, text="AMPERA  ·  GPU 72°C  ·  45%",
                     font=F(16, "bold"), text_color=ACENTO,
                     anchor="w").pack(anchor="w", padx=12, pady=(0, 12))

        # --- restablecer ---
        fila_reset = ctk.CTkFrame(cuerpo, fg_color="transparent")
        fila_reset.grid(row=6, column=0, sticky="w", pady=(6, 12))
        self._boton(fila_reset, t("appearance.reset"), ancho=220, color="#555f6b",
                    command=self._reset_apariencia).pack(side="left")
        self._apariencia_lista = True

    def _etiqueta_escala(self):
        return min(ESCALAS, key=lambda kv: abs(kv[1] - ESC))[0]

    def _guardar_y_reconstruir(self):
        _guardar_apariencia({"acento": ACENTO, "fuente": UI_FONT, "escala": ESC})
        self._reconstruir()

    def _cambiar_acento(self, hexcolor):
        if not self._apariencia_lista or not _color_valido(hexcolor):
            return
        _aplicar_acento(hexcolor)
        self._guardar_y_reconstruir()
        self._log(t("appearance.log_accent"), ACENTO)

    def _elegir_acento(self):
        if not self._apariencia_lista:
            return
        from tkinter import colorchooser
        _rgb, hx = colorchooser.askcolor(color=ACENTO, parent=self,
                                         title=t("appearance.custom"))
        if hx:
            self._cambiar_acento(hx)

    def _cambiar_fuente_ui(self, nombre):
        global UI_FONT
        if not self._apariencia_lista or nombre not in UI_FUENTES:
            return
        UI_FONT = nombre
        self._guardar_y_reconstruir()
        self._log(t("appearance.log_font", nombre=nombre), ACENTO)

    def _cambiar_escala(self, etiqueta):
        global ESC
        if not self._apariencia_lista:
            return
        val = dict(ESCALAS).get(etiqueta)
        if val is None:
            return
        ESC = float(val)
        self._guardar_y_reconstruir()
        self._log(t("appearance.log_size", val=etiqueta), ACENTO)

    def _reset_apariencia(self):
        global UI_FONT, ESC
        if not self._apariencia_lista:
            return
        _aplicar_acento(ACENTOS_PRESET[0][1])
        UI_FONT = "Bahnschrift"
        ESC = 1.0
        self._guardar_y_reconstruir()
        self._log(t("appearance.log_reset"), ACENTO)

    def _toggle_sesion(self):
        if self.proc and self.proc.poll() is None and (self.proc_tag or "") == "sesion":
            self._parar_proc()
            return
        if self.proc and self.proc.poll() is None:
            self._log(t("log.tool_running"), AMARILLO)
            return
        if not self.estado.get("lhm"):
            self._log(t("log.session_no_lhm"), AMARILLO)
        self._lanzar("sesion", ["--estilo", overlay_estilo.nombre_activo()], "sesion")

    def _toggle_aviso(self):
        if self.proc and self.proc.poll() is None and (self.proc_tag or "") == "aviso":
            self._parar_proc()
            return
        if self.proc and self.proc.poll() is None:
            self._log(t("log.tool_running"), AMARILLO)
            return
        try:
            cpu_u = float(self.ent_aviso_cpu.get().strip() or "90")
            gpu_u = float(self.ent_aviso_gpu.get().strip() or "90")
        except ValueError:
            self._log(t("log.aviso.bad_umbral"), ROJO)
            return
        if not (30 <= cpu_u <= 110) or not (30 <= gpu_u <= 110):
            self._log(t("log.aviso.bad_umbral"), ROJO)
            return
        extra = ["--cpu-umbral", f"{cpu_u:g}", "--gpu-umbral", f"{gpu_u:g}"]
        if self.sw_proteger.get() == "on":
            extra.append("--proteger")
        if not self.estado.get("lhm"):
            self._log(t("log.session_no_lhm"), AMARILLO)
        self._lanzar("aviso", extra, "aviso")

    def _popup_aviso(self, titulo, texto):
        """Ventana emergente + sonido (la otra mitad de 'Ambos': toast + pop-up)."""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            try:
                self.bell()
            except Exception:
                pass
        top = ctk.CTkToplevel(self)
        top.title(titulo)
        top.geometry("460x170")
        top.attributes("-topmost", True)
        top.resizable(False, False)
        top.configure(fg_color=("#fdecea", "#2a1414"))
        ctk.CTkLabel(top, text="⚠ " + titulo, font=F(16, "bold"),
                     text_color=ROJO, wraplength=420, justify="left").pack(
            anchor="w", padx=16, pady=(16, 6))
        ctk.CTkLabel(top, text=texto, font=F(13), wraplength=420,
                     justify="left").pack(anchor="w", padx=16, pady=(0, 10))
        self._boton(top, "OK", ancho=100, command=top.destroy).pack(pady=(0, 14))
        try:
            top.grab_set()
        except Exception:
            pass

    def _reparar_pendientes(self, sesion_justo_murio=False):
        """Repara lo que quedó a medias cuando una herramienta murió sin ejecutar su
        `finally`. En este equipo CTRL_BREAK/terminate NO ejecutan el finally del
        hijo, así que la limpieza no puede depender de él: cada herramienta deja un
        archivo de seguridad y sabe restaurar desde él.

          * aviso_termico / watchdog / certificar -> devuelven el plan y el techo de
            CPU al estado que tenía el usuario antes de la prueba;
          * sesion -> reconstruye el reporte HTML que no se escribió, a partir del CSV.

        `sesion_justo_murio=True` lo llama la GUI cuando el proceso de sesión YA
        terminó: ahí no hay sesión viva que molestar, y el reporte se recupera al
        momento en vez de esperar la ventana de abandono. Idempotente."""
        techo_restore = False
        for modulo in ("aviso_termico", "watchdog", "certificar"):
            try:
                if __import__(modulo).restaurar_si_pendiente():
                    techo_restore = True
            except Exception:
                pass
        try:
            reportes = __import__("sesion").restaurar_si_pendiente(
                incluir_recientes=sesion_justo_murio)
        except Exception:
            reportes = []
        if techo_restore:
            self._log(t("log.repair.ceiling"), AMARILLO)
            planes_energia.invalisar()
            self._refrescar_planes()
        if reportes:
            self._log(t("log.repair.session", n=len(reportes)), AMARILLO)

    # ---------------- limpieza de RAM (Mem Reduct) ----------------
    def _limpiar_ram(self, profunda=None, silencioso=False):
        """Lanza 'memreduct.exe -clean' (o -clean:full) y mide la RAM liberada.
        Corre en un hilo para no congelar la GUI; el resultado va a la consola."""
        if not MEMREDUCT:
            self._log(t("log.mem.notfound"), ROJO)
            return
        if profunda is None:
            try:
                profunda = self.chk_profunda.get() == "on"
            except Exception:
                profunda = False
        modo = "-clean:full" if profunda else "-clean"
        if not silencioso:
            self._log(t("log.mem.cleaning", modo=modo), ACENTO)

        def trabajo():
            try:
                antes = psutil.virtual_memory().available
                subprocess.run([MEMREDUCT, modo], creationflags=_SIN_VENTANA, timeout=25)
                time.sleep(4)  # la limpieza la hace la instancia de bandeja, es asíncrona
                desp = psutil.virtual_memory().available
                delta = (desp - antes) / 2 ** 30
                self.cola.put(("logcolor", (
                    t("log.mem.result", antes=f"{antes / 2**30:.2f}",
                      desp=f"{desp / 2**30:.2f}", delta=f"{delta:+.2f}"),
                    VERDE if delta >= -0.05 else AMARILLO)))
            except Exception as e:
                self.cola.put(("logcolor", (t("log.mem.error", err=e), ROJO)))

        threading.Thread(target=trabajo, daemon=True).start()

    def _toggle_auto_limpieza(self):
        if self.sw_auto.get() != "on":
            self._auto_on = False
            self._log(t("log.auto.off"), AMARILLO)
            return
        if not MEMREDUCT:
            self._log(t("log.auto.notfound"), ROJO)
            self.sw_auto.deselect()
            return
        try:
            mins = int(float(self.ent_intervalo.get().strip() or "15"))
            mins = max(1, mins)
        except ValueError:
            self._log(t("log.auto.bad_interval"), ROJO)
            self.sw_auto.deselect()
            return
        try:
            umbral = float(self.ent_umbral.get().strip() or "0")
            umbral = max(0.0, umbral)
        except ValueError:
            umbral = 0.0
        self._auto_intervalo_ms = mins * 60 * 1000
        self._auto_umbral_gb = umbral
        self._auto_on = True
        cond = (t("log.auto.cond_threshold", umbral=f"{umbral:g}") if umbral > 0
                else t("log.auto.cond_always"))
        self._log(t("log.auto.on", mins=mins, cond=cond), VERDE)
        self.after(self._auto_intervalo_ms, self._auto_tick)

    def _auto_tick(self):
        if not self._auto_on:
            return
        libre = self.estado.get("ram_libre")
        debe = (self._auto_umbral_gb <= 0 or
                (isinstance(libre, (int, float)) and libre < self._auto_umbral_gb))
        if debe:
            self._limpiar_ram(silencioso=False)
        self.after(self._auto_intervalo_ms, self._auto_tick)

    # ---------------- subprocesos ----------------
    def _cmd_tool(self, nombre, extra=None, consola=False):
        """Construye el comando para lanzar una herramienta del backend.
        Congelado: NitroTunerCLI.exe --tool <nombre>. Fuente: main.py --tool <nombre>."""
        extra = extra or []
        if getattr(sys, "frozen", False):
            exe = os.path.join(os.path.dirname(sys.executable), "NitroTunerCLI.exe")
            return [exe, "--tool", nombre, *extra]
        py = self._python_consola() if consola else sys.executable
        return [py, os.path.join(BASE, "main.py"), "--tool", nombre, *extra]

    def _lanzar(self, tool, extra, tag):
        if self.proc and self.proc.poll() is None:
            self._log(t("log.tool_running_stop"), AMARILLO)
            return
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        self.proc = subprocess.Popen(self._cmd_tool(tool, extra),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=flags, cwd=BASE)
        self.proc_tag = tag
        self._actualizar_boton_proc()
        self._log(t("log.launched", tag=tag), VERDE)
        threading.Thread(target=self._leer_salida, args=(self.proc,), daemon=True).start()

    def _leer_salida(self, proc):
        for linea in proc.stdout:
            self.cola.put(("linea", linea.rstrip("\n")))
        proc.wait()
        self.cola.put(("fin", proc.returncode))

    def _detener_hijo(self, proc, gracia=2.0):
        """Detiene un hijo SIEMPRE, tenga o no consola esta GUI.

        El exe de ventana (NitroTuner.exe) NO tiene consola, así que
        os.kill(CTRL_BREAK) lanza WinError 6 y antes la herramienta no se paraba.
        Aquí: se intenta CTRL_BREAK (da oportunidad de limpiar en equipos donde
        funcione) y, si el hijo sigue vivo tras la gracia, se termina/killea.
        La limpieza que el finally no llegue a hacer la compensa _reparar_pendientes."""
        if proc is None or proc.poll() is not None:
            return
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
        except Exception:
            pass  # sin consola no se puede emitir CTRL_BREAK: se cae al terminate
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

    def _parar_proc(self):
        if self.proc and self.proc.poll() is None:
            self._log(t("log.stop_sent"), AMARILLO)
            # en un hilo para no congelar la interfaz durante la espera de gracia
            threading.Thread(target=self._detener_hijo, args=(self.proc,),
                             daemon=True).start()

    def _pintar_boton(self, btn, activo, texto_on, texto_off):
        """Conserva el botón hueco y cambia solo contorno/texto según el estado."""
        color = ROJO if activo else ACENTO
        btn.configure(
            text=(texto_on if activo else texto_off),
            border_color=color,
            text_color=(color if activo else TEXTO_BOTON),
            hover_color=_tinte_hover(color))

    def _actualizar_boton_proc(self):
        corriendo = self.proc is not None and self.proc.poll() is None
        tag = self.proc_tag or ""
        es_watch = tag.startswith("watchdog")
        self._pintar_boton(self.btn_watch, corriendo and es_watch,
                           t("tools.watchdog.stop"), t("tools.watchdog.start"))
        es_sesion = tag == "sesion"
        self._pintar_boton(self.btn_sesion, corriendo and es_sesion,
                           t("tools.session.stop"), t("tools.session.start"))
        es_aviso = tag == "aviso"
        self._pintar_boton(self.btn_aviso, corriendo and es_aviso,
                           t("tools.aviso.stop"), t("tools.aviso.start"))

    def _drenar_cola(self):
        try:
            while True:
                tag, val = self.cola.get_nowait()
                if tag == "linea":
                    if isinstance(val, str) and val.startswith("@@NOTIF@@"):
                        partes = val.split("@@NOTIF@@")
                        tit = partes[1] if len(partes) > 1 else t("tools.aviso.title")
                        txt = partes[2] if len(partes) > 2 else ""
                        self._popup_aviso(tit, txt)
                        self._log(t("log.aviso.fired", texto=txt), ROJO)
                    else:
                        self._log(val)
                elif tag == "logcolor":
                    texto, color = val
                    self._log(texto, color)
                elif tag == "fin":
                    self._log(t("log.proc_done", code=val),
                              VERDE if val == 0 else AMARILLO)
                    tag_fin = self.proc_tag or ""
                    self.proc = None
                    self.proc_tag = None
                    self._actualizar_boton_proc()
                    if tag_fin.startswith("certificar"):
                        self._cargar_perfiles()
                    # el hijo ya murió: si dejó limpieza pendiente (techo de CPU,
                    # reporte de sesión) se repara ahora mismo
                    self._reparar_pendientes(sesion_justo_murio=(tag_fin == "sesion"))
        except queue.Empty:
            pass
        self.after(200, self._drenar_cola)

    # ---------------- utilidades GUI ----------------
    def _log(self, texto, color=None):
        # _construir_perfiles llama a _cargar_perfiles ANTES de crear la consola;
        # si el JSON está dañado, _log se ejecutaba sin self.log y tumbaba el
        # arranque. Ahora se guarda el mensaje y se vuelca cuando exista.
        log = getattr(self, "log", None)
        if log is None:
            pendientes = getattr(self, "_log_pendiente", None)
            if pendientes is None:
                pendientes = self._log_pendiente = []
            pendientes.append(texto)
            return
        try:
            log.configure(state="normal")
            log.insert("end", texto + "\n")
            log.see("end")
            log.configure(state="disabled")
        except Exception:
            pass

    def _preguntar(self, titulo, texto):
        from tkinter import messagebox
        return messagebox.askyesno(titulo, texto, parent=self)

    def _al_cerrar(self):
        self.parar_sensores.set()
        self._auto_on = False  # detiene el bucle de auto-limpieza de RAM
        # 1) parar la herramienta en marcha. _detener_hijo garantiza que muera aunque
        #    esta GUI no tenga consola (CTRL_BREAK daría WinError 6 y no pararía nada).
        self._detener_hijo(self.proc, gracia=2.0)
        # la herramienta pudo frenar el CPU o dejar su reporte sin generar; si el
        # hijo murió sin limpiar su finally (aquí CTRL_BREAK/terminate no lo ejecutan),
        # la GUI lo restaura desde los archivos de seguridad.
        self._reparar_pendientes(sesion_justo_murio=(self.proc_tag == "sesion"))
        # 2) cerrar los monitores abiertos en ventana aparte
        for m in self.monitor_procs:
            if m.poll() is None:
                try:
                    m.terminate()
                except Exception:
                    pass
        self.monitor_procs.clear()
        # 3) El servidor de sensores (LhmServer / Libre Hardware Monitor) NO se
        #    cierra aquí: lo arranca la tarea "Tuner Sensor" al iniciar sesión y
        #    es compartido. Matarlo dejaba la app sin temperatura de CPU hasta el
        #    próximo inicio de sesión.
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    App().mainloop()
