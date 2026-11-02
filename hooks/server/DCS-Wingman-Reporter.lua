-- DCS-Wingman-Reporter.lua  (ASCII only)
-- DCS Wingman: find your wingman again.   https://github.com/Fox3Luck/DCS-Wingman
-- Dedicated-server GameGUI hook. Install: Saved Games\<DCS>\Scripts\Hooks\
--
-- WHAT IT DOES
--   * Every report_interval seconds (and a few seconds after any connect,
--     disconnect or slot change) it POSTs the connected-player roster to a
--     DCS Wingman presence service, so pilots' friends can see where they fly.
--   * Lets pilots use DCS Wingman from plain chat, with no client mod:
--       -wingman            help
--       -friend <name>      friend a pilot who is on THIS server right now
--       -friends            list your friends and where they are flying
--       -unfriend <name>    remove a friend
--       -wingman off | on   hide / show your own presence
--     Commands are consumed; they never appear in public chat.
--   * Delivers chat lines the service returns (welcome line, friends online).
--
-- WHAT IT NEVER DOES
--   No file writes (dcs.log lines only), no os.execute, no slot or mission
--   changes. Every network exchange is bounded by one http_timeout budget
--   (1..10 s), headers are capped at 64 and bodies at 64 KiB, no two requests
--   start within http_timeout of the previous one ending, commands are rate
--   limited per player, and after 3 transport failures the hook backs off to
--   one attempt per 5 minutes and answers commands locally.
--
-- CONFIG  Saved Games\<DCS>\Config\dcs-wingman.cfg  (a Lua file returning a table)
--   return {
--     api_url         = "http://203.0.113.10:8600",  -- numeric host preferred: DNS lookups are not time-bounded
--     server_key      = "wsk_...",                   -- your server's key, or a fleet key (wfk_...) shared by a hosting fleet
--     enabled         = true,
--     report_interval = 60,                          -- seconds, minimum 15
--     http_timeout    = 3,                           -- seconds, total budget per request, 1..10
--   }
--   Without a readable config carrying api_url and server_key the hook is inert.
--
-- ENVIRONMENT  DCS GUI Lua state (Lua 5.1) with DCS's bundled LuaSocket (plain
-- HTTP/1.0 over socket.tcp; DCS ships no TLS). DCS Lua is single threaded, so one
-- successful request still costs the server up to http_timeout of frame time.
-- Hook callbacks must not return values, except onPlayerTrySendChat, whose
-- return is the filtered message by DCS design ("" drops it, nil passes it on).

local TAG     = "WINGMAN"
local VERSION = "0.3.1"
local MAX_HEADERS = 64
local MAX_LINE    = 2048        -- status and header lines; LuaSocket cannot cap a line read, so this bounds what is kept
local MAX_BODY    = 65536
local MAX_TIMEOUT = 10
local CMD_COOLDOWN = 8          -- seconds between commands from one player
local DIRTY_SETTLE = 4          -- seconds after the last roster change before an early report
local BACKOFF_AFTER = 3         -- consecutive transport failures before backing off
local BACKOFF_SECONDS = 300
local HELP = {
  "DCS Wingman " .. VERSION .. " - find your wingman again.",
  "-friend <name>    friend a pilot on this server right now",
  "-friends          your friends and where they are flying",
  "-unfriend <name>  remove a friend",
  "-wingman off|on   hide or show your presence",
  "Free and open source: github.com/Fox3Luck/DCS-Wingman",
}

local function logline(level, msg)
  if log and log.write then pcall(log.write, TAG, level, tostring(msg)) end
end
local function info(msg) logline(log and log.INFO or 2, msg) end
local function warn(msg) logline(log and log.WARNING or 1, msg) end

