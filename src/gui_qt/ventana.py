"""Interfaz de AMPERA 1.1.0 con PySide6.

Reutiliza el backend real: lee `perfiles_juegos.json` y
`perfiles_certificados.json`, guarda acento/fuente/escala en `config.json` y
lanza las mismas herramientas que la interfaz clásica (aplicar, certificar,
watchdog, sensor, planes de energía y overlay).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog,
    QComboBox, QDateTimeEdit, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

import overlay_estilo
import planes_energia
from datos import dic, escribir, leer, perfiles as sanear_perfiles, perfiles_defecto
from rutas import data_path, resource_path

from . import servicios, textos
from .dialogos import DialogoCertificar, DialogoPerfil
from .iconos import icono
from .tema import (
    ACENTO_DEFECTO, ACENTOS, FUENTE_DEFECTO, FUENTES, TEMAS, color_valido,
    hoja_estilo,
)

PERFILES = data_path("perfiles_juegos.json")
CERTS = data_path("perfiles_certificados.json")
CONFIG = data_path("config.json")

VERSION = "1.1.0"

FLECHAS_POS = {
    "arriba-izquierda": "↖", "arriba": "↑", "arriba-derecha": "↗",
    "izquierda": "←", "centro": "●", "derecha": "→",
    "abajo-izquierda": "↙", "abajo": "↓", "abajo-derecha": "↘",
}

CLAVES_PAGINA = ("resumen", "estado", "perfiles", "herramientas",
                 "recomendaciones", "centro", "apariencia")


def paginas() -> list[tuple[str, str, str]]:
    return [(c, textos.t(f"nav_{c}"), textos.t(f"sub_{c}", v=VERSION))
            for c in CLAVES_PAGINA]


def estados() -> list[tuple[str, str]]:
    return [("todos", textos.t("todos")),
            ("cert", textos.t("certificados")),
            ("sincert", textos.t("sin_certificar"))]


def _leer_apariencia() -> dict:
    cfg, _ = leer(CONFIG, {})
    return dic(dic(cfg).get("apariencia"))


def _guardar_apariencia(acento: str | None = None, fuente: str | None = None,
                        escala: float | None = None) -> None:
    """Actualiza solo las claves indicadas; conserva idioma y el resto."""
    cfg, _ = leer(CONFIG, {})
    cfg = dic(cfg)
    ap = dic(cfg.get("apariencia"))
    if acento is not None:
        ap["acento"] = acento
    if fuente is not None:
        ap["fuente"] = fuente
    if escala is not None:
        ap["escala"] = escala
    cfg["apariencia"] = ap
    try:
        escribir(CONFIG, cfg)
    except Exception:
        pass


def _leer_opcion(clave: str, defecto=None):
    cfg, _ = leer(CONFIG, {})
    return dic(cfg).get(clave, defecto)


def _guardar_opcion(clave: str, valor) -> None:
    cfg, _ = leer(CONFIG, {})
    cfg = dic(cfg)
    cfg[clave] = valor
    try:
        escribir(CONFIG, cfg)
    except Exception:
        pass


def cargar_perfiles() -> tuple[dict, dict, tuple[str, str] | None]:
    """Devuelve (perfiles saneados, certificados, aviso). Solo lectura.

    El aviso va como (motivo, detalle) para poder traducirlo al mostrarlo.
    """
    crudo, error = leer(PERFILES, {})
    aviso = None
    if error is not None or not isinstance(crudo, dict):
        aviso = ("error", str(error or ""))
        crudo = {}
    perfiles = sanear_perfiles(crudo)
    if not perfiles:
        perfiles = perfiles_defecto()
        aviso = aviso or ("vacio", "")
    certs, _ = leer(CERTS, {})
    return perfiles, dic(certs), aviso


class Ventana(QMainWindow):
    def __init__(self, tema: str = "oscuro", pagina: str = "resumen",
                 acento: str | None = None):
        super().__init__()
        ap = _leer_apariencia()
        self.tema = tema if tema in TEMAS else "oscuro"

        candidato = acento if acento and color_valido(acento) else ap.get("acento")
        self.acento = candidato if color_valido(candidato) else ACENTO_DEFECTO

        disponibles = QFontDatabase.families()
        guardada = ap.get("fuente")
        self.familia = guardada if guardada in disponibles else FUENTE_DEFECTO
        if self.familia not in disponibles:
            self.familia = ("Segoe UI" if "Segoe UI" in disponibles
                            else (disponibles[0] if disponibles else "Arial"))

        escala = ap.get("escala")
        if isinstance(escala, (int, float)):
            self.tamano = max(9, min(13, round(10 * escala)))
        else:
            self.tamano = 10

        self.perfiles, self.certs, self.aviso = cargar_perfiles()
        self.idioma = textos.idioma_actual()
        self._paginas = paginas()
        self.pares_tema: list[tuple[QPushButton, QPushButton]] = []
        self.muestras: list[tuple[QPushButton, QFrame, str]] = []
        self.nav_iconos: list[tuple[QPushButton, str]] = []
        self.tarjetas_perfil: list[tuple[QFrame, str, bool]] = []
        self.expansores: list[tuple[QPushButton, QWidget, QFrame]] = []
        self.scroll_perfiles: QScrollArea | None = None
        self._filtro = "todos"
        self._msg_base = f"{textos.t('listo')} · AMPERA {VERSION}"
        self._umbral_watchdog = 89
        self._watchdog_activo = False
        self._tarea_watchdog: servicios.Tarea | None = None
        self._umbral_termico = int(_leer_opcion("termico_umbral", 90) or 90)
        self._termico_activo = False
        self._termico_parando = False
        self._tarea_termico: servicios.Tarea | None = None
        self._dlg_termico: QMessageBox | None = None
        self._ram_auto = bool(_leer_opcion("ram_auto", False))
        self._ram_intervalo = int(_leer_opcion("ram_intervalo", 15) or 15)
        try:
            self._ram_umbral = float(_leer_opcion("ram_umbral", 0) or 0)
        except (TypeError, ValueError):
            self._ram_umbral = 0.0
        self._hilo_ram: servicios.Hilo | None = None
        self._procesos: list[dict] = []
        self._equipo: dict | None = None
        self._reco: dict | None = None
        self._hilo_equipo: servicios.Hilo | None = None
        self._diag_activo = False
        self._programadas = self._leer_programadas()
        self.timer_ram = QTimer(self)
        self.timer_ram.timeout.connect(self._tick_ram)
        self.timer_prog = QTimer(self)
        self.timer_prog.setInterval(30_000)
        self.timer_prog.timeout.connect(self._revisar_programadas)
        self._hilos: set[servicios.Hilo] = set()
        self._sensores: dict = {}
        self._planes: list[dict] = []
        self._plan_activo = ""
        self._cerrando = False
        self._vivo_pausado = False
        self._hilo_vivo: servicios.Hilo | None = None
        self._ultimo_vivo: dict = {}
        try:
            segundos = int(_leer_opcion("estado_sondeo", 2) or 2)
        except (TypeError, ValueError):
            segundos = 2
        self._sondeo = segundos if segundos in (2, 3, 5, 10, 20, 30) else 2
        self.timer_vivo = QTimer(self)
        self.timer_vivo.setInterval(self._sondeo * 1000)
        self.timer_vivo.timeout.connect(self._muestrear_vivo)
        self._marcas_archivos = self._leer_marcas()
        self._reparado = servicios.reparar_pendientes()

        self.setObjectName("Ventana")
        self.setWindowTitle(f"AMPERA · {VERSION}")
        self.setMinimumSize(1024, 580)
        pantalla = self.screen().availableGeometry() if self.screen() else None
        ancho, alto = 1240, 780
        if pantalla is not None:
            ancho = min(1280, max(1024, pantalla.width() - 40))
            alto = min(820, max(600, pantalla.height() - 40))
        self.resize(ancho, alto)
        icono = Path(resource_path("AMPERA.ico"))
        if icono.exists():
            self.setWindowIcon(QIcon(str(icono)))

        self._construir(pagina)
        self._aplicar_estilo()
        self._pintar_muestras()
        self._sincronizar_tema()
        self._refrescar_muestra()
        self._actualizar_resumen()
        self._restaurar_ram_auto()
        self._actualizar_ram_libre()

        self.timer_archivos = QTimer(self)
        self.timer_archivos.setInterval(1500)
        self.timer_archivos.timeout.connect(self._vigilar_archivos)
        self.timer_archivos.start()
        self._timer_estado = QTimer(self)
        self._timer_estado.setSingleShot(True)
        self._timer_estado.timeout.connect(
            lambda: self.lbl_estado.setText(self._msg_base))
        self.timer_prog.start()
        self._revisar_programadas()
        QTimer.singleShot(350, lambda: self._sondear(False))

        if self._reparado:
            self._estado(textos.t("m_techo_restaurado"))

    # ---------- estructura ----------

    def _construir(self, pagina: str) -> None:
        self._paginas = paginas()
        central = QWidget()
        central.setObjectName("Central")
        self.setCentralWidget(central)
        raiz = QHBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        self.panel_lateral = self._barra_lateral()
        raiz.addWidget(self.panel_lateral)

        derecha = QWidget()
        col = QVBoxLayout(derecha)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._barra_superior())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._pagina_resumen())
        self.stack.addWidget(self._pagina_estado())
        self.stack.addWidget(self._pagina_perfiles())
        self.stack.addWidget(self._pagina_herramientas())
        self.stack.addWidget(self._pagina_recomendaciones())
        self.stack.addWidget(self._pagina_centro())
        self.stack.addWidget(self._pagina_apariencia())
        col.addWidget(self.stack, 1)
        self.lbl_estado = QLabel(self._msg_base)
        self.lbl_estado.setObjectName("EstadoPie")
        col.addWidget(self.lbl_estado)
        raiz.addWidget(derecha, 1)

        self.stack.currentChanged.connect(self._cambio_pagina)
        claves = [p[0] for p in self._paginas]
        indice = claves.index(pagina) if pagina in claves else 0
        self.stack.setCurrentIndex(indice)
        self._cambio_pagina(indice)

    def _barra_lateral(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("PanelLateral")
        panel.setMinimumWidth(244)
        col = QVBoxLayout(panel)
        col.setContentsMargins(18, 16, 18, 14)
        col.setSpacing(3)

        marca = QHBoxLayout()
        marca.setSpacing(10)
        logo = QLabel()
        ruta = Path(resource_path("AMPERA.ico"))
        if ruta.exists():
            logo.setPixmap(QIcon(str(ruta)).pixmap(28, 28))
        nombre = QLabel("AMPERA")
        nombre.setObjectName("Marca")
        chip = QLabel(VERSION)
        chip.setObjectName("Insignia")
        marca.addWidget(logo)
        marca.addWidget(nombre)
        marca.addStretch()
        marca.addWidget(chip)
        col.addLayout(marca)
        col.addSpacing(20)

        self.botones_nav: list[QPushButton] = []
        grupo = QButtonGroup(self)
        grupo.setExclusive(True)
        claves = [p[0] for p in self._paginas]
        for i, ((_, titulo, _), clave) in enumerate(zip(self._paginas, claves)):
            b = QPushButton(titulo)
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setMinimumHeight(41)
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            grupo.addButton(b, i)
            col.addWidget(b)
            self.botones_nav.append(b)
            self.nav_iconos.append((b, clave))

        col.addStretch()
        col.addWidget(_separador())
        col.addSpacing(14)

        et = QLabel(textos.t("seccion_apariencia"))
        et.setObjectName("Etiqueta")
        col.addWidget(et)
        col.addSpacing(6)

        rejilla = QGridLayout()
        rejilla.setContentsMargins(0, 0, 0, 0)
        rejilla.setHorizontalSpacing(6)
        rejilla.setVerticalSpacing(2)
        for i, (nombre_acento, color) in enumerate(ACENTOS):
            caja, _ = self._crear_muestra(color, nombre_acento)
            rejilla.addWidget(caja, i // 3, i % 3)
        col.addLayout(rejilla)
        col.addSpacing(12)

        et2 = QLabel(textos.t("seccion_tema"))
        et2.setObjectName("Etiqueta")
        col.addWidget(et2)
        col.addSpacing(4)
        self._crear_par_tema(col)
        col.addSpacing(18)

        pie = QLabel(f"AMPERA {VERSION}")
        pie.setObjectName("Nota")
        col.addWidget(pie)
        return panel

    def _barra_superior(self) -> QFrame:
        barra = QFrame()
        barra.setObjectName("BarraSuperior")
        barra.setMinimumHeight(78)
        fila = QHBoxLayout(barra)
        fila.setContentsMargins(30, 16, 30, 16)
        texto = QVBoxLayout()
        texto.setSpacing(2)
        self.lbl_titulo = QLabel(self._paginas[0][1])
        self.lbl_titulo.setObjectName("Titulo")
        self.lbl_subtitulo = QLabel(self._paginas[0][2])
        self.lbl_subtitulo.setObjectName("Subtitulo")
        texto.addWidget(self.lbl_titulo)
        texto.addWidget(self.lbl_subtitulo)
        fila.addLayout(texto)
        fila.addStretch()
        sello = QLabel(f"{VERSION} · PySide6")
        sello.setObjectName("InsigniaNeutra")
        fila.addWidget(sello)
        return barra

    def _pagina(self) -> tuple[QWidget, QVBoxLayout]:
        pagina = QWidget()
        pagina.setObjectName("Pagina")
        col = QVBoxLayout(pagina)
        col.setContentsMargins(30, 24, 30, 24)
        col.setSpacing(16)
        return pagina, col

    def _pagina_scroll(self) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        contenido = QWidget()
        contenido.setObjectName("Contenido")
        col = QVBoxLayout(contenido)
        col.setContentsMargins(30, 24, 24, 24)
        col.setSpacing(16)
        area.setWidget(contenido)
        return area, col

    def _tarjeta(self, titulo: str | None = None,
                 insignia: tuple[str, str] | None = None
                 ) -> tuple[QFrame, QVBoxLayout, QLabel | None]:
        marco = QFrame()
        marco.setObjectName("Tarjeta")
        col = QVBoxLayout(marco)
        col.setContentsMargins(20, 18, 20, 18)
        col.setSpacing(12)
        etiqueta_insignia: QLabel | None = None
        if titulo:
            fila = QHBoxLayout()
            fila.setSpacing(10)
            et = QLabel(titulo)
            et.setObjectName("TarjetaTitulo")
            fila.addWidget(et)
            fila.addStretch()
            if insignia is not None:
                texto, objeto = insignia
                etiqueta_insignia = QLabel(texto)
                etiqueta_insignia.setObjectName(objeto)
                fila.addWidget(etiqueta_insignia)
            col.addLayout(fila)
        return marco, col, etiqueta_insignia

    @staticmethod
    def _preparar_barra(barra: QProgressBar) -> None:
        barra.setRange(0, 100)
        barra.setValue(0)
        barra.setTextVisible(False)

    def _fila_medida(self, etiqueta: str
                     ) -> tuple[QHBoxLayout, QLabel, QProgressBar]:
        fila = QHBoxLayout()
        fila.setSpacing(10)
        et = QLabel(etiqueta)
        et.setObjectName("Nota")
        et.setMinimumWidth(78)
        barra = QProgressBar()
        self._preparar_barra(barra)
        barra.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        valor = QLabel("—")
        valor.setStyleSheet("font-weight: bold;")
        valor.setMinimumWidth(84)
        valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fila.addWidget(et)
        fila.addWidget(barra, 1)
        fila.addWidget(valor)
        return fila, valor, barra

    # ---------- páginas ----------

    def _pagina_resumen(self) -> QWidget:
        pagina, col = self._pagina_scroll()
        fila = QHBoxLayout()
        fila.setSpacing(16)
        for titulo, cifra, pie, guardar in (
            (textos.t("resumen_kpi_perfiles"), str(self._total_perfiles()),
             textos.t("kpi_leidos"), "lbl_n_perfiles"),
            (textos.t("resumen_kpi_certs"), str(self._total_certificados()),
             textos.t("kpi_maquina"), "lbl_n_certs"),
            (textos.t("resumen_kpi_interfaz"), "PySide6",
             textos.t("kpi_iface", v=VERSION), None),
        ):
            tarjeta = QFrame()
            tarjeta.setObjectName("Tarjeta")
            c = QVBoxLayout(tarjeta)
            c.setContentsMargins(20, 16, 20, 16)
            c.setSpacing(6)
            cab = QHBoxLayout()
            cab.setSpacing(8)
            punto = QLabel()
            punto.setObjectName("Punto")
            punto.setFixedSize(6, 6)
            et = QLabel(titulo)
            et.setObjectName("Etiqueta")
            cab.addWidget(punto, 0, Qt.AlignVCenter)
            cab.addWidget(et)
            cab.addStretch()
            val = QLabel(cifra)
            val.setObjectName("Cifra")
            sub = QLabel(pie)
            sub.setObjectName("Nota")
            c.addLayout(cab)
            c.addWidget(val)
            c.addWidget(sub)
            tarjeta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            if guardar:
                setattr(self, guardar, val)
            fila.addWidget(tarjeta)
        col.addLayout(fila)

        columnas = QHBoxLayout()
        columnas.setSpacing(16)

        estado, c, insignia = self._tarjeta(
            textos.t("resumen_watchdog"),
            (textos.t("detenido"), "InsigniaNeutra"))
        self.insignia_watchdog = insignia
        fila_umbral, self.lbl_umbral_resumen = _fila_dato(
            textos.t("umbral_critico"), f"{self._umbral_watchdog} °C")
        c.addLayout(fila_umbral)
        c.addWidget(_separador())
        fila_sensor, self.lbl_sensor_resumen = _fila_dato(
            textos.t("sensor_cpu"), textos.t("comprobando"))
        c.addLayout(fila_sensor)
        c.addWidget(_separador())
        fila_plan, self.lbl_plan_resumen = _fila_dato(
            textos.t("plan_energia"), textos.t("leyendo"))
        c.addLayout(fila_plan)
        c.addWidget(_separador())
        nota = QLabel(textos.t("resumen_nota_wd"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)
        c.addStretch()
        columnas.addWidget(estado, 3)

        migracion, c, _ = self._tarjeta(textos.t("resumen_estado", v=VERSION))
        for i, texto in enumerate((textos.t("hito1"), textos.t("hito2"),
                                   textos.t("hito3"), textos.t("hito4"))):
            fila2 = QHBoxLayout()
            et = QLabel(texto)
            et.setWordWrap(True)
            ins = QLabel("")
            ins.setObjectName("InsigniaNeutra")
            if i == 0:
                self.insignia_hito1 = ins
            else:
                ins.setText(textos.t("hecho"))
                self._poner_objeto(ins, "InsigniaOk")
            fila2.addWidget(et, 1)
            fila2.addWidget(ins, 0, Qt.AlignTop)
            c.addLayout(fila2)
        self._pintar_hito1()
        c.addStretch()
        ver = self._pildora(textos.t("ver_perfiles"), rol="secundario", alto=32)
        ver.clicked.connect(
            lambda: self.stack.setCurrentIndex(self._indice_pagina("perfiles")))
        c.addWidget(ver, alignment=Qt.AlignRight)
        columnas.addWidget(migracion, 2)

        col.addLayout(columnas)
        col.addStretch()
        return pagina

    def _pagina_estado(self) -> QWidget:
        pagina, col = self._pagina_scroll()
        rejilla = QGridLayout()
        rejilla.setSpacing(16)

        cpu, c, self.insignia_cpu = self._tarjeta(
            textos.t("estado_cpu"), (textos.t("comprobando"), "InsigniaNeutra"))
        self.lbl_cpu_temp = QLabel("—")
        self.lbl_cpu_temp.setObjectName("Cifra")
        c.addWidget(self.lbl_cpu_temp)
        fila, self.lbl_cpu_uso, self.bar_cpu_uso = self._fila_medida(
            textos.t("uso"))
        c.addLayout(fila)
        fila, self.lbl_cpu_reloj = _fila_dato(textos.t("clock"), "—")
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(cpu, 0, 0)

        gpu, c, self.insignia_gpu = self._tarjeta(
            textos.t("estado_gpu"), (textos.t("comprobando"), "InsigniaNeutra"))
        self.lbl_gpu_temp = QLabel("—")
        self.lbl_gpu_temp.setObjectName("Cifra")
        c.addWidget(self.lbl_gpu_temp)
        fila, self.lbl_gpu_uso, self.bar_gpu_uso = self._fila_medida(
            textos.t("uso"))
        c.addLayout(fila)
        fila, self.lbl_gpu_w = _fila_dato(textos.t("watts"), "—")
        c.addLayout(fila)
        fila, self.lbl_gpu_reloj = _fila_dato(textos.t("clock"), "—")
        c.addLayout(fila)
        self.lbl_gpu_nombre = QLabel("")
        self.lbl_gpu_nombre.setObjectName("Nota")
        self.lbl_gpu_nombre.setWordWrap(True)
        self.lbl_gpu_nombre.setVisible(False)
        c.addWidget(self.lbl_gpu_nombre)
        c.addStretch()
        rejilla.addWidget(gpu, 0, 1)

        ram, c, _ = self._tarjeta(textos.t("estado_ram"))
        self.lbl_ram_pct = QLabel("—")
        self.lbl_ram_pct.setObjectName("Cifra")
        c.addWidget(self.lbl_ram_pct)
        self.bar_ram = QProgressBar()
        self._preparar_barra(self.bar_ram)
        c.addWidget(self.bar_ram)
        fila, self.lbl_ram_usada = _fila_dato(textos.t("en_uso"), "—")
        c.addLayout(fila)
        fila, self.lbl_ram_total = _fila_dato(textos.t("total"), "—")
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(ram, 1, 0)

        rom, c, _ = self._tarjeta(textos.t("estado_rom"))
        self.lbl_rom_pct = QLabel("—")
        self.lbl_rom_pct.setObjectName("Cifra")
        c.addWidget(self.lbl_rom_pct)
        self.bar_rom = QProgressBar()
        self._preparar_barra(self.bar_rom)
        c.addWidget(self.bar_rom)
        fila, self.lbl_rom_usada = _fila_dato(textos.t("en_uso"), "—")
        c.addLayout(fila)
        fila, self.lbl_rom_total = _fila_dato(textos.t("total"), "—")
        c.addLayout(fila)
        self.lbl_rom_unidad = QLabel("")
        self.lbl_rom_unidad.setObjectName("Nota")
        c.addWidget(self.lbl_rom_unidad)
        c.addStretch()
        rejilla.addWidget(rom, 1, 1)

        rejilla.setColumnStretch(0, 1)
        rejilla.setColumnStretch(1, 1)
        col.addLayout(rejilla)

        pie = QHBoxLayout()
        pie.setSpacing(12)
        nota = QLabel(textos.t("estado_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        pie.addWidget(nota, 1)
        self.lbl_vivo = QLabel(textos.t("en_vivo"))
        self.lbl_vivo.setObjectName("Nota")
        pie.addWidget(self.lbl_vivo)

        et_sondeo = QLabel(textos.t("sondeo"))
        et_sondeo.setObjectName("Nota")
        pie.addWidget(et_sondeo)
        self.combo_sondeo = QComboBox()
        for segundos in (2, 3, 5, 10, 20, 30):
            self.combo_sondeo.addItem(f"{segundos} s", segundos)
        indice = self.combo_sondeo.findData(self._sondeo)
        self.combo_sondeo.setCurrentIndex(max(0, indice))
        self.combo_sondeo.setMinimumWidth(74)
        self.combo_sondeo.currentIndexChanged.connect(self._cambiar_sondeo)
        pie.addWidget(self.combo_sondeo)

        self.btn_pausa = self._pildora(textos.t("pausar"), rol="secundario",
                                      alto=32, ancho=112)
        self.btn_pausa.clicked.connect(self._alternar_pausa)
        pie.addWidget(self.btn_pausa)
        col.addLayout(pie)
        col.addStretch()

        if self._ultimo_vivo:
            self._pintar_vivo(self._ultimo_vivo)
        return pagina

    def _pagina_perfiles(self) -> QWidget:
        pagina, col = self._pagina()

        barra = QHBoxLayout()
        barra.setSpacing(8)
        self.ent_buscar = QLineEdit()
        self.ent_buscar.setPlaceholderText(textos.t("buscar_perfil"))
        self.ent_buscar.setMinimumWidth(220)
        self.ent_buscar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.ent_buscar.textChanged.connect(self._filtrar)
        barra.addWidget(self.ent_buscar, 1)

        refrescar = self._pildora(textos.t("refrescar"), rol="secundario",
                                  alto=36, ancho=112)
        refrescar.clicked.connect(self._refrescar)
        barra.addWidget(refrescar)
        nuevo = self._pildora(textos.t("nuevo_perfil"), rol="primario",
                              alto=36, ancho=132)
        nuevo.clicked.connect(self._nuevo_perfil)
        barra.addWidget(nuevo)
        col.addLayout(barra)

        filtros = QHBoxLayout()
        filtros.setSpacing(8)
        self.grupo_filtro = QButtonGroup(self)
        self.grupo_filtro.setExclusive(True)
        for (clave, texto), ancho in zip(estados(), (74, 108, 138)):
            b = self._pildora(texto, rol="secundario", alto=36, ancho=ancho)
            b.setCheckable(True)
            if clave == "todos":
                b.setChecked(True)
            b.clicked.connect(lambda _=False, c=clave: self._fijar_filtro(c))
            self.grupo_filtro.addButton(b)
            filtros.addWidget(b)
        filtros.addStretch()
        self.lbl_conteo = QLabel()
        self.lbl_conteo.setObjectName("Nota")
        filtros.addWidget(self.lbl_conteo)
        col.addLayout(filtros)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.scroll_perfiles = scroll
        contenido = QWidget()
        contenido.setObjectName("Contenido")
        self._lista_perfiles = QVBoxLayout(contenido)
        self._lista_perfiles.setContentsMargins(0, 0, 6, 0)
        self._lista_perfiles.setSpacing(12)
        scroll.setWidget(contenido)
        col.addWidget(scroll, 1)
        self._pintar_lista()
        return pagina

    def _pagina_herramientas(self) -> QWidget:
        pagina, col = self._pagina_scroll()

        rejilla = QGridLayout()
        rejilla.setSpacing(16)

        tarjeta, c, self.insignia_watchdog_herr = self._tarjeta(
            textos.t("resumen_watchdog"),
            (textos.t("detenido"), "InsigniaNeutra"))
        d = QLabel(textos.t("herr_wd_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        et = QLabel(textos.t("umbral_critico"))
        et.setObjectName("Nota")
        self.combo_umbral = QComboBox()
        for valor in (85, 89, 92, 95):
            self.combo_umbral.addItem(f"{valor} °C", valor)
        indice = self.combo_umbral.findData(self._umbral_watchdog)
        self.combo_umbral.setCurrentIndex(max(0, indice))
        self.combo_umbral.setMinimumWidth(110)
        self.combo_umbral.currentIndexChanged.connect(self._cambiar_umbral)
        fila.addWidget(et)
        fila.addWidget(self.combo_umbral)
        fila.addStretch()
        self.btn_watchdog = self._pildora(textos.t("activar_watchdog"),
                                          rol="primario", alto=32, ancho=168)
        self.btn_watchdog.clicked.connect(self._alternar_watchdog)
        if self._tarea_watchdog is not None:
            self.btn_watchdog.setText(textos.t("detener_watchdog"))
            self.combo_umbral.setEnabled(False)
        fila.addWidget(self.btn_watchdog)
        c.addLayout(fila)

        self.lbl_watchdog_salida = QLabel(textos.t("detenido"))
        self.lbl_watchdog_salida.setObjectName("Nota")
        self.lbl_watchdog_salida.setSizePolicy(QSizePolicy.Ignored,
                                               QSizePolicy.Preferred)
        c.addWidget(self.lbl_watchdog_salida)
        c.addStretch()
        rejilla.addWidget(tarjeta, 0, 0)

        tarjeta, c, self.insignia_sensor = self._tarjeta(
            textos.t("sensor_cpu"), (textos.t("comprobando"), "InsigniaNeutra"))
        d = QLabel(textos.t("herr_sensor_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)
        self.lbl_sensor = QLabel(textos.t("sin_lecturas"))
        self.lbl_sensor.setObjectName("Nota")
        self.lbl_sensor.setWordWrap(True)
        c.addWidget(self.lbl_sensor)
        fila = QHBoxLayout()
        fila.addStretch()
        self.btn_probar = self._pildora(textos.t("probar_lectura"),
                                        rol="secundario", alto=32, ancho=142)
        self.btn_probar.clicked.connect(self._probar_sensor)
        fila.addWidget(self.btn_probar)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 0, 1)

        tarjeta, c, _ = self._tarjeta(textos.t("plan_energia"))
        d = QLabel(textos.t("herr_plan_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)
        self.combo_plan = QComboBox()
        self.combo_plan.setMinimumWidth(220)
        c.addWidget(self.combo_plan)
        self.lbl_plan_herr = QLabel(textos.t("leyendo_planes"))
        self.lbl_plan_herr.setObjectName("Nota")
        self.lbl_plan_herr.setWordWrap(True)
        c.addWidget(self.lbl_plan_herr)
        fila = QHBoxLayout()
        fila.addStretch()
        self.btn_aplicar_plan = self._pildora(textos.t("aplicar_plan"),
                                              rol="secundario", alto=32,
                                              ancho=130)
        self.btn_aplicar_plan.clicked.connect(self._aplicar_plan)
        fila.addWidget(self.btn_aplicar_plan)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 1, 0)

        tarjeta, c, _ = self._tarjeta(textos.t("overlay_fps"))
        self.combo_estilo = QComboBox()
        nombres, activo = servicios.estilos()
        self.combo_estilo.addItems(nombres)
        self.combo_estilo.setCurrentText(activo)
        self.combo_estilo.currentTextChanged.connect(self._cambiar_estilo)
        c.addWidget(self.combo_estilo)

        fila_pos = QHBoxLayout()
        fila_pos.setSpacing(6)
        et_pos = QLabel(textos.t("overlay_posicion"))
        et_pos.setObjectName("Nota")
        c.addWidget(et_pos)
        rejilla_pos = QGridLayout()
        rejilla_pos.setSpacing(3)
        self._grupo_pos = QButtonGroup(self)
        self._grupo_pos.setExclusive(True)
        actual = servicios.posicion_overlay().get("ancla", "arriba-izquierda")
        for i, ancla in enumerate(overlay_estilo.POSICIONES[:9]):
            b = self._pildora(FLECHAS_POS[ancla], rol="secundario",
                              alto=26, ancho=30)
            b.setCheckable(True)
            b.setChecked(ancla == actual)
            b.clicked.connect(lambda _=False, a=ancla: self._fijar_posicion(a))
            self._grupo_pos.addButton(b)
            rejilla_pos.addWidget(b, i // 3, i % 3)
        fila_pos.addStretch()
        fila_pos.addLayout(rejilla_pos)
        fila_pos.addStretch()
        c.addLayout(fila_pos)

        nota_pos = QLabel(textos.t("overlay_pos_nota"))
        nota_pos.setObjectName("Nota")
        nota_pos.setWordWrap(True)
        c.addWidget(nota_pos)

        self.btn_fps_admin = self._pildora(textos.t("fps_admin"),
                                           rol="secundario", alto=30,
                                           ancho=150)
        self.btn_fps_admin.setCheckable(True)
        self.btn_fps_admin.setChecked(
            bool(_leer_opcion("overlay_fps_admin", True)))
        self.btn_fps_admin.toggled.connect(
            lambda v: _guardar_opcion("overlay_fps_admin", bool(v)))

        fila = QHBoxLayout()
        fila.addWidget(self.btn_fps_admin)
        fila.addStretch()
        abrir = self._pildora(textos.t("abrir_sesion"), rol="secundario",
                              alto=32, ancho=126)
        abrir.clicked.connect(self._abrir_overlay)
        fila.addWidget(abrir)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 1, 1)

        tarjeta, c, self.insignia_termico = self._tarjeta(
            textos.t("herr_termico"),
            (textos.t("detenido"), "InsigniaNeutra"))
        d = QLabel(textos.t("herr_termico_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        et = QLabel(textos.t("termico_limite"))
        et.setObjectName("Nota")
        self.combo_termico = QComboBox()
        for valor in (75, 80, 85, 88, 90, 95):
            self.combo_termico.addItem(f"{valor} °C", valor)
        indice = self.combo_termico.findData(self._umbral_termico)
        self.combo_termico.setCurrentIndex(max(0, indice))
        self.combo_termico.setMinimumWidth(92)
        self.combo_termico.currentIndexChanged.connect(
            self._cambiar_umbral_termico)
        fila.addWidget(et)
        fila.addWidget(self.combo_termico)
        fila.addStretch()
        self.btn_termico = self._pildora(textos.t("termico_activar"),
                                         rol="primario", alto=32, ancho=168)
        self.btn_termico.clicked.connect(self._alternar_termico)
        if self._tarea_termico is not None:
            self.btn_termico.setText(textos.t("termico_detener"))
            self.combo_termico.setEnabled(False)
        fila.addWidget(self.btn_termico)
        c.addLayout(fila)

        self.btn_termico_frenar = self._pildora(textos.t("termico_frenar"),
                                                rol="secundario", alto=30,
                                                ancho=172)
        self.btn_termico_frenar.setCheckable(True)
        self.btn_termico_frenar.setChecked(
            bool(_leer_opcion("termico_frenar", True)))
        self.btn_termico_frenar.toggled.connect(
            lambda v: _guardar_opcion("termico_frenar", bool(v)))
        fila = QHBoxLayout()
        fila.addWidget(self.btn_termico_frenar)
        fila.addStretch()
        c.addLayout(fila)

        self.lbl_termico_salida = QLabel(textos.t("detenido"))
        self.lbl_termico_salida.setObjectName("Nota")
        self.lbl_termico_salida.setSizePolicy(QSizePolicy.Ignored,
                                              QSizePolicy.Preferred)
        c.addWidget(self.lbl_termico_salida)
        c.addStretch()
        rejilla.addWidget(tarjeta, 2, 0)

        tarjeta, c, self.insignia_ram = self._tarjeta(
            textos.t("herr_ram"), ("—", "InsigniaNeutra"))
        d = QLabel(textos.t("herr_ram_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)
        self.lbl_ram_libre = QLabel(textos.t("sin_dato"))
        self.lbl_ram_libre.setObjectName("Cifra")
        c.addWidget(self.lbl_ram_libre)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        self.btn_ram = self._pildora(textos.t("ram_limpiar"), rol="primario",
                                     alto=32, ancho=126)
        self.btn_ram.clicked.connect(lambda: self._limpiar_ram(False))
        self.btn_ram_profunda = self._pildora(textos.t("ram_profunda"),
                                              rol="secundario", alto=32,
                                              ancho=104)
        self.btn_ram_profunda.clicked.connect(lambda: self._limpiar_ram(True))
        fila.addWidget(self.btn_ram)
        fila.addWidget(self.btn_ram_profunda)
        fila.addStretch()
        c.addLayout(fila)

        if servicios.memreduct() is None:
            self.btn_ram.setEnabled(False)
            self.btn_ram_profunda.setEnabled(False)
            no_hay = QLabel(textos.t("m_ram_no"))
            no_hay.setObjectName("Nota")
            no_hay.setWordWrap(True)
            c.addWidget(no_hay)

        fila = QHBoxLayout()
        fila.setSpacing(8)
        self.btn_ram_auto = self._pildora(textos.t("ram_auto"),
                                          rol="secundario", alto=30, ancho=112)
        self.btn_ram_auto.setCheckable(True)
        self.btn_ram_auto.clicked.connect(self._alternar_auto_ram)
        et_cada = QLabel(textos.t("ram_cada"))
        et_cada.setObjectName("Nota")
        self.combo_ram_min = QComboBox()
        for n in (5, 10, 15, 30, 60):
            self.combo_ram_min.addItem(f"{n} min", n)
        indice = self.combo_ram_min.findData(self._ram_intervalo)
        self.combo_ram_min.setCurrentIndex(max(0, indice))
        self.combo_ram_min.setMinimumWidth(88)
        self.combo_ram_min.currentIndexChanged.connect(
            self._reconfigurar_auto_ram)
        et_si = QLabel(textos.t("ram_si_libre"))
        et_si.setObjectName("Nota")
        self.combo_ram_umbral = QComboBox()
        self.combo_ram_umbral.addItem(textos.t("ram_siempre"), 0.0)
        for gb in (1.0, 2.0, 4.0, 8.0):
            self.combo_ram_umbral.addItem(f"{gb:g} GB", gb)
        indice = self.combo_ram_umbral.findData(self._ram_umbral)
        self.combo_ram_umbral.setCurrentIndex(max(0, indice))
        self.combo_ram_umbral.setMinimumWidth(96)
        self.combo_ram_umbral.currentIndexChanged.connect(
            self._reconfigurar_auto_ram)
        fila.addWidget(self.btn_ram_auto)
        fila.addWidget(et_cada)
        fila.addWidget(self.combo_ram_min)
        fila.addWidget(et_si)
        fila.addWidget(self.combo_ram_umbral)
        fila.addStretch()
        c.addLayout(fila)

        self.lbl_ram_salida = QLabel(textos.t("listo"))
        self.lbl_ram_salida.setObjectName("Nota")
        self.lbl_ram_salida.setSizePolicy(QSizePolicy.Ignored,
                                          QSizePolicy.Preferred)
        c.addWidget(self.lbl_ram_salida)
        c.addStretch()
        rejilla.addWidget(tarjeta, 2, 1)

        tarjeta, c, self.insignia_procesos = self._tarjeta(
            textos.t("herr_procesos"), (textos.t("listo"), "InsigniaNeutra"))
        d = QLabel(textos.t("herr_procesos_nota"))
        d.setObjectName("Nota")
        d.setWordWrap(True)
        c.addWidget(d)

        self.lista_procesos = QListWidget()
        self.lista_procesos.setObjectName("ListaProcesos")
        self.lista_procesos.setMinimumHeight(110)
        self.lista_procesos.setSelectionMode(QAbstractItemView.NoSelection)
        self.lista_procesos.setFocusPolicy(Qt.NoFocus)
        c.addWidget(self.lista_procesos)

        self.lbl_procesos = QLabel("")
        self.lbl_procesos.setObjectName("Nota")
        self.lbl_procesos.setWordWrap(True)
        c.addWidget(self.lbl_procesos)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        fila.addStretch()
        self.btn_proc_analizar = self._pildora(textos.t("proc_analizar"),
                                              rol="secundario", alto=32,
                                              ancho=126)
        self.btn_proc_analizar.clicked.connect(self._analizar_procesos)
        self.btn_proc_cerrar = self._pildora(textos.t("proc_cerrar"),
                                             rol="peligro", alto=32,
                                             ancho=186)
        self.btn_proc_cerrar.clicked.connect(self._cerrar_seleccionados)
        self.btn_proc_cerrar.setEnabled(False)
        fila.addWidget(self.btn_proc_analizar)
        fila.addWidget(self.btn_proc_cerrar)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 3, 0, 1, 2)

        self.btn_ram_auto.setChecked(self._ram_auto)
        if servicios.memreduct() is None:
            self.btn_ram_auto.setEnabled(False)

        rejilla.setColumnStretch(0, 1)
        rejilla.setColumnStretch(1, 1)
        col.addLayout(rejilla)
        col.addStretch()
        return pagina

    def _fila_dato(self, col: QVBoxLayout, etiqueta: str) -> QLabel:
        fila = QHBoxLayout()
        fila.setSpacing(10)
        et = QLabel(etiqueta.upper())
        et.setObjectName("Etiqueta")
        et.setMinimumWidth(78)
        valor = QLabel("—")
        valor.setWordWrap(True)
        fila.addWidget(et)
        fila.addWidget(valor, 1)
        col.addLayout(fila)
        return valor

    def _pagina_recomendaciones(self) -> QWidget:
        pagina, col = self._pagina_scroll()

        fila = QHBoxLayout()
        fila.setSpacing(16)
        izquierda = QVBoxLayout()
        izquierda.setSpacing(16)

        tarjeta, c, self.insignia_equipo = self._tarjeta(
            textos.t("rec_tipo"), (textos.t("rec_leyendo"), "InsigniaNeutra"))
        self.lbl_tipo = QLabel("—")
        self.lbl_tipo.setObjectName("Cifra")
        c.addWidget(self.lbl_tipo)
        self.lbl_modelo = QLabel("")
        self.lbl_modelo.setObjectName("Nota")
        self.lbl_modelo.setWordWrap(True)
        c.addWidget(self.lbl_modelo)
        self.lbl_fuente = QLabel(textos.t("rec_fuente_est"))
        self.lbl_fuente.setObjectName("Nota")
        self.lbl_fuente.setWordWrap(True)
        c.addWidget(self.lbl_fuente)
        c.addStretch()
        izquierda.addWidget(tarjeta)

        tarjeta, c, _ = self._tarjeta(textos.t("rec_comp"))
        self.lbl_dato_cpu = self._fila_dato(c, textos.t("cpu"))
        self.lbl_dato_ram = self._fila_dato(c, textos.t("ram"))
        self.lbl_dato_gpu = self._fila_dato(c, textos.t("gpu"))
        self.lbl_dato_energia = self._fila_dato(c, textos.t("rec_energia"))
        self.lbl_dato_win = self._fila_dato(c, "Windows")
        c.addStretch()
        izquierda.addWidget(tarjeta)
        izquierda.addStretch()
        fila.addLayout(izquierda, 1)

        derecha = QVBoxLayout()
        derecha.setSpacing(16)
        tarjeta, c, self.insignia_reco = self._tarjeta(
            textos.t("rec_perfil"), (textos.t("rec_leyendo"), "InsigniaNeutra"))
        self.lbl_reco_nombre = QLabel("—")
        self.lbl_reco_nombre.setObjectName("NombrePerfil")
        c.addWidget(self.lbl_reco_nombre)
        self.lbl_reco_plan = QLabel("")
        self.lbl_reco_plan.setObjectName("Nota")
        self.lbl_reco_plan.setWordWrap(True)
        c.addWidget(self.lbl_reco_plan)
        self.lbl_reco_motivo = QLabel("")
        self.lbl_reco_motivo.setObjectName("Nota")
        self.lbl_reco_motivo.setWordWrap(True)
        c.addWidget(self.lbl_reco_motivo)
        botones = QHBoxLayout()
        botones.setSpacing(10)
        botones.addStretch()
        self.btn_reco_analizar = self._pildora(textos.t("rec_analizar"),
                                              rol="secundario", alto=32,
                                              ancho=158)
        self.btn_reco_analizar.clicked.connect(self._analizar_equipo)
        self.btn_reco_crear = self._pildora(textos.t("rec_crear"),
                                           rol="secundario", alto=32,
                                           ancho=128)
        self.btn_reco_crear.clicked.connect(self._crear_reco)
        self.btn_reco_aplicar = self._pildora(textos.t("rec_aplicar"),
                                             rol="primario", alto=32,
                                             ancho=184)
        self.btn_reco_aplicar.clicked.connect(self._aplicar_reco)
        for boton in (self.btn_reco_analizar, self.btn_reco_crear,
                      self.btn_reco_aplicar):
            botones.addWidget(boton)
        c.addLayout(botones)
        c.addStretch()
        derecha.addWidget(tarjeta)
        derecha.addStretch()
        fila.addLayout(derecha, 1)

        col.addLayout(fila)
        col.addStretch()

        if self._equipo:
            self._pintar_equipo()
            self._reco = servicios.recomendar_perfil(self._equipo, self.perfiles,
                                                     self.certs)
            self._pintar_reco()
        return pagina

    def _pagina_centro(self) -> QWidget:
        pagina, col = self._pagina_scroll()
        rejilla = QGridLayout()
        rejilla.setHorizontalSpacing(16)
        rejilla.setVerticalSpacing(16)

        tarjeta, c, self.insignia_prog = self._tarjeta(
            textos.t("cen_prog"), ("0", "InsigniaNeutra"))
        nota = QLabel(textos.t("cen_prog_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)

        controles = QHBoxLayout()
        controles.setSpacing(10)
        self.combo_accion = QComboBox()
        for clave in ("ram", "ram_full", "diag", "bat", "normal"):
            self.combo_accion.addItem(textos.t(f"cen_acc_{clave}"), clave)
        self.combo_accion.setMinimumWidth(190)
        controles.addWidget(self.combo_accion)
        self.fecha_prog = QDateTimeEdit(QDateTime.currentDateTime().addSecs(600))
        self.fecha_prog.setCalendarPopup(True)
        self.fecha_prog.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.fecha_prog.setMinimumWidth(168)
        controles.addWidget(self.fecha_prog)
        self.chk_diaria = QCheckBox(textos.t("cen_diaria"))
        self.chk_diaria.toggled.connect(self._diaria_cambiada)
        controles.addWidget(self.chk_diaria)
        self.btn_programar = self._pildora(textos.t("cen_programar"),
                                          rol="secundario", alto=32,
                                          ancho=124)
        self.btn_programar.clicked.connect(self._programar)
        controles.addWidget(self.btn_programar)
        controles.addStretch()
        c.addLayout(controles)

        self.lista_prog = QListWidget()
        self.lista_prog.setObjectName("ListaProgramadas")
        self.lista_prog.setFixedHeight(128)
        self.lista_prog.setSelectionMode(QAbstractItemView.SingleSelection)
        c.addWidget(self.lista_prog)
        self.lbl_prog = QLabel("")
        self.lbl_prog.setObjectName("Nota")
        self.lbl_prog.setWordWrap(True)
        c.addWidget(self.lbl_prog)
        pie = QHBoxLayout()
        pie.addStretch()
        self.btn_quitar_prog = self._pildora(textos.t("cen_quitar"),
                                            rol="peligro", alto=30, ancho=118)
        self.btn_quitar_prog.clicked.connect(self._quitar_programada)
        pie.addWidget(self.btn_quitar_prog)
        c.addLayout(pie)
        rejilla.addWidget(tarjeta, 0, 0, 1, 2)

        tarjeta, c, self.insignia_diag = self._tarjeta(
            textos.t("cen_diag"), (textos.t("listo"), "InsigniaNeutra"))
        nota = QLabel(textos.t("cen_diag_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)
        self.lbl_diag = QLabel("—")
        self.lbl_diag.setObjectName("Nota")
        self.lbl_diag.setWordWrap(True)
        self.lbl_diag.setTextInteractionFlags(Qt.TextSelectableByMouse)
        c.addWidget(self.lbl_diag)
        fila = QHBoxLayout()
        fila.addStretch()
        self.btn_diag = self._pildora(textos.t("cen_diag_btn"),
                                     rol="secundario", alto=32, ancho=136)
        self.btn_diag.clicked.connect(self._autodiagnostico)
        fila.addWidget(self.btn_diag)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 1, 0)

        tarjeta, c, self.insignia_bat = self._tarjeta(
            textos.t("cen_bat"), (textos.t("rec_leyendo"), "InsigniaNeutra"))
        self.lbl_bat = QLabel(textos.t("rec_leyendo"))
        self.lbl_bat.setObjectName("NombrePerfil")
        self.lbl_bat.setWordWrap(True)
        c.addWidget(self.lbl_bat)
        nota = QLabel(textos.t("cen_bat_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)
        fila = QHBoxLayout()
        fila.setSpacing(10)
        fila.addStretch()
        self.btn_bat = self._pildora(textos.t("cen_bat_btn"), rol="primario",
                                    alto=32, ancho=136)
        self.btn_bat.clicked.connect(self._optimizar_bateria)
        fila.addWidget(self.btn_bat)
        self.btn_normal = self._pildora(textos.t("cen_normal_btn"),
                                       rol="secundario", alto=32, ancho=124)
        self.btn_normal.clicked.connect(self._volver_normal)
        fila.addWidget(self.btn_normal)
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 1, 1)

        tarjeta, c, _ = self._tarjeta(textos.t("cen_extra"))
        nota = QLabel(textos.t("cen_extra_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)
        fila = QHBoxLayout()
        fila.setSpacing(10)
        self.btn_extra_ram = self._pildora(textos.t("ram_limpiar"),
                                          rol="secundario", alto=32,
                                          ancho=156)
        self.btn_extra_ram.clicked.connect(lambda: self._limpiar_ram(False))
        fila.addWidget(self.btn_extra_ram)
        self.btn_extra_proc = self._pildora(textos.t("herr_procesos"),
                                           rol="secundario", alto=32,
                                           ancho=226)
        self.btn_extra_proc.clicked.connect(
            lambda: self.stack.setCurrentIndex(
                self._indice_pagina("herramientas")))
        fila.addWidget(self.btn_extra_proc)
        fila.addStretch()
        c.addLayout(fila)
        c.addStretch()
        rejilla.addWidget(tarjeta, 2, 0, 1, 2)

        rejilla.setColumnStretch(0, 1)
        rejilla.setColumnStretch(1, 1)
        col.addLayout(rejilla)
        col.addStretch()

        self._pintar_programadas()
        self._pintar_bat()
        return pagina

    def _pagina_apariencia(self) -> QWidget:
        pagina, col = self._pagina_scroll()

        fila = QHBoxLayout()
        fila.setSpacing(16)
        izquierda = QVBoxLayout()
        izquierda.setSpacing(16)

        fuente, c, _ = self._tarjeta(textos.t("ap_fuente"))
        controles = QHBoxLayout()
        controles.setSpacing(10)
        self.combo_fuente = QComboBox()
        disponibles = set(QFontDatabase.families())
        for familia in FUENTES:
            if familia in disponibles:
                self.combo_fuente.addItem(familia)
        if self.combo_fuente.count() == 0:
            self.combo_fuente.addItem("Segoe UI")
        if self.combo_fuente.findText(self.familia) < 0:
            self.combo_fuente.addItem(self.familia)
        self.combo_fuente.setCurrentText(self.familia)
        self.combo_fuente.setMinimumWidth(260)
        self.combo_fuente.currentTextChanged.connect(self._cambiar_fuente)
        self.combo_tamano = QComboBox()
        for n in range(9, 14):
            self.combo_tamano.addItem(f"{n} pt", n)
        self.combo_tamano.setCurrentIndex(self.tamano - 9)
        self.combo_tamano.setMinimumWidth(92)
        self.combo_tamano.setToolTip(textos.t("ap_min_nota"))
        self.combo_tamano.currentIndexChanged.connect(
            lambda _: self._cambiar_tamano(self.combo_tamano.currentData())
        )
        controles.addWidget(self.combo_fuente, 1)
        controles.addWidget(self.combo_tamano)
        c.addLayout(controles)

        muestra = QFrame()
        muestra.setObjectName("TarjetaInterior")
        m = QVBoxLayout(muestra)
        m.setContentsMargins(16, 12, 16, 12)
        m.setSpacing(2)
        self.lbl_aa = QLabel("Aa Bb Cc")
        self.lbl_muestra = QLabel(textos.t("ap_muestra"))
        m.addWidget(self.lbl_aa)
        m.addWidget(self.lbl_muestra)
        c.addWidget(muestra)
        izquierda.addWidget(fuente)

        tema, c, _ = self._tarjeta(textos.t("ap_tema_acento"))
        fila_t = QHBoxLayout()
        fila_t.setSpacing(10)
        et = QLabel(textos.t("tema"))
        et.setObjectName("Nota")
        fila_t.addWidget(et)
        self._crear_par_tema(fila_t)
        fila_t.addStretch()
        c.addLayout(fila_t)

        fila_a = QHBoxLayout()
        fila_a.setSpacing(6)
        et2 = QLabel(textos.t("acento"))
        et2.setObjectName("Nota")
        fila_a.addWidget(et2)
        fila_a.addSpacing(4)
        for nombre_acento, color in ACENTOS:
            caja, _ = self._crear_muestra(color, nombre_acento)
            fila_a.addWidget(caja)
        personalizado = self._pildora(textos.t("otro"), rol="secundario",
                                      alto=32, ancho=64)
        personalizado.setToolTip(textos.t("ap_acento_tip"))
        personalizado.clicked.connect(self._elegir_acento)
        fila_a.addWidget(personalizado)
        fila_a.addStretch()
        c.addLayout(fila_a)

        fila_r = QHBoxLayout()
        fila_r.setSpacing(10)
        ayuda = QLabel(textos.t("ap_ayuda"))
        ayuda.setObjectName("Nota")
        fila_r.addWidget(ayuda)
        fila_r.addStretch()
        rest = self._pildora(textos.t("restablecer"), rol="secundario",
                             alto=32, ancho=124)
        rest.clicked.connect(self._restablecer_apariencia)
        fila_r.addWidget(rest)
        c.addLayout(fila_r)
        izquierda.addWidget(tema)
        fila.addLayout(izquierda, 3)

        derecha = QVBoxLayout()
        derecha.setSpacing(16)

        idioma_tarjeta, c, _ = self._tarjeta(textos.t("idioma_titulo"))
        self.combo_idioma = QComboBox()
        for codigo, nombre_idioma in textos.IDIOMAS:
            self.combo_idioma.addItem(nombre_idioma, codigo)
        actual = self.combo_idioma.findData(self.idioma)
        self.combo_idioma.setCurrentIndex(max(0, actual))
        self.combo_idioma.setMinimumWidth(240)
        self.combo_idioma.currentIndexChanged.connect(
            lambda _: self._aplicar_idioma(self.combo_idioma.currentData()))
        c.addWidget(self.combo_idioma)
        nota_idioma = QLabel(textos.t("idioma_nota"))
        nota_idioma.setObjectName("Nota")
        nota_idioma.setWordWrap(True)
        c.addWidget(nota_idioma)
        derecha.addWidget(idioma_tarjeta)

        vista, c, _ = self._tarjeta(textos.t("ap_vista"))
        maqueta = QFrame()
        maqueta.setObjectName("TarjetaInterior")
        v = QVBoxLayout(maqueta)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)
        cab = QHBoxLayout()
        cab.setSpacing(10)
        avatar = QLabel("A")
        avatar.setObjectName("Avatar")
        avatar.setFixedSize(38, 38)
        avatar.setAlignment(Qt.AlignCenter)
        texto = QVBoxLayout()
        texto.setSpacing(2)
        nombre = QLabel(textos.t("ap_perfil_ejemplo"))
        nombre.setObjectName("NombrePerfil")
        sub = QLabel(textos.t("ap_perfil_nota"))
        sub.setObjectName("Nota")
        sub.setWordWrap(True)
        texto.addWidget(nombre)
        texto.addWidget(sub)
        cab.addWidget(avatar, 0, Qt.AlignTop)
        cab.addLayout(texto, 1)
        v.addLayout(cab)

        botones = QHBoxLayout()
        botones.setSpacing(8)
        for texto_b, rol in ((textos.t("aplicar"), "primario"),
                             (textos.t("certificar"), "secundario"),
                             (textos.t("borrar"), "peligro")):
            b = self._pildora(texto_b, rol=rol, alto=32)
            b.clicked.connect(
                lambda _=False, n=texto_b:
                self._estado(textos.t("ap_boton_nota", n=n)))
            botones.addWidget(b)
        botones.addStretch()
        v.addLayout(botones)

        insignias = QHBoxLayout()
        insignias.setSpacing(8)
        for texto_i, objeto in ((textos.t("certificado"), "InsigniaOk"),
                                ("CPU 90 %", "Insignia"),
                                (textos.t("ap_ejemplo"), "InsigniaNeutra")):
            b = QLabel(texto_i)
            b.setObjectName(objeto)
            insignias.addWidget(b)
        insignias.addStretch()
        v.addLayout(insignias)
        c.addWidget(maqueta)
        nota = QLabel(textos.t("ap_pie_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        c.addWidget(nota)
        derecha.addWidget(vista)
        derecha.addStretch()
        fila.addLayout(derecha, 2)
        col.addLayout(fila)
        col.addStretch()
        return pagina

    # ---------- lista de perfiles ----------

    def _pintar_lista(self) -> None:
        while self._lista_perfiles.count():
            item = self._lista_perfiles.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.tarjetas_perfil.clear()
        self.expansores.clear()

        if self.aviso:
            motivo, detalle = self.aviso
            if motivo == "error":
                texto_aviso = textos.t(
                    "aviso_error",
                    e=detalle or textos.t("estructura_inesperada"))
            else:
                texto_aviso = textos.t("aviso_sin_perfiles")
            av = QLabel(texto_aviso)
            av.setObjectName("Aviso")
            av.setWordWrap(True)
            self._lista_perfiles.addWidget(av)

        for nombre in sorted(self.perfiles, key=str.casefold):
            self._lista_perfiles.addWidget(
                self._tarjeta_perfil(nombre, self.perfiles[nombre])
            )
        self.lbl_vacio = QLabel(textos.t("sin_resultados"))
        self.lbl_vacio.setObjectName("Nota")
        self.lbl_vacio.setAlignment(Qt.AlignCenter)
        self.lbl_vacio.setContentsMargins(0, 28, 0, 28)
        self.lbl_vacio.setVisible(False)
        self._lista_perfiles.addWidget(self.lbl_vacio)
        self._lista_perfiles.addStretch()
        if hasattr(self, "ent_buscar"):
            self._filtrar(self.ent_buscar.text())

    def _tarjeta_perfil(self, nombre: str, cfg: dict) -> QFrame:
        cert = dic(self.certs.get(nombre))
        ok = bool(cert.get("certificado"))
        visible = self._etiqueta_perfil(nombre, cfg)

        tarjeta = QFrame()
        tarjeta.setObjectName("Tarjeta")
        v = QVBoxLayout(tarjeta)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        cab = QHBoxLayout()
        cab.setSpacing(14)
        inicial = visible.strip()[:1].upper() or "·"
        avatar = QLabel(inicial)
        avatar.setObjectName("Avatar")
        avatar.setFixedSize(42, 42)
        avatar.setAlignment(Qt.AlignCenter)
        cab.addWidget(avatar, 0, Qt.AlignTop)

        texto = QVBoxLayout()
        texto.setSpacing(3)
        linea1 = QHBoxLayout()
        linea1.setSpacing(8)
        etiqueta = QLabel(visible)
        etiqueta.setObjectName("NombrePerfil")
        insignia = QLabel(textos.t("certificado") if ok
                          else textos.t("sin_certificar"))
        insignia.setObjectName("InsigniaOk" if ok else "InsigniaPend")
        cpu = QLabel(f"CPU {cfg.get('cpu_max', 100)} %")
        cpu.setObjectName("Insignia")
        linea1.addWidget(etiqueta)
        linea1.addWidget(insignia)
        linea1.addWidget(cpu)
        linea1.addStretch()
        texto.addLayout(linea1)

        nota_texto = textos.nota_perfil(nombre, cfg.get("nota"))
        nota = QLabel(nota_texto or textos.t("sin_nota"))
        nota.setObjectName("Nota")
        nota.setWordWrap(True)
        texto.addWidget(nota)

        plan = self._nombre_plan(cfg.get("plan"))
        partes = [textos.t("plan", nombre=plan)]
        if ok and cert.get("fecha"):
            partes.append(textos.t(
                "cert_el", f=str(cert["fecha"]).split()[0]))
        if ok and cert.get("pico_gpu_c") not in (None, ""):
            partes.append(textos.t("cert_pico", g=cert.get("pico_gpu_c")))
        meta = QLabel("  ·  ".join(partes))
        meta.setObjectName("Nota")
        texto.addWidget(meta)
        cab.addLayout(texto, 1)

        boton = self._pildora(f"{textos.t('acciones')} ▾", rol="secundario",
                              alto=32, ancho=118)
        cab.addWidget(boton, 0, Qt.AlignTop)
        v.addLayout(cab)

        acciones = QWidget()
        a = QHBoxLayout(acciones)
        a.setContentsMargins(56, 2, 0, 0)
        a.setSpacing(8)
        for texto_b, rol, accion in (
            (textos.t("aplicar"), "primario", self._aplicar_perfil),
            (textos.t("certificar"), "secundario", self._certificar_perfil),
            (textos.t("ver_editar"), "secundario", self._editar_perfil),
            (textos.t("borrar"), "peligro", self._borrar_perfil),
        ):
            b = self._pildora(texto_b, rol=rol, alto=32, ancho=108)
            b.clicked.connect(lambda _=False, f=accion, n=nombre: f(n))
            a.addWidget(b)
        a.addStretch()
        acciones.setVisible(False)
        v.addWidget(acciones)

        boton.clicked.connect(
            lambda _=False, pnl=acciones, btn=boton, card=tarjeta:
            self._alternar(pnl, btn, card)
        )
        self.expansores.append((boton, acciones, tarjeta))
        contenido = " ".join([
            visible, nota_texto, plan, str(cfg.get("cpu_max", "")),
        ])
        self.tarjetas_perfil.append((tarjeta, contenido.casefold(), ok))
        return tarjeta

    def _total_certificados(self) -> int:
        return sum(1 for c in self.certs.values() if dic(c).get("certificado"))

    def _total_perfiles(self) -> int:
        """Perfiles guardados de verdad; el de fábrica no cuenta como trabajo del usuario."""
        return sum(1 for n in self.perfiles if not textos.es_perfil_fabrica(n))

    def _pintar_hito1(self) -> None:
        """El primer hito solo está hecho cuando hay perfiles o certificados reales."""
        insignia = getattr(self, "insignia_hito1", None)
        if insignia is None:
            return
        hecho = self._total_perfiles() > 0 or self._total_certificados() > 0
        insignia.setText(textos.t("hecho") if hecho else textos.t("pendiente"))
        self._poner_objeto(insignia, "InsigniaOk" if hecho else "InsigniaNeutra")

    def _actualizar_metricas(self) -> None:
        if hasattr(self, "lbl_n_perfiles"):
            self.lbl_n_perfiles.setText(str(self._total_perfiles()))
        if hasattr(self, "lbl_n_certs"):
            self.lbl_n_certs.setText(str(self._total_certificados()))
        self._pintar_hito1()

    def _refrescar(self, silencioso: bool = False) -> None:
        self.perfiles, self.certs, self.aviso = cargar_perfiles()
        self._marcas_archivos = self._leer_marcas()
        self._actualizar_metricas()
        self._pintar_lista()
        self._actualizar_resumen()
        if not silencioso:
            self._estado(textos.t("m_perfiles_recargados"))

    @staticmethod
    def _leer_marcas() -> tuple[float, float]:
        marcas = []
        for ruta in (PERFILES, CERTS):
            try:
                marcas.append(os.path.getmtime(ruta))
            except OSError:
                marcas.append(0.0)
        return marcas[0], marcas[1]

    def _vigilar_archivos(self) -> None:
        if self._cerrando:
            return
        if self._leer_marcas() != self._marcas_archivos:
            self._refrescar(silencioso=True)

    def _fijar_filtro(self, clave: str) -> None:
        self._filtro = clave
        if hasattr(self, "ent_buscar"):
            self._filtrar(self.ent_buscar.text())

    def _filtrar(self, texto: str) -> None:
        busqueda = texto.strip().casefold()
        visibles = 0
        for tarjeta, contenido, certificado in self.tarjetas_perfil:
            coincide_filtro = (self._filtro == "todos"
                               or (self._filtro == "cert") == certificado)
            visible = busqueda in contenido and coincide_filtro
            tarjeta.setVisible(visible)
            visibles += 1 if visible else 0
        if getattr(self, "lbl_vacio", None) is not None:
            self.lbl_vacio.setVisible(visibles == 0)
        if hasattr(self, "lbl_conteo"):
            total = len(self.tarjetas_perfil)
            self.lbl_conteo.setText(
                textos.t("contador", n=visibles, total=total))

    def _alternar(self, panel: QWidget, boton: QPushButton, tarjeta: QFrame) -> None:
        for otro_btn, otro_panel, _ in self.expansores:
            if otro_panel is not panel and otro_panel.isVisible():
                otro_panel.setVisible(False)
                otro_btn.setText(f"{textos.t('acciones')} ▾")
        visible = not panel.isVisible()
        panel.setVisible(visible)
        boton.setText(f"{textos.t('acciones')} {'▴' if visible else '▾'}")
        if visible and self.scroll_perfiles is not None:
            QTimer.singleShot(
                60, lambda: self.scroll_perfiles.ensureWidgetVisible(tarjeta, 0, 12)
            )

    # ---------- acciones de perfiles ----------

    @staticmethod
    def _plan_local(p: dict) -> str:
        """Nombre del plan en el idioma de la app si es uno de los de Windows."""
        return textos.nombre_plan(p.get("alias"), p["nombre"])

    def _nombre_plan(self, valor) -> str:
        texto = str(valor or "").strip()
        if not texto:
            return "—"
        for p in self._planes:
            if texto.lower() in (p["guid"].lower(), p["nombre"].lower()):
                return self._plan_local(p)
        guid, nombre = planes_energia.resolver(texto)
        p = planes_energia.por_guid(guid)
        if p:
            return self._plan_local(p)
        return nombre or texto

    @staticmethod
    def _etiqueta_perfil(nombre: str, cfg: dict | None = None) -> str:
        """Nombre visible: el perfil de fábrica se traduce, el resto va tal cual."""
        return textos.nombre_perfil(nombre, (cfg or {}).get("nota"))

    def _aplicar_perfil(self, nombre: str) -> None:
        cfg = self.perfiles.get(nombre)
        if not cfg:
            return
        visible = self._etiqueta_perfil(nombre, cfg)
        if not servicios.es_certificado(nombre):
            respuesta = QMessageBox.question(
                self, textos.t("t_perfil_sin_cert"),
                textos.t("p_aplicar_igual", n=visible),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if respuesta != QMessageBox.Yes:
                return
        cerrar_ok = False
        if cfg.get("cerrar"):
            respuesta = QMessageBox.question(
                self, textos.t("t_cerrar_apps"),
                textos.t("p_cerrar_apps", l=", ".join(cfg["cerrar"])),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            cerrar_ok = respuesta == QMessageBox.Yes
        try:
            resultado = servicios.aplicar_perfil(nombre, cfg, cerrar_ok)
        except Exception as e:
            QMessageBox.critical(self, textos.t("t_no_aplicar"), str(e))
            return
        partes = [textos.t("m_aplicado", n=visible),
                  textos.t("m_plan_parte",
                           p=resultado["plan"] or textos.t("sin_cambio")),
                  textos.t("m_techo_parte", c=resultado["pct"])]
        if resultado["cerradas"]:
            partes.append(textos.t("m_cerradas",
                                   l=", ".join(resultado["cerradas"])))
        self._estado("  ·  ".join(partes))
        self._sondear(False)
        manual = []
        if resultado["fans"]:
            manual.append(textos.t("m_fans", v=resultado["fans"]))
        if resultado["xtu"]:
            manual.append(textos.t("m_xtu", v=resultado["xtu"]))
        if manual:
            QMessageBox.information(
                self, textos.t("t_ajustes_manuales"),
                "\n".join(manual) + "\n\n" + textos.t("m_manual_nota"))

    def _certificar_perfil(self, nombre: str) -> None:
        if self._tarea_watchdog is not None:
            QMessageBox.warning(
                self, textos.t("t_wd_activo"),
                textos.t("p_wd_certificar"))
            return
        cfg = self.perfiles.get(nombre) or {}
        DialogoCertificar(self, nombre, cfg.get("nota", "")).exec()
        self._refrescar(silencioso=True)
        self._sondear(False)

    def _editar_perfil(self, nombre: str) -> None:
        cfg = self.perfiles.get(nombre) or {}
        dialogo = DialogoPerfil(self, nombre=nombre, cfg=cfg,
                                cert=dic(self.certs.get(nombre)),
                                planes=self._planes or None)
        if not dialogo.exec():
            return
        try:
            retirado = servicios.guardar_perfil(nombre, dialogo.datos)
        except Exception as e:
            QMessageBox.critical(self, textos.t("t_no_guardar_perfil"), str(e))
            return
        self._refrescar(silencioso=True)
        mensaje = textos.t("m_perfil_guardado",
                           n=self._etiqueta_perfil(nombre, cfg))
        if retirado:
            mensaje += textos.t("m_cert_retirada")
        self._estado(mensaje)

    def _nuevo_perfil(self) -> None:
        activo = next((p["guid"] for p in self._planes if p.get("activo")), "")
        cfg = {"plan": activo or self._plan_activo or "alto", "cpu_max": 100,
               "cerrar": [], "prioridad": "normal", "nota": ""}
        dialogo = DialogoPerfil(self, nombre=None, cfg=cfg, cert={},
                                planes=self._planes or None)
        if not dialogo.exec():
            return
        if dialogo.nombre in self.perfiles:
            QMessageBox.warning(
                self, textos.t("t_nombre_repetido"),
                textos.t("p_nombre_repetido", n=dialogo.nombre))
            return
        try:
            servicios.guardar_perfil(dialogo.nombre, dialogo.datos, es_nuevo=True)
        except Exception as e:
            QMessageBox.critical(self, textos.t("t_no_crear_perfil"), str(e))
            return
        self._refrescar(silencioso=True)
        self._estado(textos.t("m_perfil_creado", n=dialogo.nombre))

    def _borrar_perfil(self, nombre: str) -> None:
        visible = self._etiqueta_perfil(nombre, self.perfiles.get(nombre))
        respuesta = QMessageBox.question(
            self, textos.t("t_borrar_perfil"),
            textos.t("p_borrar_perfil", n=visible),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if respuesta != QMessageBox.Yes:
            return
        try:
            servicios.borrar_perfil(nombre)
        except Exception as e:
            QMessageBox.critical(self, textos.t("t_no_borrar_perfil"), str(e))
            return
        self._refrescar(silencioso=True)
        self._estado(textos.t("m_perfil_borrado", n=visible))

    # ---------- herramientas ----------

    def _alternar_watchdog(self, *_ignorado) -> None:
        if self._tarea_watchdog is not None:
            self._tarea_watchdog.detener()
            self.btn_watchdog.setEnabled(False)
            self.lbl_watchdog_salida.setText(textos.t("m_deteniendo"))
            return
        if self._tarea_termico is not None:
            QMessageBox.warning(self, textos.t("t_termico"),
                                textos.t("p_wd_termico"))
            return
        umbral = int(self.combo_umbral.currentData() or self._umbral_watchdog)
        aviso = max(60, umbral - 5)
        extra = ["--gpu-warn", str(aviso), "--gpu-crit", str(umbral),
                 "--cpu-warn", str(aviso), "--cpu-crit", str(umbral)]
        tarea = servicios.Tarea("watchdog", extra, self)
        tarea.linea.connect(self._linea_watchdog)
        tarea.terminada.connect(self._fin_watchdog)
        self._tarea_watchdog = tarea
        self._watchdog_activo = True
        self.btn_watchdog.setText(textos.t("detener_watchdog"))
        self.combo_umbral.setEnabled(False)
        self.lbl_watchdog_salida.setText(textos.t("m_arrancando"))
        self._actualizar_resumen()
        tarea.start()
        self._estado(textos.t("m_wd_activo", u=umbral))

    def _cambiar_umbral(self, _indice: int) -> None:
        valor = self.combo_umbral.currentData()
        if not valor:
            return
        self._umbral_watchdog = int(valor)
        self._actualizar_resumen()
        self._estado(textos.t("m_umbral", u=self._umbral_watchdog))

    def _linea_watchdog(self, texto: str) -> None:
        limpio = servicios.limpiar_ansi(texto).strip()
        if not limpio:
            return
        self.lbl_watchdog_salida.setText(limpio)
        self.lbl_watchdog_salida.setToolTip(limpio)

    def _fin_watchdog(self, codigo: int) -> None:
        self._tarea_watchdog = None
        self._watchdog_activo = False
        if self._cerrando:
            return
        self.btn_watchdog.setEnabled(True)
        self.btn_watchdog.setText(textos.t("activar_watchdog"))
        self.combo_umbral.setEnabled(True)
        self.lbl_watchdog_salida.setText(
            textos.t("m_detenido") if codigo in (0, -1)
            else textos.t("m_codigo", c=codigo))
        self._actualizar_resumen()
        servicios.reparar_pendientes()
        self._estado(textos.t("m_wd_detenido"))
        self._sondear(False)

    # ---------- aviso térmico ----------

    def _poner_estado_termico(self, texto: str, objeto: str) -> None:
        insignia = getattr(self, "insignia_termico", None)
        if insignia is None:
            return
        insignia.setText(texto)
        self._poner_objeto(insignia, objeto)

    def _alternar_termico(self, *_ignorado) -> None:
        if self._tarea_termico is not None:
            self._termico_parando = True
            self._tarea_termico.detener()
            self.btn_termico.setEnabled(False)
            self.lbl_termico_salida.setText(textos.t("m_deteniendo"))
            return
        if self._tarea_watchdog is not None:
            QMessageBox.warning(self, textos.t("t_wd_activo"),
                                textos.t("p_termico_wd"))
            return
        umbral = int(self.combo_termico.currentData() or self._umbral_termico)
        extra = ["--cpu-umbral", str(umbral), "--gpu-umbral", str(umbral)]
        if self.btn_termico_frenar.isChecked():
            extra.append("--proteger")
        tarea = servicios.Tarea("aviso", extra, self)
        tarea.linea.connect(self._linea_termico)
        tarea.terminada.connect(self._fin_termico)
        self._tarea_termico = tarea
        self._termico_activo = True
        self._termico_parando = False
        self.btn_termico.setText(textos.t("termico_detener"))
        self.btn_termico_frenar.setEnabled(False)
        self.combo_termico.setEnabled(False)
        self.lbl_termico_salida.setText(textos.t("m_arrancando"))
        self._poner_estado_termico(textos.t("activo"), "InsigniaOk")
        tarea.start()
        self._estado(textos.t("m_termico_on", u=umbral))

    def _cambiar_umbral_termico(self, _indice: int) -> None:
        valor = self.combo_termico.currentData()
        if not valor:
            return
        self._umbral_termico = int(valor)
        _guardar_opcion("termico_umbral", self._umbral_termico)
        self._estado(textos.t("m_termico_limite", u=self._umbral_termico))

    def _linea_termico(self, texto: str) -> None:
        limpio = servicios.limpiar_ansi(texto).strip()
        if not limpio:
            return
        if limpio.startswith("@@NOTIF@@"):
            partes = limpio.split("@@NOTIF@@")
            titulo = partes[1] if len(partes) > 1 else ""
            cuerpo = partes[2] if len(partes) > 2 else (titulo or limpio)
            if "fren" in titulo.lower():
                self._poner_estado_termico(textos.t("termico_frenando"),
                                           "InsigniaPend")
            self._aviso_termico_gui(cuerpo)
            return
        try:
            self.lbl_termico_salida.setText(limpio)
            self.lbl_termico_salida.setToolTip(limpio)
        except RuntimeError:
            pass

    def _aviso_termico_gui(self, texto: str) -> None:
        """Aviso del backend en ventana propia, no modal: la herramienta sigue."""
        QApplication.beep()
        dlg = self._dlg_termico
        if dlg is None:
            dlg = QMessageBox(self)
            dlg.setIcon(QMessageBox.Warning)
            dlg.setStandardButtons(QMessageBox.Ok)
            dlg.setWindowTitle(textos.t("t_termico"))
            dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            dlg.finished.connect(self._cerrar_dlg_termico)
            self._dlg_termico = dlg
        dlg.setText(texto)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self._estado(texto)

    def _cerrar_dlg_termico(self, _codigo: int = 0) -> None:
        self._dlg_termico = None

    def _fin_termico(self, codigo: int) -> None:
        self._tarea_termico = None
        self._termico_activo = False
        parado = self._termico_parando
        self._termico_parando = False
        if self._cerrando:
            return
        self.btn_termico.setEnabled(True)
        self.btn_termico.setText(textos.t("termico_activar"))
        self.btn_termico_frenar.setEnabled(True)
        self.combo_termico.setEnabled(True)
        self._poner_estado_termico(textos.t("detenido"), "InsigniaNeutra")
        self.lbl_termico_salida.setText(
            textos.t("m_detenido") if parado or codigo in (0, -1)
            else textos.t("m_codigo", c=codigo))
        servicios.reparar_pendientes()
        self._estado(textos.t("m_termico_off"))

    # ---------- limpieza de RAM ----------

    def _actualizar_ram_libre(self) -> None:
        if self._cerrando or not hasattr(self, "lbl_ram_libre"):
            return
        libre = servicios.ram_libre()
        if libre is None:
            self.lbl_ram_libre.setText(textos.t("sin_dato"))
            return
        self.lbl_ram_libre.setText(textos.t("ram_libre_txt", v=f"{libre:.1f}"))
        insignia = getattr(self, "insignia_ram", None)
        if insignia is not None:
            insignia.setText(f"{libre:.1f} GB")

    def _limpiar_ram(self, profunda: bool) -> None:
        if self._hilo_ram is not None:
            return
        if servicios.memreduct() is None:
            self._estado(textos.t("m_ram_no"))
            return
        self.btn_ram.setEnabled(False)
        self.btn_ram_profunda.setEnabled(False)
        self.lbl_ram_salida.setText(textos.t("m_ram_limpiando"))
        hilo = self._crear_hilo(lambda: servicios.limpiar_ram(profunda))
        self._hilo_ram = hilo
        hilo.listo.connect(self._fin_ram)
        hilo.fallo.connect(self._fallo_ram)
        hilo.finished.connect(self._soltar_ram)
        hilo.start()

    def _fin_ram(self, resultado: dict) -> None:
        if self._cerrando:
            return
        texto = textos.t(
            "m_ram_resultado", v=f"{resultado['liberado']:+.2f}",
            a=f"{resultado['antes']:.1f}", b=f"{resultado['despues']:.1f}")
        self.lbl_ram_salida.setText(texto)
        self._estado(texto)
        self._actualizar_ram_libre()

    def _fallo_ram(self, mensaje: str) -> None:
        if self._cerrando:
            return
        texto = textos.t("m_ram_error", e=mensaje)
        self.lbl_ram_salida.setText(texto)
        self._estado(texto)

    def _soltar_ram(self) -> None:
        self._hilo_ram = None
        if self._cerrando:
            return
        hay = servicios.memreduct() is not None
        try:
            self.btn_ram.setEnabled(hay)
            self.btn_ram_profunda.setEnabled(hay)
        except RuntimeError:
            pass

    def _alternar_auto_ram(self, *_ignorado) -> None:
        self._ram_auto = bool(self.btn_ram_auto.isChecked())
        _guardar_opcion("ram_auto", self._ram_auto)
        if self._ram_auto:
            self.timer_ram.start(self._ram_intervalo * 60000)
            self._estado(textos.t("m_ram_auto_on", n=self._ram_intervalo))
        else:
            self.timer_ram.stop()
            self._estado(textos.t("m_ram_auto_off"))

    def _reconfigurar_auto_ram(self, *_ignorado) -> None:
        minutos = self.combo_ram_min.currentData()
        umbral = self.combo_ram_umbral.currentData()
        if minutos:
            self._ram_intervalo = int(minutos)
            _guardar_opcion("ram_intervalo", self._ram_intervalo)
        if umbral is not None:
            self._ram_umbral = float(umbral)
            _guardar_opcion("ram_umbral", self._ram_umbral)
        if self._ram_auto:
            self.timer_ram.start(self._ram_intervalo * 60000)

    def _restaurar_ram_auto(self) -> None:
        """Aplica el estado guardado a la tarjeta recién construida."""
        if not hasattr(self, "btn_ram_auto"):
            return
        self.btn_ram_auto.setChecked(self._ram_auto)
        if self._ram_auto and servicios.memreduct() is not None:
            self.timer_ram.start(self._ram_intervalo * 60000)
        else:
            self.timer_ram.stop()

    def _tick_ram(self) -> None:
        if not self._ram_auto or self._hilo_ram is not None:
            return
        libre = servicios.ram_libre()
        debe = (self._ram_umbral <= 0
                or (libre is not None and libre < self._ram_umbral))
        if debe:
            self._limpiar_ram(False)

    # ---------- procesos en segundo plano ----------

    def _analizar_procesos(self) -> None:
        self.btn_proc_analizar.setEnabled(False)
        self.lbl_procesos.setText(textos.t("m_proc_analizando"))
        hilo = self._crear_hilo(servicios.escanear_procesos)
        hilo.listo.connect(self._fin_analisis)
        hilo.fallo.connect(self._fallo_analisis)
        hilo.start()

    def _fallo_analisis(self, mensaje: str) -> None:
        self.btn_proc_analizar.setEnabled(True)
        self.lbl_procesos.setText(mensaje)

    def _fin_analisis(self, datos: dict) -> None:
        if self._cerrando:
            return
        self.btn_proc_analizar.setEnabled(True)
        self._procesos = datos.get("candidatos") or []
        self.lista_procesos.clear()
        for cand in self._procesos:
            item = QListWidgetItem(
                f"{cand['nombre']}  ·  {cand['mb']:.1f} MB  ·  "
                f"{textos.t(cand['motivo'])}")
            item.setData(Qt.UserRole, cand["pid"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.lista_procesos.addItem(item)
        self.btn_proc_cerrar.setEnabled(bool(self._procesos))
        if self._procesos:
            self.lbl_procesos.setText(textos.t(
                "proc_resumen", n=len(self._procesos),
                mb=f"{datos.get('total_mb', 0):.1f}",
                p=datos.get("protegidos", 0)))
            self.insignia_procesos.setText(str(len(self._procesos)))
            self._poner_objeto(self.insignia_procesos, "InsigniaPend")
        else:
            self.lbl_procesos.setText(textos.t("proc_ninguno"))
            self.insignia_procesos.setText(textos.t("listo"))
            self._poner_objeto(self.insignia_procesos, "InsigniaOk")

    def _cerrar_seleccionados(self) -> None:
        pids: list[int] = []
        for i in range(self.lista_procesos.count()):
            item = self.lista_procesos.item(i)
            if item.checkState() == Qt.Checked:
                pids.append(item.data(Qt.UserRole))
        if not pids:
            self._estado(textos.t("m_proc_nada"))
            return
        total = sum(c["mb"] for c in self._procesos if c["pid"] in pids)
        respuesta = QMessageBox.question(
            self, textos.t("herr_procesos"),
            textos.t("p_proc_confirmar", n=len(pids), mb=f"{total:.1f}"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if respuesta != QMessageBox.Yes:
            return
        self.btn_proc_cerrar.setEnabled(False)
        hilo = self._crear_hilo(lambda: servicios.cerrar_procesos(pids))
        hilo.listo.connect(self._fin_cierre)
        hilo.fallo.connect(lambda e: self._estado(str(e)))
        hilo.start()

    def _fin_cierre(self, resultado: dict) -> None:
        if self._cerrando:
            return
        cerrados = resultado.get("cerrados") or []
        self._estado(textos.t("m_proc_cerrados", n=len(cerrados)))
        if cerrados:
            QTimer.singleShot(600, self._analizar_procesos)
        else:
            self._analizar_procesos()

    # ---------- recomendaciones ----------

    def _analizar_equipo(self) -> None:
        if self._hilo_equipo is not None and self._hilo_equipo.isRunning():
            return
        self.btn_reco_analizar.setEnabled(False)
        self.insignia_equipo.setText(textos.t("rec_leyendo"))
        self._poner_objeto(self.insignia_equipo, "InsigniaPend")
        hilo = self._crear_hilo(servicios.info_equipo)
        self._hilo_equipo = hilo
        hilo.listo.connect(self._fin_equipo)
        hilo.fallo.connect(self._fallo_equipo)
        hilo.start()

    def _fallo_equipo(self, mensaje: str) -> None:
        self._hilo_equipo = None
        self.btn_reco_analizar.setEnabled(True)
        self.insignia_equipo.setText(textos.t("rec_leyendo"))
        self._poner_objeto(self.insignia_equipo, "InsigniaNeutra")
        self.lbl_reco_motivo.setText(textos.t("m_reco_fallo", e=mensaje))

    def _fin_equipo(self, datos: dict) -> None:
        self._hilo_equipo = None
        if self._cerrando:
            return
        self._equipo = datos
        self._reco = servicios.recomendar_perfil(datos, self.perfiles, self.certs)
        self.btn_reco_analizar.setEnabled(True)
        self._pintar_equipo()
        self._pintar_reco()
        self._pintar_bat()

    def _pintar_equipo(self) -> None:
        if not hasattr(self, "lbl_tipo"):
            return
        datos = self._equipo or {}
        tipo = datos.get("tipo", "torre")
        self.lbl_tipo.setText(textos.t(f"rec_tipo_{tipo}"))
        self.lbl_modelo.setText(datos.get("modelo") or "")
        self.lbl_fuente.setText(textos.t(
            "rec_fuente_wmi" if datos.get("fuente") == "wmi"
            else "rec_fuente_est"))
        gpu = datos.get("gpu") or textos.t("rec_sin_gpu")
        self.lbl_dato_cpu.setText(textos.t(
            "rec_nucleos", cpu=datos.get("cpu") or "—",
            n=datos.get("nucleos") or 0, h=datos.get("hilos") or 0))
        self.lbl_dato_ram.setText(f"{float(datos.get('ram_total') or 0):g} GB")
        self.lbl_dato_gpu.setText(" · ".join(
            parte for parte in gpu.split("/") if parte.strip()))
        if datos.get("bateria"):
            estado = textos.t("rec_enchufado" if datos.get("enchufado")
                              else "rec_descargando")
            self.lbl_dato_energia.setText(f"{datos.get('pct')} % · {estado}")
        else:
            self.lbl_dato_energia.setText(textos.t("rec_sin_bateria"))
        self.lbl_dato_win.setText(
            f"{datos.get('win') or 'Windows'} ({datos.get('build') or '—'})")
        self.insignia_equipo.setText(textos.t("listo"))
        self._poner_objeto(self.insignia_equipo, "InsigniaOk")

    def _pintar_reco(self) -> None:
        if not self._reco or not hasattr(self, "lbl_reco_nombre"):
            return
        reco = self._reco
        tipo = textos.t(f"rec_tipo_{(self._equipo or {}).get('tipo', 'torre')}")
        cfg = reco.get("cfg") or {}
        pct = int(cfg.get("cpu_max", 100) or 100)
        plan = servicios.nombre_plan(cfg.get("plan", "")) or str(
            cfg.get("plan", ""))
        if reco.get("crear"):
            nombre = textos.t("rec_auto_nombre", tipo=tipo)
            self.lbl_reco_motivo.setText(textos.t("rec_nota_nuevo"))
            etiqueta, objeto = textos.t("rec_nuevo"), "InsigniaPend"
        else:
            nombre = reco.get("nombre", "")
            self.lbl_reco_motivo.setText(textos.t(
                "rec_motivo", tipo=tipo, plan=plan, pct=pct))
            etiqueta, objeto = textos.t("rec_existente"), "InsigniaOk"
        self.lbl_reco_nombre.setText(nombre)
        self.lbl_reco_plan.setText(textos.t("rec_plan", plan=plan, pct=pct))
        self.insignia_reco.setText(etiqueta)
        self._poner_objeto(self.insignia_reco, objeto)
        self.btn_reco_crear.setEnabled(bool(reco.get("crear")))
        self.btn_reco_aplicar.setEnabled(True)

    def _crear_reco(self) -> str | None:
        if not self._reco:
            return None
        tipo = textos.t(f"rec_tipo_{(self._equipo or {}).get('tipo', 'torre')}")
        nombre = textos.t("rec_auto_nombre", tipo=tipo)
        base, n = nombre, 2
        while nombre in self.perfiles:
            nombre = f"{base} {n}"
            n += 1
        cfg = dict(self._reco.get("cfg") or {})
        cfg["nota"] = textos.t("rec_auto_nota", tipo=tipo,
                               ram=f"{float((self._equipo or {}).get('ram_total') or 0):g}")
        try:
            servicios.guardar_perfil(nombre, cfg, es_nuevo=True)
        except Exception as e:
            self._estado(f"{textos.t('rec_crear')}: {e}")
            return None
        self.perfiles, self.certs, self.aviso = cargar_perfiles()
        self._pintar_lista()
        if self._equipo:
            self._reco = servicios.recomendar_perfil(self._equipo, self.perfiles,
                                                     self.certs)
            self._pintar_reco()
        self._estado(textos.t("m_reco_creado", n=nombre))
        return nombre

    def _aplicar_reco(self) -> None:
        if not self._reco:
            return
        nombre = self._reco.get("nombre", "")
        if self._reco.get("crear"):
            nombre = self._crear_reco() or ""
            if not nombre:
                return
        cfg = self.perfiles.get(nombre) or (self._reco.get("cfg") or {})
        self.btn_reco_aplicar.setEnabled(False)
        hilo = self._crear_hilo(lambda: servicios.aplicar_perfil(nombre, cfg))
        hilo.listo.connect(lambda r, n=nombre: self._fin_aplicar_reco(n, r))
        hilo.fallo.connect(self._fallo_aplicar_reco)
        hilo.start()

    def _fallo_aplicar_reco(self, mensaje: str) -> None:
        self.btn_reco_aplicar.setEnabled(True)
        self._estado(mensaje)

    def _fin_aplicar_reco(self, nombre: str, resultado: dict) -> None:
        if self._cerrando:
            return
        self.btn_reco_aplicar.setEnabled(True)
        self._plan_activo = resultado.get("plan") or self._plan_activo
        self._actualizar_resumen()
        self._estado(textos.t("m_reco_aplicado", n=nombre,
                              plan=self._plan_activo,
                              pct=resultado.get("pct", 100)))

    # ---------- centro de control ----------

    def _leer_programadas(self) -> list[dict]:
        crudo = _leer_opcion("programadas", [])
        salida: list[dict] = []
        for p in crudo if isinstance(crudo, list) else []:
            if not isinstance(p, dict):
                continue
            if p.get("accion") not in ("ram", "ram_full", "diag", "bat",
                                       "normal"):
                continue
            try:
                cuando = datetime.fromisoformat(str(p.get("iso") or ""))
            except ValueError:
                continue
            salida.append({
                "id": str(p.get("id") or int(cuando.timestamp())),
                "accion": p["accion"],
                "iso": cuando.replace(second=0, microsecond=0).isoformat(
                    timespec="minutes"),
                "diaria": bool(p.get("diaria")),
                "hecha": str(p.get("hecha") or ""),
                "perdida": bool(p.get("perdida")),
            })
        return salida

    def _guardar_programadas(self) -> None:
        _guardar_opcion("programadas", self._programadas)

    def _accion_texto(self, clave: str) -> str:
        return textos.t(f"cen_acc_{clave}")

    def _pintar_programadas(self) -> None:
        if not hasattr(self, "lista_prog"):
            return
        self.lista_prog.clear()
        for p in self._programadas:
            try:
                cuando = datetime.fromisoformat(p["iso"])
            except ValueError:
                continue
            texto = f"{cuando:%H:%M} · {self._accion_texto(p['accion'])}"
            if p.get("diaria"):
                texto = (f"{cuando:%H:%M} · {self._accion_texto(p['accion'])}"
                         f" · {textos.t('cen_cada_dia')}")
            else:
                texto = (f"{cuando:%d/%m/%Y %H:%M} · "
                         f"{self._accion_texto(p['accion'])}")
            if p.get("perdida"):
                texto += f" · {textos.t('cen_perdida')}"
            item = QListWidgetItem(texto)
            item.setData(Qt.UserRole, p["id"])
            self.lista_prog.addItem(item)
        total = len(self._programadas)
        self.insignia_prog.setText(str(total))
        self._poner_objeto(self.insignia_prog,
                           "InsigniaOk" if total else "InsigniaNeutra")
        self.lbl_prog.setText("" if total else textos.t("cen_nada"))
        self.btn_quitar_prog.setEnabled(bool(total))

    def _diaria_cambiada(self, activa: bool) -> None:
        if not hasattr(self, "fecha_prog"):
            return
        if activa:
            self.fecha_prog.setDate(QDate.currentDate())
            self.fecha_prog.setDisplayFormat("HH:mm")
        else:
            self.fecha_prog.setDisplayFormat("dd/MM/yyyy HH:mm")

    def _programar(self) -> None:
        accion = self.combo_accion.currentData()
        cuando = self.fecha_prog.dateTime().toPython()
        diaria = self.chk_diaria.isChecked()
        ahora = datetime.now()
        if diaria:
            cuando = ahora.replace(hour=cuando.hour, minute=cuando.minute,
                                   second=0, microsecond=0)
        elif cuando <= ahora + timedelta(seconds=30):
            self._estado(textos.t("m_cen_fecha"))
            return
        self._programadas.append({
            "id": str(time.time_ns()),
            "accion": accion,
            "iso": cuando.isoformat(timespec="minutes"),
            "diaria": diaria,
            "hecha": "",
            "perdida": False,
        })
        self._guardar_programadas()
        self._pintar_programadas()
        self._estado(textos.t("m_cen_guardada"))

    def _quitar_programada(self) -> None:
        item = self.lista_prog.currentItem()
        if item is None:
            return
        ident = item.data(Qt.UserRole)
        self._programadas = [p for p in self._programadas
                             if p.get("id") != ident]
        self._guardar_programadas()
        self._pintar_programadas()
        self._estado(textos.t("m_cen_borrada"))

    def _ejecutar_programada(self, p: dict) -> None:
        accion = p.get("accion")
        self._estado(textos.t("m_cen_ejecutada",
                              accion=self._accion_texto(accion)))
        if accion in ("ram", "ram_full"):
            self._limpiar_ram(accion == "ram_full")
        elif accion == "diag":
            self._autodiagnostico()
        elif accion == "bat":
            self._optimizar_bateria()
        elif accion == "normal":
            self._volver_normal()

    def _revisar_programadas(self) -> None:
        if self._cerrando or not self._programadas:
            return
        ahora = datetime.now()
        hoy = ahora.strftime("%Y-%m-%d")
        cambios = False
        for p in list(self._programadas):
            if p.get("perdida"):
                continue
            try:
                cuando = datetime.fromisoformat(p.get("iso", ""))
            except ValueError:
                self._programadas.remove(p)
                cambios = True
                continue
            if p.get("diaria"):
                if p.get("hecha") == hoy:
                    continue
                objetivo = ahora.replace(hour=cuando.hour, minute=cuando.minute,
                                         second=0, microsecond=0)
                if objetivo <= ahora <= objetivo + timedelta(minutes=5):
                    p["hecha"] = hoy
                    self._ejecutar_programada(p)
                    cambios = True
            elif cuando <= ahora <= cuando + timedelta(minutes=5):
                self._ejecutar_programada(p)
                self._programadas.remove(p)
                cambios = True
            elif ahora > cuando + timedelta(minutes=5):
                p["perdida"] = True
                cambios = True
        if cambios:
            self._guardar_programadas()
            self._pintar_programadas()

    def _autodiagnostico(self) -> None:
        if self._diag_activo:
            return
        self._diag_activo = True
        self.btn_diag.setEnabled(False)
        self.lbl_diag.setText(textos.t("cen_analizando"))
        self.insignia_diag.setText(textos.t("cen_analizando"))
        self._poner_objeto(self.insignia_diag, "InsigniaPend")
        hilo = self._crear_hilo(servicios.leer_rendimiento)
        hilo.listo.connect(self._fin_diag)
        hilo.fallo.connect(self._fallo_diag)
        hilo.start()

    def _fallo_diag(self, mensaje: str) -> None:
        self._diag_activo = False
        self.btn_diag.setEnabled(True)
        self.lbl_diag.setText(mensaje)
        self.insignia_diag.setText(textos.t("listo"))
        self._poner_objeto(self.insignia_diag, "InsigniaNeutra")

    def _fin_diag(self, datos: dict) -> None:
        if self._cerrando:
            return
        self._diag_activo = False
        self.btn_diag.setEnabled(True)
        lineas: list[str] = []
        avisos = 0
        lhm = bool(self._sensores.get("lhm"))
        if not lhm:
            avisos += 1
        lineas.append(
            f"{'[OK]' if lhm else '[!]'} {textos.t('sensor')}: "
            f"{textos.t('s_lhm_conectado') if lhm else textos.t('s_lhm_sin')}")
        lineas.append(f"[OK] {textos.t('plan_energia')}: "
                      f"{self._plan_activo or '—'}")

        ram_pct = float(datos.get("ram_pct") or 0)
        total = float(datos.get("ram_total") or 0)
        usada = float(datos.get("ram_usada") or 0)
        if ram_pct >= 90:
            avisos += 1
        lineas.append(
            f"{'[!]' if ram_pct >= 90 else '[OK]'} " +
            textos.t("diag_ram", libre=f"{max(0.0, total - usada):.1f}",
                     total=f"{total:.1f}", pct=round(ram_pct)))

        disco_pct = float(datos.get("rom_pct") or 0)
        disco_total = float(datos.get("rom_total") or 0)
        disco_usado = float(datos.get("rom_usada") or 0)
        if disco_pct >= 92:
            avisos += 1
        lineas.append(
            f"{'[!]' if disco_pct >= 92 else '[OK]'} " +
            textos.t("diag_disco", u=datos.get("rom_unidad") or "C:",
                     libre=f"{max(0.0, disco_total - disco_usado):.1f}",
                     pct=round(disco_pct)))

        cpu_t = datos.get("cpu_temp")
        gpu_t = datos.get("gpu")
        caliente = (cpu_t is not None and cpu_t >= 90)
        if caliente:
            avisos += 1
        lineas.append(
            f"{'[!]' if caliente else '[OK]'} " +
            textos.t("diag_temps",
                     cpu=f"{cpu_t:.0f}" if cpu_t is not None else "—",
                     gpu=f"{gpu_t:.0f}" if gpu_t is not None else "—"))

        equipo = self._equipo or {}
        if equipo.get("bateria"):
            estado = textos.t("rec_enchufado" if equipo.get("enchufado")
                              else "rec_descargando")
            lineas.append("[OK] " + textos.t("diag_bat",
                                             pct=equipo.get("pct"),
                                             estado=estado))
        else:
            lineas.append("[OK] " + textos.t("cen_bat_sin"))

        guardas = int(self._watchdog_activo) + int(self._termico_activo)
        lineas.append("[OK] " + (textos.t("diag_guardas_on", n=guardas)
                                 if guardas else
                                 textos.t("diag_guardas_off")))
        lineas.append("[OK] " + (textos.t("diag_ram_auto_on")
                                 if self._ram_auto else
                                 textos.t("diag_ram_auto_off")))
        certs = sum(1 for v in self.certs.values()
                    if isinstance(v, dict) and v.get("certificado"))
        lineas.append("[OK] " + textos.t("diag_certs", n=certs))

        self.lbl_diag.setText("\n".join(lineas))
        self.insignia_diag.setText(
            textos.t("listo") if not avisos else str(avisos))
        self._poner_objeto(self.insignia_diag,
                           "InsigniaOk" if not avisos else "InsigniaPend")

    def _pintar_bat(self) -> None:
        if not hasattr(self, "lbl_bat"):
            return
        equipo = self._equipo
        if not equipo:
            self.lbl_bat.setText(textos.t("rec_leyendo"))
            return
        if not equipo.get("bateria"):
            self.lbl_bat.setText(textos.t("cen_bat_sin"))
            self.btn_bat.setEnabled(False)
            self.insignia_bat.setText(textos.t("cen_bat_sin"))
            self._poner_objeto(self.insignia_bat, "InsigniaNeutra")
            return
        estado = textos.t("rec_enchufado" if equipo.get("enchufado")
                          else "rec_descargando")
        self.lbl_bat.setText(f"{equipo.get('pct')} % · {estado}")
        self.btn_bat.setEnabled(True)
        self.insignia_bat.setText(f"{equipo.get('pct')} %")
        self._poner_objeto(self.insignia_bat, "InsigniaOk")

    def _optimizar_bateria(self) -> None:
        if self._equipo and not self._equipo.get("bateria"):
            self._estado(textos.t("cen_bat_sin"))
            return
        self.btn_bat.setEnabled(False)
        hilo = self._crear_hilo(servicios.optimizar_bateria)
        hilo.listo.connect(lambda r: self._fin_modo(r, True))
        hilo.fallo.connect(self._fallo_modo)
        hilo.start()

    def _volver_normal(self) -> None:
        self.btn_normal.setEnabled(False)
        hilo = self._crear_hilo(servicios.modo_normal)
        hilo.listo.connect(lambda r: self._fin_modo(r, False))
        hilo.fallo.connect(self._fallo_modo)
        hilo.start()

    def _fallo_modo(self, mensaje: str) -> None:
        self.btn_bat.setEnabled(True)
        self.btn_normal.setEnabled(True)
        self._estado(mensaje)

    def _fin_modo(self, resultado: dict, bateria: bool) -> None:
        if self._cerrando:
            return
        self.btn_bat.setEnabled(True)
        self.btn_normal.setEnabled(True)
        self._plan_activo = resultado.get("plan") or self._plan_activo
        self._actualizar_resumen()
        self._estado(textos.t("m_bat_ok" if bateria else "m_normal_ok",
                              plan=self._plan_activo,
                              pct=resultado.get("pct", 100)))

    def _sondear(self, completo: bool = False) -> None:
        if self._cerrando:
            return
        hilo = self._crear_hilo(lambda: servicios.leer_estado(completo))
        hilo.listo.connect(self._estado_sensores)
        hilo.fallo.connect(self._fallo_sensores)
        hilo.start()

    def _crear_hilo(self, fn) -> servicios.Hilo:
        hilo = servicios.Hilo(fn, self)
        self._hilos.add(hilo)
        hilo.finished.connect(lambda h=hilo: self._hilos.discard(h))
        return hilo

    def _probar_sensor(self) -> None:
        self.btn_probar.setEnabled(False)
        self.lbl_sensor.setText(textos.t("m_leyendo_sensores"))
        hilo = self._crear_hilo(lambda: servicios.leer_estado(completo=True))
        hilo.listo.connect(self._estado_sensores)
        hilo.fallo.connect(self._fallo_sensores)
        hilo.finished.connect(lambda: self.btn_probar.setEnabled(True))
        hilo.start()

    def _estado_sensores(self, datos: dict) -> None:
        if self._cerrando:
            return
        self._sensores = datos
        planes = datos.get("planes") or []
        primeros = not self._planes
        if planes:
            self._planes = planes
        if datos.get("plan"):
            self._plan_activo = self._nombre_plan(datos.get("plan"))
        if hasattr(self, "combo_plan"):
            self._llenar_planes(planes or self._planes, datos.get("plan_guid"))
        if hasattr(self, "lbl_sensor"):
            self.lbl_sensor.setText(self._texto_sensores(datos))
        if hasattr(self, "lbl_plan_herr") and self._plan_activo:
            self.lbl_plan_herr.setText(
                textos.t("plan_activo", p=self._plan_activo))
        self._actualizar_resumen()
        desplegado = any(panel.isVisible() for _, panel, _ in self.expansores)
        if primeros and planes and not desplegado:
            self._pintar_lista()

    # ---------- lecturas en vivo (pestaña Estado) ----------

    def _muestrear_vivo(self) -> None:
        if self._cerrando or self._vivo_pausado or self._hilo_vivo is not None:
            return
        hilo = self._crear_hilo(servicios.leer_rendimiento)
        self._hilo_vivo = hilo
        hilo.listo.connect(self._pintar_vivo)
        hilo.fallo.connect(self._fallo_vivo)
        hilo.finished.connect(self._fin_vivo)
        hilo.start()

    def _fin_vivo(self) -> None:
        self._hilo_vivo = None

    def _fallo_vivo(self, mensaje: str) -> None:
        if hasattr(self, "lbl_vivo") and not self._cerrando:
            self.lbl_vivo.setText(mensaje)

    @staticmethod
    def _txt_temp(valor) -> str:
        return f"{valor:.1f} °C" if valor is not None else textos.t("sin_dato")

    @staticmethod
    def _txt_mhz(valor) -> str:
        return f"{round(valor)} MHz" if valor is not None else "—"

    @staticmethod
    def _txt_gb(valor) -> str:
        return f"{valor:.1f} GB" if valor is not None else "—"

    def _tintar(self, etiqueta: QLabel, valor, alta: float,
                critica: float) -> None:
        t = TEMAS[self.tema]
        if valor is not None and valor >= critica:
            etiqueta.setStyleSheet(f"color: {t['rojo']};")
        elif valor is not None and valor >= alta:
            etiqueta.setStyleSheet(f"color: {t['aviso']};")
        else:
            etiqueta.setStyleSheet("")

    @staticmethod
    def _poner_barra(etiqueta: QLabel, barra: QProgressBar, valor) -> None:
        if valor is None:
            etiqueta.setText("—")
            barra.setValue(0)
        else:
            etiqueta.setText(f"{valor:.0f} %")
            barra.setValue(round(valor))

    def _pintar_vivo(self, datos: dict) -> None:
        if self._cerrando or not hasattr(self, "lbl_cpu_temp"):
            return
        self._ultimo_vivo = dict(datos)

        temp = datos.get("cpu_temp")
        self.lbl_cpu_temp.setText(self._txt_temp(temp))
        self._tintar(self.lbl_cpu_temp, temp, 90, 95)
        fuente = datos.get("cpu_fuente")
        if fuente == "lhm":
            self.insignia_cpu.setText("LHM")
            self._poner_objeto(self.insignia_cpu, "InsigniaOk")
        elif fuente == "acpi":
            self.insignia_cpu.setText("ACPI")
            self._poner_objeto(self.insignia_cpu, "Insignia")
        else:
            self.insignia_cpu.setText(textos.t("sin_lhm"))
            self._poner_objeto(self.insignia_cpu, "InsigniaPend")
        self._poner_barra(self.lbl_cpu_uso, self.bar_cpu_uso,
                          datos.get("cpu_uso"))
        self.lbl_cpu_reloj.setText(self._txt_mhz(datos.get("cpu_reloj")))

        temp_gpu = datos.get("gpu")
        self.lbl_gpu_temp.setText(self._txt_temp(temp_gpu))
        self._tintar(self.lbl_gpu_temp, temp_gpu, 85, 92)
        if temp_gpu is not None:
            self.insignia_gpu.setText("NVML")
            self._poner_objeto(self.insignia_gpu, "InsigniaOk")
        else:
            self.insignia_gpu.setText("—")
            self._poner_objeto(self.insignia_gpu, "InsigniaPend")
        self._poner_barra(self.lbl_gpu_uso, self.bar_gpu_uso,
                          datos.get("gpu_uso"))
        potencia = datos.get("gpu_w")
        self.lbl_gpu_w.setText(f"{potencia:.1f} W" if potencia is not None else "—")
        self.lbl_gpu_reloj.setText(self._txt_mhz(datos.get("gpu_reloj")))
        nombre = datos.get("gpu_nombre") or ""
        self.lbl_gpu_nombre.setText(nombre)
        self.lbl_gpu_nombre.setVisible(bool(nombre))

        self._pintar_memoria(self.lbl_ram_pct, self.bar_ram,
                             self.lbl_ram_usada, self.lbl_ram_total, datos, "ram")
        self._pintar_memoria(self.lbl_rom_pct, self.bar_rom,
                             self.lbl_rom_usada, self.lbl_rom_total, datos, "rom")
        unidad = datos.get("rom_unidad") or "C:"
        self.lbl_rom_unidad.setText(textos.t("disco_sistema", unidad=unidad))

    def _pintar_memoria(self, pct_lbl: QLabel, barra: QProgressBar,
                        usada_lbl: QLabel, total_lbl: QLabel,
                        datos: dict, prefijo: str) -> None:
        pct = datos.get(f"{prefijo}_pct")
        if pct is None:
            pct_lbl.setText("—")
            barra.setValue(0)
        else:
            pct_lbl.setText(f"{pct:.0f} %")
            barra.setValue(round(pct))
        usada_lbl.setText(self._txt_gb(datos.get(f"{prefijo}_usada")))
        total_lbl.setText(self._txt_gb(datos.get(f"{prefijo}_total")))

    def _alternar_pausa(self) -> None:
        self._vivo_pausado = not self._vivo_pausado
        if self._vivo_pausado:
            self.timer_vivo.stop()
            self.btn_pausa.setText(textos.t("reanudar"))
            self.lbl_vivo.setText(textos.t("pausado"))
            return
        self.btn_pausa.setText(textos.t("pausar"))
        self.lbl_vivo.setText(textos.t("en_vivo"))
        self.timer_vivo.start()
        self._muestrear_vivo()

    def _cambiar_sondeo(self, _indice: int) -> None:
        valor = self.combo_sondeo.currentData()
        if not valor:
            return
        self._sondeo = int(valor)
        _guardar_opcion("estado_sondeo", self._sondeo)
        # Qt reinicia la cuenta al cambiar el intervalo.
        self.timer_vivo.setInterval(self._sondeo * 1000)
        self._estado(textos.t("m_sondeo", n=self._sondeo))

    def _fallo_sensores(self, mensaje: str) -> None:
        if hasattr(self, "lbl_sensor"):
            self.lbl_sensor.setText(textos.t("m_fallo_sensores", m=mensaje))

    @staticmethod
    def _texto_sensores(datos: dict) -> str:
        lineas = []
        if datos.get("lhm"):
            cpu = datos.get("cpu")
            lineas.append(textos.t("s_lhm_ok_cpu", c=f"{cpu:.1f}")
                          if cpu is not None else textos.t("s_lhm_ok_sin"))
        else:
            lineas.append(textos.t("s_lhm_fallo"))
            if datos.get("cpu_acpi") is not None:
                lineas.append(textos.t("s_acpi", c=f"{datos['cpu_acpi']:.1f}"))
        if datos.get("gpu") is not None:
            lineas.append(textos.t(
                "s_gpu_linea", n=datos.get("gpu_nombre") or "GPU",
                c=datos["gpu"]))
        else:
            lineas.append(textos.t("s_gpu_off"))
        return "\n".join(lineas)

    def _llenar_planes(self, planes: list[dict], activo_guid: str | None = None) -> None:
        if not planes:
            return
        self.combo_plan.blockSignals(True)
        self.combo_plan.clear()
        for p in planes:
            en_uso = bool(p.get("activo")) or bool(
                activo_guid and p["guid"].lower() == activo_guid.lower())
            texto = self._plan_local(p) + ("  ·  " + textos.t("en_uso") if en_uso else "")
            self.combo_plan.addItem(texto, p["guid"])
            if en_uso:
                self.combo_plan.setCurrentIndex(self.combo_plan.count() - 1)
        self.combo_plan.blockSignals(False)

    def _aplicar_plan(self) -> None:
        guid = self.combo_plan.currentData()
        if not guid:
            self._estado(textos.t("m_sin_planes"))
            return
        self.btn_aplicar_plan.setEnabled(False)
        self._estado(textos.t("m_aplicando_plan"))
        hilo = self._crear_hilo(lambda: servicios.aplicar_plan(guid))
        hilo.listo.connect(self._plan_aplicado)
        hilo.fallo.connect(self._fallo_plan)
        hilo.finished.connect(lambda: self.btn_aplicar_plan.setEnabled(True))
        hilo.start()

    def _plan_aplicado(self, resultado: dict) -> None:
        if resultado.get("planes"):
            self._planes = resultado["planes"]
            self._llenar_planes(resultado["planes"], resultado.get("guid"))
        nombre = self._nombre_plan(
            resultado.get("guid") or resultado.get("nombre"))
        self._plan_activo = "" if nombre == "—" else nombre
        if hasattr(self, "lbl_plan_herr"):
            self.lbl_plan_herr.setText(textos.t(
                "plan_activo",
                p=self._plan_activo or textos.t("sin_cambio")))
        self._actualizar_resumen()
        self._estado(textos.t(
            "m_plan_aplicado", p=self._plan_activo or textos.t("sin_cambio")))

    def _fallo_plan(self, mensaje: str) -> None:
        QMessageBox.warning(self, textos.t("t_no_plan"), mensaje)
        self._estado(textos.t("m_no_plan"))

    def _cambiar_estilo(self, nombre: str) -> None:
        if not nombre:
            return
        try:
            activo = servicios.fijar_estilo(nombre)
        except Exception as e:
            QMessageBox.warning(self, textos.t("t_no_estilo"), str(e))
            return
        self._estado(textos.t("m_estilo", e=activo))

    def _fijar_posicion(self, ancla: str) -> None:
        try:
            servicios.fijar_posicion_overlay(ancla)
        except Exception as e:
            QMessageBox.warning(self, textos.t("t_no_estilo"), str(e))
            return
        self._estado(textos.t("m_pos_overlay"))

    def _abrir_overlay(self) -> None:
        estilo = self.combo_estilo.currentText()
        extra = ["--estilo", estilo] if estilo else []
        if self.btn_fps_admin.isChecked():
            try:
                lanzada = servicios.abrir_herramienta_admin("sesion", extra)
            except Exception as e:
                QMessageBox.critical(self, textos.t("t_no_sesion"), str(e))
                return
            if not lanzada:
                self._estado(textos.t("m_admin_cancelado"))
                return
            self._estado(textos.t("m_sesion_admin"))
            return
        try:
            servicios.abrir_herramienta("sesion", extra)
        except Exception as e:
            QMessageBox.critical(self, textos.t("t_no_sesion"), str(e))
            return
        self._estado(textos.t("m_sesion"))

    # ---------- resumen ----------

    @staticmethod
    def _poner_objeto(widget: QLabel, nombre: str) -> None:
        if widget.objectName() == nombre:
            return
        widget.setObjectName(nombre)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _actualizar_resumen(self) -> None:
        activo = self._watchdog_activo
        for insignia in (getattr(self, "insignia_watchdog", None),
                         getattr(self, "insignia_watchdog_herr", None)):
            if insignia is None:
                continue
            insignia.setText(textos.t("activo") if activo
                             else textos.t("detenido"))
            self._poner_objeto(insignia,
                               "InsigniaOk" if activo else "InsigniaNeutra")
        if hasattr(self, "lbl_umbral_resumen"):
            self.lbl_umbral_resumen.setText(f"{self._umbral_watchdog} °C")
        if hasattr(self, "lbl_sensor_resumen"):
            if not self._sensores:
                texto = textos.t("comprobando")
            elif self._sensores.get("lhm"):
                texto = textos.t("s_lhm_conectado")
            else:
                texto = textos.t("s_lhm_sin")
            self.lbl_sensor_resumen.setText(texto)
        if hasattr(self, "lbl_plan_resumen"):
            self.lbl_plan_resumen.setText(self._plan_activo or textos.t("leyendo"))
        insignia_sensor = getattr(self, "insignia_sensor", None)
        if insignia_sensor is not None:
            if not self._sensores:
                insignia_sensor.setText(textos.t("comprobando"))
                self._poner_objeto(insignia_sensor, "InsigniaNeutra")
            elif self._sensores.get("lhm") or self._sensores.get("gpu") is not None:
                insignia_sensor.setText(textos.t("s_conectado"))
                self._poner_objeto(insignia_sensor, "InsigniaOk")
            else:
                insignia_sensor.setText(textos.t("s_sin_sensores"))
                self._poner_objeto(insignia_sensor, "InsigniaPend")

    def closeEvent(self, evento) -> None:
        self._cerrando = True
        self.timer_vivo.stop()
        self.timer_ram.stop()
        self.timer_prog.stop()
        if hasattr(self, "timer_archivos"):
            self.timer_archivos.stop()
        if self._tarea_watchdog is not None:
            self._tarea_watchdog.detener()
            self._tarea_watchdog.wait(5000)
            self._tarea_watchdog = None
        if self._tarea_termico is not None:
            self._tarea_termico.detener()
            self._tarea_termico.wait(5000)
            self._tarea_termico = None
        for hilo in list(self._hilos):
            if not hilo.wait(4000):
                hilo.terminate()
                hilo.wait(1000)
        servicios.reparar_pendientes()
        super().closeEvent(evento)

    # ---------- apariencia ----------

    def _crear_muestra(self, color: str, nombre: str
                       ) -> tuple[QFrame, QPushButton]:
        caja = QFrame()
        caja.setObjectName("AnilloMuestra")
        caja.setFixedSize(38, 38)
        capa = QVBoxLayout(caja)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.setAlignment(Qt.AlignCenter)
        boton = QPushButton()
        boton.setFixedSize(28, 28)
        boton.setCursor(Qt.PointingHandCursor)
        boton.setToolTip(nombre)
        boton.setStyleSheet(
            f"QPushButton {{ background: {color}; border: none; border-radius: 14px; }}"
        )
        boton.clicked.connect(lambda _=False, c=color: self._cambiar_acento(c))
        capa.addWidget(boton)
        self.muestras.append((boton, caja, color))
        return caja, boton

    def _crear_par_tema(self, contenedor) -> None:
        caja = QWidget()
        fila = QHBoxLayout(caja)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(7)
        grupo = QButtonGroup(self)
        grupo.setExclusive(True)
        claro = self._pildora(textos.t("tema_claro"), rol="secundario",
                              alto=32, ancho=82)
        oscuro = self._pildora(textos.t("tema_oscuro"), rol="secundario",
                               alto=32, ancho=82)
        for b in (claro, oscuro):
            b.setCheckable(True)
            grupo.addButton(b)
        claro.clicked.connect(lambda: self._fijar_tema("claro"))
        oscuro.clicked.connect(lambda: self._fijar_tema("oscuro"))
        fila.addWidget(claro)
        fila.addWidget(oscuro)
        contenedor.addWidget(caja)
        self.pares_tema.append((claro, oscuro))

    def _fijar_tema(self, tema: str) -> None:
        if tema not in TEMAS:
            return
        self.tema = tema
        self._aplicar_estilo()
        self._pintar_muestras()
        self._sincronizar_tema()
        self._refrescar_muestra()

    def _sincronizar_tema(self) -> None:
        for claro, oscuro in self.pares_tema:
            claro.setChecked(self.tema == "claro")
            oscuro.setChecked(self.tema == "oscuro")

    def _cambiar_acento(self, color: str) -> None:
        if not color_valido(color):
            return
        self.acento = color
        self._aplicar_estilo()
        self._pintar_muestras()
        self._refrescar_muestra()
        _guardar_apariencia(acento=color)
        self._estado(textos.t("m_acento", c=color))

    def _elegir_acento(self) -> None:
        color = QColorDialog.getColor(
            QColor(self.acento), self, textos.t("ap_acento_t")
        )
        if color.isValid():
            self._cambiar_acento(color.name())

    def _cambiar_fuente(self, familia: str) -> None:
        if not familia or familia == self.familia:
            return
        self.familia = familia
        self._aplicar_estilo()
        self._refrescar_muestra()
        _guardar_apariencia(fuente=familia)
        self._estado(textos.t("m_fuente", f=familia))

    def _cambiar_tamano(self, tamano) -> None:
        if not tamano:
            return
        tamano = int(tamano)
        if tamano == self.tamano:
            return
        self.tamano = tamano
        self._aplicar_estilo()
        self._refrescar_muestra()
        _guardar_apariencia(escala=round(tamano / 10, 2))
        self._estado(textos.t("m_tamano", n=tamano))

    def _restablecer_apariencia(self) -> None:
        disponibles = set(QFontDatabase.families())
        familia = FUENTE_DEFECTO if FUENTE_DEFECTO in disponibles else self.familia
        self.acento = ACENTO_DEFECTO
        self.familia = familia
        self.tamano = 10
        self.combo_fuente.blockSignals(True)
        if self.combo_fuente.findText(familia) < 0:
            self.combo_fuente.addItem(familia)
        self.combo_fuente.setCurrentText(familia)
        self.combo_fuente.blockSignals(False)
        self.combo_tamano.blockSignals(True)
        self.combo_tamano.setCurrentIndex(1)
        self.combo_tamano.blockSignals(False)
        _guardar_apariencia(acento=ACENTO_DEFECTO, fuente=familia, escala=1.0)
        self._aplicar_estilo()
        self._pintar_muestras()
        self._refrescar_muestra()
        self._estado(textos.t("m_apariencia_reset"))

    def _refrescar_muestra(self) -> None:
        esc = self.tamano / 10.0
        alto_aa = round(34 * esc)
        self.lbl_aa.setStyleSheet(
            f'font-family: "{self.familia}"; font-size: {alto_aa}px;'
            f" font-weight: bold; color: {self.acento};"
        )
        self.lbl_aa.setMinimumHeight(round(alto_aa * 1.45))
        tenue = TEMAS[self.tema]["tenue"]
        self.lbl_muestra.setStyleSheet(
            f'font-family: "{self.familia}"; font-size: {self.tamano + 1}pt;'
            f" color: {tenue}; background: transparent;"
        )

    def _aplicar_estilo(self) -> None:
        app = QApplication.instance()
        if app is not None:
            hoja = hoja_estilo(self.tema, self.acento, self.familia, self.tamano)
            if textos.rtl():
                hoja += "\nQPushButton#Nav { text-align: right; }"
            app.setStyleSheet(hoja)
        self._pintar_iconos()
        if hasattr(self, "stack"):
            self._ajustar_minimo()

    def _ajustar_minimo(self) -> None:
        """El mínimo crece con la letra para que ninguna tarjeta se recorte."""
        ancho = max(1080, round(1080 * self.tamano / 10))
        pantalla = self.screen().availableGeometry() if self.screen() else None
        if pantalla is not None:
            ancho = min(ancho, max(1024, pantalla.width() - 20))
        self.setMinimumSize(ancho, 580)
        if self.width() < ancho:
            self.resize(ancho, self.height())

    def _pintar_iconos(self) -> None:
        t = TEMAS[self.tema]
        for boton, clave in self.nav_iconos:
            boton.setIcon(icono(clave, t["tenue"], self.acento, t["panel"]))

    def _pintar_muestras(self) -> None:
        t = TEMAS[self.tema]
        for _, caja, color in self.muestras:
            activo = color.lower() == self.acento.lower()
            if activo:
                anillo = QColor(t["texto"])
                anillo.setAlphaF(0.6)
                borde = f"2px solid {anillo.name(QColor.HexArgb)}"
            else:
                borde = "2px solid transparent"
            caja.setStyleSheet(
                "QFrame#AnilloMuestra { background: transparent;"
                f" border: {borde}; border-radius: 19px; }}"
            )

    # ---------- utilidades ----------

    def _pildora(self, texto: str, rol: str = "primario", alto: int = 38,
                 ancho: int | None = None) -> QPushButton:
        base = {"primario": "Pildora", "secundario": "PildoraSec",
                "peligro": "PildoraRoja"}[rol]
        if alto <= 34:
            base = {"Pildora": "PildoraPequena",
                    "PildoraSec": "PildoraSecPequena",
                    "PildoraRoja": "PildoraRoja"}[base]
        b = QPushButton(texto)
        b.setObjectName(base)
        b.setFixedHeight(alto)
        if ancho:
            b.setMinimumWidth(ancho)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _indice_pagina(self, clave: str) -> int:
        claves = [p[0] for p in self._paginas]
        return claves.index(clave) if clave in claves else 0

    def _cambio_pagina(self, indice: int) -> None:
        if not 0 <= indice < len(self._paginas):
            return
        clave, titulo, subtitulo = self._paginas[indice]
        self.lbl_titulo.setText(titulo)
        self.lbl_subtitulo.setText(subtitulo)
        for i, boton in enumerate(self.botones_nav):
            boton.setChecked(i == indice)
        if clave == "estado" and not self._vivo_pausado:
            self._muestrear_vivo()
            if not self.timer_vivo.isActive():
                self.timer_vivo.start()
        else:
            self.timer_vivo.stop()
        if clave == "herramientas":
            self._actualizar_ram_libre()
        if clave == "recomendaciones" and self._equipo is None:
            self._analizar_equipo()

    def _aplicar_idioma(self, codigo: str) -> None:
        if not codigo or codigo == self.idioma:
            return
        textos.usar(codigo)
        textos.guardar(codigo)
        self.idioma = codigo
        self._msg_base = f"{textos.t('listo')} · AMPERA {VERSION}"
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(
                Qt.RightToLeft if textos.rtl() else Qt.LeftToRight)
        indice = self.stack.currentIndex()
        clave = "resumen"
        if 0 <= indice < len(self._paginas):
            clave = self._paginas[indice][0]
        self.timer_vivo.stop()
        self._reconstruir(clave)
        self._estado(f"{textos.t('idioma_titulo')}: {textos.nombre(codigo)}")
        self._sondear(False)

    def _reconstruir(self, clave: str) -> None:
        viejo = self.takeCentralWidget()
        if viejo is not None:
            viejo.deleteLater()
        self.pares_tema.clear()
        self.muestras.clear()
        self.nav_iconos.clear()
        self.tarjetas_perfil.clear()
        self.expansores.clear()
        self.scroll_perfiles = None
        self._construir(clave)
        self._aplicar_estilo()
        self._pintar_muestras()
        self._sincronizar_tema()
        self._refrescar_muestra()
        self._actualizar_resumen()
        self._restaurar_ram_auto()
        self._actualizar_ram_libre()

    def _estado(self, texto: str) -> None:
        self.lbl_estado.setText(texto)
        if hasattr(self, "_timer_estado"):
            self._timer_estado.start(6000)
        else:
            QTimer.singleShot(6000, lambda: self.lbl_estado.setText(self._msg_base))

    def expandir_primero(self) -> None:
        if self.expansores:
            self.expansores[0][0].click()


def _separador() -> QFrame:
    s = QFrame()
    s.setObjectName("Separador")
    s.setFixedHeight(1)
    return s


def _fila_dato(nombre: str, valor: str) -> tuple[QHBoxLayout, QLabel]:
    fila = QHBoxLayout()
    et = QLabel(nombre)
    et.setObjectName("Nota")
    va = QLabel(valor)
    va.setStyleSheet("font-weight: bold;")
    fila.addWidget(et)
    fila.addStretch()
    fila.addWidget(va)
    return fila, va


def main(argv: list[str] | None = None) -> int:
    textos.iniciar()
    parser = argparse.ArgumentParser(description="AMPERA · interfaz PySide6")
    parser.add_argument("--qt", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--tema", choices=list(TEMAS), default=None)
    parser.add_argument("--acento", default=None)
    parser.add_argument("--idioma", choices=textos.codigos(), default=None,
                        help="Idioma solo para esta sesión")
    parser.add_argument("--pagina", choices=list(CLAVES_PAGINA),
                        default="resumen")
    parser.add_argument("--captura", default=None,
                        help="Guarda una captura PNG y sale")
    parser.add_argument("--expandir", action="store_true",
                        help="Abre las acciones del primer perfil")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    if args.idioma:
        textos.usar(args.idioma)
    app.setLayoutDirection(
        Qt.RightToLeft if textos.rtl() else Qt.LeftToRight)
    ventana = Ventana(tema=args.tema or "oscuro", pagina=args.pagina,
                      acento=args.acento)
    ventana.show()

    if args.expandir:
        QTimer.singleShot(250, ventana.expandir_primero)

    if args.captura:
        def capturar() -> None:
            QApplication.processEvents()
            ventana.grab().save(args.captura)
            print(f"Captura guardada en {args.captura}")
            app.quit()

        QTimer.singleShot(3200, capturar)

    return app.exec()
