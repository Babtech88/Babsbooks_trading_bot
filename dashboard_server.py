"""
dashboard_server.py
--------------------------------------------------------------------------
A separate, read-only service for viewing the fleet's live status.

SECURITY MODEL -- read this before deploying
--------------------------------------------------------------------------
This is deliberately a SEPARATE process from master_risk_manager.py, on
its own port. It is the ONLY thing meant to ever be exposed to the
internet (via a tunnel -- see DASHBOARD_DEPLOYMENT.md). The MRM itself
stays bound to 127.0.0.1 exactly as before and is never reachable from
outside this machine.

This service can only:
  - GET the MRM's /v1/status over localhost
  - read closed-trade history directly from the MRM's own SQLite file
It has NO code path that can call /v1/halt, /v1/resume, /v1/request_trade,
or modify anything. Someone with the dashboard link cannot place, cancel,
or affect a single trade -- they can only look.

Access control: every request to /api/summary must include the correct
access key (?key=... or an Authorization: Bearer header). Set it via the
DASHBOARD_ACCESS_KEY environment variable before starting this. Share the
link with the key already in it (e.g. https://yourlink/?key=...) with your
trusted viewers -- treat that key like a password; anyone who has it can
view (but not act on) your live account data.
--------------------------------------------------------------------------
"""

import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, Query, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse

MRM_URL          = os.environ.get("MRM_URL", "http://127.0.0.1:8800")
MRM_DB_PATH      = os.environ.get("MRM_DB_PATH", "mrm_state.db")
ACCESS_KEY       = os.environ.get("DASHBOARD_ACCESS_KEY")
STATIC_DIR       = Path(__file__).parent

if not ACCESS_KEY:
    raise RuntimeError(
        "DASHBOARD_ACCESS_KEY environment variable is required -- this protects "
        "the dashboard from being viewable by anyone who finds the URL. "
        "Example: $env:DASHBOARD_ACCESS_KEY = 'choose-a-long-random-string'"
    )

app = FastAPI(title="BabsBooks Fleet Dashboard (read-only)")


def check_key(key: Optional[str], authorization: Optional[str]):
    provided = key
    if not provided and authorization and authorization.startswith("Bearer "):
        provided = authorization[len("Bearer "):]
    if provided != ACCESS_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing access key")


def fetch_mrm_status() -> dict:
    try:
        r = requests.get(f"{MRM_URL}/v1/status", timeout=5)
        r.raise_for_status()
        return {"connected": True, **r.json()}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def fetch_today_closed() -> list:
    """Read closed positions directly from the MRM's SQLite file.
    Read-only connection -- this process never writes to the MRM's DB."""
    if not os.path.exists(MRM_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(f"file:{MRM_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        today_str = date.today().isoformat()
        rows = conn.execute("""
            SELECT bot_id, symbol, direction, risk_pct, strategy_tag,
                   opened_at, closed_at, realized_pnl
              FROM positions
             WHERE status = 'closed' AND closed_at LIKE ?
             ORDER BY closed_at DESC
             LIMIT 200
        """, (f"{today_str}%",)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def summarize_by_bot(open_positions: list, closed_today: list) -> list:
    bots = {}

    def touch(bot_id):
        if bot_id not in bots:
            bots[bot_id] = {
                "bot_id": bot_id, "open_count": 0, "open_risk_pct": 0.0,
                "trades_today": 0, "wins_today": 0, "losses_today": 0,
                "realized_pnl_pct_today": 0.0,
            }
        return bots[bot_id]

    for p in open_positions:
        b = touch(p["bot_id"])
        b["open_count"] += 1
        b["open_risk_pct"] += p.get("risk_pct", 0) or 0

    for t in closed_today:
        b = touch(t["bot_id"])
        b["trades_today"] += 1
        pnl = t.get("realized_pnl") or 0
        b["realized_pnl_pct_today"] += pnl
        if pnl > 0:
            b["wins_today"] += 1
        elif pnl < 0:
            b["losses_today"] += 1

    return sorted(bots.values(), key=lambda x: x["bot_id"])


@app.get("/api/summary")
def api_summary(key: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    check_key(key, authorization)

    status = fetch_mrm_status()
    closed_today = fetch_today_closed()
    open_positions = status.get("open_positions", []) if status.get("connected") else []
    bots = summarize_by_bot(open_positions, closed_today)

    return JSONResponse({
        "server_time": datetime.now().isoformat(),
        "connected": status.get("connected", False),
        "error": status.get("error"),
        "day": status.get("day"),
        "equity": status.get("equity"),
        "start_equity": status.get("start_equity"),
        "peak_equity": status.get("peak_equity"),
        "daily_realized_pnl_pct": status.get("daily_realized_pnl_pct"),
        "consecutive_losses": status.get("consecutive_losses"),
        "halted": status.get("halted", False),
        "halt_reason": status.get("halt_reason", ""),
        "total_open_risk_pct": status.get("total_open_risk_pct", 0),
        "account_risk_cap_pct": status.get("account_risk_cap_pct", 5.0),
        "currency_exposure_pct": status.get("currency_exposure_pct", {}),
        "open_positions": open_positions,
        "closed_today": closed_today,
        "bots": bots,
    })


@app.get("/")
def dashboard_page(key: Optional[str] = Query(None)):
    # The key is only checked by /api/summary -- the HTML shell itself is
    # harmless without data, so it's fine to serve. The page's own JS reads
    # ?key= from the URL and attaches it to every /api/summary call.
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8801)
