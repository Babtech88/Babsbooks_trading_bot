"""
================================================================================
 BABSBOOKS MASTER RISK MANAGER (MRM) v1.0
================================================================================
Central risk authority sitting between N independent trading bots and one
shared MT5 account.

WHY THIS EXISTS
----------------------------------------------------------------------------
Each bot (V36 / V45 / V46 / simple_trade_bot, etc.) makes its own entry
decisions and manages its own per-bot risk. That's fine running solo. Running
several at once against ONE account, none of them can see what the others
are doing -> correlated exposure stacks invisibly and a single bad day can
blow through account-level drawdown limits before any individual bot's
own cap even trips.

The MRM fixes this by being the one thing that sees everything and holds
final veto power. No bot talks to MT5 directly for order placement sizing
decisions anymore -- it asks the MRM first.

FLOW
----------------------------------------------------------------------------
  bot decides "I want to enter"
      -> POST /v1/request_trade         (MRM reserves exposure, approves/rejects/resizes)
      -> bot places the order at broker
      -> POST /v1/confirm_fill          (reservation becomes real) OR
      -> POST /v1/reject_fill           (broker rejected -> release reservation)
      ... position lives on the books ...
      -> POST /v1/close_position        (releases exposure, updates realized P&L)

  GET  /v1/status                       (full live snapshot -- useful for a dashboard)
  POST /v1/reset_day                    (manual daily reset; also auto-resets on date change)
  POST /v1/halt                         (manual kill switch)
  POST /v1/resume                       (manual resume after a halt, requires confirm=true)

TRANSPORT CHOICE
----------------------------------------------------------------------------
FastAPI + localhost HTTP was chosen over the SQLite-polling-mailbox pattern
because it gives synchronous approve/reject in a single round trip (no
polling lag), and doubles as the natural backend for the live dashboard
you're already planning for the crypto bot project. Runs as one extra
local service (`uvicorn master_risk_manager:app`).

STATE OF RECORD
----------------------------------------------------------------------------
Equity/balance is pulled directly from MT5 (source of truth), NOT trusted
from bot self-reports, so a buggy bot can't lie about its own P&L to dodge
the halt. If MetaTrader5 isn't importable (e.g. testing off-Windows), the
MRM falls back to a manually-set equity value via /v1/set_equity so you can
still exercise the logic.

All state also persists to SQLite (mrm_state.db) so a service restart
doesn't lose today's drawdown counters or open reservations.
================================================================================
"""

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════
# CONFIG -- tune these to match your account and how you want the fleet run
# ══════════════════════════════════════════════════════════════════════════

class Config:
    DB_PATH = "mrm_state.db"

    # --- Account-wide risk ceilings (these are the numbers that matter now,
    #     NOT each bot's individual per-trade cap) ---
    MAX_ACCOUNT_RISK_PCT = 5.0          # sum of all open positions' risk, account-wide
    MAX_DAILY_DRAWDOWN_PCT = 2.0        # realized + open floating loss vs today's start equity
    MAX_PEAK_DRAWDOWN_PCT = 8.0         # vs all-time peak equity -- the "stop everything" line
    MAX_SINGLE_TRADE_RISK_PCT = 1.0     # hard ceiling per trade regardless of what the bot asked for

    # --- Currency exposure (mirrors V36's "1.5x per currency" concept,
    #     but computed account-wide across every bot instead of per-bot) ---
    MAX_CURRENCY_EXPOSURE_MULT = 1.5    # max exposure to one currency = this x MAX_SINGLE_TRADE_RISK_PCT

    # --- Correlation control ---
    CORRELATION_THRESHOLD = 0.70        # |corr| >= this => treated as same risk bucket
    CORRELATED_GROUP_RISK_CAP_PCT = 1.5 # max combined risk allowed within one correlated bucket

    # --- Timing / clustering ---
    MIN_TRADE_SPACING_SECONDS = 180     # min gap between new entries in the *same* correlated group

    # --- Consecutive loss circuit breaker (fleet-wide, not per-bot) ---
    MAX_FLEET_CONSECUTIVE_LOSSES = 4
    CONSECUTIVE_LOSS_COOLDOWN_MIN = 60

    # Approximate static correlation matrix for the pairs you actually trade.
    # Values are illustrative long-run approximations, not live-computed --
    # good enough for a risk *gate*, not for signal generation.
    CORRELATION_MATRIX: Dict[Tuple[str, str], float] = {
        ("EURUSD", "GBPUSD"): 0.85,
        ("EURUSD", "AUDUSD"): 0.65,
        ("EURUSD", "EURJPY"): 0.55,
        ("EURUSD", "EURGBP"): -0.35,
        ("EURUSD", "GBPJPY"): 0.50,
        ("EURUSD", "XAUUSD"): 0.30,
        ("GBPUSD", "AUDUSD"): 0.60,
        ("GBPUSD", "EURGBP"): -0.55,
        ("GBPUSD", "GBPJPY"): 0.70,
        ("GBPUSD", "EURJPY"): 0.45,
        ("AUDUSD", "XAUUSD"): 0.45,
        ("EURJPY", "GBPJPY"): 0.80,
        ("EURJPY", "EURGBP"): 0.25,
        ("GBPJPY", "EURGBP"): -0.30,
        ("XAUUSD", "GBPUSD"): 0.25,
        ("XAUUSD", "EURJPY"): 0.20,
    }


