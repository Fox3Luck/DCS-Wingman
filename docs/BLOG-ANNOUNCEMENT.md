---
title: "Find your wingman again: introducing DCS Wingman"
slug: dcs-wingman-announcement
status: draft
author: Luck, Fox3 Managed Solutions
date: 2026-11-02
excerpt: "DCS has no friend list. Fox3 built one, open source and free, and it is rolling out across our hosted servers now."
cover: "Two jets in close formation at dusk over water, navy sky with a red horizon line, Fox3 palette."
tags: [dcs-wingman, community, announcement, multiplayer]
---

# Find your wingman again: introducing DCS Wingman

Every night we watch the same story play out across about 300 DCS servers. Two pilots
meet on a server, fly together for two hours, save each other twice, and log off. The
next evening they are both online again, on different servers, looking for each other
in a list of names they half remember. DCS World has no friend list, no way to see
which server a friend is on, and no in-game path to a server's Discord. Pilots have
asked Eagle Dynamics for this since 2013.

In October, Plasma and Rez spent a Red Shack episode on exactly this problem. They
called it the multiplayer desert: plenty of servers, plenty of pilots, and no way to
find the people you want to fly with. They asked for someone to build the missing
piece. We host more DCS servers than anyone, so we decided it should be us.

Today we are announcing **DCS Wingman**. It is free, it is open source under the MIT
license, and it is rolling out across the Fox3 hosted fleet now.

## What it does

On any server that runs DCS Wingman, you get one line in chat when you connect. From
there, everything happens in the chat box you already use:

- `-friend Maverick` sends a friend request to the pilot named Maverick.
- `-friends` lists your wingmen and tells you which server each one is on right now.
- `-unfriend Maverick` removes a wingman.
- `-wingman off` hides you from everyone and deletes your location. `-wingman on`
  brings you back.
- `-wingman` prints the help.

There is one rule, and it is the rule that makes the whole thing work: you can only
friend a pilot who is on the same server as you at that moment, and they have to
friend you back. That is how wingmen are made in real life, and it keeps the system
free of spam. A stranger on another server cannot add you. Only someone you actually
flew with can.

Pilots install nothing. The server does the work.

## What it does for server owners

Server owners add one Lua file to Scripts\Hooks and a small config file. That is the
whole install. The server then appears in the public directory at
[wingman.fox3cloud.com](https://wingman.fox3cloud.com) with its mission, player count,
Discord invite and SRS address, so a pilot looking for a place to fly tonight can find
you, your community and your radio frequency in one place.

Every server hosted by Fox3 will run DCS Wingman by default. Customers who prefer to
stay out can turn it off with one line in the config, and support will do it on
request.

## Privacy, stated plainly

Your DCS identity is stored only as a keyed hash. The raw ID never touches the
database. The public directory shows player counts, never names. `-wingman off` hides
you and deletes your location immediately. Friendships are mutual and private. The
whole service is a few hundred lines of Python you can read on GitHub, and every change
to it goes through an independent code review before it ships.

## Why we built it this way

We could have built a Fox3 feature and kept it for our customers. We built a community
project instead. DCS Wingman is independent, sponsored by Fox3 Managed Solutions, and
open to every server in DCS whether we host it or not. The social layer only works if
it covers the whole game. A friend list that stops at one hosting company is a contact
list.

DCS Wingman is not an Eagle Dynamics product and is not affiliated with Eagle
Dynamics. DCS World is a trademark of Eagle Dynamics SA. If ED ever ships a native
friend list, we will point everyone at it and retire ours with a smile.

## What comes next

The chat commands are the first tier. Next comes a client mod with an in-game friends
window and a Join button, so you see where your wingmen are flying and get there in one
click. After that: Discord notifications when a friend comes online, and squadron pages
in the directory.

## How to help

- **Pilots:** ask your favorite server to add DCS Wingman. The install guide is on
  GitHub and takes a few minutes.
- **Server owners:** grab the hook and the config example at
  [github.com/Fox3Luck/DCS-Wingman](https://github.com/Fox3Luck/DCS-Wingman), register
  your server, and tell your pilots the commands.
- **Everyone:** join the [Fox3 Discord](https://discord.gg/777luck777) to follow the
  launch, report what is missing, and tell us who you found again.

Thank you to Plasma and Rez and the Red Shack for naming the problem out loud. This one
is for every pilot who ever typed "anyone seen Maverick?" into a server chat.

Find your wingman again.
