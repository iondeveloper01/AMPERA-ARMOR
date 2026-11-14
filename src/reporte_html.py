"""Generador del reporte HTML de una sesión (reutilizable).

Recibe las muestras (lista de dicts, una por segundo) y los marcadores que el
usuario anota durante la partida, y escribe un .html autocontenido (CSS y SVG
inline, sin dependencias externas) con: cabecera de duración, tabla de
estadísticos (min/máx/media) por métrica, recuento de avisos/críticos,
gráfica de temperatura GPU/CPU y la lista de marcadores.
"""
import html
from datetime import datetime

from monitor_fase1 import (COMMIT_WARN, CPU_CRIT, CPU_WARN, GPU_CRIT, GPU_WARN)

# (clave, etiqueta, unidad, decimales, umbral_aviso, umbral_critico, direccion)
# direccion: "alto" = malo si sube, "bajo" = malo si baja
METRICAS = [
    ("gpu_temp",   "Temp GPU",     "°C",  0, GPU_WARN, GPU_CRIT, "alto"),
    ("cpu_temp",   "Temp CPU",     "°C",  0, CPU_WARN, CPU_CRIT, "alto"),
    ("gpu_pow",    "Potencia GPU", "W",   1, None, None, None),
    ("gpu_clk",    "Clock GPU",    "MHz", 0, None, None, None),
    ("gpu_uso",    "Uso GPU",      "%",   0, None, None, None),
    ("cpu_uso",    "Uso CPU",      "%",   0, None, None, None),
    ("ram_pct",    "RAM usada",    "%",   0, None, None, None),
    ("ram_libre",  "RAM libre",    "GB",  1, None, None, None),
    ("vram",       "VRAM",         "GB",  2, None, None, None),
    ("commit_libre", "Commit libre", "GB", 1, None, COMMIT_WARN, "bajo"),
]

