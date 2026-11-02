"""HTTP API for the DCS Wingman presence service.

    POST /v1/admin/servers   register one server (X-Admin-Key)         -> server_key
    POST /v1/admin/fleets    register a hosting fleet (X-Admin-Key)    -> fleet_key
    POST /v1/report          roster from a server hook (Bearer key)    -> chat messages
    POST /v1/command         chat command from a server hook (Bearer)  -> chat messages
    GET  /v1/servers         public directory of servers online now (no player names)
    GET  /v1/stats           public counters
    GET  /healthz

A Bearer key is either a server key or a fleet key. With a fleet key the server row
is created on first contact, identified by "<client ip>:<port>" inside that fleet.

Reply bodies from /v1/report and /v1/command carry raw UCIDs so the hook can address
chat lines. They are never logged or stored.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
import sqlite3
import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .config import Settings, load_settings
from .db import connect, init_schema
from .identity import key_hash, new_fleet_key, new_server_key
from .logic import Person, process_command, process_report

MAX_ROSTER = 1000
SRS_RE = re.compile(r"^[A-Za-z0-9.-]{1,120}(:\d{1,5})?$")


# ----------------------------------------------------------------------------- models

def _lua_table_to_list(v: Any) -> Any:
    # Lua's lua2json renders an empty table as {} and a sparse one as an object.
    if isinstance(v, dict):
        return list(v.values())
    return v


def _https_url(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    scheme, sep, rest = v.partition("://")
    if scheme.lower() != "https" or not sep or not rest or any(ch.isspace() or ord(ch) < 32 for ch in v):
        raise ValueError("must be an https:// URL")
    return "https://" + rest


def _srs_endpoint(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not SRS_RE.match(v):
        raise ValueError("SRS must look like host or host:port")
    if ":" in v and not 1 <= int(v.rsplit(":", 1)[1]) <= 65535:
        raise ValueError("SRS port must be 1..65535")
    return v


class Player(BaseModel):
    ucid: str = Field(min_length=8, max_length=64)
    name: str = Field(default="", max_length=64)
    side: int = Field(default=0, ge=0, le=2)
    slot: str = Field(default="", max_length=64)


class ServerInfo(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    port: int | None = Field(default=None, ge=1, le=65535)
    max_players: int | None = Field(default=None, ge=0, le=MAX_ROSTER)
    mission: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class Report(BaseModel):
    v: Literal[1] = 1
    hook: str | None = Field(default=None, max_length=32)
    server: ServerInfo = Field(default_factory=ServerInfo)
    players: list[Player] = Field(default_factory=list, max_length=MAX_ROSTER)

    @field_validator("players", mode="before")
    @classmethod
    def _players_from_lua(cls, v: Any) -> Any:
        return _lua_table_to_list(v)


class Candidate(BaseModel):
    ucid: str = Field(min_length=8, max_length=64)
    name: str = Field(default="", max_length=64)


class Command(BaseModel):
    v: Literal[1] = 1
    server: ServerInfo = Field(default_factory=ServerInfo)
    actor: Candidate
    command: str = Field(min_length=1, max_length=16)
    arg: str = Field(default="", max_length=64)
    candidates: list[Candidate] = Field(default_factory=list, max_length=MAX_ROSTER)

    @field_validator("candidates", mode="before")
    @classmethod
    def _candidates_from_lua(cls, v: Any) -> Any:
        return _lua_table_to_list(v)


class RegisterServer(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    discord_url: str | None = Field(default=None, max_length=200)
    srs: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("discord_url", "website")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        return _https_url(v)

    @field_validator("srs")
    @classmethod
    def _srs(cls, v: str | None) -> str | None:
        return _srs_endpoint(v)


class RegisterFleet(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    discord_url: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)

    @field_validator("discord_url", "website")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        return _https_url(v)


# ----------------------------------------------------------------------------- helpers

def format_address(host: str | None, port: int | None) -> str:
    """Return host[:port] for a validated IP, bracketing IPv6. Unknown hosts become 'unknown'."""
    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return "unknown"
    text = f"[{ip}]" if ip.version == 6 else str(ip)
    return f"{text}:{port}" if port else text


def client_ip(request: Request, trust_proxy: bool) -> str | None:
    """The reporting server's IP. Behind the Cloudflare tunnel only CF-Connecting-IP is
    trusted and a request without it has no usable address (never the tunnel's loopback
    peer, which every origin shares). X-Forwarded-For is never used."""
    if trust_proxy:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()[:64]
        return cf or None
    return request.client.host if request.client else None


def bearer_key(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="server key required")
    return authorization[7:].strip()


MAX_FLEET_SERVERS = 2000       # rows one fleet key may create
MAX_FLEET_PORTS_PER_IP = 128   # DCS instances one address may register inside a fleet
FLEET_IDLE_REAP = 30 * 86400   # fleet rows silent this long are removed on next first contact


def resolve_server(
    conn: sqlite3.Connection, key: str, address: str, server_info: ServerInfo, now: float
) -> sqlite3.Row:
    """Find the server row for a Bearer key: a server key maps directly; a fleet key
    maps to (fleet, client ip:port), creating the row on first contact. Runs inside
    BEGIN IMMEDIATE so concurrent first contacts serialize; a UNIQUE race re-selects."""
    h = key_hash(key)
    row = conn.execute("SELECT * FROM servers WHERE key_hash = ?", (h,)).fetchone()
    if row is not None:
        return row
    fleet = conn.execute("SELECT * FROM fleets WHERE key_hash = ?", (h,)).fetchone()
    if fleet is None:
        raise HTTPException(status_code=401, detail="unknown server key")
    if address == "unknown" or server_info.port is None:
        raise HTTPException(status_code=400, detail="fleet servers must report a port from a known address")
    identity = address
    fid = fleet["id"]
    row = conn.execute(
        "SELECT * FROM servers WHERE fleet_id = ? AND fleet_identity = ?", (fid, identity)
    ).fetchone()
    if row is not None:
        # touch it now, inside this transaction, so a concurrent first contact's idle
        # reap cannot delete a server that just resolved but has not reported yet
        conn.execute("UPDATE servers SET last_report_at = ? WHERE id = ?", (now, row["id"]))
        return row
    # first contact: reap long-silent fleet rows, then enforce growth caps
    conn.execute(
        "DELETE FROM servers WHERE fleet_id = ? AND COALESCE(last_report_at, created_at) < ?",
        (fid, now - FLEET_IDLE_REAP),
    )
    total = conn.execute("SELECT COUNT(*) AS n FROM servers WHERE fleet_id = ?", (fid,)).fetchone()["n"]
    host = identity.rsplit(":", 1)[0]
    same_ip = conn.execute(
        "SELECT COUNT(*) AS n FROM servers WHERE fleet_id = ? AND fleet_identity LIKE ?",
        (fid, host + ":%"),
    ).fetchone()["n"]
    if total >= MAX_FLEET_SERVERS or same_ip >= MAX_FLEET_PORTS_PER_IP:
        raise HTTPException(status_code=429, detail="fleet server limit reached")
    try:
        conn.execute(
            """INSERT INTO servers (fleet_id, fleet_identity, name, port, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (fid, identity, (server_info.name or f"{fleet['name']} server")[:120], server_info.port, now),
        )
    except sqlite3.IntegrityError:
        pass  # a concurrent first contact won; fall through to the re-select
    row = conn.execute(
        "SELECT * FROM servers WHERE fleet_id = ? AND fleet_identity = ?", (fid, identity)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="server row vanished during registration")
    return row


