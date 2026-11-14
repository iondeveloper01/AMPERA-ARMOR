"""Iconos de línea para la barra lateral, dibujados con QPainter."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def _dibujar(clave: str, color: str, fondo: str) -> QPixmap:
    pm = QPixmap(36, 36)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(2)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pluma = QPen(QColor(color), 1.7)
    pluma.setCapStyle(Qt.RoundCap)
    pluma.setJoinStyle(Qt.RoundJoin)
    p.setPen(pluma)
    p.setBrush(Qt.NoBrush)

    if clave == "resumen":
        for x in (2.5, 9.5):
            for y in (2.5, 9.5):
                p.drawRoundedRect(QRectF(x, y, 6, 6), 1.8, 1.8)
    elif clave == "estado":
        puntos = [(2.5, 10.0), (5.4, 10.0), (7.0, 5.0), (9.0, 14.0),
                  (10.6, 10.0), (15.5, 10.0)]
        for (x1, y1), (x2, y2) in zip(puntos, puntos[1:]):
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif clave == "perfiles":
        for y in (4.5, 9.0, 13.5):
            p.setBrush(QColor(color))
            p.drawEllipse(QPointF(3.0, y), 1.1, 1.1)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(6.4, y), QPointF(15.5, y))
    elif clave == "herramientas":
        for y, xk in ((4.5, 11.5), (9.0, 6.8), (13.5, 12.6)):
            p.drawLine(QPointF(2.5, y), QPointF(15.5, y))
            p.setBrush(QColor(fondo))
            p.drawEllipse(QPointF(xk, y), 1.9, 1.9)
            p.setBrush(Qt.NoBrush)
    elif clave == "apariencia":
        p.setBrush(QColor(color))
        p.drawPie(QRectF(2.6, 2.6, 12.8, 12.8), 90 * 16, 180 * 16)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(9, 9), 6.4, 6.4)
    elif clave == "recomendaciones":
        estrella = [(7.0, 3.0), (8.7, 6.1), (12.3, 7.6), (8.7, 9.1),
                    (7.0, 13.2), (5.3, 9.1), (1.7, 7.6), (5.3, 6.1),
                    (7.0, 3.0)]
        p.drawPolyline([QPointF(x, y) for x, y in estrella])
        p.drawLine(QPointF(13.6, 2.6), QPointF(13.6, 5.4))
        p.drawLine(QPointF(12.2, 4.0), QPointF(15.0, 4.0))
    elif clave == "centro":
        p.drawEllipse(QPointF(9, 9), 6.6, 6.6)
        p.drawLine(QPointF(9, 9), QPointF(9, 4.4))
        p.drawLine(QPointF(9, 9), QPointF(12.4, 10.8))

    p.end()
    return pm


def icono(clave: str, color_off: str, color_on: str, fondo: str) -> QIcon:
    """Icono con dos versiones: normal (tenue) y marcado (acento)."""
    ic = QIcon()
    ic.addPixmap(_dibujar(clave, color_off, fondo), QIcon.Normal, QIcon.Off)
    ic.addPixmap(_dibujar(clave, color_on, fondo), QIcon.Normal, QIcon.On)
    return ic
