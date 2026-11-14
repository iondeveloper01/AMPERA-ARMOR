import sys
import time

seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
end = time.perf_counter() + seconds
x = 0
while time.perf_counter() < end:
    for _ in range(20000):
        x = (x * 1103515245 + 12345) % 2147483648
print("carga de 1 nucleo terminada")
