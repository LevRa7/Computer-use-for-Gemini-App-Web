# Детальный план внедрения: Интеграция Google Antigravity SDK (Superpowers TDD)

- **Дата:** 2026-09-06
- **Спецификация:** [`spec_antigravity_sdk_integration.md`](file:///root/.gemini/antigravity-cli/brain/7d76670c-1b5e-4b9f-a56f-d8ae0bb5bcf6/spec_antigravity_sdk_integration.md)
- **Методология:** Superpowers Subagent-Driven TDD (Red -> Verify Red -> Green -> Verify Green -> Commit)
- **Целевое рабочее пространство:** `/root/agy-gdrive-runner` (Coordinator) и синхронизация в `/home/lev/MyProjects/antigravity-mesh` (Matebook16)

---

## Чеклист готовности фаз

- [x] **Task 1: Pydantic Data Contracts & Schemas (`core/schemas.py`)**
- [x] **Task 2: Lifecycle Hooks Engine & Hard Error Gate (`core/hooks.py`)**
- [x] **Task 3: Proactive Watchdog & Config Triggers (`core/triggers.py`)**
- [x] **Task 4: Declarative Safety Policies & Predicates (`core/policies.py`)**
- [x] **Task 5: Hierarchical Subagents Definition (`core/subagents.py`)**
- [x] **Task 6: Unified Agent Harness & Session Persistence (`core/agent_harness.py`)**
- [x] **Task 7: FastMCP Server Typed Integration (`core/server.py`)**
- [x] **Task 8: End-to-End E2E Verification & Git Sync to Matebook16**

---

## Task 1: Pydantic Data Contracts & Schemas

**Цель:** Создать строгие типизированные контракты данных для системных метрик, результатов выполнения инструментов, планов распределения и снапшотов здоровья сети, исключив парсинг текста через регулярные выражения.

- **Файлы:**
  - Создать: `core/schemas.py`
  - Создать: `tests/test_schemas.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_schemas.py`, проверяющий валидацию моделей `NodeVitals`, `ToolExecutionResult`, `MeshHealthSnapshot`, `TaskDispatchPlan` (валидные типы, дефолтные значения `timestamp`, валидация диапазонов `ram_usage_pct` от 0 до 100).
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_schemas.py -v
   # Ожидаемый результат: ModuleNotFoundError: No module named 'core.schemas'
   ```
3. **Шаг 3 (Green):** Реализовать `core/schemas.py` с использованием `pydantic.BaseModel`, `Field`, `validator` / `field_validator`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_schemas.py -v
   # Ожидаемый результат: 4 passed in 0.05s, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): implement Pydantic v2 data contracts and schemas"
   ```

---

## Task 2: Lifecycle Hooks Engine & Hard Error Gate

**Цель:** Реализовать аппаратные рантайм-хуки Google Antigravity SDK: перехватчик ошибок `on_tool_error`, шлюз пре-валидации `pre_tool_call_decide` (блокировка создания файлов очередей на Google Диске и опасных шелл-команд), и телеметрию `post_tool_call`.

- **Файлы:**
  - Создать: `core/hooks.py`
  - Создать: `tests/test_hooks.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_hooks.py`:
   - Тест 1: `test_pre_tool_call_blocks_drive_queues()` — попытка вызвать инструмент с `antigravity_tasks.json` должна возвращать `HookResult(allow=False)`.
   - Тест 2: `test_pre_tool_call_blocks_dangerous_rm()` — попытка вызвать команду с `rm -rf /` должна отклоняться.
   - Тест 3: `test_on_tool_error_halts_execution()` — передача исключения или ненулевого кода возврата возвращает чёткую системную директиву остановки `[HARD ERROR HALT]`.
   - Тест 4: `test_post_tool_call_logs_telemetry()` — успешный вызов инструмента регистрирует запись в лог с точной длительностью.
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_hooks.py -v
   # Ожидаемый результат: ModuleNotFoundError: No module named 'core.hooks'
   ```
3. **Шаг 3 (Green):** Реализовать `core/hooks.py` с использованием декораторов `@hooks.on_tool_error`, `@hooks.pre_tool_call_decide`, `@hooks.post_tool_call`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_hooks.py -v
   # Ожидаемый результат: 4 passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): implement Antigravity SDK lifecycle hooks and hard error gate"
   ```

---

## Task 3: Proactive Watchdog & Config Triggers

**Цель:** Реализовать фоновый мониторинг здоровья узлов mesh-сети через `every(interval, callback)` и горячую перезагрузку конфигураций через `on_file_change`.

- **Файлы:**
  - Создать: `core/triggers.py`
  - Создать: `tests/test_triggers.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_triggers.py`:
   - Тест `test_mesh_watchdog_detects_node_failure()` — мокирование падения узла и проверка вызова `ctx.send(...)` с предупреждением.
   - Тест `test_mesh_watchdog_healthy_silent()` — когда все узлы здоровы, сообщения в контекст не спамятся.
   - Тест `test_config_trigger_reloads_facts()` — проверка вызова колбэка обновления профиля узла при модификации `server_facts.json`.
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_triggers.py -v
   # Ожидаемый результат: ModuleNotFoundError: No module named 'core.triggers'
   ```
3. **Шаг 3 (Green):** Реализовать `core/triggers.py`, используя `google.antigravity.triggers.every`, `TriggerContext`, `on_file_change`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_triggers.py -v
   # Ожидаемый результат: 3 passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): implement proactive watchdog and file change triggers"
   ```

---

## Task 4: Declarative Safety Policies & Predicates

**Цель:** Настроить 9-уровневую систему политик безопасности Antigravity SDK: изоляцию рабочего каталога (`policy.workspace_only`), белые списки для безопасных команд и интерактивное подтверждение (`policy.ask_user`) для деструктивных действий.

- **Файлы:**
  - Создать: `core/policies.py`
  - Создать: `tests/test_policies.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_policies.py`:
   - Проверка разрешения безопасных команд (`git status`, `systemctl status`, `uptime`).
   - Проверка перевода деструктивных команд (`reboot`, `systemctl stop`, `rm`) в режим подтверждения `ask_user`.
   - Проверка ограничения путей файловых операций пределами рабочих директорий (`/root/agy-gdrive-runner`, `/home/lev/MyProjects/antigravity-mesh`).
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_policies.py -v
   # Ожидаемый результат: ModuleNotFoundError: No module named 'core.policies'
   ```
3. **Шаг 3 (Green):** Реализовать `core/policies.py` с использованием `google.antigravity.hooks.policy`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_policies.py -v
   # Ожидаемый результат: passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): implement declarative safety policies and command predicates"
   ```

---

## Task 5: Hierarchical Subagents Definition

**Цель:** Реализовать конфигурации специализированных субагентов для координатора, удалённых узлов и анализа кода с изоляцией контекста и контролем глубины рекурсии.

- **Файлы:**
  - Создать: `core/subagents.py`
  - Создать: `tests/test_subagents.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_subagents.py`:
   - Проверка наличия субагентов `coordinator_worker`, `remote_node_worker`, `code_researcher`.
   - Проверка, что `code_researcher` имеет доступ только к `view_file` и `search_directory` (без прав на `run_command`).
   - Проверка лимита глубины рекурсии (`max_subagent_depth <= 2`).
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_subagents.py -v
   # Ожидаемый результат: ModuleNotFoundError
   ```
3. **Шаг 3 (Green):** Реализовать `core/subagents.py`, используя `types.SubagentConfig`, `types.SubagentCapabilities`, `types.AgentBehavior.AUTONOMOUS`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_subagents.py -v
   # Ожидаемый результат: passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): configure hierarchical autonomous subagents and capability limits"
   ```

---

## Task 6: Unified Agent Harness & Session Persistence

**Цель:** Создать главный загрузчик агента (`core/agent_harness.py`), объединяющий конфигурацию `LocalAgentConfig`, хуки, триггеры, политики, каталог сохранения сессий (`save_dir`) и лимиты бюджета (`BudgetConfig`).

- **Файлы:**
  - Создать: `core/agent_harness.py`
  - Создать: `tests/test_harness.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_harness.py`:
   - Проверка создания валидного `LocalAgentConfig` со всеми хуками и триггерами.
   - Проверка сохранения и возобновления сессии по `conversation_id`.
   - Проверка соблюдения лимитов `BudgetConfig` (`max_model_calls=30`).
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_harness.py -v
   # Ожидаемый результат: ModuleNotFoundError
   ```
3. **Шаг 3 (Green):** Реализовать `core/agent_harness.py` с фабрикой `create_agent_config(conversation_id, save_dir, interactive)`.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_harness.py -v
   # Ожидаемый результат: passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(core): implement unified agent harness with session persistence and budget limits"
   ```

---

## Task 7: FastMCP Server Typed Integration

**Цель:** Интегрировать схемы Pydantic из `core/schemas.py` в инструменты FastMCP-сервера (`core/server.py` и `agy_mcp_server.py`), чтобы вызовы `system_vitals`, `<node>_vitals` и `<node>_exec` возвращали строго типизированный JSON.

- **Файлы:**
  - Модифицировать: `agy_mcp_server.py` (или `core/server.py`)
  - Создать: `tests/test_mcp_typed_integration.py`

### Шаги реализации:
1. **Шаг 1 (Red):** Написать тест `tests/test_mcp_typed_integration.py`:
   - Вызов `system_vitals` через JSON-RPC POST `/mcp`.
   - Валидация ответа через `NodeVitals.model_validate_json(...)`.
   - Проверка, что вызов не содержит текстовой «воды», а парсится без исключений.
2. **Шаг 2 (Verify Red):**
   ```bash
   pytest tests/test_mcp_typed_integration.py -v
   # Ожидаемый результат: ValidationError при парсинге текущего текстового вывода
   ```
3. **Шаг 3 (Green):** Обновить генерацию ответа в `system_vitals` и `matebook_vitals` для возврата структурированного JSON, сохраняя совместимость с читаемым текстом.
4. **Шаг 4 (Verify Green):**
   ```bash
   pytest tests/test_mcp_typed_integration.py -v
   # Ожидаемый результат: passed, exit code 0
   ```
5. **Шаг 5 (Commit):**
   ```bash
   git commit -m "feat(mcp): integrate typed Pydantic output schemas into FastMCP endpoints"
   ```

---

## Task 8: End-to-End E2E Verification & Git Sync to Matebook16

**Цель:** Выполнить сквозное тестирование полного конвейера (схемы + хуки + триггеры + сервер), зафиксировать изменения в Git и синхронизировать рабочий каталог на целевом ноутбуке Matebook16.

- **Файлы:**
  - Создать: `tests/test_e2e_antigravity_mesh.py`
  - Целевой репозиторий: `/home/lev/MyProjects/antigravity-mesh`

### Шаги реализации:
1. **Шаг 1:** Запуск полного тестового набора:
   ```bash
   pytest tests/ -v
   # Критерий успеха: Все тесты (Task 1 - Task 7) зелёные, 0 ошибок.
   ```
2. **Шаг 2:** Проверка перезапуска и статуса `agy-mcp.service`:
   ```bash
   systemctl restart agy-mcp.service
   systemctl is-active agy-mcp.service
   # Ожидаемый результат: active
   ```
3. **Шаг 3:** Синхронизация с Matebook16 через Git:
   ```bash
   git push origin master
   # На Matebook16 через MCP matebook_exec:
   # git pull origin master && pytest tests/ -v
   ```
4. **Шаг 4:** Фиксация релизного коммита:
   ```bash
   git commit -m "release: Antigravity SDK integration v2.0 (Hooks, Triggers, Pydantic, Subagents)"
   ```
