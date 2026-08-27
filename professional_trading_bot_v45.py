"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         BABSBOOKS PROFESSIONAL TRADING BOT  V45 — MECHANICAL EDITION        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  MECHANICAL THINKING ENGINE                                                  ║
║  The bot reads the market, builds a complete picture, then makes one        ║
║  binary decision: trade or skip. No ambiguity. No guessing.                 ║
║                                                                              ║
║  DECISION HIERARCHY (must pass ALL stages):                                  ║
║    1. Market Regime    — is this a tradeable market right now?               ║
║    2. Session Filter   — are we in London or Overlap session?                ║
║    3. News Filter      — no high-impact event within 30 min?                 ║
║    4. Structure Bias   — what direction is the dominant trend?               ║
║    5. SMC Setup        — institutional footprint present?                    ║
║    6. Entry Trigger    — momentum candle confirming the move?                ║
║    7. Risk Gate        — does the RR and position size make sense?           ║
║                                                                              ║
║  PROFITABILITY RULES:                                                         ║
║    • Never trade against the H1 trend                                        ║
║    • Never enter without SMC confluence (EQ + BOS + FVG)                    ║
║    • Never enter without a momentum candle trigger                           ║
║    • Always use 3× ATR stop — wide enough to breathe                        ║
║    • Always target minimum 2:1 RR                                            ║
║    • Max 3 trades/day — quality over quantity                                ║
║    • Stop trading after 2 losses in a day                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ── Imports ───────────────────────────────────────────────────────────────────
import os, time, json, sqlite3, traceback, warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

import MetaTrader5 as mt5

# ── Master Risk Manager integration ─────────────────────────────────────────
from bot_risk_config import risk, get_symbols, track_reservation, poll_closed_positions

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION — every number has a clear reason
# ─────────────────────────────────────────────────────────────────────────────

# Account & risk
ACCOUNT_CURRENCY    = "USD"
RISK_PER_TRADE_PCT  = 0.50        # risk 0.5% per trade = ~$125 on $25k
MAX_LOT             = 0.50        # hard cap — never more than this
MAX_TRADES_PER_DAY  = 3           # quality over quantity
MAX_LOSSES_PER_DAY  = 2           # stop trading after 2 losses today
MIN_RR              = 2.0         # minimum reward:risk ratio to enter

# Stop loss — wide so normal candle noise doesn't stop us out
ATR_SL_MULTIPLIER   = 3.5         # SL = 3.5 × ATR14 (M5)
MIN_SL_PIPS: Dict[str, int] = {   # absolute floor — never tighter
    "EURUSD": 40, "GBPUSD": 50, "AUDUSD": 40,
    "EURJPY": 70, "GBPJPY": 90, "EURGBP": 40,
}

# Market regime — only trade when market is alive
MIN_ATR_PCT         = 0.018       # below this = dead market, skip
MIN_VOLUME_RATIO    = 0.70        # need real participation

# Signal quality — all must pass
MIN_BP              = 0.62        # breakout probability minimum
MIN_PRESSURE        = 55          # institutional pressure minimum
MIN_CONFIDENCE      = 72          # entry confidence minimum

# Sessions (UTC hours) — only London peak and Overlap
SESSIONS = {
    "London":  (8,  12),
    "Overlap": (12, 16),
}

# Symbols to trade
# EURJPY removed — 29 trades, 17.2% win rate in live data = no edge
# AUDUSD kept — 10 trades, 90% win rate = real edge
# Start with the 4 pairs that show positive signals
SYMBOLS = get_symbols(["AUDUSD", "EURUSD", "GBPUSD", "EURGBP", "GBPJPY"])
# Add EURJPY back only after 30+ trades show >50% win rate on the above

# News blackout — UTC (hour, minute, description)
NEWS_EVENTS: List[Tuple[int, int, str]] = [
    (8,  30, "UK CPI/GDP"),
    (9,  30, "BOE/ECB Decision"),
    (12, 30, "US CPI/NFP/Retail"),
    (13, 30, "US Jobless Claims"),
    (14,  0, "US ISM/Fed"),
    (18,  0, "Fed Rate Decision"),
]
NEWS_BLOCK_BEFORE = 30  # minutes
NEWS_BLOCK_AFTER  = 20  # minutes

# Persistence
_DIR            = os.path.dirname(os.path.abspath(__file__))
DB_PATH         = os.path.join(_DIR, "babsbooks_v45.db")
POSITIONS_FILE  = os.path.join(_DIR, "v45_positions.json")
CLUSTER_FILE    = os.path.join(_DIR, "v45_cluster.json")

# ─────────────────────────────────────────────────────────────────────────────
#  MARKET INTELLIGENCE — fundamental bias per pair
#  Update RATES and SENTIMENT weekly after major releases
# ─────────────────────────────────────────────────────────────────────────────

CB_RATES = {           # central bank rates %
    "EUR": 2.15,       # ECB — hiking cycle
    "GBP": 3.75,       # BoE — holding
    "USD": 4.50,       # Fed — holding
    "AUD": 4.10,       # RBA
    "JPY": 0.50,       # BoJ — ultra-loose
}

SENTIMENT = {          # +1 bullish, 0 neutral, -1 bearish (recent data)
    "EUR": +1,         # CPI rising, ECB hawkish
    "GBP":  0,         # mixed UK data
    "USD":  0,         # cooling labour market
    "AUD": -1,         # China demand soft
    "JPY": -1,         # carry trade active, BoJ dovish
}

