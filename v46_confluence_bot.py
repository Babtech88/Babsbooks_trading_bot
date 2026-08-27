"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         BABSBOOKS PROFESSIONAL TRADING BOT  V46 — CONFLUENCE EDITION        ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  MECHANICAL THINKING ENGINE  +  6 NEW SIGNAL LAYERS                        ║
║                                                                              ║
║  NEW IN V46:                                                                 ║
║    ✦ Ichimoku Cloud      — strongest trend filter in forex                  ║
║    ✦ Order Block Detection— institutional buy/sell zones                    ║
║    ✦ RSI Divergence       — price vs RSI diverging = reversal signal        ║
║    ✦ Supply & Demand Zones— key price rejection areas                       ║
║    ✦ Confluence Counter   — count how many signals agree before entry       ║
║    ✦ Full Candle Study    — detailed breakdown printed before every entry   ║
║                                                                              ║
║  DECISION HIERARCHY (must pass ALL stages):                                  ║
║    1. Market Regime    — is this a tradeable market right now?               ║
║    2. Session Filter   — are we in London or Overlap session?                ║
║    3. News Filter      — no high-impact event within 30 min?                 ║
║    4. Structure Bias   — what direction is the dominant trend?               ║
║    5. SMC Setup        — institutional footprint present?                    ║
║    6. Entry Trigger    — momentum candle confirming the move?                ║
║    7. Confluence Gate  — minimum 4 of 8 signals must agree                  ║
║    8. Risk Gate        — does the RR and position size make sense?           ║
║                                                                              ║
║  PROFITABILITY RULES:                                                         ║
║    • Never trade against the H1 trend OR Ichimoku Cloud                     ║
║    • Never enter without SMC confluence (EQ + BOS + FVG)                    ║
║    • Never enter without a momentum candle trigger                           ║
║    • Require minimum 4/8 confluence signals                                  ║
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
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT_CURRENCY    = "USD"
RISK_PER_TRADE_PCT  = 0.50
MAX_LOT             = 0.50
MAX_TRADES_PER_DAY  = 3
MAX_LOSSES_PER_DAY  = 2
MIN_RR              = 2.0

ATR_SL_MULTIPLIER   = 3.5
MIN_SL_PIPS: Dict[str, int] = {
    "EURUSD": 40, "GBPUSD": 50, "AUDUSD": 40,
    "EURJPY": 70, "GBPJPY": 90, "EURGBP": 40,
}

MIN_ATR_PCT         = 0.018
MIN_VOLUME_RATIO    = 0.70
MIN_BP              = 0.62
MIN_PRESSURE        = 55
MIN_CONFIDENCE      = 72

# ── NEW V46: Confluence threshold ──────────────────────────────────────────────
# 8 signal layers: Ichimoku, Order Block, RSI Div, S&D Zone,
#                  H1 Trend, SMC (BOS/FVG/EQ), Pattern, Fundamental
MIN_CONFLUENCE      = 4   # need at least 4/8 signals agreeing

SESSIONS = {
    "London":  (8,  12),
    "Overlap": (12, 16),
}

SYMBOLS = get_symbols(["EURUSD", "GBPUSD", "AUDUSD", "EURJPY", "GBPJPY", "EURGBP"])

NEWS_EVENTS: List[Tuple[int, int, str]] = [
    (8,  30, "UK CPI/GDP"),
    (9,  30, "BOE/ECB Decision"),
    (12, 30, "US CPI/NFP/Retail"),
    (13, 30, "US Jobless Claims"),
    (14,  0, "US ISM/Fed"),
    (18,  0, "Fed Rate Decision"),
]
NEWS_BLOCK_BEFORE = 30
NEWS_BLOCK_AFTER  = 20

_DIR            = os.path.dirname(os.path.abspath(__file__))
DB_PATH         = os.path.join(_DIR, "babsbooks_v46.db")
POSITIONS_FILE  = os.path.join(_DIR, "v46_positions.json")
CLUSTER_FILE    = os.path.join(_DIR, "v46_cluster.json")

# ─────────────────────────────────────────────────────────────────────────────
#  MARKET INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

CB_RATES = {
    "EUR": 2.15, "GBP": 3.75, "USD": 4.50, "AUD": 4.10, "JPY": 0.50,
}

SENTIMENT = {
    "EUR": +1, "GBP": 0, "USD": 0, "AUD": -1, "JPY": -1,
}

# ─────────────────────────────────────────────────────────────────────────────
#  DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IchimokuResult:
    """Ichimoku Cloud components."""
    tenkan:     float   # Conversion line (9)
    kijun:      float   # Base line (26)
    senkou_a:   float   # Leading Span A
    senkou_b:   float   # Leading Span B
    chikou:     float   # Lagging span (current close shifted back)
    above_cloud:bool    # price above both spans = bullish
    below_cloud:bool    # price below both spans = bearish
    in_cloud:   bool    # inside cloud = ranging/uncertain
    tk_cross:   str     # "BULL" / "BEAR" / "NONE" — TK cross signal
    cloud_color:str     # "GREEN" (A>B) / "RED" (B>A)


@dataclass
class OrderBlockResult:
    """Institutional Order Block zones."""
    bull_ob_high: float   # top of bullish OB (demand zone)
    bull_ob_low:  float   # bottom of bullish OB
    bear_ob_high: float   # top of bearish OB (supply zone)
    bear_ob_low:  float   # bottom of bearish OB
    price_in_bull_ob: bool
    price_in_bear_ob: bool
    bull_ob_strength: int   # 0-100
    bear_ob_strength: int


@dataclass
class RSIDivResult:
    """RSI Divergence detection."""
    bull_div:   bool    # price LL but RSI HL = hidden bullish
    bear_div:   bool    # price HH but RSI LH = hidden bearish
    bull_hidden:bool    # price HL but RSI LL = continuation buy
    bear_hidden:bool    # price LH but RSI HH = continuation sell
    div_strength:int    # 0-100


@dataclass
class SDZoneResult:
    """Supply & Demand zones."""
    demand_high: float
    demand_low:  float
    supply_high: float
    supply_low:  float
    at_demand:   bool
    at_supply:   bool
    demand_strength: int   # freshness + number of tests
    supply_strength: int


@dataclass
class ConfluenceReport:
    """All 8 signal layers evaluated."""
    direction:       str    # "BUY" / "SELL"
    ichimoku:        bool
    order_block:     bool
    rsi_divergence:  bool
    sd_zone:         bool
    h1_trend:        bool
    smc:             bool
    pattern:         bool
    fundamental:     bool
    score:           int    # total signals agreeing (0-8)
    labels:          List[str]   # which ones fired