-------------------------------------------------------------------------------
-- CONFIG
-------------------------------------------------------------------------------
local function load_config()
  if not (lfs and lfs.writedir) then return nil, "lfs unavailable" end
  local path = lfs.writedir() .. "Config\\dcs-wingman.cfg"
  local chunk = loadfile(path)
  if not chunk then return nil, "no config at " .. path end
  local ok, c = pcall(chunk)
  if not ok or type(c) ~= "table" then return nil, "config did not return a table" end
  if c.enabled ~= nil and c.enabled ~= true then return nil, "disabled in config" end
  if type(c.api_url) ~= "string" or #c.api_url == 0
     or type(c.server_key) ~= "string" or #c.server_key == 0 then
    return nil, "config needs non-empty api_url and server_key"
  end
  c.api_url = c.api_url:gsub("/+$", "")
  if not c.api_url:match("^http://([^/:]+):?(%d*)(/?.*)$") then
    return nil, "api_url must be http://host[:port][/prefix] (no https: DCS Lua has no TLS)"
  end
  c.report_interval = math.max(15, tonumber(c.report_interval) or 60)
  local wanted = tonumber(c.http_timeout) or 3
  c.http_timeout = math.min(MAX_TIMEOUT, math.max(1, wanted))
  if c.http_timeout ~= wanted then
    info("http_timeout " .. tostring(wanted) .. " clamped to " .. c.http_timeout .. "s")
  end
  return c
end

local cfg, cfg_err = load_config()
if not cfg then
  info("inert: " .. tostring(cfg_err))
  return
end

-------------------------------------------------------------------------------
-- LUASOCKET (only touch package.path if the plain require fails)
-------------------------------------------------------------------------------
local ok_sock, socket = pcall(require, "socket")
if not ok_sock then
  package.path = package.path .. ";.\\LuaSocket\\?.lua"
  ok_sock, socket = pcall(require, "socket")
end
if not ok_sock then
  warn("inert: LuaSocket unavailable: " .. tostring(socket))
  return
end

local function now() return socket.gettime() end

-------------------------------------------------------------------------------
-- MINIMAL HTTP CLIENT: one POST, HTTP/1.0, total time budget, capped sizes
-- Returns text, or nil, err, kind where kind is "transport" (timeout, connect,
-- short read, 5xx) or "http" (any other non-200). Only transport counts toward
-- backoff. Also records when the request ended, for request spacing.
-------------------------------------------------------------------------------
local state = {
  next_report = 0, dirty_at = nil, failures = 0, backoff_logged = false,
  last_http_end = 0,
}

local function parse_url(u)
  local host, port, path = u:match("^http://([^/:]+):?(%d*)(/?.*)$")
  if not host then return nil end
  return host, tonumber(port) or 80, path or ""
end

