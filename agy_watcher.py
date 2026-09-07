#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")

"""
agy_watcher.py - Real-time task executor supporting direct Bash commands and Antigravity CLI.
Updates tasks in Google Drive in real-time with Markdown status indicators:
  ### ⏳ Выполняется: (running command)
  ### ✅ Выполнено: (completed commands and terminal output)
"""

import sys
import os
import re
import json
import time
import signal
import shutil
import logging
import argparse
import subprocess
import select
from datetime import datetime, timezone
from pathlib import Path

from gdrive_client import GDriveClient, DEFAULT_FILE_ID, GEMINI_REMOTE_FOLDER_ID

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "agy_watcher.log"
METRICS_FILE = BASE_DIR / "task_metrics.json"
HISTORY_LOG_FILE = BASE_DIR / "task_history.log"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("agy_watcher")

RUNNING = True

def signal_handler(signum, frame):
    global RUNNING
    logger.info(f"Received termination signal ({signum}). Stopping watcher...")
    RUNNING = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def record_task_metrics(metric: dict):
    """Saves granular execution timing and command details to log and JSON metrics."""
    try:
        cmds_str = "\n".join(
            f"    {i+1}. [rc={c.get('exit_code', 0)}] ({c.get('duration_seconds', 0)}s): {c.get('command')}"
            for i, c in enumerate(metric.get("commands", []))
        )
        queue_latency_str = f"{metric.get('queue_latency_seconds')}s" if metric.get('queue_latency_seconds') is not None else "N/A"
        log_entry = (
            f"[{metric.get('finished_at', '')}] TASK: {metric.get('task_id')} | STATUS: {metric.get('status')} | RC: {metric.get('exit_code')}\n"
            f"  - Queue Pickup Latency: {queue_latency_str}\n"
            f"  - Execution Duration:   {metric.get('exec_duration_seconds', 0)}s\n"
            f"  - Drive Upload Time:    {metric.get('upload_duration_seconds', 0)}s\n"
            f"  - Total Runner Time:    {metric.get('total_runner_time_seconds', 0)}s\n"
            f"  - Output Size:          {metric.get('output_length_chars', 0)} chars\n"
            f"  - Commands Executed:\n{cmds_str}\n"
            + "=" * 80 + "\n"
        )
        with open(HISTORY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        logger.warning(f"Failed to append to task_history.log: {e}")

    try:
        records = []
        if METRICS_FILE.exists():
            try:
                with open(METRICS_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
                    if not isinstance(records, list):
                        records = []
            except Exception:
                records = []
        records.insert(0, metric)
        records = records[:100]
        with open(METRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to update task_metrics.json: {e}")

def find_agy_binary() -> str:
    which_path = shutil.which("agy")
    if which_path:
        return which_path

    candidate_paths = [
        Path.home() / ".local" / "bin" / "agy",
        Path("/root/.local/bin/agy"),
        Path.home() / ".gemini" / "antigravity-cli" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/usr/bin/agy")
    ]
    for p in candidate_paths:
        if p.exists() and os.access(p, os.X_OK):
            return str(p)

    return "/root/.local/bin/agy"

def is_shell_command(text: str) -> bool:
    """Heuristic to detect if input is a direct shell command or natural language."""
    text = text.strip()
    if text.startswith(("sh:", "bash:", "cmd:", "$")):
        return True
    
    # Common command prefixes
    first_word = text.split()[0] if text.split() else ""
    common_cmds = {
        "ls", "pwd", "cd", "cat", "echo", "df", "free", "uptime", "ps", "top", "htop",
        "systemctl", "journalctl", "git", "docker", "rclone", "curl", "wget", "grep",
        "find", "mkdir", "rm", "cp", "mv", "touch", "tail", "head", "uname", "whoami",
        "netstat", "ss", "ip", "ifconfig", "python", "python3", "node", "npm", "bun",
        "ssh", "sshpass", "scp", "tar", "gzip", "unzip", "export", "sudo"
    }
    if first_word in common_cmds:
        return True

    # Has shell operators
    if any(op in text for op in [" && ", " || ", " | ", " ; ", " > ", " >> ", " 2>&1", "<<"]):
        return True

    return False

def build_progress_tags(completed_entries: list, current_running: str = None, live_url: str = None) -> str:
    """Builds the completed and running markdown blocks without HTML tags."""
    parts = []
    if live_url:
        parts.append(f"🔗 **Live трансляция / лог:** [{live_url}]({live_url})")

    if completed_entries:
        comp_body = "\n\n".join(
            f"$ {e['command']}\n{e['output'].strip()}" for e in completed_entries
        )
        parts.append(f"### ✅ Выполнено:\n```bash\n{comp_body}\n```")

    if current_running:
        parts.append(f"### ⏳ Выполняется:\n```bash\n$ {current_running}\n```")

    return "\n\n".join(parts) if parts else ""

def execute_shell_commands(
    commands: list[str],
    task: dict,
    data: dict,
    file_id: str,
    client: GDriveClient,
    sync_intermediate: bool = False,
    live_log_file: Path = None,
    live_url: str = None
) -> tuple[int, str, float, list[dict]]:
    """Executes a list of bash commands sequentially, streaming live lines to live_log_file."""
    start_total = time.time()
    completed_entries = []
    overall_exit_code = 0
    all_outputs = []

    for cmd in commands:
        cmd_clean = cmd.strip()
        if not cmd_clean:
            continue

        logger.info(f"Starting execution of: {cmd_clean}")
        task["running_command"] = cmd_clean
        task["log"] = build_progress_tags(completed_entries, current_running=cmd_clean, live_url=live_url)

        if live_log_file:
            try:
                with open(live_log_file, "a", encoding="utf-8") as lf:
                    lf.write(f"\n$ {cmd_clean}\n")
                    lf.flush()
            except Exception:
                pass

        if sync_intermediate:
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            data["version"] = data.get("version", 0) + 1
            try:
                client.update_queue(file_id, data)
            except Exception as e:
                logger.warning(f"Live update push failed: {e}")

        cmd_start = time.time()
        cmd_out_lines = []
        timeout_seconds = 120.0
        cmd_rc = -1
        try:
            proc = subprocess.Popen(
                ["bash", "-c", cmd_clean],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            poll = select.poll()
            poll.register(proc.stdout, select.POLLIN | select.POLLHUP)

            while True:
                if time.time() - cmd_start > timeout_seconds:
                    proc.kill()
                    proc.wait()
                    cmd_out_lines.append(f"\nCommand timed out after {int(timeout_seconds)} seconds.")
                    cmd_rc = -1
                    break

                events = poll.poll(500)
                if events:
                    line = proc.stdout.readline()
                    if line:
                        cmd_out_lines.append(line)
                        if live_log_file:
                            try:
                                with open(live_log_file, "a", encoding="utf-8") as lf:
                                    lf.write(line)
                                    lf.flush()
                            except Exception:
                                pass
                    elif proc.poll() is not None:
                        break
                elif proc.poll() is not None:
                    break

            try:
                remaining = proc.stdout.read()
                if remaining:
                    cmd_out_lines.append(remaining)
            except Exception:
                pass

            if proc.returncode is not None:
                cmd_rc = proc.returncode
            cmd_duration = round(time.time() - cmd_start, 2)
            cmd_out = "".join(cmd_out_lines)
        except Exception as err:
            cmd_duration = round(time.time() - cmd_start, 2)
            cmd_out = f"Execution exception: {err}"
            cmd_rc = -1

        entry = {
            "command": cmd_clean,
            "exit_code": cmd_rc,
            "duration": cmd_duration,
            "output": cmd_out
        }
        completed_entries.append(entry)
        all_outputs.append(f"$ {cmd_clean}\n{cmd_out.strip()}")

        if cmd_rc != 0:
            if overall_exit_code == 0:
                overall_exit_code = cmd_rc
            logger.error(f"Command '{cmd_clean}' failed with code {cmd_rc}. Halting remaining commands.")
            if live_log_file:
                try:
                    with open(live_log_file, "a", encoding="utf-8") as lf:
                        lf.write(f"\n[ERROR GATE]: Command failed with exit code {cmd_rc}. Execution halted.\n")
                        lf.flush()
                except Exception:
                    pass
            break

        logger.info(f"Command '{cmd_clean}' completed with code {cmd_rc} in {cmd_duration}s.")

    total_duration = round(time.time() - start_total, 2)
    task["running_command"] = None
    task["completed_commands"] = completed_entries
    task["log"] = build_progress_tags(completed_entries, current_running=None, live_url=live_url)
    combined_output = "\n\n".join(all_outputs)
    return overall_exit_code, combined_output, total_duration, completed_entries

def execute_agy_prompt(
    prompt: str,
    agy_bin: str,
    task: dict,
    data: dict,
    file_id: str,
    client: GDriveClient,
    live_log_file: Path = None,
    live_url: str = None
) -> tuple[int, str, float]:
    """Executes a prompt via agy CLI, streaming steps into Markdown progress tags."""
    start_total = time.time()
    completed_steps = []
    current_step_name = f"agy -p \"{prompt[:60]}...\""
    task["running_command"] = current_step_name
    task["log"] = build_progress_tags(completed_steps, current_running=current_step_name, live_url=live_url)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["version"] = data.get("version", 0) + 1

    try:
        client.update_queue(file_id, data)
    except Exception as e:
        logger.warning(f"Initial live update push failed: {e}")

    if live_log_file:
        try:
            with open(live_log_file, "a", encoding="utf-8") as lf:
                lf.write(f"\n[AGY Agent Task]: {prompt}\n\n")
                lf.flush()
        except Exception:
            pass

    cmd = [
        agy_bin,
        "-p", prompt,
        "--output-format", "stream-json",
        "--dangerously-skip-permissions"
    ]
    last_push = time.time()
    final_response = ""

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if not line:
                time.sleep(0.1)
                continue

            line_str = line.strip()
            if not line_str:
                continue

            if live_log_file:
                try:
                    with open(live_log_file, "a", encoding="utf-8") as lf:
                        lf.write(line_str + "\n")
                        lf.flush()
                except Exception:
                    pass

            try:
                event_data = json.loads(line_str)
                event = event_data.get("event")

                if event == "step_update":
                    step = event_data.get("step_update", {})
                    tool_name = step.get("tool_name")
                    if tool_name:
                        tool_info = step.get("tool_info", {})
                        step_desc = f"{tool_name} {json.dumps(tool_info, ensure_ascii=False)[:80]}"
                        task["running_command"] = step_desc
                        task["log"] = build_progress_tags(completed_steps, current_running=step_desc, live_url=live_url)

                        now = time.time()
                        if now - last_push >= 3.0:
                            data["updated_at"] = datetime.now(timezone.utc).isoformat()
                            try:
                                client.update_queue(file_id, data)
                                last_push = now
                            except Exception:
                                pass
                elif event == "result":
                    res = event_data.get("result", {})
                    final_response = res.get("response", "")
            except json.JSONDecodeError:
                pass

        proc.wait(timeout=10)
        total_duration = round(time.time() - start_total, 2)
        rc = proc.returncode

        output_text = final_response if final_response else "(Выполнено)"
        completed_steps.append({
            "command": f"agy: {prompt}",
            "exit_code": rc,
            "duration": total_duration,
            "output": output_text
        })
        task["running_command"] = None
        task["completed_commands"] = completed_steps
        task["log"] = build_progress_tags(completed_steps, current_running=None, live_url=live_url)
        return rc, output_text, total_duration
    except Exception as err:
        total_duration = round(time.time() - start_total, 2)
        task["running_command"] = None
        task["log"] = f"### ❌ Ошибка:\n🔗 Live лог: {live_url}\n```\n{err}\n```"
        return -1, str(err), total_duration

def process_single_task(task: dict, data: dict, file_id: str, agy_bin: str, client: GDriveClient):
    t_start = time.time()
    task_id = task.get("id", f"task_{int(t_start)}")
    logger.info(f"=== Processing task [{task_id}] ===")

    # Setup live logs and public response URL
    base_dir = Path(__file__).resolve().parent
    live_dir = base_dir / "live_logs"
    live_dir.mkdir(parents=True, exist_ok=True)
    live_log_file = live_dir / f"{task_id}.log"
    live_meta_file = live_dir / f"{task_id}.json"
    live_url = f"https://levra7-ai.mooo.com/api/agy/live/{task_id}"

    created_at_str = task.get("created_at")
    queue_latency = None
    if created_at_str:
        try:
            c_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            queue_latency = round(t_start - c_dt.timestamp(), 2)
        except Exception:
            pass
    elif "_" in task_id:
        part = task_id.split("_")[-1]
        if part.isdigit() and len(part) >= 10:
            try:
                c_epoch = int(part[:10])
                queue_latency = round(t_start - c_epoch, 2)
            except Exception:
                pass

    # Determine command list before initial ack
    commands = task.get("commands")
    if not commands:
        cmd_field = task.get("command") or task.get("prompt", "")
        if isinstance(cmd_field, list):
            commands = cmd_field
        elif task.get("type") == "command" or is_shell_command(cmd_field):
            if isinstance(cmd_field, str):
                cmd_str = cmd_field.strip()
                if "\n" in cmd_str:
                    commands = [cmd_str]
                else:
                    commands = [cmd_str.lstrip("$ ")]
            else:
                commands = [str(cmd_field)]
        else:
            commands = None

    # Initialize live log and metadata file on disk
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with open(live_log_file, "w", encoding="utf-8") as lf:
            lf.write(f"=== [Task {task_id}] Started at {now_iso} ===\n")
            if commands:
                lf.write("Commands to execute:\n" + "\n".join(f"  $ {c}" for c in commands) + "\n\n")
            else:
                lf.write(f"Prompt: {task.get('prompt') or task.get('command')}\n\n")
            lf.flush()
    except Exception as e:
        logger.warning(f"Failed to initialize live_log_file: {e}")

    try:
        with open(live_meta_file, "w", encoding="utf-8") as mf:
            json.dump({
                "task_id": task_id,
                "status": "in_progress",
                "started_at": now_iso,
                "live_url": live_url,
                "commands": commands or [task.get("prompt")]
            }, mf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to initialize live_meta_file: {e}")

    # Immediately announce task in_progress and publish live_url / response_url to Google Drive
    task["status"] = "in_progress"
    task["started_at"] = now_iso
    task["live_url"] = live_url
    task["response_url"] = live_url
    task["log"] = f"🔗 **Live трансляция:** [{live_url}]({live_url})\n\n### ⏳ Выполняется:\n```bash\nИнициализация запуска...\n```"
    data["updated_at"] = now_iso
    data["version"] = data.get("version", 0) + 1
    try:
        client.update_queue(file_id, data)
        logger.info(f"Updated status for [{task_id}] to 'in_progress' with live_url: {live_url}")
    except Exception as e:
        logger.warning(f"Failed to update initial in_progress status: {e}")

    exec_start = time.time()
    completed_entries = []
    sync_in_progress = task.get("sync_in_progress", False)

    if commands:
        logger.info(f"Executing as direct shell command(s): {commands}")
        retcode, output, duration, completed_entries = execute_shell_commands(
            commands=commands,
            task=task,
            data=data,
            file_id=file_id,
            client=client,
            sync_intermediate=sync_in_progress,
            live_log_file=live_log_file,
            live_url=live_url
        )
    else:
        prompt = task.get("prompt") or task.get("command", "")
        logger.info(f"Executing as agy prompt: {prompt}")
        retcode, output, duration = execute_agy_prompt(
            prompt=prompt,
            agy_bin=agy_bin,
            task=task,
            data=data,
            file_id=file_id,
            client=client,
            live_log_file=live_log_file,
            live_url=live_url
        )
        completed_entries = [{"command": f"agy: {prompt}", "exit_code": retcode, "duration": duration, "output": output}]

    exec_duration = round(time.time() - exec_start, 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Finalize live log and metadata file
    try:
        with open(live_log_file, "a", encoding="utf-8") as lf:
            lf.write(f"\n=== [Task {task_id}] Finished with exit_code {retcode} in {exec_duration}s ===\n")
            lf.flush()
    except Exception:
        pass

    try:
        with open(live_meta_file, "w", encoding="utf-8") as mf:
            json.dump({
                "task_id": task_id,
                "status": "completed" if retcode == 0 else "failed",
                "exit_code": retcode,
                "started_at": task.get("started_at"),
                "completed_at": now_iso,
                "duration_seconds": exec_duration,
                "live_url": live_url,
                "output": output
            }, mf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to update final live_meta_file: {e}")

    task["status"] = "completed" if retcode == 0 else "failed"
    task["exit_code"] = retcode
    task["duration_seconds"] = exec_duration
    task["output"] = output
    task["completed_at"] = now_iso
    task["live_url"] = live_url
    task["response_url"] = live_url
    data["updated_at"] = now_iso
    data["version"] = data.get("version", 0) + 1

    upload_start = time.time()
    try:
        client.update_queue(file_id, data)
        upload_duration = round(time.time() - upload_start, 2)
        total_runner_time = round(time.time() - t_start, 2)
        logger.info(f"Task [{task_id}] finalized with status '{task['status']}' (exec: {exec_duration}s, upload: {upload_duration}s, total: {total_runner_time}s).")
    except Exception as e:
        upload_duration = round(time.time() - upload_start, 2)
        total_runner_time = round(time.time() - t_start, 2)
        logger.error(f"Failed to finalize task [{task_id}] on Google Drive: {e}")

    # Record detailed metrics
    metric_entry = {
        "task_id": task_id,
        "finished_at": now_iso,
        "created_at": created_at_str,
        "queue_latency_seconds": queue_latency,
        "exec_duration_seconds": exec_duration,
        "upload_duration_seconds": upload_duration,
        "total_runner_time_seconds": total_runner_time,
        "status": task["status"],
        "exit_code": retcode,
        "live_url": live_url,
        "commands": [
            {
                "command": e.get("command"),
                "exit_code": e.get("exit_code"),
                "duration_seconds": e.get("duration")
            } for e in completed_entries
        ],
        "output_length_chars": len(output)
    }
    record_task_metrics(metric_entry)

def expire_stale_tasks(tasks: list, data: dict, file_id: str, client: GDriveClient) -> bool:
    """Expires any tasks that have been pending or in_progress for longer than 60s without completion."""
    now = time.time()
    modified = False
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = t.get("status")
        if status in ("completed", "failed", "cancelled"):
            continue

        created_str = t.get("created_at") or t.get("started_at")
        task_age = None
        if created_str:
            try:
                dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                task_age = now - dt.timestamp()
            except Exception:
                pass
        elif "_" in t.get("id", ""):
            part = t.get("id", "").split("_")[-1]
            if part.isdigit() and len(part) >= 10:
                try:
                    task_age = now - int(part[:10])
                except Exception:
                    pass

        if task_age is not None and task_age > 60:
            logger.warning(f"Expiring stale task [{t.get('id')}] (age: {int(task_age)}s > 60s limit).")
            t["status"] = "failed"
            t["exit_code"] = -1
            t["output"] = f"Queue Timeout Error: Task was in state '{status}' for {int(task_age)}s without completion. Auto-failed to prevent hanging."
            t["completed_at"] = datetime.now(timezone.utc).isoformat()
            t["running_command"] = None
            t["log"] = f"### ❌ Ошибка очереди (Timeout):\n```\nЗадача зависла в статусе '{status}' на {int(task_age)} секунд и была прервана вотчером.\n```"
            modified = True
            try:
                base_dir = Path(__file__).resolve().parent
                live_dir = base_dir / "live_logs"
                live_dir.mkdir(parents=True, exist_ok=True)
                tid = t.get("id", "unknown")
                with open(live_dir / f"{tid}.json", "w", encoding="utf-8") as f_json:
                    json.dump(t, f_json, indent=2, ensure_ascii=False)
                with open(live_dir / f"{tid}.log", "w", encoding="utf-8") as f_log:
                    f_log.write(t["output"] + "\n")
            except Exception as le:
                logger.warning(f"Failed to write live log for expired task: {le}")

    if modified:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["version"] = data.get("version", 0) + 1
        try:
            client.update_queue(file_id, data)
        except Exception as e:
            logger.warning(f"Failed to update queue after expiring stale tasks: {e}")
    return modified

def run_loop(client: GDriveClient, agy_bin: str, check_interval: int, fixed_file_id: str = None):
    logger.info(f"Starting Task Watcher. Monitoring folder: {GEMINI_REMOTE_FOLDER_ID} (interval: {check_interval}s)")
    last_heartbeat = 0

    while RUNNING:
        if fixed_file_id:
            target_files = [fixed_file_id]
        else:
            target_files = client.find_all_queue_files()

        processed_any = False
        for fid in target_files:
            if not RUNNING:
                break
            try:
                data, raw = client.read_queue(fid)
                tasks = data.get("tasks", [])
                expire_stale_tasks(tasks, data, fid, client)
                def is_pending_task(t):
                    if not isinstance(t, dict):
                        return False
                    st = t.get("status")
                    if st in ("completed", "failed", "cancelled"):
                        return False
                    if st == "in_progress" and t.get("output"):
                        return False
                    # Has a command or prompt
                    return bool(t.get("command") or t.get("prompt") or t.get("commands"))

                has_pending = any(is_pending_task(t) for t in tasks)

                if has_pending:
                    for task in tasks:
                        if not RUNNING:
                            break
                        if is_pending_task(task):
                            process_single_task(task, data, fid, agy_bin, client)
                            processed_any = True
            except Exception as err:
                logger.error(f"Error during queue check for {fid}: {err}")

        # Update heartbeat every 30s
        now_t = time.time()
        if now_t - last_heartbeat > 30 and target_files:
            for fid in target_files:
                try:
                    data, _ = client.read_queue(fid)
                    data["watcher_heartbeat"] = datetime.now(timezone.utc).isoformat()
                    data["watcher_status"] = "alive"
                    data["poll_interval_seconds"] = check_interval
                    client.update_queue(fid, data)
                    last_heartbeat = now_t
                except Exception:
                    pass

        # If we just processed tasks, check again quickly in 2s; otherwise wait normal interval
        sleep_time = 2 if processed_any else check_interval
        time.sleep(sleep_time)

def main():
    parser = argparse.ArgumentParser(description="Real-time Antigravity Task Watcher")
    parser.add_argument("--interval", type=int, default=7, help="Poll interval in seconds (default: 7)")
    parser.add_argument("--file-id", type=str, default=None, help="Google Drive File ID (optional, auto-discovers if omitted)")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    agy_bin = find_agy_binary()

    client = GDriveClient(base_dir)
    if not client.authenticate():
        logger.error("Authentication failed.")
        sys.exit(1)

    run_loop(client, agy_bin, args.interval, fixed_file_id=args.file_id)

if __name__ == "__main__":
    main()