# ─────────────────────────────────────────────────────────────────────────────
#  DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    """Complete picture of the market for one symbol at one moment."""
    symbol:     str
    timestamp:  str
    session:    str
    atr_pct:    float       # ATR as % of price
    atr_val:    float       # ATR in price units
    volume:     float       # volume ratio vs average
    trend_h1:   str         # "BULL" / "BEAR" / "RANGE"
    structure:  str         # "BULLISH" / "BEARISH" / "RANGING"
    bp:         float       # breakout probability 0-1
    pressure:   int         # institutional pressure 0-100
    rsi:        float
    at_eq:      bool        # price near equilibrium
    bull_bos:   bool        # bullish break of structure
    bear_bos:   bool        # bearish break of structure
    bull_fvg:   bool        # bullish fair value gap
    bear_fvg:   bool        # bearish fair value gap
    bull_engulf:bool        # bullish engulfing candle
    bear_engulf:bool        # bearish engulfing candle
    bull_pin:   bool        # bullish pin bar
    bear_pin:   bool        # bearish pin bar
    fund_bias:  str         # "buy" / "sell" / "neutral"
    fund_str:   int         # fundamental strength 0-100
    price:      float
    spread:     float       # pips


@dataclass
class TradeDecision:
    """The bot's binary decision for a symbol."""
    symbol:     str
    action:     str         # "BUY" / "SELL" / "SKIP"
    reason:     str         # why this decision was made
    confidence: int         # 0-100
    entry:      float = 0.0
    sl:         float = 0.0
    tp:         float = 0.0
    sl_pips:    int   = 0
    tp_pips:    int   = 0
    rr:         float = 0.0
    lot:        float = 0.0
    risk_usd:   float = 0.0


@dataclass
class OpenTrade:
    ticket:     int
    symbol:     str
    direction:  str
    entry:      float
    sl:         float
    tp:         float
    lot:        float
    opened_at:  str
    db_id:      int = 0

# ─────────────────────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────────────────────

class Database:
    def __init__(self):
        self.path = DB_PATH
        self._setup()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _setup(self):
        with self._connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket        INTEGER UNIQUE,
                    symbol        TEXT,
                    direction     TEXT,
                    entry_price   REAL,
                    sl            REAL,
                    tp            REAL,
                    lot           REAL,
                    sl_pips       INTEGER,
                    rr            REAL,
                    session       TEXT,
                    trend_h1      TEXT,
                    structure     TEXT,
                    bp            REAL,
                    pressure      INTEGER,
                    at_eq         INTEGER,
                    bos           INTEGER,
                    fvg           INTEGER,
                    pattern       TEXT,
                    fund_bias     TEXT,
                    confidence    INTEGER,
                    entry_time    TEXT,
                    exit_price    REAL,
                    profit        REAL,
                    realized_r    REAL,
                    hold_minutes  REAL,
                    win           INTEGER,
                    exit_time     TEXT
                );
                CREATE TABLE IF NOT EXISTS equity (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT,
                    balance   REAL,
                    equity    REAL,
                    open_lots REAL
                );
            """)

    def open_trade(self, t: OpenTrade, snap: MarketSnapshot,
                   sl_pips: int, rr: float, confidence: int) -> int:
        pat = ("engulf" if snap.bull_engulf or snap.bear_engulf
               else "pin" if snap.bull_pin or snap.bear_pin else "none")
        bos = 1 if (snap.bull_bos or snap.bear_bos) else 0
        fvg = 1 if (snap.bull_fvg or snap.bear_fvg) else 0
        with self._connect() as c:
            cur = c.execute("""
                INSERT OR IGNORE INTO trades
                (ticket,symbol,direction,entry_price,sl,tp,lot,sl_pips,rr,
                 session,trend_h1,structure,bp,pressure,at_eq,bos,fvg,pattern,
                 fund_bias,confidence,entry_time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (t.ticket, t.symbol, t.direction, t.entry, t.sl, t.tp, t.lot,
                  sl_pips, rr, snap.session, snap.trend_h1, snap.structure,
                  snap.bp, snap.pressure, 1 if snap.at_eq else 0,
                  bos, fvg, pat, snap.fund_bias, confidence,
                  datetime.now().isoformat()))
            return cur.lastrowid or 0

    def close_trade(self, db_id: int, ticket: int, exit_price: float,
                    profit: float, realized_r: float, hold_mins: float):
        win = 1 if profit > 0 else 0
        with self._connect() as c:
            c.execute("""
                UPDATE trades SET exit_price=?,profit=?,realized_r=?,
                hold_minutes=?,win=?,exit_time=?
                WHERE id=? OR ticket=?
            """, (exit_price, profit, realized_r, hold_mins, win,
                  datetime.now().isoformat(), db_id, ticket))

    def record_equity(self, balance: float, equity: float, lots: float):
        with self._connect() as c:
            c.execute("INSERT INTO equity (ts,balance,equity,open_lots) VALUES (?,?,?,?)",
                      (datetime.now().isoformat(), balance, equity, lots))

    def get_stats(self) -> Dict:
        with self._connect() as c:
            def s(q): return c.execute(q).fetchone()[0] or 0
            n    = s("SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL")
            wins = s("SELECT COUNT(*) FROM trades WHERE win=1 AND exit_price IS NOT NULL")
            pf_w = s("SELECT COALESCE(SUM(profit),0) FROM trades WHERE win=1")
            pf_l = abs(s("SELECT COALESCE(SUM(profit),0) FROM trades WHERE win=0 AND exit_price IS NOT NULL"))
            exp  = s("SELECT COALESCE(AVG(realized_r),0) FROM trades WHERE exit_price IS NOT NULL")
            return {
                "trades": n,
                "win_rate": (wins / n * 100) if n else 0,
                "profit_factor": (pf_w / pf_l) if pf_l else 0,
                "expectancy_r": exp,
                "total_profit": s("SELECT COALESCE(SUM(profit),0) FROM trades WHERE exit_price IS NOT NULL"),
            }

    def get_today_stats(self) -> Dict:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as c:
            rows = c.execute(
                "SELECT profit FROM trades WHERE exit_price IS NOT NULL AND entry_time LIKE ?",
                (f"{today}%",)
            ).fetchall()
        losses = sum(1 for r in rows if r[0] < 0)
        profit = sum(r[0] for r in rows)
        return {"trades": len(rows), "losses": losses, "profit": profit}

    def recover_offline_closes(self):
        """Find trades open in DB but closed in MT5 history."""
        from_dt = datetime.now() - timedelta(days=30)
        deals   = mt5.history_deals_get(from_dt, datetime.now()) or []
        closed  = {d.order: d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT}
        recovered = 0
        with self._connect() as c:
            open_rows = c.execute(
                "SELECT id, ticket, entry_price, sl, direction FROM trades WHERE exit_price IS NULL"
            ).fetchall()
        for row in open_rows:
            db_id, ticket, entry_p, sl, direction = row
            if ticket not in closed:
                continue
            deal = closed[ticket]
            profit   = deal.profit + deal.commission + deal.swap
            exit_p   = deal.price
            sl_dist  = abs(entry_p - (sl or 0)) or abs(entry_p) * 0.001
            mult     = 1 if direction == "BUY" else -1
            realized = round((exit_p - entry_p) / sl_dist * mult, 3)
            self.close_trade(db_id, ticket, exit_p, profit, realized, 0)
            recovered += 1
            icon = "✅" if profit > 0 else "❌"
            print(f"  {icon} Recovered #{ticket}  ${profit:+.2f}  ({realized:+.2f}R)")
        if recovered:
            print(f"  ✅ {recovered} trade(s) recovered from MT5 history")

    def backfill_realized_r(self) -> int:
        """
        Fix the 0.000R problem from v36.
        Recalculates realized_r from entry_price, sl, exit_price already
        stored in the database — these values were saved correctly, only
        the R calculation was missing.
        Run once on startup when using the old v36 database.
        """
        with self._connect() as c:
            rows = c.execute("""
                SELECT id, entry_price, sl, exit_price, direction, profit
                FROM trades
                WHERE exit_price IS NOT NULL
                AND (realized_r IS NULL OR realized_r = 0.0 OR realized_r = 0)
            """).fetchall()
            fixed = 0
            for db_id, entry_p, sl, exit_p, direction, profit in rows:
                if not all([entry_p, sl, exit_p]):
                    continue
                sl_dist = abs(entry_p - sl)
                if sl_dist < 1e-9:
                    continue
                mult     = 1.0 if direction in ("BUY", "buy") else -1.0
                realized = round((exit_p - entry_p) / sl_dist * mult, 3)
                win      = 1 if (profit or 0) > 0 else 0
                c.execute(
                    "UPDATE trades SET realized_r=?, win=? WHERE id=?",
                    (realized, win, db_id)
                )
                fixed += 1
        if fixed:
            print(f"  ✅ Backfilled realized_r for {fixed} historical trades")
        return fixed

