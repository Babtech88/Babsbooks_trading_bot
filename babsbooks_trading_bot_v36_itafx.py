# babsbooks_trading_bot_v36_itafx.py
"""
╔══════════════════════════════════════════════════════════════════════════════════╗
║           BABSBOOKS PROFESSIONAL TRADING BOT  V36.0 — COMPLETE EDITION         ║
║                    (ITAFX PROP FIRM + 40-INDICATOR VARIANT)                     ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║  ALL FEATURES ACTIVE  |  PROFITABILITY OPTIMISED  |  CLEAN ARCHITECTURE        ║
╚══════════════════════════════════════════════════════════════════════════════════╝
"""

import os
import time
import json
import sqlite3
import traceback
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import MetaTrader5 as mt5

# ── Master Risk Manager integration ─────────────────────────────────────────
from bot_risk_config import risk, get_symbols, track_reservation, poll_closed_positions

try:
    from scipy import stats as sp_stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("⚠️  scipy not found — run:  pip install scipy")

VERSION = "36.0-COMPLETE-EDITION-ITAFX"

BANNER = f"""
{'═'*80}
  🏛️  BABSBOOKS PROFESSIONAL TRADING BOT   {VERSION}
{'═'*80}
  Complete Integrated System  |  All Features Active  |  Profitability Optimised
  Statistical Validation  |  Edge Discovery  |  Predictive Modelling
{'═'*80}
"""

SYMBOLS    = get_symbols(["EURUSD", "GBPUSD", "AUDUSD", "EURJPY", "GBPJPY", "EURGBP"])
TIMEFRAME  = mt5.TIMEFRAME_M5
BARS       = 250

MIN_PROFIT_FACTOR  = 1.5
MIN_EXPECTANCY_R   = 0.5

MAX_RISK_PER_TRADE          = 0.50

ACCOUNT_MODE = "itafx_2step"

_MODES = {
    "personal":    (  10.0,   20.0,    5,       3,     True,   False,     0  ),
    "demo":        (  10.0,   20.0,    5,       3,     True,   False,     0  ),
    "itafx_1step": (   3.0,    6.0,    3,       2,    False,   False,    30  ),
    "itafx_2step": (   6.0,   12.0,    3,       2,     True,   False,    40  ),
    "itafx_inst":  (   3.0,    6.0,    3,       2,    False,    True,    15  ),
}
_M = _MODES.get(ACCOUNT_MODE, _MODES["personal"])
(MAX_DAILY_DRAWDOWN_PCT, MAX_EQUITY_DRAWDOWN_PCT,
 MAX_DAILY_TRADES, MAX_CONSECUTIVE_LOSSES,
 WEEKEND_HOLD_ALLOWED, TRAILING_DRAWDOWN,
 CONSISTENCY_SCORE_LIMIT) = _M

IS_PROP_FIRM = ACCOUNT_MODE.startswith("itafx")

_BUFFER = 0.50 if IS_PROP_FIRM else 0.80
DAILY_DD_PERSONAL_LIMIT = MAX_DAILY_DRAWDOWN_PCT  * _BUFFER
TOTAL_DD_PERSONAL_LIMIT = MAX_EQUITY_DRAWDOWN_PCT * _BUFFER

MAX_DAILY_RISK_PCT = DAILY_DD_PERSONAL_LIMIT

ITAFX_EQUITY_STATES = {
    "CAUTION":  DAILY_DD_PERSONAL_LIMIT * 0.50,
    "DRAWDOWN": DAILY_DD_PERSONAL_LIMIT * 0.75,
    "HALT":     DAILY_DD_PERSONAL_LIMIT * 0.90,
}

MAX_LOT_HARD_CAP_PROP = 0.50

HQT_ENABLED        = True
HQT_STEP_PIPS      = 10
HQT_LOT_PER_ENTRY  = 0.01
HQT_MAX_ENTRIES    = 5
HQT_TP1_R          = 1.0
HQT_TP2_R          = 2.0
HQT_TP3_R          = 3.5
HQT_MAX_FLOAT_LOSS  = 2.0

MIN_ATR_PERCENT_ABSOLUTE    = 0.018

SESSION_ATR_FLOOR: Dict[str, float] = {
    "London":  0.020,
    "Overlap": 0.030,
    "NewYork": 0.025,
    "Asian":   0.999,
}

MIN_ATR_PERCENT_PER_STATE: Dict[str, float] = {
    "RANGE":           0.020,
    "COMPRESSION":     0.017,
    "BREAKOUT_SETUP":  0.016,
    "BREAKOUT_ACTIVE": 0.016,
    "EXPANSION":       0.016,
    "EXHAUSTION":      0.020,
}

MIN_VOLUME_RATIO            = 0.80

RANGE_SCALP_ALLOWED         = False
MIN_BREAKOUT_PROBABILITY    = 0.62

MIN_CONFIDENCE_BY_STATE: Dict[str, int] = {
    "RANGE":           85,
    "COMPRESSION":     75,
    "BREAKOUT_SETUP":  72,
    "BREAKOUT_ACTIVE": 68,
    "EXPANSION":       65,
    "EXHAUSTION":      80,
}
MIN_CONFIDENCE_SCORE        = 70

MIN_PRESSURE_SCORE          = 55
MIN_EXECUTION_SCORE         = 62
MIN_OPPORTUNITY_SCORE       = 68

BASE_SELECTIVITY                    = 65
SELECTIVITY_PER_OPEN_TRADE          = 5
MAX_SELECTIVITY                     = 85

ATR_PERCENTILE_WINDOW       = 20

SCAN_DEBUG = True

PATTERN_DETECTION_ENABLED = True

MARKET_STRUCTURE_FILTER   = True
STRUCTURE_LOOKBACK        = 50

NEWS_FILTER_ENABLED       = True
NEWS_BLOCK_MINUTES_BEFORE = 30
NEWS_BLOCK_MINUTES_AFTER  = 15
HIGH_IMPACT_NEWS: List[Tuple[int, int, str]] = [
    (12, 30, "US CPI / NFP / Retail Sales"),
    (13, 30, "US Jobless Claims"),
    (14, 00, "US ISM / Fed speeches"),
    (18, 00, "Fed rate decisions"),
    (9,  30, "BOE / ECB rate decisions"),
    (8,  30, "UK CPI / GDP"),
]

PERFORMANCE_DISPLAY_CYCLES = 40
FTMO_READINESS_THRESHOLDS  = {
    "min_trades":    50,
    "min_win_rate":  0.55,
    "min_pf":        1.30,
    "max_dd":        5.0,
}

MAX_SPREAD_NORMAL   = 2.0
MAX_SPREAD_JPY      = 3.5

STATE_QUALITY: Dict[str, int] = {
    "RANGE":            40,
    "COMPRESSION":      62,
    "BREAKOUT_SETUP":   76,
    "BREAKOUT_ACTIVE": 100,
    "EXPANSION":        86,
    "EXHAUSTION":       30,
}

STATE_COOLDOWNS: Dict[str, int] = {
    "RANGE":            8  * 60,
    "COMPRESSION":      6  * 60,
    "BREAKOUT_SETUP":   4  * 60,
    "BREAKOUT_ACTIVE":  2  * 60,
    "EXPANSION":        3  * 60,
    "EXHAUSTION":      14  * 60,
}
BASE_COOLDOWN = 4 * 60

ATR_SL_MULTIPLIER: Dict[str, float] = {
    "very_low":  2.2,
    "low":       1.8,
    "normal":    1.5,
    "high":      1.3,
    "very_high": 1.1,
}

ATR_RR_TABLE = [
    (0.025, 5.0, 1.5),
    (0.040, 4.5, 1.8),
    (0.065, 4.0, 2.0),
    (9.999, 3.2, 2.5),
]

STATE_RR_BONUS: Dict[str, float] = {
    "RANGE":           0.0,
    "COMPRESSION":     0.3,
    "BREAKOUT_SETUP":  0.5,
    "BREAKOUT_ACTIVE": 0.8,
    "EXPANSION":       1.0,
    "EXHAUSTION":      0.0,
}

TP_RR_HIGH_BP_BONUS = 0.3

TP_RR: Dict[str, float] = {
    "RANGE":           1.2,
    "COMPRESSION":     1.5,
    "BREAKOUT_SETUP":  2.0,
    "BREAKOUT_ACTIVE": 2.5,
    "EXPANSION":       3.0,
    "EXHAUSTION":      1.2,
}

MIN_SL_PIPS: Dict[str, int] = {
    "EURUSD": 40, "GBPUSD": 50, "AUDUSD": 40,
    "EURJPY": 70, "GBPJPY": 90, "EURGBP": 40,
}

MAX_SL_PIPS: Dict[str, int] = {
    "EURUSD": 100, "GBPUSD": 120, "AUDUSD": 100,
    "EURJPY": 170, "GBPJPY": 220, "EURGBP": 90,
}

SWING_LOOKBACK_BARS   = 12
SL_BUFFER_SPREAD_MULT = 2.0

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

BREAKEVEN_TRIGGER_R = 1.0

TARGET_RR: Dict[str, Dict[str, float]] = {
    "RANGE":            {"min": 1.2, "target": 1.5, "max": 2.0},
    "COMPRESSION":      {"min": 1.5, "target": 2.0, "max": 2.5},
    "BREAKOUT_SETUP":   {"min": 2.0, "target": 2.5, "max": 3.5},
    "BREAKOUT_ACTIVE":  {"min": 2.5, "target": 3.0, "max": 4.5},
    "EXPANSION":        {"min": 3.0, "target": 3.5, "max": 5.5},
    "EXHAUSTION":       {"min": 1.2, "target": 1.5, "max": 2.0},
}

VOLATILITY_RR_MULT: Dict[str, float] = {
    "very_low": 1.00, "low": 1.00, "normal": 1.00, "high": 1.10, "very_high": 1.20,
}

MIN_TIME_BETWEEN_TRADES_SEC = 60
MIN_PRICE_DISTANCE_PIPS     = 5

CORRELATION_THRESHOLD    = 0.70
MAX_CORRELATED_POSITIONS = 1

CURRENCY_MAP: Dict[str, Dict[str, int]] = {
    "EURUSD": {"EUR": 1, "USD": -1},
    "GBPUSD": {"GBP": 1, "USD": -1},
    "AUDUSD": {"AUD": 1, "USD": -1},
    "EURJPY": {"EUR": 1, "JPY": -1},
    "GBPJPY": {"GBP": 1, "JPY": -1},
    "EURGBP": {"EUR": 1, "GBP": -1},
}
MAX_CURRENCY_EXPOSURE = 1.2

MC_SIMS_HIGH     = 50_000
MC_SIMS_MODERATE = 10_000
MC_SIMS_LOW      =  2_000
MC_CONFIDENCE_THRESHOLDS = {"HIGH": 100, "MODERATE": 30, "LOW": 0}
STRESS_FACTORS = [0.3, 0.5, 0.7, 1.0, 1.3, 1.5, 2.0]
WALK_FORWARD_WINDOWS = [30, 60, 90, 120]

STATE_HISTORY_LEN          = 20
MAX_SETUP_DURATION_CANDLES = 15
BREAKOUT_TIMEOUT_MINUTES   = 45

EXPANSION_WEIGHTS = {
    "candle_spread": 20, "close_location": 25,
    "volume_acceleration": 20, "follow_through": 20, "pullback_depth": 15,
}

EXHAUSTION_WEIGHTS = {
    "atr_declining": 25, "volume_fading": 25, "momentum_divergence": 20,
    "wick_expansion": 15, "failed_continuation": 15,
}
EXHAUSTION_THRESHOLD = 60

CHECK_INTERVAL_SEC = 30

import os as _os
_SCRIPT_DIR = _os.path.dirname(_os.path.abspath(__file__))
DB_PATH     = _os.path.join(_SCRIPT_DIR, "babsbooks_v36_itafx.db")
POSITIONS_FILE = _os.path.join(_SCRIPT_DIR, "open_positions_v36_itafx.json")
JOURNAL_FILE    = "trade_journal_v36_itafx.json"
BOT_STATUS_FILE = "bot_status_v36_itafx.json"


def atomic_json_save(data: Any, filepath: str, indent: int = 2) -> bool:
    tmp    = filepath + ".tmp"
    backup = filepath + ".bak"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, default=str)
        if os.path.exists(filepath):
            try: os.replace(filepath, backup)
            except OSError: pass
        os.replace(tmp, filepath)
        try: os.remove(backup)
        except OSError: pass
        return True
    except Exception as e:
        print(f"⚠️  Atomic save failed for {filepath}: {e}")
        if os.path.exists(backup):
            try: os.replace(backup, filepath)
            except OSError: pass
        return False


def atomic_json_load(filepath: str, default: Any = None) -> Any:
    for path in (filepath, filepath + ".bak"):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if path.endswith(".bak"):
                    print(f"   ℹ️  Recovered from backup: {filepath}")
                return data
            except Exception:
                continue
    return default if default is not None else []


