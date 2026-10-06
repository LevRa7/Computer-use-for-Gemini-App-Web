# Домен проекта: одно место настройки

Домен задаётся **в одном месте** и оттуда его берут все компоненты. В репозитории
знание о домене живёт ровно в одном модуле — `core/domain.py`; ни один установщик,
шаблон или конфиг больше не содержит домена.

## Порядок поиска

| Приоритет | Источник | Кто читает |
| :--- | :--- | :--- |
| 1 | `configure(public_base_url=…)` — инъекция | тесты |
| 2 | `MESH_PUBLIC_URL` в окружении | все |
| 3 | `AGY_PUBLIC_BASE_URL` в окружении (старый алиас) | все |
| 4 | файл `domain.env` | все |
| 5 | `MESH_GATEWAY` (только хост туннеля узла) | узел |
| 6 | `DEFAULT_PUBLIC_BASE_URL` в `core/domain.py` | все |

Файл `domain.env` — то самое единственное место на хосте:

```ini
MESH_PUBLIC_URL=https://mesh.example.com
```

| ОС | Путь |
| :--- | :--- |
| Linux | `/etc/antigravity-mesh/domain.env` |
| Windows | `%USERPROFILE%\.config\antigravity-mesh\domain.env` |

Путь переопределяется `MESH_DOMAIN_FILE`. Файл может быть с BOM — это учитывают и
Python (`encoding="utf-8-sig"`), и shell-резолвер (`ops/mesh-domain.sh`).

## Кто именно спрашивает общий модуль

| Компонент | Как использует |
| :--- | :--- |
| `core/agent.py` | хост туннеля (`wss://<хост>/ws/tunnel`) |
| `gateway.py` | `PUBLIC_BASE_URL`/`PUBLIC_HOST`, ссылки в ответах и текстах |
| `core/web_share.py` | публичные ссылки на файлы: `https://<домен>/<узел>/<слаг>/…` |
| `core/mcp_tools.py` | передаёт `public_url` в публикацию файлов |
| `agy_sync.py`, `agy_watcher.py` | ссылки в генерируемых фактах и логах |
| `install.sh`, `install.ps1` | плейсхолдер `__MESH_DOMAIN__`; домен подставляется при публикации |
| `deploy_gateway.sh` | `ops/mesh-domain.sh`; пишет `domain.env` на шлюзе |
| nginx | `ops/nginx/mesh-domain.vhost.template` + `ops/nginx/render-domain.sh` |

Shell-сторона использует один и тот же резолвер — `ops/mesh-domain.sh`; его
подключают и деплой, и рендер nginx.

## Как сменить домен

1. **Одно значение.** Прописать новый домен в `MESH_PUBLIC_URL` в `domain.env` на
   каждом хосте: на шлюзе и на узлах. Больше нигде менять не нужно.
2. **Сертификат.** Выпустить на новое имя: `certbot certonly --nginx -d <домен>`
   или, если имя принадлежит Tailscale, `tailscale cert <имя>`.
3. **nginx на шлюзе.** `ops/nginx/render-domain.sh --apply` — рендерит vhost из
   шаблона, делает бэкап в `/root/backups/antigravity-mesh/nginx-<метка>/`,
   проверяет `nginx -t` и только потом перезагружает. `--check` сравнивает живой
   конфиг с шаблоном, `--out DIR` просто рендерит рядом.
4. **Опубликовать установщики.**
   `MESH_GATEWAY_SSH=root@<шлюз> MESH_PUBLIC_URL=https://<домен> ./deploy_gateway.sh`
   — подставит домен в отдаваемые `install.sh`/`install.ps1`, положит
   `core/domain.py` рядом со шлюзом и запишет `domain.env` на хосте.
5. **Узлы.** Перезапустить агента: `systemctl --user restart antigravity-agent`
   (Linux) или перезапустить задачу автозапуска (Windows). Новое значение
   подхватится из `domain.env`.

## Проверка, что домен действительно один

```bash
ops/nginx/render-domain.sh --check     # живой vhost == шаблон
python3 -c "from core import domain; print(domain.public_base_url(), domain.source())"
```

В тестах это закреплено: `tests/test_domain.py` (приоритеты и единственность
литерала), `tests/test_domain_single_source.py` (ни один установщик, шаблон или
скрипт не содержит домена; shell-резолвер совпадает с Python-овым),
`tests/test_web_share.py` (ссылки берут домен из общего модуля).

## Что нельзя делать

* писать домен в `install.sh`, `install.ps1`, `deploy_gateway.sh`, конфиги nginx
  или в любой модуль кроме `core/domain.py` — тесты это поймают;
* задавать `MESH_PUBLIC_URL` одновременно в `gateway.env` и в `domain.env`:
  окружение побеждает файл, и домен снова оказывается в двух местах
  (`deploy_gateway.sh` предупредит об этом при деплое);
* править отрендеренный vhost на хосте — правьте шаблон и рендерите заново.
