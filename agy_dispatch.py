#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")

"""
agy_dispatch.py - CLI tool to dispatch tasks/commands to Google Drive queue and inspect results.
"""

import sys
import json
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

from gdrive_client import GDriveClient, DEFAULT_FILE_ID

def get_client_and_file_id(file_id_arg: str = DEFAULT_FILE_ID):
    base_dir = Path(__file__).resolve().parent
    client = GDriveClient(base_dir)
    if not client.authenticate():
        print("[-] Not authenticated with Google Drive.")
        sys.exit(1)
    file_id = client.find_queue_file_id(file_id_arg)
    if not file_id:
        print("[-] antigravity_tasks.json not found on Google Drive.")
        sys.exit(1)
    return client, file_id

def cmd_list(args):
    client, file_id = get_client_and_file_id(args.file_id)
    data, _ = client.read_queue(file_id)
    tasks = data.get("tasks", [])
    print(f"\n--- Queue on Google Drive (Total tasks: {len(tasks)}, Last updated: {data.get('updated_at')}) ---")
    if not tasks:
        print("No tasks found in queue.")
        return
    for i, t in enumerate(tasks, 1):
        status = t.get("status", "unknown").upper()
        task_id = t.get("id")
        created = t.get("created_at", "")
        cmd = t.get("command") or t.get("prompt") or t.get("commands")
        print(f"[{i}] {status:<11} | ID: {task_id} | Created: {created}")
        print(f"    Command/Prompt: {cmd}")
        if t.get("running_command"):
            print(f"    ⏳ Выполняется: {t['running_command']}")
        if t.get("log"):
            log_snippet = t["log"].splitlines()[:4]
            print(f"    Log: {' '.join(log_snippet)}...")
        elif t.get("output"):
            output_snippet = t["output"].splitlines()[0][:80]
            print(f"    Output: {output_snippet}...")
        print()

def cmd_send(args):
    client, file_id = get_client_and_file_id(args.file_id)
    data, _ = client.read_queue(file_id)
    task_id = f"task_{int(time.time())}"
    
    # If comma-separated or multiple commands passed
    raw_cmd = args.command
    if ";" in raw_cmd and not args.as_script:
        commands = [c.strip() for c in raw_cmd.split(";") if c.strip()]
    elif "\n" in raw_cmd:
        commands = [c.strip() for c in raw_cmd.splitlines() if c.strip()]
    else:
        commands = [raw_cmd]

    new_task = {
        "id": task_id,
        "command": raw_cmd,
        "commands": commands,
        "type": "command" if not args.agy else "agy",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
        "running_command": None,
        "log": "",
        "output": ""
    }
    data["tasks"].append(new_task)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["version"] = data.get("version", 0) + 1
    client.update_queue(file_id, data)
    print(f"[+] Task queued successfully!")
    print(f"    ID: {task_id}")
    print(f"    Commands: {commands}")

def cmd_clear(args):
    client, file_id = get_client_and_file_id(args.file_id)
    data, _ = client.read_queue(file_id)
    count = len(data.get("tasks", []))
    data["tasks"] = []
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    client.update_queue(file_id, data)
    print(f"[+] Cleared {count} tasks from queue.")

def main():
    parser = argparse.ArgumentParser(description="Antigravity Task Dispatcher CLI")
    parser.add_argument("--file-id", type=str, default=DEFAULT_FILE_ID, help="Google Drive File ID")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List all tasks in queue")
    p_list.set_defaults(func=cmd_list)

    p_send = subparsers.add_parser("send", help="Send command(s) to queue")
    p_send.add_argument("command", type=str, help="Shell command or prompt")
    p_send.add_argument("--agy", action="store_true", help="Force execution via agy CLI")
    p_send.add_argument("--as-script", action="store_true", help="Treat multi-command string as single script")
    p_send.set_defaults(func=cmd_send)

    p_clear = subparsers.add_parser("clear", help="Clear all tasks in queue")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
