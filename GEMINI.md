# Project Rules: Antigravity Remote Execution Mesh

## 🚨 MANDATORY EXECUTION POLICY
1. **Never use Google Search or Browser to interact with the server API endpoint.**
2. **Never attempt direct SSH from the Google sandbox (no internal mesh network in sandbox).**
3. **Always use Native MCP tools directly:**
   - `bash_exec(command)` for Coordinator / Local Host
   - Target execution tools (`<node>_exec(command)`) for remote nodes
   - `gdrive_copy(file_name_or_id, dest_path, target_node)` for downloading/copying physical files between Google Drive and nodes
   - `system_vitals()` and node-specific vitals
   - `read_file(path, start_line, end_line)`, `write_file(path, content)`
   - `get_orchestration_skill()` for dynamically loading current skill rules
4. **Нативная автоматизация MCP API:**
   - Все команды исполняются исключительно через нативные инструменты MCP (`bash_exec`, `<node>_exec`). Очереди задач на Google Диске навсегда отключены. Просить пользователя запускать команды вручную строго запрещено!
5. **In `<thought>`, always log:**
   - Channel used (MCP API)
   - Tool name
   - Commands executed
   - Files read or modified
   - Target host and duration.
6. **Mobile UI Rule:** Never use `<details>` or `<summary>` HTML tags. Always output:
   `### ✅ Выполнено:` with a bash block and 1-2 concise Russian sentences.
7. **Strict Log & Execution Gate (Шлюз проверки логов):**
   - **Запрет спекулятивного продолжения:** Модель обязана дожидаться фактического ответа API. Пока ответ не получен — категорически запрещено выполнять последующие действия.
   - **Ориентация строго на вывод ответа/лога:** Единственный источник правды — фактический вывод терминала (stdout, stderr, код возврата) (`Exit: 0`, `isError: false`).
8. **Железный возврат при ошибках (Hard Error Halt & Rollback):**
   - При любой ошибке API, сбое или ненулевом коде возврата (`exit_code != 0`, `isError: true`), модель **ОБЯЗАНА ЖЕЛЕЗНО ОСТАНОВИТЬСЯ**.
   - Категорически запрещено продолжать цепочку действий, имитировать успех или генерировать вымышленные результаты.
   - Модель обязана зафиксировать отказ, вернуться в исходную точку ошибки, вывести реальный фрагмент ошибки и доложить пользователю.
9. **Запрет очередей Google Диска (No Google Drive Queues Policy):**
   - Файловые очереди задач на Google Диске полностью ликвидированы. Выполнение идёт напрямую через нативный MCP API. Google Диск используется исключительно для передачи файлов (`gdrive_copy`).