def get_correlation(sym_a: str, sym_b: str) -> float:
    if sym_a == sym_b:
        return 1.0
    key = (sym_a, sym_b)
    if key in Config.CORRELATION_MATRIX:
        return Config.CORRELATION_MATRIX[key]
    rev = (sym_b, sym_a)
    if rev in Config.CORRELATION_MATRIX:
        return Config.CORRELATION_MATRIX[rev]
    return 0.0  # unknown pair -> assume uncorrelated (conservative default is debatable;
                # flip to a nonzero default if you'd rather be cautious about unlisted pairs)


def currencies_in(symbol: str) -> Tuple[str, str]:
    """EURUSD -> ('EUR', 'USD'). XAUUSD -> ('XAU', 'USD')."""
    symbol = symbol.upper()
    return symbol[:3], symbol[3:6]


# ══════════════════════════════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════

class Store:
    """Thin SQLite persistence so a service restart doesn't lose today's state."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    bot_id TEXT, symbol TEXT, direction TEXT,
                    risk_pct REAL, risk_amount REAL,
                    status TEXT,               -- 'reserved' | 'open' | 'closed' | 'released'
                    strategy_tag TEXT,
                    opened_at TEXT, closed_at TEXT,
                    realized_pnl REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS daily_state (
                    day TEXT PRIMARY KEY,
                    start_equity REAL, realized_pnl REAL,
                    consecutive_losses INTEGER, halted INTEGER, halt_reason TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT, event TEXT, detail TEXT
                )
            """)

    def log(self, event: str, detail: dict):
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO audit_log (ts, event, detail) VALUES (?,?,?)",
                      (datetime.utcnow().isoformat(), event, json.dumps(detail, default=str)))

    def upsert_position(self, p: "Position"):
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT INTO positions (position_id, bot_id, symbol, direction, risk_pct,
                    risk_amount, status, strategy_tag, opened_at, closed_at, realized_pnl)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(position_id) DO UPDATE SET
                    status=excluded.status, opened_at=excluded.opened_at,
                    closed_at=excluded.closed_at, realized_pnl=excluded.realized_pnl
            """, (p.position_id, p.bot_id, p.symbol, p.direction, p.risk_pct,
                  p.risk_amount, p.status, p.strategy_tag,
                  p.opened_at.isoformat() if p.opened_at else None,
                  p.closed_at.isoformat() if p.closed_at else None,
                  p.realized_pnl))

    def save_daily_state(self, d: "DailyState"):
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT INTO daily_state (day, start_equity, realized_pnl, consecutive_losses, halted, halt_reason)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                    start_equity=excluded.start_equity, realized_pnl=excluded.realized_pnl,
                    consecutive_losses=excluded.consecutive_losses, halted=excluded.halted,
                    halt_reason=excluded.halt_reason
            """, (d.day.isoformat(), d.start_equity, d.realized_pnl,
                  d.consecutive_losses, int(d.halted), d.halt_reason))


# ══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STATE
# ══════════════════════════════════════════════════════════════════════════

class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Position:
    position_id: str
    bot_id: str
    symbol: str
    direction: str
    risk_pct: float
    risk_amount: float
    strategy_tag: str = ""
    status: str = "reserved"          # reserved | open | closed | released
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    realized_pnl: float = 0.0


@dataclass
class DailyState:
    day: date
    start_equity: float
    peak_equity: float
    realized_pnl: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    halt_reason: str = ""
    cooldown_until: Optional[datetime] = None


