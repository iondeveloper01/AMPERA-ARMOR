' NitroTuner - lanza LhmServer.exe OCULTO (sin ventana negra).
' Portable: usa su propia carpeta, asi funciona en cualquier PC/ruta.
' Lo ejecuta la tarea programada "Tuner Sensor" al iniciar sesion.
Option Explicit
Dim fso, sh, base, exe
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
exe  = base & "\tools\LHM\LhmServer.exe"
If fso.FileExists(exe) Then
  ' 0 = ventana oculta, False = no espera a que termine (el servidor sigue corriendo)
  sh.Run """" & exe & """", 0, False
End If