# ─────────────────────────────────────────────────────────────────────────────
#  MARKET READER — builds the complete market picture
# ─────────────────────────────────────────────────────────────────────────────

def get_rates(symbol: str, timeframe, count: int = 200) -> Optional[pd.DataFrame]:
    bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if bars is None or len(bars) < 50:
        return None
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.columns = [c.lower() for c in df.columns]
    return df


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def pip_value(symbol: str) -> float:
    """Approximate pip value in USD per 1 lot."""
    if "JPY" in symbol:
        info = mt5.symbol_info(symbol)
        rate = info.bid if info else 160.0
        return 1000.0 / rate
    return 10.0


def read_market(symbol: str, session: str) -> Optional[MarketSnapshot]:
    """
    Build a complete market snapshot for one symbol.
    This is the bot's eyes — it sees everything before deciding anything.
    """
    df5  = get_rates(symbol, mt5.TIMEFRAME_M5,  200)
    df60 = get_rates(symbol, mt5.TIMEFRAME_H1,   50)
    if df5 is None or df60 is None:
        return None

    # ── Indicators on M5 ──────────────────────────────────────────────────────
    # ATR (14-period)
    high, low, close = df5["high"], df5["low"], df5["close"]
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr_val = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
    price   = float(close.iloc[-1])
    atr_pct = atr_val / price * 100

    # Volume ratio
    vol = df5.get("tick_volume", df5.get("real_volume", pd.Series([1]*len(df5))))
    vol_ma  = float(vol.rolling(20, min_periods=1).mean().iloc[-1]) or 1
    vol_ratio = float(vol.iloc[-1]) / vol_ma

    # RSI (14)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = float(100 - 100 / (1 + rs.iloc[-1]))

    # 20-bar range
    rh = float(high.iloc[-20:].max())
    rl = float(low.iloc[-20:].min())
    rw = rh - rl or 1e-8

    # Breakout probability
    pos_pct = (price - rl) / rw
    atr_exp = atr_val / (rw / 20) if rw > 0 else 1.0
    bp = min(1.0, max(0.0, (atr_exp - 0.8) / 1.2))

    # Pressure (volume × ATR expansion)
    pressure = int(min(100, vol_ratio * 40 + atr_exp * 20))

    # Spread
    info = mt5.symbol_info(symbol)
    spread = (info.spread / 10.0) if info else 3.0

    # ── H1 trend ──────────────────────────────────────────────────────────────
    h1_close = df60["close"]
    ema21_h1 = float(h1_close.ewm(span=21).mean().iloc[-1])
    ema50_h1 = float(h1_close.ewm(span=50).mean().iloc[-1])
    h1_price = float(h1_close.iloc[-1])
    if h1_price > ema21_h1 > ema50_h1:
        trend_h1 = "BULL"
    elif h1_price < ema21_h1 < ema50_h1:
        trend_h1 = "BEAR"
    else:
        trend_h1 = "RANGE"

    # ── M5 Market structure (50-bar HH/HL vs LH/LL) ──────────────────────────
    tail   = df5.tail(50)
    half   = 25
    f_hh   = float(tail.iloc[:half]["high"].max())
    f_ll   = float(tail.iloc[:half]["low"].min())
    s_hh   = float(tail.iloc[half:]["high"].max())
    s_ll   = float(tail.iloc[half:]["low"].min())
    if s_hh > f_hh and s_ll > f_ll:
        structure = "BULLISH"
    elif s_hh < f_hh and s_ll < f_ll:
        structure = "BEARISH"
    else:
        structure = "RANGING"

    # ── SMC: EQ, BOS, FVG ─────────────────────────────────────────────────────
    eq       = (rh + rl) / 2.0
    at_eq    = abs(price - eq) <= atr_val * 0.5

    swing_h  = float(df5["high"].iloc[-11:-1].max())
    swing_l  = float(df5["low"].iloc[-11:-1].min())
    bull_bos = price > swing_h
    bear_bos = price < swing_l

    c1, c3   = df5.iloc[-3], df5.iloc[-1]
    bull_fvg = float(c1["high"]) < float(c3["low"])
    bear_fvg = float(c1["low"])  > float(c3["high"])

    # ── Candle patterns ────────────────────────────────────────────────────────
    cur, prev = df5.iloc[-1], df5.iloc[-2]
    c_o, c_c  = float(cur["open"]),  float(cur["close"])
    c_h, c_l  = float(cur["high"]),  float(cur["low"])
    p_o, p_c  = float(prev["open"]), float(prev["close"])
    c_range   = c_h - c_l or 1e-8
    body      = abs(c_c - c_o)
    lo_wick   = min(c_o, c_c) - c_l
    hi_wick   = c_h - max(c_o, c_c)

    bull_engulf = p_c < p_o and c_c > c_o and c_o < p_c and c_c > p_o
    bear_engulf = p_c > p_o and c_c < c_o and c_o > p_c and c_c < p_o
    bull_pin    = lo_wick / c_range >= 0.60 and body / c_range <= 0.30
    bear_pin    = hi_wick / c_range >= 0.60 and body / c_range <= 0.30

    # ── Fundamental bias ───────────────────────────────────────────────────────
    base, quote = symbol[:3], symbol[3:]
    rate_diff   = CB_RATES.get(base, 0) - CB_RATES.get(quote, 0)
    sent_diff   = SENTIMENT.get(base, 0) - SENTIMENT.get(quote, 0)
    if rate_diff > 0.5 or sent_diff > 0:
        fund_bias = "buy";  fund_str = int(min(100, abs(rate_diff) * 20 + abs(sent_diff) * 25))
    elif rate_diff < -0.5 or sent_diff < 0:
        fund_bias = "sell"; fund_str = int(min(100, abs(rate_diff) * 20 + abs(sent_diff) * 25))
    else:
        fund_bias = "neutral"; fund_str = 0

    return MarketSnapshot(
        symbol=symbol, timestamp=datetime.utcnow().isoformat(), session=session,
        atr_pct=atr_pct, atr_val=atr_val, volume=vol_ratio,
        trend_h1=trend_h1, structure=structure, bp=bp, pressure=pressure,
        rsi=rsi, at_eq=at_eq, bull_bos=bull_bos, bear_bos=bear_bos,
        bull_fvg=bull_fvg, bear_fvg=bear_fvg,
        bull_engulf=bull_engulf, bear_engulf=bear_engulf,
        bull_pin=bull_pin, bear_pin=bear_pin,
        fund_bias=fund_bias, fund_str=fund_str,
        price=price, spread=spread,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  MECHANICAL DECISION ENGINE
#  One function. One decision. Clear reasoning at every step.
# ─────────────────────────────────────────────────────────────────────────────

def decide(snap: MarketSnapshot, balance: float) -> TradeDecision:
    """
    The mechanical brain. Processes the market snapshot through 7 gates.
    Returns a TradeDecision with action=BUY/SELL or action=SKIP with reason.

    Gate 1: Market regime
    Gate 2: Volatility minimum
    Gate 3: Volume confirmation
    Gate 4: Direction determination (H1 trend + structure)
    Gate 5: SMC setup confirmation (at least 2 of 3: EQ, BOS, FVG)
    Gate 6: Entry trigger (pattern OR price at boundary)
    Gate 7: Risk/reward calculation
    """
    sym = snap.symbol

    def skip(reason: str) -> TradeDecision:
        return TradeDecision(symbol=sym, action="SKIP", reason=reason, confidence=0)

    # ── Gate 1: Market regime ─────────────────────────────────────────────────
    if snap.atr_pct < MIN_ATR_PCT:
        return skip(f"Dead market — ATR {snap.atr_pct:.3f}% < {MIN_ATR_PCT}%")

    # ── Gate 2: Volume ────────────────────────────────────────────────────────
    if snap.volume < MIN_VOLUME_RATIO:
        return skip(f"Low volume — {snap.volume:.2f}× < {MIN_VOLUME_RATIO}×")

    # ── Gate 3: Breakout probability and pressure ─────────────────────────────
    if snap.bp < MIN_BP:
        return skip(f"Weak setup — bp={snap.bp:.2f} < {MIN_BP}")
    if snap.pressure < MIN_PRESSURE:
        return skip(f"Low pressure — {snap.pressure} < {MIN_PRESSURE}")

    # ── Gate 4: Direction — H1 trend + M5 structure must agree ───────────────
    # Determine candidate direction from H1 trend (primary) and structure
    if snap.trend_h1 == "BULL" and snap.structure in ("BULLISH", "RANGING"):
        direction = "BUY"
    elif snap.trend_h1 == "BEAR" and snap.structure in ("BEARISH", "RANGING"):
        direction = "SELL"
    elif snap.trend_h1 == "BULL" and snap.structure == "BEARISH":
        return skip("H1 BULL but M5 BEARISH — conflicting timeframes")
    elif snap.trend_h1 == "BEAR" and snap.structure == "BULLISH":
        return skip("H1 BEAR but M5 BULLISH — conflicting timeframes")
    elif snap.trend_h1 == "RANGE":
        # No clear H1 trend — need very strong M5 structure
        if snap.structure == "BULLISH":
            direction = "BUY"
        elif snap.structure == "BEARISH":
            direction = "SELL"
        else:
            return skip("No clear direction — H1 RANGE + M5 RANGING")
    else:
        return skip(f"Direction unclear — H1:{snap.trend_h1} M5:{snap.structure}")

    # Fundamental alignment bonus / penalty
    if snap.fund_bias == direction.lower() and snap.fund_str >= 30:
        fund_bonus = 10  # fundamentals agree
    elif snap.fund_bias != "neutral" and snap.fund_bias != direction.lower() and snap.fund_str >= 40:
        return skip(f"Counter-fundamental — signal={direction} but fundamentals={snap.fund_bias}({snap.fund_str}%)")
    else:
        fund_bonus = 0

    # ── Gate 5: SMC — need at least 2 of 3 components ────────────────────────
    if direction == "BUY":
        smc_components = [snap.at_eq, snap.bull_bos, snap.bull_fvg]
        smc_labels     = ["at_EQ", "bull_BOS", "bull_FVG"]
    else:
        smc_components = [snap.at_eq, snap.bear_bos, snap.bear_fvg]
        smc_labels     = ["at_EQ", "bear_BOS", "bear_FVG"]

    smc_count  = sum(smc_components)
    smc_active = [smc_labels[i] for i, v in enumerate(smc_components) if v]

    if smc_count < 1:
        return skip(f"No SMC confirmation ({direction}) — need at least 1 of EQ/BOS/FVG")

    smc_bonus = smc_count * 8  # 8/16/24 pts

    # ── Gate 6: Entry trigger — momentum candle in direction ─────────────────
    if direction == "BUY":
        has_trigger = (snap.bull_engulf or snap.bull_pin or
                       snap.rsi < 35 or   # oversold bounce
                       snap.bull_bos)     # price already broke structure
        trigger_name = ("engulf" if snap.bull_engulf else
                        "pin_bar" if snap.bull_pin else
                        "oversold" if snap.rsi < 35 else
                        "BOS_break" if snap.bull_bos else "none")
    else:
        has_trigger = (snap.bear_engulf or snap.bear_pin or
                       snap.rsi > 65 or
                       snap.bear_bos)
        trigger_name = ("engulf" if snap.bear_engulf else
                        "pin_bar" if snap.bear_pin else
                        "overbought" if snap.rsi > 65 else
                        "BOS_break" if snap.bear_bos else "none")

    if not has_trigger:
        return skip(f"No entry trigger ({direction}) — waiting for momentum candle")

    trigger_bonus = 15 if trigger_name in ("engulf", "pin_bar") else 8

    # ── Gate 7: Risk/reward ───────────────────────────────────────────────────
    ps     = pip_size(sym)
    pv     = pip_value(sym)

    # SL: 3.5× ATR from entry, then enforce minimum pip floor
    sl_raw      = snap.atr_val * ATR_SL_MULTIPLIER
    sl_pips_raw = int(sl_raw / ps)
    sl_pips     = max(MIN_SL_PIPS.get(sym, 40), sl_pips_raw)
    sl_dist     = sl_pips * ps

    entry = snap.price
    if direction == "BUY":
        sl = entry - sl_dist
        tp = entry + sl_dist * MIN_RR
    else:
        sl = entry + sl_dist
        tp = entry - sl_dist * MIN_RR

    tp_pips = int(abs(tp - entry) / ps)
    rr      = tp_pips / sl_pips if sl_pips else 0

    if rr < MIN_RR:
        return skip(f"RR {rr:.2f} < {MIN_RR} minimum")

    # Position sizing: risk exactly RISK_PER_TRADE_PCT of balance
    risk_usd  = balance * RISK_PER_TRADE_PCT / 100.0
    lot_value = sl_dist * (pv / ps)
    raw_lot   = risk_usd / lot_value if lot_value > 0 else 0.01
    lot       = round(max(0.01, min(MAX_LOT, raw_lot)) / 0.01) * 0.01

    # ── Confidence score ──────────────────────────────────────────────────────
    conf = (
        40                              # base score for passing all gates
        + smc_bonus                     # 8/16/24 — SMC quality
        + trigger_bonus                 # 8/15 — entry trigger quality
        + fund_bonus                    # 0/10 — fundamental alignment
        + (5 if smc_count == 3 else 0) # full SMC bonus
        + (5 if snap.trend_h1 != "RANGE" else 0)  # clear H1 trend
        + (3 if snap.at_eq else 0)     # at equilibrium
    )
    conf = min(100, conf)

    if conf < MIN_CONFIDENCE:
        return skip(f"Confidence {conf} < {MIN_CONFIDENCE} minimum")

    smc_str = "+".join(smc_active) if smc_active else "none"
    reason  = (f"H1:{snap.trend_h1} M5:{snap.structure} | "
               f"SMC:{smc_str} | trigger:{trigger_name} | "
               f"fund:{snap.fund_bias} | conf:{conf}")

    return TradeDecision(
        symbol=sym, action=direction, reason=reason, confidence=conf,
        entry=entry, sl=sl, tp=tp,
        sl_pips=sl_pips, tp_pips=tp_pips, rr=round(rr, 2),
        lot=lot, risk_usd=round(risk_usd, 2),
    )

# ─────────────────────────────────────────────────────────────────────────────
#  SESSION & NEWS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def current_session() -> Optional[str]:
    """Returns current session name or None if outside trading hours."""
    h = datetime.utcnow().hour
    for name, (start, end) in SESSIONS.items():
        if start <= h < end:
            return name
    return None


def news_blocked() -> Tuple[bool, str]:
    """Returns (blocked, reason) if near a high-impact news event."""
    now_mins = datetime.utcnow().hour * 60 + datetime.utcnow().minute
    for nh, nm, desc in NEWS_EVENTS:
        news_mins = nh * 60 + nm
        before    = news_mins - now_mins
        after     = now_mins - news_mins
        if 0 <= before <= NEWS_BLOCK_BEFORE:
            return True, f"⏰ {desc} in {before}min"
        if 0 <= after <= NEWS_BLOCK_AFTER:
            return True, f"📰 {desc} released {after}min ago"
    return False, ""

# ─────────────────────────────────────────────────────────────────────────────
#  POSITION PERSISTENCE — survives restarts
# ─────────────────────────────────────────────────────────────────────────────

def save_positions(trades: Dict[int, OpenTrade]):
    try:
        data = {str(k): {
            "ticket": v.ticket, "symbol": v.symbol, "direction": v.direction,
            "entry": v.entry, "sl": v.sl, "tp": v.tp, "lot": v.lot,
            "opened_at": v.opened_at, "db_id": v.db_id,
        } for k, v in trades.items()}
        with open(POSITIONS_FILE, "w") as f:
            json.dump({"positions": data, "saved": datetime.now().isoformat()}, f, indent=2)
    except Exception as e:
        print(f"  ⚠️  Could not save positions: {e}")


def load_positions() -> Dict[int, OpenTrade]:
    try:
        if not os.path.exists(POSITIONS_FILE):
            return {}
        with open(POSITIONS_FILE) as f:
            data = json.load(f)
        result = {}
        for k, v in data.get("positions", {}).items():
            t = OpenTrade(
                ticket=v["ticket"], symbol=v["symbol"], direction=v["direction"],
                entry=v["entry"], sl=v["sl"], tp=v["tp"], lot=v["lot"],
                opened_at=v["opened_at"], db_id=v.get("db_id", 0),
            )
            result[v["ticket"]] = t
        if result:
            print(f"  🔄 Loaded {len(result)} open position(s) from disk")
        return result
    except Exception as e:
        print(f"  ⚠️  Could not load positions: {e}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
#  CLUSTER GUARD — prevents duplicate entries on same symbol
# ─────────────────────────────────────────────────────────────────────────────

class ClusterGuard:
    MIN_SECONDS = 300    # 5 minutes between trades on same symbol
    MIN_PIPS    = 8      # price must have moved 8 pips from last entry

    def __init__(self):
        self._times:  Dict[str, float] = {}
        self._prices: Dict[str, float] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(CLUSTER_FILE):
                with open(CLUSTER_FILE) as f:
                    d = json.load(f)
                now_wall = time.time()
                now_mono = time.monotonic()
                for sym, wall_t in d.get("times", {}).items():
                    self._times[sym]  = now_mono - (now_wall - wall_t)
                self._prices = d.get("prices", {})
        except Exception:
            pass

    def _save(self):
        try:
            now_wall = time.time()
            now_mono = time.monotonic()
            wall_times = {sym: now_wall - (now_mono - t)
                          for sym, t in self._times.items()}
            with open(CLUSTER_FILE, "w") as f:
                json.dump({"times": wall_times, "prices": self._prices}, f)
        except Exception:
            pass

    def allow(self, symbol: str, price: float) -> Tuple[bool, str]:
        now = time.monotonic()
        ps  = pip_size(symbol)
        if symbol in self._times:
            elapsed = now - self._times[symbol]
            if elapsed < self.MIN_SECONDS:
                return False, f"⏳ {symbol} cluster: {self.MIN_SECONDS-int(elapsed)}s remaining"
        if symbol in self._prices:
            moved = abs(price - self._prices[symbol]) / ps
            if moved < self.MIN_PIPS:
                return False, f"⏳ {symbol} cluster: price moved only {moved:.1f}pip"
        return True, "OK"

    def record(self, symbol: str, price: float):
        self._times[symbol]  = time.monotonic()
        self._prices[symbol] = price
        self._save()

# ─────────────────────────────────────────────────────────────────────────────
#  TRADE EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────

def execute(decision: TradeDecision) -> Optional[int]:
    """Send the order to MT5. Returns ticket on success."""
    sym   = decision.symbol
    info  = mt5.symbol_info(sym)
    if not info:
        print(f"  ⚠️  {sym}: symbol info unavailable")
        return None

    # ── Master Risk Manager gate ─────────────────────────────────────────
    # decision.risk_usd is a dollar amount from this bot's own sizing --
    # convert to %-of-equity (what the MRM speaks in) before asking.
    acc_info = mt5.account_info()
    balance  = float(acc_info.balance) if acc_info else 0.0
    proposed_risk_pct = (decision.risk_usd / balance * 100) if balance else 0.0

    mrm_decision = risk.request_trade(
        symbol=sym,
        direction=decision.action,
        proposed_risk_pct=proposed_risk_pct,
        strategy_tag="v45_7gate",
    )
    if not mrm_decision.approved:
        print(f"  🛑 MRM rejected {sym} {decision.action}: {mrm_decision.reason}")
        return None

    lot = decision.lot
    if mrm_decision.approved_risk_pct != proposed_risk_pct and proposed_risk_pct > 0:
        # MRM resized the trade -- scale the lot proportionally to the approved risk.
        lot = round(max(0.01, decision.lot * (mrm_decision.approved_risk_pct / proposed_risk_pct)) / 0.01) * 0.01

    mt5.symbol_select(sym, True)
    tick  = mt5.symbol_info_tick(sym)
    if not tick:
        risk.reject_fill(mrm_decision.position_id)
        return None

    order_type = mt5.ORDER_TYPE_BUY  if decision.action == "BUY" else mt5.ORDER_TYPE_SELL
    price      = tick.ask            if decision.action == "BUY" else tick.bid
    deviation  = 20

    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       sym,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           round(decision.sl,  info.digits),
        "tp":           round(decision.tp,  info.digits),
        "deviation":    deviation,
        "magic":        450001,
        "comment":      f"V45_{decision.action[:1]}_{decision.confidence}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        risk.confirm_fill(mrm_decision.position_id)
        track_reservation(result.order, mrm_decision.position_id)
        return result.order
    else:
        code = result.retcode if result else "None"
        print(f"  ❌ Order failed: {code}")
        risk.reject_fill(mrm_decision.position_id)
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  HARVEST — close trades that finished while bot was running/offline
# ─────────────────────────────────────────────────────────────────────────────

def harvest(open_trades: Dict[int, OpenTrade], db: Database) -> Dict[int, OpenTrade]:
    """
    Check all open trades against MT5 live positions every cycle.
    Any trade no longer in MT5 positions = closed. Record it properly.

    Also scans last 7 days of MT5 history to catch any deal that closed
    while bot was offline — this is what was causing 0.000R in the database.
    """
    if not open_trades:
        return open_trades

    live_positions = mt5.positions_get() or []
    live_tickets   = {p.ticket for p in live_positions}

    # Fetch recent history once per harvest call
    from_dt  = datetime.now() - timedelta(days=7)
    all_deals = mt5.history_deals_get(from_dt, datetime.now()) or []
    # Index exit deals by order ticket for fast lookup
    exit_deals = {d.order: d for d in all_deals
                  if d.entry == mt5.DEAL_ENTRY_OUT}

    closed_any = False

    for ticket in list(open_trades.keys()):
        if ticket in live_tickets:
            continue   # trade still open

        t    = open_trades[ticket]
        deal = exit_deals.get(ticket)

        if deal is None:
            # Try searching by symbol+time as fallback
            deal = next((d for d in all_deals
                         if d.entry == mt5.DEAL_ENTRY_OUT
                         and d.symbol == t.symbol), None)

        if deal:
            profit    = deal.profit + deal.commission + deal.swap
            exit_p    = deal.price
            entry_p   = t.entry
            sl_dist   = abs(entry_p - t.sl)
            if sl_dist < 1e-8:
                sl_dist = abs(entry_p) * 0.001  # fallback 0.1%
            mult      = 1.0 if t.direction == "BUY" else -1.0
            realized_r = round((exit_p - entry_p) / sl_dist * mult, 3)

            try:
                entry_dt  = datetime.fromisoformat(t.opened_at)
                hold_mins = (datetime.fromtimestamp(deal.time) - entry_dt
                             ).total_seconds() / 60
            except Exception:
                hold_mins = 0.0

            db.close_trade(t.db_id, ticket, exit_p, profit,
                           realized_r, round(hold_mins, 1))

            icon  = "✅" if profit > 0 else "❌"
            r_str = f"{realized_r:+.2f}R"
            print(f"\n   {icon} CLOSED {t.symbol} {t.direction}  "
                  f"Entry:{entry_p:.5f} → Exit:{exit_p:.5f}  "
                  f"${profit:+.2f}  {r_str}  {hold_mins:.0f}min")
        else:
            # Trade not in live positions and no deal found — may have been
            # manually closed or expired. Remove from tracking.
            print(f"\n   ⚠️  {t.symbol} #{ticket} not found in MT5 — removing from tracker")

        del open_trades[ticket]
        closed_any = True

    if closed_any:
        save_positions(open_trades)

    return open_trades

# ─────────────────────────────────────────────────────────────────────────────
#  PERFORMANCE DISPLAY
# ─────────────────────────────────────────────────────────────────────────────

def print_stats(db: Database, balance: float):
    stats = db.get_stats()
    n     = stats["trades"]
    sep   = "─" * 60
    print(f"\n{sep}")
    print(f"  📊 PERFORMANCE  —  Balance: ${balance:,.2f}")
    print(sep)
    if n == 0:
        print("  No closed trades yet.")
    else:
        wr  = stats["win_rate"]
        pf  = stats["profit_factor"]
        exp = stats["expectancy_r"]
        tot = stats["total_profit"]

        def mark(val, good, bad):
            return "✅" if val >= good else ("⚠️ " if val >= bad else "❌")

        print(f"  Trades:          {n}")
        print(f"  Win rate:        {wr:.1f}%   {mark(wr,55,45)}")
        print(f"  Profit factor:   {pf:.2f}    {mark(pf,1.3,1.0)}")
        print(f"  Expectancy:      {exp:+.3f}R {mark(exp,0.3,0.0)}")
        print(f"  Total profit:    ${tot:+.2f}")

        if n >= 20:
            ready = wr >= 55 and pf >= 1.3 and exp >= 0.3
            print(f"\n  FTMO readiness: {'✅ READY' if ready else '⏳ Not yet'}")
    print(sep)

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════════════════════╗
║        BABSBOOKS PROFESSIONAL TRADING BOT  V45 — MECHANICAL EDITION         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  7-Gate Decision Engine  |  SMC + H1 Trend  |  Wide Stops  |  2:1 Min RR  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

def main():
    print(BANNER)

    if not mt5.initialize():
        print("❌ MetaTrader5 failed to initialise"); return

    account = mt5.account_info()
    if not account:
        print("❌ No account info"); return

    print(f"✅ Connected  |  Balance: ${account.balance:,.2f}  |  {account.company}")

    db          = Database()
    cluster     = ClusterGuard()
    open_trades = load_positions()

    # Fix historical 0.000R records from previous sessions
    fixed = db.backfill_realized_r()
    if fixed:
        print(f"  📊 Fixed {fixed} historical R-values — stats now accurate")

    # Recover any trades closed while bot was offline
    db.recover_offline_closes()

    # Show existing stats
    print_stats(db, account.balance)

    cycle      = 0
    today_date = datetime.now().date()
    print(f"\n{'═'*72}")
    print(f"  🚀 TRADING — max {MAX_TRADES_PER_DAY} trades/day  |  min {MIN_RR}:1 RR  |  {RISK_PER_TRADE_PCT}% risk")
    print(f"{'═'*72}")

    try:
        while True:
            cycle += 1
            now   = datetime.now()

            # ── Daily reset ────────────────────────────────────────────────────
            if now.date() != today_date:
                today_date = now.date()
                print(f"\n  📅 New day — counters reset")

            # ── Harvest closed trades ──────────────────────────────────────────
            open_trades = harvest(open_trades, db)
            poll_closed_positions()  # release MRM-reserved exposure for anything that closed

            # ── Session check ──────────────────────────────────────────────────
            session = current_session()
            if session is None:
                next_sess = "London" if datetime.utcnow().hour < 8 else "next day London"
                wait_h    = (8 - datetime.utcnow().hour) % 24
                if cycle % 20 == 1:
                    print(f"  💤 Outside session ({datetime.utcnow().strftime('%H:%M')} UTC) "
                          f"— {next_sess} opens in ~{wait_h}h")
                time.sleep(60); continue

            # ── Daily limits ───────────────────────────────────────────────────
            today_stats = db.get_today_stats()
            if today_stats["trades"] >= MAX_TRADES_PER_DAY:
                if cycle % 10 == 1:
                    print(f"  ⏸️  Daily trade limit ({MAX_TRADES_PER_DAY}) reached")
                time.sleep(60); continue
            if today_stats["losses"] >= MAX_LOSSES_PER_DAY:
                if cycle % 10 == 1:
                    print(f"  🛑 Daily loss limit ({MAX_LOSSES_PER_DAY}) — protecting capital")
                time.sleep(60); continue

            # ── News check ─────────────────────────────────────────────────────
            blocked, news_reason = news_blocked()

            # ── Get account info ───────────────────────────────────────────────
            acct = mt5.account_info()
            if not acct:
                time.sleep(30); continue
            balance = acct.balance

            # ── Cycle header ───────────────────────────────────────────────────
            open_cnt = len(open_trades)
            print(f"\n  🔄 Cycle #{cycle}  {now.strftime('%H:%M:%S')}  {session}  "
                  f"Open:{open_cnt}  Trades:{today_stats['trades']}/{MAX_TRADES_PER_DAY}  "
                  f"Losses:{today_stats['losses']}/{MAX_LOSSES_PER_DAY}")

            if blocked:
                print(f"  📰 {news_reason} — all pairs paused")
                time.sleep(30); continue

            # ── Currency exposure tracking (intra-cycle) ───────────────────────
            # Already open: extract which currencies are exposed
            cycle_exposure: Dict[str, int] = {}
            for ot in open_trades.values():
                b, q = ot.symbol[:3], ot.symbol[3:]
                mult = 1 if ot.direction == "BUY" else -1
                cycle_exposure[b] = cycle_exposure.get(b, 0) + mult
                cycle_exposure[q] = cycle_exposure.get(q, 0) - mult
            cycle_done: set = set()

            # ── Scan symbols ───────────────────────────────────────────────────
            for symbol in SYMBOLS:
                if symbol in cycle_done:
                    continue
                if open_cnt >= MAX_TRADES_PER_DAY:
                    break

                # Read the market
                snap = read_market(symbol, session)
                if snap is None:
                    print(f"    ⚪ {symbol}  no data")
                    continue

                print(f"    🔍 {symbol}  ATR={snap.atr_pct:.3f}%  "
                      f"Vol={snap.volume:.1f}×  H1:{snap.trend_h1}  "
                      f"M5:{snap.structure}  bp={snap.bp:.2f}  "
                      f"pressure={snap.pressure}  RSI={snap.rsi:.0f}")

                # Make the mechanical decision
                dec = decide(snap, balance)

                if dec.action == "SKIP":
                    print(f"    ⏭️  {symbol}  {dec.reason}")
                    continue

                # ── Correlation: no two same-direction same-currency trades ──
                base, quote = symbol[:3], symbol[3:]
                mult  = 1 if dec.action == "BUY" else -1
                new_b = cycle_exposure.get(base, 0) + mult
                new_q = cycle_exposure.get(quote, 0) - mult
                if abs(new_b) > 1 or abs(new_q) > 1:
                    print(f"    ⛔ {symbol}  Currency exposure would exceed limit")
                    continue

                # ── JPY pairs: extra spread check ─────────────────────────────
                if "JPY" in symbol and snap.spread > 2.5:
                    print(f"    ⛔ {symbol}  Spread {snap.spread:.1f}pip > 2.5 (JPY limit)")
                    continue

                # ── Non-JPY spread check ──────────────────────────────────────
                if "JPY" not in symbol and snap.spread > 2.0:
                    print(f"    ⛔ {symbol}  Spread {snap.spread:.1f}pip > 2.0")
                    continue

                # ── Cluster guard ─────────────────────────────────────────────
                ok, msg = cluster.allow(symbol, snap.price)
                if not ok:
                    print(f"    {msg}")
                    continue

                # ── EXECUTE ───────────────────────────────────────────────────
                print(f"\n    🎯 {symbol} {dec.action}  conf={dec.confidence}  "
                      f"SL={dec.sl_pips}pip  TP={dec.tp_pips}pip  "
                      f"RR=1:{dec.rr}  lot={dec.lot}  risk=${dec.risk_usd}")
                print(f"       Reason: {dec.reason}")

                ticket = execute(dec)
                if ticket:
                    ot = OpenTrade(
                        ticket=ticket, symbol=symbol, direction=dec.action,
                        entry=dec.entry, sl=dec.sl, tp=dec.tp, lot=dec.lot,
                        opened_at=datetime.now().isoformat(),
                    )
                    db_id = db.open_trade(ot, snap, dec.sl_pips, dec.rr, dec.confidence)
                    ot.db_id = db_id
                    open_trades[ticket] = ot
                    cluster.record(symbol, snap.price)
                    cycle_done.add(symbol)
                    open_cnt += 1
                    cycle_exposure[base]  = cycle_exposure.get(base, 0) + mult
                    cycle_exposure[quote] = cycle_exposure.get(quote, 0) - mult
                    save_positions(open_trades)

                    print(f"   ✅ Order #{ticket}  {dec.lot} lots  "
                          f"{'█' * int(dec.confidence / 5)}")

            # ── Periodic stats (every 30 cycles = ~15 min) ────────────────────
            if cycle % 30 == 0:
                acct2 = mt5.account_info()
                if acct2:
                    db.record_equity(acct2.balance, acct2.equity,
                                     sum(t.lot for t in open_trades.values()))
                    print_stats(db, acct2.balance)

            time.sleep(30)

    except KeyboardInterrupt:
        print("\n\n  ⏹️  Stopped by user")
    finally:
        acct_f = mt5.account_info()
        if acct_f:
            print_stats(db, acct_f.balance)
        mt5.shutdown()
        print("  👋 MT5 disconnected")


if __name__ == "__main__":
    main()