# Deploying the BabsBooks Fleet on a Windows VPS

This covers what's *different* about running on a VPS versus your own PC —
not the bot logic itself, which you've already tested locally.

## 1. What you need on the VPS

- Windows Server or Windows VPS with RDP access (Contabo, Vultr, ForexVPS,
  etc. — any Windows VPS provider works; forex-specific VPS providers are
  usually closer to your broker's servers, which lowers execution latency).
- MetaTrader 5 installed and **logged into your account**, with:
  - Tools → Options → Expert Advisors → "Allow automated trading" checked
  - Tools → Options → Expert Advisors → "Allow DLL imports" checked
- Python 3.10+ installed on the VPS (same as your local setup).
- All your files copied over: `master_risk_manager.py`, `risk_client.py`,
  `bot_risk_config.py`, and every wired bot script you're running, plus
  `start_fleet.ps1` and `stop_fleet.ps1` from this folder.

```powershell
pip install fastapi uvicorn pydantic requests MetaTrader5
```

## 2. The core problem VPS deployment solves (and doesn't)

A VPS keeps things running when your own PC is off or your internet drops.
It does **not** automatically mean things survive a VPS *reboot* or a
process *crash* — you have to set that up explicitly. That's what sections
3 and 4 below are for.

## 3. Starting everything with one command

Copy `start_fleet.ps1` and `stop_fleet.ps1` into the same folder as your
bot scripts, then edit the `$Bots` array in `start_fleet.ps1` to match
which bots and how many instances you actually want running (it currently
lists all 6 wired strategies + 1 dedicated XAUUSD-only instance = 7 slots).

```powershell
cd C:\path\to\your\bots
.\start_fleet.ps1
```

This starts the MRM first, waits for it to respond, then starts each bot
with a unique `BOT_ID` and `MRM_URL=http://127.0.0.1:8800`, each logging to
its own file in `.\logs\`. To stop everything cleanly:

```powershell
.\stop_fleet.ps1
```

**Watch the logs the first time**, especially `logs\mrm.log` and each
`logs\bot_X_error.log` — that's where you'll see it if a bot fails to
connect to MT5 or hits a symbol-not-found error on this broker's naming.

## 4. Surviving reboots and crashes

`start_fleet.ps1` running once in an RDP session does **not** survive a
VPS reboot, and a crashed bot process won't restart itself. Two ways to
fix this, in order of robustness:

### Option A — Task Scheduler (simpler, good enough for most people)

1. Open Task Scheduler on the VPS.
2. Create Task (not "Basic Task" — the extra options matter here):
   - General tab: "Run whether user is logged on or not", check
     "Run with highest privileges".
   - Triggers tab: New → "At startup".
   - Actions tab: New → Program: `powershell.exe`, Arguments:
     `-ExecutionPolicy Bypass -File "C:\path\to\start_fleet.ps1"`
3. This restarts the whole fleet on VPS reboot. It does **not** restart an
   individual bot if just that one process crashes mid-session — for that,
   see Option B.

### Option B — NSSM (Non-Sucking Service Manager) — auto-restart on crash

NSSM wraps a process as a real Windows service, which Windows will
automatically restart if it dies. This matters more than it sounds like —
an unhandled exception in one bot (network blip, MT5 hiccup) would
otherwise just sit there dead until you notice.

1. Download NSSM (`nssm.exe`) — search "nssm windows service wrapper".
2. Install the MRM as a service:
   ```powershell
   nssm install BabsBooksMRM "C:\Python3x\python.exe" "-m uvicorn master_risk_manager:app --host 127.0.0.1 --port 8800"
   nssm set BabsBooksMRM AppDirectory "C:\path\to\your\bots"
   nssm set BabsBooksMRM AppExit Default Restart
   nssm start BabsBooksMRM
   ```
3. Repeat per bot, setting `AppEnvironmentExtra` for `BOT_ID` (and
   `SYMBOL_MODE` for the XAUUSD-only instance):
   ```powershell
   nssm install BabsBooksBot1 "C:\Python3x\python.exe" "simple_trade_bot_v46_gold.py"
   nssm set BabsBooksBot1 AppDirectory "C:\path\to\your\bots"
   nssm set BabsBooksBot1 AppEnvironmentExtra "BOT_ID=bot_1_v46gold" "MRM_URL=http://127.0.0.1:8800"
   nssm set BabsBooksBot1 AppExit Default Restart
   nssm start BabsBooksBot1
   ```
   This is more setup work than Task Scheduler, but each bot becomes a
   proper service: survives reboots, restarts on crash, and you can see
   status in `services.msc`.

## 5. Security — keep the MRM off the public internet

The launcher binds the MRM to `127.0.0.1` (localhost only), which is
correct and intentional — bots on the same VPS can reach it, but nothing
external can. **Do not** change that host to `0.0.0.0` or your VPS's
public IP unless you also add authentication in front of it; otherwise
anyone who finds the port could submit trade requests as one of your bots.

If you ever need to check the fleet's status remotely, use RDP to view
`logs\mrm.log` or hit `http://127.0.0.1:8800/v1/status` from inside an RDP
session — don't expose the port itself.

## 6. Recommended first real run on the VPS

Don't launch all 7 at once on a fresh VPS. Start with just the MRM and one
bot (edit `$Bots` down to one entry), confirm it places and closes trades
correctly against your real MT5 account for a session, then add the rest
back in one at a time. The MRM's `/v1/status` endpoint and the halt/resume
logic have both been tested against the real code — but a live account
with real MT5 latency is still a different environment than the sandbox
tests I ran.
