"""Diálogos de la interfaz PySide6: editor de perfil y certificación."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

import datos as datos_json
import planes_energia
from . import servicios, textos


def _pildora(texto: str, rol: str = "primario", alto: int = 36,
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


def _repintar(widget: QWidget, objeto: str) -> None:
    if widget.objectName() == objeto:
        return
    widget.setObjectName(objeto)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class DialogoPerfil(QDialog):
    """Ver y editar un perfil sin abrir el JSON a mano.

    Los planes que ofrece son los que existen en ESTE equipo. Las claves que el
    editor no muestra (por ejemplo "xtu") se conservan al guardar.
    """

    def __init__(self, parent=None, nombre: str | None = None, cfg: dict | None = None,
                 cert: dict | None = None, planes: list[dict] | None = None):
        super().__init__(parent)
        self._cfg = dict(cfg or {})
        self.nombre = nombre
        self.datos: dict | None = None
        editando = nombre is not None
        visible = (textos.nombre_perfil(nombre, self._cfg.get("nota"))
                   if editando else "")
        self.setWindowTitle(
            textos.t("d_editar_titulo", n=visible) if editando
            else textos.t("d_nuevo_titulo"))
        self.setMinimumWidth(640)
        self.setModal(True)

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 20)
        col.setSpacing(12)

        titulo = QLabel(visible or textos.t("d_nuevo_titulo"))
        titulo.setObjectName("TarjetaTitulo")
        col.addWidget(titulo)

        cert = cert or {}
        if editando:
            if cert.get("certificado"):
                estado = QLabel(
                    textos.t("cert_el",
                             f=str(cert.get("fecha", "?")).split()[0])
                    + " · "
                    + textos.t("cert_pico", g=cert.get("pico_gpu_c", "?"))
                )
                estado.setObjectName("InsigniaOk")
            else:
                estado = QLabel(textos.t("sin_certificar"))
                estado.setObjectName("InsigniaPend")
            col.addWidget(estado, alignment=Qt.AlignLeft)

        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setColumnStretch(1, 1)
        fila = [0]

        def etiqueta(texto: str) -> QLabel:
            lbl = QLabel(texto)
            lbl.setObjectName("Nota")
            return lbl

        def agregar(texto: str, widget: QWidget) -> QWidget:
            form.addWidget(etiqueta(texto), fila[0], 0, Qt.AlignTop | Qt.AlignLeft)
            form.addWidget(widget, fila[0], 1)
            fila[0] += 1
            return widget

        self.ent_nombre = QLineEdit(visible if editando else "")
        self.ent_nombre.setPlaceholderText(textos.t("d_nombre_ph"))
        self.ent_nombre.setReadOnly(editando)
        agregar(textos.t("d_nombre"), self.ent_nombre)

        nota_guardada = str(self._cfg.get("nota", "") or "")
        if editando:
            nota_guardada = textos.nota_perfil(nombre, nota_guardada)
        self.ent_nota = QLineEdit(nota_guardada)
        self.ent_nota.setPlaceholderText(textos.t("d_nota_ph"))
        agregar(textos.t("d_nota"), self.ent_nota)

        opciones = planes if planes is not None else planes_energia.listar(forzar=True)
        self.combo_plan = QComboBox()
        for p in opciones:
            self.combo_plan.addItem(
                textos.nombre_plan(p.get("alias"), p["nombre"]), p["guid"])
        guardado = self._cfg.get("plan", "alto")
        guid, nombre_plan = planes_energia.resolver(guardado)
        indice = self.combo_plan.findData(guid) if guid else -1
        if indice >= 0:
            self.combo_plan.setCurrentIndex(indice)
        elif guid:
            alias = guardado if guardado in textos.PLANES_WINDOWS else None
            self.combo_plan.addItem(
                textos.nombre_plan(alias, nombre_plan or str(guardado)), guid)
            self.combo_plan.setCurrentIndex(self.combo_plan.count() - 1)
        agregar(textos.t("d_plan"), self.combo_plan)

        self.spin_cpu = QSpinBox()
        self.spin_cpu.setRange(5, 100)
        self.spin_cpu.setSuffix(" %")
        self.spin_cpu.setValue(datos_json.entero(self._cfg.get("cpu_max"), 100, 5, 100))
        agregar(textos.t("d_techo"), self.spin_cpu)

        self.combo_prio = QComboBox()
        for texto, valor in ((textos.t("normal"), "normal"),
                             (textos.t("alta"), "alta"),
                             (textos.t("d_baja"), "baja")):
            self.combo_prio.addItem(texto, valor)
        actual = str(self._cfg.get("prioridad", "normal"))
        indice = self.combo_prio.findData(actual)
        self.combo_prio.setCurrentIndex(indice if indice >= 0 else 0)
        agregar(textos.t("d_prioridad"), self.combo_prio)

        self.ent_cerrar = QLineEdit(", ".join(self._cfg.get("cerrar") or []))
        self.ent_cerrar.setPlaceholderText(textos.t("d_cerrar_ph"))
        agregar(textos.t("d_cerrar"), self.ent_cerrar)

        caja_exe = QWidget()
        fila_exe = QHBoxLayout(caja_exe)
        fila_exe.setContentsMargins(0, 0, 0, 0)
        fila_exe.setSpacing(8)
        self.ent_exe = QLineEdit(str(self._cfg.get("exe", "") or ""))
        self.ent_exe.setPlaceholderText(textos.t("d_exe_ph"))
        examinar = _pildora(textos.t("d_examinar"), rol="secundario", alto=36,
                            ancho=108)
        examinar.clicked.connect(self._elegir_exe)
        fila_exe.addWidget(self.ent_exe, 1)
        fila_exe.addWidget(examinar)
        agregar(textos.t("d_exe"), caja_exe)

        self.combo_fans = QComboBox()
        self.combo_fans.addItem(textos.t("d_fans_igual"), "")
        for valor in ("Auto", "Max"):
            self.combo_fans.addItem(valor, valor)
        fans = str(self._cfg.get("fans", "") or "")
        indice = self.combo_fans.findData(fans)
        if indice < 0 and fans:
            self.combo_fans.addItem(fans, fans)
            indice = self.combo_fans.count() - 1
        self.combo_fans.setCurrentIndex(max(0, indice))
        agregar(textos.t("d_fans"), self.combo_fans)

        col.addLayout(form)

        ayuda = QLabel(textos.t("d_ayuda"))
        ayuda.setObjectName("Nota")
        ayuda.setWordWrap(True)
        col.addWidget(ayuda)
        col.addStretch()

        pie = QHBoxLayout()
        pie.setSpacing(8)
        pie.addStretch()
        cancelar = _pildora(textos.t("d_cancelar"), rol="secundario", ancho=118)
        cancelar.clicked.connect(self.reject)
        guardar = _pildora(textos.t("d_guardar"), rol="primario", ancho=132)
        guardar.setDefault(True)
        guardar.clicked.connect(self._guardar)
        pie.addWidget(cancelar)
        pie.addWidget(guardar)
        col.addLayout(pie)
        self.adjustSize()
        self.ent_nota.setFocus()
        self.ent_nota.deselect()

    def showEvent(self, evento) -> None:
        super().showEvent(evento)
        # Al mostrarse, Qt enfoca el primer campo editable y selecciona todo el
        # texto: molesta porque parece que vas a sobrescribir la nota.
        QTimer.singleShot(0, self.ent_nota.deselect)

    def _elegir_exe(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, textos.t("d_elegir_exe"), "",
            textos.t("d_filtro_exe"))
        if ruta:
            self.ent_exe.setText(ruta)

    def _guardar(self) -> None:
        nombre = (self.nombre if self.nombre is not None
                  else self.ent_nombre.text().strip())
        if not nombre:
            QMessageBox.warning(self, textos.t("d_falta_nombre"),
                                textos.t("d_falta_nombre_msg"))
            return
        datos = dict(self._cfg)
        datos["plan"] = self.combo_plan.currentData() or self._cfg.get("plan", "alto")
        datos["cpu_max"] = int(self.spin_cpu.value())
        datos["prioridad"] = self.combo_prio.currentData()
        datos["cerrar"] = [x.strip() for x in self.ent_cerrar.text().split(",")
                           if x.strip()]
        datos["exe"] = self.ent_exe.text().strip().strip('"')
        datos["fans"] = self.combo_fans.currentData() or ""
        nota = self.ent_nota.text().strip()
        if nota == textos.t("perfil_fabrica_nota"):
            nota = ""
        datos["nota"] = nota
        self.nombre = nombre
        self.datos = datos
        self.accept()


class DialogoCertificar(QDialog):
    """Ejecuta `certificar` con la salida en vivo y permite detenerlo."""

    def __init__(self, parent=None, nombre: str = "", nota: str = ""):
        super().__init__(parent)
        self._nombre = nombre
        self._tarea: servicios.Tarea | None = None
        visible = textos.nombre_perfil(nombre, nota)
        self.setWindowTitle(textos.t("d_cert_titulo", n=visible))
        self.setMinimumSize(760, 520)
        self.setModal(True)

        col = QVBoxLayout(self)
        col.setContentsMargins(24, 20, 24, 20)
        col.setSpacing(12)

        titulo = QLabel(textos.t("d_cert_encabezado", n=visible))
        titulo.setObjectName("TarjetaTitulo")
        col.addWidget(titulo)

        desc = QLabel(textos.t("d_cert_desc"))
        desc.setObjectName("Nota")
        desc.setWordWrap(True)
        col.addWidget(desc)

        fila = QHBoxLayout()
        fila.setSpacing(10)
        et = QLabel(textos.t("d_duracion"))
        et.setObjectName("Nota")
        fila.addWidget(et)
        self.combo_duracion = QComboBox()
        for texto, segundos in (("30 s", 30), (textos.t("d_min1"), 60),
                                (textos.t("d_min2"), 120),
                                (textos.t("d_min5"), 300)):
            self.combo_duracion.addItem(texto, segundos)
        fila.addWidget(self.combo_duracion)
        fila.addStretch()
        self.lbl_resultado = QLabel("")
        self.lbl_resultado.setObjectName("InsigniaNeutra")
        self.lbl_resultado.setVisible(False)
        fila.addWidget(self.lbl_resultado)
        col.addLayout(fila)

        self.salida = QPlainTextEdit()
        self.salida.setReadOnly(True)
        self.salida.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.salida.setMaximumBlockCount(5000)
        self.salida.setPlaceholderText(textos.t("d_salida_ph"))
        col.addWidget(self.salida, 1)

        botones = QHBoxLayout()
        botones.setSpacing(8)
        self.btn_iniciar = _pildora(textos.t("d_iniciar"), rol="primario",
                                    ancho=150)
        self.btn_iniciar.clicked.connect(self._iniciar)
        self.btn_detener = _pildora(textos.t("d_detener"), rol="peligro",
                                    alto=34, ancho=118)
        self.btn_detener.setEnabled(False)
        self.btn_detener.clicked.connect(self._detener)
        cerrar = _pildora(textos.t("d_cerrar_dlg"), rol="secundario", ancho=118)
        cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_iniciar)
        botones.addWidget(self.btn_detener)
        botones.addStretch()
        botones.addWidget(cerrar)
        col.addLayout(botones)

    def _poner_resultado(self, texto: str, objeto: str) -> None:
        self.lbl_resultado.setText(texto)
        self.lbl_resultado.setVisible(bool(texto))
        _repintar(self.lbl_resultado, objeto)

    def _iniciar(self) -> None:
        if self._tarea is not None:
            return
        segundos = int(self.combo_duracion.currentData() or 30)
        self.salida.clear()
        self._poner_resultado(textos.t("d_en_curso"), "Insignia")
        tarea = servicios.Tarea(
            "certificar", [self._nombre, "--segundos", str(segundos)], self)
        tarea.linea.connect(self._linea)
        tarea.terminada.connect(self._fin)
        self._tarea = tarea
        self.btn_iniciar.setEnabled(False)
        self.btn_detener.setEnabled(True)
        self.combo_duracion.setEnabled(False)
        tarea.start()

    def _linea(self, texto: str) -> None:
        self.salida.appendPlainText(servicios.limpiar_ansi(texto))

    def _fin(self, codigo: int) -> None:
        self._tarea = None
        self.btn_iniciar.setEnabled(True)
        self.btn_detener.setEnabled(False)
        self.combo_duracion.setEnabled(True)
        if codigo == 0:
            self._poner_resultado(textos.t("certificado"), "InsigniaOk")
        elif codigo == 1:
            self._poner_resultado(textos.t("d_no_supero"), "InsigniaPend")
        else:
            self._poner_resultado(textos.t("detenido"), "InsigniaPend")
        if servicios.reparar_pendientes():
            self.salida.appendPlainText(textos.t("d_prueba_cortada"))

    def _detener(self) -> None:
        if self._tarea is not None:
            self._tarea.detener()
            self.btn_detener.setEnabled(False)
            self._poner_resultado(textos.t("m_deteniendo"), "InsigniaPend")

    def closeEvent(self, evento) -> None:
        if self._tarea is not None:
            QMessageBox.information(
                self, textos.t("d_prueba_curso"),
                textos.t("d_prueba_curso_msg"))
            evento.ignore()
            return
        super().closeEvent(evento)
