# DCS Wingman reporter - server owner install

The reporter is one Lua file. It runs inside the DCS dedicated server, reports
who is flying to the DCS Wingman presence service, and answers chat commands so
pilots on your server can use DCS Wingman without installing anything.

## Steps

1. Register your server and receive a `server_key` (see the service README, or
   the registration form once it exists). Keep the key private; it only
   authorizes presence reports for your server.
2. Copy `DCS-Wingman-Reporter.lua` to
   `Saved Games\<DCS profile>\Scripts\Hooks\` on the server machine.
   The profile is usually `DCS.openbeta_server` or `DCS.server`.
3. Copy `dcs-wingman.cfg.example` to `Saved Games\<DCS profile>\Config\dcs-wingman.cfg`
   and fill in `server_key`. `api_url` is already set to the public service,
   `http://wingman-api.fox3cloud.com`.
4. Restart the DCS server. In `Logs\dcs.log` look for a line tagged `WINGMAN`:
   `DCS-Wingman-Reporter 0.1.0 loaded`. If the line says `inert`, the reason follows.

## What pilots see

When a pilot connects they get one welcome line in chat, plus a line naming
any of their friends who are online right now. They can then type:

    -wingman            help
    -friend <name>      friend a pilot who is on this server right now
    -friends            list friends and where they are flying
    -unfriend <name>    remove a friend
    -wingman off | on   hide or show their own presence

Commands never appear in public chat.

## Removing it

Delete the hook file (or set `enabled = false` in the config) and restart.
Presence for your server expires on the service within five minutes.

## Fox3 hosted servers

Fox3 ships the reporter in the server seed, so Fox3 customers get it on the
next boot. Opt out by setting `enabled = false` in `Config\dcs-wingman.cfg`.