_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;background:#111;color:#e6e6e6;margin:0;padding:24px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px;color:#9ecbff}
.sub{color:#999;font-size:13px;margin-bottom:18px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:right;padding:6px 10px;border-bottom:1px solid #2a2a2a}
th:first-child,td:first-child{text-align:left}
th{color:#bbb;font-weight:600;background:#1a1a1a}
.ok{color:#5fd08a}.warn{color:#e8c15a}.crit{color:#ff6b5e}
.card{background:#181818;border:1px solid #262626;border-radius:10px;padding:14px 16px;margin-bottom:14px}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;margin-right:6px}
.b-ok{background:#173a24;color:#5fd08a}.b-warn{background:#3d3419;color:#e8c15a}
.b-crit{background:#43201c;color:#ff6b5e}
svg{width:100%;height:150px;background:#141414;border-radius:8px}
.legend{font-size:12px;color:#aaa;margin-top:4px}
.marker{border-left:3px solid #9ecbff;padding:2px 0 2px 10px;margin:6px 0;font-size:13px}
.marker b{color:#9ecbff}
"""


def _nums(muestras, clave):
    return [m[clave] for m in muestras if isinstance(m.get(clave), (int, float))]


def _stats(vals, dec):
    if not vals:
        return ("?", "?", "?")
    f = "{:." + str(dec) + "f}"
    return (f.format(min(vals)), f.format(max(vals)), f.format(sum(vals) / len(vals)))


def _clase_temp(v, warn, crit, direccion):
    if v is None or warn is None:
        return "ok"
    if direccion == "alto":
        return "crit" if v >= crit else ("warn" if v >= warn else "ok")
    return "crit" if v <= crit else ("warn" if v <= warn else "ok")


def _svg(muestras, claves_colores):
    """Sparkline SVG simple de varias series (0-100 normalizado por su propio máx)."""
    w, h, pad = 900, 140, 6
    partes = [f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none">']
    n = len(muestras)
    for clave, color in claves_colores:
        vals = [m.get(clave) for m in muestras]
        nums = [v for v in vals if isinstance(v, (int, float))]
        if not nums:
            continue
        mx, mn = max(nums), min(nums)
        rng = (mx - mn) or 1
        pts = []
        for i, v in enumerate(vals):
            if not isinstance(v, (int, float)):
                continue
            x = pad + (w - 2 * pad) * (i / max(1, n - 1))
            y = h - pad - (h - 2 * pad) * ((v - mn) / rng)
            pts.append(f"{x:.1f},{y:.1f}")
        if len(pts) >= 2:
            partes.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" '
                          f'points="{" ".join(pts)}"/>')
    partes.append("</svg>")
    return "".join(partes)


def _downsample(muestras, max_pts=700):
    if len(muestras) <= max_pts:
        return muestras
    paso = len(muestras) / max_pts
    return [muestras[int(i * paso)] for i in range(max_pts)]


def generar(muestras, marcadores, ruta, titulo="Sesión de juego"):
    """Escribe el reporte HTML en `ruta`. Devuelve la ruta."""
    inicio = muestras[0]["t"] if muestras else datetime.now().strftime("%H:%M:%S")
    fin = muestras[-1]["t"] if muestras else inicio
    n = len(muestras)

    # recuento de niveles
    avisos = sum(1 for m in muestras if m.get("nivel") == 1)
    crits = sum(1 for m in muestras if m.get("nivel") == 2)
    picos = {}
    for clave, *_ in METRICAS:
        vals = _nums(muestras, clave)
        picos[clave] = max(vals) if vals else None

    filas = []
    for clave, etik, unidad, dec, warn, crit, direccion in METRICAS:
        vals = _nums(muestras, clave)
        mn, mx, md = _stats(vals, dec)
        # clase del máximo (o mínimo si es "bajo")
        representante = None
        if vals:
            representante = (min(vals) if direccion == "bajo" else max(vals))
        cls = _clase_temp(representante, warn, crit, direccion) if vals else "ok"
        filas.append(f"<tr><td>{etik}</td><td class='{cls}'>{mx}</td>"
                     f"<td>{md}</td><td>{mn}</td><td>{unidad}</td></tr>")

    marc_html = ""
    if marcadores:
        marc_html = "".join(
            f'<div class="marker"><b>{html.escape(m["t"])}</b> — {html.escape(m["texto"])}</div>'
            for m in marcadores)
    else:
        marc_html = '<div class="sub">Sin marcadores (pulsa Shift+M durante la partida para anotar un momento).</div>'

    badge_resumen = ('<span class="badge b-ok">sin avisos</span>' if (avisos + crits) == 0
                     else f'<span class="badge b-warn">{avisos} avisos</span>'
                          f'<span class="badge b-crit">{crits} críticos</span>')

    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>{html.escape(titulo)}</title><style>{_CSS}</style></head><body>
<h1>{html.escape(titulo)}</h1>
<div class="sub">Inicio {html.escape(str(inicio))} · Fin {html.escape(str(fin))} · {n} muestras (≈{n} s) · {badge_resumen}</div>

<div class="card">
<h2>Estadísticos por métrica</h2>
<table><thead><tr><th>Métrica</th><th>Máx</th><th>Media</th><th>Mín</th><th>Unidad</th></tr></thead>
<tbody>{''.join(filas)}</tbody></table>
</div>

<div class="card">
<h2>Temperaturas durante la sesión</h2>
{_svg(_downsample(muestras), [("gpu_temp", "#5fd08a"), ("cpu_temp", "#e8c15a")])}
<div class="legend">Verde = GPU · Amarillo = CPU (escala relativa a su propio rango).</div>
</div>

<div class="card">
<h2>Marcadores ({len(marcadores)})</h2>
{marc_html}
</div>

<div class="sub">Generado por NitroTuner el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.</div>
</body></html>"""

    with open(ruta, "w", encoding="utf-8") as f:
        f.write(doc)
    return ruta
