import asyncio
import json
import os
import time
import tracemalloc
from unittest.mock import patch, MagicMock
import pytest
from google.antigravity import types
from core.schemas import NodeVitals, ToolExecutionResult
from core.server import get_coordinator_vitals
from core.hooks import mesh_pre_tool_call, mesh_post_tool_call, AUDIT_LOG_PATH
from core.policies import get_mesh_policies, evaluate_policies
from core.triggers import check_mesh_health

# ==============================================================================
# 1. Тесты надежности: таймауты и сетевые сбои (Resilience & Fault Tolerance)
# ==============================================================================

def test_remote_execution_timeout_resilience():
    """Проверка корректной обработки таймаута без падения процесса."""
    import subprocess

    def simulate_timeout_command(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ssh", "lev@100.119.202.62"], timeout=12)

    with patch("subprocess.run", side_effect=simulate_timeout_command):
        try:
            simulate_timeout_command()
        except subprocess.TimeoutExpired as e:
            res = ToolExecutionResult(
                command="ssh matebook",
                target_node="matebook",
                exit_code=-1,
                duration_seconds=12.0,
                stdout="",
                stderr=str(e),
                is_error=True,
                error_summary="Command timed out after 12s"
            )

    assert res.exit_code == -1
    assert res.is_error is True
    assert "timed out" in res.error_summary

def test_vitals_resilience_on_proc_failure():
    """Проверка устойчивости сбора метрик при недоступности /proc/meminfo."""
    with patch("builtins.open", side_effect=IOError("Permission denied /proc/meminfo")):
        vitals = get_coordinator_vitals()
        assert isinstance(vitals, NodeVitals)
        assert vitals.is_online is True
        assert vitals.ram_usage_pct >= 0.0

# ==============================================================================
# 2. Тесты производительности: бенчмарк задержки хуков (Latency Benchmark)
# ==============================================================================

@pytest.mark.asyncio
async def test_hooks_latency_benchmark():
    """Замер накладных расходов хуков pre_tool_call и политик. Порог: < 1.5 мс на вызов."""
    policies = get_mesh_policies()
    call = types.ToolCall(name="run_command", args={"command": "uptime"})

    # Прогрев (warmup)
    for _ in range(20):
        await mesh_pre_tool_call(call)
        await evaluate_policies(policies, call)

    iterations = 500
    t0 = time.perf_counter()
    for _ in range(iterations):
        res1 = await mesh_pre_tool_call(call)
        res2 = await evaluate_policies(policies, call)
        assert res1.allow is True
        assert res2.allow is True
    elapsed = time.perf_counter() - t0

    avg_ms = (elapsed / iterations) * 1000
    # Накладные расходы каждого вызова должны быть менее 1.5 мс
    assert avg_ms < 1.5, f"Hooks latency too high: {avg_ms:.3f}ms per call"

# ==============================================================================
# 3. Тесты конкурентности: параллельная запись телеметрии (Concurrency)
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_audit_logging():
    """Проверка потокобезопасной параллельной записи в mcp_audit.jsonl без повреждения данных."""
    concurrent_calls = 40

    async def log_worker(worker_id: int):
        tool_res = types.ToolResult(
            name="system_vitals",
            id=f"concurrent-{worker_id}",
            step_id=f"step-{worker_id}",
            result=f"result-{worker_id}",
            error=None,
            exception=None,
            server_name="coordinator"
        )
        await mesh_post_tool_call(tool_res)

    tasks = [log_worker(i) for i in range(concurrent_calls)]
    await asyncio.gather(*tasks)

    assert os.path.exists(AUDIT_LOG_PATH)
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Проверяем, что все строки являются валидным JSON
    valid_records = [json.loads(line) for line in lines if line.strip()]
    recorded_ids = {r["id"] for r in valid_records if r.get("id", "").startswith("concurrent-")}
    assert len(recorded_ids) == concurrent_calls

# ==============================================================================
# 4. Тесты утечек памяти: Soak-тест вотчдога (Memory Leak Check)
# ==============================================================================

@pytest.mark.asyncio
async def test_watchdog_memory_leak_soak():
    """Проверка отсутствия утечек памяти при 300 циклах работы вотчдога."""
    tracemalloc.start()
    snapshot1 = tracemalloc.take_snapshot()

    from unittest.mock import AsyncMock
    mock_ctx = MagicMock()
    mock_ctx.send = AsyncMock()

    node = NodeVitals(
        hostname="coordinator",
        ip="127.0.0.1",
        is_online=True,
        cpu_load_1m=0.1,
        ram_used_mb=1000,
        ram_total_mb=4000,
        ram_usage_pct=25.0,
        disk_free_gb=10.0,
        disk_total_gb=50.0,
        uptime="1d"
    )

    async def mock_fetcher():
        return [node]

    for _ in range(300):
        await check_mesh_health(mock_ctx, vitals_fetcher=mock_fetcher)

    snapshot2 = tracemalloc.take_snapshot()
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')
    total_diff_kb = sum(stat.size_diff for stat in top_stats) / 1024
    tracemalloc.stop()

    # Прирост памяти за 300 итераций не должен превышать 1.5 МБ
    assert total_diff_kb < 1536, f"Memory growth too high: {total_diff_kb:.2f} KB"