class RiskState:
    """
    All the live bookkeeping the MRM needs to make an approve/reject decision.
    Guarded by a single lock -- correctness over throughput; trade-decision
    volume here is orders per minute, not per millisecond, so a coarse lock
    is the right tradeoff.
    """

    def __init__(self, store: Store):
        self.store = store
        self.lock = threading.RLock()
        self.positions: Dict[str, Position] = {}
        self.last_entry_time_by_group: Dict[str, datetime] = {}
        self.manual_equity_override: Optional[float] = None
        self.daily: Optional[DailyState] = None
        self._ensure_day()

    # -- equity source of truth -------------------------------------------------
    def get_account_equity(self) -> float:
        if self.manual_equity_override is not None:
            return self.manual_equity_override
        if MT5_AVAILABLE:
            info = mt5.account_info()
            if info is not None:
                return float(info.equity)
            # account_info() returning None almost always means this process's
            # MT5 connection isn't actually up (never initialized, terminal
            # closed, or logged out) -- try once to (re)connect before giving
            # up, since a bot polling every few seconds means a transient
            # drop shouldn't require a manual restart to recover from.
            if mt5.initialize():
                info = mt5.account_info()
                if info is not None:
                    return float(info.equity)
        raise HTTPException(
            status_code=503,
            detail="No equity source available. MetaTrader5 not connected in the MRM's "
                   "own process (check MT5 is open and logged in on this machine) and no "
                   "manual override set -- call POST /v1/set_equity for testing/non-Windows use."
        )

    # -- day rollover -------------------------------------------------------------
    def _ensure_day(self):
        today = date.today()
        if self.daily is None or self.daily.day != today:
            try:
                equity = self.get_account_equity()
            except HTTPException:
                equity = 0.0  # will require /v1/set_equity before trades can be approved
            peak = equity
            if self.daily is not None:
                peak = max(self.daily.peak_equity, equity)
            self.daily = DailyState(day=today, start_equity=equity, peak_equity=peak)
            self.store.save_daily_state(self.daily)
            self.store.log("day_rollover", {"day": today.isoformat(), "start_equity": equity})

    # -- exposure math -------------------------------------------------------------
    def open_and_reserved(self) -> List[Position]:
        return [p for p in self.positions.values() if p.status in ("reserved", "open")]

    def total_open_risk_pct(self) -> float:
        return sum(p.risk_pct for p in self.open_and_reserved())

    def currency_exposure_pct(self, currency: str) -> float:
        total = 0.0
        for p in self.open_and_reserved():
            base, quote = currencies_in(p.symbol)
            if currency in (base, quote):
                total += p.risk_pct
        return total

    def correlated_group_risk_pct(self, symbol: str) -> float:
        """Sum of risk across all open/reserved positions correlated with `symbol`."""
        total = 0.0
        for p in self.open_and_reserved():
            corr = get_correlation(symbol, p.symbol)
            if abs(corr) >= Config.CORRELATION_THRESHOLD:
                total += p.risk_pct
        return total

    def net_opposing_exposure(self, symbol: str, direction: str) -> float:
        """
        Same-symbol netting: if bots disagree on direction for the same symbol,
        return the SIGNED existing exposure (positive = same side as `direction`
        already open, negative = opposing side already open).
        """
        signed = 0.0
        for p in self.open_and_reserved():
            if p.symbol != symbol:
                continue
            sign = 1 if p.direction == direction else -1
            signed += sign * p.risk_pct
        return signed

    def group_key_for_spacing(self, symbol: str) -> str:
        """All symbols correlated with `symbol` (incl. itself) share one clustering clock."""
        members = sorted(
            [symbol] + [s for pair, _ in Config.CORRELATION_MATRIX.items()
                        for s in pair if s != symbol and abs(get_correlation(symbol, s)) >= Config.CORRELATION_THRESHOLD]
        )
        return "|".join(sorted(set(members)))


# ══════════════════════════════════════════════════════════════════════════
# API MODELS
# ══════════════════════════════════════════════════════════════════════════

class TradeRequest(BaseModel):
    bot_id: str = Field(..., description="e.g. 'bot_1_v36', 'bot_3_v45_gbpjpy'")
    symbol: str
    direction: Direction
    proposed_risk_pct: float = Field(..., gt=0, description="risk as % of account equity, e.g. 1.0")
    strategy_tag: str = ""


