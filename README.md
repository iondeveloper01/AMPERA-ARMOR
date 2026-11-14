# AMPERA

Herramienta de ajuste y monitorización para **Windows 11**, pensada primero para
portátiles gaming (Acer Nitro y similares). Perfiles de energía, techo de CPU,
temperaturas, overlay de FPS, limpieza de RAM y guardia térmica, todo desde una
interfaz gráfica en **20 idiomas**.

> Proyecto personal de [iondeveloper01](https://github.com/iondeveloper01).
> Sin telemetría, sin cuentas y sin conexiones a Internet.
>
> © 2026 ion-developer · Código propio bajo licencia **MIT** (ver [LICENSE](LICENSE)).

---

## Descargar

**[Última versión → Releases](https://github.com/iondeveloper01/AMPERA-ARMOR/releases/latest)**

| Archivo | Versión | Tamaño |
|---|---|---|
| `AMPERA-Setup-1.1.0.exe` | 1.1.0 | 55,1 MB |

SHA-256 del instalador 1.1.0:

```
4cb3984e85ff819724fe223b8f97a38e579e8c87c3ff783b94db9a28ec648a3a
```

Para comprobarlo en Windows (PowerShell):

```powershell
Get-FileHash .\AMPERA-Setup-1.1.0.exe -Algorithm SHA256
```

### Instalar

1. Descarga el `.exe` desde *Releases*.
2. Ejecútalo y acepta el aviso de Windows (UAC).
3. Abre **AMPERA** desde el menú Inicio o el acceso directo del escritorio.

Los binarios **no están firmados** todavía, así que SmartScreen puede mostrar
«Windows protegió tu PC». Pulsa *Más información → Ejecutar de todas formas*
solo si el hash coincide con el de arriba.

## Requisitos

- Windows 11 de 64 bits (probado en 26H2, build 26300). Windows 10 no está
  soportado.
- Permiso de administrador para leer temperaturas de CPU y para medir FPS
  reales. Sin esos permisos, AMPERA funciona y simplemente oculta esas
  lecturas.
- GPU NVIDIA para las lecturas de GPU (NVML). Con Intel/AMD-only la GUI arranca,
  pero las métricas de GPU no están disponibles en esta versión.

## Qué hay dentro

- **Resumen** — estado del equipo en vivo (CPU, GPU, RAM, temperaturas, batería)
  con frecuencia de sondeo ajustable: 2 s de fábrica, hasta 30 s para equipos
  lentos.
- **Perfiles** — planes de energía de Windows + techo de CPU, con perfil de
  fábrica y certificación de perfiles.
- **Herramientas** — overlay de FPS, limpieza de RAM (normal/profunda/
  automática), aviso térmico con 3 avisos y frenado automático, watchdog,
  monitor y análisis de procesos en segundo plano.
- **Recomendaciones** — detecta si el equipo es portátil, torre, mini PC o
  servidor y propone (o crea) el perfil adecuado.
- **Centro de control** — acciones programadas por fecha/hora o diarias,
  autodiagnóstico de solo lectura y optimización de batería.
- **Apariencia** — tema claro/oscuro, color de acento, tamaño de letra e idioma.

### Novedades de la 1.1.0

1. Overlay rediseñado, con 9 posiciones elegibles y arrastre en sesión
   (`Ctrl+Shift+L`).
2. FPS y frametime **reales** con PresentMon (antes eran estimaciones).
3. Limpieza de RAM integrada en la GUI, con modo automático por intervalo y
   umbral de memoria libre.
4. Aviso térmico: umbral entre 75 y 95 °C, tres avisos y frenado del techo de
   CPU que **nunca sube** por encima del límite que tú fijaste.
5. Pestañas nuevas *Recomendaciones* y *Centro de control*.
6. Sondeo de Estado configurable y optimización de procesos en segundo plano
   (con lista blanca y protección de servicios y ventanas visibles).
7. 20 idiomas completos, árabe y urdu en RTL.

## Código fuente

El código de AMPERA está en [`src/`](src/), bajo licencia MIT. Instrucciones para
ejecutarlo y compilarlo: [`src/README.md`](src/README.md).

## Privacidad y seguridad

- Todo se guarda en local, en `%LOCALAPPDATA%\AMPERA`. El instalador no incluye
  perfiles ni configuración de nadie.
- No hay telemetría ni tráfico saliente. El único servidor que se abre es el de
  sensores, y escucha **solo** en `127.0.0.1:8085`.
- Sin `shell=True`, `eval` ni deserialización insegura; escrituras atómicas con
  cuarentena para JSON dañados y HTML escapado en los reportes.
- La optimización de procesos nunca toca servicios del sistema, rutas de
  `C:\Windows\` ni procesos con ventana visible, y siempre pide confirmación.

## Componentes de terceros

El instalador redistribuye, sin modificar, estas herramientas en `tools\`:

| Componente | Licencia | Para qué |
|---|---|---|
| Libre Hardware Monitor 0.9.6 | GPL-3.0 | Temperatura de CPU |
| Mem Reduct 3.5.2 | GPL-3.0 | Limpieza de RAM |
| PresentMon 2.6.0 | MIT | FPS y frametime reales |

Detalles y enlaces al código fuente en
[LICENCIAS-TERCEROS.md](LICENCIAS-TERCEROS.md). El código de `LhmServer.exe`
(el puente GPL-3.0 con LibreHardwareMonitorLib) está en
[`LhmServer.cs`](LhmServer.cs).

## Limitaciones conocidas

- Los FPS reales necesitan una sesión elevada y un juego en primer plano; la
  validación en juego real está pendiente.
- Sin firma Authenticode: SmartScreen avisará en la primera ejecución.
- Sin GPU NVIDIA no hay métricas de GPU.
- La detección de torre/mini/servidor se ha probado con casos sintéticos, no
  con hardware real de esos tipos.

## Licencia

El código propio de AMPERA es **MIT**, © 2026 **ion-developer**. Consulta el texto
completo en [LICENSE](LICENSE). Los componentes de terceros conservan sus licencias
(ver arriba). `LhmServer.cs` enlaza `LibreHardwareMonitorLib.dll` (GPL-3.0), por lo
que ese puente se distribuye bajo GPL-3.0 con su fuente en este repositorio.

Este software se proporciona «tal cual», sin garantía. Ajustar potencia y
temperaturas conlleva riesgo; úsalo bajo tu responsabilidad.