import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Dict, Any


def _windows_cpu_usage_pct(sample_seconds: float = 0.1) -> float:
    """Measured CPU utilisation on Windows.

    Windows has no load average, so ``os.getloadavg`` does not exist and the
    previous code reported a hardcoded 0.0. ``GetSystemTimes`` gives idle and
    total processor time, and their delta over a short sample is the utilisation
    every other Windows tool reports.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        def as_int(value: "FILETIME") -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        def sample():
            idle_c, kernel_c, user_c = FILETIME(), FILETIME(), FILETIME()
            ok = kernel32.GetSystemTimes(
                ctypes.byref(idle_c), ctypes.byref(kernel_c), ctypes.byref(user_c)
            )
            if not ok:
                return None
            return as_int(idle_c), as_int(kernel_c), as_int(user_c)

        first = sample()
        if first is None:
            return 0.0
        time.sleep(sample_seconds)
        second = sample()
        if second is None:
            return 0.0

        idle_delta = second[0] - first[0]
        total_delta = (second[1] - first[1]) + (second[2] - first[2])
        if total_delta <= 0:
            return 0.0
        usage = 100.0 * (total_delta - idle_delta) / total_delta
        return round(max(0.0, min(100.0, usage)), 1)
    except Exception:
        return 0.0


def get_host_vitals() -> Dict[str, Any]:
    hostname = socket.gethostname()
    system = platform.system()  # 'Linux', 'Darwin', 'Windows' - and 'Android' on a phone

    # Python on Android (Termux, 3.13+) answers platform.system() == "Android", not
    # "Linux", although it is Linux underneath and /proc is right there. Without this
    # a phone reported "0 MB of RAM" - verified on an OPPO PHY110 running Android 16
    # with Python 3.14, where the memory block came back as 0.0/0.0 MB. The file is
    # the honest signal, so it decides.
    linux_like = system == "Linux" or os.path.isfile("/proc/meminfo")

    # CPU load average (Unix)
    cpu_load = {"1m": 0.0, "5m": 0.0, "15m": 0.0}
    try:
        if hasattr(os, "getloadavg"):
            load = os.getloadavg()
            cpu_load = {
                "1m": round(load[0], 2),
                "5m": round(load[1], 2),
                "15m": round(load[2], 2),
            }
        elif system == "Windows":
            # No load average exists there: report the measured utilisation in all
            # three slots so no consumer reads a fabricated zero.
            usage = _windows_cpu_usage_pct()
            cpu_load = {"1m": usage, "5m": usage, "15m": usage}
    except Exception:
        pass

    # RAM metrics
    total_mb = 0.0
    free_mb = 0.0

    if linux_like:
        total_kb, avail_kb = 0, 0
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    key = parts[0].strip()
                    if key == "MemTotal":
                        total_kb = int(parts[1].split()[0])
                    elif key == "MemAvailable":
                        avail_kb = int(parts[1].split()[0])
        except Exception:
            pass
        total_mb = round(total_kb / 1024, 1)
        free_mb = round(avail_kb / 1024, 1)
    elif system == "Darwin":
        # macOS
        try:
            mem_bytes = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
            total_mb = round(mem_bytes / (1024**2), 1)
            vm = subprocess.check_output(["vm_stat"], text=True)
            page_size = 4096
            free_pages = 0
            for line in vm.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                elif "Pages free:" in line or "Pages speculative:" in line:
                    free_pages += int(line.split()[-1].replace(".", ""))
            free_mb = round((free_pages * page_size) / (1024**2), 1)
        except Exception:
            pass
    elif system == "Windows":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_mb = round(stat.ullTotalPhys / (1024**2), 1)
            free_mb = round(stat.ullAvailPhys / (1024**2), 1)
        except Exception:
            pass

    used_mb = max(0.0, round(total_mb - free_mb, 1))
    used_ram_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0

    ram = {
        "total_mb": total_mb,
        "used_mb": used_mb,
        "free_mb": free_mb,
        "used_pct": used_ram_pct,
    }

    # Disk metrics
    root_path = "C:\\" if system == "Windows" else "/"
    try:
        du = shutil.disk_usage(root_path)
        total_gb = round(du.total / (1024**3), 2)
        used_gb = round(du.used / (1024**3), 2)
        free_gb = round(du.free / (1024**3), 2)
        used_disk_pct = round((du.used / du.total) * 100.0, 1) if du.total > 0 else 0.0
    except Exception:
        total_gb, used_gb, free_gb, used_disk_pct = 0.0, 0.0, 0.0, 0.0

    disk = {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "used_pct": used_disk_pct,
    }

    return {
        "hostname": hostname,
        "os": system,
        "cpu_load": cpu_load,
        "ram": ram,
        "disk": disk,
    }