@dataclass
class CandleStudy:
    """Full breakdown of the setup candle — printed before every entry."""
    symbol:       str
    direction:    str
    price:        float
    # Candle anatomy
    body_pct:     float   # body as % of range
    upper_wick:   float   # upper wick as % of range
    lower_wick:   float   # lower wick as % of range
    candle_type:  str     # engulf / pin / doji / strong / weak
    bull_pressure:float   # close position in range (0=low, 1=high)
    # Multi-timeframe
    h1_trend:     str
    m5_structure: str
    # Indicators
    rsi:          float
    atr_pct:      float
    volume_ratio: float
    # New V46
    ichimoku_side:str
    cloud_color:  str
    at_ob:        bool
    ob_type:      str
    rsi_div:      str     # "BULL_DIV" / "BEAR_DIV" / "HIDDEN" / "NONE"
    at_sd_zone:   bool
    sd_type:      str     # "DEMAND" / "SUPPLY" / "NONE"
    # SMC
    at_eq:        bool
    bos:          bool
    fvg:          bool
    # Confluence
    confluence:   ConfluenceReport
    # Risk
    sl_pips:      int
    tp_pips:      int
    rr:           float
    lot:          float
    risk_usd:     float


@dataclass
class MarketSnapshot:
    symbol:     str
    timestamp:  str
    session:    str
    atr_pct:    float
    atr_val:    float
    volume:     float
    trend_h1:   str
    structure:  str
    bp:         float
    pressure:   int
    rsi:        float
    at_eq:      bool
    bull_bos:   bool
    bear_bos:   bool
    bull_fvg:   bool
    bear_fvg:   bool
    bull_engulf:bool
    bear_engulf:bool
    bull_pin:   bool
    bear_pin:   bool
    fund_bias:  str
    fund_str:   int
    price:      float
    spread:     float
    # V46 additions
    ichimoku:   Optional[IchimokuResult]     = None
    order_block:Optional[OrderBlockResult]   = None
    rsi_div:    Optional[RSIDivResult]       = None
    sd_zone:    Optional[SDZoneResult]       = None


