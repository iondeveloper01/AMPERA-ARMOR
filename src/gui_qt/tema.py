"""Colores, fuentes y hoja de estilo de la interfaz PySide6 de AMPERA."""

import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

_MARCAS: dict[str, str] = {}

TEMAS = {
    "oscuro": {
        "fondo": "#0c1116",
        "panel": "#0f151b",
        "tarjeta": "#141b22",
        "tarjeta_alta": "#18212a",
        "borde": "#232e39",
        "borde_fuerte": "#33414f",
        "texto": "#e6edf3",
        "tenue": "#8c9aa7",
        "ok": "#3fb950",
        "aviso": "#d29922",
        "rojo": "#f85149",
    },
    "claro": {
        "fondo": "#f3f5f9",
        "panel": "#ffffff",
        "tarjeta": "#ffffff",
        "tarjeta_alta": "#f7f9fc",
        "borde": "#e2e8f0",
        "borde_fuerte": "#cbd5e1",
        "texto": "#0f1720",
        "tenue": "#5b6b7b",
        "ok": "#1a7f37",
        "aviso": "#9a6700",
        "rojo": "#d1242f",
    },
}

# Mismos presets que la interfaz CustomTkinter (app.py) para que ambas coincidan.
ACENTOS = [
    ("Cian", "#00b4d8"),
    ("Verde", "#2ecc71"),
    ("Rosa", "#ff5da2"),
    ("Ámbar", "#ffb020"),
    ("Morado", "#9b5de5"),
    ("Rojo", "#ff4d4d"),
]

FUENTES = [
    "Bahnschrift", "Bahnschrift SemiBold", "Bahnschrift Light",
    "Segoe UI", "Segoe UI Variable Display",
    "Arial", "Arial Rounded MT Bold", "Verdana", "Tahoma", "Trebuchet MS",
    "Calibri", "Candara", "Corbel", "Franklin Gothic Medium",
    "Century Gothic", "Georgia", "Impact", "Palatino Linotype",
    "Cascadia Code", "Cascadia Mono", "Consolas", "Courier New",
]

ACENTO_DEFECTO = "#00b4d8"
FUENTE_DEFECTO = "Segoe UI Variable Display"


def color_valido(hx: str) -> bool:
    """#rrggbb usable como acento; se rechaza el negro puro."""
    return (isinstance(hx, str) and len(hx) == 7 and hx[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in hx[1:])
            and hx.lower() != "#000000")


def _marca(color: str) -> str:
    """PNG de la casilla marcada: Qt no dibuja la marca al pintar el fondo."""
    if color in _MARCAS:
        return _MARCAS[color]
    ruta = os.path.join(tempfile.gettempdir(), f"ampera_marca_{color[1:]}.png")
    if not os.path.exists(ruta):
        pm = QPixmap(30, 30)
        pm.fill(Qt.transparent)
        pm.setDevicePixelRatio(2)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(1, 1, 13, 13), 3.5, 3.5)
        pluma = QPen(QColor("#08131a"), 1.8)
        pluma.setCapStyle(Qt.RoundCap)
        pluma.setJoinStyle(Qt.RoundJoin)
        p.setPen(pluma)
        p.drawPolyline([QPointF(4.2, 7.4), QPointF(6.3, 9.5),
                        QPointF(10.2, 4.9)])
        p.end()
        pm.save(ruta, "PNG")
    _MARCAS[color] = ruta.replace("\\", "/")
    return _MARCAS[color]


def _rgba(color: str, alfa: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alfa * 100)}%)"


