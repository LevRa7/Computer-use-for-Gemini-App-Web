import pytest
from core.vitals import get_host_vitals

def test_get_host_vitals_structure():
    vitals = get_host_vitals()
    assert isinstance(vitals, dict)
    
    # Hostname check
    assert "hostname" in vitals
    assert isinstance(vitals["hostname"], str)
    assert len(vitals["hostname"]) > 0

    # CPU load check
    assert "cpu_load" in vitals
    assert isinstance(vitals["cpu_load"], dict)
    assert "1m" in vitals["cpu_load"]
    assert "5m" in vitals["cpu_load"]
    assert "15m" in vitals["cpu_load"]
    assert isinstance(vitals["cpu_load"]["1m"], float)

    # RAM check
    assert "ram" in vitals
    ram = vitals["ram"]
    assert isinstance(ram, dict)
    assert ram["total_mb"] > 0
    assert ram["used_mb"] >= 0
    assert ram["free_mb"] >= 0
    assert 0.0 <= ram["used_pct"] <= 100.0

    # Disk check
    assert "disk" in vitals
    disk = vitals["disk"]
    assert isinstance(disk, dict)
    assert disk["total_gb"] > 0
    assert disk["used_gb"] >= 0
    assert disk["free_gb"] >= 0
    assert 0.0 <= disk["used_pct"] <= 100.0
