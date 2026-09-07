#!/usr/bin/env python3
"""
gdrive_client.py - Direct Python Google Drive API client for antigravity_tasks.json.
Operates strictly inside the 'gemini-remote' folder on Google Drive.
Uses exponential backoff and persistent HTTP requests to avoid rclone overhead and rate limits.
"""

import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import requests

logger = logging.getLogger("gdrive_client")

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"

GEMINI_REMOTE_FOLDER_ID = "18A74X_2SEup7HkUh0oq5z8icYkRaAQYk"
QUEUE_FILE_ID = "1yBlk4JchKHD88Gn7nnM2EJZRhx9CKucH"
DEFAULT_FILE_ID = QUEUE_FILE_ID
FILE_NAME = "antigravity_tasks.json"

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


class GDriveClient:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or BASE_DIR
        self.token_file = self.base_dir / "token.json"
        self.session = requests.Session()
        self.access_token = None
        self.file_id = QUEUE_FILE_ID
        self.last_folder_search = 0
        self.cached_file_ids = [QUEUE_FILE_ID]

    def authenticate(self) -> bool:
        return self.token_file.exists()

    def find_all_queue_files(self, folder_id: str = GEMINI_REMOTE_FOLDER_ID, force: bool = False) -> list:
        """Finds all antigravity_tasks.json file IDs inside gemini-remote folder with fast refresh."""
        now = time.time()
        if not force and (now - self.last_folder_search < 5) and self.cached_file_ids:
            return self.cached_file_ids

        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            r = self.session.get(
                f"{DRIVE_API_BASE}/files",
                headers=headers,
                params={
                    "q": f"'{folder_id}' in parents and trashed = false and name = '{FILE_NAME}'",
                    "fields": "files(id, name, modifiedTime)",
                    "orderBy": "modifiedTime desc"
                },
                timeout=10
            )
            self.last_folder_search = now
            if r.status_code == 200:
                files = r.json().get("files", [])
                ids = [f["id"] for f in files if "id" in f]
                if ids:
                    self.file_id = ids[0]
                    self.cached_file_ids = ids
                    return ids
            elif r.status_code == 403:
                logger.warning("Google Drive API rate limit (403) while querying files. Using cached ID...")
                return self.cached_file_ids
        except Exception as e:
            logger.error(f"Failed to find queue files: {e}")
        return self.cached_file_ids

    def find_queue_file_id(self, target_file_id: Optional[str] = None) -> Optional[str]:
        """Returns the active queue file ID with priority on known canonical ID."""
        if target_file_id and target_file_id not in (DEFAULT_FILE_ID, "13Z7irvdPlUcFvbNhSv-hMHNgU-RZZ_Jg"):
            self.file_id = target_file_id
            return target_file_id
        if self.file_id:
            return self.file_id
        all_ids = self.find_all_queue_files()
        if all_ids:
            return all_ids[0]
        return self.file_id

    def get_token(self) -> str:
        if not self.token_file.exists():
            raise RuntimeError(f"Token file not found at {self.token_file}")

        now = time.time()
        # If we already have an active access token with valid expiry, use it directly
        if self.access_token and getattr(self, "token_expiry", 0) > now + 60:
            return self.access_token

        with open(self.token_file, "r", encoding="utf-8") as f:
            d = json.load(f)

        token = d.get("token")
        # Proactively refresh token via OAuth2 token endpoint
        logger.info("Refreshing Google Drive OAuth token...")
        try:
            r = self.session.post("https://oauth2.googleapis.com/token", data={
                "client_id": d["client_id"],
                "client_secret": d["client_secret"],
                "refresh_token": d["refresh_token"],
                "grant_type": "refresh_token"
            }, timeout=10)
            if r.status_code == 200:
                res = r.json()
                new_token = res["access_token"]
                expires_in = res.get("expires_in", 3600)
                self.token_expiry = now + expires_in
                d["token"] = new_token
                with open(self.token_file, "w", encoding="utf-8") as f:
                    json.dump(d, f)
                self.access_token = new_token
                logger.info(f"Token refreshed successfully. Valid for {expires_in}s.")
                return new_token
            else:
                logger.error(f"Token refresh failed: {r.status_code} {r.text}")
                self.access_token = token
                return token
        except Exception as e:
            logger.error(f"Token refresh request failed: {e}")
            self.access_token = token
            return token

    def read_queue(self, file_id: str = QUEUE_FILE_ID, max_retries: int = 4) -> Tuple[Dict[str, Any], str]:
        """Reads antigravity_tasks.json from Google Drive using direct API with backoff."""
        import random
        target_id = file_id or self.file_id
        for attempt in range(max_retries):
            token = self.get_token()
            headers = {"Authorization": f"Bearer {token}"}
            try:
                r = self.session.get(f"{DRIVE_API_BASE}/files/{target_id}?alt=media", headers=headers, timeout=10)
                if r.status_code == 200:
                    content_str = r.text.strip()
                    if not content_str:
                        return {"tasks": []}, ""
                    try:
                        data = json.loads(content_str)
                    except Exception:
                        data = {"tasks": []}
                    if "tasks" not in data:
                        data["tasks"] = []
                    return data, content_str
                elif r.status_code == 403:
                    wait_s = round(min(32.0, (2 ** attempt) * 2.5 + random.uniform(1.0, 3.0)), 2)
                    err_msg = ""
                    try:
                        err_json = r.json()
                        err_msg = err_json.get("error", {}).get("message", "")
                    except Exception:
                        pass
                    logger.warning(f"Google Drive API rate limit (403) reading queue ({err_msg[:60]}). Retrying in {wait_s}s...")
                    time.sleep(wait_s)
                    continue
                else:
                    logger.warning(f"Read queue failed with status {r.status_code}")
            except Exception as e:
                logger.debug(f"Read queue exception: {e}")

            time.sleep(2)

        return {"tasks": []}, ""

    def update_queue(self, file_id: str, data: Dict[str, Any], max_retries: int = 5) -> bool:
        """Uploads updated queue dictionary to Google Drive using direct PATCH."""
        import random
        target_id = file_id or self.file_id
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data["version"] = data.get("version", 0) + 1
        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        for attempt in range(max_retries):
            token = self.get_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8"
            }
            try:
                r = self.session.patch(
                    f"{DRIVE_UPLOAD_BASE}/files/{target_id}?uploadType=media",
                    headers=headers,
                    data=json_str.encode("utf-8"),
                    timeout=15
                )
                if r.status_code == 200:
                    return True
                elif r.status_code == 403:
                    wait_s = round(min(32.0, (2 ** attempt) * 2.5 + random.uniform(1.0, 3.0)), 2)
                    logger.warning(f"Update 403 rate limit, retrying in {wait_s}s...")
                    time.sleep(wait_s)
                    continue
                else:
                    logger.warning(f"Update queue failed with status {r.status_code}")
            except Exception as e:
                logger.debug(f"Update queue exception: {e}")

            time.sleep((attempt + 1) * 3)

        return False

    def create_queue_file(self) -> str:
        self.update_queue(self.file_id, {"tasks": []})
        return self.file_id

    def find_file(self, file_name: str, folder_id: Optional[str] = None) -> Optional[dict]:
        """Finds a file by exact name in Google Drive, optionally within a folder."""
        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        query_parts = [f"name = '{file_name}'", "trashed = false"]
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        query = " and ".join(query_parts)
        try:
            r = self.session.get(
                f"{DRIVE_API_BASE}/files",
                headers=headers,
                params={
                    "q": query,
                    "fields": "files(id, name, mimeType, size, modifiedTime)",
                    "orderBy": "modifiedTime desc"
                },
                timeout=15
            )
            if r.status_code == 200:
                files = r.json().get("files", [])
                if files:
                    return files[0]
        except Exception as e:
            logger.error(f"find_file error: {e}")
        return None

    def download_file(
        self,
        file_id: Optional[str] = None,
        file_name: Optional[str] = None,
        folder_id: Optional[str] = None,
        dest_path: Optional[str] = None
    ) -> Tuple[bool, str, dict]:
        """
        Downloads or exports a file from Google Drive.
        Automatically handles regular binary/text files and Google Docs/Sheets (via export).
        Returns (success: bool, message_or_content: str, meta: dict).
        """
        token = self.get_token()
        headers = {"Authorization": f"Bearer {token}"}

        file_meta = None
        if not file_id and file_name:
            if folder_id:
                file_meta = self.find_file(file_name, folder_id)
            if not file_meta:
                file_meta = self.find_file(file_name, GEMINI_REMOTE_FOLDER_ID)
            if not file_meta:
                file_meta = self.find_file(file_name)
            if file_meta:
                file_id = file_meta.get("id")
            else:
                return False, f"File '{file_name}' not found on Google Drive.", {}

        if not file_id:
            return False, "Neither file_id nor file_name provided.", {}

        if not file_meta:
            try:
                r_meta = self.session.get(
                    f"{DRIVE_API_BASE}/files/{file_id}",
                    headers=headers,
                    params={"fields": "id, name, mimeType, size"},
                    timeout=10
                )
                if r_meta.status_code == 200:
                    file_meta = r_meta.json()
                else:
                    return False, f"Failed to get metadata for file {file_id}: HTTP {r_meta.status_code}", {}
            except Exception as e:
                return False, f"Error getting metadata for file {file_id}: {e}", {}

        mime_type = file_meta.get("mimeType", "")
        actual_name = file_name or file_meta.get("name", file_id)

        try:
            if mime_type.startswith("application/vnd.google-apps.document"):
                url = f"{DRIVE_API_BASE}/files/{file_id}/export?mimeType=text/plain"
                r = self.session.get(url, headers=headers, timeout=30)
            elif mime_type.startswith("application/vnd.google-apps.spreadsheet"):
                url = f"{DRIVE_API_BASE}/files/{file_id}/export?mimeType=text/csv"
                r = self.session.get(url, headers=headers, timeout=30)
            else:
                url = f"{DRIVE_API_BASE}/files/{file_id}?alt=media"
                r = self.session.get(url, headers=headers, timeout=60)

            if r.status_code == 200:
                content_bytes = r.content
                if dest_path:
                    dest = Path(dest_path).expanduser().resolve()
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content_bytes)
                    return True, f"Downloaded '{actual_name}' ({len(content_bytes)} bytes) to {dest}", file_meta
                else:
                    try:
                        return True, content_bytes.decode("utf-8"), file_meta
                    except UnicodeDecodeError:
                        return True, f"<binary data: {len(content_bytes)} bytes>", file_meta
            else:
                return False, f"Download failed: HTTP {r.status_code} {r.text[:200]}", file_meta
        except Exception as e:
            return False, f"Download exception: {e}", file_meta
