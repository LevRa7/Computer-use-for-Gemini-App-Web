import pytest
from datetime import datetime
from pydantic import ValidationError

def test_node_vitals_validation():
    from core.schemas import NodeVitals

    vitals = NodeVitals(
        hostname="coordinator",
        ip="127.0.0.1",
        is_online=True,
        cpu_load_1m=0.45,
        ram_used_mb=1024,
        ram_total_mb=4096,
        ram_usage_pct=25.0,
        disk_free_gb=20.5,
        disk_total_gb=50.0,
        uptime="2 days, 4 hours"
    )
    assert vitals.hostname == "coordinator"
    assert vitals.is_online is True
    assert isinstance(vitals.timestamp, datetime)

    # Validate ram_usage_pct range (0 to 100)
    with pytest.raises(ValidationError):
        NodeVitals(
            hostname="test",
            ip="127.0.0.1",
            is_online=True,
            cpu_load_1m=0.1,
            ram_used_mb=100,
            ram_total_mb=100,
            ram_usage_pct=150.0,
            disk_free_gb=10.0,
            disk_total_gb=20.0,
            uptime="1 hour"
        )

def test_tool_execution_result_validation():
    from core.schemas import ToolExecutionResult

    res = ToolExecutionResult(
        command="uptime",
        target_node="coordinator",
        exit_code=0,
        duration_seconds=0.12,
        stdout="load average: 0.10",
        stderr=None,
        is_error=False,
        error_summary=None
    )
    assert res.exit_code == 0
    assert not res.is_error

    err_res = ToolExecutionResult(
        command="bad_cmd",
        target_node="matebook",
        exit_code=127,
        duration_seconds=0.05,
        stdout="",
        stderr="command not found",
        is_error=True,
        error_summary="Command execution failed"
    )
    assert err_res.exit_code == 127
    assert err_res.is_error is True

def test_mesh_health_snapshot_validation():
    from core.schemas import NodeVitals, MeshHealthSnapshot

    node = NodeVitals(
        hostname="coordinator",
        ip="127.0.0.1",
        is_online=True,
        cpu_load_1m=0.2,
        ram_used_mb=500,
        ram_total_mb=2000,
        ram_usage_pct=25.0,
        disk_free_gb=10.0,
        disk_total_gb=20.0,
        uptime="10 mins"
    )

    snapshot = MeshHealthSnapshot(
        overall_healthy=True,
        healthy_node_count=1,
        total_node_count=1,
        nodes=[node],
        warnings=[]
    )
    assert snapshot.overall_healthy is True
    assert len(snapshot.nodes) == 1
    assert isinstance(snapshot.timestamp, datetime)

def test_task_dispatch_plan_validation():
    from core.schemas import TaskDispatchPlan

    plan = TaskDispatchPlan(
        task_id="task-001",
        title="Deploy update",
        target_nodes=["coordinator", "matebook"],
        ordered_steps=["git pull", "pytest"],
        rollback_steps=["git checkout HEAD~1"],
        dry_run_verified=True
    )
    assert plan.task_id == "task-001"
    assert plan.dry_run_verified is True