class TradeDecision(BaseModel):
    approved: bool
    position_id: Optional[str] = None
    approved_risk_pct: float = 0.0
    reason: str


class ConfirmFill(BaseModel):
    position_id: str


class ClosePosition(BaseModel):
    position_id: str
    realized_pnl_pct: float = Field(..., description="P&L as % of account equity, signed")


class SetEquity(BaseModel):
    equity: float


class HaltRequest(BaseModel):
    reason: str = "manual halt"


class ResumeRequest(BaseModel):
    confirm: bool = False


# ══════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="BabsBooks Master Risk Manager", version="1.0")
store = Store(Config.DB_PATH)
state = RiskState(store)


@app.on_event("startup")
def connect_mt5_on_startup():
    """
    The MRM runs as its own process, separate from every bot -- MT5's Python
    module keeps a connection per-process, so the MRM needs its own
    mt5.initialize() call. Without this, mt5.account_info() silently returns
    None forever and every trade request fails closed with no clear signal
    why. Connecting here (rather than only lazily inside get_account_equity)
    means the failure shows up immediately in this process's own log at
    startup, instead of hours into a run.
    """
    if not MT5_AVAILABLE:
        print("[MRM] MetaTrader5 package not importable -- running in manual-equity mode. "
              "Use POST /v1/set_equity.")
        return
    if mt5.initialize():
        info = mt5.account_info()
        if info is not None:
            print(f"[MRM] Connected to MT5 -- account #{info.login}, "
                  f"equity=${info.equity:,.2f}, broker={info.company}")
        else:
            print("[MRM] mt5.initialize() succeeded but account_info() returned None -- "
                  "make sure MT5 is logged into an account, not just open.")
    else:
        err = mt5.last_error()
        print(f"[MRM] mt5.initialize() FAILED: {err}. "
              f"Equity will be unavailable until this is fixed or /v1/set_equity is called. "
              f"Check: MT5 is open, logged in, and 'Allow automated trading' is enabled.")


def _halt(reason: str):
    state.daily.halted = True
    state.daily.halt_reason = reason
    store.save_daily_state(state.daily)
    store.log("halt", {"reason": reason})


@app.post("/v1/set_equity")
def set_equity(body: SetEquity):
    """Manual equity override -- for testing, or bridging if MT5 isn't reachable
    from this process. In production with MT5 available this is not needed."""
    with state.lock:
        state.manual_equity_override = body.equity
        # If today's start_equity was never properly initialized (equity source
        # wasn't available yet at startup/day-rollover, so it fell back to 0),
        # backfill it now with the first real equity reading we get. Without
        # this, daily-drawdown % is computed against a start_equity of 0
        # forever until the next calendar day rolls over.
        if state.daily is not None and state.daily.start_equity == 0:
            state.daily.start_equity = body.equity
            state.daily.peak_equity = max(state.daily.peak_equity, body.equity)
            store.save_daily_state(state.daily)
        state._ensure_day()
    return {"ok": True, "equity": body.equity}