# ----------------------------------------------------------------------------- app

def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or load_settings()
    st.validate()
    init_schema(st.db_path)
    app = FastAPI(
        title="DCS Wingman presence service",
        version=__version__,
        docs_url="/docs" if st.docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if st.docs else None,
    )

    def get_conn():
        conn = connect(st.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if not x_admin_key or not hmac.compare_digest(x_admin_key, st.admin_key):
            raise HTTPException(status_code=401, detail="admin key required")

    def transaction(conn: sqlite3.Connection, work):
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = work()
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.post("/v1/admin/servers", dependencies=[Depends(require_admin)])
    def register_server(body: RegisterServer, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        key = new_server_key()
        cur = conn.execute(
            """INSERT INTO servers (key_hash, name, discord_url, srs, website, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key_hash(key), body.name, body.discord_url, body.srs, body.website, body.description, time.time()),
        )
        return {
            "server_id": cur.lastrowid,
            "server_key": key,
            "note": "Store the key in Config\\dcs-wingman.cfg. It is not recoverable; re-register to rotate.",
        }

    @app.post("/v1/admin/fleets", dependencies=[Depends(require_admin)])
    def register_fleet(body: RegisterFleet, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        key = new_fleet_key()
        cur = conn.execute(
            "INSERT INTO fleets (key_hash, name, discord_url, website, created_at) VALUES (?, ?, ?, ?, ?)",
            (key_hash(key), body.name, body.discord_url, body.website, time.time()),
        )
        return {
            "fleet_id": cur.lastrowid,
            "fleet_key": key,
            "note": "One key for every server in the fleet; each server is identified by its ip:port on first report.",
        }

    @app.post("/v1/report")
    def report(
        body: Report,
        request: Request,
        authorization: str | None = Header(default=None),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict:
        now = time.time()
        key = bearer_key(authorization)
        address = format_address(client_ip(request, st.trust_proxy), body.server.port)
        # two short write transactions instead of one long one: identity first, then the roster
        server = transaction(conn, lambda: resolve_server(conn, key, address, body.server, now))
        messages = transaction(
            conn,
            lambda: process_report(
                conn, st, server, address,
                body.server.model_dump(), [p.model_dump() for p in body.players], now,
            ),
        )
        return {"ok": True, "messages": messages}

    @app.post("/v1/command")
    def command(
        body: Command,
        request: Request,
        authorization: str | None = Header(default=None),
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> dict:
        now = time.time()
        key = bearer_key(authorization)
        address = format_address(client_ip(request, st.trust_proxy), body.server.port)
        server = transaction(conn, lambda: resolve_server(conn, key, address, body.server, now))
        messages = transaction(
            conn,
            lambda: process_command(
                conn, st, server,
                Person(ucid=body.actor.ucid, name=body.actor.name or "pilot"),
                body.command, body.arg,
                [Person(ucid=c.ucid, name=c.name or "pilot") for c in body.candidates], now,
            ),
        )
        return {"ok": True, "messages": messages}

    @app.get("/v1/servers")
    def directory(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        now = time.time()
        rows = conn.execute(
            """SELECT s.id, s.name, s.address, s.mission, s.max_players, s.srs, s.description,
                      s.last_report_at,
                      COALESCE(s.discord_url, f.discord_url) AS discord_url,
                      COALESCE(s.website, f.website) AS website,
                      f.name AS fleet_name,
                      (SELECT COUNT(*) FROM presence pr WHERE pr.server_id = s.id) AS players
               FROM servers s LEFT JOIN fleets f ON f.id = s.fleet_id
               WHERE s.last_report_at IS NOT NULL AND s.last_report_at >= ?
               ORDER BY players DESC, s.name COLLATE NOCASE""",
            (now - st.presence_ttl,),
        ).fetchall()
        return {
            "generated_at": int(now),
            "servers": [
                {
                    "name": r["name"], "address": r["address"], "mission": r["mission"],
                    "players": r["players"], "max_players": r["max_players"],
                    "discord_url": r["discord_url"], "srs": r["srs"], "website": r["website"],
                    "description": r["description"], "fleet": r["fleet_name"],
                    "last_seen": int(r["last_report_at"]),
                }
                for r in rows
            ],
        }

    @app.get("/v1/stats")
    def stats(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
        now = time.time()
        cutoff = now - st.presence_ttl
        online = conn.execute(
            "SELECT COUNT(*) AS n FROM servers WHERE last_report_at >= ?", (cutoff,)
        ).fetchone()["n"]
        pilots = conn.execute(
            """SELECT COUNT(*) AS n FROM presence pr JOIN servers s ON s.id = pr.server_id
               WHERE s.last_report_at >= ?""",
            (cutoff,),
        ).fetchone()["n"]
        friendships = conn.execute("SELECT COUNT(*) AS n FROM friendships").fetchone()["n"]
        return {"servers_online": online, "pilots_online": pilots, "friendships": friendships}

    return app
