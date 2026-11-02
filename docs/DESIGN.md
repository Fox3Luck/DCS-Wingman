# DCS Wingman design

Status: Tier 0 built 2026-11-02 (reporter hook + presence service). Client mod next.

## Problem

From the Red Shack podcast (Plasma and Rez, 2026-10-23) and the ED wish-list
threads going back to 2013:

* No friend list tied to your DCS login.
* No way to see whether a friend is online or which server they are on.
* No click-to-join from a friend's presence.
* New pilots land on servers with no SRS and no Discord and nobody talks to them.
* Discovery (servers, Discords, squadrons) happens on Reddit and Facebook, not
  in the game.
* Snapshot on a Wednesday evening: about 1,700 servers listed, about 400 humans.

## What DCS lets a mod do (verified 2026-11-02 on DCS 2.9)

Hooks in `Saved Games\<DCS>\Scripts\Hooks` run in the GUI Lua state on both
the dedicated server and the player client, outside the integrity check. SRS,
Scratchpad and LiveryLink live there.

Client-side `net.*` includes: `serverlist_search/get` (ED's master list: name,
address, player count, mission, map, password flag; no player names),
`start_client(addr, password)` (programmatic join), `get_server_host`,
`get_my_player_id`, `get_player_list`, `get_player_info`, `send_chat`,
`recv_chat`. Callbacks: `onNetConnect`, `onNetDisconnect`, `onPlayerChangeSlot`,
`onSimulationFrame`, `onChatMessage`.

Networking: `require("socket")` works after adding `.\LuaSocket\?.lua` to
`package.path`. DCS ships LuaSocket without TLS, so pure Lua speaks plain HTTP.
LiveryLink proves a DLL can be loaded from `Saved Games\Mods\Services\...\bin`,
which is the path to HTTPS (a small WinHTTP wrapper).

UI: `require("dxgui")` and `require("DialogLoader")` build in-game windows. The
SRS overlay is the reference implementation.

Server side: UCID via `net.get_player_info(id).ucid`; `onPlayerTrySendChat`
can consume chat commands; `net.send_chat_to` replies to one player.

## Architecture

    +------------------+      HTTP/JSON       +---------------------+
    | DCS server hook  | -------------------> | presence service    |
    | (Lua, Tier 0)    | <------------------- | FastAPI + SQLite    |
    +------------------+   chat lines to      +---------------------+
            ^              deliver                 ^           ^
            | chat commands                        |           |
    +------------------+                   +-------------+  +-----------+
    | pilot, no mod    |                   | web directory|  | Discord   |
    +------------------+                   | (Tier 0.5)   |  | bot (0.5) |
                                           +-------------+  +-----------+
    +------------------+   HTTP (DLL for HTTPS later)
    | DCS client mod   | -------------------> service
    | (Lua + dxgui,    |   friends, presence, join
    |  Tier 1)         |
    +------------------+

### Tier 0: server reporter + service (built)

* Reporter posts the roster every 60 s and about 4 s after any connect,
  disconnect or slot change. Three consecutive failures back off to 5 minutes.
* Chat commands `-wingman`, `-friend`, `-friends`, `-unfriend`, `-wingman off|on`
  are consumed server-side and answered privately.
* Friend handshake requires co-presence: both pilots must be on the same server
  at the same time, and both must ask. This kills spam and name-squatting.
* The service greets each pilot once per server per 30 minutes and tells them
  which friends are online right now.
* Public directory: servers reporting in the last 5 minutes with counts,
  Discord invite, SRS address and website. No player names.

### Tier 0.5: web directory + Discord bot (next)

* Static site over `GET /v1/servers`: which servers are alive right now, their
  Discord, SRS, schedule, and a "claim your server" flow for owners.
* Discord bot: `/whoisflying`, `/friends`, DM when a friend comes online,
  account link via a pairing code typed in DCS chat (`-wingman link ABCD`).

### Tier 1: client mod

* In-game window (dxgui): friends, status, server, Join button
  (`net.start_client`), "fly with me" invite.
* Friend requests from the in-game player list, same co-presence rule.
* Pairing code shown in the window links the DCS identity to Discord.

### Tier 2: hardening and growth

* WinHTTP DLL for HTTPS from the hook; signed requests.
* Squadron pages, recruiting, looking-for-group tied to live presence.
* Server owner self-service registration and claim verification (a token the
  owner places in the mission or server description).

## Identity and privacy

* `pid = HMAC-SHA256(pepper, lowercase(ucid))[:32]`. The pepper is a service
  secret. Rotating it orphans every friendship, so it is treated like a signing
  key: generated once, backed up, never casually rotated.
* Raw UCIDs cross the wire from server to service and are used in replies to
  address chat lines, then dropped. They are never written to the database
  (tested).
* Hidden pilots (`-wingman off`) have no presence row and show as offline.
* Server keys are random 128-bit tokens stored as SHA-256 hashes.
* Delete-me and export will be added with the Discord link (Tier 0.5), since
  until then nothing ties a pilot to a person we can authenticate.

## Threats and residuals

| Risk | Mitigation | Residual |
|------|------------|----------|
| Plain HTTP between server and service exposes UCIDs and the server key in transit | Server key only authorizes presence for that one server; UCIDs are already visible to every server a pilot joins | Accepted until the HTTPS DLL; Fox3 fleet traffic can ride a private path |
| A rogue server reports fake rosters | Each server only affects its own presence rows; friendships need both pilots to act from the same server | A rogue server could forge a two-sided handshake between two UCIDs it knows; low value, logged |
| Name impersonation inside a server | Co-presence plus explicit two-way confirmation; replies name the requester | Pilots should check the requester name in the reply |
| Hook blocks the sim frame | 3 s socket timeout, one request per minute, backoff | A slow service costs one 3 s hitch per minute on the server, never on clients |
| ED ships native friends | Directory and Discord layers still stand; the mod can wrap native friends | None |

## Decisions (Joe, 2026-11-02)

1. Neutral community brand, MIT, "sponsored by Fox3". Name: DCS Wingman.
2. Reporter on by default across the Fox3 fleet with per-customer opt-out.
3. Launch with Plasma and Rez (Red Shack podcast) as partners.
4. Hosting approved; service is small enough for a single VM.

## Open items

* Domain: decided 2026-11-02, no purchase. Site https://wingman.fox3cloud.com,
  hook endpoint http://wingman-api.fox3cloud.com (plain HTTP by zone exception).
* Public GitHub repo `Fox3Luck/DCS-Wingman`: done 2026-11-02.
* Service deployed 2026-11-02 (VM 101 on v2 behind a Cloudflare tunnel). Register the first servers (ATL-254 first).
* Fox3 seed integration and the customer opt-out note in the onboarding doc.