@app.post("/v1/request_trade", response_model=TradeDecision)
def request_trade(req: TradeRequest):
    with state.lock:
        state._ensure_day()
        d = state.daily
        equity = state.get_account_equity()
        d.peak_equity = max(d.peak_equity, equity)

        # 1. Fleet-wide halt check (drawdown, manual, or consecutive-loss cooldown)
        if d.halted:
            return TradeDecision(approved=False, reason=f"FLEET HALTED: {d.halt_reason}")

        if d.cooldown_until and datetime.utcnow() < d.cooldown_until:
            remaining = (d.cooldown_until - datetime.utcnow()).total_seconds() / 60
            return TradeDecision(approved=False,
                                  reason=f"Consecutive-loss cooldown active, {remaining:.0f} min remaining")

        # 2. Live drawdown check against real equity (belt-and-suspenders even
        #    though closes also trigger this -- catches floating loss too if
        #    the bot reports interim mark-to-market via /v1/mark, see below)
        daily_dd_pct = ((d.start_equity - equity) / d.start_equity * 100) if d.start_equity else 0
        peak_dd_pct = ((d.peak_equity - equity) / d.peak_equity * 100) if d.peak_equity else 0
        if daily_dd_pct >= Config.MAX_DAILY_DRAWDOWN_PCT:
            _halt(f"Daily drawdown {daily_dd_pct:.2f}% >= cap {Config.MAX_DAILY_DRAWDOWN_PCT}%")
            return TradeDecision(approved=False, reason=d.halt_reason)
        if peak_dd_pct >= Config.MAX_PEAK_DRAWDOWN_PCT:
            _halt(f"Peak drawdown {peak_dd_pct:.2f}% >= cap {Config.MAX_PEAK_DRAWDOWN_PCT}%")
            return TradeDecision(approved=False, reason=d.halt_reason)

        # 3. Per-trade hard ceiling, regardless of what the bot asked for
        risk_pct = min(req.proposed_risk_pct, Config.MAX_SINGLE_TRADE_RISK_PCT)

        # 4. Account-wide total exposure ceiling
        if state.total_open_risk_pct() + risk_pct > Config.MAX_ACCOUNT_RISK_PCT:
            return TradeDecision(
                approved=False,
                reason=f"Account risk ceiling reached "
                       f"({state.total_open_risk_pct():.2f}% open, cap {Config.MAX_ACCOUNT_RISK_PCT}%)"
            )

        # 5. Currency exposure limit
        base, quote = currencies_in(req.symbol)
        cap = Config.MAX_SINGLE_TRADE_RISK_PCT * Config.MAX_CURRENCY_EXPOSURE_MULT
        for cur in (base, quote):
            existing = state.currency_exposure_pct(cur)
            if existing + risk_pct > cap:
                return TradeDecision(
                    approved=False,
                    reason=f"{cur} exposure would hit {existing + risk_pct:.2f}% (cap {cap:.2f}%)"
                )

        # 6. Correlated-group exposure limit
        group_existing = state.correlated_group_risk_pct(req.symbol)
        if group_existing + risk_pct > Config.CORRELATED_GROUP_RISK_CAP_PCT:
            return TradeDecision(
                approved=False,
                reason=f"Correlated-group risk would hit {group_existing + risk_pct:.2f}% "
                       f"(cap {Config.CORRELATED_GROUP_RISK_CAP_PCT}%) -- another bot already "
                       f"holds a correlated position"
            )

        # 7. Same-symbol opposing-direction netting (two bots disagree on direction)
        net_existing = state.net_opposing_exposure(req.symbol, req.direction.value)
        if net_existing < 0:
            # There's already opposing exposure on this exact symbol.
            opposing_amt = abs(net_existing)
            if risk_pct <= opposing_amt:
                return TradeDecision(
                    approved=False,
                    reason=f"Bots disagree on {req.symbol} direction -- "
                           f"{opposing_amt:.2f}% already open on the opposite side. "
                           f"Netted to zero, rejecting rather than flip-flopping the account."
                )
            else:
                risk_pct = risk_pct - opposing_amt
                # falls through approved at the reduced, netted size

        # 8. Trade clustering / timing gate within the correlated group
        group_key = state.group_key_for_spacing(req.symbol)
        last = state.last_entry_time_by_group.get(group_key)
        if last and (datetime.utcnow() - last).total_seconds() < Config.MIN_TRADE_SPACING_SECONDS:
            wait = Config.MIN_TRADE_SPACING_SECONDS - (datetime.utcnow() - last).total_seconds()
            return TradeDecision(
                approved=False,
                reason=f"Clustering guard: another correlated entry {wait:.0f}s ago, "
                       f"min spacing {Config.MIN_TRADE_SPACING_SECONDS}s"
            )

        # --- APPROVED: reserve exposure immediately, before the fill confirms.
        # This closes the race condition where two near-simultaneous requests
        # both look approvable because neither one's reservation existed yet.
        position_id = str(uuid.uuid4())
        pos = Position(
            position_id=position_id, bot_id=req.bot_id, symbol=req.symbol,
            direction=req.direction.value, risk_pct=risk_pct,
            risk_amount=equity * risk_pct / 100, strategy_tag=req.strategy_tag,
            status="reserved",
        )
        state.positions[position_id] = pos
        state.last_entry_time_by_group[group_key] = datetime.utcnow()
        store.upsert_position(pos)
        store.log("trade_approved", asdict(pos))

        return TradeDecision(approved=True, position_id=position_id,
                              approved_risk_pct=risk_pct, reason="approved")


@app.post("/v1/confirm_fill")
def confirm_fill(body: ConfirmFill):
    with state.lock:
        pos = state.positions.get(body.position_id)
        if not pos:
            raise HTTPException(404, "unknown position_id")
        pos.status = "open"
        pos.opened_at = datetime.utcnow()
        store.upsert_position(pos)
        store.log("fill_confirmed", {"position_id": pos.position_id})
    return {"ok": True}


