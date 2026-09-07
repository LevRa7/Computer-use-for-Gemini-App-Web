from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

class NodeVitals(BaseModel):
    hostname: str
    ip: str
    is_online: bool
    cpu_load_1m: float
    ram_used_mb: int
    ram_total_mb: int
    ram_usage_pct: float
    disk_free_gb: float
    disk_total_gb: float
    uptime: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ram_usage_pct")
    @classmethod
    def validate_ram_usage_pct(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"ram_usage_pct must be between 0.0 and 100.0, got {v}")
        return v

class ToolExecutionResult(BaseModel):
    command: str
    target_node: str
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: Optional[str] = None
    is_error: bool
    error_summary: Optional[str] = None

class MeshHealthSnapshot(BaseModel):
    overall_healthy: bool
    healthy_node_count: int
    total_node_count: int
    nodes: List[NodeVitals]
    warnings: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TaskDispatchPlan(BaseModel):
    task_id: str
    title: str
    target_nodes: List[str]
    ordered_steps: List[str]
    rollback_steps: List[str]
    dry_run_verified: bool = False