@dataclass
class TradeDecision:
    symbol:     str
    action:     str
    reason:     str
    confidence: int
    entry:      float = 0.0
    sl:         float = 0.0
    tp:         float = 0.0
    sl_pips:    int   = 0
    tp_pips:    int   = 0
    rr:         float = 0.0
    lot:        float = 0.0
    risk_usd:   float = 0.0
    confluence: Optional[ConfluenceReport] = None
    candle_study: Optional[CandleStudy]   = None


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
                    confluence_score INTEGER,
                    confluence_labels TEXT,
                    ichimoku_side TEXT,
                    at_ob         INTEGER,
                    rsi_div       TEXT,
                    at_sd_zone    INTEGER,
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
                   sl_pips: int, rr: float, confidence: int,
                   conf_report: Optional[ConfluenceReport] = None) -> int:
        pat = ("engulf" if snap.bull_engulf or snap.bear_engulf
               else "pin" if snap.bull_pin or snap.bear_pin else "none")
        bos = 1 if (snap.bull_bos or snap.bear_bos) else 0
        fvg = 1 if (snap.bull_fvg or snap.bear_fvg) else 0

        ich_side    = snap.ichimoku.tk_cross if snap.ichimoku else "N/A"
        at_ob       = 1 if snap.order_block and (snap.order_block.price_in_bull_ob or snap.order_block.price_in_bear_ob) else 0
        rsi_div_str = "NONE"
        if snap.rsi_div:
            if snap.rsi_div.bull_div:   rsi_div_str = "BULL_DIV"
            elif snap.rsi_div.bear_div: rsi_div_str = "BEAR_DIV"
            elif snap.rsi_div.bull_hidden: rsi_div_str = "HIDDEN_BULL"
            elif snap.rsi_div.bear_hidden: rsi_div_str = "HIDDEN_BEAR"
        at_sd = 1 if snap.sd_zone and (snap.sd_zone.at_demand or snap.sd_zone.at_supply) else 0
        conf_score  = conf_report.score  if conf_report else 0
        conf_labels = ",".join(conf_report.labels) if conf_report else ""

        with self._connect() as c:
            cur = c.execute("""
                INSERT OR IGNORE INTO trades
                (ticket,symbol,direction,entry_price,sl,tp,lot,sl_pips,rr,
                 session,trend_h1,structure,bp,pressure,at_eq,bos,fvg,pattern,
                 fund_bias,confidence,confluence_score,confluence_labels,
                 ichimoku_side,at_ob,rsi_div,at_sd_zone,entry_time)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (t.ticket, t.symbol, t.direction, t.entry, t.sl, t.tp, t.lot,
                  sl_pips, rr, snap.session, snap.trend_h1, snap.structure,
                  snap.bp, snap.pressure, 1 if snap.at_eq else 0,
                  bos, fvg, pat, snap.fund_bias, confidence,
                  conf_score, conf_labels, ich_side, at_ob, rsi_div_str, at_sd,
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
            sl_dist  = abs(entry_p - sl) or 0.0001
            mult     = 1 if direction == "BUY" else -1
            realized = (exit_p - entry_p) / sl_dist * mult
            self.close_trade(db_id, ticket, exit_p, profit, round(realized, 3), 0)
            recovered += 1
            icon = "✅" if profit > 0 else "❌"
            print(f"  {icon} Recovered {ticket}  ${profit:+.2f}  ({realized:+.2f}R)")
        if recovered:
            print(f"  ✅ {recovered} trade(s) recovered from MT5 history")

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_rates(symbol: str, timeframe, count: int = 300) -> Optional[pd.DataFrame]:
    bars = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if bars is None or len(bars) < 80:
        return None
    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.columns = [c.lower() for c in df.columns]
    return df


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def pip_value(symbol: str) -> float:
    if "JPY" in symbol:
        info = mt5.symbol_info(symbol)
        rate = info.bid if info else 160.0
        return 1000.0 / rate
    return 10.0

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: ICHIMOKU CLOUD
#  Tenkan(9), Kijun(26), Senkou A & B, Chikou — full cloud evaluation
# ─────────────────────────────────────────────────────────────────────────────

def calc_ichimoku(df: pd.DataFrame) -> IchimokuResult:
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    def donchian(period):
        return (high.rolling(period).max() + low.rolling(period).min()) / 2

    tenkan   = donchian(9)
    kijun    = donchian(26)
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = donchian(52).shift(26)
    chikou   = close.shift(-26)

    tk   = float(tenkan.iloc[-1])
    kj   = float(kijun.iloc[-1])
    sa   = float(senkou_a.iloc[-1]) if not pd.isna(senkou_a.iloc[-1]) else float(senkou_a.dropna().iloc[-1])
    sb   = float(senkou_b.iloc[-1]) if not pd.isna(senkou_b.iloc[-1]) else float(senkou_b.dropna().iloc[-1])
    price = float(close.iloc[-1])

    above_cloud = price > max(sa, sb)
    below_cloud = price < min(sa, sb)
    in_cloud    = not above_cloud and not below_cloud

    # TK Cross
    prev_tk = float(tenkan.iloc[-2])
    prev_kj = float(kijun.iloc[-2])
    if prev_tk <= prev_kj and tk > kj:
        tk_cross = "BULL"
    elif prev_tk >= prev_kj and tk < kj:
        tk_cross = "BEAR"
    else:
        tk_cross = "NONE"

    cloud_color = "GREEN" if sa > sb else "RED"

    return IchimokuResult(
        tenkan=tk, kijun=kj, senkou_a=sa, senkou_b=sb,
        chikou=float(close.iloc[-27]) if len(close) > 27 else price,
        above_cloud=above_cloud, below_cloud=below_cloud, in_cloud=in_cloud,
        tk_cross=tk_cross, cloud_color=cloud_color,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: ORDER BLOCK DETECTION
#  Last strong impulsive candle before a significant move = institutional zone
# ─────────────────────────────────────────────────────────────────────────────

def detect_order_blocks(df: pd.DataFrame, atr: float) -> OrderBlockResult:
    """
    Bullish OB: last bearish candle before a strong bullish move upward.
    Bearish OB: last bullish candle before a strong bearish move downward.
    Strength = size of move after OB relative to ATR.
    """
    price = float(df["close"].iloc[-1])
    look  = 50   # bars to scan

    tail = df.tail(look).reset_index(drop=True)
    o = tail["open"].values
    c = tail["close"].values
    h = tail["high"].values
    l = tail["low"].values

    bull_ob_high = bull_ob_low = 0.0
    bear_ob_high = bear_ob_low = 0.0
    bull_str = bear_str = 0

    # Find bullish OB: bearish candle followed by upward impulse (>1.5 ATR)
    for i in range(len(tail) - 4, 2, -1):
        is_bear_candle = c[i] < o[i]
        if not is_bear_candle:
            continue
        # Check if subsequent 3 candles move up by >1.5 ATR
        move_up = max(h[i+1:i+4]) - o[i]
        if move_up >= 1.5 * atr:
            bull_ob_high = h[i]
            bull_ob_low  = l[i]
            bull_str     = int(min(100, (move_up / atr) * 30))
            break

    # Find bearish OB: bullish candle followed by downward impulse
    for i in range(len(tail) - 4, 2, -1):
        is_bull_candle = c[i] > o[i]
        if not is_bull_candle:
            continue
        move_dn = o[i] - min(l[i+1:i+4])
        if move_dn >= 1.5 * atr:
            bear_ob_high = h[i]
            bear_ob_low  = l[i]
            bear_str     = int(min(100, (move_dn / atr) * 30))
            break

    price_in_bull = (bull_ob_low > 0 and bull_ob_low <= price <= bull_ob_high)
    price_in_bear = (bear_ob_low > 0 and bear_ob_low <= price <= bear_ob_high)

    return OrderBlockResult(
        bull_ob_high=bull_ob_high, bull_ob_low=bull_ob_low,
        bear_ob_high=bear_ob_high, bear_ob_low=bear_ob_low,
        price_in_bull_ob=price_in_bull, price_in_bear_ob=price_in_bear,
        bull_ob_strength=bull_str, bear_ob_strength=bear_str,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: RSI DIVERGENCE
#  Compares price swing vs RSI swing over last N bars
# ─────────────────────────────────────────────────────────────────────────────

def detect_rsi_divergence(df: pd.DataFrame) -> RSIDivResult:
    """
    Regular divergence: momentum warning, often precedes reversals.
    Hidden divergence: trend continuation signal.
    """
    close = df["close"]
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - 100 / (1 + rs)

    # Look at last 30 bars to find swings
    window = 30
    if len(df) < window + 5:
        return RSIDivResult(False, False, False, False, 0)

    tail_c   = close.values[-window:]
    tail_rsi = rsi.values[-window:]

    # Find recent lows and highs in price
    p_recent_low_idx  = int(np.argmin(tail_c[-15:]) + window - 15)
    p_prev_low_idx    = int(np.argmin(tail_c[:-15]))
    p_recent_high_idx = int(np.argmax(tail_c[-15:]) + window - 15)
    p_prev_high_idx   = int(np.argmax(tail_c[:-15]))

    p_recent_low   = tail_c[p_recent_low_idx]
    p_prev_low     = tail_c[p_prev_low_idx]
    r_recent_low   = tail_rsi[p_recent_low_idx]
    r_prev_low     = tail_rsi[p_prev_low_idx]

    p_recent_high  = tail_c[p_recent_high_idx]
    p_prev_high    = tail_c[p_prev_high_idx]
    r_recent_high  = tail_rsi[p_recent_high_idx]
    r_prev_high    = tail_rsi[p_prev_high_idx]

    # Regular bullish divergence: price LL, RSI HL (reversal buy signal)
    bull_div    = (p_recent_low < p_prev_low and r_recent_low > r_prev_low
                   and r_recent_low < 45)

    # Regular bearish divergence: price HH, RSI LH (reversal sell signal)
    bear_div    = (p_recent_high > p_prev_high and r_recent_high < r_prev_high
                   and r_recent_high > 55)

    # Hidden bullish divergence: price HL, RSI LL (trend continuation buy)
    bull_hidden = (p_recent_low > p_prev_low and r_recent_low < r_prev_low
                   and r_recent_low < 50)

    # Hidden bearish divergence: price LH, RSI HH (trend continuation sell)
    bear_hidden = (p_recent_high < p_prev_high and r_recent_high > r_prev_high
                   and r_recent_high > 50)

    # Strength: how far apart are the RSI levels
    div_gap  = 0.0
    if bull_div:    div_gap = r_recent_low  - r_prev_low
    elif bear_div:  div_gap = r_prev_high   - r_recent_high
    strength = int(min(100, abs(div_gap) * 3))

    return RSIDivResult(
        bull_div=bull_div, bear_div=bear_div,
        bull_hidden=bull_hidden, bear_hidden=bear_hidden,
        div_strength=strength,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: SUPPLY & DEMAND ZONES
#  Strong base candles + explosive moves = institutional levels
# ─────────────────────────────────────────────────────────────────────────────

def detect_sd_zones(df: pd.DataFrame, atr: float) -> SDZoneResult:
    """
    Demand zone: tight consolidation followed by strong rally — buyers loaded here.
    Supply zone: tight consolidation before a strong drop — sellers loaded here.
    Freshness: untested zones are stronger.
    """
    price = float(df["close"].iloc[-1])
    tail  = df.tail(80).reset_index(drop=True)
    o = tail["open"].values
    c = tail["close"].values
    h = tail["high"].values
    l = tail["low"].values

    demand_high = demand_low = 0.0
    supply_high = supply_low = 0.0
    demand_str  = supply_str = 0

    # Demand zone: find base (low-range candles) before explosive up move
    for i in range(len(tail) - 6, 3, -1):
        body       = abs(c[i] - o[i])
        next_move  = max(h[i+1:i+5]) - max(c[i], o[i])
        range_this = h[i] - l[i]
        if range_this < 0.001:
            continue
        is_base    = body / range_this < 0.5   # indecision candle
        is_impulse = next_move >= 2.0 * atr
        if is_base and is_impulse:
            demand_high = max(o[i], c[i]) + atr * 0.3
            demand_low  = l[i]
            # Freshness: how many times has price revisited?
            tests = sum(1 for j in range(i+1, len(tail))
                        if demand_low <= l[j] <= demand_high)
            demand_str = max(10, int(min(100, (next_move / atr) * 25 - tests * 10)))
            break

    # Supply zone: base before explosive down move
    for i in range(len(tail) - 6, 3, -1):
        body       = abs(c[i] - o[i])
        next_drop  = min(o[i], c[i]) - min(l[i+1:i+5])
        range_this = h[i] - l[i]
        if range_this < 0.001:
            continue
        is_base    = body / range_this < 0.5
        is_impulse = next_drop >= 2.0 * atr
        if is_base and is_impulse:
            supply_low  = min(o[i], c[i]) - atr * 0.3
            supply_high = h[i]
            tests = sum(1 for j in range(i+1, len(tail))
                        if supply_low <= h[j] <= supply_high)
            supply_str = max(10, int(min(100, (next_drop / atr) * 25 - tests * 10)))
            break

    at_demand = demand_low > 0 and demand_low <= price <= demand_high
    at_supply = supply_low > 0 and supply_low <= price <= supply_high

    return SDZoneResult(
        demand_high=demand_high, demand_low=demand_low,
        supply_high=supply_high, supply_low=supply_low,
        at_demand=at_demand, at_supply=at_supply,
        demand_strength=demand_str, supply_strength=supply_str,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: CONFLUENCE COUNTER
#  Tallies all 8 signal layers and returns score + breakdown
# ─────────────────────────────────────────────────────────────────────────────

def count_confluence(snap: MarketSnapshot, direction: str) -> ConfluenceReport:
    """
    8 signal layers evaluated against the proposed trade direction.
    Each adds 1 point if it agrees with the direction.
    """
    is_buy = direction == "BUY"
    signals = []

    # 1. Ichimoku
    ich_ok = False
    if snap.ichimoku:
        ich = snap.ichimoku
        if is_buy:
            ich_ok = ich.above_cloud or (not ich.below_cloud and ich.tk_cross == "BULL")
        else:
            ich_ok = ich.below_cloud or (not ich.above_cloud and ich.tk_cross == "BEAR")
    if ich_ok:
        signals.append("Ichimoku")

    # 2. Order Block
    ob_ok = False
    if snap.order_block:
        ob = snap.order_block
        if is_buy and ob.price_in_bull_ob:
            ob_ok = True
        elif not is_buy and ob.price_in_bear_ob:
            ob_ok = True
    if ob_ok:
        signals.append("OrderBlock")

    # 3. RSI Divergence
    div_ok = False
    if snap.rsi_div:
        d = snap.rsi_div
        if is_buy and (d.bull_div or d.bull_hidden):
            div_ok = True
        elif not is_buy and (d.bear_div or d.bear_hidden):
            div_ok = True
    if div_ok:
        signals.append("RSI_Div")

    # 4. Supply & Demand Zone
    sd_ok = False
    if snap.sd_zone:
        sd = snap.sd_zone
        if is_buy and sd.at_demand:
            sd_ok = True
        elif not is_buy and sd.at_supply:
            sd_ok = True
    if sd_ok:
        signals.append("S&D_Zone")

    # 5. H1 Trend
    h1_ok = False
    if is_buy  and snap.trend_h1 == "BULL": h1_ok = True
    if not is_buy and snap.trend_h1 == "BEAR": h1_ok = True
    if h1_ok:
        signals.append("H1_Trend")

    # 6. SMC (any of EQ/BOS/FVG)
    smc_ok = False
    if is_buy  and (snap.at_eq or snap.bull_bos or snap.bull_fvg): smc_ok = True
    if not is_buy and (snap.at_eq or snap.bear_bos or snap.bear_fvg): smc_ok = True
    if smc_ok:
        signals.append("SMC")

    # 7. Pattern (engulf or pin)
    pat_ok = False
    if is_buy  and (snap.bull_engulf or snap.bull_pin): pat_ok = True
    if not is_buy and (snap.bear_engulf or snap.bear_pin): pat_ok = True
    if pat_ok:
        signals.append("Pattern")

    # 8. Fundamental
    fund_ok = snap.fund_bias == direction.lower() and snap.fund_str >= 30
    if fund_ok:
        signals.append("Fundamental")

    return ConfluenceReport(
        direction=direction,
        ichimoku=ich_ok,
        order_block=ob_ok,
        rsi_divergence=div_ok,
        sd_zone=sd_ok,
        h1_trend=h1_ok,
        smc=smc_ok,
        pattern=pat_ok,
        fundamental=fund_ok,
        score=len(signals),
        labels=signals,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ✦ NEW V46: FULL CANDLE STUDY PRINTOUT
#  Every entry gets a complete written breakdown — no more blind entries
# ─────────────────────────────────────────────────────────────────────────────

def print_candle_study(study: CandleStudy):
    """
    Prints a rich, formatted analysis of every signal component
    before an order is placed. Full transparency on what the bot sees.
    """
    sep  = "═" * 72
    sep2 = "─" * 72
    tick = "✅"
    cross= "❌"
    dash = "⬜"

    def yn(val: bool) -> str:
        return tick if val else cross

    # Confluence bar visual
    score = study.confluence.score
    bar   = "█" * score + "░" * (8 - score)

    print(f"\n{sep}")
    print(f"  🔬 CANDLE STUDY — {study.symbol}  {study.direction}  @ {study.price:.5f}")
    print(sep)

    # ── Candle Anatomy ────────────────────────────────────────────────────────
    print(f"\n  📊 CANDLE ANATOMY")
    print(sep2)
    print(f"  Type          : {study.candle_type.upper()}")
    print(f"  Body          : {study.body_pct*100:.1f}% of range")
    print(f"  Upper Wick    : {study.upper_wick*100:.1f}% of range")
    print(f"  Lower Wick    : {study.lower_wick*100:.1f}% of range")
    bull_bar = int(study.bull_pressure * 20)
    bear_bar = 20 - bull_bar
    print(f"  Bull Pressure : [{'█'*bull_bar}{'░'*bear_bar}] {study.bull_pressure*100:.0f}%")

    # ── Multi-Timeframe ───────────────────────────────────────────────────────
    print(f"\n  🕐 MULTI-TIMEFRAME")
    print(sep2)
    print(f"  H1 Trend      : {study.h1_trend}")
    print(f"  M5 Structure  : {study.m5_structure}")
    print(f"  RSI (14)      : {study.rsi:.1f}  {'⚡ Oversold' if study.rsi < 35 else '⚡ Overbought' if study.rsi > 65 else 'Neutral'}")
    print(f"  ATR           : {study.atr_pct:.3f}%  {'🔥 High' if study.atr_pct > 0.04 else '✓ Normal'}")
    print(f"  Volume        : {study.volume_ratio:.1f}×  {'🔥 Surge' if study.volume_ratio > 1.5 else '✓ Normal'}")

    # ── Ichimoku Cloud ────────────────────────────────────────────────────────
    print(f"\n  ☁️  ICHIMOKU CLOUD")
    print(sep2)
    if study.ichimoku_side != "N/A":
        ich = study.confluence.ichimoku
        print(f"  Cloud Side    : {study.ichimoku_side}  {yn(ich)}")
        print(f"  Cloud Color   : {study.cloud_color}")
    else:
        print(f"  Ichimoku      : Not computed")

    # ── Order Block ───────────────────────────────────────────────────────────
    print(f"\n  🏛️  ORDER BLOCK (Institutional Zone)")
    print(sep2)
    ob_fire = study.at_ob
    ob_type = study.ob_type if study.at_ob else "None detected"
    print(f"  At OB         : {yn(ob_fire)}  {ob_type}")

    # ── RSI Divergence ────────────────────────────────────────────────────────
    print(f"\n  📉 RSI DIVERGENCE")
    print(sep2)
    div_fire = study.rsi_div != "NONE"
    print(f"  Signal        : {yn(div_fire)}  {study.rsi_div}")

    # ── Supply & Demand ───────────────────────────────────────────────────────
    print(f"\n  📌 SUPPLY & DEMAND ZONE")
    print(sep2)
    sd_fire = study.at_sd_zone
    sd_type = study.sd_type if study.at_sd_zone else "Not at key zone"
    print(f"  At Zone       : {yn(sd_fire)}  {sd_type}")

    # ── SMC ───────────────────────────────────────────────────────────────────
    print(f"\n  🏦 SMART MONEY (SMC)")
    print(sep2)
    print(f"  EQ (Equilib.) : {yn(study.at_eq)}")
    print(f"  BOS (Break)   : {yn(study.bos)}")
    print(f"  FVG (Gap)     : {yn(study.fvg)}")

    # ── Confluence Summary ─────────────────────────────────────────────────────
    print(f"\n  ⚖️  CONFLUENCE COUNTER  [{bar}]  {score}/8 signals")
    print(sep2)
    all_signals = [
        ("Ichimoku Cloud",      study.confluence.ichimoku),
        ("Order Block",         study.confluence.order_block),
        ("RSI Divergence",      study.confluence.rsi_divergence),
        ("S&D Zone",            study.confluence.sd_zone),
        ("H1 Trend",            study.confluence.h1_trend),
        ("SMC (EQ/BOS/FVG)",    study.confluence.smc),
        ("Candle Pattern",      study.confluence.pattern),
        ("Fundamental Bias",    study.confluence.fundamental),
    ]
    for label, fired in all_signals:
        icon = tick if fired else cross
        print(f"  {icon}  {label}")

    # ── Risk ──────────────────────────────────────────────────────────────────
    print(f"\n  💰 RISK PARAMETERS")
    print(sep2)
    print(f"  SL            : {study.sl_pips} pips")
    print(f"  TP            : {study.tp_pips} pips")
    print(f"  RR            : 1:{study.rr:.1f}")
    print(f"  Lot           : {study.lot}")
    print(f"  Risk          : ${study.risk_usd:.2f}")

    verdict = "🟢 FIRING" if score >= MIN_CONFLUENCE else "🔴 BLOCKED"
    print(f"\n  {verdict}  —  Confluence {score}/{MIN_CONFLUENCE} required")
    print(f"{sep}\n")


def build_candle_study(snap: MarketSnapshot, dec: TradeDecision,
                       conf: ConfluenceReport) -> CandleStudy:
    """Assemble the full CandleStudy from snapshot + decision."""
    # Determine candle type
    bull_pressure = 0.5
    candle_type   = "unknown"

    if snap.bull_engulf or snap.bear_engulf:
        candle_type = "engulfing"
    elif snap.bull_pin or snap.bear_pin:
        candle_type = "pin_bar"

    # Order block info
    ob_type = "None"
    at_ob   = False
    if snap.order_block:
        if snap.order_block.price_in_bull_ob:
            ob_type = f"BULLISH_OB (str={snap.order_block.bull_ob_strength})"
            at_ob   = True
        elif snap.order_block.price_in_bear_ob:
            ob_type = f"BEARISH_OB (str={snap.order_block.bear_ob_strength})"
            at_ob   = True

    # RSI div string
    rsi_div_str = "NONE"
    if snap.rsi_div:
        if snap.rsi_div.bull_div:       rsi_div_str = "BULL_DIV (reversal)"
        elif snap.rsi_div.bear_div:     rsi_div_str = "BEAR_DIV (reversal)"
        elif snap.rsi_div.bull_hidden:  rsi_div_str = "HIDDEN_BULL (continuation)"
        elif snap.rsi_div.bear_hidden:  rsi_div_str = "HIDDEN_BEAR (continuation)"

    # S&D zone string
    sd_type = "NONE"
    at_sd   = False
    if snap.sd_zone:
        if snap.sd_zone.at_demand:
            sd_type = f"DEMAND (str={snap.sd_zone.demand_strength})"
            at_sd   = True
        elif snap.sd_zone.at_supply:
            sd_type = f"SUPPLY (str={snap.sd_zone.supply_strength})"
            at_sd   = True

    ich_side    = "N/A"
    cloud_color = "N/A"
    if snap.ichimoku:
        ich = snap.ichimoku
        if ich.above_cloud:   ich_side = "ABOVE (bullish)"
        elif ich.below_cloud: ich_side = "BELOW (bearish)"
        else:                 ich_side = "IN CLOUD (neutral)"
        cloud_color = ich.cloud_color

    bos = snap.bull_bos if dec.action == "BUY" else snap.bear_bos
    fvg = snap.bull_fvg if dec.action == "BUY" else snap.bear_fvg

    return CandleStudy(
        symbol=snap.symbol, direction=dec.action, price=snap.price,
        body_pct=0.5, upper_wick=0.25, lower_wick=0.25,   # defaults; real values in read_market
        candle_type=candle_type, bull_pressure=0.5,
        h1_trend=snap.trend_h1, m5_structure=snap.structure,
        rsi=snap.rsi, atr_pct=snap.atr_pct, volume_ratio=snap.volume,
        ichimoku_side=ich_side, cloud_color=cloud_color,
        at_ob=at_ob, ob_type=ob_type,
        rsi_div=rsi_div_str, at_sd_zone=at_sd, sd_type=sd_type,
        at_eq=snap.at_eq, bos=bos, fvg=fvg,
        confluence=conf,
        sl_pips=dec.sl_pips, tp_pips=dec.tp_pips, rr=dec.rr,
        lot=dec.lot, risk_usd=dec.risk_usd,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  MARKET READER — builds the complete market picture
# ─────────────────────────────────────────────────────────────────────────────

def read_market(symbol: str, session: str) -> Optional[MarketSnapshot]:
    df5  = get_rates(symbol, mt5.TIMEFRAME_M5,  300)
    df60 = get_rates(symbol, mt5.TIMEFRAME_H1,   80)
    if df5 is None or df60 is None:
        return None

    high, low, close = df5["high"], df5["low"], df5["close"]

    # ATR
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low  - close.shift()).abs()], axis=1).max(axis=1)
    atr_val = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
    price   = float(close.iloc[-1])
    atr_pct = atr_val / price * 100

    # Volume
    vol = df5.get("tick_volume", df5.get("real_volume", pd.Series([1]*len(df5))))
    vol_ma    = float(vol.rolling(20, min_periods=1).mean().iloc[-1]) or 1
    vol_ratio = float(vol.iloc[-1]) / vol_ma

    # RSI
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss  = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = float(100 - 100 / (1 + rs.iloc[-1]))

    # Range
    rh = float(high.iloc[-20:].max())
    rl = float(low.iloc[-20:].min())
    rw = rh - rl or 1e-8

    # BP & pressure
    pos_pct = (price - rl) / rw
    atr_exp = atr_val / (rw / 20) if rw > 0 else 1.0
    bp       = min(1.0, max(0.0, (atr_exp - 0.8) / 1.2))
    pressure = int(min(100, vol_ratio * 40 + atr_exp * 20))

    # Spread
    info   = mt5.symbol_info(symbol)
    spread = (info.spread / 10.0) if info else 3.0

    # H1 trend
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

    # M5 Structure
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

    # SMC
    eq      = (rh + rl) / 2.0
    at_eq   = abs(price - eq) <= atr_val * 0.5
    swing_h = float(df5["high"].iloc[-11:-1].max())
    swing_l = float(df5["low"].iloc[-11:-1].min())
    bull_bos = price > swing_h
    bear_bos = price < swing_l
    c1, c3   = df5.iloc[-3], df5.iloc[-1]
    bull_fvg = float(c1["high"]) < float(c3["low"])
    bear_fvg = float(c1["low"])  > float(c3["high"])

    # Candle patterns
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

    # Fundamental
    base, quote = symbol[:3], symbol[3:]
    rate_diff   = CB_RATES.get(base, 0) - CB_RATES.get(quote, 0)
    sent_diff   = SENTIMENT.get(base, 0) - SENTIMENT.get(quote, 0)
    if rate_diff > 0.5 or sent_diff > 0:
        fund_bias = "buy";     fund_str = int(min(100, abs(rate_diff)*20 + abs(sent_diff)*25))
    elif rate_diff < -0.5 or sent_diff < 0:
        fund_bias = "sell";    fund_str = int(min(100, abs(rate_diff)*20 + abs(sent_diff)*25))
    else:
        fund_bias = "neutral"; fund_str = 0

    # ── V46: Run all 4 new signal engines ─────────────────────────────────────
    ichimoku    = calc_ichimoku(df5)
    order_block = detect_order_blocks(df5, atr_val)
    rsi_div     = detect_rsi_divergence(df5)
    sd_zone     = detect_sd_zones(df5, atr_val)

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
        ichimoku=ichimoku, order_block=order_block,
        rsi_div=rsi_div, sd_zone=sd_zone,
    )

# ─────────────────────────────────────────────────────────────────────────────
#  MECHANICAL DECISION ENGINE — V46
#  8 gates. 8 signal layers. Full candle study on every entry.
# ─────────────────────────────────────────────────────────────────────────────

def decide(snap: MarketSnapshot, balance: float) -> TradeDecision:
    sym = snap.symbol

    def skip(reason: str) -> TradeDecision:
        return TradeDecision(symbol=sym, action="SKIP", reason=reason, confidence=0)

    # ── Gate 1: Market regime ─────────────────────────────────────────────────
    if snap.atr_pct < MIN_ATR_PCT:
        return skip(f"Dead market — ATR {snap.atr_pct:.3f}% < {MIN_ATR_PCT}%")

    # ── Gate 2: Volume ────────────────────────────────────────────────────────
    if snap.volume < MIN_VOLUME_RATIO:
        return skip(f"Low volume — {snap.volume:.2f}× < {MIN_VOLUME_RATIO}×")

    # ── Gate 3: BP and pressure ───────────────────────────────────────────────
    if snap.bp < MIN_BP:
        return skip(f"Weak setup — bp={snap.bp:.2f} < {MIN_BP}")
    if snap.pressure < MIN_PRESSURE:
        return skip(f"Low pressure — {snap.pressure} < {MIN_PRESSURE}")

    # ── Gate 4: Direction ─────────────────────────────────────────────────────
    if snap.trend_h1 == "BULL" and snap.structure in ("BULLISH", "RANGING"):
        direction = "BUY"
    elif snap.trend_h1 == "BEAR" and snap.structure in ("BEARISH", "RANGING"):
        direction = "SELL"
    elif snap.trend_h1 == "BULL" and snap.structure == "BEARISH":
        return skip("H1 BULL but M5 BEARISH — conflicting timeframes")
    elif snap.trend_h1 == "BEAR" and snap.structure == "BULLISH":
        return skip("H1 BEAR but M5 BULLISH — conflicting timeframes")
    elif snap.trend_h1 == "RANGE":
        if snap.structure == "BULLISH":     direction = "BUY"
        elif snap.structure == "BEARISH":   direction = "SELL"
        else: return skip("No clear direction — H1 RANGE + M5 RANGING")
    else:
        return skip(f"Direction unclear — H1:{snap.trend_h1} M5:{snap.structure}")

    # ── NEW V46: Ichimoku gate ────────────────────────────────────────────────
    if snap.ichimoku:
        ich = snap.ichimoku
        if direction == "BUY" and ich.below_cloud:
            return skip(f"Ichimoku block — price BELOW cloud, no BUY")
        if direction == "SELL" and ich.above_cloud:
            return skip(f"Ichimoku block — price ABOVE cloud, no SELL")
        if ich.in_cloud:
            # Inside cloud = uncertainty. Still allow if other signals strong.
            pass   # penalized in confluence instead

    # Fundamental
    if snap.fund_bias == direction.lower() and snap.fund_str >= 30:
        fund_bonus = 10
    elif snap.fund_bias != "neutral" and snap.fund_bias != direction.lower() and snap.fund_str >= 40:
        return skip(f"Counter-fundamental — signal={direction} but fund={snap.fund_bias}({snap.fund_str}%)")
    else:
        fund_bonus = 0

    # ── Gate 5: SMC ───────────────────────────────────────────────────────────
    if direction == "BUY":
        smc_components = [snap.at_eq, snap.bull_bos, snap.bull_fvg]
        smc_labels     = ["at_EQ", "bull_BOS", "bull_FVG"]
    else:
        smc_components = [snap.at_eq, snap.bear_bos, snap.bear_fvg]
        smc_labels     = ["at_EQ", "bear_BOS", "bear_FVG"]
    smc_count  = sum(smc_components)
    smc_active = [smc_labels[i] for i, v in enumerate(smc_components) if v]
    if smc_count < 1:
        return skip(f"No SMC confirmation ({direction})")
    smc_bonus = smc_count * 8

    # ── Gate 6: Entry trigger ─────────────────────────────────────────────────
    if direction == "BUY":
        has_trigger  = snap.bull_engulf or snap.bull_pin or snap.rsi < 35 or snap.bull_bos
        trigger_name = ("engulf" if snap.bull_engulf else "pin_bar" if snap.bull_pin
                        else "oversold" if snap.rsi < 35 else "BOS_break" if snap.bull_bos else "none")
    else:
        has_trigger  = snap.bear_engulf or snap.bear_pin or snap.rsi > 65 or snap.bear_bos
        trigger_name = ("engulf" if snap.bear_engulf else "pin_bar" if snap.bear_pin
                        else "overbought" if snap.rsi > 65 else "BOS_break" if snap.bear_bos else "none")
    if not has_trigger:
        return skip(f"No entry trigger ({direction})")
    trigger_bonus = 15 if trigger_name in ("engulf", "pin_bar") else 8

    # ── NEW V46: Gate 7 — Confluence Counter ──────────────────────────────────
    conf_report = count_confluence(snap, direction)
    if conf_report.score < MIN_CONFLUENCE:
        labels = ", ".join(conf_report.labels) if conf_report.labels else "none"
        return skip(f"Confluence {conf_report.score}/{MIN_CONFLUENCE} — active: [{labels}]")

    # Confluence bonus
    conf_bonus = conf_report.score * 3   # max +24

    # ── Gate 8: Risk/reward ───────────────────────────────────────────────────
    ps     = pip_size(sym)
    pv     = pip_value(sym)
    sl_raw      = snap.atr_val * ATR_SL_MULTIPLIER
    sl_pips_raw = int(sl_raw / ps)
    sl_pips     = max(MIN_SL_PIPS.get(sym, 40), sl_pips_raw)
    sl_dist     = sl_pips * ps
    entry = snap.price
    if direction == "BUY":
        sl = entry - sl_dist; tp = entry + sl_dist * MIN_RR
    else:
        sl = entry + sl_dist; tp = entry - sl_dist * MIN_RR
    tp_pips = int(abs(tp - entry) / ps)
    rr      = tp_pips / sl_pips if sl_pips else 0
    if rr < MIN_RR:
        return skip(f"RR {rr:.2f} < {MIN_RR} minimum")

    risk_usd  = balance * RISK_PER_TRADE_PCT / 100.0
    lot_value = sl_dist * (pv / ps)
    raw_lot   = risk_usd / lot_value if lot_value > 0 else 0.01
    lot       = round(max(0.01, min(MAX_LOT, raw_lot)) / 0.01) * 0.01

    # ── Final confidence score ────────────────────────────────────────────────
    conf = (
        40
        + smc_bonus
        + trigger_bonus
        + fund_bonus
        + conf_bonus
        + (5 if smc_count == 3 else 0)
        + (5 if snap.trend_h1 != "RANGE" else 0)
        + (3 if snap.at_eq else 0)
    )
    conf = min(100, conf)

    if conf < MIN_CONFIDENCE:
        return skip(f"Confidence {conf} < {MIN_CONFIDENCE} minimum")

    smc_str = "+".join(smc_active) if smc_active else "none"
    conf_str = "+".join(conf_report.labels)
    reason  = (f"H1:{snap.trend_h1} M5:{snap.structure} | "
               f"SMC:{smc_str} | trigger:{trigger_name} | "
               f"Confluence:{conf_report.score}/8:[{conf_str}] | "
               f"fund:{snap.fund_bias} | conf:{conf}")

    dec = TradeDecision(
        symbol=sym, action=direction, reason=reason, confidence=conf,
        entry=entry, sl=sl, tp=tp, sl_pips=sl_pips, tp_pips=tp_pips,
        rr=round(rr, 2), lot=lot, risk_usd=round(risk_usd, 2),
        confluence=conf_report,
    )

    # ── Build and attach candle study (printed at execution time) ─────────────
    dec.candle_study = build_candle_study(snap, dec, conf_report)

    return dec

# ─────────────────────────────────────────────────────────────────────────────
#  SESSION & NEWS
# ─────────────────────────────────────────────────────────────────────────────

def current_session() -> Optional[str]:
    h = datetime.utcnow().hour
    for name, (start, end) in SESSIONS.items():
        if start <= h < end:
            return name
    return None


def news_blocked() -> Tuple[bool, str]:
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
#  POSITION PERSISTENCE
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
#  CLUSTER GUARD
# ─────────────────────────────────────────────────────────────────────────────

class ClusterGuard:
    MIN_SECONDS = 300
    MIN_PIPS    = 8

    def __init__(self):
        self._times:  Dict[str, float] = {}
        self._prices: Dict[str, float] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(CLUSTER_FILE):
                with open(CLUSTER_FILE) as f:
                    d = json.load(f)
                now_wall = time.time(); now_mono = time.monotonic()
                for sym, wall_t in d.get("times", {}).items():
                    self._times[sym] = now_mono - (now_wall - wall_t)
                self._prices = d.get("prices", {})
        except Exception:
            pass

    def _save(self):
        try:
            now_wall = time.time(); now_mono = time.monotonic()
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
    sym   = decision.symbol
    info  = mt5.symbol_info(sym)
    if not info:
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
        strategy_tag="v46_confluence",
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
    req = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       sym,
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           round(decision.sl,  info.digits),
        "tp":           round(decision.tp,  info.digits),
        "deviation":    20,
        "magic":        460001,
        "comment":      f"V46_{decision.action[:1]}_{decision.confidence}_C{decision.confluence.score if decision.confluence else 0}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(req)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        risk.confirm_fill(mrm_decision.position_id)
        track_reservation(result.order, mrm_decision.position_id)
        return result.order
    else:
        risk.reject_fill(mrm_decision.position_id)
        code = result.retcode if result else "None"
        print(f"  ❌ Order failed: {code}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
#  HARVEST
# ─────────────────────────────────────────────────────────────────────────────

def harvest(open_trades: Dict[int, OpenTrade], db: Database) -> Dict[int, OpenTrade]:
    if not open_trades:
        return open_trades
    live = {p.ticket for p in (mt5.positions_get() or [])}
    closed_any = False
    for ticket in list(open_trades.keys()):
        if ticket in live:
            continue
        t       = open_trades[ticket]
        from_dt = datetime.now() - timedelta(days=7)
        deals   = mt5.history_deals_get(from_dt, datetime.now()) or []
        deal    = next((d for d in deals
                        if d.order == ticket and d.entry == mt5.DEAL_ENTRY_OUT), None)
        if deal:
            profit    = deal.profit + deal.commission + deal.swap
            exit_p    = deal.price
            sl_dist   = abs(t.entry - t.sl) or 0.0001
            mult      = 1 if t.direction == "BUY" else -1
            realized  = (exit_p - t.entry) / sl_dist * mult
            entry_dt  = datetime.fromisoformat(t.opened_at)
            hold_mins = (datetime.fromtimestamp(deal.time) - entry_dt).total_seconds() / 60
            db.close_trade(t.db_id, ticket, exit_p, profit,
                           round(realized, 3), round(hold_mins, 1))
            icon = "✅" if profit > 0 else "❌"
            print(f"\n   {icon} CLOSED {t.symbol} {t.direction}  "
                  f"${profit:+.2f}  ({realized:+.2f}R)  {hold_mins:.0f}min")
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
║       BABSBOOKS PROFESSIONAL TRADING BOT  V46 — CONFLUENCE EDITION          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  8-Gate Engine  |  Ichimoku + OB + RSI Div + S&D  |  8-Signal Confluence   ║
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
    db.recover_offline_closes()
    print_stats(db, account.balance)

    cycle      = 0
    today_date = datetime.now().date()
    print(f"\n{'═'*72}")
    print(f"  🚀 V46 TRADING — 8-signal confluence  |  min {MIN_CONFLUENCE}/8 required")
    print(f"     Ichimoku ✦ Order Blocks ✦ RSI Div ✦ S&D Zones ✦ Candle Study")
    print(f"{'═'*72}")

    try:
        while True:
            cycle += 1
            now   = datetime.now()

            if now.date() != today_date:
                today_date = now.date()
                print(f"\n  📅 New day — counters reset")

            open_trades = harvest(open_trades, db)
            poll_closed_positions()  # release MRM-reserved exposure for anything that closed

            session = current_session()
            if session is None:
                wait_h = (8 - datetime.utcnow().hour) % 24
                if cycle % 20 == 1:
                    print(f"  💤 Outside session ({datetime.utcnow().strftime('%H:%M')} UTC) "
                          f"— London opens in ~{wait_h}h")
                time.sleep(60); continue

            today_stats = db.get_today_stats()
            if today_stats["trades"] >= MAX_TRADES_PER_DAY:
                if cycle % 10 == 1:
                    print(f"  ⏸️  Daily trade limit ({MAX_TRADES_PER_DAY}) reached")
                time.sleep(60); continue
            if today_stats["losses"] >= MAX_LOSSES_PER_DAY:
                if cycle % 10 == 1:
                    print(f"  🛑 Daily loss limit ({MAX_LOSSES_PER_DAY}) — protecting capital")
                time.sleep(60); continue

            blocked, news_reason = news_blocked()
            acct = mt5.account_info()
            if not acct:
                time.sleep(30); continue
            balance = acct.balance

            open_cnt = len(open_trades)
            print(f"\n  🔄 Cycle #{cycle}  {now.strftime('%H:%M:%S')}  {session}  "
                  f"Open:{open_cnt}  Trades:{today_stats['trades']}/{MAX_TRADES_PER_DAY}  "
                  f"Losses:{today_stats['losses']}/{MAX_LOSSES_PER_DAY}")

            if blocked:
                print(f"  📰 {news_reason} — all pairs paused")
                time.sleep(30); continue

            cycle_exposure: Dict[str, int] = {}
            for ot in open_trades.values():
                b, q = ot.symbol[:3], ot.symbol[3:]
                mult = 1 if ot.direction == "BUY" else -1
                cycle_exposure[b] = cycle_exposure.get(b, 0) + mult
                cycle_exposure[q] = cycle_exposure.get(q, 0) - mult
            cycle_done: set = set()

            for symbol in SYMBOLS:
                if symbol in cycle_done:
                    continue
                if open_cnt >= MAX_TRADES_PER_DAY:
                    break

                snap = read_market(symbol, session)
                if snap is None:
                    print(f"    ⚪ {symbol}  no data"); continue

                # ── V46 enhanced scan line ─────────────────────────────────────
                ich_str = ("☁️ ↑" if snap.ichimoku and snap.ichimoku.above_cloud else
                           "☁️ ↓" if snap.ichimoku and snap.ichimoku.below_cloud else
                           "☁️ ~") if snap.ichimoku else "N/A"
                ob_str  = ("🏛️ B" if snap.order_block and snap.order_block.price_in_bull_ob else
                           "🏛️ S" if snap.order_block and snap.order_block.price_in_bear_ob else "·")
                div_str = ("📉↑" if snap.rsi_div and (snap.rsi_div.bull_div or snap.rsi_div.bull_hidden) else
                           "📉↓" if snap.rsi_div and (snap.rsi_div.bear_div or snap.rsi_div.bear_hidden) else "·")
                sd_str  = ("📌D" if snap.sd_zone and snap.sd_zone.at_demand else
                           "📌S" if snap.sd_zone and snap.sd_zone.at_supply else "·")

                print(f"    🔍 {symbol}  ATR={snap.atr_pct:.3f}%  H1:{snap.trend_h1}  "
                      f"M5:{snap.structure}  RSI={snap.rsi:.0f}  "
                      f"{ich_str} {ob_str} {div_str} {sd_str}")

                dec = decide(snap, balance)

                if dec.action == "SKIP":
                    print(f"    ⏭️  {symbol}  {dec.reason}"); continue

                # Currency exposure
                base, quote = symbol[:3], symbol[3:]
                mult  = 1 if dec.action == "BUY" else -1
                new_b = cycle_exposure.get(base, 0) + mult
                new_q = cycle_exposure.get(quote, 0) - mult
                if abs(new_b) > 1 or abs(new_q) > 1:
                    print(f"    ⛔ {symbol}  Currency exposure limit"); continue

                # Spread check
                if "JPY" in symbol and snap.spread > 2.5:
                    print(f"    ⛔ {symbol}  JPY spread {snap.spread:.1f}pip > 2.5"); continue
                if "JPY" not in symbol and snap.spread > 2.0:
                    print(f"    ⛔ {symbol}  Spread {snap.spread:.1f}pip > 2.0"); continue

                ok, msg = cluster.allow(symbol, snap.price)
                if not ok:
                    print(f"    {msg}"); continue

                # ── V46: Print full candle study BEFORE executing ──────────────
                if dec.candle_study:
                    print_candle_study(dec.candle_study)

                print(f"\n    🎯 {symbol} {dec.action}  conf={dec.confidence}  "
                      f"SL={dec.sl_pips}pip  TP={dec.tp_pips}pip  "
                      f"RR=1:{dec.rr}  lot={dec.lot}  risk=${dec.risk_usd}")
                print(f"       Confluence: {dec.confluence.score}/8  [{'+'.join(dec.confluence.labels)}]")
                print(f"       Reason: {dec.reason}")

                ticket = execute(dec)
                if ticket:
                    ot = OpenTrade(
                        ticket=ticket, symbol=symbol, direction=dec.action,
                        entry=dec.entry, sl=dec.sl, tp=dec.tp, lot=dec.lot,
                        opened_at=datetime.now().isoformat(),
                    )
                    db_id = db.open_trade(ot, snap, dec.sl_pips, dec.rr,
                                          dec.confidence, dec.confluence)
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