@app.post("/v1/reject_fill")
def reject_fill(body: ConfirmFill):
    """Broker rejected the order after MRM approval -- release the reservation."""
    with state.lock:
        pos = state.positions.get(body.position_id)
        if not pos:
            raise HTTPException(404, "unknown position_id")
        pos.status = "released"
        store.upsert_position(pos)
        store.log("fill_rejected_released", {"position_id": pos.position_id})
    return {"ok": True}


@app.post("/v1/close_position")
def close_position(body: ClosePosition):
    with state.lock:
        pos = state.positions.get(body.position_id)
        if not pos:
            raise HTTPException(404, "unknown position_id")
        state._ensure_day()
        d = state.daily

        pos.status = "closed"
        pos.closed_at = datetime.utcnow()
        pos.realized_pnl = body.realized_pnl_pct
        store.upsert_position(pos)

        d.realized_pnl += body.realized_pnl_pct
        if body.realized_pnl_pct < 0:
            d.consecutive_losses += 1
        else:
            d.consecutive_losses = 0

        if d.consecutive_losses >= Config.MAX_FLEET_CONSECUTIVE_LOSSES:
            d.cooldown_until = datetime.utcnow().timestamp() + Config.CONSECUTIVE_LOSS_COOLDOWN_MIN * 60
            from datetime import timedelta as _td
            d.cooldown_until = datetime.utcnow() + _td(minutes=Config.CONSECUTIVE_LOSS_COOLDOWN_MIN)
            store.log("consecutive_loss_cooldown", {
                "count": d.consecutive_losses, "cooldown_until": d.cooldown_until.isoformat()
            })

        equity = state.get_account_equity()
        daily_dd_pct = ((d.start_equity - equity) / d.start_equity * 100) if d.start_equity else 0
        if daily_dd_pct >= Config.MAX_DAILY_DRAWDOWN_PCT:
            _halt(f"Daily drawdown {daily_dd_pct:.2f}% >= cap {Config.MAX_DAILY_DRAWDOWN_PCT}% (post-close)")

        store.save_daily_state(d)
        store.log("position_closed", asdict(pos))
    return {"ok": True, "daily_realized_pnl_pct": d.realized_pnl, "consecutive_losses": d.consecutive_losses}


@app.get("/v1/status")
def status():
    with state.lock:
        state._ensure_day()
        d = state.daily
        try:
            equity = state.get_account_equity()
        except HTTPException:
            equity = None
        open_positions = [asdict(p) for p in state.open_and_reserved()]
        currency_exposure = {}
        for p in state.open_and_reserved():
            base, quote = currencies_in(p.symbol)
            for cur in (base, quote):
                currency_exposure[cur] = currency_exposure.get(cur, 0) + p.risk_pct
        return {
            "day": d.day.isoformat(),
            "equity": equity,
            "start_equity": d.start_equity,
            "peak_equity": d.peak_equity,
            "daily_realized_pnl_pct": d.realized_pnl,
            "consecutive_losses": d.consecutive_losses,
            "halted": d.halted,
            "halt_reason": d.halt_reason,
            "cooldown_until": d.cooldown_until.isoformat() if d.cooldown_until else None,
            "total_open_risk_pct": state.total_open_risk_pct(),
            "account_risk_cap_pct": Config.MAX_ACCOUNT_RISK_PCT,
            "currency_exposure_pct": currency_exposure,
            "open_positions": open_positions,
        }


@app.post("/v1/reset_day")
def reset_day():
    with state.lock:
        equity = state.get_account_equity()
        state.daily = DailyState(day=date.today(), start_equity=equity, peak_equity=equity)
        store.save_daily_state(state.daily)
        store.log("manual_day_reset", {"equity": equity})
    return {"ok": True}


@app.post("/v1/halt")
def manual_halt(body: HaltRequest):
    with state.lock:
        _halt(body.reason)
    return {"ok": True, "halted": True}


@app.post("/v1/resume")
def manual_resume(body: ResumeRequest):
    if not body.confirm:
        raise HTTPException(400, "Set confirm=true to resume trading after a halt.")
    with state.lock:
        state.daily.halted = False
        state.daily.halt_reason = ""
        store.save_daily_state(state.daily)
        store.log("manual_resume", {})
    return {"ok": True, "halted": False}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8800)