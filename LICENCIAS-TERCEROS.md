# Licencias de terceros

AMPERA no incluye código propio bajo estas licencias, pero **sí distribuye** las
siguientes herramientas de terceros dentro del instalador
(`AMPERA-Setup-*.exe` → carpeta `tools\`):

| Componente | Licencia | Código fuente | Uso en AMPERA |
|---|---|---|---|
| Libre Hardware Monitor (LHM) 0.9.6 — `LibreHardwareMonitor.exe`, `LibreHardwareMonitorLib.dll` y DLLs dependientes | GPL-3.0 | https://github.com/LibreHardwareMonitor/LibreHardwareMonitor | Lectura de temperatura de CPU. `LhmServer.cs` enlaza `LibreHardwareMonitorLib.dll` para exponer `http://127.0.0.1:8085/data.json`. |
| Mem Reduct 3.5.2 — `memreduct.exe` | GPL-3.0 | https://github.com/henrypp/memreduct | Opcional: limpieza de RAM desde la pestaña Herramientas. |
| PresentMon 2.6.0 — `PresentMon.exe` | MIT | https://github.com/GameTechDev/PresentMon | Medición de FPS y frametime reales (pestaña Overlay de FPS y sesión). Se lanza oculto y sin escribir CSV en disco. |

LHM y Mem Reduct son software libre bajo **GNU GPL v3.0**; PresentMon se distribuye bajo licencia **MIT**. El texto completo de la
licencia está disponible en `https://www.gnu.org/licenses/gpl-3.0.txt` y en sus
repositorios oficiales. Los binarios se redistribuyen sin modificaciones.

## Implicación para `LhmServer.cs`

Como `LhmServer.cs` enlaza dinámicamente una librería GPL-3.0, el binario
resultante (`tools/LHM/LhmServer.exe`) se distribuye bajo los términos de la
GPL-3.0. Su código fuente completo es este mismo repositorio
(`LhmServer.cs`, compilable con `csc.exe` de .NET Framework 4.8).

## Licencia del resto del código de AMPERA

Por definir por el autor (ion). Mientras no se indique lo contrario, el código
propio se publica en este repositorio con fines de revisión y uso personal.

## Marcas

- Los iconos de bandeja/notificaciones usan el nombre **AMPERA**.
- `Acer`, `Nitro V 15`, `NVIDIA`, `RTX`, `Intel` y `MSI Afterburner` se citan
  únicamente como referencia de compatibilidad; son marcas de sus respectivos
  propietarios. AMPERA no está afiliado ni respaldado por ninguno de ellos.
