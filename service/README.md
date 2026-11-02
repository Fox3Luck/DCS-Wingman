# DCS Wingman presence service

Small FastAPI + SQLite service. It receives rosters from server hooks, keeps
friendships, and answers the chat commands. Raw UCIDs are never stored: pilots
are keyed by a peppered HMAC of the UCID.

## Run locally

    cd service
    python -m venv .venv && .venv/Scripts/activate   (or: source .venv/bin/activate)
    pip install -r requirements.txt
    set WINGMAN_PEPPER=<32+ random chars>
    set WINGMAN_ADMIN_KEY=<32+ random chars>
    uvicorn wingman_service.asgi:app --port 8600

## Tests

    cd service && python -m pytest -q

## Register a server

    curl -X POST http://localhost:8600/v1/admin/servers -H "X-Admin-Key: $WINGMAN_ADMIN_KEY" \
         -H "Content-Type: application/json" \
         -d '{"name":"My Server","discord_url":"https://discord.gg/...","srs":"my.host:5002"}'

The reply carries `server_key`. Put it in the server's `Config\dcs-wingman.cfg`.

## API

| Method | Path               | Auth          | Purpose                                   |
|--------|--------------------|---------------|-------------------------------------------|
| POST   | /v1/admin/servers  | X-Admin-Key   | Register a server, returns its key        |
| POST   | /v1/report         | Bearer key    | Roster from a server hook                 |
| POST   | /v1/command        | Bearer key    | Chat command relayed by a server hook     |
| GET    | /v1/servers        | none          | Servers online now, counts only, no names |
| GET    | /v1/stats          | none          | Public counters                           |
| GET    | /healthz           | none          | Liveness                                  |

## Deploy

See `deploy/dcs-wingman.service` and `deploy/env.example`. Put TLS and the
public hostname on a reverse proxy for browsers and Discord. The DCS hook
itself speaks plain HTTP because DCS ships no TLS in its Lua, so keep an HTTP
listener for the hook endpoint until the client DLL lands (docs/DESIGN.md).
