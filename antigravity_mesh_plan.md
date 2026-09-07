📋 Antigravity Mesh: Пошаговый TDD-план реализации коробочного решения
Цель и глобальные требования
* Цель: Полная реализация и развёртывание модульного коробочного решения Antigravity Mesh на целевом узле Matebook16 (100.119.202.62).
* Методология: Superpowers Subagent-Driven Development (TDD: Тест -> Провал -> Реализация -> Успех -> Коммит).
* Без плейсхолдеров: Все команды, пути и логика должны быть полностью специфицированы.


________________


Task 1: Инициализация рабочего пространства и окружения [✅ ВЫПОЛНЕНО]
* Целевой узел: Matebook16 (~/MyProjects/antigravity-mesh/)
* [x] Шаг 1: Развёртывание дистрибутива из архива antigravity-mesh-v1.0.0.tar.gz.
* [x] Шаг 2: Инициализация Git-репозитория и структуры каталогов (core/, skills/, templates/, tests/, docs/).
* [x] Шаг 3: Настройка виртуального окружения Python (requirements.txt: FastMCP/Starlette, Uvicorn, Pytest, Jinja2).
* [x] Шаг 4: Проверка дымового теста импортов зависимостей.
* [x] Шаг 5: Фиксация начального коммита в Git.


________________


Task 2: Модуль телеметрии хоста (core/vitals.py) [✅ ВЫПОЛНЕНО]
* Компонент: core/vitals.py, тест tests/test_vitals.py
* [x] Шаг 1 (Red): Написание падающего теста test_get_host_vitals() для проверки структуры полей (hostname, cpu_load, ram, disk).
* [x] Шаг 2 (Verify Red): Запуск pytest tests/test_vitals.py и фиксация падения.
* [x] Шаг 3 (Green): Реализация get_host_vitals() через чтение /proc/loadavg, /proc/meminfo, shutil.disk_usage.
* [x] Шаг 4 (Verify Green): Запуск pytest tests/test_vitals.py и подтверждение успешного прохождения (1 passed in 0.02s).
* [x] Шаг 5: Коммит: feat: implement host vitals telemetry collector.


________________


Task 3: FastMCP-сервер и динамическая доставка скилла (core/server.py) [✅ ВЫПОЛНЕНО]
* Компонент: core/server.py, skills/orchestrator.md, тест tests/test_mcp_endpoints.py
* [x] Шаг 1 (Red): Написание падающего теста tests/test_mcp_endpoints.py для инструментов (bash_exec, system_vitals, get_orchestration_skill), ресурса resource://skills/orchestrator.md и промпта antigravity-orchestrator.
* [x] Шаг 2 (Verify Red): Прогон теста и подтверждение ошибки отсутствия модуля.
* [x] Шаг 3 (Green): Создание шаблона skills/orchestrator.md и реализация FastMCP сервера с SSE-транспортом.
* [x] Шаг 4 (Verify Green): Запуск pytest tests/test_mcp_endpoints.py и подтверждение прохождения всех тестов (4 passed in 0.02s).
* [x] Шаг 5: Коммит: feat: implement FastMCP server with dynamic in-band skill delivery.


________________


Task 4: Двухрежимный интерактивный установщик (install.sh) [✅ ВЫПОЛНЕНО]
* Компонент: install.sh, templates/, тест tests/test_installer.sh
* [x] Шаг 1 (Red): Написание проверочного bash-теста tests/test_installer.sh для проверки неинтерактивных флагов (--mode=standalone, --tls=self-signed, --port=8443, --dry-run).
* [x] Шаг 2 (Verify Red): Запуск bash tests/test_installer.sh и фиксация ошибки (RC: 1).
* [x] Шаг 3 (Green): Реализация install.sh с поддержкой меню:
   * Ветка 1: Cloud Gateway + Reverse Tunnel (FRP/Chisel, авто-субдомен, Wildcard SSL).
   * Ветка 2: Standalone (Caddy для своего домена, OpenSSL SAN для IP, Plain HTTP).
   * Генерация systemd-службы agy-mcp.service из templates/agy.service.j2.
* [x] Шаг 4 (Verify Green): Запуск bash tests/test_installer.sh и подтверждение корректной генерации конфигов.
* [x] Шаг 5: Коммит: feat: implement interactive dual-mode installer wizard.


________________


Task 5: Сквозная верификация и релиз v1.0.0 [✅ ВЫПОЛНЕНО]
* Целевой узел: Matebook16 (100.119.202.62)
* [x] Шаг 1: Полный запуск тестового набора: pytest tests/ -v (5 passed in 0.02s).
* [x] Шаг 2: Прогон bash install.sh --dry-run --mode=standalone --tls=none --port=8096 (DRY-RUN OK).
* [x] Шаг 3: Фиксация релизного тега: git tag -a v1.0.0 -m "Production release v1.0.0".