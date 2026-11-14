// NitroTuner - Servidor de sensores CPU (sustituye al "Remote Web Server" de LHM).
// Lee la temperatura del CPU con LibreHardwareMonitorLib.dll y la sirve en
// http://127.0.0.1:8085/data.json con la MISMA estructura de arbol que espera
// temp_cpu_lhm() en monitor_fase1.py. Asi NitroTuner tiene temp de CPU sin abrir
// la GUI de LHM ni pulsar "Options -> Remote Web Server -> Run".
//
// Compilar (csc de .NET Framework, C# 5). En Git Bash usa flags con "-":
//   csc -nologo -target:exe -platform:x64 -out:tools\LHM\LhmServer.exe ^
//       -reference:tools\LHM\LibreHardwareMonitorLib.dll LhmServer.cs
//
// Necesita ADMINISTRADOR para leer los sensores del CPU (carga un driver ring0).
// El puerto se enlaza solo a 127.0.0.1/localhost, que no requiere urlacl.

using System;
using System.Globalization;
using System.Net;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

namespace NitroTuner
{
    internal static class LhmServer
    {
        private static Computer _computer;

        private static int Main(string[] args)
        {
            int port = 8085;
            if (args.Length > 0)
            {
                int p;
                if (int.TryParse(args[0], out p) && p > 0 && p < 65536) port = p;
            }

            bool createdNew;
            Mutex mutex = new Mutex(true, "NitroTunerLhmServer", out createdNew);
            if (!createdNew)
            {
                Console.WriteLine("Ya hay una instancia del servidor de sensores en marcha.");
                return 0;
            }

            _computer = new Computer();
            _computer.IsCpuEnabled = true;
            try
            {
                _computer.Open();
            }
            catch (Exception ex)
            {
                Console.WriteLine("AVISO: no pude abrir los sensores del CPU (" + ex.Message + ").");
                Console.WriteLine("       Ejecuta como administrador para obtener la temperatura.");
            }

            HttpListener listener = new HttpListener();
            string p1 = port.ToString(CultureInfo.InvariantCulture);
            listener.Prefixes.Add("http://127.0.0.1:" + p1 + "/");
            listener.Prefixes.Add("http://localhost:" + p1 + "/");
            try
            {
                listener.Start();
            }
            catch (Exception ex)
            {
                Console.WriteLine("ERROR: no pude iniciar el servidor en el puerto " + p1 + ": " + ex.Message);
                try { _computer.Close(); } catch { }
                return 1;
            }

            Console.WriteLine("Servidor de sensores NitroTuner activo en http://127.0.0.1:" + p1 + "/data.json");
            Console.WriteLine("Pulsa Ctrl+C para detenerlo.");

            Console.CancelKeyPress += delegate(object s, ConsoleCancelEventArgs e)
            {
                e.Cancel = true;
                Detener(listener);
            };

            while (listener.IsListening)
            {
                HttpListenerContext ctx;
                try
                {
                    ctx = listener.GetContext();
                }
                catch
                {
                    break;
                }
                try
                {
                    string json = ConstruirJson();
                    byte[] buf = Encoding.UTF8.GetBytes(json);
                    ctx.Response.ContentType = "application/json; charset=utf-8";
                    ctx.Response.ContentLength64 = buf.Length;
                    ctx.Response.OutputStream.Write(buf, 0, buf.Length);
                    ctx.Response.OutputStream.Close();
                }
                catch
                {
                }
            }

            GC.KeepAlive(mutex);
            return 0;
        }

        private static void Detener(HttpListener listener)
        {
            try { listener.Stop(); } catch { }
            try { listener.Close(); } catch { }
            try { if (_computer != null) _computer.Close(); } catch { }
        }

        private static string ConstruirJson()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"Text\":\"Root\",\"Children\":[");
            bool first = true;
            foreach (IHardware hw in _computer.Hardware)
            {
                try { hw.Update(); } catch { }
                if (!first) sb.Append(",");
                first = false;
                AppendHardware(sb, hw);
            }
            sb.Append("]}");
            return sb.ToString();
        }

        private static void AppendHardware(StringBuilder sb, IHardware hw)
        {
            sb.Append("{");
            sb.Append("\"Text\":").Append(Quote(hw.Name)).Append(",");
            sb.Append("\"Type\":").Append(Quote(hw.HardwareType.ToString())).Append(",");
            sb.Append("\"SensorId\":").Append(Quote(hw.Identifier.ToString())).Append(",");
            sb.Append("\"Children\":[");
            bool first = true;
            foreach (ISensor s in hw.Sensors)
            {
                if (!first) sb.Append(",");
                first = false;
                AppendSensor(sb, s);
            }
            foreach (IHardware sub in hw.SubHardware)
            {
                try { sub.Update(); } catch { }
                if (!first) sb.Append(",");
                first = false;
                AppendHardware(sb, sub);
            }
            sb.Append("]}");
        }

        private static void AppendSensor(StringBuilder sb, ISensor s)
        {
            sb.Append("{");
            sb.Append("\"Text\":").Append(Quote(s.Name)).Append(",");
            sb.Append("\"Type\":").Append(Quote(s.SensorType.ToString())).Append(",");
            sb.Append("\"SensorId\":").Append(Quote(s.Identifier.ToString())).Append(",");
            sb.Append("\"Value\":").Append(Quote(FormatValue(s))).Append(",");
            sb.Append("\"Children\":[]");
            sb.Append("}");
        }

        private static string FormatValue(ISensor s)
        {
            if (!s.Value.HasValue) return "";
            string num = s.Value.Value.ToString("0.0", CultureInfo.InvariantCulture);
            string unit;
            switch (s.SensorType)
            {
                case SensorType.Temperature: unit = " \u00B0C"; break;
                case SensorType.Load: unit = " %"; break;
                case SensorType.Clock: unit = " MHz"; break;
                case SensorType.Power: unit = " W"; break;
                case SensorType.Fan: unit = " RPM"; break;
                case SensorType.Voltage: unit = " V"; break;
                case SensorType.Data: unit = " GB"; break;
                case SensorType.SmallData: unit = " MB"; break;
                case SensorType.Throughput: unit = " B/s"; break;
                case SensorType.TimeSpan: unit = " s"; break;
                case SensorType.Energy: unit = " mWh"; break;
                default: unit = ""; break;
            }
            return num + unit;
        }

        private static string Quote(string s)
        {
            if (s == null) return "\"\"";
            StringBuilder sb = new StringBuilder();
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }
    }
}
