#!/usr/bin/env python3
import sys
import re
import json
import time
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"
RCLONE_CONF = Path.home() / ".config" / "rclone" / "rclone.conf"

def main():
    print("[*] Запуск временного сервера авторизации rclone...")
    proc = subprocess.Popen(
        ["rclone", "authorize", "drive", "--auth-no-open-browser"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    auth_url = None
    port = 53682

    # Ждём ссылку на авторизацию
    for _ in range(50):
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        match = re.search(r'(http://127\.0\.0\.1:(\d+)/auth\?state=\S+)', line)
        if match:
            local_url = match.group(1)
            port = int(match.group(2))
            # Получаем реальный Google OAuth URL через редирект
            req = urllib.request.Request(local_url, headers={"User-Agent": "curl/7.68.0"})
            try:
                urllib.request.urlopen(req)
            except urllib.error.HTTPError as e:
                auth_url = e.headers.get("Location")
            except Exception:
                pass
            if not auth_url:
                # Если urllib последовал за редиректом, извлекаем напрямую
                auth_url = local_url
            break

    if not auth_url:
        print("[-] Не удалось получить ссылку для авторизации.")
        proc.kill()
        sys.exit(1)

    print("\n" + "=" * 76)
    print("ШАГ АВТОРИЗАЦИИ В GOOGLE ДИСКЕ:")
    print("=" * 76)
    print("1. Откройте эту ссылку в браузере:")
    print()
    print(auth_url)
    print()
    print("2. Войдите в ваш Google-аккаунт и нажмите «Разрешить» (Allow).")
    print("3. В браузере откроется адрес вида:")
    print(f"   http://127.0.0.1:{port}/?state=...&code=4/0A...")
    print("   (Страница может написать «Не удается получить доступ к сайту» — это нормально!)")
    print("4. Скопируйте целиком адресную строку из браузера и вставьте сюда:")
    print("=" * 76 + "\n")

    callback_input = None
    try:
        callback_input = input("Вставьте скопированный URL (или код): ").strip()
    except (EOFError, KeyboardInterrupt):
        proc.kill()
        sys.exit(1)

    if not callback_input:
        print("[-] Пустой ввод.")
        proc.kill()
        sys.exit(1)

    # Извлекаем query string
    if "http" in callback_input:
        parsed = urllib.parse.urlparse(callback_input)
        query = parsed.query
        callback_url = f"http://127.0.0.1:{port}/?{query}"
    elif "code=" in callback_input:
        callback_url = f"http://127.0.0.1:{port}/?{callback_input.lstrip('?')}"
    else:
        callback_url = f"http://127.0.0.1:{port}/?code={callback_input}"

    print(f"[*] Отправка кода локальному сервису авторизации...")
    try:
        urllib.request.urlopen(callback_url, timeout=5)
    except Exception as e:
        pass

    # Считываем результат из proc
    token_json_str = None
    output_lines = []
    start_capture = False

    for _ in range(50):
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            break
        output_lines.append(line)
        if "Paste the following into your remote machine --->" in line:
            start_capture = True
            continue
        if "<---End paste" in line:
            start_capture = False
            break
        if start_capture:
            if line.strip().startswith("{"):
                token_json_str = line.strip()
                break

    proc.poll()
    if not token_json_str:
        # Проверяем весь вывод
        full_out = "".join(output_lines)
        match_json = re.search(r'(\{.*"access_token".*\})', full_out)
        if match_json:
            token_json_str = match_json.group(1)

    if not token_json_str:
        print("[-] Не удалось извлечь токен. Вывод процесса:")
        print("".join(output_lines))
        sys.exit(1)

    print("[+] Токен успешно получен от Google!")
    token_data = json.loads(token_json_str)

    # 1. Сохраняем token.json для нашего python клиента
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)
    print(f"[+] Сохранён файл: {TOKEN_FILE}")

    # 2. Настраиваем rclone.conf
    RCLONE_CONF.parent.mkdir(parents=True, exist_ok=True)
    rclone_content = f"""[gdrive]
type = drive
scope = drive
token = {json.dumps(token_json_str)}
"""
    with open(RCLONE_CONF, "w", encoding="utf-8") as f:
        f.write(rclone_content)
    print(f"[+] Настроен конфигурационный файл rclone: {RCLONE_CONF}")

    # 3. Проверяем доступ к файлу очереди задач
    print("[*] Проверка доступа к Google Диску...")
    from gdrive_client import GDriveClient, DEFAULT_FILE_ID
    client = GDriveClient(BASE_DIR)
    if client.authenticate():
        file_id = client.find_queue_file_id(DEFAULT_FILE_ID)
        if file_id:
            print(f"[+] УСПЕХ! Найден файл antigravity_tasks.json (File ID: {file_id})")
            data, _ = client.read_queue(file_id)
            print(f"[+] Всего задач в очереди: {len(data.get('tasks', []))}")
        else:
            print("[+] Создаём файл antigravity_tasks.json в корне Google Диска...")
            new_id = client.create_queue_file()
            print(f"[+] Файл создан! File ID: {new_id}")
    else:
        print("[-] Ошибка инициализации Google Drive клиента.")

    # 4. Запускаем systemd службу agy-watcher
    print("[*] Запуск фоновой службы agy-watcher...")
    subprocess.run(["systemctl", "enable", "--now", "agy-watcher"])
    status = subprocess.run(["systemctl", "is-active", "agy-watcher"], capture_output=True, text=True)
    print(f"[+] Статус службы agy-watcher: {status.stdout.strip()}")
    print("\n[+] ВСЁ НАСТРОЕНО И ЗАПУЩЕНО В АВТОМАТИЧЕСКОМ РЕЖИМЕ!")

if __name__ == "__main__":
    main()
