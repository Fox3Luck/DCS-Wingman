# DCS Wingman

**Find your wingman again.** Free, open-source friends, presence and
server/Discord discovery for DCS World. Sponsored by Fox3 Managed Solutions.

You fly two hours with a stranger on a public server, you save each other a
couple of times, and then the mission ends and you never see them again. DCS has
no friend list, no way to see who is online, and no way to find the Discord that
goes with the server you just left. DCS Wingman fixes that without waiting for
Eagle Dynamics.

**Live:** directory and site at https://wingman.fox3cloud.com. Server hooks report to
`http://wingman-api.fox3cloud.com` (plain HTTP on purpose: DCS Lua has no TLS).

## How it works

DCS Wingman has three layers. Each one is useful on its own.

| Layer | Who installs it | What it gives you | Status |
|-------|-----------------|-------------------|--------|
| **Server reporter** (`hooks/server`) | Server owners, one Lua file | Pilots on that server can `-friend` each other from chat, see where friends are flying, and get the server's Discord and SRS details. No client install. | live |
| **Presence service** (`service`) | We host it | Friendships, presence, server directory. Raw UCIDs are never stored. | live |
| **Client mod** (`hooks/client`) | Pilots who want more | In-game friends window with Join buttons, invites, Discord link. | next |

Fox3 ships the reporter on its hosted servers by default (owners can opt out),
so the first day DCS Wingman is live, a few hundred servers already speak it.

## For pilots

On a server that runs DCS Wingman you will see one line in chat when you
connect. Then:

    -wingman            help
    -friend <name>      friend a pilot who is on this server right now
    -friends            your friends and where they are flying
    -unfriend <name>    remove a friend
    -wingman off | on   hide or show your presence

You can only friend a pilot you are actually flying with. That is on purpose:
it stops spam and it matches how wingmen are made.

## For server owners

See `hooks/server/INSTALL.md`. One file, one config, restart the server.

## Privacy

* Your DCS UCID is hashed with a secret pepper before it is stored. The service
  never keeps the raw value.
* Presence is per pilot and reversible: `-wingman off` hides you and deletes
  your current location.
* The public server directory shows counts, never names.
* No account, email, or Discord login is needed for the chat commands.

## Repository layout

    hooks/server/     DCS dedicated-server hook (Lua) + install guide
    service/          presence service (FastAPI + SQLite) + tests + systemd unit
    docs/DESIGN.md    architecture, API, decisions, roadmap
    docs/LAUNCH.md    launch plan and outreach drafts

## License

MIT. See `LICENSE`.
