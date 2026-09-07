#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore")

import time
import json
from datetime import datetime, timezone
from pathlib import Path
from gdrive_client import GDriveClient, DEFAULT_FILE_ID

def run_test():
    base_dir = Path(__file__).resolve().parent
    client = GDriveClient(base_dir)
    file_id = client.find_queue_file_id(DEFAULT_FILE_ID)

    print("=" * 70)
    print("ЗАПУСК БЕНЧМАРКА: ТЕСТИРОВАНИЕ СКОРОСТИ И ЗАДЕРЖЕК GOOGLE ДИСКА")
    print("=" * 70)

    # 1. Читаем текущую очередь
    data, _ = client.read_queue(file_id)
    task_id = f"bench_{int(time.time())}"
    test_command = "uname -r && uptime && free -h"

    new_task = {
        "id": task_id,
        "command": test_command,
        "commands": ["uname -r", "uptime", "free -h"],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "running_command": None,
        "log": "",
        "output": ""
    }
    data["tasks"].append(new_task)

    print(f"[*] Отправка задачи [{task_id}] на Google Диск...")
    print(f"    Команды: {new_task['commands']}")
    
    t0 = time.time()
    client.update_queue(file_id, data)
    t0_done = time.time()
    upload_initial_time = round(t0_done - t0, 2)
    print(f"[+] Задача успешно записана на Google Диск за {upload_initial_time}с!")

    t_in_progress = None
    t_completed = None
    task_result = None

    print("[*] Мониторинг Google Диска в реальном времени...")

    for i in range(60):
        time.sleep(0.8)
        try:
            current_data, _ = client.read_queue(file_id)
            current_tasks = current_data.get("tasks", [])
            target = next((t for t in current_tasks if t.get("id") == task_id), None)
            
            if not target:
                continue

            status = target.get("status")
            running = target.get("running_command")

            if status == "in_progress" and t_in_progress is None:
                t_in_progress = time.time()
                pickup_delay = round(t_in_progress - t0_done, 2)
                print(f"[+] [{round(t_in_progress - t0, 2)}с] Хост подхватил задачу! (Задержка ответа: {pickup_delay}с)")
                if running:
                    print(f"    ⏳ Выполняется: $ {running}")

            if status == "in_progress" and running:
                print(f"    -> Шаг на хосте: {running}")

            if status == "completed":
                t_completed = time.time()
                task_result = target
                print(f"[+] [{round(t_completed - t0, 2)}с] Задача ЗАВЕРШЕНА на Google Диске!")
                break
        except Exception as e:
            # Игнорируем временные сетевые ошибки
            pass

    print("\n" + "=" * 70)
    print("ИТОГОВЫЕ РЕЗУЛЬТАТЫ ИЗМЕРЕНИЙ:")
    print("=" * 70)

    if not task_result:
        print("[-] Тест превысил таймаут.")
        return

    total_time = round(t_completed - t0, 2)
    pickup_time = round(t_in_progress - t0_done, 2) if t_in_progress else None
    exec_internal = task_result.get("duration_seconds", 0)
    upload_sync_time = round(t_completed - t_in_progress, 2) if t_in_progress else None

    print(f"1. Время записи задачи на Google Диск (Gemini ➔ Диск):  {upload_initial_time} сек")
    if pickup_time is not None:
        print(f"2. Время обнаружения и старта на хосте (Диск ➔ Сервер): {pickup_time} сек")
    print(f"3. Чистое время выполнения команд в Bash на сервере:    {exec_internal} сек")
    if upload_sync_time is not None:
        print(f"4. Запись вывода и синхронизация (Сервер ➔ Диск):      {upload_sync_time} сек")
    print("-" * 70)
    print(f"ПОЛНЫЙ ЦИКЛ (Round-Trip Time от отправки до получения): {total_time} сек")
    print("=" * 70)

    print("\nПолученный вывод терминала (Выполнено):")
    print(task_result.get("log") or task_result.get("output"))

if __name__ == "__main__":
    run_test()