class TradeDatabase:
    DDL = {
        "trades": """
            CREATE TABLE IF NOT EXISTS trades (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket           INTEGER UNIQUE,
                symbol           TEXT,
                signal           TEXT,
                state            TEXT,
                session          TEXT,
                entry_price      REAL,
                exit_price       REAL,
                sl               REAL,
                tp               REAL,
                lot              REAL,
                risk_percent     REAL,
                confidence       INTEGER,
                execution_score  INTEGER,
                opportunity_score INTEGER,
                pressure_score   INTEGER,
                expansion_quality INTEGER,
                atr_percentile   REAL,
                spread_at_entry  REAL,
                profit           REAL,
                pnl_percent      REAL,
                realized_r       REAL,
                holding_minutes  REAL,
                win              BOOLEAN,
                tags             TEXT,
                edge_strength    TEXT,
                entry_time       TEXT,
                exit_time        TEXT
            )""",

        "trade_snapshots": """
            CREATE TABLE IF NOT EXISTS trade_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id            INTEGER,
                symbol              TEXT,
                snapshot_time       TEXT,
                price               REAL,
                atr_percent         REAL,
                atr_expansion       REAL,
                volume_ratio        REAL,
                volume_normalized   REAL,
                momentum            REAL,
                range_width         REAL,
                range_position_pct  REAL,
                state               TEXT,
                pressure_score      INTEGER,
                breakout_probability REAL,
                spread_pips         REAL,
                session             TEXT,
                hour_of_day         INTEGER,
                day_of_week         INTEGER,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )""",

        "feature_attribution": """
            CREATE TABLE IF NOT EXISTS feature_attribution (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id           INTEGER,
                feature_name       TEXT,
                feature_value      REAL,
                contribution_score REAL,
                direction          TEXT,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )""",

        "equity_curve": """
            CREATE TABLE IF NOT EXISTS equity_curve (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT,
                balance          REAL,
                equity           REAL,
                drawdown_percent REAL
            )""",

        "walk_forward_results": """
            CREATE TABLE IF NOT EXISTS walk_forward_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_date TEXT,
                train_start     TEXT,
                train_end       TEXT,
                test_start      TEXT,
                test_end        TEXT,
                train_sharpe    REAL,
                test_sharpe     REAL,
                decay_ratio     REAL,
                train_trades    INTEGER,
                test_trades     INTEGER
            )""",
    }

    MIGRATION_COLUMNS = {
        "trades": [
            ("state",             "TEXT"),
            ("pressure_score",    "INTEGER"),
            ("expansion_quality", "INTEGER"),
            ("opportunity_score", "INTEGER"),
            ("execution_score",   "INTEGER"),
            ("tags",              "TEXT"),
            ("edge_strength",     "TEXT"),
        ]
    }

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self):
        with self._connect() as conn:
            for sql in self.DDL.values():
                conn.execute(sql)
            self._migrate(conn)
        print("✅ Database ready")

    def _migrate(self, conn: sqlite3.Connection):
        for table, columns in self.MIGRATION_COLUMNS.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for col_name, col_type in columns:
                if col_name not in existing:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                        print(f"   ↳ migrated column: {table}.{col_name}")
                    except sqlite3.OperationalError:
                        pass

    def add_trade(self, d: Dict) -> int:
        sql = """
            INSERT INTO trades
              (ticket,symbol,signal,state,session,entry_price,exit_price,sl,tp,
               lot,risk_percent,confidence,execution_score,opportunity_score,
               pressure_score,expansion_quality,atr_percentile,spread_at_entry,
               profit,pnl_percent,realized_r,holding_minutes,win,tags,
               edge_strength,entry_time,exit_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._connect() as conn:
            cur = conn.execute(sql, (
                d.get("ticket"),      d.get("symbol"),         d.get("signal"),
                d.get("state"),       d.get("session"),         d.get("entry_price"),
                d.get("exit_price"),  d.get("sl"),              d.get("tp"),
                d.get("lot"),         d.get("risk_percent"),    d.get("confidence"),
                d.get("execution_score"), d.get("opportunity_score"),
                d.get("pressure_score"),  d.get("expansion_quality"),
                d.get("atr_percentile"),  d.get("spread_at_entry"),
                d.get("profit"),      d.get("pnl_percent"),     d.get("realized_r"),
                d.get("holding_minutes"), 1 if d.get("win") else 0,
                d.get("tags"),        d.get("edge_strength"),
                d.get("entry_time"),  d.get("exit_time"),
            ))
            return cur.lastrowid

    def close_trade(self, trade_id: int, ticket: int, exit_price: float,
                    profit: float, realized_r: float,
                    exit_time: str, holding_minutes: float):
        sql = """
            UPDATE trades
               SET exit_price=?, profit=?, realized_r=?, exit_time=?,
                   holding_minutes=?, win=?
             WHERE id=? OR ticket=?
        """
        with self._connect() as conn:
            conn.execute(sql, (
                exit_price, profit, realized_r, exit_time, holding_minutes,
                1 if profit > 0 else 0, trade_id, ticket,
            ))

    def add_snapshot(self, d: Dict):
        sql = """
            INSERT INTO trade_snapshots
              (trade_id,symbol,snapshot_time,price,atr_percent,atr_expansion,
               volume_ratio,volume_normalized,momentum,range_width,
               range_position_pct,state,pressure_score,breakout_probability,
               spread_pips,session,hour_of_day,day_of_week)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                d.get("trade_id"),    d.get("symbol"),         d.get("snapshot_time"),
                d.get("price"),       d.get("atr_percent"),    d.get("atr_expansion"),
                d.get("volume_ratio"), d.get("volume_normalized"), d.get("momentum"),
                d.get("range_width"), d.get("range_position_pct"), d.get("state"),
                d.get("pressure_score"), d.get("breakout_probability"),
                d.get("spread_pips"), d.get("session"),
                d.get("hour_of_day"), d.get("day_of_week"),
            ))

    def add_feature_attribution(self, trade_id: int, features: List[Dict]):
        sql = """
            INSERT INTO feature_attribution
              (trade_id,feature_name,feature_value,contribution_score,direction)
            VALUES (?,?,?,?,?)
        """
        rows = [(trade_id, f["name"], f["value"], f["contribution"], f["direction"])
                for f in features]
        with self._connect() as conn:
            conn.executemany(sql, rows)

    def add_equity_point(self, balance: float, equity: float, drawdown_pct: float):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO equity_curve (timestamp,balance,equity,drawdown_percent) VALUES (?,?,?,?)",
                (datetime.now().isoformat(), balance, equity, drawdown_pct),
            )

    def add_walk_forward(self, r: Dict):
        sql = """
            INSERT INTO walk_forward_results
              (validation_date,train_start,train_end,test_start,test_end,
               train_sharpe,test_sharpe,decay_ratio,train_trades,test_trades)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                datetime.now().isoformat(),
                r.get("train_start"), r.get("train_end"),
                r.get("test_start"),  r.get("test_end"),
                r.get("train_sharpe"), r.get("test_sharpe"),
                r.get("decay_ratio"), r.get("train_trades"), r.get("test_trades"),
            ))

    def get_statistics(self) -> Dict:
        with self._connect() as conn:
            def scalar(sql, params=()):
                row = conn.execute(sql, params).fetchone()
                return row[0] if row and row[0] is not None else 0

            total     = scalar("SELECT COUNT(*) FROM trades WHERE realized_r IS NOT NULL")
            win_rate  = scalar("SELECT AVG(CASE WHEN win=1 THEN 1.0 ELSE 0.0 END) FROM trades WHERE realized_r IS NOT NULL")
            exp       = scalar("SELECT AVG(realized_r) FROM trades WHERE realized_r IS NOT NULL")
            total_r   = scalar("SELECT SUM(realized_r) FROM trades WHERE realized_r IS NOT NULL")
            pf_raw    = scalar("""
                SELECT ABS(
                    SUM(CASE WHEN profit>0 THEN profit ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN profit<0 THEN profit ELSE 0 END)), 0)
                ) FROM trades WHERE profit IS NOT NULL
            """)
            max_dd    = scalar("SELECT MIN(drawdown_percent) FROM equity_curve") or 0

        return {
            "total_trades":    total,
            "win_rate":        float(win_rate),
            "expectancy":      float(exp),
            "total_return_r":  float(total_r),
            "profit_factor":   float(pf_raw) if pf_raw else 0.0,
            "max_drawdown_pct": abs(float(max_dd)),
        }

    def get_trades(self, limit: int = 500) -> List[Dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE realized_r IS NOT NULL ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_performance_by_state(self) -> Dict[str, Dict]:
        result: Dict[str, Dict] = {}
        with self._connect() as conn:
            for state in STATE_QUALITY:
                row = conn.execute("""
                    SELECT COUNT(*) as n,
                           AVG(realized_r) as avg_r,
                           SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins,
                           SUM(realized_r) as total_r
                      FROM trades WHERE state=? AND realized_r IS NOT NULL
                """, (state,)).fetchone()
                if row and row[0] > 0:
                    result[state] = {
                        "trades":   int(row[0]),
                        "avg_r":    round(float(row[1] or 0), 3),
                        "wins":     int(row[2] or 0),
                        "total_r":  round(float(row[3] or 0), 3),
                        "win_rate": round(row[2] / row[0] * 100, 1) if row[0] else 0,
                    }
        return result

    def get_performance_by_session(self) -> Dict[str, Dict]:
        result: Dict[str, Dict] = {}
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT session,
                       COUNT(*) as n,
                       AVG(realized_r) as avg_r,
                       SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as wins
                  FROM trades WHERE realized_r IS NOT NULL
                 GROUP BY session
            """).fetchall()
            for row in rows:
                sess = row[0] or "unknown"
                result[sess] = {
                    "trades":   int(row[1]),
                    "avg_r":    round(float(row[2] or 0), 3),
                    "win_rate": round(row[3] / row[1] * 100, 1) if row[1] else 0,
                }
        return result

    def get_top_features(self, top_n: int = 10) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT fa.feature_name,
                       AVG(fa.contribution_score) as avg_contrib,
                       AVG(t.realized_r)          as avg_r,
                       COUNT(*)                   as n
                  FROM feature_attribution fa
                  JOIN trades t ON fa.trade_id = t.id
                 WHERE t.realized_r IS NOT NULL
                 GROUP BY fa.feature_name
                 ORDER BY avg_contrib DESC
                 LIMIT ?
            """, (top_n,)).fetchall()
        return [{"feature": r[0], "avg_contrib": r[1], "avg_r": r[2], "count": r[3]}
                for r in rows]


class EquityCurveProtection:
    STATES = ("NORMAL", "CAUTION", "DRAWDOWN", "HALT")
    DD_THRESHOLDS    = ITAFX_EQUITY_STATES
    RISK_MULTIPLIERS = {"NORMAL": 1.0, "CAUTION": 0.60, "DRAWDOWN": 0.30, "HALT": 0.0}

    def __init__(self, initial_balance: float, db: TradeDatabase):
        self.initial_balance   = initial_balance
        self.peak_balance      = initial_balance
        self.day_start_balance = initial_balance
        self.db                = db
        self.state             = "NORMAL"
        self.consecutive_losses = 0
        self._reset_day        = datetime.now().date()

    def record(self, balance: float, trade_result: Optional[str] = None) -> float:
        today = datetime.now().date()
        if today != self._reset_day:
            self._reset_day        = today
            self.day_start_balance = balance
            self.consecutive_losses = 0

        if TRAILING_DRAWDOWN:
            self.peak_balance = max(self.peak_balance, balance)
            dd_pct = (self.peak_balance - balance) / self.initial_balance * 100
        else:
            dd_pct = (self.initial_balance - balance) / self.initial_balance * 100

        daily_dd_pct = (self.day_start_balance - balance) / self.initial_balance * 100

        self.db.add_equity_point(balance, balance, daily_dd_pct)

        if trade_result == "loss":
            self.consecutive_losses += 1
        elif trade_result in ("win", "breakeven"):
            self.consecutive_losses = 0

        working_dd = max(dd_pct, daily_dd_pct)
        if working_dd >= self.DD_THRESHOLDS["HALT"]:
            self.state = "HALT"
        elif working_dd >= self.DD_THRESHOLDS["DRAWDOWN"]:
            self.state = "DRAWDOWN"
        elif working_dd >= self.DD_THRESHOLDS["CAUTION"]:
            self.state = "CAUTION"
        else:
            self.state = "NORMAL"

        return daily_dd_pct

    @property
    def risk_multiplier(self) -> float:
        return self.RISK_MULTIPLIERS[self.state]

    def blocked(self) -> Tuple[bool, str]:
        if self.state == "HALT":
            return True, (f"🛑 ITAFX SAFETY HALT — daily drawdown approaching firm limit "
                          f"({MAX_DAILY_DRAWDOWN_PCT}%). BOT STOPPED to protect account.")
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return True, f"⛔ {self.consecutive_losses} consecutive losses today — paused for the day"

        if not WEEKEND_HOLD_ALLOWED:
            now = datetime.utcnow()
            if now.weekday() == 4 and now.hour >= 21:
                return True, "⛔ ITAFX: No new trades Friday 21:00+ (weekend hold not allowed on this account)"
            if now.weekday() in (5, 6):
                return True, "⛔ ITAFX: Weekend — no trading on this account type"

        return False, ""

    def itafx_status(self, balance: float) -> str:
        daily_dd = (self.day_start_balance - balance) / self.initial_balance * 100
        total_dd = (self.initial_balance - balance) / self.initial_balance * 100
        daily_remaining = MAX_DAILY_DRAWDOWN_PCT - daily_dd
        total_remaining = MAX_EQUITY_DRAWDOWN_PCT - total_dd
        return (f"{'PROP' if IS_PROP_FIRM else 'PERSONAL'} [{ACCOUNT_MODE.upper()}]  "
                f"Daily: {daily_dd:.2f}%/{MAX_DAILY_DRAWDOWN_PCT}% "
                f"(⚠️{daily_remaining:.2f}% left)  "
                f"Total: {total_dd:.2f}%/{MAX_EQUITY_DRAWDOWN_PCT}% "
                f"({total_remaining:.2f}% left)  "
                f"State: {self.state}")


class ClusteringGuard:
    _PERSIST_FILE = _os.path.join(_SCRIPT_DIR, "cluster_state_v36_itafx.json")

    def __init__(self):
        self._last_time:  Dict[str, float] = {}
        self._last_price: Dict[str, float] = {}
        self._load()

    def _load(self):
        try:
            import json, os
            if os.path.exists(self._PERSIST_FILE):
                with open(self._PERSIST_FILE) as f:
                    data = json.load(f)
                wall_now  = time.time()
                mono_now  = time.monotonic()
                for sym, wall_ts in data.get("last_wall_time", {}).items():
                    elapsed = wall_now - wall_ts
                    self._last_time[sym] = mono_now - elapsed
                self._last_price = data.get("last_price", {})
                print(f"  🔒 ClusterGuard: loaded state for {list(self._last_time.keys())}")
        except Exception:
            pass

    def _save(self):
        try:
            import json
            wall_now = time.time()
            mono_now = time.monotonic()
            wall_times = {}
            for sym, mono_ts in self._last_time.items():
                elapsed = mono_now - mono_ts
                wall_times[sym] = wall_now - elapsed
            with open(self._PERSIST_FILE, "w") as f:
                json.dump({"last_wall_time": wall_times,
                           "last_price":     self._last_price}, f)
        except Exception:
            pass

    def allow(self, symbol: str, price: float, point: float) -> Tuple[bool, str]:
        now = time.monotonic()
        if symbol in self._last_time:
            elapsed = now - self._last_time[symbol]
            if elapsed < MIN_TIME_BETWEEN_TRADES_SEC:
                msg = f"Time cluster {elapsed:.0f}s < {MIN_TIME_BETWEEN_TRADES_SEC}s"
                if SCAN_DEBUG:
                    print(f"      ⏳ {symbol}  {msg}")
                return False, msg
        if symbol in self._last_price:
            dist_pips = abs(price - self._last_price[symbol]) / (point or 0.0001)
            if dist_pips < MIN_PRICE_DISTANCE_PIPS:
                msg = f"Price cluster {dist_pips:.1f}pip < {MIN_PRICE_DISTANCE_PIPS}pip"
                if SCAN_DEBUG:
                    print(f"      ⏳ {symbol}  {msg}")
                return False, msg
        return True, "OK"

    def record(self, symbol: str, price: float):
        self._last_time[symbol]  = time.monotonic()
        self._last_price[symbol] = price
        self._save()


class CorrelationFilter:
    def __init__(self):
        self._matrix:      pd.DataFrame = pd.DataFrame()
        self._last_update: float        = 0.0
        self._ttl:         float        = 300.0

    def _refresh(self):
        series_list = []
        for sym in SYMBOLS:
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 120)
            if rates is not None and len(rates) >= 20:
                series_list.append(pd.Series(pd.DataFrame(rates)["close"].pct_change().values))
            else:
                series_list.append(pd.Series(np.zeros(120)))

        min_len = min(len(s) for s in series_list)
        arr = np.column_stack([s.values[:min_len] for s in series_list])
        self._matrix = pd.DataFrame(np.corrcoef(arr, rowvar=False),
                                    index=SYMBOLS, columns=SYMBOLS)
        self._last_update = time.monotonic()

    def allow(self, symbol: str, open_positions: Dict[str, str]) -> Tuple[bool, str]:
        if "JPY" in symbol:
            for other_sym in open_positions:
                if "JPY" in other_sym and other_sym != symbol:
                    return False, f"JPY cross already open ({other_sym}) — correlated"

        if self._matrix.empty or (time.monotonic() - self._last_update) > self._ttl:
            try:
                self._refresh()
            except Exception:
                return True, "OK"

        if symbol not in self._matrix.columns:
            return True, "OK"

        for other_sym, other_dir in open_positions.items():
            if other_sym == symbol:
                continue
            corr = self._matrix.loc[symbol, other_sym] if other_sym in self._matrix.columns else 0.0
            if abs(corr) > CORRELATION_THRESHOLD:
                return False, f"High correlation {corr:.2f} with open {other_sym}"
        return True, "OK"


class CurrencyExposureGuard:
    @staticmethod
    def allow(symbol: str, signal: str,
              open_positions: Dict[str, str]) -> Tuple[bool, str]:
        exposure: Dict[str, float] = defaultdict(float)

        for sym, sig in open_positions.items():
            mult = 1 if sig == "buy" else -1
            for curr, val in CURRENCY_MAP.get(sym, {}).items():
                exposure[curr] += val * mult

        new_mult = 1 if signal == "buy" else -1
        for curr, val in CURRENCY_MAP.get(symbol, {}).items():
            projected = exposure[curr] + val * new_mult
            if abs(projected) > MAX_CURRENCY_EXPOSURE:
                return False, f"{curr} exposure would reach {projected:.1f}"
        return True, "OK"


class SessionVolumeNormaliser:
    def __init__(self):
        self._history: Dict[str, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._MAX = 7 * 24

    def push(self, symbol: str, hour: int, volume: float):
        buf = self._history[symbol][hour]
        buf.append(volume)
        if len(buf) > self._MAX:
            del buf[: len(buf) - self._MAX]

    def normalised(self, symbol: str, hour: int, current_volume: float) -> float:
        hist = self._history[symbol].get(hour, [])
        if len(hist) < 5:
            all_vols: List[float] = []
            for h_vols in self._history[symbol].values():
                all_vols.extend(h_vols)
            if len(all_vols) >= 5:
                avg = float(np.mean(all_vols))
                return float(np.clip(current_volume / avg if avg > 0 else 1.0, 0.3, 3.0))
            return 1.0
        avg = float(np.mean(hist[-50:]))
        if avg == 0:
            return 1.0
        return float(np.clip(current_volume / avg, 0.3, 3.0))


class StateMemory:
    def __init__(self):
        self.state             = "RANGE"
        self.prev_state        = "RANGE"
        self.start_time        = time.monotonic()
        self.duration_candles  = 0
        self.transition_str    = 0
        self._log: List[Tuple[str, float, int]] = []

    def update(self, raw_state: str, pressure: int, atr_exp: float) -> str:
        if raw_state != self.state:
            self._log.append((self.state, self.start_time, pressure))
            if len(self._log) > STATE_HISTORY_LEN:
                self._log = self._log[-STATE_HISTORY_LEN:]
            self.prev_state       = self.state
            self.state            = raw_state
            self.start_time       = time.monotonic()
            self.duration_candles = 1
            self.transition_str   = self._calc_strength(pressure, atr_exp)
        else:
            self.duration_candles += 1

        return self._apply_timeouts()

    def breakout_probability(self) -> float:
        dc = self.duration_candles
        if self.state == "BREAKOUT_SETUP":
            if dc < 5:   return 0.62
            if dc < 10:  return 0.76
            if dc < MAX_SETUP_DURATION_CANDLES: return 0.60
            return 0.28
        if self.state == "COMPRESSION":   return 0.46
        if self.state in ("BREAKOUT_ACTIVE", "EXPANSION"): return 0.86
        return 0.20

    def quality_boost(self) -> float:
        base = STATE_QUALITY.get(self.state, 50) / 100.0
        if self.state == "BREAKOUT_SETUP" and self.duration_candles > 10:
            base *= 0.70
        if self.state in ("BREAKOUT_ACTIVE", "EXPANSION") and self.duration_candles < 3:
            base *= 1.25
        return min(1.5, base)

    def _apply_timeouts(self) -> str:
        if self.state == "BREAKOUT_SETUP" and self.duration_candles > MAX_SETUP_DURATION_CANDLES:
            return "COMPRESSION"
        if self.state in ("BREAKOUT_ACTIVE", "EXPANSION"):
            mins = (time.monotonic() - self.start_time) / 60.0
            if mins > BREAKOUT_TIMEOUT_MINUTES:
                return "EXHAUSTION"
        return self.state

    @staticmethod
    def _calc_strength(pressure: int, atr_exp: float) -> int:
        s = 0
        s += 40 if pressure > 70 else (20 if pressure > 50 else 0)
        s += 30 if atr_exp > 1.5 else (15 if atr_exp > 1.2 else 0)
        return min(100, s)


class BreakoutStateMachine:
    def __init__(self):
        self._memory:   Dict[str, StateMemory] = {}
        self._press_q:  Dict[str, List[int]]   = defaultdict(list)

    def memory(self, symbol: str) -> StateMemory:
        if symbol not in self._memory:
            self._memory[symbol] = StateMemory()
        return self._memory[symbol]

    def pressure_score(self, df: pd.DataFrame, atr_exp: float, vol_norm: float) -> int:
        last  = df.iloc[-1]

        def fv(key, fb=0.0):
            try:
                v = float(last[key])
                return fb if (np.isnan(v) or np.isinf(v)) else v
            except (KeyError, TypeError, ValueError):
                return fb

        rh    = fv("range_high_20")
        rl    = fv("range_low_20")
        price = fv("close")
        rw    = rh - rl
        score = 10

        if rw > 0:
            pct  = (price - rl) / rw
            prox = min(pct, 1.0 - pct) * 2
            score += int(np.clip(prox, 0, 1) * 35)

        score += 30 if atr_exp > 1.5 else (20 if atr_exp > 1.2 else (12 if atr_exp > 1.0 else 4))

        mom = abs(fv("momentum", 0.0))
        score += 20 if mom > 0.8 else (14 if mom > 0.5 else (8 if mom > 0.3 else 3))

        score += 15 if vol_norm > 1.5 else (10 if vol_norm > 1.2 else (5 if vol_norm > 1.0 else 0))

        return min(100, int(score))

    def classify(
        self, symbol: str, df: pd.DataFrame,
        pressure: int, atr_exp: float, vol_norm: float,
    ) -> Tuple[str, int, float]:
        last  = df.iloc[-1]
        price = last["close"]
        rh    = last["range_high_20"]
        rl    = last["range_low_20"]
        mem   = self.memory(symbol)

        q = self._press_q[symbol]
        q.append(pressure)
        if len(q) > 10:
            del q[:len(q) - 10]
        avg_p = float(np.mean(q))

        is_break = price > rh or price < rl
        if is_break and vol_norm > 1.2 and atr_exp > 1.3:
            raw = "BREAKOUT_ACTIVE"
        elif is_break and (vol_norm > 1.0 or atr_exp > 1.1):
            raw = "BREAKOUT_ACTIVE"
        elif is_break:
            raw = "BREAKOUT_SETUP"
        elif pressure > 70 and avg_p > 60:
            raw = "BREAKOUT_SETUP"
        elif pressure > 55 or (atr_exp > 1.5 and vol_norm > 1.2):
            raw = "COMPRESSION"
        else:
            raw = "RANGE"

        exh_score    = self._exhaustion_score(df)
        was_trending = mem.prev_state in ("BREAKOUT_ACTIVE", "EXPANSION") or \
                       mem.state      in ("BREAKOUT_ACTIVE", "EXPANSION")
        long_enough  = mem.duration_candles >= 5
        if exh_score >= EXHAUSTION_THRESHOLD and was_trending and long_enough:
            raw = "EXHAUSTION"

        if raw in ("BREAKOUT_ACTIVE", "EXPANSION"):
            exp_q = self._expansion_quality(df, vol_norm)
            if exp_q < 25 and raw == "EXPANSION":
                raw = "BREAKOUT_ACTIVE"
        else:
            exp_q = 0

        final = mem.update(raw, pressure, atr_exp)
        return final, STATE_QUALITY.get(final, 50), mem.breakout_probability()

    def _expansion_quality(self, df: pd.DataFrame, vol_norm: float) -> int:
        if len(df) < 5:
            return 0
        last = df.iloc[-1]
        prev = df.iloc[-2]
        score = 0

        spread = last["high"] - last["low"]
        atr    = last["atr"] if last["atr"] > 0 else 1.0
        score += min(20, int(spread / atr * 20)) * EXPANSION_WEIGHTS["candle_spread"] // 20

        body  = abs(last["close"] - last["open"])
        rng   = spread if spread > 0 else 0.0001
        close_loc = body / rng
        score += int(close_loc * EXPANSION_WEIGHTS["close_location"])

        if vol_norm > 1.3:
            score += EXPANSION_WEIGHTS["volume_acceleration"]
        elif vol_norm > 1.1:
            score += EXPANSION_WEIGHTS["volume_acceleration"] // 2

        if len(df) >= 3:
            c1 = df.iloc[-3]["high"] - df.iloc[-3]["low"]
            c2 = df.iloc[-2]["high"] - df.iloc[-2]["low"]
            c3 = last["high"] - last["low"]
            if c3 > c2 > c1:
                score += EXPANSION_WEIGHTS["follow_through"]

        return min(100, score)

    def _exhaustion_score(self, df: pd.DataFrame) -> int:
        if len(df) < 12:
            return 0
        score = 0
        tail  = df.tail(10)

        atrs = tail["atr"].values
        if atrs[-1] < atrs[0] * 0.80 and atrs[-1] < atrs.mean() * 0.90:
            score += EXHAUSTION_WEIGHTS["atr_declining"]

        vols = tail["volume_ratio"].values if "volume_ratio" in tail.columns else np.ones(10)
        if vols[0] > 1.1 and vols[-1] < vols[0] * 0.70:
            score += EXHAUSTION_WEIGHTS["volume_fading"]

        moms = tail["momentum"].values
        had_momentum = abs(moms[0]) > 0.30
        now_stalled  = abs(moms[-1]) < 0.10
        if had_momentum and now_stalled and moms[-1] * moms[0] > 0:
            score += EXHAUSTION_WEIGHTS["momentum_divergence"]

        last = df.iloc[-1]
        rng  = last["high"] - last["low"]
        if rng > 0:
            wick_ratio = (last["upper_wick"] + last["lower_wick"]) / rng
            if wick_ratio > 0.75:
                score += EXHAUSTION_WEIGHTS["wick_expansion"]

        if len(df) >= 6:
            prior_high = df.iloc[-6:-3]["high"].max()
            prior_low  = df.iloc[-6:-3]["low"].min()
            recent_cls = df.iloc[-3:]["close"].values
            bull_fail  = prior_high > 0 and all(c < prior_high for c in recent_cls)
            bear_fail  = prior_low  > 0 and all(c > prior_low  for c in recent_cls)
            if bull_fail or bear_fail:
                score += EXHAUSTION_WEIGHTS["failed_continuation"]

        return score


class MonteCarloValidator:
    def __init__(self, db: TradeDatabase):
        self.db      = db
        self.returns: List[float] = []
        self.mode    = "LOW"
        self.results: Dict = {}

    def load(self) -> Tuple[int, str, int]:
        trades       = self.db.get_trades(500)
        self.returns = [t["realized_r"] for t in trades if t.get("realized_r") is not None]
        n            = len(self.returns)
        if n >= MC_CONFIDENCE_THRESHOLDS["HIGH"]:
            self.mode, sims = "HIGH",     MC_SIMS_HIGH
        elif n >= MC_CONFIDENCE_THRESHOLDS["MODERATE"]:
            self.mode, sims = "MODERATE", MC_SIMS_MODERATE
        else:
            self.mode, sims = "LOW",      MC_SIMS_LOW
        return n, self.mode, sims

    def run(self) -> Dict:
        if len(self.returns) < 10:
            return {"status": "insufficient_data"}

        n_sims = {"HIGH": MC_SIMS_HIGH, "MODERATE": MC_SIMS_MODERATE, "LOW": MC_SIMS_LOW}[self.mode]
        arr    = np.array(self.returns)
        rng    = np.random.default_rng(42)

        total_r: List[float] = []
        sharpes: List[float] = []
        max_dds: List[float] = []

        for _ in range(n_sims):
            sample = rng.choice(arr, size=len(arr), replace=True)
            cum    = np.cumsum(sample)
            ann_r  = cum[-1] * (252 / max(len(arr), 1))
            ann_sd = float(np.std(sample)) * np.sqrt(252 / max(len(arr), 1)) + 1e-9
            sharpe = ann_r / ann_sd
            dd     = float(np.min(cum - np.maximum.accumulate(cum)))
            total_r.append(float(cum[-1]))
            sharpes.append(float(sharpe))
            max_dds.append(dd)

        arr_r = np.array(total_r)
        self.results = {
            "mode":           self.mode,
            "n_sims":         n_sims,
            "n_trades":       len(self.returns),
            "mean_return":    float(np.mean(arr_r)),
            "p5_return":      float(np.percentile(arr_r, 5)),
            "p95_return":     float(np.percentile(arr_r, 95)),
            "prob_negative":  float(np.mean(arr_r < 0)),
            "mean_sharpe":    float(np.mean(sharpes)),
            "mean_max_dd":    float(np.mean(max_dds)),
            "survival_prob":  float(np.mean(arr_r > -4.0)),
        }

        stress: Dict[str, Dict] = {}
        for factor in STRESS_FACTORS:
            scaled     = arr * factor
            cum_all    = np.cumsum(rng.choice(scaled, size=(1000, len(scaled)), replace=True), axis=1)
            stress[str(factor)] = {
                "mean_r":     float(np.mean(cum_all[:, -1])),
                "survival":   float(np.mean(cum_all[:, -1] > -4.0)),
            }
        self.results["stress_tests"] = stress

        return self.results

    def run_walk_forward(self, db: TradeDatabase) -> List[Dict]:
        trades = self.db.get_trades(500)
        if len(trades) < 20:
            return []

        results = []
        for window in WALK_FORWARD_WINDOWS:
            half  = window // 2
            n     = len(trades)
            if n < window:
                continue

            train = trades[:half]
            test  = trades[half:window]
            if not train or not test:
                continue

            def sharpe(ts: List[Dict]) -> float:
                rs = [t["realized_r"] for t in ts if t.get("realized_r")]
                if len(rs) < 2:
                    return 0.0
                return float(np.mean(rs) / (np.std(rs) + 1e-9))

            ts = sharpe(train)
            ws = sharpe(test)
            wf = {
                "window":       window,
                "train_trades": len(train),
                "test_trades":  len(test),
                "train_sharpe": round(ts, 3),
                "test_sharpe":  round(ws, 3),
                "decay_ratio":  round(ws / ts, 3) if ts != 0 else 0.0,
                "train_start":  train[-1].get("entry_time", ""),
                "train_end":    train[0].get("entry_time", ""),
                "test_start":   test[-1].get("entry_time", ""),
                "test_end":     test[0].get("entry_time", ""),
            }
            results.append(wf)
            db.add_walk_forward(wf)
        return results

    def print_report(self):
        if not self.results or "mean_return" not in self.results:
            print("\n⚠️  Run Monte Carlo first or insufficient data")
            return
        r = self.results
        sep = "═" * 72
        print(f"\n{sep}")
        print(f"  🎲  MONTE CARLO VALIDATION  [{r['mode']} confidence — {r['n_sims']:,} simulations]")
        print(sep)
        print(f"  Trades used         : {r['n_trades']}")
        print(f"  Mean return         : {r['mean_return']:+.2f}R")
        print(f"  5th pct / 95th pct  : {r['p5_return']:+.2f}R  /  {r['p95_return']:+.2f}R")
        print(f"  Prob negative       : {r['prob_negative']*100:.1f}%")
        print(f"  Mean Sharpe         : {r['mean_sharpe']:.2f}")
        print(f"  Mean max drawdown   : {r['mean_max_dd']:.2f}R")
        print(f"  Survival prob       : {r['survival_prob']*100:.1f}%")
        print(f"\n  Stress tests (factor × historical volatility):")
        for factor, d in r.get("stress_tests", {}).items():
            bar = "▓" * int(d["survival"] * 20)
            print(f"    {factor:>4}×  mean={d['mean_r']:+.2f}R  survival={d['survival']*100:.0f}%  {bar}")
        print(sep)


class TradeReplayEngine:
    def __init__(self, db: TradeDatabase):
        self.db     = db
        self.trades: List[Dict] = []

    def load(self, limit: int = 100) -> int:
        self.trades = self.db.get_trades(limit)
        return len(self.trades)

    def replay(self, idx: int = 0):
        if not self.trades or idx >= len(self.trades):
            print("❌ Trade not found"); return
        t = self.trades[idx]
        pnl = t.get("profit", 0) or 0
        sep = "─" * 64
        print(f"\n{'═'*64}")
        print(f"  🎬  TRADE REPLAY  #{t.get('ticket')}  —  {t.get('symbol')}")
        print(f"{'═'*64}")
        print(f"  Entry      : {t.get('entry_time')}  ({t.get('session')})")
        print(f"  Exit       : {t.get('exit_time')}")
        print(f"  Direction  : {str(t.get('signal', '')).upper()}  |  State: {t.get('state')}")
        print(sep)
        print(f"  Confidence : {t.get('confidence')}   Opportunity: {t.get('opportunity_score')}   Pressure: {t.get('pressure_score')}")
        print(f"  Edge       : {t.get('edge_strength')}  |  Tags: {t.get('tags')}")
        print(sep)
        sign = "+" if pnl >= 0 else ""
        print(f"  P&L        : {sign}${pnl:.2f}   Realized R: {t.get('realized_r', 0):+.2f}   {'✅ WIN' if t.get('win') else '❌ LOSS'}")
        print(f"{'═'*64}")

    def comprehensive_report(self):
        if not self.trades:
            print("❌ Load trades first"); return
        sep = "═" * 72
        print(f"\n{sep}")
        print("  📊  COMPREHENSIVE PERFORMANCE REPORT")
        print(sep)

        print("\n  🎯 BY STATE")
        for state, d in self.db.get_performance_by_state().items():
            bar = "▓" * int(d["win_rate"] / 5)
            print(f"    {state:<20} {d['trades']:>3} trades  WR:{d['win_rate']:>5.1f}%  AvgR:{d['avg_r']:+.3f}  {bar}")

        print("\n  🕐 BY SESSION")
        for sess, d in self.db.get_performance_by_session().items():
            bar = "▓" * int(d["win_rate"] / 5)
            print(f"    {sess:<12} {d['trades']:>3} trades  WR:{d['win_rate']:>5.1f}%  AvgR:{d['avg_r']:+.3f}  {bar}")

        print("\n  🔬 TOP PREDICTIVE FEATURES")
        for f in self.db.get_top_features(8):
            print(f"    {f['feature']:<25} avg_contrib:{f['avg_contrib']:.3f}  avg_R:{f['avg_r']:.3f}  n={f['count']}")

        print(sep)


@dataclass
class TradeRecord:
    ticket:           int
    symbol:           str
    signal:           str
    entry_price:      float
    sl:               float
    tp:               float
    lot:              float
    risk_percent:     float
    confidence:       int
    state:            str
    entry_time:       str
    db_id:            int       = 0
    opportunity_score: int      = 0
    execution_score:  int       = 0
    pressure_score:   int       = 0
    expansion_quality: int      = 0
    atr_percentile:   float     = 0.0
    spread_at_entry:  float     = 0.0
    session:          str       = ""
    tags:             str       = ""
    edge_strength:    str       = ""
    breakeven_set:    bool      = False
    partial_closes:   List[float] = field(default_factory=list)
    status:           str       = "open"


class TradeManager:
    _POSITIONS_FILE = POSITIONS_FILE

    def __init__(self):
        self.open:          Dict[int, TradeRecord] = {}
        self.history:       List[TradeRecord]      = []
        self.daily_count    = 0
        self.daily_risk_pct = 0.0
        self.daily_pnl      = 0.0
        self._reset_day:    date = datetime.now().date()
        self._state_cooldown: Dict[str, float] = {}
        self._load_open_positions()

    def _load_open_positions(self):
        import json, os
        if not os.path.exists(self._POSITIONS_FILE):
            return
        try:
            with open(self._POSITIONS_FILE) as f:
                data = json.load(f)
            recovered = 0
            for d in data.get("positions", []):
                tr = TradeRecord(
                    ticket=d["ticket"], symbol=d["symbol"], signal=d["signal"],
                    entry_price=d["entry_price"], sl=d["sl"], tp=d["tp"],
                    lot=d["lot"], risk_percent=d["risk_percent"],
                    confidence=d.get("confidence", 0),
                    opportunity_score=d.get("opportunity_score", 0),
                    execution_score=d.get("execution_score", 0),
                    pressure_score=d.get("pressure_score", 0),
                    expansion_quality=0,
                    atr_percentile=d.get("atr_percentile", 0),
                    spread_at_entry=d.get("spread_at_entry", 0),
                    state=d.get("state", "UNKNOWN"),
                    entry_time=d.get("entry_time", datetime.now().isoformat()),
                    session=d.get("session", "Unknown"),
                    tags=d.get("tags", ""),
                    edge_strength=d.get("edge_strength", "unknown"),
                )
                tr.db_id = d.get("db_id", 0)
                self.open[tr.ticket] = tr
                recovered += 1
            if data.get("date", "") == str(datetime.now().date()):
                self.daily_count    = data.get("daily_count", 0)
                self.daily_risk_pct = data.get("daily_risk_pct", 0.0)
            if recovered:
                print(f"  🔄 Recovered {recovered} open position(s) from disk")
                for tix, tr in self.open.items():
                    print(f"     ↳ #{tix} {tr.symbol} {tr.signal} @ {tr.entry_price}"
                          f"  SL:{tr.sl:.5f}  TP:{tr.tp:.5f}")
        except Exception as e:
            print(f"  ⚠️  Could not restore positions: {e}")

    def _save_open_positions(self):
        import json
        positions = []
        for tix, tr in self.open.items():
            positions.append({
                "ticket": tix, "symbol": tr.symbol, "signal": tr.signal,
                "entry_price": tr.entry_price, "sl": tr.sl, "tp": tr.tp,
                "lot": tr.lot, "risk_percent": tr.risk_percent,
                "confidence": tr.confidence, "opportunity_score": tr.opportunity_score,
                "execution_score": tr.execution_score, "pressure_score": tr.pressure_score,
                "atr_percentile": tr.atr_percentile, "spread_at_entry": tr.spread_at_entry,
                "state": tr.state, "entry_time": tr.entry_time,
                "session": tr.session, "tags": tr.tags,
                "edge_strength": tr.edge_strength, "db_id": tr.db_id,
            })
        try:
            with open(self._POSITIONS_FILE, "w") as f:
                json.dump({
                    "positions":      positions,
                    "date":           str(datetime.now().date()),
                    "daily_count":    self.daily_count,
                    "daily_risk_pct": self.daily_risk_pct,
                    "saved_at":       datetime.now().isoformat(),
                }, f, indent=2)
        except Exception as e:
            print(f"  ⚠️  Could not save positions: {e}")

    def _try_reset(self):
        today = datetime.now().date()
        if today != self._reset_day:
            self._reset_day     = today
            self.daily_count    = 0
            self.daily_risk_pct = 0.0
            self.daily_pnl      = 0.0
            print(f"\n📅 Daily counters reset ({today})")

    def can_trade_today(self) -> Tuple[bool, str]:
        self._try_reset()
        if self.daily_count >= MAX_DAILY_TRADES:
            return False, f"Daily trade limit ({self.daily_count}/{MAX_DAILY_TRADES})"
        if self.daily_risk_pct >= MAX_DAILY_RISK_PCT:
            return False, f"Daily risk limit ({self.daily_risk_pct:.1f}%)"
        return True, "OK"

    def can_trade_state(self, state: str, symbol: str) -> Tuple[bool, int]:
        key = f"{state}__{symbol}"
        last_t = self._state_cooldown.get(key, 0.0)
        elapsed = time.monotonic() - last_t
        cooldown = STATE_COOLDOWNS.get(state, BASE_COOLDOWN)
        if elapsed < cooldown:
            return False, int(cooldown - elapsed)
        return True, 0

    def register(self, trade: TradeRecord) -> bool:
        ok, reason = self.can_trade_today()
        if not ok:
            print(f"   ⏸️  {reason}"); return False

        ok, secs = self.can_trade_state(trade.state, trade.symbol)
        if not ok:
            print(f"   ⏸️  State cooldown {secs}s"); return False

        self.open[trade.ticket] = trade
        key = f"{trade.state}__{trade.symbol}"
        self._state_cooldown[key] = time.monotonic()
        self.daily_count    += 1
        self.daily_risk_pct += trade.risk_percent
        self._save_open_positions()
        return True

    def close(self, ticket: int, profit: float, exit_price: float, realized_r: float):
        if ticket not in self.open:
            return None
        t = self.open.pop(ticket)
        t.status   = "closed"
        self.daily_pnl += profit
        self.history.append(t)
        self._save_open_positions()
        return t

    def sync(self):
        try:
            positions = mt5.positions_get()
            live_tix  = {p.ticket for p in positions} if positions else set()
            for tix in list(self.open):
                if tix not in live_tix:
                    stale = self.open.pop(tix)
                    stale.status = "closed"
                    self.history.append(stale)
        except Exception:
            pass

    @property
    def open_symbols(self) -> Dict[str, str]:
        return {t.symbol: t.signal for t in self.open.values()}

    @property
    def open_count(self) -> int:
        return len(self.open)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    ╔══════════════════════════════════════════════════════════════════════╗
    ║  TECHNICAL INDICATOR SUITE — 40+ calculations                       ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    df = df.copy()
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    open_ = df["open"]

    df["ema_9"]   = close.ewm(span=9,   adjust=False).mean()
    df["ema_21"]  = close.ewm(span=21,  adjust=False).mean()
    df["ema_50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()
    df["sma_200"] = close.rolling(200, min_periods=50).mean()

    ema21  = close.ewm(span=21, adjust=False).mean()
    df["dema_21"] = 2 * ema21 - ema21.ewm(span=21, adjust=False).mean()

    half_period = 10
    wma_half = close.rolling(half_period, min_periods=1).mean()
    wma_full = close.rolling(21, min_periods=1).mean()
    hull_raw = 2 * wma_half - wma_full
    df["hma_21"] = hull_raw.rolling(int(np.sqrt(21)), min_periods=1).mean()

    df["ema_bull_stack"] = (
        (close > df["ema_9"]).astype(int)  +
        (df["ema_9"]  > df["ema_21"]).astype(int) +
        (df["ema_21"] > df["ema_50"]).astype(int) +
        (df["ema_50"] > df["ema_200"]).astype(int)
    )
    df["ema_bear_stack"] = (
        (close < df["ema_9"]).astype(int)  +
        (df["ema_9"]  < df["ema_21"]).astype(int) +
        (df["ema_21"] < df["ema_50"]).astype(int) +
        (df["ema_50"] < df["ema_200"]).astype(int)
    )

    df["golden_cross"] = (df["ema_21"] > df["ema_50"]) & (df["ema_21"].shift() <= df["ema_50"].shift())
    df["death_cross"]  = (df["ema_21"] < df["ema_50"]) & (df["ema_21"].shift() >= df["ema_50"].shift())

    hl   = high - low
    hc   = (high - close.shift()).abs()
    lc   = (low  - close.shift()).abs()
    df["tr"]           = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"]          = df["tr"].rolling(14, min_periods=1).mean()
    df["atr_percent"]  = (df["atr"] / close.replace(0, np.nan) * 100).fillna(0.030)
    df["atr_sma"]      = df["atr"].rolling(20, min_periods=1).mean().replace(0, np.nan).ffill().bfill()
    df["atr_expansion"]= (df["atr"] / df["atr_sma"]).clip(0.5, 2.5).fillna(1.0)
    df["atr_pct_rank"] = (
        df["atr_percent"]
        .rolling(ATR_PERCENTILE_WINDOW, min_periods=1)
        .rank(pct=True)
        .fillna(0.5)
    )

    bb_mid         = close.rolling(20, min_periods=1).mean()
    bb_std         = close.rolling(20, min_periods=1).std().fillna(0)
    df["bb_mid"]   = bb_mid
    df["bb_upper"] = bb_mid + 2.0 * bb_std
    df["bb_lower"] = bb_mid - 2.0 * bb_std
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid.replace(0, 1)
    bb_rng         = (df["bb_upper"] - df["bb_lower"]).replace(0, 1e-8)
    df["bb_pos"]   = ((close - df["bb_lower"]) / bb_rng).clip(0, 1).fillna(0.5)
    df["bb_squeeze"]= df["bb_width"] < df["bb_width"].rolling(20, min_periods=1).mean() * 0.75

    kc_mid          = close.ewm(span=20, adjust=False).mean()
    df["kc_mid"]    = kc_mid
    df["kc_upper"]  = kc_mid + 1.5 * df["atr"]
    df["kc_lower"]  = kc_mid - 1.5 * df["atr"]

    df["squeeze_momentum"] = (
        (df["bb_upper"] < df["kc_upper"]) &
        (df["bb_lower"] > df["kc_lower"])
    )

    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss   = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs     = gain / loss.replace(0, 1e-9)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50)
    df["rsi_signal"] = df["rsi"].ewm(span=3, adjust=False).mean()
    df["rsi_bull_div"] = (close > close.shift(5)) & (df["rsi"] < df["rsi"].shift(5))
    df["rsi_bear_div"] = (close < close.shift(5)) & (df["rsi"] > df["rsi"].shift(5))

    lo14  = low.rolling(14,  min_periods=1).min()
    hi14  = high.rolling(14, min_periods=1).max()
    k_raw = (close - lo14) / (hi14 - lo14).replace(0, 1e-8) * 100
    df["stoch_k"] = k_raw.rolling(3, min_periods=1).mean().clip(0, 100).fillna(50)
    df["stoch_d"] = df["stoch_k"].rolling(3, min_periods=1).mean().fillna(50)
    df["stoch_bull_cross"] = (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k"].shift() <= df["stoch_d"].shift())
    df["stoch_bear_cross"] = (df["stoch_k"] < df["stoch_d"]) & (df["stoch_k"].shift() >= df["stoch_d"].shift())

    df["williams_r"] = ((hi14 - close) / (hi14 - lo14).replace(0, 1e-8) * -100).clip(-100, 0).fillna(-50)

    tp    = (high + low + close) / 3
    tp_ma = tp.rolling(20, min_periods=1).mean()
    tp_md = tp.rolling(20, min_periods=1).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    df["cci"] = ((tp - tp_ma) / (0.015 * tp_md.replace(0, 1e-8))).fillna(0)

    df["roc_10"] = close.pct_change(10).mul(100).fillna(0)
    df["roc_5"]  = close.pct_change(5).mul(100).fillna(0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"]        = (ema12 - ema26).fillna(0)
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean().fillna(0)
    df["macd_hist"]   = (df["macd"] - df["macd_signal"]).fillna(0)
    df["macd_bull_cross"] = (df["macd"] > df["macd_signal"]) & (df["macd"].shift() <= df["macd_signal"].shift())
    df["macd_bear_cross"] = (df["macd"] < df["macd_signal"]) & (df["macd"].shift() >= df["macd_signal"].shift())
    df["macd_hist_rising"] = df["macd_hist"] > df["macd_hist"].shift()

    vol_col = "tick_volume" if "tick_volume" in df.columns else "real_volume"
    if vol_col in df.columns:
        vol = df[vol_col].fillna(0)
        df["vol_sma"]      = vol.rolling(20, min_periods=1).mean()
        df["volume_ratio"] = (vol / df["vol_sma"].replace(0, 1)).clip(0.3, 4.0)
        obv = (np.where(close > close.shift(), vol,
               np.where(close < close.shift(), -vol, 0)))
        df["obv"]        = pd.Series(obv, index=df.index).cumsum()
        df["obv_signal"] = df["obv"].ewm(span=10, adjust=False).mean()
        df["obv_bull"]   = df["obv"] > df["obv_signal"]
        mf  = tp * vol
        mf_pos = mf.where(tp > tp.shift(), 0).rolling(14, min_periods=1).sum()
        mf_neg = mf.where(tp < tp.shift(), 0).rolling(14, min_periods=1).sum()
        df["mfi"] = (100 - 100 / (1 + mf_pos / mf_neg.replace(0, 1e-9))).fillna(50)
        df["vwap"] = (tp * vol).rolling(50, min_periods=1).sum() / vol.rolling(50, min_periods=1).sum().replace(0, 1)
        df["above_vwap"] = close > df["vwap"]
        df["vol_spike"] = df["volume_ratio"] >= 2.0
    else:
        df["volume_ratio"]  = 1.0
        df["obv"]           = 0.0
        df["obv_bull"]      = False
        df["mfi"]           = 50.0
        df["vwap"]          = close
        df["above_vwap"]    = True
        df["vol_spike"]     = False

    df["candle_range"]    = high - low
    df["upper_wick"]      = high - df[["close", "open"]].max(axis=1)
    df["lower_wick"]      = df[["close", "open"]].min(axis=1) - low
    safe_rng              = df["candle_range"].replace(0, 1e-8)
    df["upper_wick_ratio"]= (df["upper_wick"] / safe_rng).fillna(0)
    df["lower_wick_ratio"]= (df["lower_wick"] / safe_rng).fillna(0)
    df["long_upper_wick"] = df["upper_wick_ratio"] > 0.55
    df["long_lower_wick"] = df["lower_wick_ratio"] > 0.55
    df["body_pct"]        = (abs(close - open_) / safe_rng).fillna(0.5)
    df["close_position"]  = ((close - low) / safe_rng).fillna(0.5)

    df["csi_bull"] = df["body_pct"] * df["close_position"]
    df["csi_bear"] = df["body_pct"] * (1 - df["close_position"])

    df["avg_range_5"] = df["candle_range"].rolling(5, min_periods=1).mean().replace(0, 1e-8)
    df["candle_vel"]  = (df["candle_range"] / df["avg_range_5"]).clip(0, 5)

    df["range_high_20"] = high.rolling(20, min_periods=1).max()
    df["range_low_20"]  = low.rolling(20,  min_periods=1).min()
    df["range_width"]   = (df["range_high_20"] - df["range_low_20"]).fillna(0)
    df["momentum"]      = close.pct_change(5).mul(100).fillna(0)

    rw = (df["range_high_20"] - df["range_low_20"]).replace(0, 1e-8)
    df["range_position"] = ((close - df["range_low_20"]) / rw * 100).clip(0, 100).fillna(50)

    df["swing_high_50"] = high.rolling(50, min_periods=1).max()
    df["swing_low_50"]  = low.rolling(50,  min_periods=1).min()
    df["liq_sweep_up"]  = ((high > df["swing_high_50"].shift()) & (close < df["swing_high_50"].shift())).fillna(False)
    df["liq_sweep_dn"]  = ((low  < df["swing_low_50"].shift())  & (close > df["swing_low_50"].shift())).fillna(False)

    df["bull_confluence"] = (
        (close > df["ema_21"]) &
        (df["ema_21"] > df["ema_50"]) &
        (df["macd_hist"] > 0) &
        (df["rsi"] > 45) & (df["rsi"] < 75) &
        df["obv_bull"]
    ).astype(int)

    df["bear_confluence"] = (
        (close < df["ema_21"]) &
        (df["ema_21"] < df["ema_50"]) &
        (df["macd_hist"] < 0) &
        (df["rsi"] < 55) & (df["rsi"] > 25) &
        (~df["obv_bull"])
    ).astype(int)

    t_high = high.rolling(9,  min_periods=1).max()
    t_low  = low.rolling(9,   min_periods=1).min()
    df["ichimoku_tenkan"]  = (t_high + t_low) / 2

    k_high = high.rolling(26, min_periods=1).max()
    k_low  = low.rolling(26,  min_periods=1).min()
    df["ichimoku_kijun"]   = (k_high + k_low) / 2

    df["ichimoku_span_a"]  = ((df["ichimoku_tenkan"] + df["ichimoku_kijun"]) / 2).shift(26)

    s_high = high.rolling(52, min_periods=1).max()
    s_low  = low.rolling(52,  min_periods=1).min()
    df["ichimoku_span_b"]  = ((s_high + s_low) / 2).shift(26)

    df["ichimoku_chikou"]  = close.shift(-26)

    span_a_now = ((df["ichimoku_tenkan"] + df["ichimoku_kijun"]) / 2)
    span_b_now = (s_high + s_low) / 2
    cloud_top  = pd.concat([span_a_now, span_b_now], axis=1).max(axis=1)
    cloud_bot  = pd.concat([span_a_now, span_b_now], axis=1).min(axis=1)

    df["above_cloud"]      = close > cloud_top
    df["below_cloud"]      = close < cloud_bot
    df["in_cloud"]         = ~df["above_cloud"] & ~df["below_cloud"]
    df["cloud_bullish"]    = span_a_now > span_b_now
    df["tk_cross_bull"]    = (df["ichimoku_tenkan"] > df["ichimoku_kijun"]) & \
                             (df["ichimoku_tenkan"].shift() <= df["ichimoku_kijun"].shift())
    df["tk_cross_bear"]    = (df["ichimoku_tenkan"] < df["ichimoku_kijun"]) & \
                             (df["ichimoku_tenkan"].shift() >= df["ichimoku_kijun"].shift())

    bear_candle = close < open_
    bull_candle = close > open_
    big_move_up   = (close - close.shift(3)) > df["atr"] * 1.5
    big_move_down = (close.shift(3) - close) > df["atr"] * 1.5

    # NOTE: fillna(method="ffill") is deprecated in newer pandas — using .ffill() instead
    df["bull_ob_high"] = high.where(bear_candle.shift(1) & big_move_up).ffill()
    df["bull_ob_low"]  = low.where(bear_candle.shift(1)  & big_move_up).ffill()
    df["at_bull_ob"]   = (close >= df["bull_ob_low"]) & (close <= df["bull_ob_high"])

    df["bear_ob_high"] = high.where(bull_candle.shift(1) & big_move_down).ffill()
    df["bear_ob_low"]  = low.where(bull_candle.shift(1)  & big_move_down).ffill()
    df["at_bear_ob"]   = (close >= df["bear_ob_low"]) & (close <= df["bear_ob_high"])

    df["demand_zone_high"] = low.rolling(50,  min_periods=10).min() + df["atr"] * 0.5
    df["demand_zone_low"]  = low.rolling(50,  min_periods=10).min() - df["atr"] * 0.3
    df["supply_zone_low"]  = high.rolling(50, min_periods=10).max() - df["atr"] * 0.5
    df["supply_zone_high"] = high.rolling(50, min_periods=10).max() + df["atr"] * 0.3

    df["in_demand_zone"] = (close >= df["demand_zone_low"]) & (close <= df["demand_zone_high"])
    df["in_supply_zone"] = (close >= df["supply_zone_low"]) & (close <= df["supply_zone_high"])

    df["macd_bull_div"] = (close < close.shift(5)) & (df["macd_hist"] > df["macd_hist"].shift(5))
    df["macd_bear_div"] = (close > close.shift(5)) & (df["macd_hist"] < df["macd_hist"].shift(5))

    df["bull_confluence_score"] = (
        df["ema_bull_stack"].clip(0, 4) +
        df["above_vwap"].astype(int) * 2 +
        (df["rsi"] < 50).astype(int) * 2 +
        (df["macd_hist"] > 0).astype(int) * 2 +
        df["obv_bull"].astype(int) * 2 +
        df["above_cloud"].astype(int) * 3 +
        df["tk_cross_bull"].astype(int) * 3 +
        df["at_bull_ob"].astype(int) * 3 +
        df["in_demand_zone"].astype(int) * 2 +
        df["bull_confluence"].astype(int) * 2 +
        (df["rsi_bull_div"] | df["macd_bull_div"]).astype(int) * 4
    ).clip(0, 30)

    df["bear_confluence_score"] = (
        df["ema_bear_stack"].clip(0, 4) +
        (~df["above_vwap"]).astype(int) * 2 +
        (df["rsi"] > 50).astype(int) * 2 +
        (df["macd_hist"] < 0).astype(int) * 2 +
        (~df["obv_bull"]).astype(int) * 2 +
        df["below_cloud"].astype(int) * 3 +
        df["tk_cross_bear"].astype(int) * 3 +
        df["at_bear_ob"].astype(int) * 3 +
        df["in_supply_zone"].astype(int) * 2 +
        df["bear_confluence"].astype(int) * 2 +
        (df["rsi_bear_div"] | df["macd_bear_div"]).astype(int) * 4
    ).clip(0, 30)

    return df.bfill().ffill()


def pre_entry_candle_study(symbol: str, df: pd.DataFrame,
                            signal: str, candles: Dict,
                            smc: Dict, structure: str,
                            confidence: int, opp: int) -> int:
    last  = df.iloc[-1]
    arrow = "📈 BUY" if signal == "buy" else "📉 SELL"
    is_buy = signal == "buy"

    print(f"\n  {'═'*62}")
    print(f"  🔬 PRE-ENTRY CANDLE STUDY  —  {symbol}  {arrow}")
    print(f"  {'═'*62}")

    body_pct  = candles.get("body_pct", 0) * 100
    up_wick   = candles.get("upper_wick_pct", 0) * 100
    dn_wick   = candles.get("lower_wick_pct", 0) * 100
    velocity  = candles.get("candle_velocity", 1.0)
    close_pos = candles.get("close_position", 0.5) * 100
    csi_b     = float(last.get("csi_bull", 0))
    csi_s     = float(last.get("csi_bear", 0))

    print(f"\n  🕯️  CANDLE ANATOMY")
    print(f"     Body:       {body_pct:.0f}%   {'█' * min(20, int(body_pct/5))}")
    print(f"     Upper wick: {up_wick:.0f}%   {'▲' * min(20, int(up_wick/5))}")
    print(f"     Lower wick: {dn_wick:.0f}%   {'▼' * min(20, int(dn_wick/5))}")
    print(f"     Close pos:  {close_pos:.0f}%  (0=low end, 100=high end)")
    print(f"     Velocity:   {velocity:.1f}× average range")
    csi_icon = "💚 bullish" if csi_b > 0.5 else ("❤️  bearish" if csi_s > 0.5 else "⬜ neutral")
    print(f"     CSI:        {csi_icon}  ({csi_b:.2f} / {csi_s:.2f})")

    print(f"\n  🏷️  PATTERNS DETECTED")
    top_p = candles.get("top_patterns", [])
    if top_p:
        for p in top_p:
            print(f"     ✅ {p}")
    else:
        print(f"     — No patterns detected this candle")
    grade      = candles.get("candle_grade", "F")
    score      = candles.get("candle_score", 0)
    bias       = candles.get("candle_bias", "neutral")
    grade_icon = {"A":"🟢","B":"🟡","C":"🟠","D":"🔴","F":"⛔"}.get(grade,"⬜")
    print(f"     Grade: {grade_icon} {grade}   Score: {score}/100   Bias: {bias}")

    rsi_val    = float(last.get("rsi", 50))
    stoch_k    = float(last.get("stoch_k", 50))
    macd_h     = float(last.get("macd_hist", 0))
    bb_pos     = float(last.get("bb_pos", 0.5))
    above_vwap = bool(last.get("above_vwap", True))
    ema_stack  = int(last.get("ema_bull_stack", 0))
    ema_bstk   = int(last.get("ema_bear_stack", 0))
    mfi_val    = float(last.get("mfi", 50))
    cci_val    = float(last.get("cci", 0))
    wr_val     = float(last.get("williams_r", -50))
    obv_bull   = bool(last.get("obv_bull", True))
    sq_active  = bool(last.get("squeeze_momentum", False))
    rsi_div_b  = bool(last.get("rsi_bull_div", False))
    rsi_div_s  = bool(last.get("rsi_bear_div", False))
    mac_div_b  = bool(last.get("macd_bull_div", False))
    mac_div_s  = bool(last.get("macd_bear_div", False))
    above_cloud= bool(last.get("above_cloud", True))
    below_cloud= bool(last.get("below_cloud", False))
    cloud_bull = bool(last.get("cloud_bullish", True))
    tk_bull    = bool(last.get("tk_cross_bull", False))
    tk_bear    = bool(last.get("tk_cross_bear", False))
    at_bull_ob = bool(last.get("at_bull_ob", False))
    at_bear_ob = bool(last.get("at_bear_ob", False))
    in_demand  = bool(last.get("in_demand_zone", False))
    in_supply  = bool(last.get("in_supply_zone", False))
    bull_cs    = int(last.get("bull_confluence_score", 0))
    bear_cs    = int(last.get("bear_confluence_score", 0))

    def yn(cond): return "✅" if cond else "❌"

    print(f"\n  📊 TECHNICAL INDICATORS")
    print(f"     RSI {rsi_val:.1f}        {yn(rsi_val<50 if is_buy else rsi_val>50)}  "
          f"{'Oversold' if rsi_val<30 else 'Overbought' if rsi_val>70 else 'Neutral'}")
    print(f"     Stoch {stoch_k:.1f}      {yn(stoch_k<80 if is_buy else stoch_k>20)}")
    print(f"     MACD {macd_h:+.6f}  {yn(macd_h>0 if is_buy else macd_h<0)}")
    print(f"     BB pos {bb_pos:.2f}     {yn(bb_pos<0.35 if is_buy else bb_pos>0.65)}  "
          f"{'Near lower band' if bb_pos<0.25 else 'Near upper band' if bb_pos>0.75 else 'Mid range'}")
    print(f"     VWAP          {yn(above_vwap if is_buy else not above_vwap)}  "
          f"Price {'above' if above_vwap else 'below'} VWAP")
    stk = ema_stack if is_buy else ema_bstk
    print(f"     EMA stack {stk}/4  {yn(stk>=2)}  "
          f"{'Strong trend' if stk>=3 else 'Moderate' if stk==2 else 'Weak/None'}")
    print(f"     MFI {mfi_val:.0f}        {yn(mfi_val<50 if is_buy else mfi_val>50)}")
    print(f"     CCI {cci_val:+.0f}       {yn(cci_val<0 if is_buy else cci_val>0)}")
    print(f"     Williams {wr_val:.0f}   {yn(wr_val<-50 if is_buy else wr_val>-50)}")
    print(f"     OBV           {yn(obv_bull if is_buy else not obv_bull)}  "
          f"{'Accumulation' if obv_bull else 'Distribution'}")
    print(f"     Squeeze       {'🔥 ACTIVE — breakout imminent' if sq_active else '— Inactive'}")

    print(f"\n  🔀 DIVERGENCE (highest-probability reversal signals)")
    if is_buy:
        if rsi_div_b:
            print(f"     ✅ RSI BULLISH DIVERGENCE — price fell but RSI rose")
            print(f"        → Sellers losing strength, reversal likely UP")
        else:
            print(f"     ❌ No RSI bullish divergence")
        if mac_div_b:
            print(f"     ✅ MACD BULLISH DIVERGENCE — histogram turning up vs price")
        else:
            print(f"     ❌ No MACD bullish divergence")
    else:
        if rsi_div_s:
            print(f"     ✅ RSI BEARISH DIVERGENCE — price rose but RSI fell")
            print(f"        → Buyers losing strength, reversal likely DOWN")
        else:
            print(f"     ❌ No RSI bearish divergence")
        if mac_div_s:
            print(f"     ✅ MACD BEARISH DIVERGENCE — histogram turning down vs price")
        else:
            print(f"     ❌ No MACD bearish divergence")

    print(f"\n  ☁️  ICHIMOKU CLOUD")
    cloud_pos = "ABOVE ✅ (bullish)" if above_cloud else \
                ("BELOW ❌ (bearish)" if below_cloud else "INSIDE ⚠️  (no-trade zone)")
    print(f"     Price vs cloud:  {cloud_pos}")
    print(f"     Cloud colour:    {'Green 💚 (bullish)' if cloud_bull else 'Red ❤️  (bearish)'}")
    if tk_bull:
        print(f"     ✅ TK CROSS UP — Tenkan crossed above Kijun (bullish signal)")
    if tk_bear:
        print(f"     ✅ TK CROSS DN — Tenkan crossed below Kijun (bearish signal)")
    cloud_ok = (above_cloud and is_buy) or (below_cloud and not is_buy)
    print(f"     Cloud aligned:   {'✅ YES' if cloud_ok else '⚠️  NO — price inside/against cloud'}")

    print(f"\n  🏛️  ORDER BLOCKS & SUPPLY/DEMAND ZONES")
    if is_buy:
        print(f"     Bull order block: {yn(at_bull_ob)}  "
              f"{'In institutional buy zone — strong support' if at_bull_ob else '—'}")
        print(f"     Demand zone:      {yn(in_demand)}  "
              f"{'Near 50-bar lows — buyers historically active here' if in_demand else '—'}")
    else:
        print(f"     Bear order block: {yn(at_bear_ob)}  "
              f"{'In institutional sell zone — strong resistance' if at_bear_ob else '—'}")
        print(f"     Supply zone:      {yn(in_supply)}  "
              f"{'Near 50-bar highs — sellers historically active here' if in_supply else '—'}")

    print(f"\n  🧠 SMC — SMART MONEY CONCEPTS")
    print(f"     At EQ (equilibrium): {yn(smc.get('at_eq',False))}")
    bos_hit = smc.get('bull_bos',False) if is_buy else smc.get('bear_bos',False)
    fvg_hit = smc.get('bull_fvg',False) if is_buy else smc.get('bear_fvg',False)
    print(f"     Break of Structure:  {yn(bos_hit)}")
    print(f"     Fair Value Gap:      {yn(fvg_hit)}")
    smc_count = sum([smc.get('at_eq',False), bos_hit, fvg_hit])
    print(f"     SMC components:      {smc_count}/3  "
          f"{'✅ Full confluence' if smc_count==3 else '~ Partial' if smc_count>0 else '❌ None'}")
    print(f"     Market structure:    {structure}")

    print(f"\n  ✅ PRE-ENTRY CHECKLIST")
    cl_key    = f"checklist_{signal}"
    checklist = candles.get(cl_key, {})
    cl_score  = candles.get(f"checklist_{signal}_score", 0)
    for item, passed in checklist.items():
        print(f"     {'✅' if passed else '❌'} {item}")
    print(f"     Checklist: {cl_score}/10  "
          f"{'✅ PASS' if cl_score>=6 else '⚠️  MARGINAL' if cl_score>=5 else '❌ FAIL'}")

    confluence_count = 0
    if is_buy:
        checks = [
            (rsi_val < 55,                 "RSI below 55"),
            (macd_h > 0,                   "MACD positive"),
            (above_vwap,                   "Above VWAP"),
            (ema_stack >= 2,               "EMA bull stack ≥2"),
            (above_cloud,                  "Above Ichimoku cloud"),
            (obv_bull,                     "OBV accumulation"),
            (at_bull_ob,                   "In bull order block"),
            (in_demand,                    "In demand zone"),
            (rsi_div_b or mac_div_b,       "Bullish divergence"),
            (tk_bull,                      "TK cross bullish"),
            (bool(top_p),                  "Pattern detected"),
            (smc_count >= 1,               "SMC confirmation"),
        ]
    else:
        checks = [
            (rsi_val > 45,                 "RSI above 45"),
            (macd_h < 0,                   "MACD negative"),
            (not above_vwap,               "Below VWAP"),
            (ema_bstk >= 2,                "EMA bear stack ≥2"),
            (below_cloud,                  "Below Ichimoku cloud"),
            (not obv_bull,                 "OBV distribution"),
            (at_bear_ob,                   "In bear order block"),
            (in_supply,                    "In supply zone"),
            (rsi_div_s or mac_div_s,       "Bearish divergence"),
            (tk_bear,                      "TK cross bearish"),
            (bool(top_p),                  "Pattern detected"),
            (smc_count >= 1,               "SMC confirmation"),
        ]

    confluence_count = sum(1 for cond, _ in checks if cond)
    conf_sc = bull_cs if is_buy else bear_cs

    print(f"\n  🎯 FINAL VERDICT")
    print(f"     Confluence score:  {conf_sc}/30  {'█' * (conf_sc//2)}")
    print(f"     Signals agreeing:  {confluence_count}/12")
    quality = ("🟢 HIGH QUALITY — strong edge" if confluence_count >= 8 else
               "🟡 MEDIUM — acceptable setup" if confluence_count >= 5 else
               "🔴 LOW — weak setup, caution")
    print(f"     Trade quality:     {quality}")
    print(f"     Confidence:        {confidence}  |  Opportunity: {opp}")
    print(f"  {'═'*62}\n")

    return confluence_count


def smc_equilibrium(df: pd.DataFrame, lookback: int = 20) -> float:
    tail = df.tail(lookback)
    return (float(tail["high"].max()) + float(tail["low"].min())) / 2.0


def smc_at_equilibrium(price: float, eq: float, atr: float) -> bool:
    tolerance = atr * 0.30
    return abs(price - eq) <= tolerance


def smc_break_of_structure(df: pd.DataFrame, lookback: int = 10) -> Tuple[bool, bool]:
    hist         = df.iloc[-(lookback + 1):-1]
    current      = float(df.iloc[-1]["close"])
    swing_high   = float(hist["high"].max())
    swing_low    = float(hist["low"].min())

    bullish_bos = current > swing_high
    bearish_bos = current < swing_low
    return bullish_bos, bearish_bos


def smc_fair_value_gap(df: pd.DataFrame) -> Tuple[bool, bool]:
    if len(df) < 4:
        return False, False

    c1 = df.iloc[-3]
    c3 = df.iloc[-1]

    bull_fvg = float(c1["high"]) < float(c3["low"])
    bear_fvg = float(c1["low"])  > float(c3["high"])
    return bull_fvg, bear_fvg


def smc_analysis(df: pd.DataFrame) -> Dict:
    if len(df) < 25:
        return {"valid": False, "score": 0, "buy": False, "sell": False}

    price  = float(df.iloc[-1]["close"])
    atr    = float(df.iloc[-1]["atr"])
    eq     = smc_equilibrium(df, 20)
    at_eq  = smc_at_equilibrium(price, eq, atr)
    bull_bos, bear_bos = smc_break_of_structure(df, 10)
    bull_fvg, bear_fvg = smc_fair_value_gap(df)

    smc_buy  = bull_bos and at_eq and bull_fvg
    smc_sell = bear_bos and at_eq and bear_fvg

    score = 0
    if at_eq:              score += 30
    if bull_bos:           score += 25
    if bear_bos:           score += 25
    if bull_fvg:           score += 25
    if bear_fvg:           score += 25
    if smc_buy or smc_sell: score = min(100, score + 20)
    if bull_bos and bear_bos: score = max(0, score - 30)

    eq_dist_pct = abs(price - eq) / atr if atr > 0 else 1.0

    return {
        "valid":       True,
        "eq":          round(eq, 5),
        "at_eq":       at_eq,
        "eq_dist_pct": round(eq_dist_pct, 2),
        "bull_bos":    bull_bos,
        "bear_bos":    bear_bos,
        "bull_fvg":    bull_fvg,
        "bear_fvg":    bear_fvg,
        "smc_buy":     smc_buy,
        "smc_sell":    smc_sell,
        "score":       min(100, score),
    }


def candle_intelligence(df: pd.DataFrame) -> Dict:
    empty = {
        "bull_engulf": False, "bear_engulf": False,
        "bull_pin": False, "bear_pin": False,
        "inside_bar": False, "outside_bar": False,
        "bull_marubozu": False, "bear_marubozu": False,
        "doji": False, "gravestone_doji": False, "dragonfly_doji": False,
        "morning_star": False, "evening_star": False,
        "three_white_soldiers": False, "three_black_crows": False,
        "tweezer_bottom": False, "tweezer_top": False,
        "rising_three": False, "falling_three": False,
        "body_pct": 0.0, "upper_wick_pct": 0.0, "lower_wick_pct": 0.0,
        "bull_momentum": 0, "bear_momentum": 0, "momentum_accel": False,
        "vol_on_signal": 0.0, "candle_velocity": 0.0, "close_position": 0.5,
        "rejection_up": False, "rejection_down": False,
        "trend_5": "flat", "trend_10": "flat",
        "rsi_zone": "neutral", "stoch_cross": "none",
        "macd_aligned": False, "above_vwap": False,
        "ema_stack": 0, "bb_position": 0.5, "squeeze_active": False,
        "obv_aligned": False, "mfi_zone": "neutral",
        "cci_signal": "neutral", "williams_signal": "neutral",
        "vol_spike": False, "csi": 0.0,
        "checklist_bull": {}, "checklist_bear": {},
        "checklist_bull_score": 0, "checklist_bear_score": 0,
        "candle_score": 0, "candle_bias": "neutral",
        "candle_grade": "F", "top_patterns": [],
        "pattern_score": 0,
    }

    if len(df) < 20:
        return empty

    c  = df.iloc[-1]
    p1 = df.iloc[-2]
    p2 = df.iloc[-3]
    p3 = df.iloc[-4]
    p4 = df.iloc[-5]

    def o(r):   return float(r["open"])
    def cl(r):  return float(r["close"])
    def h(r):   return float(r["high"])
    def l(r):   return float(r["low"])
    def rng(r): return h(r) - l(r) or 1e-8
    def body(r):return abs(cl(r) - o(r))
    def bull(r):return cl(r) > o(r)
    def bear(r):return cl(r) < o(r)

    cur_range    = rng(c)
    cur_body     = body(c)
    cur_body_pct = cur_body / cur_range
    upper_wick   = h(c) - max(o(c), cl(c))
    lower_wick   = min(o(c), cl(c)) - l(c)
    upper_wick_p = upper_wick / cur_range
    lower_wick_p = lower_wick / cur_range
    close_pos    = (cl(c) - l(c)) / cur_range

    bull_engulf = (bear(p1) and bull(c) and o(c) <= cl(p1) and cl(c) >= o(p1) and cur_body > body(p1))
    bear_engulf = (bull(p1) and bear(c) and o(c) >= cl(p1) and cl(c) <= o(p1) and cur_body > body(p1))

    bull_pin = (lower_wick_p >= 0.60 and cur_body_pct <= 0.30 and upper_wick_p <= 0.15)
    bear_pin = (upper_wick_p >= 0.60 and cur_body_pct <= 0.30 and lower_wick_p <= 0.15)

    inside_bar  = h(c) <= h(p1) and l(c) >= l(p1)
    outside_bar = h(c) > h(p1) and l(c) < l(p1)

    bull_marubozu = (bull(c) and cur_body_pct >= 0.85 and lower_wick_p <= 0.08 and upper_wick_p <= 0.08)
    bear_marubozu = (bear(c) and cur_body_pct >= 0.85 and lower_wick_p <= 0.08 and upper_wick_p <= 0.08)

    doji = cur_body_pct <= 0.10
    gravestone_doji = doji and upper_wick_p >= 0.65 and lower_wick_p <= 0.15
    dragonfly_doji  = doji and lower_wick_p >= 0.65 and upper_wick_p <= 0.15

    morning_star = (bear(p2) and body(p2)/rng(p2) >= 0.60 and
                    body(p1)/rng(p1) <= 0.30 and
                    bull(c) and cur_body_pct >= 0.50 and
                    cl(c) > (o(p2) + cl(p2)) / 2)

    evening_star = (bull(p2) and body(p2)/rng(p2) >= 0.60 and
                    body(p1)/rng(p1) <= 0.30 and
                    bear(c) and cur_body_pct >= 0.50 and
                    cl(c) < (o(p2) + cl(p2)) / 2)

    three_white_soldiers = (
        bull(c) and bull(p1) and bull(p2) and
        cl(c) > cl(p1) > cl(p2) and o(c) > o(p1) > o(p2) and
        all(body(df.iloc[-i])/rng(df.iloc[-i]) >= 0.60 for i in range(1, 4))
    )

    three_black_crows = (
        bear(c) and bear(p1) and bear(p2) and
        cl(c) < cl(p1) < cl(p2) and o(c) < o(p1) < o(p2) and
        all(body(df.iloc[-i])/rng(df.iloc[-i]) >= 0.60 for i in range(1, 4))
    )

    tweezer_bottom = bear(p1) and bull(c) and abs(l(c) - l(p1)) / rng(p1) <= 0.10
    tweezer_top    = bull(p1) and bear(c) and abs(h(c) - h(p1)) / rng(p1) <= 0.10

    rising_three  = (bull(p4) and body(p4)/rng(p4) >= 0.65 and
                     bear(p3) and body(p3)/rng(p3) <= 0.40 and
                     bear(p2) and body(p2)/rng(p2) <= 0.40 and
                     bull(c)  and cl(c) > h(p4))

    falling_three = (bear(p4) and body(p4)/rng(p4) >= 0.65 and
                     bull(p3) and body(p3)/rng(p3) <= 0.40 and
                     bull(p2) and body(p2)/rng(p2) <= 0.40 and
                     bear(c)  and cl(c) < l(p4))

    rsi = float(c.get("rsi", 50))
    if rsi < 30:   rsi_zone = "oversold"
    elif rsi < 45: rsi_zone = "bull_zone"
    elif rsi < 55: rsi_zone = "neutral"
    elif rsi < 70: rsi_zone = "bear_zone"
    else:          rsi_zone = "overbought"

    sk = float(c.get("stoch_k", 50)); sd = float(c.get("stoch_d", 50))
    pk = float(p1.get("stoch_k", 50)); pd_ = float(p1.get("stoch_d", 50))
    if sk > sd and pk <= pd_ and sk < 80: stoch_cross = "bull"
    elif sk < sd and pk >= pd_ and sk > 20: stoch_cross = "bear"
    else: stoch_cross = "none"

    macd_h  = float(c.get("macd_hist", 0))
    macd_h1 = float(p1.get("macd_hist", 0))
    macd_bull = macd_h > 0 and macd_h > macd_h1
    macd_bear = macd_h < 0 and macd_h < macd_h1

    above_vwap = bool(c.get("above_vwap", True))

    ema_stack  = int(c.get("ema_bull_stack", 0))
    ema_bstack = int(c.get("ema_bear_stack", 0))

    bb_pos = float(c.get("bb_pos", 0.5))

    squeeze_active = bool(c.get("squeeze_momentum", False))

    obv_bull = bool(c.get("obv_bull", True))

    mfi = float(c.get("mfi", 50))
    if mfi < 20:   mfi_zone = "oversold"
    elif mfi > 80: mfi_zone = "overbought"
    else:          mfi_zone = "neutral"

    cci = float(c.get("cci", 0))
    if cci > 100:   cci_signal = "overbought"
    elif cci < -100: cci_signal = "oversold"
    else:           cci_signal = "neutral"

    wr = float(c.get("williams_r", -50))
    if wr > -20:   williams_signal = "overbought"
    elif wr < -80: williams_signal = "oversold"
    else:          williams_signal = "neutral"

    vol_spike = bool(c.get("vol_spike", False))

    csi_bull = float(c.get("csi_bull", 0))
    csi_bear = float(c.get("csi_bear", 0))

    last5  = df.iloc[-5:]
    last10 = df.iloc[-10:]

    bull_momentum = sum(1 for i in range(len(last5))
                        if float(last5.iloc[i]["close"]) > float(last5.iloc[i]["open"]))
    bear_momentum = 5 - bull_momentum

    bodies3 = [body(df.iloc[-i]) for i in range(1, 4)]
    momentum_accel = bodies3[0] > bodies3[1] > bodies3[2]

    c5  = [float(df.iloc[-i]["close"]) for i in range(1, 6)]
    c10 = [float(df.iloc[-i]["close"]) for i in range(1, 11)]
    bull5  = sum(1 for i in range(4) if c5[i] > c5[i+1])
    bear5  = sum(1 for i in range(4) if c5[i] < c5[i+1])
    bull10 = sum(1 for i in range(9) if c10[i] > c10[i+1])
    bear10 = sum(1 for i in range(9) if c10[i] < c10[i+1])
    trend_5  = "bull" if bull5 >= 3  else ("bear" if bear5 >= 3 else "flat")
    trend_10 = "bull" if bull10 >= 6 else ("bear" if bear10 >= 6 else "flat")

    ps = 0.01 if float(c["close"]) > 50 else 0.0001
    vol_on_signal = float(c.get("volume_ratio", 1.0))
    velocity = float(c.get("candle_vel", 1.0)) * (float(c["candle_range"]) / ps
                                                    if "candle_range" in df.columns else 5.0)

    rejection_up   = upper_wick_p >= 0.45
    rejection_down = lower_wick_p >= 0.45

    cl_bull = {
        "RSI not overbought":        rsi < 70,
        "RSI in bull zone":          rsi < 55 or rsi_zone in ("oversold", "bull_zone"),
        "MACD histogram positive":   macd_bull or macd_h > 0,
        "Price above VWAP":          above_vwap,
        "EMA bullish alignment":     ema_stack >= 2,
        "Stochastic not overbought": sk < 80,
        "MFI not overbought":        mfi < 80,
        "Williams %R not overbought":wr < -20,
        "Lower wick / bull pattern": rejection_down or bull_pin or bull_engulf or dragonfly_doji,
        "Volume confirms move":      vol_on_signal >= 0.70,
    }

    cl_bear = {
        "RSI not oversold":          rsi > 30,
        "RSI in bear zone":          rsi > 45 or rsi_zone in ("overbought", "bear_zone"),
        "MACD histogram negative":   macd_bear or macd_h < 0,
        "Price below VWAP":          not above_vwap,
        "EMA bearish alignment":     ema_bstack >= 2,
        "Stochastic not oversold":   sk > 20,
        "MFI not oversold":          mfi > 20,
        "Williams %R not oversold":  wr > -80,
        "Upper wick / bear pattern": rejection_up or bear_pin or bear_engulf or gravestone_doji,
        "Volume confirms move":      vol_on_signal >= 0.70,
    }

    cl_bull_score = sum(cl_bull.values())
    cl_bear_score = sum(cl_bear.values())

    bull_score = 0
    bear_score = 0
    active_patterns: List[str] = []

    if bull_engulf:            bull_score += 30; active_patterns.append("BullEngulf🕯️")
    if bear_engulf:            bear_score += 30; active_patterns.append("BearEngulf🕯️")
    if three_white_soldiers:   bull_score += 30; active_patterns.append("3Soldiers⚔️")
    if three_black_crows:      bear_score += 30; active_patterns.append("3Crows🐦")
    if morning_star:           bull_score += 28; active_patterns.append("MorningStar⭐")
    if evening_star:           bear_score += 28; active_patterns.append("EveningStar⭐")
    if bull_pin:               bull_score += 22; active_patterns.append("BullPin📌")
    if bear_pin:               bear_score += 22; active_patterns.append("BearPin📌")
    if dragonfly_doji:         bull_score += 20; active_patterns.append("Dragonfly🐉")
    if gravestone_doji:        bear_score += 20; active_patterns.append("Gravestone🪦")
    if tweezer_bottom:         bull_score += 18; active_patterns.append("TweezerBot🔧")
    if tweezer_top:            bear_score += 18; active_patterns.append("TweezerTop🔧")
    if bull_marubozu:          bull_score += 18; active_patterns.append("BullMaru💚")
    if bear_marubozu:          bear_score += 18; active_patterns.append("BearMaru❤️")
    if rising_three:           bull_score += 15; active_patterns.append("RisingThree📈")
    if falling_three:          bear_score += 15; active_patterns.append("FallingThree📉")
    if outside_bar and bull(c):bull_score += 12; active_patterns.append("OutsideBar↕️")
    if outside_bar and bear(c):bear_score += 12; active_patterns.append("OutsideBar↕️")

    if rsi_zone == "oversold":  bull_score += 12
    if rsi_zone == "bull_zone": bull_score += 6
    if rsi_zone == "overbought":bear_score += 12
    if rsi_zone == "bear_zone": bear_score += 6
    if stoch_cross == "bull":   bull_score += 10
    if stoch_cross == "bear":   bear_score += 10
    if macd_bull:               bull_score += 8
    if macd_bear:                bear_score += 8
    if above_vwap:              bull_score += 6
    else:                       bear_score += 6
    if ema_stack >= 3:          bull_score += 10
    elif ema_stack == 2:        bull_score += 5
    if ema_bstack >= 3:         bear_score += 10
    elif ema_bstack == 2:       bear_score += 5
    if bb_pos < 0.20:           bull_score += 8
    if bb_pos > 0.80:           bear_score += 8
    if squeeze_active:          bull_score += 5; bear_score += 5
    if obv_bull:                bull_score += 6
    else:                       bear_score += 6
    if mfi_zone == "oversold":  bull_score += 8
    if mfi_zone == "overbought":bear_score += 8
    if cci_signal == "oversold":bull_score += 6
    if cci_signal == "overbought":bear_score += 6
    if williams_signal == "oversold":  bull_score += 6
    if williams_signal == "overbought":bear_score += 6
    if vol_spike:
        if bull(c): bull_score += 8
        else:       bear_score += 8

    if bull_momentum >= 4: bull_score += 10; active_patterns.append(f"Mom{bull_momentum}🔥")
    if bear_momentum >= 4: bear_score += 10; active_patterns.append(f"Mom{bear_momentum}🔥")
    if momentum_accel and bull_momentum >= 3: bull_score += 6; active_patterns.append("Accel⚡")
    if momentum_accel and bear_momentum >= 3: bear_score += 6; active_patterns.append("Accel⚡")
    if trend_5 == "bull":  bull_score += 8
    if trend_5 == "bear":  bear_score += 8
    if trend_10 == "bull": bull_score += 6
    if trend_10 == "bear": bear_score += 6

    if rejection_down and close_pos >= 0.60: bull_score += 8
    if rejection_up   and close_pos <= 0.40: bear_score += 8
    if csi_bull > 0.50: bull_score += 6
    if csi_bear > 0.50: bear_score += 6

    if cl_bull_score >= 8: bull_score += 15
    elif cl_bull_score >= 6: bull_score += 8
    if cl_bear_score >= 8: bear_score += 15
    elif cl_bear_score >= 6: bear_score += 8

    if doji and not gravestone_doji and not dragonfly_doji:
        bull_score = max(0, bull_score - 12)
        bear_score = max(0, bear_score - 12)

    if bull_score >= bear_score + 15:
        candle_bias  = "bull"
        candle_score = min(100, bull_score)
    elif bear_score >= bull_score + 15:
        candle_bias  = "bear"
        candle_score = min(100, bear_score)
    else:
        candle_bias  = "neutral"
        candle_score = min(100, max(bull_score, bear_score))

    grade = ("A" if candle_score >= 80 else
             "B" if candle_score >= 65 else
             "C" if candle_score >= 50 else
             "D" if candle_score >= 35 else "F")

    return {
        "bull_engulf": bull_engulf, "bear_engulf": bear_engulf,
        "bull_pin": bull_pin, "bear_pin": bear_pin,
        "inside_bar": inside_bar, "outside_bar": outside_bar,
        "bull_marubozu": bull_marubozu, "bear_marubozu": bear_marubozu,
        "doji": doji, "gravestone_doji": gravestone_doji, "dragonfly_doji": dragonfly_doji,
        "morning_star": morning_star, "evening_star": evening_star,
        "three_white_soldiers": three_white_soldiers, "three_black_crows": three_black_crows,
        "tweezer_bottom": tweezer_bottom, "tweezer_top": tweezer_top,
        "rising_three": rising_three, "falling_three": falling_three,
        "body_pct": round(cur_body_pct, 3),
        "upper_wick_pct": round(upper_wick_p, 3),
        "lower_wick_pct": round(lower_wick_p, 3),
        "close_position": round(close_pos, 3),
        "csi": round(csi_bull if bull(c) else csi_bear, 3),
        "bull_momentum": bull_momentum, "bear_momentum": bear_momentum,
        "momentum_accel": momentum_accel, "trend_5": trend_5, "trend_10": trend_10,
        "rejection_up": rejection_up, "rejection_down": rejection_down,
        "rsi_zone": rsi_zone, "stoch_cross": stoch_cross,
        "macd_aligned": macd_bull if bull(c) else macd_bear,
        "above_vwap": above_vwap, "ema_stack": ema_stack,
        "bb_position": round(bb_pos, 3), "squeeze_active": squeeze_active,
        "obv_aligned": obv_bull, "mfi_zone": mfi_zone,
        "cci_signal": cci_signal, "williams_signal": williams_signal,
        "vol_spike": vol_spike, "vol_on_signal": round(vol_on_signal, 2),
        "candle_velocity": round(velocity, 1),
        "checklist_bull": cl_bull, "checklist_bear": cl_bear,
        "checklist_bull_score": cl_bull_score,
        "checklist_bear_score": cl_bear_score,
        "bull_score": bull_score, "bear_score": bear_score,
        "candle_score": candle_score, "candle_bias": candle_bias,
        "candle_grade": grade, "top_patterns": active_patterns[:5],
        "pattern_score": candle_score,
    }


detect_patterns = candle_intelligence

def market_structure(df: pd.DataFrame, lookback: int = 50) -> str:
    if len(df) < lookback + 5:
        return "RANGING"

    tail = df.tail(lookback)
    half = lookback // 2
    first  = tail.iloc[:half]
    second = tail.iloc[half:]

    first_hh  = float(first["high"].max())
    first_ll  = float(first["low"].min())
    second_hh = float(second["high"].max())
    second_ll = float(second["low"].min())

    higher_high = second_hh > first_hh
    higher_low  = second_ll > first_ll
    lower_high  = second_hh < first_hh
    lower_low   = second_ll < first_ll

    if higher_high and higher_low:  return "BULLISH"
    if lower_high  and lower_low:   return "BEARISH"
    return "RANGING"


def structure_bias(structure: str) -> str:
    if structure == "BULLISH": return "buy"
    if structure == "BEARISH": return "sell"
    return "any"


def structure_allows_trade(signal: str, structure: str) -> Tuple[bool, str]:
    if not MARKET_STRUCTURE_FILTER:
        return True, "filter_off"
    if structure == "BULLISH" and signal == "sell":
        return False, f"sell blocked in BULLISH structure"
    if structure == "BEARISH" and signal == "buy":
        return False, f"buy blocked in BEARISH structure"
    return True, f"aligned with {structure}"


def liquidity_sweep_score(df: pd.DataFrame, signal: str) -> int:
    if len(df) < 5:
        return 0
    last = df.iloc[-1]
    if signal == "buy"  and bool(last.get("liq_sweep_dn",  False)): return 25
    if signal == "sell" and bool(last.get("liq_sweep_up", False)): return 25
    recent = df.tail(3)
    if signal == "buy"  and recent["liq_sweep_dn"].any():  return 12
    if signal == "sell" and recent["liq_sweep_up"].any(): return 12
    return 0


def is_near_news(utc_hour: int, utc_minute: int) -> Tuple[bool, str]:
    if not NEWS_FILTER_ENABLED:
        return False, ""

    current_mins = utc_hour * 60 + utc_minute

    for news_h, news_m, desc in HIGH_IMPACT_NEWS:
        news_mins  = news_h * 60 + news_m
        mins_to    = news_mins - current_mins
        mins_since = current_mins - news_mins

        if 0 <= mins_to <= NEWS_BLOCK_MINUTES_BEFORE:
            return True, f"⏰ {desc} in {mins_to}min — trading paused"
        if 0 <= mins_since <= NEWS_BLOCK_MINUTES_AFTER:
            return True, f"📰 {desc} just released ({mins_since}min ago) — waiting"

    return False, ""


def print_live_performance(db, manager, balance: float):
    stats = db.get_statistics()
    n     = stats["total_trades"]
    sep   = "─" * 66

    print(f"\n{sep}")
    print(f"  📊  LIVE PERFORMANCE DASHBOARD  —  Balance: ${balance:,.2f}")
    print(sep)

    print(f"  Today:  {manager.daily_count}/{MAX_DAILY_TRADES} trades  |  "
          f"Open: {manager.open_count}  |  "
          f"P&L today: ${manager.daily_pnl:+.2f}")

    if n == 0:
        print(f"  No closed trades recorded yet.")
        print(sep)
        return

    wr  = stats["win_rate"] * 100
    exp = stats["expectancy"]
    pf  = stats["profit_factor"]
    dd  = stats["max_drawdown_pct"]
    tr  = stats["total_return_r"]

    print(f"\n  {'Metric':<22} {'Value':>10}  {'Target':>10}  {'Status'}")
    print(f"  {'─'*56}")

    def row(label, val, target, fmt, higher_is_better=True):
        ok = (val >= target) if higher_is_better else (val <= target)
        icon = "✅" if ok else "⚠️ "
        print(f"  {label:<22} {fmt.format(val):>10}  {fmt.format(target):>10}  {icon}")

    row("Total Trades",    n,   50,   "{:.0f}",    True)
    row("Win Rate",        wr,  55.0, "{:.1f}%",   True)
    row("Expectancy (R)",  exp, 0.50, "{:+.3f}R",  True)
    row("Profit Factor",   pf,  1.30, "{:.2f}",    True)
    row("Max Drawdown",    dd,  5.0,  "{:.1f}%",   False)
    row("Total Return",    tr,  0.0,  "{:+.2f}R",  True)

    print(f"\n  📈 BY SYMBOL:")
    sym_data = db.get_performance_by_state()
    try:
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        rows = conn.execute("""
            SELECT symbol,
                   COUNT(*) as n,
                   AVG(realized_r) as avg_r,
                   SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as wr,
                   SUM(realized_r) as total_r
              FROM trades WHERE realized_r IS NOT NULL
             GROUP BY symbol ORDER BY total_r DESC
        """).fetchall()
        conn.close()
        for sym, cnt, avg_r, sym_wr, total_r in rows:
            bar  = "▓" * int((sym_wr or 0) / 5)
            icon = "✅" if (avg_r or 0) > 0 else "❌"
            print(f"    {icon} {sym:<8} {cnt:>3} trades  "
                  f"WR:{sym_wr:>5.1f}%  AvgR:{avg_r:+.3f}  "
                  f"Total:{total_r:+.2f}R  {bar}")
    except Exception:
        pass

    try:
        conn  = sqlite3.connect(db.db_path)
        w_row = conn.execute("SELECT AVG(realized_r) FROM trades WHERE win=1 AND realized_r IS NOT NULL").fetchone()
        l_row = conn.execute("SELECT AVG(realized_r) FROM trades WHERE win=0 AND realized_r IS NOT NULL").fetchone()
        conn.close()
        avg_win  = float(w_row[0]) if w_row and w_row[0] else 0
        avg_loss = float(l_row[0]) if l_row and l_row[0] else 0
        ratio    = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        print(f"\n  ⚖️  Avg Win: {avg_win:+.3f}R  |  "
              f"Avg Loss: {avg_loss:+.3f}R  |  "
              f"W/L Ratio: {ratio:.2f}x")
        quality = "🟢 Good" if ratio >= 1.5 else ("🟡 Fair" if ratio >= 1.0 else "🔴 Poor")
        print(f"     Win/Loss ratio quality: {quality}")
    except Exception:
        pass

    t   = FTMO_READINESS_THRESHOLDS
    ready = (n   >= t["min_trades"]   and
             wr  >= t["min_win_rate"] * 100 and
             pf  >= t["min_pf"]       and
             dd  <= t["max_dd"])

    print(f"\n  {'═'*56}")
    if ready:
        print(f"  🏆  FTMO CHALLENGE READY  —  All targets achieved!")
        print(f"     Recommended lot size: 0.20 (start conservative)")
    else:
        missing = []
        if n   < t["min_trades"]:             missing.append(f"{t['min_trades']-n} more trades")
        if wr  < t["min_win_rate"] * 100:     missing.append(f"WR needs +{t['min_win_rate']*100-wr:.1f}%")
        if pf  < t["min_pf"]:                 missing.append(f"PF needs +{t['min_pf']-pf:.2f}")
        if dd  > t["max_dd"]:                 missing.append(f"DD must drop {dd-t['max_dd']:.1f}%")
        print(f"  ⏳  NOT YET READY:  {' | '.join(missing)}")

    print(sep)


CENTRAL_BANK_RATES: Dict[str, float] = {
    "EUR": 2.15, "GBP": 3.75, "USD": 4.50, "AUD": 4.10, "JPY": 0.50,
}

CENTRAL_BANK_EVENTS: List[Tuple[str, str, str, str]] = [
    ("2026-06-11", "EUR", "ECB Rate Decision",  "HIKE +0.25%"),
    ("2026-06-18", "GBP", "BoE Rate Decision",  "HOLD"),
    ("2026-06-18", "USD", "FOMC Decision",       "HOLD"),
    ("2026-06-10", "AUD", "RBA Minutes",         "NEUTRAL"),
]

ECONOMIC_SENTIMENT: Dict[str, int] = {
    "EUR": +1, "GBP":  0, "USD":  0, "AUD": -1, "JPY": -1,
}

def rate_differential(base: str, quote: str) -> float:
    return CENTRAL_BANK_RATES.get(base, 0.0) - CENTRAL_BANK_RATES.get(quote, 0.0)


def fundamental_bias(symbol: str) -> Tuple[str, int, str]:
    if len(symbol) != 6:
        return "neutral", 0, "unknown symbol"

    base  = symbol[:3]
    quote = symbol[3:]

    diff        = rate_differential(base, quote)
    diff_abs    = min(abs(diff), 5.0)
    rate_score  = int(diff_abs / 5.0 * 40)
    rate_dir    = "buy" if diff > 0.5 else ("sell" if diff < -0.5 else "neutral")

    base_sent   = ECONOMIC_SENTIMENT.get(base, 0)
    quote_sent  = ECONOMIC_SENTIMENT.get(quote, 0)
    sent_diff   = base_sent - quote_sent
    sent_score  = int(abs(sent_diff) / 2.0 * 30)
    sent_dir    = "buy" if sent_diff > 0 else ("sell" if sent_diff < 0 else "neutral")

    cb_score  = 0
    cb_dir    = "neutral"
    today     = datetime.utcnow().date()
    for date_str, currency, event, action in CENTRAL_BANK_EVENTS:
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_away = (event_date - today).days
        if abs(days_away) > 7:
            continue

        if currency == base:
            if "HIKE" in action:
                cb_dir = "buy"; cb_score = max(cb_score, 25 if days_away <= 2 else 15)
            elif "CUT" in action:
                cb_dir = "sell"; cb_score = max(cb_score, 25 if days_away <= 2 else 15)
            elif "HOLD" in action:
                cb_score = max(cb_score, 5)
        elif currency == quote:
            if "HIKE" in action:
                cb_dir = "sell"; cb_score = max(cb_score, 25 if days_away <= 2 else 15)
            elif "CUT" in action:
                cb_dir = "buy"; cb_score = max(cb_score, 25 if days_away <= 2 else 15)

    directions = [d for d in [rate_dir, sent_dir, cb_dir] if d != "neutral"]
    if not directions:
        return "neutral", 0, "no fundamental bias"

    buy_votes  = directions.count("buy")
    sell_votes = directions.count("sell")

    if buy_votes > sell_votes:
        final_dir = "buy"
        strength  = rate_score + sent_score + cb_score
        reason    = (f"Rate diff {diff:+.2f}% | Sentiment {base}+{base_sent}/{quote}+{quote_sent}"
                     f" | CB: {action if cb_dir!='neutral' else 'neutral'}")
    elif sell_votes > buy_votes:
        final_dir = "sell"
        strength  = rate_score + sent_score + cb_score
        reason    = (f"Rate diff {diff:+.2f}% | Sentiment {base}+{base_sent}/{quote}+{quote_sent}"
                     f" | CB: {action if cb_dir!='neutral' else 'neutral'}")
    else:
        final_dir = "neutral"
        strength  = 0
        reason    = "conflicting fundamentals"

    return final_dir, min(100, strength), reason


def market_intelligence_report(symbols: List[str]) -> Dict[str, Dict]:
    report = {}
    for symbol in symbols:
        base  = symbol[:3] if len(symbol) == 6 else "???"
        quote = symbol[3:] if len(symbol) == 6 else "???"
        bias, strength, reason = fundamental_bias(symbol)
        diff = rate_differential(base, quote)
        report[symbol] = {
            "bias":      bias,
            "strength":  strength,
            "rate_diff": diff,
            "base_rate": CENTRAL_BANK_RATES.get(base, 0),
            "quote_rate":CENTRAL_BANK_RATES.get(quote, 0),
            "base_sent": ECONOMIC_SENTIMENT.get(base, 0),
            "quote_sent":ECONOMIC_SENTIMENT.get(quote, 0),
            "reason":    reason,
        }
    return report


def print_market_intelligence(report: Dict[str, Dict]):
    print(f"\n{'═'*72}")
    print(f"  🌍  MARKET INTELLIGENCE BRIEFING  —  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*72}")

    today = datetime.utcnow().date()
    upcoming = [(d, c, e, a) for d, c, e, a in CENTRAL_BANK_EVENTS
                if abs((datetime.strptime(d, "%Y-%m-%d").date() - today).days) <= 7]
    if upcoming:
        print(f"\n  📅 UPCOMING CENTRAL BANK EVENTS (next 7 days):")
        for date_str, currency, event, action in sorted(upcoming):
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_away  = (event_date - today).days
            flag = "🔴" if "HIKE" in action or "CUT" in action else "🟡"
            timing = "TODAY" if days_away == 0 else (f"in {days_away}d" if days_away > 0 else f"{abs(days_away)}d ago")
            print(f"     {flag} {date_str} ({timing})  {currency}  {event}  →  {action}")

    print(f"\n  💰 INTEREST RATES:")
    for currency, rate in sorted(CENTRAL_BANK_RATES.items(), key=lambda x: -x[1]):
        sent  = ECONOMIC_SENTIMENT.get(currency, 0)
        arrow = "↑" if sent > 0 else ("↓" if sent < 0 else "→")
        print(f"     {currency}  {rate:.2f}%  {arrow}")

    print(f"\n  📊 PAIR FUNDAMENTALS:")
    print(f"  {'Symbol':<8} {'Bias':<6} {'Str':>4}  {'Rate Diff':>10}  Reasoning")
    print(f"  {'─'*65}")
    for symbol, data in report.items():
        bias     = data["bias"]
        strength = data["strength"]
        diff     = data["rate_diff"]
        bias_icon = "📈" if bias == "buy" else ("📉" if bias == "sell" else "➡️ ")
        bar = "█" * (strength // 10)
        print(f"  {symbol:<8} {bias_icon}{bias:<5} {strength:>3}%  {diff:>+9.2f}%  {bar}")

    print(f"{'═'*72}\n")


def intelligence_confidence_bonus(symbol: str, signal: str,
                                   report: Dict[str, Dict]) -> int:
    if symbol not in report:
        return 0

    data     = report[symbol]
    bias     = data["bias"]
    strength = data["strength"]

    if bias == "neutral" or strength < 20:
        return 0

    if signal == bias:
        return int(strength / 100 * 20)
    elif bias != "neutral":
        return -int(strength / 100 * 15)

    return 0


def generate_signal(df: pd.DataFrame, state: str, bp: float,
                    symbol: str = "", structure: str = "RANGING") -> Tuple[str, int]:
    if len(df) < 50:
        return "hold", 0

    last  = df.iloc[-1]
    rh    = float(last["range_high_20"])
    rl    = float(last["range_low_20"])
    price = float(last["close"])
    rw    = rh - rl
    if rw <= 0:
        return "hold", 0

    rsi      = float(last.get("rsi", 50))
    p_boost  = int(bp * 18)
    pos_pct  = (price - rl) / rw
    smc      = smc_analysis(df)
    preferred = structure_bias(structure)

    def smc_boost(direction):
        if not smc["valid"]: return 0
        if direction == "buy"  and smc["smc_buy"]:              return 12
        if direction == "sell" and smc["smc_sell"]:             return 12
        if direction == "buy"  and smc["at_eq"] and smc["bull_bos"]: return 6
        if direction == "sell" and smc["at_eq"] and smc["bear_bos"]: return 6
        return 0

    if state in ("BREAKOUT_ACTIVE", "EXPANSION"):
        if price > rh:
            return "buy",  min(95, 82 + p_boost + smc_boost("buy"))
        if price < rl:
            return "sell", min(95, 82 + p_boost + smc_boost("sell"))
        if price > rh * 0.990:
            return "buy",  min(88, 72 + p_boost + smc_boost("buy"))
        if price < rl * 1.010:
            return "sell", min(88, 72 + p_boost + smc_boost("sell"))

    elif state == "BREAKOUT_SETUP":
        if price > rh * 0.995:
            return "buy",  min(82, 68 + p_boost + smc_boost("buy"))
        if price < rl * 1.005:
            return "sell", min(82, 68 + p_boost + smc_boost("sell"))

    elif state == "COMPRESSION":
        if preferred == "sell":
            if pos_pct >= 0.55 or price > rh * 0.993:
                return "sell", min(78, 60 + p_boost + smc_boost("sell"))
            if pos_pct >= 0.40 and rsi > 50:
                return "sell", min(70, 55 + p_boost + smc_boost("sell"))
        elif preferred == "buy":
            if pos_pct <= 0.45 or price < rl * 1.007:
                return "buy", min(78, 60 + p_boost + smc_boost("buy"))
            if pos_pct <= 0.60 and rsi < 50:
                return "buy", min(70, 55 + p_boost + smc_boost("buy"))
        else:
            if pos_pct >= 0.75 or price > rh * 0.993:
                return "buy",  min(78, 60 + p_boost + smc_boost("buy"))
            if pos_pct <= 0.25 or price < rl * 1.007:
                return "sell", min(78, 60 + p_boost + smc_boost("sell"))

    elif state == "RANGE":
        if price <= rl * 1.010:
            if bool(last.get("long_lower_wick", False)) or rsi < 42:
                return "buy", 62 + smc_boost("buy")
        if price >= rh * 0.990:
            if bool(last.get("long_upper_wick", False)) or rsi > 58:
                return "sell", 62 + smc_boost("sell")

    return "hold", 0


def adaptive_confidence(
    df: pd.DataFrame, symbol: str, signal: str,
    spread_pips: float, session_boost: float,
    pressure: int, state_quality: int, bp: float,
    vol_norm: float, atr_exp: float,
) -> int:
    last  = df.iloc[-1]
    score = 48

    trend = "bull" if last["ema_21"] > last["ema_50"] else "bear"
    if (signal == "buy" and trend == "bull") or (signal == "sell" and trend == "bear"):
        score += 14
    else:
        score -= 8

    if not np.isnan(last.get("sma_200", float("nan"))):
        if (signal == "buy" and last["close"] > last["sma_200"]) or \
           (signal == "sell" and last["close"] < last["sma_200"]):
            score += 6

    score += 14 if vol_norm > 1.5 else (9 if vol_norm > 1.2 else (3 if vol_norm > 1.0 else -4))

    score += 12 if pressure > 70 else (7 if pressure > 50 else (2 if pressure > 35 else -3))

    score += state_quality // 10

    if bp >= 0.76:   score += 15
    elif bp >= 0.62: score += 8
    elif bp >= 0.46: score += 2
    else:            score -= 6

    score += 8 if atr_exp > 1.4 else (5 if atr_exp > 1.1 else 0)

    rsi = last.get("rsi", 50)
    if signal == "buy":
        if rsi < 20:    score -= 20
        elif rsi < 30:  score -= 10
        elif 35 < rsi < 65: score += 5
        elif rsi > 75:  score -= 8
    else:
        if rsi > 80:    score -= 20
        elif rsi > 70:  score -= 10
        elif 35 < rsi < 65: score += 5
        elif rsi < 25:  score -= 8

    hist = last.get("macd_hist", 0)
    if (signal == "buy" and hist > 0) or (signal == "sell" and hist < 0):
        score += 5
    else:
        score -= 3

    score += int(session_boost * 8)

    max_sp = MAX_SPREAD_JPY if "JPY" in symbol else MAX_SPREAD_NORMAL
    if spread_pips > max_sp:
        score -= int((spread_pips - max_sp) * 12)

    if last.get("liq_sweep_up") or last.get("liq_sweep_dn"):
        score += 6

    body = last.get("body_pct", 0.5)
    if body > 0.65:
        score += 5

    wick_sum = last.get("upper_wick_ratio", 0) + last.get("lower_wick_ratio", 0)
    if wick_sum > 1.1:
        score -= 6

    mom = last.get("momentum", 0)
    if (signal == "buy" and mom > 0.3) or (signal == "sell" and mom < -0.3):
        score += 5
    elif (signal == "buy" and mom < -0.5) or (signal == "sell" and mom > 0.5):
        score -= 7

    return int(np.clip(score, 0, 97))


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001

def pip_value(symbol: str) -> float:
    return 9.5 if "JPY" in symbol else 10.0

def symbol_point(symbol: str) -> float:
    try:
        info = mt5.symbol_info(symbol)
        return info.point if info else (0.001 if "JPY" in symbol else 0.0001)
    except Exception:
        return 0.001 if "JPY" in symbol else 0.0001

def session_info(utc_hour: int) -> Tuple[str, float]:
    if   12 <= utc_hour < 15: return "Overlap",  1.30
    elif  7 <= utc_hour < 12: return "London",   1.05
    elif 15 <= utc_hour < 20: return "NewYork",  0.90
    else:                      return "Asian",    0.55

def volatility_regime(atr_pct: float) -> str:
    if atr_pct < 0.020: return "very_low"
    if atr_pct < 0.025: return "low"
    if atr_pct < 0.045: return "normal"
    if atr_pct < 0.065: return "high"
    return "very_high"

def lot_size(balance: float, risk_pct: float, sl_pips: int, symbol: str) -> float:
    risk_amt = balance * risk_pct / 100
    pv       = pip_value(symbol)
    lot      = risk_amt / (sl_pips * pv) if sl_pips > 0 else 0.01
    lot      = round(max(0.01, min(0.10, lot)) / 0.01) * 0.01
    return lot


def dynamic_lot_size(balance: float, risk_pct: float,
                     entry: float, sl: float, symbol: str) -> float:
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.01

    ps = pip_size(symbol)
    pv = pip_value(symbol)
    if ps <= 0:
        return 0.01

    risk_amount = balance * risk_pct / 100.0
    lot_value   = sl_dist * (pv / ps)

    if lot_value <= 0:
        return 0.01

    raw_lot = risk_amount / lot_value

    if balance < 2000:
        MAX_LOT_HARD_CAP = 0.05
    elif balance < 10000:
        MAX_LOT_HARD_CAP = 0.10
    elif balance < 25000:
        MAX_LOT_HARD_CAP = 0.20
    else:
        MAX_LOT_HARD_CAP = MAX_LOT_HARD_CAP_PROP

    lot = round(max(0.01, min(MAX_LOT_HARD_CAP, raw_lot)) / 0.01) * 0.01
    return lot

def validate_stops(symbol: str, entry: float, sl: float, tp: float):
    try:
        info = mt5.symbol_info(symbol)
        if not info: return True, sl, tp
        min_dist = info.trade_stops_level * info.point
        if min_dist > 0 and abs(entry - sl) < min_dist:
            sl = entry - min_dist if sl < entry else entry + min_dist
    except Exception:
        pass
    return True, sl, tp

def fetch_rates(symbol: str, bars: int = BARS) -> Optional[pd.DataFrame]:
    rates = mt5.copy_rates_from_pos(symbol, TIMEFRAME, 0, bars)
    if rates is None or len(rates) < 60:
        return None
    return pd.DataFrame(rates)

def risk_bar(pct: float, width: int = 20) -> str:
    filled = min(width, int(pct / MAX_RISK_PER_TRADE * width))
    ch = "🟢" if pct < 0.5 else ("🟡" if pct < 0.75 else ("🟠" if pct < 0.9 else "🔴"))
    return ch * filled + "⚪" * (width - filled)

def generate_tags(state: str, session: str, vol_norm: float,
                  confidence: int, edge_str: str) -> str:
    tags = [state.lower(), session.lower()]
    if vol_norm > 1.5:  tags.append("vol_surge")
    elif vol_norm > 1.2: tags.append("elevated_vol")
    if confidence >= 80:  tags.append("high_conf")
    tags.append(edge_str)
    return ",".join(tags)

def edge_label(score: int) -> str:
    if score >= 85: return "very_strong"
    if score >= 75: return "strong"
    if score >= 65: return "moderate"
    return "weak"


def manage_open_trades(manager: TradeManager):
    positions = mt5.positions_get()
    if not positions:
        return

    pos_map = {p.ticket: p for p in positions}

    for ticket, trade in list(manager.open.items()):
        pos = pos_map.get(ticket)
        if pos is None:
            continue

        sl_dist = abs(trade.entry_price - trade.sl)
        if sl_dist < 1e-8:
            continue

        price    = pos.price_current
        profit_r = ((price - trade.entry_price) / sl_dist if trade.signal == "buy"
                    else (trade.entry_price - price) / sl_dist)

        sym_info = mt5.symbol_info(trade.symbol)
        if not sym_info:
            continue
        point = sym_info.point

        def modify_sl(new_sl: float) -> bool:
            res = mt5.order_send({
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl":       round(new_sl, sym_info.digits),
                "tp":       pos.tp,
            })
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

        def partial_close(volume: float, label: str) -> bool:
            vol = round(max(0.01, volume), 2)
            if vol < 0.01:
                return False
            tick = mt5.symbol_info_tick(trade.symbol)
            if not tick:
                return False
            close_price = tick.bid if trade.signal == "buy" else tick.ask
            res = mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       trade.symbol,
                "volume":       vol,
                "type":         mt5.ORDER_TYPE_SELL if trade.signal == "buy" else mt5.ORDER_TYPE_BUY,
                "price":        close_price,
                "position":     ticket,
                "deviation":    20,
                "magic":        999999,
                "comment":      label,
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            })
            return bool(res and res.retcode == mt5.TRADE_RETCODE_DONE)

        partial_count = len(trade.partial_closes)

        if profit_r >= TP1_R and partial_count == 0:
            vol_tp1 = round(trade.lot * 0.40, 2)
            if partial_close(vol_tp1, "TP1_40pct"):
                trade.partial_closes.append(("TP1", price, profit_r))
                be_sl = (trade.entry_price + point if trade.signal == "buy"
                         else trade.entry_price - point)
                if modify_sl(be_sl):
                    trade.breakeven_set = True
                    print(f"   🔒 {trade.symbol} TP1 hit — "
                          f"closed 40% @ {price:.5f} ({profit_r:.2f}R)  "
                          f"SL → breakeven")

        elif profit_r >= TP2_R and partial_count == 1:
            vol_tp2 = round(trade.lot * 0.35, 2)
            if partial_close(vol_tp2, "TP2_35pct"):
                trade.partial_closes.append(("TP2", price, profit_r))
                trail_sl = (trade.entry_price + sl_dist * 1.0 if trade.signal == "buy"
                            else trade.entry_price - sl_dist * 1.0)
                if modify_sl(trail_sl):
                    print(f"   💰 {trade.symbol} TP2 hit — "
                          f"closed 35% @ {price:.5f} ({profit_r:.2f}R)  "
                          f"SL trailed to +1R")

        elif profit_r >= TP3_R and partial_count == 2:
            vol_tp3 = round(trade.lot * 0.25, 2)
            if partial_close(vol_tp3, "TP3_25pct"):
                trade.partial_closes.append(("TP3", price, profit_r))
                print(f"   🏆 {trade.symbol} TP3 hit — "
                      f"closed 25% @ {price:.5f} ({profit_r:.2f}R)  "
                      f"full target reached")


def harvest_closed_trades(
    manager: TradeManager, db: TradeDatabase, equity_prot: EquityCurveProtection,
    processed_tickets: set,
):
    from_dt = datetime.now() - timedelta(days=7)
    deals = mt5.history_deals_get(from_dt, datetime.now())
    if not deals:
        return

    for deal in deals:
        if deal.ticket in processed_tickets:
            continue
        if deal.entry != mt5.DEAL_ENTRY_OUT:
            continue

        tix = deal.order

        trade = manager.open.get(tix)

        if trade is None:
            try:
                with db._connect() as conn:
                    row = conn.execute(
                        """SELECT id, symbol, signal, entry_price, sl, tp,
                                  lot, risk_percent, confidence, opportunity_score,
                                  execution_score, pressure_score, atr_percentile,
                                  spread_at_entry, state, entry_time, session, tags, edge_strength
                             FROM trades
                            WHERE ticket=? AND exit_time IS NULL
                            LIMIT 1""",
                        (tix,)
                    ).fetchone()
                if row:
                    trade = TradeRecord(
                        ticket=tix, symbol=row[1], signal=row[2],
                        entry_price=row[3], sl=row[4], tp=row[5],
                        lot=row[6], risk_percent=row[7],
                        confidence=row[8] or 0, opportunity_score=row[9] or 0,
                        execution_score=row[10] or 0, pressure_score=row[11] or 0,
                        expansion_quality=0, atr_percentile=row[12] or 0,
                        spread_at_entry=row[13] or 0, state=row[14] or "UNKNOWN",
                        entry_time=row[15] or datetime.now().isoformat(),
                        session=row[16] or "Unknown", tags=row[17] or "",
                        edge_strength=row[18] or "unknown",
                    )
                    trade.db_id = row[0]
            except Exception:
                pass

        if trade is None:
            continue

        profit     = deal.profit + deal.commission + deal.swap
        exit_p     = deal.price
        entry_p    = trade.entry_price

        raw_sl_dist = abs(entry_p - trade.sl) if trade.sl else 0
        ps          = 0.01 if "JPY" in trade.symbol else 0.0001
        min_dist    = 10 * ps
        if raw_sl_dist < min_dist:
            raw_sl_dist = 30 * ps
        sl_dist    = raw_sl_dist
        realized_r = (exit_p - entry_p) / sl_dist * (1 if trade.signal == "buy" else -1)
        entry_dt   = datetime.fromisoformat(trade.entry_time)
        hold_mins  = (datetime.now() - entry_dt).total_seconds() / 60

        if tix in manager.open:
            manager.close(tix, profit, exit_p, realized_r)
        processed_tickets.add(deal.ticket)

        acct = mt5.account_info()
        if acct:
            equity_prot.record(acct.balance, "win" if profit > 0 else "loss")

        if hasattr(trade, 'db_id') and trade.db_id:
            db.close_trade(
                trade_id=trade.db_id, ticket=tix,
                exit_price=exit_p, profit=profit,
                realized_r=round(realized_r, 3),
                exit_time=datetime.now().isoformat(),
                holding_minutes=round(hold_mins, 1),
            )
            sign = "+" if profit >= 0 else ""
            icon = "✅" if profit > 0 else "❌"
            source = "live" if tix in {t: t for t in manager.open} else "recovered"
            print(f"   {icon}  Closed {trade.symbol}  "
                  f"{sign}${profit:.2f}  ({realized_r:+.2f}R)  "
                  f"hold {hold_mins:.0f}m  [{source}]")


def startup_recovery(db: TradeDatabase):
    print(f"\n  🔍 Running startup recovery scan (last 30 days)...")
    from_dt = datetime.now() - timedelta(days=30)
    deals = mt5.history_deals_get(from_dt, datetime.now())
    if not deals:
        print(f"  ✅ No deals found in MT5 history")
        return

    recovered = 0
    try:
        with db._connect() as conn:
            open_trades = conn.execute(
                "SELECT id, ticket, symbol, signal, entry_price, sl, entry_time "
                "FROM trades WHERE exit_time IS NULL"
            ).fetchall()

        if not open_trades:
            print(f"  ✅ No unclosed trades in database")
            return

        closed_deals = {d.order: d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT}

        for row in open_trades:
            db_id, ticket, symbol, signal, entry_p, sl, entry_time_str = row
            if ticket in closed_deals:
                deal      = closed_deals[ticket]
                profit    = deal.profit + deal.commission + deal.swap
                exit_p    = deal.price
                sl_dist   = abs(entry_p - (sl or entry_p * 0.999)) or 0.0001
                realized_r = (exit_p - entry_p) / sl_dist * (1 if signal == "buy" else -1)
                try:
                    entry_dt  = datetime.fromisoformat(entry_time_str)
                    hold_mins = (datetime.fromtimestamp(deal.time) - entry_dt).total_seconds() / 60
                except Exception:
                    hold_mins = 0

                db.close_trade(
                    trade_id=db_id, ticket=ticket,
                    exit_price=exit_p, profit=profit,
                    realized_r=round(realized_r, 3),
                    exit_time=datetime.fromtimestamp(deal.time).isoformat(),
                    holding_minutes=round(hold_mins, 1),
                )
                sign = "+" if profit >= 0 else ""
                icon = "✅" if profit > 0 else "❌"
                print(f"     {icon} Recovered {symbol}  {sign}${profit:.2f}  "
                      f"({realized_r:+.2f}R)  ticket #{ticket}")
                recovered += 1

    except Exception as e:
        print(f"  ⚠️  Recovery error: {e}")

    if recovered:
        print(f"  ✅ Recovery complete — {recovered} trade(s) restored to database")
    else:
        print(f"  ✅ All trades already accounted for")


@dataclass
class HQTPosition:
    symbol:        str
    signal:        str
    entries:       List[Dict]
    avg_price:     float
    total_lots:    float
    sl:            float
    initial_sl:    float
    tp1:           float
    tp2:           float
    tp3:           float
    last_entry_px: float
    dca_count:     int = 0
    tp1_hit:       bool = False
    tp2_hit:       bool = False
    breakeven_set: bool = False


class HQTManager:
    _FILE = os.path.join(_SCRIPT_DIR, "hqt_positions_v36_itafx.json")

    def __init__(self):
        self.positions: Dict[str, HQTPosition] = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._FILE):
                with open(self._FILE) as f:
                    data = json.load(f)
                for sym, d in data.items():
                    self.positions[sym] = HQTPosition(**d)
                if self.positions:
                    print(f"  🔄 HQT: loaded {len(self.positions)} active position(s)")
        except Exception:
            pass

    def _save(self):
        try:
            import dataclasses
            data = {}
            for sym, pos in self.positions.items():
                data[sym] = dataclasses.asdict(pos)
            with open(self._FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"  ⚠️  HQT save error: {e}")

    def register_initial(self, symbol: str, signal: str, ticket: int,
                         lot: float, entry_px: float, sl: float, tp1: float,
                         tp2: float, tp3: float):
        pos = HQTPosition(
            symbol=symbol, signal=signal,
            entries=[{"ticket": ticket, "lot": lot,
                      "price": entry_px, "time": datetime.now().isoformat()}],
            avg_price=entry_px, total_lots=lot,
            sl=sl, initial_sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
            last_entry_px=entry_px, dca_count=0,
        )
        self.positions[symbol] = pos
        self._save()
        print(f"  📋 HQT {symbol} {signal.upper()} registered  "
              f"avg={entry_px:.5f}  TP1={tp1:.5f}  TP2={tp2:.5f}  TP3={tp3:.5f}")

    def should_add_entry(self, symbol: str, current_price: float,
                         balance: float) -> Tuple[bool, str]:
        if not HQT_ENABLED or symbol not in self.positions:
            return False, "no active HQT position"

        pos = self.positions[symbol]
        ps  = 0.01 if "JPY" in symbol else 0.0001

        if pos.dca_count >= HQT_MAX_ENTRIES:
            return False, f"max DCA entries ({HQT_MAX_ENTRIES}) reached"

        if pos.tp1_hit:
            return False, "TP1 hit — protecting remaining position"

        float_pnl_pct = self._floating_pct(pos, current_price, balance)
        if float_pnl_pct < -HQT_MAX_FLOAT_LOSS:
            return False, f"floating loss {float_pnl_pct:.1f}% exceeds {HQT_MAX_FLOAT_LOSS}% limit"

        pip_move = (pos.last_entry_px - current_price) / ps if pos.signal == "buy" \
                    else (current_price - pos.last_entry_px) / ps

        if pip_move >= HQT_STEP_PIPS:
            return True, f"price moved {pip_move:.1f}pip against — adding DCA entry #{pos.dca_count + 1}"

        return False, f"waiting for {HQT_STEP_PIPS - pip_move:.1f} more pips"

    def add_entry(self, symbol: str, ticket: int, lot: float,
                  entry_px: float):
        if symbol not in self.positions:
            return
        pos = self.positions[symbol]
        pos.entries.append({"ticket": ticket, "lot": lot,
                             "price": entry_px, "time": datetime.now().isoformat()})
        total_cost = sum(e["lot"] * e["price"] for e in pos.entries)
        total_lots = sum(e["lot"] for e in pos.entries)
        pos.avg_price     = total_cost / total_lots if total_lots else entry_px
        pos.total_lots    = total_lots
        pos.last_entry_px = entry_px
        pos.dca_count    += 1
        self._save()

        ps  = 0.01 if "JPY" in symbol else 0.0001
        print(f"  ➕ HQT DCA #{pos.dca_count} {symbol}  "
              f"entry={entry_px:.5f}  avg_price={pos.avg_price:.5f}  "
              f"total_lots={total_lots:.2f}")

    def manage_active(self, balance: float):
        if not self.positions:
            return

        for symbol, pos in list(self.positions.items()):
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                continue

            current = tick.bid if pos.signal == "buy" else tick.ask
            ps      = 0.01 if "JPY" in symbol else 0.0001
            sl_dist = abs(pos.avg_price - pos.initial_sl)
            if sl_dist < 1e-9:
                continue

            if pos.signal == "buy":
                current_r = (current - pos.avg_price) / sl_dist
            else:
                current_r = (pos.avg_price - current) / sl_dist

            if not pos.tp1_hit and current_r >= HQT_TP1_R:
                closed = self._partial_close(symbol, 0.40, pos)
                if closed:
                    pos.tp1_hit = True
                    self._move_sl_to_be(symbol, pos)
                    pos.breakeven_set = True
                    self._save()
                    print(f"  🎯 HQT TP1 hit {symbol}  R={current_r:.2f}  "
                          f"Closed 40%  SL moved to breakeven {pos.avg_price:.5f}")

            elif pos.tp1_hit and not pos.tp2_hit and current_r >= HQT_TP2_R:
                closed = self._partial_close(symbol, 0.35, pos)
                if closed:
                    pos.tp2_hit = True
                    self._save()
                    print(f"  🎯 HQT TP2 hit {symbol}  R={current_r:.2f}  Closed 35%")

            elif pos.tp2_hit and current_r >= HQT_TP3_R:
                closed = self._partial_close(symbol, 1.0, pos)
                if closed:
                    print(f"  🏆 HQT TP3 hit {symbol}  R={current_r:.2f}  Fully closed!")
                    del self.positions[symbol]
                    self._save()

            elif self._all_closed(symbol, pos):
                print(f"  📋 HQT {symbol} position fully closed — removing")
                del self.positions[symbol]
                self._save()

    def _floating_pct(self, pos: HQTPosition, current: float, balance: float) -> float:
        pips = (current - pos.avg_price) if pos.signal == "buy" \
               else (pos.avg_price - current)
        pv   = 10.0 if "JPY" not in pos.symbol else 1000.0 / max(current, 1)
        ps   = 0.01 if "JPY" in pos.symbol else 0.0001
        pnl  = (pips / ps) * pv * pos.total_lots
        return (pnl / balance * 100) if balance > 0 else 0

    def _partial_close(self, symbol: str, fraction: float, pos: HQTPosition) -> bool:
        lots_to_close = round(pos.total_lots * fraction, 2)
        if lots_to_close < 0.01:
            return False

        tick     = mt5.symbol_info_tick(symbol)
        info     = mt5.symbol_info(symbol)
        if not tick or not info:
            return False

        price    = tick.bid if pos.signal == "buy" else tick.ask
        order_type = (mt5.ORDER_TYPE_SELL if pos.signal == "buy"
                      else mt5.ORDER_TYPE_BUY)

        remaining = lots_to_close
        for entry in pos.entries:
            if remaining <= 0:
                break
            close_lot = min(round(remaining, 2), round(entry["lot"], 2))
            if close_lot < 0.01:
                continue

            req = {
                "action":    mt5.TRADE_ACTION_DEAL,
                "symbol":    symbol,
                "volume":    close_lot,
                "type":      order_type,
                "position":  entry["ticket"],
                "price":     price,
                "deviation": 20,
                "magic":     360002,
                "comment":   f"HQT_partial_{fraction:.0%}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(req)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                remaining     -= close_lot
                entry["lot"]  -= close_lot
                pos.total_lots -= close_lot
            else:
                code = result.retcode if result else "None"
                print(f"  ⚠️  Partial close failed: {code}")

        return True

    def _move_sl_to_be(self, symbol: str, pos: HQTPosition):
        info = mt5.symbol_info(symbol)
        if not info:
            return
        new_sl = round(pos.avg_price, info.digits)
        for entry in pos.entries:
            if entry.get("lot", 0) < 0.01:
                continue
            req = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": entry["ticket"],
                "sl":       new_sl,
                "tp":       round(pos.tp3, info.digits),
            }
            mt5.order_send(req)
        pos.sl = new_sl

    def _all_closed(self, symbol: str, pos: HQTPosition) -> bool:
        live = {p.ticket for p in (mt5.positions_get(symbol=symbol) or [])}
        for entry in pos.entries:
            if entry["ticket"] in live:
                return False
        return True

    def print_summary(self, balance: float):
        if not self.positions:
            return
        print(f"\n  {'─'*55}")
        print(f"  📊 HQT ACTIVE POSITIONS")
        for symbol, pos in self.positions.items():
            tick = mt5.symbol_info_tick(symbol)
            current = (tick.bid if pos.signal == "buy" else tick.ask) if tick else pos.avg_price
            sl_dist = abs(pos.avg_price - pos.initial_sl) or 1e-9
            r = ((current - pos.avg_price) / sl_dist if pos.signal == "buy"
                 else (pos.avg_price - current) / sl_dist)
            float_pct = self._floating_pct(pos, current, balance)
            be_icon   = "🔒BE" if pos.breakeven_set else "   "
            print(f"  {be_icon} {symbol} {pos.signal.upper()}  "
                  f"avg={pos.avg_price:.5f}  lots={pos.total_lots:.2f}  "
                  f"R={r:+.2f}  float={float_pct:+.1f}%  "
                  f"DCA×{pos.dca_count}")
        print(f"  {'─'*55}")


def hqt_tp_prices(signal: str, avg_price: float, sl: float,
                  symbol: str) -> Tuple[float, float, float]:
    sl_dist = abs(avg_price - sl)
    if signal == "buy":
        return (avg_price + sl_dist * HQT_TP1_R,
                avg_price + sl_dist * HQT_TP2_R,
                avg_price + sl_dist * HQT_TP3_R)
    else:
        return (avg_price - sl_dist * HQT_TP1_R,
                avg_price - sl_dist * HQT_TP2_R,
                avg_price - sl_dist * HQT_TP3_R)


def main():
    print(BANNER)

    print("  🔌 Connecting to MetaTrader 5...")

    connected = False

    if mt5.initialize():
        connected = True
        print("  ✅ Connected to open MT5 terminal")
    else:
        err = mt5.last_error()
        print(f"  ❌ initialize() failed: {err}")
        print(f"\n  {'─'*60}")
        print(f"  TROUBLESHOOTING:")
        print(f"  1. Make sure MetaTrader 5 is OPEN and you are LOGGED IN")
        print(f"  2. In MT5: Tools → Options → Expert Advisors")
        print(f"     ✅ Check: 'Allow automated trading'")
        print(f"     ✅ Check: 'Allow DLL imports'")
        print(f"  3. In MT5: Tools → Options → Expert Advisors")
        print(f"     ✅ Check: 'Allow WebRequest for listed URL'")
        print(f"  4. Make sure you are NOT in Strategy Tester")
        print(f"  5. Try running MT5 as Administrator")
        print(f"  {'─'*60}")
        return

    account = mt5.account_info()
    if not account:
        print(f"  ❌ Could not get account info: {mt5.last_error()}")
        print(f"     Make sure you are logged into your ITAFX account in MT5")
        mt5.shutdown(); return

    print(f"  ✅ Account: #{account.login}  |  "
          f"Balance: ${account.balance:,.2f}  |  "
          f"Server: {account.server}")
    print(f"  ✅ Broker: {account.company}")
    print(f"  ✅ Leverage: 1:{account.leverage}  |  "
          f"Currency: {account.currency}  |  "
          f"Type: {'Demo' if account.trade_mode == 0 else 'Real'}")

    print(f"\n  🔍 Checking symbol availability on this broker...")

    def find_symbol(base: str) -> Optional[str]:
        candidates = [
            base,
            base + ".",
            base + "m",
            base + "_Pro",
            base + "pro",
            base.lower(),
            base + ".",
        ]
        all_symbols = [s.name for s in (mt5.symbols_get() or [])]
        for candidate in candidates:
            if candidate in all_symbols:
                mt5.symbol_select(candidate, True)
                return candidate
        for s in all_symbols:
            if s.startswith(base) and len(s) <= len(base) + 4:
                mt5.symbol_select(s, True)
                return s
        return None

    SYMBOL_MAP: Dict[str, str] = {}
    missing = []
    for sym in SYMBOLS:
        found = find_symbol(sym)
        if found:
            SYMBOL_MAP[sym] = found
            status = "✅" if found == sym else f"⚠️  mapped → {found}"
            print(f"     {status}  {sym}")
        else:
            missing.append(sym)
            print(f"     ❌  {sym}  — NOT FOUND on this broker")

    if missing:
        print(f"\n  ⚠️  {len(missing)} symbols not found: {missing}")
        print(f"     Bot will skip these. Add them to MT5 Market Watch if needed.")
        for sym in missing:
            if sym in SYMBOLS:
                SYMBOLS.remove(sym)

    if not SYMBOLS:
        print(f"  ❌ No valid symbols found — check your broker's symbol list")
        mt5.shutdown(); return

    for i, sym in enumerate(SYMBOLS):
        if sym in SYMBOL_MAP and SYMBOL_MAP[sym] != sym:
            SYMBOLS[i] = SYMBOL_MAP[sym]

    print(f"\n  ✅ Trading {len(SYMBOLS)} symbols: {SYMBOLS}")

    print(f"\n  {'═'*62}")
    if IS_PROP_FIRM:
        mode_label = ACCOUNT_MODE.replace("itafx_","ITAFX ").upper()
        print(f"  🏦 {mode_label} — PROP FIRM ACCOUNT")
        print(f"  {'═'*62}")
        print(f"  Daily loss limit:    {MAX_DAILY_DRAWDOWN_PCT}%  (bot stops at {DAILY_DD_PERSONAL_LIMIT:.1f}%)")
        print(f"  Max total drawdown:  {MAX_EQUITY_DRAWDOWN_PCT}%  (bot halts at {TOTAL_DD_PERSONAL_LIMIT:.1f}%)")
        print(f"  Drawdown type:       {'TRAILING' if TRAILING_DRAWDOWN else 'STATIC'}")
        print(f"  Weekend holding:     {'✅ Allowed' if WEEKEND_HOLD_ALLOWED else '❌ NOT ALLOWED — closes Friday 21:00 UTC'}")
        print(f"  Hedging:             ❌ Prohibited")
        print(f"  Martingale:          ❌ Prohibited — 0.5% fixed risk per trade")
        if CONSISTENCY_SCORE_LIMIT > 0:
            print(f"  Consistency limit:   {CONSISTENCY_SCORE_LIMIT}% per day")
    else:
        print(f"  💼 PERSONAL / DEMO ACCOUNT  ({ACCOUNT_MODE.upper()})")
        print(f"  {'═'*62}")
        print(f"  Daily loss limit:    {MAX_DAILY_DRAWDOWN_PCT}%  (relaxed — personal account)")
        print(f"  Max total drawdown:  {MAX_EQUITY_DRAWDOWN_PCT}%")
        print(f"  Weekend holding:     ✅ Allowed")
        print(f"  No prop firm restrictions")
    print(f"  Max trades/day:      {MAX_DAILY_TRADES}")
    print(f"  Risk per trade:      0.5% = ${account.balance * 0.005:,.2f}")
    print(f"  {'═'*62}\n")
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)

    db           = TradeDatabase()
    eq_prot      = EquityCurveProtection(account.balance, db)
    cluster_gd   = ClusteringGuard()
    corr_filt    = CorrelationFilter()
    curr_gd      = CurrencyExposureGuard()
    vol_norm     = SessionVolumeNormaliser()
    state_mach   = BreakoutStateMachine()
    manager      = TradeManager()
    hqt          = HQTManager()
    mc_validator = MonteCarloValidator(db)
    replay_eng   = TradeReplayEngine(db)
    processed_tickets: set = set()

    print("\n  🔍 Scanning MT5 history for trades missed while bot was offline...")
    try:
        import sqlite3 as _sq
        from_dt   = datetime.now() - timedelta(days=30)
        all_deals = mt5.history_deals_get(from_dt, datetime.now())
        recovered = 0

        if all_deals:
            with _sq.connect(db.db_path) as _c:
                closed_db = {r[0] for r in _c.execute(
                    "SELECT ticket FROM trades WHERE exit_price IS NOT NULL")}
                open_db   = {r[0]: {"entry": r[1], "sl": r[2], "tp": r[3],
                                    "signal": r[4], "lot": r[5], "db_id": r[6]}
                             for r in _c.execute(
                    "SELECT ticket, entry_price, sl, tp, signal, lot, id "
                    "FROM trades WHERE exit_price IS NULL")}

            for deal in all_deals:
                if deal.entry != mt5.DEAL_ENTRY_OUT: continue
                if deal.ticket in closed_db:         continue

                order_tix = deal.order
                profit    = deal.profit + deal.commission + deal.swap

                if order_tix in open_db:
                    d = open_db[order_tix]
                    entry_p    = d["entry"]
                    sl_dist    = abs(entry_p - d["sl"]) or 0.0001
                    exit_p     = deal.price
                    realized_r = (exit_p - entry_p) / sl_dist * (
                        1 if d["signal"] == "buy" else -1)
                    hold_mins  = 0.0
                    try:
                        entry_dt  = datetime.fromisoformat(
                            next(r[0] for r in _sq.connect(db.db_path).execute(
                                f"SELECT entry_time FROM trades WHERE id={d['db_id']}")))
                        hold_mins = (datetime.fromtimestamp(deal.time) - entry_dt
                                     ).total_seconds() / 60
                    except Exception:
                        pass

                    db.close_trade(
                        trade_id=d["db_id"], ticket=order_tix,
                        exit_price=exit_p, profit=profit,
                        realized_r=round(realized_r, 3),
                        exit_time=datetime.fromtimestamp(deal.time).isoformat(),
                        holding_minutes=round(hold_mins, 1),
                    )
                    if order_tix in manager.open:
                        manager.close(order_tix, profit, exit_p, realized_r)

                    eq_prot.record(account.balance, "win" if profit > 0 else "loss")
                    processed_tickets.add(deal.ticket)
                    recovered += 1
                    sign = "+" if profit >= 0 else ""
                    print(f"     {'✅' if profit > 0 else '❌'} Recovered "
                          f"#{order_tix} {deal.symbol}  {sign}${profit:.2f}"
                          f"  R:{realized_r:+.2f}")

                elif deal.symbol in SYMBOLS:
                    acct_now = mt5.account_info()
                    db.add_trade({
                        "ticket": order_tix, "symbol": deal.symbol,
                        "signal": "buy" if deal.type == mt5.DEAL_TYPE_BUY else "sell",
                        "state": "RECOVERED", "session": "Unknown",
                        "entry_price": deal.price, "exit_price": deal.price,
                        "sl": 0, "tp": 0, "lot": deal.volume,
                        "risk_percent": 0, "confidence": 0,
                        "execution_score": 0, "opportunity_score": 0,
                        "pressure_score": 0, "expansion_quality": 0,
                        "atr_percentile": 0, "spread_at_entry": 0,
                        "profit": profit,
                        "pnl_percent": (profit / acct_now.balance * 100
                                        if acct_now else 0),
                        "realized_r": 0.0, "holding_minutes": 0.0,
                        "win": profit > 0,
                        "tags": "recovered_on_startup",
                        "edge_strength": "unknown",
                        "entry_time": datetime.fromtimestamp(deal.time).isoformat(),
                        "exit_time":  datetime.fromtimestamp(deal.time).isoformat(),
                    })
                    processed_tickets.add(deal.ticket)
                    recovered += 1
                    sign = "+" if profit >= 0 else ""
                    print(f"     🔁 Imported #{order_tix} {deal.symbol}  "
                          f"{sign}${profit:.2f}")

        msg = (f"  ✅ Recovered {recovered} missed trade(s)" if recovered
               else "  ✅ Database up to date — no missed trades")
        print(msg)
    except Exception as e:
        print(f"  ⚠️  Startup recovery error: {e}")
    prev_states: Dict[str, str] = {s: "RANGE" for s in SYMBOLS}

    stats = db.get_statistics()
    print(f"\n{'─'*60}")
    print(f"  📊  HISTORICAL STATISTICS")
    print(f"{'─'*60}")
    print(f"  Total trades   : {stats['total_trades']}")
    if stats["total_trades"] >= 5:
        print(f"  Win rate       : {stats['win_rate']*100:.1f}%")
        print(f"  Expectancy     : {stats['expectancy']:+.3f}R")
        print(f"  Profit factor  : {stats['profit_factor']:.2f}")
        print(f"  Max drawdown   : {stats['max_drawdown_pct']:.1f}%")

    if stats["total_trades"] >= 10:
        n, mode, sims = mc_validator.load()
        print(f"\n🎲 {n} trades found — running Monte Carlo ({mode}, {sims:,} sims)…")
        mc_validator.run()
        mc_validator.print_report()
        wf = mc_validator.run_walk_forward(db)
        if wf:
            print(f"\n  📐  Walk-Forward Summary")
            for w in wf:
                print(f"    {w['window']:>3}d  train_sharpe={w['train_sharpe']:.2f}  "
                      f"test_sharpe={w['test_sharpe']:.2f}  decay={w['decay_ratio']:.2f}")

        replay_eng.load(100)
        replay_eng.comprehensive_report()

    print(f"\n{'═'*72}")
    print("  🚀  LIVE TRADING — monitoring loop active")
    print(f"  Target PF > {MIN_PROFIT_FACTOR}  |  Target Exp > {MIN_EXPECTANCY_R}R  |  "
          f"Max {MAX_DAILY_TRADES} trades/day")
    print(f"{'═'*72}\n")

    cycle = 0

    try:
        while True:
            cycle += 1

            account = mt5.account_info()
            if not account:
                print("  ⚠️  No account info — retrying in 10s")
                time.sleep(10); continue

            blocked, reason = eq_prot.blocked()
            if blocked:
                print(f"\n🔴 Trading blocked: {reason}  (retry in 5m)")
                time.sleep(300); continue

            risk_mult = eq_prot.risk_multiplier
            if risk_mult == 0:
                print("  🔴 Equity HALT — risk_mult=0 — sleeping 5m")
                time.sleep(300); continue

            if cycle % 10 == 1:
                itafx_line = eq_prot.itafx_status(account.balance)
                print(f"\n  💼 {itafx_line}")

            try:
                harvest_closed_trades(manager, db, eq_prot, processed_tickets)
            except Exception as e:
                print(f"  ⚠️  harvest error: {e}")

            manager.sync()
            poll_closed_positions()  # release MRM-reserved exposure for anything that closed

            if manager.open:
                manage_open_trades(manager)

            utc_hour = datetime.utcnow().hour
            session, s_boost = session_info(utc_hour)
            if session == "Asian":
                now_utc      = datetime.utcnow()
                london_open  = now_utc.replace(hour=7, minute=0, second=0, microsecond=0)
                if now_utc >= london_open:
                    london_open += timedelta(days=1)
                wait_mins = int((london_open - now_utc).total_seconds() / 60)
                lagos_open = london_open + timedelta(hours=1)

                if cycle % 6 == 1:
                    print(f"\n  💤 ASIAN SESSION  UTC={now_utc.strftime('%H:%M')}  "
                          f"Lagos={( now_utc + timedelta(hours=1)).strftime('%H:%M')}")
                    print(f"  ⏳ London opens in {wait_mins} min  "
                          f"({lagos_open.strftime('%H:%M')} Lagos time)")
                    print(f"  💡 Best trading windows (Lagos time):")
                    print(f"     🏦 London Open : 08:00–13:00")
                    print(f"     🔥 Overlap     : 13:00–16:00  ← highest activity")
                    print(f"     🗽 New York    : 16:00–21:00")
                    print(f"  😴 Sleeping until market opens...")
                time.sleep(300)
                continue

            df_eu = fetch_rates("EURUSD")
            if df_eu is None:
                print(f"  ⚠️  EURUSD rates unavailable — retry in 30s")
                time.sleep(30); continue
            df_eu      = add_indicators(df_eu)
            mkt_atr    = float(df_eu["atr_percent"].iloc[-1])
            mkt_vol    = float(df_eu["volume_ratio"].iloc[-1])
            vol_regime = volatility_regime(mkt_atr)

            if cycle == 1 or cycle % 60 == 0:
                intel_report = market_intelligence_report(SYMBOLS)
                print_market_intelligence(intel_report)
            elif 'intel_report' not in dir():
                intel_report = market_intelligence_report(SYMBOLS)

            session_atr_floor = SESSION_ATR_FLOOR.get(session, MIN_ATR_PERCENT_ABSOLUTE)
            effective_floor   = max(MIN_ATR_PERCENT_ABSOLUTE, session_atr_floor)
            if mkt_atr < effective_floor:
                if cycle % 3 == 1:
                    print(f"  ⏸️  ATR {mkt_atr:.3f}% < {effective_floor:.3f}% "
                          f"({session} floor) — waiting for volatility")
                time.sleep(60); continue

            open_cnt      = manager.open_count
            req_score     = min(MAX_SELECTIVITY, BASE_SELECTIVITY + open_cnt * SELECTIVITY_PER_OPEN_TRADE)
            equity_state  = eq_prot.state

            print(f"\n  🔄  Cycle #{cycle}  {datetime.now().strftime('%H:%M:%S')}  "
                  f"{session}  ATR={mkt_atr:.3f}%  Vol={mkt_vol:.1f}×  "
                  f"Regime={vol_regime}  EQ={equity_state}  "
                  f"Open={open_cnt}/{MAX_DAILY_TRADES}  NeedScore≥{req_score}")

            if HQT_ENABLED and hqt.positions:
                hqt.manage_active(account.balance)
                hqt.print_summary(account.balance)

            cycle_executed: set = set()
            cycle_positions: Dict[str, str] = dict(manager.open_symbols)

            for symbol in SYMBOLS:
                try:
                    if symbol in cycle_executed:
                        continue

                    df = fetch_rates(symbol)
                    if df is None:
                        if SCAN_DEBUG: print(f"      ⚠️  {symbol}  no rates returned")
                        continue
                    df   = add_indicators(df)
                    last = df.iloc[-1]

                    cur_hr  = datetime.utcnow().hour
                    raw_vol = float(last["tick_volume"]) if "tick_volume" in df.columns else 1000.0
                    vol_norm.push(symbol, cur_hr, raw_vol)
                    v_norm  = vol_norm.normalised(symbol, cur_hr, raw_vol)

                    def sf(val, fallback=0.0):
                        try:
                            v = float(val)
                            return fallback if np.isnan(v) or np.isinf(v) else v
                        except (TypeError, ValueError):
                            return fallback

                    atr_exp  = sf(last["atr_expansion"], 1.0)
                    atr_rank = sf(last["atr_pct_rank"],  0.50)

                    rank_gate_active = cycle > 80
                    if rank_gate_active and atr_rank < 0.15:
                        if SCAN_DEBUG:
                            print(f"      ⚪ {symbol}  ATR rank {atr_rank:.2f} — dead market")
                        continue

                    pressure = state_mach.pressure_score(df, atr_exp, v_norm)
                    if SCAN_DEBUG:
                        print(f"      🔍 {symbol}  atr_exp={atr_exp:.2f}  v_norm={v_norm:.2f}  "
                              f"rank={atr_rank:.2f}  pressure={pressure}  "
                              f"(need≥{MIN_PRESSURE_SCORE})")
                    if pressure < MIN_PRESSURE_SCORE:
                        continue

                    state, sq, bp = state_mach.classify(
                        symbol, df, pressure, atr_exp, v_norm
                    )
                    exh_dbg = state_mach._exhaustion_score(df)
                    if state != prev_states[symbol]:
                        print(f"      📌 {symbol}  {prev_states[symbol]} → {state}  "
                              f"(pressure={pressure}  bp={bp:.2f}  exh={exh_dbg})")
                        prev_states[symbol] = state
                    elif SCAN_DEBUG:
                        print(f"      📌 {symbol}  state={state}  sq={sq}  "
                              f"pressure={pressure}  bp={bp:.2f}  exh={exh_dbg}")

                    if state == "EXHAUSTION":
                        if SCAN_DEBUG: print(f"      ⏭️  {symbol}  EXHAUSTION — skip")
                        continue

                    if state == "RANGE":
                        if not RANGE_SCALP_ALLOWED:
                            if SCAN_DEBUG:
                                print(f"      🚫 {symbol}  RANGE scalping disabled")
                            continue
                        if bp < MIN_BREAKOUT_PROBABILITY:
                            if SCAN_DEBUG:
                                print(f"      🚫 {symbol}  RANGE bp={bp:.2f} < {MIN_BREAKOUT_PROBABILITY}")
                            continue

                    sym_atr_pct   = float(df["atr_percent"].iloc[-1])
                    state_atr_min = MIN_ATR_PERCENT_PER_STATE.get(state, MIN_ATR_PERCENT_ABSOLUTE)
                    if sym_atr_pct < state_atr_min:
                        if SCAN_DEBUG:
                            print(f"      🚫 {symbol}  ATR {sym_atr_pct:.3f}% < {state_atr_min:.3f}% floor for {state}")
                        continue

                    if v_norm < MIN_VOLUME_RATIO:
                        if SCAN_DEBUG:
                            print(f"      🚫 {symbol}  vol {v_norm:.2f}× < {MIN_VOLUME_RATIO}× minimum")
                        continue

                    utc_now    = datetime.utcnow()
                    news_block, news_reason = is_near_news(utc_now.hour, utc_now.minute)
                    if news_block:
                        if SCAN_DEBUG:
                            print(f"      📰 {symbol}  {news_reason}")
                        continue

                    structure = market_structure(df, STRUCTURE_LOOKBACK)
                    if SCAN_DEBUG:
                        pref = structure_bias(structure)
                        print(f"      🏗️  {symbol}  structure={structure}  preferred={pref}")

                    signal, raw_conf = generate_signal(df, state, bp, symbol, structure)
                    last_tmp  = df.iloc[-1]
                    rh_d      = float(last_tmp["range_high_20"])
                    rl_d      = float(last_tmp["range_low_20"])
                    rw_d      = rh_d - rl_d
                    pp        = round((float(last_tmp["close"]) - rl_d) / rw_d * 100, 1) if rw_d > 0 else 50
                    rsi_d     = round(float(last_tmp.get("rsi", 50)), 1)
                    if SCAN_DEBUG:
                        print(f"      📡 {symbol}  signal={signal}  raw_conf={raw_conf}  "
                              f"pos={pp}%  rsi={rsi_d}")
                    if signal == "hold":
                        continue

                    struct_ok, struct_reason = structure_allows_trade(signal, structure)
                    if not struct_ok:
                        if SCAN_DEBUG:
                            print(f"      🏗️  {symbol}  {struct_reason}")
                        continue

                    smc      = smc_analysis(df)
                    candles  = candle_intelligence(df)
                    liq_sc   = liquidity_sweep_score(df, signal)
                    smc_ok      = (smc["smc_buy"] if signal == "buy" else smc["smc_sell"])
                    smc_partial = smc["at_eq"] or (smc["bull_bos"] if signal == "buy" else smc["bear_bos"])
                    pat_ok      = ((candles["bull_engulf"] or candles["bull_pin"] or
                                    candles["bull_marubozu"] or candles["dragonfly_doji"] or
                                    candles["morning_star"] or candles["three_white_soldiers"] or
                                    candles["tweezer_bottom"] or candles["rising_three"])
                                   if signal == "buy" else
                                   (candles["bear_engulf"] or candles["bear_pin"] or
                                    candles["bear_marubozu"] or candles["gravestone_doji"] or
                                    candles["evening_star"] or candles["three_black_crows"] or
                                    candles["tweezer_top"] or candles["falling_three"]))

                    candle_aligned = (candles["candle_bias"] == signal[:4] or
                                      candles["candle_grade"] in ("A", "B"))
                    candle_neutral = candles["candle_bias"] == "neutral"

                    if not candle_aligned and not candle_neutral and not smc_ok:
                        if SCAN_DEBUG:
                            print(f"      🕯️  {symbol}  Candle BLOCKED — "
                                  f"bias={candles['candle_bias']} vs signal={signal}  "
                                  f"grade={candles['candle_grade']}  "
                                  f"score={candles['candle_score']}")
                        continue

                    if not smc_ok and not smc_partial and not pat_ok and liq_sc == 0:
                        if SCAN_DEBUG:
                            print(f"      🚫 {symbol}  No SMC/candle confirmation — skip "
                                  f"(SMC={smc_ok} partial={smc_partial} pat={pat_ok} liq={liq_sc})")
                        continue

                    if SCAN_DEBUG:
                        top_p = " + ".join(candles["top_patterns"]) or "none"
                        cl_s  = candles.get(f"checklist_{signal}_score", 0)
                        print(f"      🧠 {symbol}  SMC={'✅' if smc_ok else ('~' if smc_partial else '❌')}  "
                              f"EQ={smc.get('at_eq')}  BOS={'bull' if smc.get('bull_bos') else ('bear' if smc.get('bear_bos') else 'no')}  "
                              f"FVG={'bull' if smc.get('bull_fvg') else ('bear' if smc.get('bear_fvg') else 'no')}  "
                              f"Struct={structure}  Liq={liq_sc}  Patterns:[{top_p}]  Grade:{candles['candle_grade']}")

                    if "JPY" in symbol:
                        reject_reason = None
                        if signal == "buy"  and rsi_d < 30:
                            reject_reason = f"RSI {rsi_d} < 30 (falling knife)"
                        elif signal == "sell" and rsi_d > 70:
                            reject_reason = f"RSI {rsi_d} > 70 (rising knife)"
                        if reject_reason is None and bp < 0.65:
                            reject_reason = f"bp={bp:.2f} < 0.65 (weak breakout)"
                        if reject_reason is None and pressure < 65:
                            reject_reason = f"pressure={pressure} < 65 (insufficient)"
                        if reject_reason is None and v_norm < 1.2:
                            reject_reason = f"vol {v_norm:.2f}× < 1.2× (low participation)"
                        info_tmp   = mt5.symbol_info(symbol)
                        jpy_spread = (info_tmp.spread / 10.0) if info_tmp else 9.9
                        if reject_reason is None and jpy_spread > 2.5:
                            reject_reason = f"spread {jpy_spread:.1f}pip > 2.5pip"
                        if reject_reason:
                            if SCAN_DEBUG:
                                print(f"      🇯🇵 {symbol}  REJECTED: {reject_reason}")
                            continue

                    info = mt5.symbol_info(symbol)
                    if not info: continue
                    sp_pips = info.spread / 10.0
                    max_sp  = MAX_SPREAD_JPY if "JPY" in symbol else MAX_SPREAD_NORMAL
                    if sp_pips > max_sp:
                        if SCAN_DEBUG:
                            print(f"      🚫 {symbol}  spread {sp_pips:.1f}pip > {max_sp}pip max")
                        continue

                    conf = adaptive_confidence(
                        df, symbol, signal, sp_pips, s_boost,
                        pressure, sq, bp, v_norm, atr_exp,
                    )
                    conf = int(conf * 0.70 + raw_conf * 0.30)

                    intel_bonus = intelligence_confidence_bonus(
                        symbol, signal, intel_report
                    )
                    conf = int(np.clip(conf + intel_bonus, 0, 100))
                    intel_data  = intel_report.get(symbol, {})
                    intel_bias  = intel_data.get("bias", "neutral")
                    intel_str   = intel_data.get("strength", 0)

                    required_conf = max(
                        MIN_CONFIDENCE_SCORE,
                        MIN_CONFIDENCE_BY_STATE.get(state, MIN_CONFIDENCE_SCORE)
                    )
                    if SCAN_DEBUG:
                        aligned = "✅" if signal == intel_bias else ("⚠️" if intel_bias != "neutral" else "➡️")
                        print(f"      🔍 {symbol}  adaptive_conf={conf}  need≥{required_conf}  "
                              f"spread={sp_pips:.1f}pip  "
                              f"fundamental={intel_bias}({intel_str}%) {aligned} bonus={intel_bonus:+d}")
                    if conf < required_conf:
                        continue

                    exec_sc = 82 if sp_pips < 0.8 else (72 if sp_pips < 1.2 else 62)
                    if exec_sc < MIN_EXECUTION_SCORE:
                        continue

                    mem         = state_mach.memory(symbol)
                    state_bonus = STATE_QUALITY.get(state, 40)

                    smc_bonus = 20 if smc_ok else (10 if smc_partial else 0)

                    candle_bonus = {"A": 20, "B": 15, "C": 8, "D": 3, "F": 0}.get(
                        candles["candle_grade"], 0
                    )
                    if candles["three_white_soldiers"] or candles["three_black_crows"]:
                        candle_bonus = min(20, candle_bonus + 5)
                    if candles["morning_star"] or candles["evening_star"]:
                        candle_bonus = min(20, candle_bonus + 4)

                    str_bonus = 10 if structure in ("BULLISH", "BEARISH") else 0

                    mom_bonus = 5 if (candles["bull_momentum"] >= 4 and signal == "buy") or \
                                     (candles["bear_momentum"] >= 4 and signal == "sell") else 0

                    opp = int(
                        conf          * 0.32 +
                        exec_sc       * 0.18 +
                        pressure      * 0.14 +
                        state_bonus   * 0.10 +
                        smc_bonus     * 0.10 +
                        candle_bonus  * 0.10 +
                        str_bonus     * 0.03 +
                        liq_sc        * 0.02 +
                        mom_bonus     * 0.01
                    )
                    pat_bonus = candle_bonus

                    if SCAN_DEBUG:
                        print(f"      🎯 {symbol}  opp={opp}  need≥{req_score}  "
                              f"conf={conf}  exec={exec_sc}  pressure={pressure}  "
                              f"smc={smc_bonus}  candle={candle_bonus}(grade:{candles['candle_grade']})  "
                              f"str={str_bonus}  liq={liq_sc}  mom={mom_bonus}")
                    if opp < req_score:
                        continue

                    confluence_count = pre_entry_candle_study(
                        symbol=symbol, df=df, signal=signal,
                        candles=candles, smc=smc, structure=structure,
                        confidence=conf, opp=opp,
                    )
                    if confluence_count < 4:
                        print(f"      ⛔ {symbol}  Confluence {confluence_count}/12 < 4 — SKIP")
                        continue

                    tick = mt5.symbol_info_tick(symbol)
                    if not tick: continue
                    entry = tick.ask if signal == "buy" else tick.bid
                    pt    = symbol_point(symbol)

                    ok, msg = cluster_gd.allow(symbol, entry, pt)
                    if not ok:
                        continue

                    ok, msg = corr_filt.allow(symbol, cycle_positions)
                    if not ok:
                        print(f"      ⛔ {symbol} corr: {msg}")
                        continue

                    ok, msg = curr_gd.allow(symbol, signal, cycle_positions)
                    if not ok:
                        print(f"      ⛔ {symbol} FX exp: {msg}")
                        continue

                    ok, msg = manager.can_trade_today()
                    if not ok:
                        if SCAN_DEBUG:
                            print(f"      ⏸️  {symbol}  {msg}")
                        break

                    ok, secs = manager.can_trade_state(state, symbol)
                    if not ok:
                        if SCAN_DEBUG:
                            print(f"      ⏳ {symbol}  State cooldown {secs}s remaining")
                        continue

                    ps      = pip_size(symbol)
                    atr_val = float(df["atr"].iloc[-1])

                    atr_mult = ATR_SL_MULTIPLIER.get(vol_regime, 1.5)
                    target_rr = 1.5
                    for max_atr, tbl_sl_mult, tbl_rr in ATR_RR_TABLE:
                        if sym_atr_pct < max_atr:
                            atr_mult  = tbl_sl_mult
                            target_rr = tbl_rr
                            break

                    if "JPY" in symbol:
                        atr_mult  = round(atr_mult * 1.4, 2)
                        target_rr = round(target_rr * 1.1, 2)

                    target_rr += STATE_RR_BONUS.get(state, 0.0)
                    if bp > 0.60:
                        target_rr += TP_RR_HIGH_BP_BONUS
                    if opp > 80:
                        target_rr += 0.2

                    atr_sl_dist = atr_val * atr_mult

                    lookback   = df.iloc[-(SWING_LOOKBACK_BARS + 1):-1]
                    swing_low  = float(lookback["low"].min())
                    swing_high = float(lookback["high"].max())

                    info_sym   = mt5.symbol_info(symbol)
                    spread_pts = info_sym.spread * info_sym.point if info_sym else 0
                    sl_buffer  = spread_pts * SL_BUFFER_SPREAD_MULT

                    if signal == "buy":
                        sl_structure = swing_low  - sl_buffer
                        sl_atr       = entry      - atr_sl_dist
                        sl           = min(sl_structure, sl_atr)
                    else:
                        sl_structure = swing_high + sl_buffer
                        sl_atr       = entry      + atr_sl_dist
                        sl           = max(sl_structure, sl_atr)

                    min_sl_dist = spread_pts * 1.5
                    if signal == "buy"  and (entry - sl) < min_sl_dist:
                        sl = entry - min_sl_dist
                    if signal == "sell" and (sl - entry) < min_sl_dist:
                        sl = entry + min_sl_dist

                    raw_sl_dist = abs(entry - sl)
                    sl_pips     = int(raw_sl_dist / ps) if ps > 0 else 15
                    min_floor   = MIN_SL_PIPS.get(symbol, 14)
                    max_cap     = MAX_SL_PIPS.get(symbol, 55)
                    sl_pips     = int(np.clip(sl_pips, min_floor, max_cap))

                    sl_dist = sl_pips * ps
                    sl      = entry - sl_dist if signal == "buy" else entry + sl_dist

                    tp_dist = sl_dist * target_rr
                    tp      = entry + tp_dist if signal == "buy" else entry - tp_dist

                    _, sl, tp = validate_stops(symbol, entry, sl, tp)

                    sl_pips  = max(1, int(abs(entry - sl) / ps))
                    tp_pips  = max(1, int(abs(entry - tp) / ps))
                    rr_ratio = round(tp_pips / sl_pips, 2) if sl_pips else 0

                    if SCAN_DEBUG:
                        print(f"      📐 {symbol}  SL={sl_pips}pip  TP={tp_pips}pip  "
                              f"RR=1:{rr_ratio}  ATRmult={atr_mult}  BaseRR={target_rr:.2f}")

                    base_risk  = min(MAX_RISK_PER_TRADE, 0.80 * s_boost)
                    final_risk = base_risk * risk_mult
                    if opp < 72:    final_risk *= 0.80
                    elif opp > 85:  final_risk *= 1.10
                    if "JPY" in symbol:
                        final_risk *= 0.75
                    if smc_ok and pat_bonus > 0:
                        final_risk = min(MAX_RISK_PER_TRADE, final_risk * 1.15)
                    final_risk = float(np.clip(final_risk, 0.10, MAX_RISK_PER_TRADE))

                    # ── Master Risk Manager gate ─────────────────────────────
                    # Ask the MRM before sizing/sending. It sees every other
                    # bot's open exposure; we don't. Fail-closed: if the MRM
                    # is unreachable, risk_client.py already returns
                    # approved=False rather than raising, so this behaves
                    # the same as a normal rejection.
                    mrm_decision = risk.request_trade(
                        symbol=symbol,
                        direction="BUY" if signal == "buy" else "SELL",
                        proposed_risk_pct=final_risk,
                        strategy_tag=f"v36_itafx_{state.lower()}",
                    )
                    if not mrm_decision.approved:
                        if SCAN_DEBUG:
                            print(f"      🛑 {symbol}  MRM rejected: {mrm_decision.reason}")
                        continue
                    if mrm_decision.approved_risk_pct != final_risk:
                        final_risk = mrm_decision.approved_risk_pct

                    lot = dynamic_lot_size(account.balance, final_risk, entry, sl, symbol)

                    edge   = edge_label(opp)
                    smc_tag = "SMC✅" if smc_ok else ("SMC~" if smc_partial else "SMC❌")
                    pat_tag = ""
                    # Fixed: this was 'patterns' in the original file, which was never
                    # defined -- would have raised NameError on every trade about to
                    # execute. 'candles' is the actual variable in scope here.
                    if candles["bull_engulf"] or candles["bear_engulf"]: pat_tag = "Engulf"
                    elif candles["bull_pin"]  or candles["bear_pin"]:    pat_tag = "PinBar"
                    elif candles["inside_bar"]:                            pat_tag = "InsideBar"
                    tags   = generate_tags(state, session, v_norm, conf, edge)

                    print(f"\n   ✅ {symbol} {signal.upper()}  "
                          f"State:{state}  Score:{opp}  Conf:{conf}  "
                          f"SL:{sl_pips}pip  TP:{tp_pips}pip  RR:1:{rr_ratio}  "
                          f"Lot:{lot}  Risk:{final_risk:.2f}%  "
                          f"{smc_tag}  {pat_tag}  Struct:{structure}")

                    req = {
                        "action":       mt5.TRADE_ACTION_DEAL,
                        "symbol":       symbol,
                        "volume":       lot,
                        "type":         mt5.ORDER_TYPE_BUY if signal == "buy" else mt5.ORDER_TYPE_SELL,
                        "price":        entry,
                        "sl":           sl,
                        "tp":           tp,
                        "deviation":    20,
                        "magic":        999999,
                        "comment":      f"{state[:8]}_{opp}",
                        "type_time":    mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_FOK,
                    }
                    res = mt5.order_send(req)

                    if not (res and res.retcode == mt5.TRADE_RETCODE_DONE):
                        # Broker rejected/failed the order after MRM approved it
                        # -- release the reserved exposure.
                        risk.reject_fill(mrm_decision.position_id)

                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        risk.confirm_fill(mrm_decision.position_id)
                        track_reservation(res.order, mrm_decision.position_id)
                        cluster_gd.record(symbol, entry)
                        cycle_executed.add(symbol)
                        cycle_positions[symbol] = signal

                        trade = TradeRecord(
                            ticket=res.order, symbol=symbol, signal=signal,
                            entry_price=entry, sl=sl, tp=tp, lot=lot,
                            risk_percent=final_risk, confidence=conf,
                            opportunity_score=opp, execution_score=exec_sc,
                            pressure_score=pressure, expansion_quality=0,
                            atr_percentile=atr_rank, spread_at_entry=sp_pips,
                            state=state, entry_time=datetime.now().isoformat(),
                            session=session, tags=tags, edge_strength=edge,
                        )

                        if manager.register(trade):
                            db_id = db.add_trade({
                                "ticket":          res.order,
                                "symbol":          symbol,
                                "signal":          signal,
                                "state":           state,
                                "session":         session,
                                "entry_price":     entry,
                                "sl":              sl,
                                "tp":              tp,
                                "lot":             lot,
                                "risk_percent":    final_risk,
                                "confidence":      conf,
                                "execution_score": exec_sc,
                                "opportunity_score": opp,
                                "pressure_score":  pressure,
                                "expansion_quality": 0,
                                "atr_percentile":  atr_rank,
                                "spread_at_entry": sp_pips,
                                "tags":            tags,
                                "edge_strength":   edge,
                                "entry_time":      datetime.now().isoformat(),
                            })
                            trade.db_id = db_id

                            rw  = float(last["range_high_20"] - last["range_low_20"])
                            rp  = ((entry - float(last["range_low_20"])) / rw * 100) if rw > 0 else 50
                            db.add_snapshot({
                                "trade_id": db_id, "symbol": symbol,
                                "snapshot_time": datetime.now().isoformat(),
                                "price": entry, "atr_percent": float(last["atr_percent"]),
                                "atr_expansion": atr_exp, "volume_ratio": float(last.get("volume_ratio", 1)),
                                "volume_normalized": v_norm, "momentum": float(last.get("momentum", 0)),
                                "range_width": rw, "range_position_pct": rp,
                                "state": state, "pressure_score": pressure,
                                "breakout_probability": bp, "spread_pips": sp_pips,
                                "session": session, "hour_of_day": utc_hour,
                                "day_of_week": datetime.now().weekday(),
                            })

                            db.add_feature_attribution(db_id, [
                                {"name": "pressure_score",      "value": pressure,    "contribution": pressure / 100,    "direction": "positive"},
                                {"name": "state_quality",       "value": sq,          "contribution": sq / 100,          "direction": "positive"},
                                {"name": "execution_score",     "value": exec_sc,     "contribution": exec_sc / 100,     "direction": "positive"},
                                {"name": "atr_expansion",       "value": atr_exp,     "contribution": min(1.0, atr_exp - 0.5), "direction": "positive" if atr_exp > 1.0 else "negative"},
                                {"name": "target_rr",           "value": target_rr,   "contribution": target_rr / 6,     "direction": "positive"},
                                {"name": "breakout_probability","value": bp,          "contribution": bp,                "direction": "positive" if bp > 0.5 else "negative"},
                                {"name": "volume_normalized",   "value": v_norm,      "contribution": min(1.0, v_norm / 2), "direction": "positive" if v_norm > 1.0 else "negative"},
                                {"name": "confidence",          "value": conf,        "contribution": conf / 100,        "direction": "positive"},
                            ])

                            eq_prot.record(account.balance)

                            if HQT_ENABLED:
                                tp1, tp2, tp3 = hqt_tp_prices(signal, entry, sl, symbol)
                                hqt.register_initial(
                                    symbol=symbol, signal=signal,
                                    ticket=res.order, lot=lot,
                                    entry_px=entry, sl=sl,
                                    tp1=tp1, tp2=tp2, tp3=tp3,
                                )

                            print(f"   ✅  Order #{res.order}  lot={lot}  "
                                  f"{risk_bar(final_risk, 15)}  {final_risk:.2f}%")
                    else:
                        err = res.comment if res else "unknown"
                        retcode = res.retcode if res else "?"
                        print(f"   ❌ Order failed [{retcode}]: {err}")

                except Exception as e:
                    print(f"   ⚠️  {symbol} error: {type(e).__name__}: {e}")
                    traceback.print_exc()

            if HQT_ENABLED and hqt.positions:
                acct_now = mt5.account_info()
                if acct_now:
                    for sym, pos in list(hqt.positions.items()):
                        tick = mt5.symbol_info_tick(sym)
                        if not tick:
                            continue
                        current_px = tick.bid if pos.signal == "buy" else tick.ask
                        should_add, dca_reason = hqt.should_add_entry(
                            sym, current_px, acct_now.balance
                        )
                        if should_add:
                            print(f"\n  ➕ HQT DCA  {sym}  {dca_reason}")
                            info = mt5.symbol_info(sym)
                            mt5.symbol_select(sym, True)
                            price = tick.ask if pos.signal == "buy" else tick.bid
                            req   = {
                                "action":    mt5.TRADE_ACTION_DEAL,
                                "symbol":    sym,
                                "volume":    HQT_LOT_PER_ENTRY,
                                "type":      (mt5.ORDER_TYPE_BUY if pos.signal == "buy"
                                              else mt5.ORDER_TYPE_SELL),
                                "price":     price,
                                "sl":        round(pos.sl, info.digits if info else 5),
                                "deviation": 20,
                                "magic":     360003,
                                "comment":   f"HQT_DCA#{pos.dca_count+1}",
                                "type_time": mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                            }
                            res = mt5.order_send(req)
                            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                hqt.add_entry(sym, res.order, HQT_LOT_PER_ENTRY, price)
                            else:
                                code = res.retcode if res else "?"
                                print(f"  ❌ DCA order failed [{code}]")

            if cycle % 20 == 0:
                eq_prot.record(account.balance)

            if cycle % PERFORMANCE_DISPLAY_CYCLES == 0:
                acct2 = mt5.account_info()
                bal   = acct2.balance if acct2 else account.balance
                print_live_performance(db, manager, bal)

            time.sleep(CHECK_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\n\n  🛑  Stopped by user")
    except Exception as e:
        print(f"\n❌  Unexpected error: {e}")
        traceback.print_exc()
    finally:
        acct_final = mt5.account_info()
        bal_final  = acct_final.balance if acct_final else 0
        print_live_performance(db, manager, bal_final)

        if stats["total_trades"] >= 10:
            replay_eng.load(100)
            replay_eng.comprehensive_report()
            n, mode, _ = mc_validator.load()
            if n >= 10:
                mc_validator.run()
                mc_validator.print_report()

        mt5.shutdown()
        print("\n  Disconnected from MetaTrader 5.")
        print(f"{'═'*72}\n")


if __name__ == "__main__":
    main()
