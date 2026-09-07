#!/usr/bin/env python3
"""
subagent_dispatcher.py - Multi-Device Subagent Orchestrator for Antigravity & Web-Gemini.
Dispatches subagent tasks to:
1. VPS Local Subagent (gemini-3.8-flash-high) - for research, logs, monitoring, fast audits.
2. Matebook16 Remote Subagent (gemini-3.1-pro-high / claude-sonnet-4-6) - for development, tests, coding.
Operates without any custom harness on the remote device, leveraging native SSH and agy daemon.
"""

import os
import sys
import json
import time
import secrets
import logging
import threading
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "subagent_dispatcher.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("subagent_dispatcher")

AGY_BIN = "/root/.local/bin/agy"
MATEBOOK_IP = "100.119.202.62"
MATEBOOK_USER = "lev"
MATEBOOK_PASS = "ST720p"

# Subagent Tasks Storage
tasks_lock = threading.Lock()
subagent_tasks = {}

def run_vps_subagent(prompt: str, model: str = "gemini-3.8-flash-high", timeout: int = 120) -> dict:
    """Runs a fast researcher subagent locally on the VPS."""
    t0 = time.time()
    logger.info(f"Spawning VPS Researcher Subagent [{model}]: {prompt[:80]}...")

    cmd = [
        AGY_BIN,
        "-p", prompt,
        "--model", model,
        "--agent", "research",
        "--dangerously-skip-permissions"
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR)
        )
        duration = round(time.time() - t0, 2)
        return {
            "status": "success" if res.returncode == 0 else "failed",
            "target": "vps",
            "role": "researcher",
            "model": model,
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "duration_seconds": duration
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "target": "vps",
            "role": "researcher",
            "model": model,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Subagent execution timed out after {timeout} seconds.",
            "duration_seconds": round(time.time() - t0, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "target": "vps",
            "role": "researcher",
            "model": model,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(e),
            "duration_seconds": round(time.time() - t0, 2)
        }


def run_matebook_subagent(prompt_or_cmd: str, model: str = "gemini-3.1-pro-high", mode: str = "auto", timeout: int = 180) -> dict:
    """Runs a development / test / coding task on Matebook16 via Tailscale SSH."""
    t0 = time.time()
    logger.info(f"Spawning Matebook16 Dev Subagent: {prompt_or_cmd[:80]}...")

    path_prefix = 'export PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:$PATH"; '
    
    # Determine if raw command or agy prompt
    is_raw = False
    if mode == "command":
        is_raw = True
    elif mode == "agy":
        is_raw = False
    else:
        # Auto-detect
        cmd_prefixes = ("git ", "npm ", "pytest", "python", "cargo ", "docker ", "cat ", "ls ", "ps ", "systemctl ", "journalctl ", "which ", "curl ", "df ", "free ", "uname", "hostname", "echo ", "mkdir ", "rm ", "cp ", "mv ", "grep ", "find ", "export ")
        first_token = prompt_or_cmd.strip().split()[0] if prompt_or_cmd.strip() else ""
        if any(prompt_or_cmd.strip().startswith(p) for p in cmd_prefixes) or first_token in ["hostname", "uptime", "free", "df", "uname", "pwd", "whoami", "top", "htop"] or " | " in prompt_or_cmd or " && " in prompt_or_cmd or " || " in prompt_or_cmd:
            is_raw = True

    if is_raw:
        remote_cmd = f"{path_prefix}{prompt_or_cmd}"
    else:
        escaped_prompt = json.dumps(prompt_or_cmd)
        remote_cmd = f"{path_prefix}agy -p {escaped_prompt} --model {model} --dangerously-skip-permissions"

    ssh_cmd = [
        "sshpass", "-p", MATEBOOK_PASS,
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"{MATEBOOK_USER}@{MATEBOOK_IP}",
        remote_cmd
    ]

    try:
        res = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        duration = round(time.time() - t0, 2)
        return {
            "status": "success" if res.returncode == 0 else "failed",
            "target": "matebook",
            "role": "coder",
            "model": model,
            "mode": "command" if is_raw else "agy",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "duration_seconds": duration
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "target": "matebook",
            "role": "coder",
            "model": model,
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Remote subagent execution timed out after {timeout} seconds.",
            "duration_seconds": round(time.time() - t0, 2)
        }
    except Exception as e:
        return {
            "status": "error",
            "target": "matebook",
            "role": "coder",
            "model": model,
            "exit_code": 1,
            "stdout": "",
            "stderr": str(e),
            "duration_seconds": round(time.time() - t0, 2)
        }


def spawn_subagent_task(target: str, prompt: str, role: str = "auto", model: str = None, mode: str = "auto", timeout: int = 180, is_async: bool = False) -> dict:
    """Dispatches a task to the appropriate device subagent."""
    # Resolve target and role
    target = target.lower().strip() if target else "auto"
    if target in ["vps", "local", "research", "fast"]:
        actual_target = "vps"
        actual_role = "researcher"
        actual_model = model or "gemini-3.8-flash-high"
    elif target in ["matebook", "remote", "coder", "dev", "laptop"]:
        actual_target = "matebook"
        actual_role = "coder"
        actual_model = model or "gemini-3.1-pro-high"
    else:
        # Default based on role
        if role == "coder":
            actual_target = "matebook"
            actual_role = "coder"
            actual_model = model or "gemini-3.1-pro-high"
        else:
            actual_target = "vps"
            actual_role = "researcher"
            actual_model = model or "gemini-3.8-flash-high"

    if not is_async:
        if actual_target == "vps":
            return run_vps_subagent(prompt, actual_model, timeout)
        else:
            return run_matebook_subagent(prompt, actual_model, mode=mode, timeout=timeout)

    # Async task creation
    task_id = f"sub_{int(time.time())}_{secrets.token_hex(3)}"
    task_info = {
        "id": task_id,
        "target": actual_target,
        "role": actual_role,
        "model": actual_model,
        "prompt": prompt,
        "status": "running",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "stdout": "",
        "stderr": "",
        "exit_code": None
    }
    with tasks_lock:
        subagent_tasks[task_id] = task_info

    def run_worker():
        if actual_target == "vps":
            res = run_vps_subagent(prompt, actual_model, timeout)
        else:
            res = run_matebook_subagent(prompt, actual_model, mode=mode, timeout=timeout)
        with tasks_lock:
            subagent_tasks[task_id].update({
                "status": "completed" if res["exit_code"] == 0 else "failed",
                "end_time": datetime.now(timezone.utc).isoformat(),
                "stdout": res["stdout"],
                "stderr": res["stderr"],
                "exit_code": res["exit_code"],
                "duration_seconds": res["duration_seconds"]
            })

    threading.Thread(target=run_worker, daemon=True).start()
    return {
        "status": "started",
        "task_id": task_id,
        "target": actual_target,
        "role": actual_role,
        "model": actual_model,
        "poll_url": f"/api/agy/subagent/status?id={task_id}"
    }

def get_subagent_status(task_id: str) -> dict:
    with tasks_lock:
        return subagent_tasks.get(task_id)

def list_subagents() -> list:
    with tasks_lock:
        return list(subagent_tasks.values())