local function do_http(c, host, port, base, path, body, deadline)
  local function arm()
    local left = deadline - now()
    if left <= 0 then error("time budget exhausted", 0) end
    c:settimeout(left)
  end
  arm()
  local ok, err = c:connect(host, port)
  if not ok then error("connect: " .. tostring(err), 0) end
  local req = table.concat({
    "POST " .. base .. path .. " HTTP/1.0",
    "Host: " .. host .. ":" .. port,
    "User-Agent: DCS-Wingman-Reporter/" .. VERSION,
    "Authorization: Bearer " .. cfg.server_key,
    "Content-Type: application/json",
    "Content-Length: " .. #body,
    "Connection: close",
    "",
    body,
  }, "\r\n")
  local pos = 1
  while pos <= #req do
    arm()
    local sent, serr, last = c:send(req, pos)
    if sent then
      pos = sent + 1
    elseif serr == "timeout" then
      pos = (last or (pos - 1)) + 1
    else
      error("send: " .. tostring(serr), 0)
    end
  end
  arm()
  local status = c:receive("*l")
  if not status then error("no response", 0) end
  if #status > MAX_LINE then error("status line too long", 0) end
  local code = tonumber(status:match("^HTTP/%d%.%d (%d%d%d)"))
  local length, nheaders = nil, 0
  while true do
    arm()
    local line = c:receive("*l")
    if not line or line == "" then break end
    if #line > MAX_LINE then error("header line too long", 0) end
    nheaders = nheaders + 1
    if nheaders > MAX_HEADERS then error("too many headers", 0) end
    local k, v = line:match("^([^:]+):%s*(.*)$")
    if k and k:lower() == "content-length" then length = tonumber(v) end
  end
  if length and length > MAX_BODY then error("body too large", 0) end
  local text
  if length then
    if length > 0 then
      arm()
      text = c:receive(length)
      if not text then error("short body", 0) end
    else
      text = ""
    end
  else
    local parts, total = {}, 0
    while true do
      arm()
      local chunk, rerr, partial = c:receive(8192)
      local piece = chunk or partial
      if piece and #piece > 0 then
        total = total + #piece
        if total > MAX_BODY then error("body too large", 0) end
        parts[#parts + 1] = piece
      end
      if not chunk then
        if rerr == "closed" then break end
        error("recv: " .. tostring(rerr), 0)
      end
    end
    text = table.concat(parts)
  end
  return code, text
end

local function http_post(path, body)
  local host, port, base = parse_url(cfg.api_url)
  if not host then return nil, "api_url must be http://host[:port][/prefix]", "config" end
  local c = socket.tcp()
  if not c then return nil, "no socket", "transport" end
  local deadline = now() + cfg.http_timeout
  local ok, code, text = pcall(do_http, c, host, port, base, path, body, deadline)
  pcall(c.close, c)
  state.last_http_end = now()
  if not ok then return nil, tostring(code), "transport" end
  if code ~= 200 then
    local kind = (code == nil or code >= 500) and "transport" or "http"
    return nil, "http " .. tostring(code) .. ": " .. tostring(text):sub(1, 160), kind
  end
  return text
end

local function post_json(path, tbl)
  local ok, body = pcall(net.lua2json, tbl)
  if not ok then return nil, "json encode: " .. tostring(body), "config" end
  local text, err, kind = http_post(path, body)
  if not text then return nil, err, kind end
  local ok2, data = pcall(net.json2lua, text)
  if not ok2 or type(data) ~= "table" then return nil, "bad json reply", "transport" end
  return data
end

local function note_result(data, kind)
  if data then
    if state.failures > 0 then info("service reachable again") end
    state.failures = 0
    state.backoff_logged = false
  elseif kind == "transport" then
    state.failures = state.failures + 1
  end
end

local function in_backoff() return state.failures >= BACKOFF_AFTER end

local function http_clear(gap)
  -- true when at least `gap` seconds have passed since the last request ended
  return now() >= state.last_http_end + gap
end

-------------------------------------------------------------------------------
-- GAME STATE
-------------------------------------------------------------------------------
local function roster()
  -- id 1 is the dedicated server itself; skip it.
  local list, by_ucid = {}, {}
  local ids = net.get_player_list() or {}
  for _, id in ipairs(ids) do
    if id ~= 1 then
      local pi = net.get_player_info(id)
      if pi and type(pi.ucid) == "string" and #pi.ucid > 0 then
        list[#list + 1] = {
          ucid = pi.ucid,
          name = tostring(pi.name or ""),
          side = tonumber(pi.side) or 0,
          slot = tostring(pi.slot or ""),
        }
        by_ucid[pi.ucid] = id
      end
    end
  end
  return list, by_ucid
end

local function server_info()
  local s = {}
  local ok, st = pcall(net.get_server_settings)
  if ok and type(st) == "table" then
    s.name = st.name
    s.port = tonumber(st.port)
    s.max_players = tonumber(st.maxPlayers)
    s.description = st.description
  end
  local okm, mission = pcall(DCS.getMissionName)
  if okm and type(mission) == "string" then s.mission = mission end
  return s
end

local function say(id, line)
  if type(line) ~= "string" then return end
  line = line:gsub("\r", "")
  if #line > 0 then pcall(net.send_chat_to, line, id) end
end

local function deliver(messages, by_ucid)
  if type(messages) ~= "table" then return end
  for _, m in ipairs(messages) do
    local id = type(m) == "table" and by_ucid[m.ucid]
    if id and type(m.text) == "string" then
      for line in (m.text .. "\n"):gmatch("(.-)\n") do say(id, line) end
    end
  end
end

-------------------------------------------------------------------------------
-- REPORTING
-------------------------------------------------------------------------------
local function send_report()
  local players, by_ucid = roster()
  local data, err, kind = post_json("/v1/report", {
    v = 1, hook = VERSION, server = server_info(), players = players,
  })
  note_result(data, kind)
  if not data then return nil, err end
  deliver(data.messages, by_ucid)
  return true
end

local function mark_dirty()
  state.dirty_at = now() + DIRTY_SETTLE
end

-------------------------------------------------------------------------------
-- CHAT COMMANDS
-------------------------------------------------------------------------------
local COMMANDS = { wingman = true, friend = true, friends = true, unfriend = true }
local cmd_state = { last_by_id = {} }

local function parse_command(msg)
  local cmd, arg = msg:match("^%s*%-(%a+)%s*(.-)%s*$")
  if not cmd then return nil end
  cmd = cmd:lower()
  if not COMMANDS[cmd] then return nil end
  return cmd, arg or ""
end

local function handle_command(id, cmd, arg)
  local t = now()
  local last = cmd_state.last_by_id[id] or 0
  if (t - last) < CMD_COOLDOWN then
    say(id, "DCS Wingman: one command every few seconds, please.")
    return
  end
  if cmd == "wingman" and (arg == "" or arg:lower() == "help") then
    cmd_state.last_by_id[id] = t
    for _, l in ipairs(HELP) do say(id, l) end
    return
  end
  -- busy and backoff replies do not start the cooldown: the player did nothing yet
  if in_backoff() then
    say(id, "DCS Wingman: the service is unreachable right now, try again in a few minutes.")
    return
  end
  if not http_clear(cfg.http_timeout) then
    say(id, "DCS Wingman: busy, try again in a few seconds.")
    return
  end
  cmd_state.last_by_id[id] = t

  local pi = net.get_player_info(id)
  if not (pi and type(pi.ucid) == "string") then return end
  local players, by_ucid = roster()
  local candidates = {}
  for _, p in ipairs(players) do
    if p.ucid ~= pi.ucid then candidates[#candidates + 1] = { ucid = p.ucid, name = p.name } end
  end
  local data, err, kind = post_json("/v1/command", {
    v = 1, server = server_info(),
    actor = { ucid = pi.ucid, name = tostring(pi.name or "") },
    command = cmd, arg = arg:sub(1, 64), candidates = candidates,
  })
  note_result(data, kind)
  if not data then
    warn("command failed: " .. tostring(err))
    say(id, "DCS Wingman: service unavailable, try again in a minute.")
    return
  end
  deliver(data.messages, by_ucid)
end

-------------------------------------------------------------------------------
-- CALLBACKS
-------------------------------------------------------------------------------
local cb = {}

function cb.onSimulationFrame()
  local okm, mp = pcall(DCS.isMultiplayer)
  if not (okm and mp) then return end
  local t = now()
  local due = t >= state.next_report
  if in_backoff() then
    if not due then return end                      -- backoff wins; dirty waits
  elseif not (due or (state.dirty_at and t >= state.dirty_at)) then
    return
  end
  -- never start a request within http_timeout of the previous one ending,
  -- and let a burst of roster changes settle before an early report
  if not http_clear(math.max(DIRTY_SETTLE, cfg.http_timeout)) then return end
  state.dirty_at = nil
  state.next_report = t + cfg.report_interval
  local ok, res, err = pcall(send_report)
  if not ok then
    state.failures = state.failures + 1
    warn("report crashed: " .. tostring(res))
  elseif not res then
    warn("report failed: " .. tostring(err))
  end
  if in_backoff() then
    state.next_report = t + BACKOFF_SECONDS
    if not state.backoff_logged then
      warn(BACKOFF_AFTER .. " consecutive failures, one attempt per " .. BACKOFF_SECONDS .. "s until the service answers")
      state.backoff_logged = true
    end
  end
end

function cb.onSimulationStart()
  state.next_report = 0
  state.dirty_at = nil
end

function cb.onPlayerConnect(id)    if id ~= 1 then mark_dirty() end end
function cb.onPlayerChangeSlot(id) if id ~= 1 then mark_dirty() end end
function cb.onPlayerDisconnect(id)
  if id ~= 1 then mark_dirty() end
  cmd_state.last_by_id[id] = nil
end

function cb.onPlayerTrySendChat(playerID, msg, to)
  if type(msg) ~= "string" then return end
  local cmd, arg = parse_command(msg)
  if not cmd then return end           -- not ours: pass through untouched
  local ok, err = pcall(handle_command, playerID, cmd, arg)
  if not ok then
    warn("command crashed: " .. tostring(err))
    say(playerID, "DCS Wingman: something went wrong, try again later.")
  end
  return ""                             -- consume the command
end

DCS.setUserCallbacks(cb)
info("DCS-Wingman-Reporter " .. VERSION .. " loaded, api=" .. cfg.api_url ..
     ", interval=" .. cfg.report_interval .. "s, timeout=" .. cfg.http_timeout .. "s")
