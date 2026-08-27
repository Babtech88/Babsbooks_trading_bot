# Deploying the Fleet Dashboard

## What this is, and why it's a separate service

`dashboard_server.py` never talks to your MT5 account, never places orders,
and cannot call any control endpoint on the MRM (`/v1/halt`, `/v1/resume`,
`/v1/request_trade`, etc.). It only does two things: GET the MRM's
`/v1/status` over localhost, and read closed-trade history directly from
the MRM's own SQLite file (read-only connection). This separation means
even if the dashboard's access key ever leaked, the worst case is someone
can *view* your fleet's numbers -- they cannot affect a single trade.

## 1. Setup on your VPS (same machine as the MRM)

Copy `dashboard_server.py` and `index.html` into a `dashboard` folder next
to your MRM and bot files:

```
trading_bot\
  master_risk_manager.py
  risk_client.py
  bot_risk_config.py
  ...bot scripts...
  dashboard\
    dashboard_server.py
    index.html
```

Install the one extra dependency (everything else it needs is already in
your `requirements.txt`):

```powershell
pip install fastapi uvicorn requests
```

## 2. Choose an access key and start it

Pick a long random string -- this is effectively a password. Don't reuse
one of your real passwords.

```powershell
cd trading_bot\dashboard
$env:DASHBOARD_ACCESS_KEY = "choose-a-long-random-string-here"
$env:MRM_URL = "http://127.0.0.1:8800"
$env:MRM_DB_PATH = "..\mrm_state.db"
python dashboard_server.py
```

It starts on port 8801. Confirm locally first:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8801/api/summary?key=choose-a-long-random-string-here"
```
You should see real JSON with your equity, positions, and today's bot
activity.

## 3. Sharing it with a few trusted people — Cloudflare Tunnel

Do **not** open port 8801 directly on your VPS's firewall/router — that's
a raw, unencrypted exposure. Instead use a tunnel, which gives you a real
HTTPS link without opening any inbound ports on the VPS at all.

**Cloudflare Tunnel** (free, no account required for a quick tunnel):

1. Download `cloudflared.exe` from Cloudflare's site (search
   "cloudflared download windows").
2. With the dashboard running on port 8801, in a new terminal:
   ```powershell
   .\cloudflared.exe tunnel --url http://localhost:8801
   ```
3. It prints a random `https://something.trycloudflare.com` URL. That's
   your public, HTTPS-encrypted link.
4. The link people actually need includes the key:
   ```
   https://something.trycloudflare.com/?key=choose-a-long-random-string-here
   ```
   Share that full URL (with the key already in it) via a private channel
   -- WhatsApp DM, not a public group.

**Only tunnel port 8801** (the dashboard), never port 8800 (the MRM). This
is the whole point of the two-service split.

## 4. Keeping it running

Same pattern as your bots -- either add `dashboard_server.py` as another
entry started via a modified `start_fleet.ps1`, or wrap it with NSSM as
its own Windows service so it survives reboots and restarts on crash. See
`VPS_DEPLOYMENT.md` for the exact NSSM commands; the pattern is identical,
just pointed at `dashboard_server.py` instead of a bot script.

If you use `cloudflared` as a "quick tunnel" (the command above), the URL
changes every time you restart it. For a stable, permanent link you'd set
up a named Cloudflare Tunnel tied to a domain you own -- more setup, but
worth it if this becomes a permanent thing investors check regularly
rather than an occasional share.

## 5. Rotating or revoking access

To cut someone off, just change `DASHBOARD_ACCESS_KEY` and restart the
service -- every old link stops working immediately, and you issue a new
link to whoever should still have access. There's no per-person
revocation since everyone shares one key; if you need individual access
logs or per-person keys later, that's a real upgrade worth doing before
sharing with more than a handful of people.
