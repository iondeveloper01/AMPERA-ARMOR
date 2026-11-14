"""Tarjeta del overlay de la sesión de juego (Qt).

Reemplaza la ventana Tk plana por una tarjeta con el lenguaje visual de AMPERA:
esquinas redondeadas, filo de acento, tipografía del estilo elegido y opacidad.
Sigue siendo "click-through": el ratón atraviesa la tarjeta y llega al juego.

La posición se lee de `overlay_estilos.json` (anclas de pantalla o coordenadas
libres). Con el atajo Ctrl+Shift+L se entra en modo mover: la tarjeta deja pasar
el ratón, se arrastra y al soltar se guarda la posición.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QLinearGradient,
                           QPainter, QPen)
from PySide6.QtWidgets import QApplication, QWidget

import overlay_estilo
from monitor_fase1 import CPU_CRIT, CPU_WARN, GPU_CRIT, GPU_WARN

_BASE_FLAGS = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.NoDropShadowWindowHint)

_MARGEN = 16
_PAD = 16


def _color(valor, alpha=255):
    c = QColor(valor)
    if not c.isValid():
        c = QColor("#000000")
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _mezcla(base: QColor, otro: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(base.red() * (1 - t) + otro.red() * t),
        round(base.green() * (1 - t) + otro.green() * t),
        round(base.blue() * (1 - t) + otro.blue() * t),
    )


class Overlay(QWidget):
    def __init__(self, estilo, estado_fn):
        self.estilo = estilo
        self._estado_fn = estado_fn
        self._mover = False
        self._arrastre = None
        self._datos = {}
        super().__init__(None, _BASE_FLAGS | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._calcular_tamano()
        self._aplicar_posicion()

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ---------- tamaño / posición ----------
    def _base(self) -> int:
        try:
            return max(8, min(28, int(self.estilo.get("tamano", 12))))
        except Exception:
            return 12

    def _calcular_tamano(self):
        b = self._base()
        ancho = max(340, int(b * 30))
        alto = int(_PAD * 2 + b * 1.8 + 6 + b * 3.4 + 10 + 1 + 8
                   + b * 1.85 * 5 + 8 + b * 1.5)
        self.setFixedSize(ancho, alto)

    def _aplicar_posicion(self):
        self._pos = overlay_estilo.ventana()
        pantallas = QGuiApplication.screens()
        if not pantallas:
            return
        indice = max(0, min(int(self._pos.get("monitor", 0)), len(pantallas) - 1))
        g = pantallas[indice].availableGeometry()
        w, h = self.width(), self.height()
        m = max(0, int(self._pos.get("margen", _MARGEN)))
        ancla = self._pos.get("ancla", "arriba-izquierda")
        if ancla == "libre":
            x = int(self._pos.get("x", g.left() + m))
            y = int(self._pos.get("y", g.top() + m))
        else:
            if ancla == "centro":
                vert, horiz = "centro", "centro"
            elif "-" in ancla:
                vert, horiz = ancla.split("-", 1)
            elif ancla in ("arriba", "abajo"):
                vert, horiz = ancla, "centro"
            else:
                vert, horiz = "centro", ancla
            x = {"izquierda": g.left() + m,
                 "derecha": g.right() - w - m,
                 "centro": g.center().x() - w // 2}.get(horiz, g.left() + m)
            y = {"arriba": g.top() + m,
                 "abajo": g.bottom() - h - m,
                 "centro": g.center().y() - h // 2}.get(vert, g.top() + m)
        x = max(g.left(), min(x, g.right() - w + 1))
        y = max(g.top(), min(y, g.bottom() - h + 1))
        self.move(x, y)

    def _indice_pantalla(self):
        centro = self.frameGeometry().center()
        for i, scr in enumerate(QGuiApplication.screens()):
            if scr.geometry().contains(centro):
                return i
        return int(self._pos.get("monitor", 0))

    # ---------- modo mover ----------
    def set_modo_mover(self, activo):
        activo = bool(activo)
        if activo == self._mover:
            return
        self._mover = activo
        self._arrastre = None
        flags = _BASE_FLAGS | (0 if activo else Qt.WindowTransparentForInput)
        self.setWindowFlags(flags)
        self.setCursor(Qt.SizeAllCursor if activo else Qt.ArrowCursor)
        self.show()
        self._aplicar_posicion()

    def mousePressEvent(self, evento):
        if self._mover and evento.button() == Qt.LeftButton:
            self._arrastre = evento.globalPosition().toPoint() - self.frameGeometry().topLeft()
            evento.accept()

    def mouseMoveEvent(self, evento):
        if self._mover and self._arrastre is not None:
            self.move(evento.globalPosition().toPoint() - self._arrastre)
            evento.accept()

    def mouseReleaseEvent(self, evento):
        if self._mover and self._arrastre is not None:
            self._arrastre = None
            self._pos = overlay_estilo.guardar_ventana(
                ancla="libre", x=self.x(), y=self.y(),
                monitor=self._indice_pantalla())
            evento.accept()

    # ---------- refresco ----------
    def _tick(self):
        try:
            datos, visible, mover, parar = self._estado_fn()
        except Exception:
            datos, visible, mover, parar = {}, True, False, False
        if parar:
            self._timer.stop()
            QApplication.quit()
            return
        self._datos = datos or {}
        if mover != self._mover:
            self.set_modo_mover(mover)
        if visible != self.isVisible():
            self.setVisible(visible)
        self.update()

    # ---------- pintura ----------
    def _nivel(self, valor, aviso, critico):
        if valor is None:
            return 0
        if valor >= critico:
            return 2
        if valor >= aviso:
            return 1
        return 0

    def _color_nivel(self, nivel):
        if nivel == 2:
            return self.estilo["critico"]
        if nivel == 1:
            return self.estilo["aviso"]
        return self.estilo["numeros"]

    @staticmethod
    def _txt(valor, sufijo="", decimales=None):
        if valor is None:
            return "—"
        if isinstance(valor, float) and decimales is not None:
            return f"{valor:.{decimales}f}{sufijo}"
        return f"{valor}{sufijo}"

    def paintEvent(self, evento):
        est = self.estilo
        b = self._base()
        w, h = self.width(), self.height()
        opacidad = float(est.get("opacidad", 0.7))
        alpha_fondo = int(255 * opacidad)
        acento = est.get("acento") or est["texto"]
        d = self._datos
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # tarjeta: degradado sutil para que no se vea plana
        fondo = QColor(est["fondo"])
        arriba = _mezcla(fondo, QColor("#ffffff"), 0.10).name()
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, _color(arriba, alpha_fondo))
        grad.setColorAt(1.0, _color(fondo.name(), alpha_fondo))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 18, 18)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_color(acento, 70), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 18, 18)

        # filo de acento a la izquierda
        p.setPen(Qt.NoPen)
        p.setBrush(_color(acento, 235))
        p.drawRoundedRect(QRectF(0, 18, 3.5, h - 36), 2, 2)

        x0 = _PAD
        ancho_util = w - _PAD * 2
        y = 13

        # cabecera: marca + tiempo + punto de estado
        p.setFont(QFont(est["fuente"], b, QFont.Bold))
        p.setPen(_color(acento))
        p.drawText(QRectF(x0, y, ancho_util - 92, b * 1.8),
                   Qt.AlignLeft | Qt.AlignVCenter, "AMPERA")
        ancho_marca = p.fontMetrics().horizontalAdvance("AMPERA")
        p.setFont(QFont(est["fuente"], max(7, b - 4)))
        p.setPen(_color(est["pista"]))
        p.drawText(QRectF(x0 + ancho_marca + 7, y, 90, b * 1.8),
                   Qt.AlignLeft | Qt.AlignVCenter, "SESIÓN")
        transcurrido = int(d.get("elapsed", 0) or 0)
        p.setFont(QFont(est["fuente"], max(7, b - 2)))
        p.drawText(QRectF(w - _PAD - 78, y, 66, b * 1.8),
                   Qt.AlignRight | Qt.AlignVCenter,
                   f"{transcurrido // 60:02d}:{transcurrido % 60:02d}")
        nivel = max(self._nivel(d.get("gpu_temp"), GPU_WARN, GPU_CRIT),
                    self._nivel(d.get("cpu_temp"), CPU_WARN, CPU_CRIT))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color_nivel(nivel)))
        p.drawEllipse(QRectF(w - _PAD - 7, y + b * 0.8, 7, 7))
        y += int(b * 1.8) + 6

        # panel de FPS
        alto_panel = b * 3.4
        p.setPen(QPen(_color(acento, 55), 1))
        p.setBrush(_color(acento, 22))
        p.drawRoundedRect(QRectF(x0, y, ancho_util, alto_panel), 10, 10)

        fps = d.get("fps")
        ms = d.get("frametime")
        texto_fps = "—" if fps is None else f"{fps:.0f}"
        p.setFont(QFont(est["fuente"], b + 15, QFont.Bold))
        p.setPen(_color(est["numeros"] if fps is not None else est["pista"]))
        p.drawText(QRectF(x0 + 12, y, 140, alto_panel),
                   Qt.AlignLeft | Qt.AlignVCenter, texto_fps)
        ancho_fps = p.fontMetrics().horizontalAdvance(texto_fps)
        p.setFont(QFont(est["fuente"], max(8, b - 2), QFont.Bold))
        p.setPen(_color(est["texto"]))
        p.drawText(QRectF(x0 + 16 + ancho_fps, y, 46, alto_panel),
                   Qt.AlignLeft | Qt.AlignBottom, "FPS")

        p.setFont(QFont(est["fuente"], max(7, b - 5)))
        p.setPen(_color(est["pista"]))
        p.drawText(QRectF(w - _PAD - 96, y + 7, 84, b * 1.3),
                   Qt.AlignRight | Qt.AlignVCenter, "FRAMETIME")
        p.setFont(QFont(est["fuente"], max(8, b - 1)))
        p.setPen(_color(est["numeros"] if ms is not None else est["pista"]))
        p.drawText(QRectF(w - _PAD - 96, y + b * 1.5, 84, b * 1.3),
                   Qt.AlignRight | Qt.AlignVCenter,
                   "—" if ms is None else f"{ms:.1f} ms")
        y += int(alto_panel) + 10

        # separador
        p.setPen(QPen(_color(est["pista"], 60), 1))
        p.drawLine(x0, y, w - _PAD, y)
        y += 8

        # filas de métricas: rótulo a la izquierda, lecturas a la derecha
        filas = [
            ("GPU", f"{self._txt(d.get('gpu_temp'), ' °C')}    "
                    f"{self._txt(d.get('gpu_uso'), ' %')}    "
                    f"{self._txt(d.get('gpu_pow'), ' W')}",
             self._nivel(d.get("gpu_temp"), GPU_WARN, GPU_CRIT)),
            ("VRAM", self._txt(d.get("vram"), " GB", 1), 0),
            ("CPU", f"{self._txt(d.get('cpu_temp'), ' °C')}    "
                    f"{self._txt(d.get('cpu_uso'), ' %')}",
             self._nivel(d.get("cpu_temp"), CPU_WARN, CPU_CRIT)),
            ("RAM", f"{self._txt(d.get('ram_pct'), ' %')}    libre "
                    f"{self._txt(d.get('ram_libre'), ' GB', 1)}", 0),
            ("COMMIT", "libre " + self._txt(d.get("commit_libre"), " GB", 1), 0),
        ]
        f_rot = QFont(est["fuente"], max(8, b - 1), QFont.Bold)
        f_val = QFont(est["fuente"], max(9, b))
        alto_fila = b * 1.85
        for rotulo, valor, niv in filas:
            p.setFont(f_rot)
            p.setPen(_color(est["texto"]))
            p.drawText(QRectF(x0, y, 84, alto_fila),
                       Qt.AlignLeft | Qt.AlignVCenter, rotulo)
            p.setFont(f_val)
            p.setPen(_color(self._color_nivel(niv)))
            p.drawText(QRectF(x0 + 88, y, ancho_util - 88, alto_fila),
                       Qt.AlignRight | Qt.AlignVCenter, valor)
            y += int(alto_fila)

        # pie: atajos o modo mover
        y += 6
        p.setFont(QFont(est["fuente"], max(7, b - 4)))
        if self._mover:
            p.setPen(_color(acento, 230))
            p.drawText(QRectF(x0, y, ancho_util, b * 1.5),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "ARRASTRA · Ctrl+Shift+L para fijar")
            p.setPen(QPen(_color(acento, 200), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1.5, 1.5, w - 3, h - 3), 18, 18)
        else:
            p.setPen(_color(est["pista"]))
            p.drawText(QRectF(x0, y, ancho_util, b * 1.5),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "Shift+Z · Shift+M · Ctrl+Shift+L mover · Ctrl+Shift+Q")
        p.end()