def hoja_estilo(tema: str, acento: str, familia: str, tamano: int) -> str:
    t = TEMAS[tema]
    esc = tamano / 10.0
    f_titulo = round(18 * esc, 1)
    f_sub = round(9.5 * esc, 1)
    f_tarjeta = round(12 * esc, 1)
    f_nombre = round(11.5 * esc, 1)
    f_etiqueta = round(8.5 * esc, 1)
    f_nota = round(9 * esc, 1)
    f_cifra = round(24 * esc, 1)
    f_insignia = round(8.5 * esc, 1)
    f_avatar = round(15 * esc, 1)
    f_marca = min(17.0, round(15 * esc, 1))
    f_nav = round(10 * esc, 1)
    f_boton = round(10 * esc, 1)
    f_boton_p = round(9 * esc, 1)
    f_pie = round(8.5 * esc, 1)
    suave = _rgba(acento, 0.16 if tema == "oscuro" else 0.10)
    pulsado = _rgba(acento, 0.30 if tema == "oscuro" else 0.22)
    seleccion = _rgba(acento, 0.20 if tema == "oscuro" else 0.12)
    hover = _rgba(t["texto"], 0.06 if tema == "oscuro" else 0.04)
    ok_fondo = _rgba(t["ok"], 0.14)
    aviso_fondo = _rgba(t["aviso"], 0.13)
    rojo_fondo = _rgba(t["rojo"], 0.13)
    return f"""
    * {{ font-family: "{familia}"; }}
    QWidget {{ color: {t['texto']}; font-size: {tamano}pt; }}
    QMainWindow#Ventana, QWidget#Central, QStackedWidget {{ background: {t['fondo']}; }}
    QDialog {{ background: {t['fondo']}; }}
    QWidget#Pagina, QWidget#Contenido {{ background: transparent; }}
    QFrame#PanelLateral {{ background: {t['panel']}; border-right: 1px solid {t['borde']}; }}
    QFrame#BarraSuperior {{ background: {t['fondo']}; border-bottom: 1px solid {t['borde']}; }}
    QFrame#Tarjeta {{ background: {t['tarjeta']}; border: 1px solid {t['borde']}; border-radius: 14px; }}
    QFrame#TarjetaInterior {{ background: {t['tarjeta_alta']}; border: 1px solid {t['borde']}; border-radius: 12px; }}
    QFrame#Separador {{ background: {t['borde']}; border: none; min-height: 1px; max-height: 1px; }}
    QFrame#AnilloMuestra {{ background: transparent; border: 2px solid transparent; border-radius: 21px; }}
    QLabel {{ background: transparent; }}
    QProgressBar {{
        background: {t['tarjeta_alta']}; border: 1px solid {t['borde']};
        border-radius: 4px; min-height: 8px; max-height: 8px;
        text-align: center; color: transparent;
    }}
    QProgressBar::chunk {{ background: {acento}; border-radius: 4px; }}

    QLabel#Marca {{ font-size: {f_marca}pt; font-weight: bold; }}
    QLabel#Titulo {{ font-size: {f_titulo}pt; font-weight: bold; }}
    QLabel#Subtitulo {{ color: {t['tenue']}; font-size: {f_sub}pt; }}
    QLabel#TarjetaTitulo {{ font-size: {f_tarjeta}pt; font-weight: bold; }}
    QLabel#NombrePerfil {{ font-size: {f_nombre}pt; font-weight: bold; }}
    QLabel#Etiqueta {{ color: {t['tenue']}; font-size: {f_etiqueta}pt; font-weight: bold; }}
    QLabel#Nota {{ color: {t['tenue']}; font-size: {f_nota}pt; }}
    QLabel#Cifra {{ color: {acento}; font-size: {f_cifra}pt; font-weight: bold; }}
    QLabel#Punto {{ background: {acento}; border-radius: 3px; }}
    QLabel#Avatar {{
        background: {suave}; color: {acento}; border-radius: 12px;
        font-size: {f_avatar}pt; font-weight: bold;
    }}

    QLabel#Insignia {{
        background: {suave}; color: {acento}; border-radius: 9px;
        padding: 3px 9px; font-size: {f_insignia}pt; font-weight: bold;
    }}
    QLabel#InsigniaNeutra {{
        background: {hover}; color: {t['tenue']}; border: 1px solid {t['borde']};
        border-radius: 10px; padding: 4px 11px; font-size: {f_insignia}pt; font-weight: bold;
    }}
    QLabel#InsigniaOk {{
        background: {ok_fondo}; color: {t['ok']}; border-radius: 9px;
        padding: 3px 9px; font-size: {f_insignia}pt; font-weight: bold;
    }}
    QLabel#InsigniaPend {{
        background: {aviso_fondo}; color: {t['aviso']}; border-radius: 9px;
        padding: 3px 9px; font-size: {f_insignia}pt; font-weight: bold;
    }}
    QLabel#Aviso {{
        background: {aviso_fondo}; color: {t['aviso']};
        border: 1px solid {_rgba(t['aviso'], 0.35)}; border-radius: 12px;
        padding: 10px 14px; font-size: {f_nota}pt;
    }}
    QLabel#EstadoPie {{
        background: {t['panel']}; color: {t['tenue']};
        border-top: 1px solid {t['borde']}; padding: 7px 30px; font-size: {f_pie}pt;
    }}

    QPushButton#Nav {{
        background: transparent; border: none; border-radius: 10px;
        padding: 10px 12px; text-align: left; color: {t['tenue']}; font-size: {f_nav}pt;
    }}
    QPushButton#Nav:hover {{ background: {hover}; color: {t['texto']}; }}
    QPushButton#Nav:checked {{ background: {suave}; color: {acento}; font-weight: bold; }}

    QPushButton#Pildora {{
        background: transparent; border: 2px solid {acento}; border-radius: 19px;
        padding: 0 18px; color: {t['texto']}; font-weight: bold; font-size: {f_boton}pt;
    }}
    QPushButton#Pildora:hover {{ background: {suave}; }}
    QPushButton#Pildora:pressed {{ background: {pulsado}; }}
    QPushButton#Pildora:checked {{ background: {seleccion}; color: {acento}; }}
    QPushButton#Pildora:disabled {{ color: {t['tenue']}; border-color: {t['borde_fuerte']}; }}

    QPushButton#PildoraPequena {{
        background: transparent; border: 2px solid {acento}; border-radius: 16px;
        padding: 0 14px; color: {t['texto']}; font-weight: bold; font-size: {f_boton_p}pt;
    }}
    QPushButton#PildoraPequena:hover {{ background: {suave}; }}
    QPushButton#PildoraPequena:pressed {{ background: {pulsado}; }}
    QPushButton#PildoraPequena:checked {{ background: {seleccion}; color: {acento}; }}

    QPushButton#PildoraSec {{
        background: transparent; border: 1px solid {t['borde_fuerte']}; border-radius: 18px;
        padding: 0 18px; color: {t['tenue']}; font-weight: bold; font-size: {f_boton}pt;
    }}
    QPushButton#PildoraSec:hover {{ background: {hover}; border-color: {t['tenue']}; color: {t['texto']}; }}
    QPushButton#PildoraSec:checked {{
        background: {seleccion}; border-color: {acento}; color: {acento};
    }}

    QPushButton#PildoraSecPequena {{
        background: transparent; border: 1px solid {t['borde_fuerte']}; border-radius: 16px;
        padding: 0 14px; color: {t['tenue']}; font-weight: bold; font-size: {f_boton_p}pt;
    }}
    QPushButton#PildoraSecPequena:hover {{ background: {hover}; border-color: {t['tenue']}; color: {t['texto']}; }}
    QPushButton#PildoraSecPequena:checked {{
        background: {seleccion}; border-color: {acento}; color: {acento};
    }}
    QPushButton#PildoraPequena:disabled, QPushButton#PildoraSec:disabled,
    QPushButton#PildoraSecPequena:disabled, QPushButton#PildoraRoja:disabled {{
        color: {_rgba(t['tenue'], 0.55)}; border-color: {t['borde']};
    }}

    QPushButton#PildoraRoja {{
        background: transparent; border: 1px solid {_rgba(t['rojo'], 0.55)}; border-radius: 16px;
        padding: 0 14px; color: {t['rojo']}; font-weight: bold; font-size: 9pt;
    }}
    QPushButton#PildoraRoja:hover {{ background: {rojo_fondo}; border-color: {t['rojo']}; }}
    QPushButton#PildoraRoja:pressed {{ background: {_rgba(t['rojo'], 0.22)}; }}

    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {{
        background: {t['tarjeta']}; border: 1px solid {t['borde']};
        border-radius: 10px; padding: 0 12px; min-height: 36px;
        selection-background-color: {acento}; selection-color: #08131a;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 1px solid {acento}; }}
    QLineEdit:read-only {{ color: {t['tenue']}; }}
    QComboBox QAbstractItemView {{
        background: {t['tarjeta']}; border: 1px solid {t['borde']};
        selection-background-color: {seleccion}; selection-color: {t['texto']};
        outline: none;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; }}
    QDateTimeEdit::up-button, QDateTimeEdit::down-button {{ width: 0; border: none; }}
    QDateTimeEdit::drop-down {{ width: 24px; border: none; }}

    QCheckBox {{ color: {t['texto']}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 4px;
        border: 1px solid {t['borde_fuerte']}; background: {t['panel']};
    }}
    QCheckBox::indicator:hover {{ border-color: {acento}; }}
    QCheckBox::indicator:checked {{
        border: none; image: url("{_marca(acento)}");
    }}

    QCalendarWidget QWidget {{ background: {t['tarjeta']}; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {t['tarjeta_alta']}; }}
    QCalendarWidget QToolButton {{
        color: {t['texto']}; background: transparent; border: none;
        font-weight: bold; padding: 4px 8px;
    }}
    QCalendarWidget QToolButton:hover {{ background: {hover}; border-radius: 6px; }}
    QCalendarWidget QSpinBox {{
        background: transparent; border: none; min-height: 20px; color: {t['texto']};
    }}
    QCalendarWidget QAbstractItemView {{
        background: {t['tarjeta']}; color: {t['texto']};
        selection-background-color: {acento}; selection-color: #08131a;
        outline: none;
    }}
    QCalendarWidget QTableView {{
        background: {t['tarjeta']}; alternate-background-color: {t['tarjeta_alta']};
    }}

    QPlainTextEdit {{
        background: {t['tarjeta_alta']}; color: {t['texto']};
        border: 1px solid {t['borde']}; border-radius: 10px;
        font-family: "Consolas"; font-size: 9pt; padding: 8px;
        selection-background-color: {acento}; selection-color: #08131a;
    }}

    QListWidget#ListaProcesos, QListWidget#ListaProgramadas {{
        background: {t['tarjeta_alta']}; border: 1px solid {t['borde']};
        border-radius: 10px; padding: 4px; outline: none;
    }}
    QListWidget#ListaProcesos, QListWidget#ListaProgramadas::item {{
        padding: 4px 6px; border-radius: 6px; min-height: 20px;
    }}
    QListWidget#ListaProcesos, QListWidget#ListaProgramadas::item:hover {{ background: {hover}; }}
    QListWidget#ListaProcesos, QListWidget#ListaProgramadas::indicator {{
        width: 15px; height: 15px; border-radius: 4px;
        border: 1px solid {t['borde_fuerte']}; background: {t['panel']};
    }}
    QListWidget#ListaProcesos, QListWidget#ListaProgramadas::indicator:hover {{ border-color: {acento}; }}
    QListWidget#ListaProcesos, QListWidget#ListaProgramadas::indicator:checked {{
        border: none; image: url("{_marca(acento)}");
    }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {t['fondo']}; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t['borde_fuerte']}; border-radius: 5px; min-height: 32px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QToolTip {{
        background: {t['panel']}; color: {t['texto']};
        border: 1px solid {t['borde']}; padding: 6px;
    }}
    """
