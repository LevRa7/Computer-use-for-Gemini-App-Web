import pytest
from core.schemas import NodeVitals

def test_system_vitals_returns_typed_pydantic_json():
    from agy_mcp_server import system_vitals

    out = system_vitals()
    # Must be valid JSON adhering strictly to NodeVitals schema
    vitals = NodeVitals.model_validate_json(out)
    assert vitals.is_online is True
    assert vitals.ram_total_mb > 0
    assert 0.0 <= vitals.ram_usage_pct <= 100.0

def test_core_server_typed_vitals():
    from core.server import get_coordinator_vitals

    vitals = get_coordinator_vitals()
    assert isinstance(vitals, NodeVitals)
    assert vitals.hostname != ""
    assert vitals.is_online is True
