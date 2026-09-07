import json
import logging
from typing import Callable, Awaitable, List, Optional, Sequence
from google.antigravity.triggers import (
    Trigger,
    TriggerContext,
    FileChange,
    FileChangeKind,
    every,
    on_file_change,
)
from core.schemas import NodeVitals

logger = logging.getLogger(__name__)

async def default_vitals_fetcher() -> List[NodeVitals]:
    """Default local fetcher when no custom fetcher is provided."""
    return []

async def check_mesh_health(
    ctx: TriggerContext,
    vitals_fetcher: Optional[Callable[[], Awaitable[List[NodeVitals]]]] = None,
) -> None:
    fetcher = vitals_fetcher or default_vitals_fetcher
    try:
        nodes = await fetcher()
    except Exception as e:
        logger.error(f"Error fetching vitals in watchdog: {e}")
        await ctx.send(f"[WATCHDOG ALERT] Failed to collect mesh vitals: {e}")
        return

    for node in nodes:
        if not node.is_online:
            await ctx.send(
                f"[WATCHDOG ALERT] Node '{node.hostname}' ({node.ip}) is offline! Check mesh tunnel and host status."
            )
        elif node.ram_usage_pct > 90.0:
            await ctx.send(
                f"[WATCHDOG ALERT] Node '{node.hostname}' exceeded critical memory threshold ({node.ram_usage_pct:.1f}%). Diagnostic required."
            )
        elif node.cpu_load_1m > 10.0:
            await ctx.send(
                f"[WATCHDOG ALERT] Node '{node.hostname}' high load average: {node.cpu_load_1m}."
            )

async def handle_config_file_change(
    ctx: TriggerContext,
    changes: Sequence[FileChange],
    reload_callback: Optional[Callable[[Sequence[FileChange]], Awaitable[None]]] = None,
) -> None:
    if reload_callback:
        await reload_callback(changes)
    
    paths_str = ", ".join([str(c.path) for c in changes])
    await ctx.send(f"[CONFIG RELOAD] Config reloaded due to change in {paths_str}")

def create_mesh_watchdog_trigger(
    interval_seconds: float = 60.0,
    vitals_fetcher: Optional[Callable[[], Awaitable[List[NodeVitals]]]] = None,
) -> Trigger:
    return every(interval_seconds, lambda ctx: check_mesh_health(ctx, vitals_fetcher))

def create_config_trigger(
    path: str = "server_facts.json",
    reload_callback: Optional[Callable[[Sequence[FileChange]], Awaitable[None]]] = None,
) -> Trigger:
    return on_file_change(
        path,
        lambda ctx, changes: handle_config_file_change(ctx, changes, reload_callback),
    )
