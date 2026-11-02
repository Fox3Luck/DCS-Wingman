"""End-to-end tests over the HTTP API with a temporary SQLite file."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from wingman_service.config import Settings
from wingman_service.logic import MIN_PREFIX, Person, match_name
from wingman_service.main import create_app, format_address

ADMIN = "admin-key-for-tests-0123456789abcdef"
UCID_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
UCID_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
UCID_C = "cccccccccccccccccccccccccccccccc"
UCID_D = "dddddddddddddddddddddddddddddddd"

PA = {"ucid": UCID_A, "name": "Plasma", "side": 2, "slot": "12"}
PB = {"ucid": UCID_B, "name": "Rez", "side": 2, "slot": "13"}
PC = {"ucid": UCID_C, "name": "Rezzo", "side": 1, "slot": "3"}
PD = {"ucid": UCID_D, "name": "Rez", "side": 1, "slot": "4"}


def make_settings(tmp_path, **kw):
    base = dict(db_path=str(tmp_path / "t.db"), pepper=b"p" * 32, admin_key=ADMIN, presence_ttl=300)
    base.update(kw)
    return Settings(**base)


CLIENT_ADDR = ("203.0.113.5", 51000)


@pytest.fixture()
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path)), client=CLIENT_ADDR) as c:
        yield c


def register(client, name, **extra):
    body = {"name": name, "discord_url": "https://discord.gg/x"}
    body.update(extra)
    r = client.post("/v1/admin/servers", json=body, headers={"X-Admin-Key": ADMIN})
    assert r.status_code == 200, r.text
    return r.json()["server_key"]


def report(client, key, players, name="Srv", port=10308, headers=None):
    h = {"Authorization": f"Bearer {key}"}
    h.update(headers or {})
    r = client.post(
        "/v1/report",
        json={"server": {"name": name, "port": port, "max_players": 32, "mission": "Op"}, "players": players},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def command(client, key, actor, cmd, arg="", candidates=()):
    r = client.post(
        "/v1/command",
        json={"actor": actor, "command": cmd, "arg": arg, "candidates": list(candidates)},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200, r.text
    return {m["ucid"]: m["text"] for m in r.json()["messages"]}


# ----------------------------------------------------------------------------- auth and bounds

def test_bad_keys_are_rejected(client):
    assert client.post("/v1/admin/servers", json={"name": "x"}, headers={"X-Admin-Key": "nope"}).status_code == 401
    assert client.post("/v1/report", json={"players": []}, headers={"Authorization": "Bearer wsk_bad"}).status_code == 401
    assert client.post("/v1/report", json={"players": []}).status_code == 401
    cmd = {"actor": PA, "command": "friends"}
    assert client.post("/v1/command", json=cmd).status_code == 401
    assert client.post("/v1/command", json=cmd, headers={"Authorization": "Bearer wsk_bad"}).status_code == 401


def test_weak_secrets_are_refused(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(make_settings(tmp_path, pepper=b"short"))
    with pytest.raises(RuntimeError):
        create_app(make_settings(tmp_path, pepper=b" " * 40))
    with pytest.raises(RuntimeError):
        create_app(make_settings(tmp_path, admin_key="tooshort"))


def test_pydantic_bounds(client):
    key = register(client, "Srv")
    h = {"Authorization": f"Bearer {key}"}
    assert client.post("/v1/report", json={"players": [{"ucid": "short", "name": "x"}]}, headers=h).status_code == 422
    assert client.post("/v1/report", json={"server": {"port": 0}, "players": []}, headers=h).status_code == 422
    assert client.post("/v1/report", json={"players": [{"ucid": UCID_A, "side": 7}]}, headers=h).status_code == 422
    big = [{"ucid": f"{i:032x}", "name": f"p{i}"} for i in range(1001)]
    assert client.post("/v1/report", json={"players": big}, headers=h).status_code == 422
    assert client.post("/v1/command", json={"actor": PA, "command": "x" * 17}, headers=h).status_code == 422
    assert client.post("/v1/admin/servers", json={"name": "Bad", "discord_url": "javascript:alert(1)"},
                       headers={"X-Admin-Key": ADMIN}).status_code == 422
    assert client.post("/v1/admin/servers", json={"name": "Bad", "srs": "not a host"},
                       headers={"X-Admin-Key": ADMIN}).status_code == 422
    for bad_srs in ("srs.example.com:0", "srs.example.com:99999"):
        assert client.post("/v1/admin/servers", json={"name": "Bad", "srs": bad_srs},
                           headers={"X-Admin-Key": ADMIN}).status_code == 422
    ok = client.post("/v1/admin/servers", json={"name": "Good", "srs": "srs.example.com:5002",
                                                 "website": "HTTPS://Example.com/x"}, headers={"X-Admin-Key": ADMIN})
    assert ok.status_code == 200
    full = [{"ucid": f"{i:032x}", "name": f"p{i}"} for i in range(1000)]
    assert client.post("/v1/report", json={"players": full}, headers=h).status_code == 200


def test_env_settings_are_stripped_and_bounded(monkeypatch, tmp_path):
    from wingman_service.config import load_settings
    monkeypatch.setenv("WINGMAN_DB", str(tmp_path / "e.db"))
    monkeypatch.setenv("WINGMAN_PEPPER", "p" * 40 + "\n")
    monkeypatch.setenv("WINGMAN_ADMIN_KEY", " " + ADMIN + "\n")
    st = load_settings()
    assert st.pepper == b"p" * 40 and st.admin_key == ADMIN
    monkeypatch.setenv("WINGMAN_PRESENCE_TTL", "0")
    with pytest.raises(RuntimeError):
        load_settings()


def test_docs_are_off_by_default(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# ----------------------------------------------------------------------------- reports

def test_first_report_greets_each_pilot_once(client):
    key = register(client, "Contention")
    msgs = report(client, key, [PA, PB])
    assert {m["ucid"] for m in msgs} == {UCID_A, UCID_B}
    assert "DCS Wingman is on this server" in msgs[0]["text"]
    assert report(client, key, [PA, PB]) == []  # no re-greeting inside the greet gap


def test_lua_style_tables_are_accepted(client):
    key = register(client, "Lua")
    h = {"Authorization": f"Bearer {key}"}
    assert client.post("/v1/report", json={"server": {}, "players": {}}, headers=h).status_code == 200
    r = client.post("/v1/report", json={"players": {"1": PA, "2": PB}}, headers=h)
    assert r.status_code == 200
    assert client.get("/v1/stats").json()["pilots_online"] == 2
    r = client.post("/v1/command", json={"actor": PA, "command": "friend", "arg": "Rez", "candidates": {"1": PB}}, headers=h)
    assert r.status_code == 200 and "Friend request sent" in r.json()["messages"][0]["text"]


def test_empty_roster_clears_presence_and_presence_moves_between_servers(client):
    k1 = register(client, "One")
    k2 = register(client, "Two")
    report(client, k1, [PA, PB], name="One")
    assert client.get("/v1/stats").json()["pilots_online"] == 2
    report(client, k2, [PB], name="Two")  # Rez moved: presence row follows, One keeps only Plasma
    d = {s["name"]: s["players"] for s in client.get("/v1/servers").json()["servers"]}
    assert d == {"One": 1, "Two": 1}
    report(client, k1, [], name="One")
    assert client.get("/v1/stats").json()["pilots_online"] == 1


def test_missing_mission_keeps_previous_value(client):
    key = register(client, "Srv")
    report(client, key, [PA], name="Srv")
    client.post("/v1/report", json={"server": {"name": "Srv"}, "players": [PA]}, headers={"Authorization": f"Bearer {key}"})
    assert client.get("/v1/servers").json()["servers"][0]["mission"] == "Op"


def test_forwarded_headers(tmp_path):
    with TestClient(create_app(make_settings(tmp_path)), client=CLIENT_ADDR) as c:
        key = register(c, "NoProxy")
        report(c, key, [PA], headers={"X-Forwarded-For": "203.0.113.9"})
        assert c.get("/v1/servers").json()["servers"][0]["address"] == "203.0.113.5:10308"  # header ignored
    with TestClient(create_app(make_settings(tmp_path, db_path=str(tmp_path / "p.db"), trust_proxy=True)),
                    client=CLIENT_ADDR) as c:
        key = register(c, "Proxy")
        report(c, key, [PA], headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
        assert c.get("/v1/servers").json()["servers"][0]["address"] == "unknown"  # XFF never trusted, no CF header = no identity
        report(c, key, [PA], headers={"CF-Connecting-IP": "2001:db8::1", "X-Forwarded-For": "203.0.113.9"})
        assert c.get("/v1/servers").json()["servers"][0]["address"] == "[2001:db8::1]:10308"  # CF wins
        report(c, key, [PA], headers={"CF-Connecting-IP": "<script>alert(1)</script>"})
        assert c.get("/v1/servers").json()["servers"][0]["address"] == "unknown"


def test_format_address():
    assert format_address("1.2.3.4", 10308) == "1.2.3.4:10308"
    assert format_address("1.2.3.4", None) == "1.2.3.4"
    assert format_address("2001:db8::1", 1) == "[2001:db8::1]:1"
    assert format_address("junk", 1) == "unknown"
    assert format_address(None, 1) == "unknown"


# ----------------------------------------------------------------------------- commands

def test_match_name_rules():
    people = [Person("u1", "Rez"), Person("u2", "Rezzo"), Person("u3", "a"), Person("u4", "Maverick")]
    assert match_name("rez", people, "here")[0].name == "Rez"
    assert match_name("rezz", people, "here")[0].name == "Rezzo"
    assert match_name("mav", people, "here")[0].name == "Maverick"
    assert len("re") < MIN_PREFIX and match_name("re", people, "here")[0] is None  # too short for a prefix
    assert match_name("a", people, "here")[0].name == "a"  # exact match still works
    assert match_name("rez", people + [Person("u5", "Rezident")], "here")[0].name == "Rez"  # exact beats prefixes
    assert "Several" in match_name("rezi", [Person("u5", "Rezident"), Person("u6", "Rezina")], "here")[1]
    dup = [Person("u1", "Rez"), Person("u2", "Rez")]
    assert "More than one" in match_name("Rez", dup, "here")[1]
    assert match_name("Re", dup, "here", allow_prefix=False)[0] is None


def test_friend_requires_copresence_and_unique_match(client):
    key = register(client, "Srv")
    m = command(client, key, PA, "friend", "Maverick", [PB, PC])
    assert "No pilot named 'Maverick'" in m[UCID_A] and "flying with" in m[UCID_A]
    m = command(client, key, PA, "friend", "rez", [PB, PC])  # exact match beats the prefix 'Rezzo'
    assert "Friend request sent to Rez" in m[UCID_A]
    assert "wants to be your wingman" in m[UCID_B]
    m = command(client, key, PA, "friend", "", [PB])
    assert "Give a pilot name" in m[UCID_A]
    m = command(client, key, PA, "friend", "Rez", [PB, PD])  # two pilots named Rez on the server
    assert "More than one pilot named 'Rez'" in m[UCID_A]


def test_cannot_friend_yourself_even_with_ucid_case_games(client):
    key = register(client, "Srv")
    me_upper = dict(PA, ucid=UCID_A.upper())
    m = command(client, key, PA, "friend", "Plasma", [me_upper, PB])
    assert "No pilot named 'Plasma'" in m[UCID_A] or "cannot friend yourself" in m[UCID_A]
    assert client.get("/v1/stats").json()["friendships"] == 0


def test_handshake_creates_friendship_and_shows_presence_across_servers(client):
    k1 = register(client, "Contention")
    k2 = register(client, "Gray Flag")
    command(client, k1, PA, "friend", "Rez", [PB])
    command(client, k1, PA, "friend", "Rez", [PB])  # duplicate request only refreshes
    m = command(client, k1, PB, "friend", "Plasma", [PA])
    assert "now friends" in m[UCID_A] and "now friends" in m[UCID_B]
    assert "already friends" in command(client, k1, PB, "friend", "Plasma", [PA])[UCID_B]
    report(client, k2, [PB], name="Gray Flag")  # Rez moves to another server
    m = command(client, k1, PA, "friends")
    assert m[UCID_A] == "Rez: Gray Flag (blue)"
    msgs = report(client, k2, [PB, PA], name="Gray Flag")  # Plasma joins Rez there and is greeted
    assert any("Friends online: Rez on Gray Flag (blue)" in x["text"] for x in msgs)
    m = command(client, k1, PA, "unfriend", "Rez")
    assert "removed" in m[UCID_A]
    assert "No friends yet" in command(client, k1, PA, "friends")[UCID_A]


def test_unfriend_refuses_ambiguous_display_names(client):
    key = register(client, "Srv")
    for other in (PB, PD):  # two different pilots both named Rez
        command(client, key, PA, "friend", other["name"], [other])
        command(client, key, other, "friend", "Plasma", [PA])
    assert client.get("/v1/stats").json()["friendships"] == 2
    m = command(client, key, PA, "unfriend", "Rez")
    assert "More than one pilot named 'Rez' is in your friends" in m[UCID_A]
    assert "No pilot named 'Re'" in command(client, key, PA, "unfriend", "Re")[UCID_A]  # exact only
    assert "-unfriend Maverick" in command(client, key, PA, "unfriend", "")[UCID_A]
    assert client.get("/v1/stats").json()["friendships"] == 2


def test_stale_friend_request_does_not_complete(tmp_path):
    import time
    with TestClient(create_app(make_settings(tmp_path, request_ttl=1))) as c:
        key = register(c, "Srv")
        command(c, key, PA, "friend", "Rez", [PB])
        time.sleep(1.2)  # the pending request ages past request_ttl
        m = command(c, key, PB, "friend", "Plasma", [PA])
        assert "Friend request sent" in m[UCID_B]  # the old request expired, so this starts a new one
        assert c.get("/v1/stats").json()["friendships"] == 0


def test_bare_wingman_is_help(client):
    key = register(client, "Srv")
    m = command(client, key, PA, "wingman")
    assert m[UCID_A].startswith("DCS Wingman:") and "-friend" in m[UCID_A]
    assert "Unknown option" in command(client, key, PA, "wingman", "bogus")[UCID_A]


def test_hidden_pilot_is_offline_to_friends_and_absent_from_counts(client):
    key = register(client, "Srv")
    command(client, key, PA, "friend", "Rez", [PB])
    command(client, key, PB, "friend", "Plasma", [PA])
    report(client, key, [PA, PB])
    assert client.get("/v1/stats").json()["pilots_online"] == 2
    m = command(client, key, PB, "wingman", "off")
    assert "hidden" in m[UCID_B]
    report(client, key, [PA, PB])
    assert command(client, key, PA, "friends")[UCID_A] == "Rez: offline"
    assert client.get("/v1/stats").json()["pilots_online"] == 1
    assert client.get("/v1/servers").json()["servers"][0]["players"] == 1
    command(client, key, PB, "wingman", "on")
    report(client, key, [PA, PB])
    assert command(client, key, PA, "friends")[UCID_A] == "Rez: Srv (blue)"


def test_directory_and_stats_never_expose_names_or_ucids(client):
    key = register(client, "Contention")
    report(client, key, [PA, PB], name="Contention Gulf")
    d = client.get("/v1/servers").json()
    assert len(d["servers"]) == 1
    s = d["servers"][0]
    assert s["name"] == "Contention Gulf" and s["players"] == 2 and s["discord_url"] == "https://discord.gg/x"
    for blob in (str(d), str(client.get("/v1/stats").json())):
        assert "Plasma" not in blob and "Rez" not in blob and UCID_A not in blob
    assert s["address"].endswith(":10308")


def test_fleet_key_registers_servers_by_address_and_port(client):
    r = client.post("/v1/admin/fleets", json={"name": "Fox3", "discord_url": "https://discord.gg/fox3"},
                    headers={"X-Admin-Key": ADMIN})
    assert r.status_code == 200, r.text
    fk = r.json()["fleet_key"]
    assert fk.startswith("wfk_")
    # two DCS instances on the same host, different ports, become two servers
    report(client, fk, [PA], name="Fox3 A6-105", port=10308)
    report(client, fk, [PB], name="Fox3 A6-106", port=10309)
    d = client.get("/v1/servers").json()["servers"]
    assert {s["name"] for s in d} == {"Fox3 A6-105", "Fox3 A6-106"}
    assert all(s["fleet"] == "Fox3" and s["discord_url"] == "https://discord.gg/fox3" for s in d)
    assert client.get("/v1/stats").json()["servers_online"] == 2
    # the same instance reporting again updates, it does not duplicate
    report(client, fk, [PA, PB], name="Fox3 A6-105", port=10308)
    assert client.get("/v1/stats").json()["servers_online"] == 2
    # commands carry the port so they resolve to the right fleet server
    r = client.post("/v1/command", json={"server": {"port": 10308}, "actor": PA, "command": "friend",
                                         "arg": "Rez", "candidates": [PB]},
                    headers={"Authorization": f"Bearer {fk}"})
    assert r.status_code == 200 and "Friend request sent" in r.json()["messages"][0]["text"]
    # a fleet report without a port cannot be attributed and is refused
    r = client.post("/v1/report", json={"server": {"name": "x"}, "players": []}, headers={"Authorization": f"Bearer {fk}"})
    assert r.status_code == 400
    # first contact through a command also creates the row, and a later report reuses it
    r = client.post("/v1/command", json={"server": {"name": "Fox3 A6-107", "port": 10310}, "actor": PC, "command": "friends"},
                    headers={"Authorization": f"Bearer {fk}"})
    assert r.status_code == 200
    report(client, fk, [PC], name="Fox3 A6-107", port=10310)
    assert client.get("/v1/stats").json()["servers_online"] == 3
    # an individually keyed server at the same address and port is a separate, untouched row
    sk = register(client, "Own key server")
    report(client, sk, [PA], name="Own key server", port=10308)
    names = {s["name"] for s in client.get("/v1/servers").json()["servers"]}
    assert "Own key server" in names and "Fox3 A6-105" in names


def test_fleet_behind_tunnel_requires_cf_header(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, trust_proxy=True)), client=("127.0.0.1", 40000)) as c:
        fk = c.post("/v1/admin/fleets", json={"name": "Fox3"}, headers={"X-Admin-Key": ADMIN}).json()["fleet_key"]
        body = {"server": {"name": "A", "port": 10308}, "players": [PA]}
        h = {"Authorization": f"Bearer {fk}"}
        assert c.post("/v1/report", json=body, headers=h).status_code == 400  # no CF header: no identity
        h2 = dict(h, **{"CF-Connecting-IP": "198.51.100.7"})
        assert c.post("/v1/report", json=body, headers=h2).status_code == 200
        h3 = dict(h, **{"CF-Connecting-IP": "198.51.100.8"})
        assert c.post("/v1/report", json=body, headers=h3).status_code == 200
        assert c.get("/v1/stats").json()["servers_online"] == 2  # two origins, two rows


def test_fleet_growth_is_capped_per_address(client):
    from wingman_service import main as m
    fk = client.post("/v1/admin/fleets", json={"name": "Cap"}, headers={"X-Admin-Key": ADMIN}).json()["fleet_key"]
    h = {"Authorization": f"Bearer {fk}"}
    for port in range(20000, 20000 + m.MAX_FLEET_PORTS_PER_IP):
        assert client.post("/v1/report", json={"server": {"port": port}, "players": []}, headers=h).status_code == 200
    assert client.post("/v1/report", json={"server": {"port": 30000}, "players": []}, headers=h).status_code == 429
    # an already known identity is still served
    assert client.post("/v1/report", json={"server": {"port": 20000}, "players": []}, headers=h).status_code == 200


def test_old_schema_database_is_refused(tmp_path):
    from wingman_service.db import SchemaError
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute("CREATE TABLE servers (id INTEGER PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError):
        create_app(make_settings(tmp_path, db_path=str(old)))


def test_raw_ucid_never_hits_the_database(client, tmp_path):
    key = register(client, "Srv")
    report(client, key, [PA])
    command(client, key, PA, "friend", "Rez", [PB])
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert UCID_A not in dump and UCID_B not in dump
