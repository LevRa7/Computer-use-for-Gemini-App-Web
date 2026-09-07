import os
import shutil
import socket
from datetime import datetime, timezone
from core.schemas import NodeVitals

def get_coordinator_vitals() -> NodeVitals:
    hostname = socket.gethostname()
    ip = "127.0.0.1"
    try:
        load = os.getloadavg()[0]
    except Exception:
        load = 0.0

    total_mb = 1
    used_mb = 0
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            if len(parts) == 2:
                k = parts[0].strip()
                v = parts[1].strip().split()[0]
                if k in ["MemTotal", "MemAvailable"]:
                    mem[k] = int(v) // 1024
        if "MemTotal" in mem and "MemAvailable" in mem:
            total_mb = mem["MemTotal"]
            avail_mb = mem["MemAvailable"]
            used_mb = max(0, total_mb - avail_mb)
    except Exception:
        pass

    usage_pct = round((used_mb / total_mb) * 100.0, 2) if total_mb > 0 else 0.0
    usage_pct = min(100.0, max(0.0, usage_pct))

    total_disk_gb = 1.0
    free_disk_gb = 0.0
    try:
        d = shutil.disk_usage("/")
        total_disk_gb = round(d.total / (1024**3), 2)
        free_disk_gb = round(d.free / (1024**3), 2)
    except Exception:
        pass

    uptime_str = "unknown"
    try:
        with open("/proc/uptime", "r") as f:
            up_secs = float(f.read().split()[0])
            hours = int(up_secs // 3600)
            mins = int((up_secs % 3600) // 60)
            uptime_str = f"{hours}h {mins}m"
    except Exception:
        pass

    return NodeVitals(
        hostname=hostname,
        ip=ip,
        is_online=True,
        cpu_load_1m=round(load, 2),
        ram_used_mb=used_mb,
        ram_total_mb=total_mb,
        ram_usage_pct=usage_pct,
        disk_free_gb=free_disk_gb,
        disk_total_gb=total_disk_gb,
        uptime=uptime_str,
        timestamp=datetime.now(timezone.utc),
    )
