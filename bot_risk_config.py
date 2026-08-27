"""
bot_risk_config.py
--------------------------------------------------------------------------
Shared by every bot instance in the 7-bot fleet. Two jobs:

1. SYMBOL POLICY
   Per your instruction: every bot trades its own normal symbol list freely,
   EXCEPT one dedicated instance which is restricted to XAUUSD only.
   Controlled entirely by environment variables at launch -- no code change
   needed per instance, just how you start the process:

       # the 6 "free" bots -- just run normally, no env vars needed:
       BOT_ID=bot_1_v46 python simple_trade_bot.py
       BOT_ID=bot_2_v46 python simple_trade_bot.py
       BOT_ID=bot_3_v45 python professional_trading_bot.py
       ... etc (unique BOT_ID per instance is the only requirement)

       # the 1 dedicated XAUUSD bot:
       BOT_ID=bot_7_xauusd SYMBOL_MODE=xauusd_only python simple_trade_bot.py

2. CLOSE DETECTION
   None of your bot files have an explicit "trade closed" callback -- trades
   exit via broker-side SL/TP fills, and the bots just notice the position
   is gone from mt5.positions_get() on the next loop. So instead of hunting
   for a close handler in each 1000+ line file, this module gives every bot
   ONE call (`poll_closed_positions()`) to add to its main loop, which:
     - diffs currently-open MT5 positions against what it saw last poll
     - for any ticket that disappeared, pulls the closing deal from MT5
       history to get the realized profit
     - converts that to %-of-equity and reports it to the MRM via
       risk.close_position(...), releasing that reserved exposure

   This means the ONLY per-file edits needed are:
     a) import this module
     b) wrap the SYMBOLS list with get_symbols(...)
     c) gate the actual order_send(...) entry call with risk.request_trade(...)
     d) call poll_closed_positions() once per main loop iteration
--------------------------------------------------------------------------
"""

import os
import logging
from typing import Dict, Set

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from risk_client import RiskClient

log = logging.getLogger("bot_risk_config")

BOT_ID = os.environ.get("BOT_ID")
if not BOT_ID:
    raise RuntimeError(
        "BOT_ID environment variable is required -- each of the 7 bot "
        "instances needs a unique id so the MRM can tell them apart. "
        "Example: BOT_ID=bot_1_v46 python simple_trade_bot.py"
    )

SYMBOL_MODE = os.environ.get("SYMBOL_MODE", "default")  # "default" | "xauusd_only"
MRM_URL = os.environ.get("MRM_URL", "http://127.0.0.1:8800")

risk = RiskClient(bot_id=BOT_ID, base_url=MRM_URL)


def get_symbols(default_symbols: list) -> list:
    """Apply the fleet's symbol policy on top of a bot file's own default list."""
    if SYMBOL_MODE == "xauusd_only":
        log.info(f"[{BOT_ID}] SYMBOL_MODE=xauusd_only -> restricting to ['XAUUSD']")
        return ["XAUUSD"]
    return default_symbols


# ── Close detection (generic, MT5-position-diff based) ──────────────────────

# ticket -> MRM position_id, populated by track_reservation() when a bot opens a trade
_tracked_positions: Dict[int, str] = {}
_last_seen_tickets: Set[int] = set()


def track_reservation(ticket: int, position_id: str):
    """Call right after a successful order_send(), with the broker ticket
    (res.order) and the MRM's decision.position_id, so the close poller
    knows which MRM reservation to release when this ticket disappears."""
    _tracked_positions[ticket] = position_id
    _last_seen_tickets.add(ticket)
    log.info(f"[{BOT_ID}] tracking ticket {ticket} -> MRM position {position_id}")


def poll_closed_positions():
    """
    Call once per main-loop iteration. Detects tickets that were open last
    poll but are gone now, looks up the realized P&L from MT5 history, and
    reports the close to the MRM. Safe to call even if nothing closed.
    """
    if mt5 is None or not _tracked_positions:
        return

    current_tickets = {p.ticket for p in (mt5.positions_get() or [])}
    closed_tickets = _last_seen_tickets - current_tickets

    for ticket in closed_tickets:
        position_id = _tracked_positions.pop(ticket, None)
        if not position_id:
            continue
        pnl_pct = _lookup_realized_pnl_pct(ticket)
        try:
            risk.close_position(position_id, realized_pnl_pct=pnl_pct)
            log.info(f"[{BOT_ID}] reported close: ticket={ticket} pnl_pct={pnl_pct:.3f}%")
        except Exception as e:
            log.error(f"[{BOT_ID}] FAILED to report close for ticket {ticket}: {e}. "
                      f"MRM exposure for this position will remain reserved until "
                      f"manually reconciled -- check /v1/status.")

    _last_seen_tickets.clear()
    _last_seen_tickets.update(current_tickets & set(_tracked_positions.keys()))


def _lookup_realized_pnl_pct(ticket: int) -> float:
    """Pull the closing deal's profit from MT5 history and express it as a
    % of current account equity. Falls back to 0.0 (logged) if history
    lookup fails -- better to release the reservation with an approximate
    number than to leave it stuck forever."""
    try:
        from datetime import datetime, timedelta
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=2), datetime.now(), position=ticket)
        if not deals:
            log.warning(f"[{BOT_ID}] no history deals found for ticket {ticket}, reporting 0.0%")
            return 0.0
        total_profit = sum(d.profit + getattr(d, "swap", 0) + getattr(d, "commission", 0) for d in deals)
        info = mt5.account_info()
        equity = float(info.equity) if info else None
        if not equity:
            return 0.0
        return (total_profit / equity) * 100
    except Exception as e:
        log.error(f"[{BOT_ID}] error looking up P&L for ticket {ticket}: {e}")
        return 0.0
