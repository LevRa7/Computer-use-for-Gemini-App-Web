import warnings; warnings.filterwarnings("ignore")
#!/usr/bin/env python3
"""
auth_gdrive.py - Google Drive authorization helper for headless Linux.
Handles OAuth 2.0 flow, saves token.json, tests connectivity.
"""

import sys
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from gdrive_client import GDriveClient, SCOPES, DEFAULT_FILE_ID

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
SERVICE_ACCOUNT_FILE = BASE_DIR / "service_account.json"
TOKEN_FILE = BASE_DIR / "token.json"

AUTH_CODE = None
AUTH_EVENT = threading.Event()

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global AUTH_CODE
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            AUTH_CODE = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorization successful!</h1><p>You can close this window now and return to the terminal.</p>"
            )
            AUTH_EVENT.set()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No code found in callback.")

    def log_message(self, format, *args):
        # Silence HTTP logs
        return

def run_local_callback_server(port=8085):
    try:
        server = HTTPServer(("0.0.0.0", port), OAuthCallbackHandler)
        server.timeout = 1.0
        while not AUTH_EVENT.is_set():
            server.handle_request()
    except Exception as e:
        pass

def print_help_instructions():
    print("=" * 70)
    print("ИНСТРУКЦИЯ ПО НАСТРОЙКЕ АВТОРИЗАЦИИ В GOOGLE DRIVE")
    print("=" * 70)
    print("Для работы требуется файл credentials.json (OAuth) или service_account.json.")
    print()
    print("ВАРИАНТ 1 (Рекомендуемый: OAuth 2.0 Client ID):")
    print("1. Откройте Google Cloud Console: https://console.cloud.google.com/")
    print("2. Создайте проект (или выберите существующий).")
    print("3. Включите Google Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com")
    print("4. Перейдите в 'OAuth consent screen' (Экран согласия OAuth):")
    print("   - User Type: External")
    print("   - Заполните App name и ваш email (например, levra772@gmail.com)")
    print("   - Добавьте scope: .../auth/drive")
    print("   - В 'Test users' обязательно добавьте ваш email (levra772@gmail.com)")
    print("5. Перейдите в 'Credentials' -> '+ CREATE CREDENTIALS' -> 'OAuth client ID':")
    print("   - Application type: Desktop app (или Web application с Redirect URI http://localhost:8085/)")
    print("   - Name: agy-drive-runner")
    print("6. Скачайте JSON файл и сохраните его как:")
    print(f"   {CREDENTIALS_FILE}")
    print()
    print("ВАРИАНТ 2 (Service Account / Сервисный аккаунт):")
    print("1. В Google Cloud Console -> Credentials -> '+ CREATE CREDENTIALS' -> Service Account.")
    print("2. Создайте ключ (Keys -> Add key -> Create new key -> JSON).")
    print(f"3. Сохраните его как: {SERVICE_ACCOUNT_FILE}")
    print("4. Откройте Google Диск и поделитесь файлом antigravity_tasks.json")
    print("   с email сервисного аккаунта (например: xxx@xxx.iam.gserviceaccount.com).")
    print("=" * 70)

def main():
    print("[*] Проверка файлов авторизации...")
    if SERVICE_ACCOUNT_FILE.exists():
        print(f"[+] Найден {SERVICE_ACCOUNT_FILE.name}! Проверяем подключение...")
        client = GDriveClient(BASE_DIR)
        if client.authenticate():
            verify_and_report(client)
            return
        else:
            print("[-] Ошибка подключения через Service Account.")

    if not CREDENTIALS_FILE.exists():
        print_help_instructions()
        sys.exit(1)

    print(f"[+] Найден {CREDENTIALS_FILE.name}. Запуск OAuth авторизации...")
    
    redirect_uri = "http://localhost:8085/"
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent"
    )

    # Запуск фонового веб-сервера для перехвата редиректа
    server_thread = threading.Thread(target=run_local_callback_server, args=(8085,), daemon=True)
    server_thread.start()

    print("\n" + "=" * 70)
    print("ШАГ АВТОРИЗАЦИИ:")
    print("1. Откройте следующую ссылку в браузере:")
    print()
    print(auth_url)
    print()
    print("2. Войдите в ваш аккаунт Google и разрешите доступ к Google Диску.")
    print("3. Если браузер автоматически вернулся на http://localhost:8085/ и показал успех - отлично!")
    print("   Если страница http://localhost:8085/ недоступна (так как это удалённый сервер),")
    print("   просто СКОПИРУЙТЕ адресную строку целиком (или параметр ?code=...) и вставьте ниже:")
    print("=" * 70 + "\n")

    user_input = None
    # Ждём либо автоматического перехвата, либо ручного ввода
    while not AUTH_EVENT.is_set():
        try:
            user_input = input("Вставьте скопированный URL или код (или Enter для проверки): ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if AUTH_EVENT.is_set():
            break

        if user_input:
            if "code=" in user_input:
                parsed = urllib.parse.urlparse(user_input)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    AUTH_CODE = params["code"][0]
                    AUTH_EVENT.set()
                    break
            else:
                AUTH_CODE = user_input
                AUTH_EVENT.set()
                break

    if not AUTH_CODE:
        print("[-] Код авторизации не был получен.")
        sys.exit(1)

    print("[*] Обмен кода на токен...")
    flow.fetch_token(code=AUTH_CODE)
    creds = flow.credentials

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"[+] Токен успешно сохранён в {TOKEN_FILE}!")
    
    # Проверка работы
    client = GDriveClient(BASE_DIR)
    if client.authenticate():
        verify_and_report(client)
    else:
        print("[-] Ошибка инициализации клиента Google Drive после авторизации.")

def verify_and_report(client: GDriveClient):
    try:
        about = client.service.about().get(fields="user").execute()
        user_info = about.get("user", {})
        print(f"[+] Успешно авторизовано как: {user_info.get('displayName')} ({user_info.get('emailAddress')})")
    except Exception as e:
        print(f"[!] Предупреждение при запросе профиля: {e}")

    file_id = client.find_queue_file_id(DEFAULT_FILE_ID)
    if file_id:
        print(f"[+] Файл очереди задач найден в Google Drive! File ID: {file_id}")
        data, _ = client.read_queue(file_id)
        tasks = data.get("tasks", [])
        print(f"[+] Количество задач в очереди: {len(tasks)}")
        pending = sum(1 for t in tasks if t.get("status") == "pending")
        print(f"[+] Из них ожидают выполнения (pending): {pending}")
    else:
        print(f"[!] Файл очереди не найден по умолчанию. Создаём новый файл antigravity_tasks.json...")
        new_id = client.create_queue_file()
        print(f"[+] Файл успешно создан! File ID: {new_id}")

if __name__ == "__main__":
    main()
