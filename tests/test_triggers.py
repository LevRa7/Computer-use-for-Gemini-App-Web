import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from google.antigravity.triggers import TriggerContext, FileChange, FileChangeKind
from core.schemas import NodeVitals

@pytest.mark.asyncio
async def test_mesh_watchdog_detects_node_failure():
    from core.triggers import check_mesh_health

    ctx = MagicMock(spec=TriggerContext)
    ctx.send = AsyncMock()

    failed_node = NodeVitals(
        hostname="matebook",
        ip="192.168.1.50",
        is_online=False,
        cpu_load_1m=0.0,
        ram_used_mb=0,
        ram_total_mb=16000,
        ram_usage_pct=0.0,
        disk_free_gb=0.0,
        disk_total_gb=512.0,
        uptime="offline"
    )

    mock_fetcher = AsyncMock(return_value=[failed_node])
    await check_mesh_health(ctx, vitals_fetcher=mock_fetcher)

    ctx.send.assert_called_once()
    alert_msg = ctx.send.call_args[0][0]
    assert "[WATCHDOG ALERT]" in alert_msg
    assert "matebook" in alert_msg

@pytest.mark.asyncio
async def test_mesh_watchdog_healthy_silent():
    from core.triggers import check_mesh_health

    ctx = MagicMock(spec=TriggerContext)
    ctx.send = AsyncMock()

    healthy_node = NodeVitals(
        hostname="coordinator",
        ip="127.0.0.1",
        is_online=True,
        cpu_load_1m=0.2,
        ram_used_mb=1000,
        ram_total_mb=4000,
        ram_usage_pct=25.0,
        disk_free_gb=20.0,
        disk_total_gb=50.0,
        uptime="1 day"
    )

    mock_fetcher = AsyncMock(return_value=[healthy_node])
    await check_mesh_health(ctx, vitals_fetcher=mock_fetcher)

    ctx.send.assert_not_called()

@pytest.mark.asyncio
async def test_config_trigger_reloads_facts():
    from core.triggers import handle_config_file_change

    ctx = MagicMock(spec=TriggerContext)
    ctx.send = AsyncMock()
    mock_reload = AsyncMock()

    changes = [
        FileChange(kind=FileChangeKind.MODIFIED, path="/root/agy-gdrive-runner/server_facts.json")
    ]

    await handle_config_file_change(ctx, changes, reload_callback=mock_reload)

    mock_reload.assert_called_once_with(changes)
    ctx.send.assert_called_once()
    assert "[CONFIG RELOAD]" in ctx.send.call_args[0][0]
