# DCS Wingman launch plan

## Sequence

1. Service live on a public host, HTTP listener for hooks, HTTPS for the site.
2. Reporter on ATL-254 and two or three friendly community servers for a week.
3. Fox3 seed ships the reporter to the hosted fleet (customers opted in by
   default, `enabled = false` to opt out). Customer notice goes out the same day.
4. Public repo, DCS User Files entry, ED forum thread in Mods and Apps, r/hoggit
   post, Hoggit wiki page.
5. Red Shack episode with Plasma and Rez: the problem they described, the fix,
   live demo of `-friend` and `-friends` across two servers.
6. Client mod follows once the chat-command tier has a month of real traffic.

## Outreach drafts for Joe

### YouTube comment on the "where are the Pilots?" episode

Luck here (Fox3). You asked, so here is the comment, and here is the answer: we
are building it. DCS Wingman is a free, open-source friend list for DCS. On any
server that runs it you type -friend <name> to friend the pilot you are flying
with, and -friends later tells you which server they are on. No client install
needed for that part; an in-game window with a Join button comes next. Fox3
hosted servers will run it by default. Want to help us launch it on the show?

### Direct message to Plasma and Rez

Plasma, Rez. Your episode on the multiplayer desert landed. We took the friend
list problem and built the first piece: a server-side hook plus a small service
so pilots can friend each other from chat and see where friends fly, with no
client install. Fox3 ships it on our hosted fleet by default, so it starts with
a few hundred servers. It is free and MIT licensed, and the name is DCS Wingman.

Would you two be the launch partners? Concretely: an early look this week, your
servers on it first, and an episode where we demo it live and you tell us what
is missing. We will credit the show as the origin of the project.

### Discord note to BIGNEWY (Eagle Dynamics community manager)

Hi BIGNEWY, Luck from Fox3 Servers here. Heads-up on a community project we are
launching, since it touches multiplayer and I would rather you hear it from us.

DCS Wingman is a free, open-source friend list and presence layer for DCS. Pilots on
a participating server type -friend <name> in chat to friend someone they are flying
with, and -friends later shows which server each friend is on. Server owners add one
Lua file in Scripts\Hooks. Nothing in the install folder is touched, nothing patches
the client, and it passes integrity check because it lives in Saved Games like SRS
and Scratchpad. Fox3 will run it by default on our hosted servers.

Repo: github.com/Fox3Luck/DCS-Wingman. It is independent and MIT licensed, with Fox3
as sponsor, and we are clear everywhere that it is not an ED product. If ED ever
ships native friends we will happily point people at that instead. If there is
anything you would like us to word differently, or a better channel for this kind
of heads-up, tell me and we will adjust.

## Distribution checklist

- [x] Domain decided 2026-11-02: wingman.fox3cloud.com (site, HTTPS) and wingman-api.fox3cloud.com (hook endpoint, plain HTTP). No separate domain will be bought.
- [ ] GitHub `Fox3Luck/DCS-Wingman` public, release zip for server owners
- [ ] DCS User Files upload (Utilities category)
- [ ] ED forum thread (Mods and Apps), Hoggit wiki page, r/hoggit post
- [x] Launch blog post published 2026-11-02: https://fox3servers.com/blog/dcs-wingman-announcement
- [ ] Fox3 customer notice (Discord announcements + email): what it is, how to
      opt out, privacy summary
- [ ] Red Shack episode scheduled
- [ ] Server owner "claim your server" form (Discord invite, SRS, schedule)
