# Gateway nginx snippet (`smart-server.online`)

This is the live `/etc/nginx/snippets/antigravity-mesh-mcp.conf` of the shared
gateway, included from the `smart-server.online` server block:

```nginx
include /etc/nginx/snippets/antigravity-mesh-mcp.conf;
```

It is versioned here because two lines in it are load-bearing for MCP clients and
were, until now, only present on the server:

## `Connection ""` on `/sse`, `/mcp`, `/messages`

These are plain HTTP endpoints. The WebSocket idiom
`proxy_set_header Connection "upgrade"` was applied to them too, which advertises
an upgrade on every POST. Only `/ws/tunnel` is a real WebSocket and keeps it.

## `keepalive_timeout 0` on `/sse`, `/mcp`, `/messages`

Google's MCP frontend sends a chunked `DELETE` whose terminator can arrive after
nginx has already answered. On a kept-alive connection those leftover bytes were
parsed as a new request line — nginx logged `"0" 400` for it (79 times in 12
minutes) and closed the connection, losing whatever the client had in flight;
the client then reported `DEADLINE_EXCEEDED` after its 60 s deadline while the
tool had already run on the node. Closing the connection after each response
removes the desync entirely.

## Deploying a change

1. Edit the snippet on the gateway (keep a timestamped backup next to it).
2. `nginx -t && systemctl reload nginx`.
3. Confirm in the access log that no new `"0" 400` lines appear.
