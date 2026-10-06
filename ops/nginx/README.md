# Gateway nginx configuration

Two files, and neither holds a domain literal:

| File | Role |
| :--- | :--- |
| `antigravity-mesh-mcp.conf` | the snippet included from the gateway server block: installers, node bootstrap payload, public file shares, MCP endpoints |
| `mesh-domain.vhost.template` | the HTTPS vhost for the gateway, with `__MESH_DOMAIN__` and `__MESH_CERT_NAME__` placeholders |
| `render-domain.sh` | renders the template from the one place the domain is declared |

## Where the domain comes from

`render-domain.sh` sources `ops/mesh-domain.sh`, which resolves the domain exactly
as `core/domain.py` does at runtime:

1. `MESH_PUBLIC_URL` in the environment;
2. `MESH_PUBLIC_URL` (or the `AGY_PUBLIC_BASE_URL` alias) in the domain file —
   `/etc/antigravity-mesh/domain.env` by default, `MESH_DOMAIN_FILE` overrides it;
3. `core/domain.py`'s `DEFAULT_PUBLIC_BASE_URL`, the single declaration of the
   project default.

`deploy_gateway.sh` uses the same script, so the gateway, the node, the installers
and nginx all agree on one value.

## Rendering

```bash
ops/nginx/render-domain.sh                 # render into ./rendered/, change nothing
ops/nginx/render-domain.sh --out DIR       # render into DIR
ops/nginx/render-domain.sh --check         # diff the rendered vhost against the live one
ops/nginx/render-domain.sh --apply         # install into /etc/nginx
```

`--apply` backs up the current vhost and snippet into
`/root/backups/antigravity-mesh/nginx-<timestamp>/`, runs `nginx -t`, and only then
reloads; on a failed test nothing is reloaded and the rollback command is printed.

`--check` is the way to prove the live host still matches the repository: with the
domain configured correctly it renders byte-identically to
`/etc/nginx/sites-available/<domain>`.

## Switching the domain

1. Put the new value in the one place — `MESH_PUBLIC_URL` in
   `/etc/antigravity-mesh/domain.env` (node hosts:
   `%USERPROFILE%\.config\antigravity-mesh\domain.env`).
2. Issue the certificate for the new name (certbot, or `tailscale cert` when the
   name is a Tailscale node name).
3. `ops/nginx/render-domain.sh --apply`.
4. `MESH_GATEWAY=… deploy_gateway.sh` to publish installers carrying the new
   default, if the domain is also served from that host.

## Two lines in the snippet are load-bearing for MCP clients

### `Connection ""` on `/sse`, `/mcp`, `/messages`

These are plain HTTP endpoints. The WebSocket idiom
`proxy_set_header Connection "upgrade"` was applied to them too, which advertises
an upgrade on every POST. Only `/ws/tunnel` is a real WebSocket and keeps it.

### `keepalive_timeout 0` on `/sse`, `/mcp`, `/messages`

Google's MCP frontend sends a chunked `DELETE` whose terminator can arrive after
nginx has already answered. On a kept-alive connection those leftover bytes were
parsed as a new request line — nginx logged `"0" 400` for it (79 times in 12
minutes) and closed the connection, losing whatever the client had in flight;
the client then reported `DEADLINE_EXCEEDED` after its 60 s deadline while the
tool had already run on the node. Closing the connection after each response
removes the desync entirely.

### `default_type text/plain`, never `add_header Content-Type`

`add_header Content-Type …` *appends* a header, and `alias` has already set one by
extension, so the response carried two `Content-Type` values
(`application/octet-stream` then `text/plain; charset=utf-8`). A client that honours
the first treats the installer as binary: PowerShell's `Invoke-WebRequest` then
hands `Invoke-Expression` a byte array, and `irm …/install.ps1 | iex` cannot work.
