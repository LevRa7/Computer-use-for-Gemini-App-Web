#!/usr/bin/env python3
import sys
import os
import json
import time
import urllib.parse
from datetime import datetime, timezone
import requests
import subprocess

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8085"

TOKEN_FILE = "/root/agy-gdrive-runner/token.json"
RCLONE_CONF = "/root/.config/rclone/rclone.conf"

def extract_code(raw_input: str) -> str:
    raw = raw_input.strip()
    if "code=" in raw:
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            return qs["code"][0]
    return raw

def exchange(code: str):
    code = extract_code(code)
    print(f"[*] Exchanging authorization code for tokens...")
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }, timeout=15)

    if resp.status_code != 200:
        print(f"[-] Exchange failed ({resp.status_code}): {resp.text}")
        return False

    tokens = resp.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)
    scopes = tokens.get("scope", "https://www.googleapis.com/auth/drive").split()

    print(f"[+] Successfully received tokens! Access token valid for {expires_in}s.")

    # Save to token.json
    token_data = {
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scopes": scopes,
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": datetime.now(timezone.utc).isoformat()
    }
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    print(f"[+] Saved updated credentials to {TOKEN_FILE}")

    # Update rclone.conf
    try:
        rclone_token_payload = {
            "access_token": access_token,
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expiry": datetime.now(timezone.utc).isoformat()
        }
        rclone_content = f"""[gdrive]
type = drive
client_id = {CLIENT_ID}
client_secret = {CLIENT_SECRET}
scope = drive
token = {json.dumps(rclone_token_payload)}
"""
        os.makedirs(os.path.dirname(RCLONE_CONF), exist_ok=True)
        with open(RCLONE_CONF, "w", encoding="utf-8") as rf:
            rf.write(rclone_content)
        print(f"[+] Updated {RCLONE_CONF}")
    except Exception as e:
        print(f"[!] Warning: failed to update rclone.conf: {e}")

    # Restart agy-watcher.service
    print("[*] Restarting agy-watcher.service...")
    try:
        subprocess.run(["systemctl", "restart", "agy-watcher"], check=True)
        print("[+] agy-watcher.service restarted successfully.")
    except Exception as e:
        print(f"[!] Warning: failed to restart agy-watcher: {e}")

    # Test reading queue with new token
    try:
        from gdrive_client import GDriveClient
        gc = GDriveClient()
        data, _ = gc.read_queue()
        print(f"[+] Google Drive API test successful! Tasks in queue: {len(data.get('tasks', []))}")
    except Exception as e:
        print(f"[!] Test read failed: {e}")

    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 exchange_oauth_code.py <code>")
        sys.exit(1)
    exchange(sys.argv[1])
