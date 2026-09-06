import os
import shutil
import socket
from typing import Dict, Any

def get_host_vitals() -> Dict[str, Any]:
    # Hostname
    hostname = socket.gethostname()

    # CPU load average
    try:
        load = os.getloadavg()
        cpu_load = {
            "1m": round(load[0], 2),
            "5m": round(load[1], 2),
            "15m": round(load[2], 2),
        }
    except (OSError, AttributeError):
        cpu_load = {"1m": 0.0, "5m": 0.0, "15m": 0.0}

    # RAM metrics via /proc/meminfo
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
    used_mb = max(0.0, round(total_mb - free_mb, 1))
    used_ram_pct = round((used_mb / total_mb) * 100.0, 1) if total_mb > 0 else 0.0

    ram = {
        "total_mb": total_mb,
        "used_mb": used_mb,
        "free_mb": free_mb,
        "used_pct": used_ram_pct,
    }

    # Disk metrics for root filesystem /
    try:
        du = shutil.disk_usage("/")
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
        "cpu_load": cpu_load,
        "ram": ram,
        "disk": disk,
    }
