"""Business rules for reports and chat commands. Pure functions over an open connection.

Every function returns a list of {"ucid": ..., "text": ...} messages for the hook to
deliver in chat. Raw UCIDs appear here only transiently, to address replies; the
caller must never log or store the returned message list. The caller also owns the
transaction (BEGIN IMMEDIATE / COMMIT); nothing here commits.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .config import Settings
from .identity import normalize_ucid, ordered_pair, pilot_id

SIDE_NAMES = {1: "red", 2: "blue"}
MAX_LINE = 180
MIN_PREFIX = 3

HELP_TEXT = (
    "DCS Wingman: -friend <name> (pilot on this server), -friends, -unfriend <name>, "
    "-wingman off|on (hide or show your presence)."
)


@dataclass(frozen=True)
class Person:
    """A pilot as the hook sees them: raw UCID plus display name."""
    ucid: str
    name: str


@dataclass(frozen=True)
class Friend:
    """A pilot as the database knows them: hashed pid plus display name."""
    pid: str
    name: str


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE else text[: MAX_LINE - 3] + "..."


def _msg(ucid: str, *lines: str) -> dict:
    return {"ucid": ucid, "text": "\n".join(_clip(line) for line in lines)}


def _side(value) -> int:
    try:
        s = int(value)
    except (TypeError, ValueError):
        return 0
    return s if 0 <= s <= 2 else 0


def _text(value, limit: int) -> str:
    return ("" if value is None else str(value))[:limit]


def upsert_pilot(conn: sqlite3.Connection, pid: str, name: str, now: float) -> None:
    conn.execute(
        """INSERT INTO pilots (pid, name, first_seen, last_seen) VALUES (?, ?, ?, ?)
           ON CONFLICT(pid) DO UPDATE SET name = excluded.name, last_seen = excluded.last_seen""",
        (pid, _text(name, 64) or "pilot", now, now),
    )


def is_hidden(conn: sqlite3.Connection, pid: str) -> bool:
    row = conn.execute("SELECT hidden FROM pilots WHERE pid = ?", (pid,)).fetchone()
    return bool(row and row["hidden"])


def friends_of(conn: sqlite3.Connection, pid: str) -> list[Friend]:
    rows = conn.execute(
        """SELECT p.pid, p.name FROM friendships f
           JOIN pilots p ON p.pid = CASE WHEN f.a_pid = ? THEN f.b_pid ELSE f.a_pid END
           WHERE f.a_pid = ? OR f.b_pid = ? ORDER BY p.name COLLATE NOCASE""",
        (pid, pid, pid),
    ).fetchall()
    return [Friend(pid=r["pid"], name=r["name"]) for r in rows]


def are_friends(conn: sqlite3.Connection, a: str, b: str) -> bool:
    x, y = ordered_pair(a, b)
    row = conn.execute("SELECT 1 FROM friendships WHERE a_pid = ? AND b_pid = ?", (x, y)).fetchone()
    return row is not None


def where_is(conn: sqlite3.Connection, st: Settings, pid: str, now: float) -> str | None:
    """'Server name (blue)' if the pilot is visibly online, else None."""
    row = conn.execute(
        """SELECT pr.side, s.name AS server_name, s.last_report_at, p.hidden
           FROM presence pr
           JOIN servers s ON s.id = pr.server_id
           JOIN pilots p ON p.pid = pr.pid
           WHERE pr.pid = ?""",
        (pid,),
    ).fetchone()
    if not row or row["hidden"] or (now - (row["last_report_at"] or 0)) > st.presence_ttl:
        return None
    side = SIDE_NAMES.get(row["side"])
    return f"{row['server_name']} ({side})" if side else row["server_name"]


def friends_online_line(conn: sqlite3.Connection, st: Settings, pid: str, now: float) -> str | None:
    parts = []
    for fr in friends_of(conn, pid):
        loc = where_is(conn, st, fr.pid, now)
        if loc:
            parts.append(f"{fr.name} on {loc}")
    if not parts:
        return None
    shown = ", ".join(parts[:5])
    extra = f" and {len(parts) - 5} more" if len(parts) > 5 else ""
    return f"Friends online: {shown}{extra}"


def expire_old_rows(conn: sqlite3.Connection, st: Settings, now: float) -> None:
    conn.execute("DELETE FROM friend_requests WHERE created_at < ?", (now - st.request_ttl,))
    conn.execute("DELETE FROM greetings WHERE at < ?", (now - 2 * st.greet_gap,))


# ----------------------------------------------------------------------------- reports

def process_report(
    conn: sqlite3.Connection,
    st: Settings,
    server: sqlite3.Row,
    address: str,
    server_info: dict,
    players: list[dict],
    now: float,
) -> list[dict]:
    sid = server["id"]
    conn.execute(
        """UPDATE servers SET name = COALESCE(?, name), port = COALESCE(?, port),
           max_players = COALESCE(?, max_players), mission = COALESCE(?, mission),
           description = COALESCE(?, description), address = ?, last_report_at = ?
           WHERE id = ?""",
        (
            server_info.get("name") or None,
            server_info.get("port"),
            server_info.get("max_players"),
            server_info.get("mission") or None,
            server_info.get("description") or None,
            address,
            now,
            sid,
        ),
    )
    expire_old_rows(conn, st, now)

    present: list[tuple[str, str]] = []  # (pid, ucid)
    seen: set[str] = set()
    for p in players[: st.max_players_per_report]:
        pid = pilot_id(st.pepper, p["ucid"])
        if pid in seen:
            continue
        seen.add(pid)
        upsert_pilot(conn, pid, p.get("name") or "", now)
        present.append((pid, p["ucid"]))
        if is_hidden(conn, pid):
            conn.execute("DELETE FROM presence WHERE pid = ?", (pid,))
            continue
        conn.execute(
            """INSERT INTO presence (pid, server_id, side, slot, since, last_seen)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(pid) DO UPDATE SET
                 side = excluded.side, slot = excluded.slot, last_seen = excluded.last_seen,
                 since = CASE WHEN presence.server_id = excluded.server_id
                              THEN presence.since ELSE excluded.since END,
                 server_id = excluded.server_id""",
            (pid, sid, _side(p.get("side")), _text(p.get("slot"), 64), now, now),
        )
    # Players who left this server since the last report: computed in Python so the
    # statement never carries more than one bound variable per row.
    stale = [
        r["pid"] for r in conn.execute("SELECT pid FROM presence WHERE server_id = ?", (sid,)).fetchall()
        if r["pid"] not in seen
    ]
    conn.executemany("DELETE FROM presence WHERE pid = ? AND server_id = ?", [(pid, sid) for pid in stale])

    messages: list[dict] = []
    for pid, ucid in present:
        row = conn.execute(
            "SELECT at FROM greetings WHERE pid = ? AND server_id = ?", (pid, sid)
        ).fetchone()
        if row and (now - row["at"]) < st.greet_gap:
            continue
        conn.execute(
            """INSERT INTO greetings (pid, server_id, at) VALUES (?, ?, ?)
               ON CONFLICT(pid, server_id) DO UPDATE SET at = excluded.at""",
            (pid, sid, now),
        )
        lines = [
            "DCS Wingman is on this server. Type -wingman for help, "
            "or -friend <name> to friend a pilot you are flying with."
        ]
        online = friends_online_line(conn, st, pid, now)
        if online:
            lines.append(online)
        messages.append(_msg(ucid, *lines))
    return messages


# ----------------------------------------------------------------------------- commands

def match_name(arg: str, candidates, scope: str, example: str = "-friend Maverick", allow_prefix: bool = True):
    """Resolve a typed name against objects with a .name: exact (case-insensitive) first,
    then a unique prefix of at least MIN_PREFIX characters. Ambiguity is refused, never
    guessed. Returns (match, None) or (None, error_text)."""
    a = arg.strip().lower()
    shown = arg.strip()
    if not a:
        return None, f"Give a pilot name, for example: {example}."
    exact = [c for c in candidates if c.name.lower() == a]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"More than one pilot named '{shown}' is {scope}. Ask them to use a unique name."
    if allow_prefix and len(a) >= MIN_PREFIX:
        prefix = [c for c in candidates if c.name.lower().startswith(a)]
        if len(prefix) == 1:
            return prefix[0], None
        if len(prefix) > 1:
            names = ", ".join(c.name for c in prefix[:6])
            return None, f"Several pilots match '{shown}': {names}. Type the full name."
    return None, f"No pilot named '{shown}' is {scope}."


def process_command(
    conn: sqlite3.Connection,
    st: Settings,
    server: sqlite3.Row,
    actor: Person,
    command: str,
    arg: str,
    candidates: list[Person],
    now: float,
) -> list[dict]:
    me = pilot_id(st.pepper, actor.ucid)
    upsert_pilot(conn, me, actor.name, now)
    expire_old_rows(conn, st, now)
    my_ucid = normalize_ucid(actor.ucid)
    others = [c for c in candidates if normalize_ucid(c.ucid) != my_ucid]
    command = command.lower()
    arg = arg.strip()

    if command == "friend":
        target, err = match_name(arg, others, "on this server right now")
        if err:
            return [_msg(actor.ucid, err + " You can only friend pilots you are flying with.")]
        them = pilot_id(st.pepper, target.ucid)
        if them == me:
            return [_msg(actor.ucid, "You cannot friend yourself.")]
        upsert_pilot(conn, them, target.name, now)
        if are_friends(conn, me, them):
            return [_msg(actor.ucid, f"You and {target.name} are already friends.")]
        reverse = conn.execute(
            "SELECT 1 FROM friend_requests WHERE from_pid = ? AND to_pid = ? AND created_at >= ?",
            (them, me, now - st.request_ttl),
        ).fetchone()
        if reverse:
            a, b = ordered_pair(me, them)
            conn.execute(
                "INSERT OR IGNORE INTO friendships (a_pid, b_pid, created_at) VALUES (?, ?, ?)",
                (a, b, now),
            )
            conn.execute(
                """DELETE FROM friend_requests
                   WHERE (from_pid = ? AND to_pid = ?) OR (from_pid = ? AND to_pid = ?)""",
                (me, them, them, me),
            )
            return [
                _msg(actor.ucid, f"You and {target.name} are now friends. -friends shows where they fly."),
                _msg(target.ucid, f"You and {actor.name} are now friends. -friends shows where they fly."),
            ]
        conn.execute(
            """INSERT INTO friend_requests (from_pid, to_pid, created_at) VALUES (?, ?, ?)
               ON CONFLICT(from_pid, to_pid) DO UPDATE SET created_at = excluded.created_at""",
            (me, them, now),
        )
        return [
            _msg(actor.ucid, f"Friend request sent to {target.name}. They accept with: -friend {actor.name}"),
            _msg(target.ucid, f"{actor.name} wants to be your wingman. Accept with: -friend {actor.name}"),
        ]

    if command == "friends":
        friends = friends_of(conn, me)
        if not friends:
            return [_msg(actor.ucid, "No friends yet. Use -friend <name> on a pilot you are flying with.")]
        lines = []
        for fr in friends[:20]:
            loc = where_is(conn, st, fr.pid, now)
            lines.append(f"{fr.name}: {loc if loc else 'offline'}")
        if len(friends) > 20:
            lines.append(f"...and {len(friends) - 20} more")
        return [_msg(actor.ucid, *lines)]

    if command == "unfriend":
        friends = friends_of(conn, me)
        target, err = match_name(arg, friends, "in your friends", example="-unfriend Maverick", allow_prefix=False)
        if err:
            return [_msg(actor.ucid, err)]
        a, b = ordered_pair(me, target.pid)
        conn.execute("DELETE FROM friendships WHERE a_pid = ? AND b_pid = ?", (a, b))
        return [_msg(actor.ucid, f"{target.name} removed from your friends.")]

    if command == "wingman":
        sub = arg.lower()
        if sub in ("", "help"):
            return [_msg(actor.ucid, HELP_TEXT)]
        if sub == "off":
            conn.execute("UPDATE pilots SET hidden = 1 WHERE pid = ?", (me,))
            conn.execute("DELETE FROM presence WHERE pid = ?", (me,))
            return [_msg(actor.ucid, "Presence hidden. Friends will see you as offline. -wingman on undoes this.")]
        if sub == "on":
            conn.execute("UPDATE pilots SET hidden = 0 WHERE pid = ?", (me,))
            return [_msg(actor.ucid, "Presence visible again. Friends can see which server you are on.")]
        return [_msg(actor.ucid, "Unknown option. " + HELP_TEXT)]

    return [_msg(actor.ucid, "Unknown command. " + HELP_TEXT)]
