# simple_trade_bot.py - BABSBOOKS PROFESSIONAL TRADING BOT V46.0 (MECHANICAL MIND EDITION)
"""
================================================================================
 BABSBOOKS PROFESSIONAL TRADING BOT V46.0 - MECHANICAL MIND EDITION
================================================================================

ARCHITECTURE: 3 INDEPENDENT STRATEGY MODULES — CONFLUENCE REQUIRED
────────────────────────────────────────────────────────────────────────────────

 STRATEGY 1 — TREND ENGINE (mechanical)
   • EMA 20/50 crossover defines direction
   • EMA 200 as trend quality filter (scoring bonus, not hard gate)
   • ADX ≥ 15 confirms momentum exists
   • Entry on pullback to EMA20 — not chasing, waiting for price to come back

 STRATEGY 2 — STRUCTURE + SMC
   • Range high/low breakout with 2-candle confirmation
   • Liquidity sweep detection (stop hunt before real move)
   • BB squeeze → expansion entries (compression before breakout)
   • RSI in 35–65 zone on entry (room to run, not exhausted)

 STRATEGY 3 — MOMENTUM SCALPER
   • RSI cross of 50 line = momentum shift
   • Stochastic K cross of D from extreme zones
   • Volume surge ≥ 1.3x average
   • Only fires in direction of Strategy 1 trend

 CONFLUENCE RULE: minimum 2 of 3 strategies must agree on direction
 SCORING: each strategy contributes points — total must exceed MIN_ENTRY_SCORE

EXIT OVERHAUL (fixing "gives back profit" problem):
   • Breakeven locked at 1.0R
   • Partial close 30% at 1.5R
   • Partial close 30% at 2.5R
   • Trail remaining 40% with 1x H1 ATR (tight lock, wide enough to not shake out)
   • Hard time exit: 6 hours if not yet at 1R

RISK MANAGEMENT (funded account safe — unchanged):
   • Max risk per trade: 1%
   • Max daily drawdown: 1.5%
   • Max equity drawdown: 8%
   • Floating loss cap: 5%
   • Max lot size: 0.50
   • Kelly Criterion sizing (25% fraction, capped at 1%)

================================================================================
"""

import time
import pandas as pd
import numpy as np
import sqlite3
import traceback
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

import MetaTrader5 as mt5

# ── Master Risk Manager integration ─────────────────────────────────────────
from bot_risk_config import risk, get_symbols, track_reservation, poll_closed_positions

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    filename='bot_v46_gold.log', level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("BabsBot")

VERSION   = "46.1-MECHANICAL-MIND-GOLD"
SEPARATOR = "=" * 100
print(f"\n{SEPARATOR}")
print(f"🧠  BABSBOOKS PROFESSIONAL TRADING BOT  —  {VERSION}")
print(f"{SEPARATOR}")
print("   3-Strategy Confluence | Mechanical Rules | Funded Account Ready")
print(f"{SEPARATOR}\n")
log.info(f"Bot starting — version {VERSION}")

# ─────────────────────────────────────────────────────────────────────────────
# SYMBOLS & TIMEFRAME
# ─────────────────────────────────────────────────────────────────────────────
SYMBOLS        = get_symbols(["EURUSD", "GBPUSD", "AUDUSD", "EURJPY", "GBPJPY", "EURGBP", "XAUUSD"])
GOLD_SYMBOLS   = {"XAUUSD"}   # routed through S4 Gold Engine
TIMEFRAME = mt5.TIMEFRAME_M5
BARS      = 250

# ─────────────────────────────────────────────────────────────────────────────
# CONFLUENCE & SCORING
# ─────────────────────────────────────────────────────────────────────────────
MIN_STRATEGIES_AGREE  = 2      # at least 2 of 3 must signal same direction
MIN_ENTRY_SCORE       = 45     # total score threshold
MAX_SCORE             = 100

# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
EMA_FAST      = 20
EMA_MED       = 50
EMA_SLOW      = 200
RSI_PERIOD    = 14
ADX_MIN       = 15
STOCH_PERIOD  = 14
BBAND_PERIOD  = 20
BBAND_STD     = 2.0
VOLUME_SURGE  = 1.3     # Strategy 3 volume requirement

# ── XAUUSD / Gold-specific settings (S4 Gold Engine) ─────────────────────────
GOLD_MACD_FAST     = 12      # MACD fast EMA
GOLD_MACD_SLOW     = 26      # MACD slow EMA
GOLD_MACD_SIGNAL   = 9       # MACD signal line
GOLD_VWAP_PERIOD   = 20      # Volume-weighted MA period (VWAP approximation)
GOLD_FIBO_LOOKBACK = 50      # Bars to find swing H/L for Fibonacci zones
GOLD_FIBO_LEVELS   = [0.236, 0.382, 0.500, 0.618, 0.786]
GOLD_MIN_SCORE     = 40      # Gold engine min score to contribute to confluence
GOLD_SESSIONS      = {"London", "Overlap", "NewYork"}  # Gold only trades these

# Entry RSI range — must have room to run
RSI_ENTRY_MIN = 35      # don't buy below this (already too oversold to confirm)
RSI_ENTRY_MAX = 65      # don't buy above this (overbought)

# ─────────────────────────────────────────────────────────────────────────────
# EARLY FILTER THRESHOLDS (relaxed for more trade opportunities)
# ─────────────────────────────────────────────────────────────────────────────
MIN_TREND_STRENGTH    = 0.30
MIN_PRESSURE_SCORE    = 18
MIN_MARKET_QUALITY    = 0.10
MIN_ATR_PERCENT_ABS   = 0.007
MIN_VOLUME_RATIO      = 0.30
BREAKOUT_CONFIRM_BARS = 2

# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT (funded account safe)
# ─────────────────────────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE       = 1.0
MAX_DAILY_RISK           = 4.0   # raised: allows 4x 1% trades per day
MAX_DAILY_DRAWDOWN_PCT   = 1.5
MAX_EQUITY_DRAWDOWN_PCT  = 8.0
MAX_CONSECUTIVE_LOSSES   = 3
MAX_OPEN_TRADES          = 3
MAX_DAILY_TRADES         = 10
FLOATING_LOSS_CAP_PCT    = 5.0
KELLY_CAP                = 0.25

SIZING_STEP_DOWN = {0: 1.0, 1: 1.0, 2: 0.7, 3: 0.5}

# ─────────────────────────────────────────────────────────────────────────────
# EXIT SYSTEM (overhauled — locks profit, doesn't give it back)
# ─────────────────────────────────────────────────────────────────────────────
PROFIT_TARGETS = [
    {"r_multiple": 1.5, "close_percent": 0.30, "action": "partial"},
    {"r_multiple": 2.5, "close_percent": 0.30, "action": "partial"},
    {"r_multiple": 6.0, "close_percent": 0.40, "action": "runner"},
]

TRAILING_CONFIG = {
    "activation_r": 2.0,     # start trailing at 2R
    "distance_atr": 1.0,     # 1x H1 ATR distance — tight enough to lock, wide enough to hold
    "step_atr":     0.3,
}

BREAKEVEN_CONFIG = {
    "activation_r": 1.0,     # move to breakeven at 1R
    "add_pips":     3,        # 3 pip buffer above entry
}

TIME_EXIT_HOURS = 6          # close if not at 1R within 6 hours

# ─────────────────────────────────────────────────────────────────────────────
# TARGET R:R BY STATE
# ─────────────────────────────────────────────────────────────────────────────
TARGET_RISK_REWARD = {
    "RANGE":           {"min_rr": 1.5, "target_rr": 2.0, "max_rr": 3.0},
    "COMPRESSION":     {"min_rr": 2.5, "target_rr": 3.5, "max_rr": 5.0},
    "BREAKOUT_SETUP":  {"min_rr": 3.0, "target_rr": 4.5, "max_rr": 6.0},
    "BREAKOUT_ACTIVE": {"min_rr": 3.5, "target_rr": 5.0, "max_rr": 7.0},
    "EXPANSION":       {"min_rr": 4.0, "target_rr": 5.5, "max_rr": 8.0},
    "EXHAUSTION":      {"min_rr": 1.5, "target_rr": 2.0, "max_rr": 2.5},
}

VOLATILITY_RR_MULTIPLIERS = {"low": 0.9, "normal": 1.0, "high": 1.2}

STATE_QUALITY = {
    "RANGE": 40, "COMPRESSION": 60, "BREAKOUT_SETUP": 75,
    "BREAKOUT_ACTIVE": 100, "EXPANSION": 85, "EXHAUSTION": 30,
}

# ─────────────────────────────────────────────────────────────────────────────
# SPREAD LIMITS
# ─────────────────────────────────────────────────────────────────────────────
SPREAD_LIMITS = {
    "EURUSD": 2.5, "GBPUSD": 2.5, "AUDUSD": 2.5,
    "EURJPY": 4.0, "GBPJPY": 5.5, "EURGBP": 2.5,
    "XAUUSD": 50.0,   # Gold spread in points (wider — 50 pts ~ $0.50)
}

# ─────────────────────────────────────────────────────────────────────────────
# CURRENCY MAP
# ─────────────────────────────────────────────────────────────────────────────
CURRENCY_MAP = {
    "EURUSD": {"EUR": -1, "USD": 1}, "GBPUSD": {"GBP": -1, "USD": 1},
    "AUDUSD": {"AUD": -1, "USD": 1}, "EURJPY": {"EUR": -1, "JPY": 1},
    "GBPJPY": {"GBP": -1, "JPY": 1}, "EURGBP": {"EUR": -1, "GBP": 1},
    "XAUUSD": {"USD": 1},   # Gold: USD exposure only
}

CORRELATION_THRESHOLD  = 0.70
MAX_CURRENCY_EXPOSURE  = 1.5

# ─────────────────────────────────────────────────────────────────────────────
# SESSION TIMING
# ─────────────────────────────────────────────────────────────────────────────
def get_session(utc_hour: int) -> Tuple[str, float]:
    if 12 <= utc_hour < 16:  return "Overlap", 1.3
    elif 7 <= utc_hour < 12: return "London",  1.1
    elif 16 <= utc_hour < 20: return "NewYork", 0.9
    return "Asian", 0.5

# ─────────────────────────────────────────────────────────────────────────────
# MISC
# ─────────────────────────────────────────────────────────────────────────────
CHECK_INTERVAL                  = 30
MIN_TIME_BETWEEN_TRADES_SECONDS = 90
MIN_PRICE_DISTANCE_PIPS         = 5
DB_PATH                         = "trading_bot_v46.db"
SHOW_REJECTION_REASONS          = True
SHOW_PERFORMANCE_METRICS        = True

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def pip_size(symbol: str) -> float:
    if symbol == "XAUUSD": return 0.01    # 1 point = $0.01 for gold
    return 0.01 if "JPY" in symbol else 0.0001

def pip_value(symbol: str) -> float:
    # Value per pip/point per 0.01 lot
    if symbol == "XAUUSD": return 1.0     # $1 per point per 0.01 lot
    return 9.5 if "JPY" in symbol else 10.0

def symbol_point(symbol: str) -> float:
    try:
        info = mt5.symbol_info(symbol)
        return info.point if info else (0.001 if "JPY" in symbol else 0.0001)
    except: return 0.001 if "JPY" in symbol else 0.0001

def volatility_regime(atr_pct: float) -> str:
    if atr_pct < 0.025: return "low"
    elif atr_pct < 0.045: return "normal"
    return "high"

def safe_rates(symbol: str, tf, bars: int) -> Optional[pd.DataFrame]:
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) < 60: return None
        return pd.DataFrame(rates)
    except Exception as e:
        log.error(f"safe_rates({symbol}): {e}"); return None

def validate_stops(symbol: str, entry: float, sl: float, tp: float):
    try:
        info = mt5.symbol_info(symbol)
        if not info: return True, sl, tp
        min_dist = info.trade_stops_level * info.point
        if min_dist > 0 and abs(entry - sl) < min_dist:
            sl = (entry - min_dist) if sl < entry else (entry + min_dist)
        return True, sl, tp
    except: return True, sl, tp

def risk_bar(risk_pct: float, width: int = 20) -> str:
    filled = min(int((risk_pct / 1.0) * width), width)
    char = "🟢" if risk_pct < 0.5 else ("🟡" if risk_pct < 0.75 else ("🟠" if risk_pct < 0.9 else "🔴"))
    return char * filled + "⚪" * max(0, width - filled)


# ─────────────────────────────────────────────────────────────────────────────
# DEBUG PRINTER
# ─────────────────────────────────────────────────────────────────────────────
class DP:
    @staticmethod
    def header(t): print(f"\n{'='*80}\n📊 {t}\n{'='*80}"); log.info(f"--- {t} ---")
    @staticmethod
    def sub(t): print(f"\n{'─'*60}\n🔍 {t}\n{'─'*60}")
    @staticmethod
    def ok(t): print(f"   ✅ {t}"); log.info(t)
    @staticmethod
    def err(t): print(f"   ❌ {t}"); log.error(t)
    @staticmethod
    def warn(t): print(f"   ⚠️  {t}"); log.warning(t)
    @staticmethod
    def info(t): print(f"   ℹ️  {t}"); log.info(t)


# ─────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df   = df.copy()
    close = df['close']; high = df['high']; low = df['low']

    # EMAs
    df['ema_20']  = close.ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_50']  = close.ewm(span=EMA_MED,  adjust=False).mean()
    df['ema_200'] = close.ewm(span=EMA_SLOW, adjust=False).mean()

    # Volume ratio
    vc = 'tick_volume' if 'tick_volume' in df.columns else 'real_volume'
    if vc in df.columns:
        vsma = df[vc].rolling(20).mean().fillna(df[vc])
        df['volume_ratio'] = (df[vc] / vsma.replace(0, 1)).clip(0.3, 3.0)
    else:
        df['volume_ratio'] = 1.0

    # ATR
    df['tr'] = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    df['atr']           = df['tr'].rolling(14).mean()
    df['atr_percent']   = df['atr'] / close * 100
    atr_sma             = df['atr'].rolling(20).mean().fillna(df['atr'])
    df['atr_expansion'] = (df['atr'] / atr_sma.replace(0, 1)).clip(0.5, 2.0)

    # Range levels
    df['range_high_20']   = high.rolling(20).max()
    df['range_low_20']    = low.rolling(20).min()
    df['rolling_high_50'] = high.rolling(50).max()
    df['rolling_low_50']  = low.rolling(50).min()

    # Candle patterns
    body_hi = df[['close','open']].max(axis=1)
    body_lo = df[['close','open']].min(axis=1)
    c_range = (high - low).replace(0, 1e-6)
    df['upper_wick_ratio'] = (high - body_hi) / c_range
    df['lower_wick_ratio'] = (body_lo - low)  / c_range
    df['long_upper_wick']  = df['upper_wick_ratio'] > 0.6
    df['long_lower_wick']  = df['lower_wick_ratio'] > 0.6

    # Liquidity sweeps
    df['liquidity_sweep_up']   = (high > df['rolling_high_50'].shift(1)) & (close < df['rolling_high_50'].shift(1))
    df['liquidity_sweep_down'] = (low  < df['rolling_low_50'].shift(1))  & (close > df['rolling_low_50'].shift(1))

    # Momentum
    df['momentum']       = close.pct_change(5) * 100
    df['close_pressure'] = ((close - low) / c_range).clip(0, 1)

    # RSI
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean().replace(0, 1e-6)
    df['rsi'] = 100 - (100 / (1 + avg_gain / avg_loss))
    df['rsi_prev'] = df['rsi'].shift(1)

    # ADX
    up_move  = high.diff(); dn_move = (-low.diff())
    plus_dm  = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    minus_dm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    tr14     = df['tr'].rolling(14).sum().replace(0, 1e-6)
    plus_di  = 100 * (plus_dm.rolling(14).sum() / tr14)
    minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)
    dx_denom = (plus_di + minus_di).replace(0, 1e-6)
    dx       = 100 * (plus_di - minus_di).abs() / dx_denom
    df['adx']      = dx.rolling(14).mean()
    df['plus_di']  = plus_di
    df['minus_di'] = minus_di

    # Stochastic
    low_n  = low.rolling(STOCH_PERIOD).min()
    high_n = high.rolling(STOCH_PERIOD).max()
    denom  = (high_n - low_n).replace(0, 1e-6)
    df['stoch_k']      = ((close - low_n) / denom * 100).clip(0, 100)
    df['stoch_d']      = df['stoch_k'].rolling(3).mean()
    df['stoch_k_prev'] = df['stoch_k'].shift(1)
    df['stoch_d_prev'] = df['stoch_d'].shift(1)

    # Bollinger Bands
    bb_mid         = close.rolling(BBAND_PERIOD).mean()
    bb_std         = close.rolling(BBAND_PERIOD).std().fillna(0)
    df['bb_upper'] = bb_mid + BBAND_STD * bb_std
    df['bb_lower'] = bb_mid - BBAND_STD * bb_std
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / bb_mid.replace(0, 1e-6)
    bb_wma         = df['bb_width'].rolling(20).mean()
    df['bb_squeeze'] = df['bb_width'] < bb_wma * 0.8

    # EMA20 pullback proximity (for Strategy 1 entry timing)
    df['ema20_dist_pct'] = (close - df['ema_20']).abs() / df['atr'].replace(0, 1e-6)

    # ── MACD (for S4 Gold Engine) ─────────────────────────────────────────────
    ema_fast_macd      = close.ewm(span=GOLD_MACD_FAST,   adjust=False).mean()
    ema_slow_macd      = close.ewm(span=GOLD_MACD_SLOW,   adjust=False).mean()
    df['macd_line']    = ema_fast_macd - ema_slow_macd
    df['macd_signal']  = df['macd_line'].ewm(span=GOLD_MACD_SIGNAL, adjust=False).mean()
    df['macd_hist']    = df['macd_line'] - df['macd_signal']
    df['macd_line_prev']   = df['macd_line'].shift(1)
    df['macd_signal_prev'] = df['macd_signal'].shift(1)
    df['macd_hist_prev']   = df['macd_hist'].shift(1)

    # ── VWAP approximation (volume-weighted moving average) ───────────────────
    vc2 = 'tick_volume' if 'tick_volume' in df.columns else 'real_volume'
    if vc2 in df.columns and df[vc2].sum() > 0:
        typical_price   = (high + low + close) / 3
        vol_series      = df[vc2].replace(0, 1)
        vwap_num        = (typical_price * vol_series).rolling(GOLD_VWAP_PERIOD).sum()
        vwap_den        = vol_series.rolling(GOLD_VWAP_PERIOD).sum().replace(0, 1)
        df['vwap']      = vwap_num / vwap_den
    else:
        df['vwap']      = close.rolling(GOLD_VWAP_PERIOD).mean()


    # ── ICHIMOKU CLOUD ────────────────────────────────────────────────────────
    # Tenkan-sen (Conversion): 9-period midpoint
    hi9  = high.rolling(9).max();  lo9  = low.rolling(9).min()
    df['ichi_tenkan']  = (hi9 + lo9) / 2
    # Kijun-sen (Base): 26-period midpoint
    hi26 = high.rolling(26).max(); lo26 = low.rolling(26).min()
    df['ichi_kijun']   = (hi26 + lo26) / 2
    # Senkou Span A (Lead 1): avg of tenkan+kijun, shifted forward 26
    df['ichi_span_a']  = ((df['ichi_tenkan'] + df['ichi_kijun']) / 2).shift(26)
    # Senkou Span B (Lead 2): 52-period midpoint, shifted forward 26
    hi52 = high.rolling(52).max(); lo52 = low.rolling(52).min()
    df['ichi_span_b']  = ((hi52 + lo52) / 2).shift(26)
    # Chikou (Lagging): close shifted back 26
    df['ichi_chikou']  = close.shift(-26)
    # Cloud top/bottom for current price position
    df['ichi_cloud_top']    = df[['ichi_span_a','ichi_span_b']].max(axis=1)
    df['ichi_cloud_bottom'] = df[['ichi_span_a','ichi_span_b']].min(axis=1)
    df['ichi_bullish_cloud'] = df['ichi_span_a'] > df['ichi_span_b']
    df['ichi_above_cloud']   = close > df['ichi_cloud_top']
    df['ichi_below_cloud']   = close < df['ichi_cloud_bottom']
    df['ichi_in_cloud']      = ~df['ichi_above_cloud'] & ~df['ichi_below_cloud']
    df['ichi_tk_cross_up']   = (df['ichi_tenkan'] > df['ichi_kijun']) & (df['ichi_tenkan'].shift(1) <= df['ichi_kijun'].shift(1))
    df['ichi_tk_cross_down'] = (df['ichi_tenkan'] < df['ichi_kijun']) & (df['ichi_tenkan'].shift(1) >= df['ichi_kijun'].shift(1))

    # ── ORDER BLOCKS ──────────────────────────────────────────────────────────
    # An order block is the last bearish candle before a bullish impulse (buy OB)
    # or the last bullish candle before a bearish impulse (sell OB).
    # We detect them over the last 30 bars and store the most recent valid zone.
    ob_lookback = min(30, len(df) - 2)
    df['ob_buy_high']  = np.nan
    df['ob_buy_low']   = np.nan
    df['ob_sell_high'] = np.nan
    df['ob_sell_low']  = np.nan
    for i in range(2, ob_lookback):
        idx = len(df) - 1 - i
        if idx < 2: break
        c_open  = df['open'].iloc[idx]
        c_close = df['close'].iloc[idx]
        c_high  = df['high'].iloc[idx]
        c_low   = df['low'].iloc[idx]
        # Bullish impulse after bearish candle = buy order block
        next_close = df['close'].iloc[idx + 1]
        next_open  = df['open'].iloc[idx + 1]
        if c_close < c_open:   # bearish candle
            if next_close > next_open and (next_close - next_open) > (c_open - c_close) * 0.8:
                if np.isnan(df['ob_buy_high'].iloc[-1]):
                    df.iloc[-1, df.columns.get_loc('ob_buy_high')] = c_high
                    df.iloc[-1, df.columns.get_loc('ob_buy_low')]  = c_low
        # Bearish impulse after bullish candle = sell order block
        if c_close > c_open:   # bullish candle
            if next_close < next_open and (next_open - next_close) > (c_close - c_open) * 0.8:
                if np.isnan(df['ob_sell_high'].iloc[-1]):
                    df.iloc[-1, df.columns.get_loc('ob_sell_high')] = c_high
                    df.iloc[-1, df.columns.get_loc('ob_sell_low')]  = c_low

    # ── RSI DIVERGENCE ────────────────────────────────────────────────────────
    # Bullish divergence: price makes lower low, RSI makes higher low
    # Bearish divergence: price makes higher high, RSI makes lower high
    # Check over last 14 bars (2 pivot points minimum)
    div_bars = min(14, len(df) - 2)
    df['rsi_bull_div'] = False
    df['rsi_bear_div'] = False
    if len(df) > div_bars + 2:
        price_window = close.iloc[-div_bars:]
        rsi_window   = df['rsi'].iloc[-div_bars:]
        p_min_idx = price_window.idxmin(); p_max_idx = price_window.idxmax()
        r_min_idx = rsi_window.idxmin();   r_max_idx = rsi_window.idxmax()
        curr_price = float(close.iloc[-1]); curr_rsi = float(df['rsi'].iloc[-1])
        min_price  = float(price_window.min()); min_rsi = float(rsi_window.min())
        max_price  = float(price_window.max()); max_rsi = float(rsi_window.max())
        # Bullish divergence: current price near/at low but RSI higher than low
        price_at_low = curr_price <= min_price * 1.001
        rsi_above_low = curr_rsi > min_rsi * 1.05   # RSI 5% higher than its low
        if price_at_low and rsi_above_low:
            df.iloc[-1, df.columns.get_loc('rsi_bull_div')] = True
        # Bearish divergence: current price near/at high but RSI lower than high
        price_at_high = curr_price >= max_price * 0.999
        rsi_below_high = curr_rsi < max_rsi * 0.95
        if price_at_high and rsi_below_high:
            df.iloc[-1, df.columns.get_loc('rsi_bear_div')] = True

    # ── SUPPLY & DEMAND ZONES ─────────────────────────────────────────────────
    # Supply zone: area where price dropped sharply (sellers overwhelmed buyers)
    # Demand zone: area where price rallied sharply (buyers overwhelmed sellers)
    # Identified by: large candle body + breakout from consolidation
    sd_lookback = min(50, len(df) - 2)
    df['demand_zone_high'] = np.nan; df['demand_zone_low'] = np.nan
    df['supply_zone_high'] = np.nan; df['supply_zone_low'] = np.nan
    atr_series = df['atr'].fillna(df['atr'].mean())
    for i in range(3, sd_lookback):
        idx = len(df) - 1 - i
        if idx < 1: break
        c_o = df['open'].iloc[idx]; c_c = df['close'].iloc[idx]
        c_h = df['high'].iloc[idx]; c_l = df['low'].iloc[idx]
        c_atr = float(atr_series.iloc[idx]) if not np.isnan(atr_series.iloc[idx]) else 1.0
        body  = abs(c_c - c_o)
        # Large body candle (1.5× ATR) = impulsive move = zone origin
        if body > c_atr * 1.5:
            if c_c > c_o:  # bullish impulse = demand zone below
                if np.isnan(df['demand_zone_high'].iloc[-1]):
                    df.iloc[-1, df.columns.get_loc('demand_zone_high')] = max(c_o, c_c)
                    df.iloc[-1, df.columns.get_loc('demand_zone_low')]  = min(c_o, c_c)
            else:           # bearish impulse = supply zone above
                if np.isnan(df['supply_zone_high'].iloc[-1]):
                    df.iloc[-1, df.columns.get_loc('supply_zone_high')] = max(c_o, c_c)
                    df.iloc[-1, df.columns.get_loc('supply_zone_low')]  = min(c_o, c_c)

    df = df.bfill().ffill()
    return df


# ─────────────────────────────────────────────────────────────────────────────
# HTF DATA
# ─────────────────────────────────────────────────────────────────────────────
def get_htf_data(symbol: str) -> Tuple:
    results = []
    for tf, bars in [(mt5.TIMEFRAME_M15, 120), (mt5.TIMEFRAME_H1, 150), (mt5.TIMEFRAME_H4, 100)]:
        df = safe_rates(symbol, tf, bars)
        results.append(add_indicators(df) if df is not None else None)
    return tuple(results)

def htf_bias(df_m15, df_h1, df_h4) -> Tuple[str, int]:
    """Returns overall HTF bias direction and score."""
    if any(x is None for x in [df_m15, df_h1, df_h4]):
        return "neutral", 0
    def td(df):
        if df is None or len(df) < 55: return "neutral"
        l = df.iloc[-1]
        if l['ema_20'] > l['ema_50']: return "bullish"
        if l['ema_20'] < l['ema_50']: return "bearish"
        return "neutral"
    t15, t1, t4 = td(df_m15), td(df_h1), td(df_h4)
    directions = [t15, t1, t4]
    bulls = directions.count("bullish")
    bears = directions.count("bearish")
    if bulls == 3: return "bullish", 25
    if bears == 3: return "bearish", 25
    if bulls == 2: return "bullish", 15
    if bears == 2: return "bearish", 15
    return "neutral", 0


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY 1 — TREND ENGINE
#  Mechanical EMA crossover with pullback entry timing
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def strategy_trend_engine(df: pd.DataFrame, df_h1=None) -> Tuple[str, int, Dict]:
    """
    Returns: (signal, score, details)
    signal: 'buy' | 'sell' | 'hold'
    score:  0–35 (contribution to total)
    """
    last = df.iloc[-1]
    details = {}

    # Direction: EMA20 vs EMA50
    if last['ema_20'] > last['ema_50']:
        direction = "buy"
    elif last['ema_20'] < last['ema_50']:
        direction = "sell"
    else:
        details['reason'] = "EMA20/50 flat"
        return "hold", 0, details

    # EMA200 alignment bonus
    ema200_aligned = (direction == "buy" and last['close'] > last['ema_200']) or \
                     (direction == "sell" and last['close'] < last['ema_200'])
    details['ema200'] = ema200_aligned

    # ADX: trend has momentum
    adx = last.get('adx', 0)
    if adx < ADX_MIN:
        details['reason'] = f"ADX too weak ({adx:.0f})"
        return "hold", 0, details
    details['adx'] = adx

    # ADX direction alignment
    plus_di = last.get('plus_di', 0); minus_di = last.get('minus_di', 0)
    di_aligned = (direction == "buy" and plus_di > minus_di) or \
                 (direction == "sell" and minus_di > plus_di)
    if not di_aligned:
        details['reason'] = f"DI not aligned (+DI={plus_di:.0f} -DI={minus_di:.0f})"
        return "hold", 0, details

    # Pullback to EMA20: price within 1.5x ATR of EMA20
    ema20_dist = last.get('ema20_dist_pct', 99)
    near_ema20 = ema20_dist < 1.5
    details['near_ema20'] = near_ema20
    details['ema20_dist'] = ema20_dist

    # Score
    score = 0
    score += 15                              # base: direction confirmed by EMA + ADX
    score += 10 if ema200_aligned else 0    # EMA200 trend quality bonus
    score += 10 if near_ema20 else 5        # pullback timing (5 pts even if not perfect)

    details['direction'] = direction
    details['score'] = score
    return direction, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY 2 — STRUCTURE + SMC
#  Breakout/sweep/squeeze entries at key levels
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def strategy_structure_smc(df: pd.DataFrame, state: str) -> Tuple[str, int, Dict]:
    """
    Returns: (signal, score, details)
    score: 0–35
    """
    last = df.iloc[-1]
    details = {}
    rh    = last['range_high_20']
    rl    = last['range_low_20']
    price = last['close']
    rsi   = last.get('rsi', 50)

    # RSI must have room to run
    # Note: for XAUUSD the RSI gate is handled inside S4 Gold Engine with
    # gold-specific zones — S2 uses a slightly wider gate here to not block
    # oversold gold bounces (RSI 25-70 accepted, extremes handled in S4)
    if not (25 <= rsi <= 75):
        details['reason'] = f"RSI extreme ({rsi:.0f})"
        return "hold", 0, details

    # Liquidity sweep
    sweep_up   = bool(df['liquidity_sweep_up'].tail(3).any())
    sweep_down = bool(df['liquidity_sweep_down'].tail(3).any())
    bb_squeeze = bool(last.get('bb_squeeze', False))
    vol_ratio  = float(last.get('volume_ratio', 1.0))

    signal = "hold"
    score  = 0

    if state in ["BREAKOUT_ACTIVE", "EXPANSION"]:
        # Price broke out — confirm with 2 closes
        def count_confirms(direction):
            tail = df['close'].tail(BREAKOUT_CONFIRM_BARS)
            return int((tail > rh).sum()) if direction == "up" else int((tail < rl).sum())

        if price > rh:
            confirms = count_confirms("up")
            if confirms >= BREAKOUT_CONFIRM_BARS:
                signal = "buy"
                score  = 20
                score += 10 if sweep_up else 0
                score += 5  if vol_ratio > 1.3 else 0

        elif price < rl:
            confirms = count_confirms("down")
            if confirms >= BREAKOUT_CONFIRM_BARS:
                signal = "sell"
                score  = 20
                score += 10 if sweep_down else 0
                score += 5  if vol_ratio > 1.3 else 0

    elif state in ["BREAKOUT_SETUP", "COMPRESSION"]:
        if (sweep_up or bb_squeeze) and price > rh * 0.998:
            signal = "buy"
            score  = 15
            score += 10 if sweep_up else 0
            score += 10 if bb_squeeze else 0

        elif (sweep_down or bb_squeeze) and price < rl * 1.002:
            signal = "sell"
            score  = 15
            score += 10 if sweep_down else 0
            score += 10 if bb_squeeze else 0

    elif state == "RANGE":
        # Mean reversion at range edges
        if price <= rl * 1.005 and last.get('long_lower_wick', False):
            signal = "buy"
            score  = 10
            score += 5 if sweep_down else 0   # sweep = stop hunt before bounce

        elif price >= rh * 0.995 and last.get('long_upper_wick', False):
            signal = "sell"
            score  = 10
            score += 5 if sweep_up else 0

    details.update({
        'signal': signal, 'score': score, 'rsi': rsi,
        'sweep_up': sweep_up, 'sweep_down': sweep_down,
        'bb_squeeze': bb_squeeze, 'vol_ratio': vol_ratio
    })
    return signal, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY 3 — MOMENTUM SCALPER
#  RSI 50-cross + Stochastic cross + volume surge
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def strategy_momentum(df: pd.DataFrame, trend_direction: str) -> Tuple[str, int, Dict]:
    """
    Returns: (signal, score, details)
    score: 0–30
    Only fires in direction of Strategy 1 trend.
    """
    last    = df.iloc[-1]
    details = {}

    rsi      = last.get('rsi', 50)
    rsi_prev = last.get('rsi_prev', 50)
    sk       = last.get('stoch_k', 50)
    sd       = last.get('stoch_d', 50)
    sk_prev  = last.get('stoch_k_prev', 50)
    sd_prev  = last.get('stoch_d_prev', 50)
    vol      = last.get('volume_ratio', 1.0)

    # RSI 50-line cross
    rsi_cross_up   = rsi_prev < 50 and rsi >= 50
    rsi_cross_down = rsi_prev > 50 and rsi <= 50

    # Stochastic cross from extreme zone
    stoch_cross_up   = sk_prev <= sd_prev and sk > sd and sk < 60
    stoch_cross_down = sk_prev >= sd_prev and sk < sd and sk > 40

    # Volume surge
    vol_surge = vol >= VOLUME_SURGE

    signal = "hold"
    score  = 0

    if trend_direction == "buy":
        if rsi_cross_up or stoch_cross_up:
            signal = "buy"
            score += 10 if rsi_cross_up   else 0
            score += 10 if stoch_cross_up  else 0
            score += 10 if vol_surge       else 5

    elif trend_direction == "sell":
        if rsi_cross_down or stoch_cross_down:
            signal = "sell"
            score += 10 if rsi_cross_down  else 0
            score += 10 if stoch_cross_down else 0
            score += 10 if vol_surge        else 5

    details.update({
        'signal': signal, 'score': score,
        'rsi_cross_up': rsi_cross_up, 'rsi_cross_down': rsi_cross_down,
        'stoch_cross_up': stoch_cross_up, 'stoch_cross_down': stoch_cross_down,
        'vol_surge': vol_surge, 'vol': vol
    })
    return signal, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  CONFLUENCE ENGINE
#  Combines all 3 strategies — requires MIN_STRATEGIES_AGREE
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY 4 — GOLD ENGINE (XAUUSD only)
#  Inspired by XAUBOT techniques: MACD crossover, VWAP institutional levels,
#  Fibonacci retracement zones, multi-timeframe trend bias, news-aware session
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def fibonacci_zones(df: pd.DataFrame) -> Dict:
    """
    Find swing high and low over GOLD_FIBO_LOOKBACK bars,
    then compute Fibonacci retracement levels.
    Returns dict with levels and direction of last swing.
    """
    tail  = df.tail(GOLD_FIBO_LOOKBACK)
    swing_high = float(tail['high'].max())
    swing_low  = float(tail['low'].min())
    hi_idx     = tail['high'].idxmax()
    lo_idx     = tail['low'].idxmin()
    swing_range = swing_high - swing_low

    # Determine if last major move was up or down
    # (which index is more recent — high or low)
    swing_up = hi_idx > lo_idx   # high came after low = upswing, retrace = buy zone

    levels = {}
    for fib in GOLD_FIBO_LEVELS:
        if swing_up:
            # Retracing down from high — fib zones are support (buy)
            levels[fib] = swing_high - fib * swing_range
        else:
            # Retracing up from low — fib zones are resistance (sell)
            levels[fib] = swing_low + fib * swing_range

    return {"swing_up": swing_up, "swing_high": swing_high,
            "swing_low": swing_low, "levels": levels}


def price_near_fibo(price: float, fib_data: Dict, atr: float, tolerance: float = 0.5) -> Tuple[bool, float]:
    """
    Check if price is within tolerance*ATR of any Fibonacci level.
    Returns (is_near, nearest_level)
    """
    levels = fib_data.get("levels", {})
    for fib, level in levels.items():
        if abs(price - level) <= tolerance * atr:
            return True, level
    return False, 0.0


def strategy_gold_engine(df: pd.DataFrame, df_h1=None, df_h4=None) -> Tuple[str, int, Dict]:
    """
    S4 — Gold Engine for XAUUSD only.
    Signals from: MACD crossover, VWAP position, Fibonacci zones,
    H1/H4 trend bias, RSI confirmation.
    Returns: (signal, score, details)
    score: 0–40 contribution to total
    """
    last    = df.iloc[-1]
    details = {}

    price   = float(last['close'])
    atr     = float(last.get('atr', 1.0))
    rsi     = float(last.get('rsi', 50))
    vwap    = float(last.get('vwap', price))

    # ── 1. MACD crossover (primary gold momentum signal) ─────────────────────
    macd_line      = float(last.get('macd_line', 0))
    macd_sig       = float(last.get('macd_signal', 0))
    macd_hist      = float(last.get('macd_hist', 0))
    macd_hist_prev = float(last.get('macd_hist_prev', 0))
    macd_line_prev = float(last.get('macd_line_prev', 0))
    macd_sig_prev  = float(last.get('macd_signal_prev', 0))

    # Crossover: MACD line crossing signal line
    macd_cross_up   = macd_line_prev <= macd_sig_prev and macd_line > macd_sig
    macd_cross_down = macd_line_prev >= macd_sig_prev and macd_line < macd_sig
    # Histogram turning: momentum shift even before crossover
    macd_hist_turning_up   = macd_hist_prev < 0 and macd_hist > macd_hist_prev
    macd_hist_turning_down = macd_hist_prev > 0 and macd_hist < macd_hist_prev

    details['macd_cross_up']   = macd_cross_up
    details['macd_cross_down'] = macd_cross_down
    details['macd_above_zero'] = macd_line > 0

    # ── 2. VWAP position (institutional reference level) ─────────────────────
    vwap_bullish = price > vwap
    vwap_dist_atr = abs(price - vwap) / atr   # distance in ATR units

    details['vwap_bullish'] = vwap_bullish
    details['vwap'] = vwap
    details['vwap_dist_atr'] = vwap_dist_atr

    # ── 3. Fibonacci zone proximity ───────────────────────────────────────────
    fib_data     = fibonacci_zones(df)
    near_fib, fib_level = price_near_fibo(price, fib_data, atr, tolerance=0.6)
    fib_swing_up = fib_data.get("swing_up", True)

    details['near_fib']    = near_fib
    details['fib_level']   = fib_level
    details['fib_swing_up'] = fib_swing_up

    # ── 4. HTF trend bias from H1 and H4 ─────────────────────────────────────
    def ema_dir(dff):
        if dff is None or len(dff) < 55: return "neutral"
        l = dff.iloc[-1]
        if l.get('ema_20', 0) > l.get('ema_50', 0): return "bullish"
        if l.get('ema_20', 0) < l.get('ema_50', 0): return "bearish"
        return "neutral"

    h1_dir = ema_dir(df_h1)
    h4_dir = ema_dir(df_h4)
    htf_gold_bull = h1_dir == "bullish" or h4_dir == "bullish"
    htf_gold_bear = h1_dir == "bearish" or h4_dir == "bearish"
    htf_aligned   = h1_dir == h4_dir and h1_dir != "neutral"

    details['h1_dir'] = h1_dir
    details['h4_dir'] = h4_dir
    details['htf_aligned'] = htf_aligned

    # ── 5. RSI confirmation ───────────────────────────────────────────────────
    rsi_ok_buy  = rsi < 40 or (45 <= rsi <= 65)   # oversold bounce OR mid-range bullish
    rsi_ok_sell = rsi > 60 or (35 <= rsi <= 55)   # overbought drop OR mid-range bearish
    rsi_oversold   = rsi < 35    # strong oversold — extra bonus for buy
    rsi_overbought = rsi > 65    # strong overbought — extra bonus for sell
    details['rsi'] = rsi
    details['rsi_oversold']   = rsi_oversold
    details['rsi_overbought'] = rsi_overbought

    signal = "hold"
    score  = 0

    # BUY conditions (need 3 of 5)
    buy_signals = 0
    if macd_cross_up or macd_hist_turning_up: buy_signals += 1
    if vwap_bullish:                           buy_signals += 1
    if near_fib and fib_swing_up:             buy_signals += 1   # fib support in upswing
    if htf_gold_bull:                          buy_signals += 1
    if rsi_ok_buy:                             buy_signals += 1

    # SELL conditions (need 3 of 5)
    sell_signals = 0
    if macd_cross_down or macd_hist_turning_down: sell_signals += 1
    if not vwap_bullish:                           sell_signals += 1
    if near_fib and not fib_swing_up:             sell_signals += 1   # fib resistance in downswing
    if htf_gold_bear:                              sell_signals += 1
    if rsi_ok_sell:                                sell_signals += 1

    if buy_signals >= 3:
        signal = "buy"
        score += 10 if (macd_cross_up or macd_hist_turning_up) else 0
        score += 8  if vwap_bullish else 0
        score += 8  if (near_fib and fib_swing_up) else 0
        score += 8  if htf_gold_bull else 0
        score += 4  if htf_aligned else 0
        score += 6  if rsi_ok_buy else 0
        score += 4  if macd_line > 0 else 0          # MACD above zero = strong bull
        score += 4  if rsi_oversold else 0            # bonus: oversold reversal setup

    elif sell_signals >= 3:
        signal = "sell"
        score += 10 if (macd_cross_down or macd_hist_turning_down) else 0
        score += 8  if not vwap_bullish else 0
        score += 8  if (near_fib and not fib_swing_up) else 0
        score += 8  if htf_gold_bear else 0
        score += 4  if htf_aligned else 0
        score += 6  if rsi_ok_sell else 0
        score += 4  if macd_line < 0 else 0           # MACD below zero = strong bear
        score += 4  if rsi_overbought else 0           # bonus: overbought reversal setup

    details['buy_signals']  = buy_signals
    details['sell_signals'] = sell_signals
    details['score']        = score
    details['signal']       = signal
    return signal, min(40, score), details



# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  ICHIMOKU ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def analyse_ichimoku(df: pd.DataFrame, signal: str) -> Tuple[bool, int, Dict]:
    """
    Returns (aligns_with_signal, score_contribution, details).
    Score: 0-25 contributed to confluence counter.
    """
    last = df.iloc[-1]
    details = {}

    above_cloud   = bool(last.get('ichi_above_cloud', False))
    below_cloud   = bool(last.get('ichi_below_cloud', False))
    in_cloud      = bool(last.get('ichi_in_cloud', True))
    bull_cloud    = bool(last.get('ichi_bullish_cloud', False))
    tk_cross_up   = bool(last.get('ichi_tk_cross_up', False))
    tk_cross_down = bool(last.get('ichi_tk_cross_down', False))
    tenkan        = float(last.get('ichi_tenkan', 0))
    kijun         = float(last.get('ichi_kijun', 0))
    price         = float(last['close'])

    details.update({
        'above_cloud': above_cloud, 'below_cloud': below_cloud,
        'in_cloud': in_cloud, 'bull_cloud': bull_cloud,
        'tk_cross_up': tk_cross_up, 'tk_cross_down': tk_cross_down,
        'tenkan': round(tenkan, 5), 'kijun': round(kijun, 5),
    })

    score = 0
    aligns = False

    if signal == "buy":
        if above_cloud:     score += 10; aligns = True
        elif in_cloud:      score += 3          # neutral — cloud acts as support
        if bull_cloud:      score += 5
        if tk_cross_up:     score += 8; aligns = True
        if tenkan > kijun:  score += 4
    elif signal == "sell":
        if below_cloud:     score += 10; aligns = True
        elif in_cloud:      score += 3
        if not bull_cloud:  score += 5
        if tk_cross_down:   score += 8; aligns = True
        if tenkan < kijun:  score += 4

    # In cloud with no cross = weak/neutral — don't count as alignment
    if in_cloud and not tk_cross_up and not tk_cross_down:
        aligns = False

    details['score'] = score
    details['aligns'] = aligns
    return aligns, min(25, score), details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  ORDER BLOCK ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def analyse_order_blocks(df: pd.DataFrame, signal: str) -> Tuple[bool, int, Dict]:
    """
    Check if current price is inside or near an order block zone.
    Score: 0-20
    """
    last  = df.iloc[-1]
    price = float(last['close'])
    atr   = float(last.get('atr', 1.0))
    details = {}

    ob_buy_h  = last.get('ob_buy_high',  np.nan)
    ob_buy_l  = last.get('ob_buy_low',   np.nan)
    ob_sell_h = last.get('ob_sell_high', np.nan)
    ob_sell_l = last.get('ob_sell_low',  np.nan)

    in_buy_ob  = not np.isnan(ob_buy_h)  and (ob_buy_l  - atr*0.3) <= price <= (ob_buy_h  + atr*0.3)
    in_sell_ob = not np.isnan(ob_sell_h) and (ob_sell_l - atr*0.3) <= price <= (ob_sell_h + atr*0.3)
    near_buy_ob  = not np.isnan(ob_buy_h)  and abs(price - ob_buy_h)  < atr * 1.0
    near_sell_ob = not np.isnan(ob_sell_h) and abs(price - ob_sell_l) < atr * 1.0

    details.update({
        'in_buy_ob': in_buy_ob, 'in_sell_ob': in_sell_ob,
        'near_buy_ob': near_buy_ob, 'near_sell_ob': near_sell_ob,
        'ob_buy_zone':  f"{ob_buy_l:.5f}-{ob_buy_h:.5f}"  if not np.isnan(ob_buy_h)  else "none",
        'ob_sell_zone': f"{ob_sell_l:.5f}-{ob_sell_h:.5f}" if not np.isnan(ob_sell_h) else "none",
    })

    score  = 0
    aligns = False
    if signal == "buy":
        if in_buy_ob:    score += 20; aligns = True
        elif near_buy_ob: score += 10; aligns = True
    elif signal == "sell":
        if in_sell_ob:    score += 20; aligns = True
        elif near_sell_ob: score += 10; aligns = True

    details['score'] = score
    details['aligns'] = aligns
    return aligns, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  RSI DIVERGENCE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def analyse_rsi_divergence(df: pd.DataFrame, signal: str) -> Tuple[bool, int, Dict]:
    """
    Detect RSI divergence — one of the strongest reversal signals.
    Score: 0-20
    """
    last = df.iloc[-1]
    bull_div = bool(last.get('rsi_bull_div', False))
    bear_div = bool(last.get('rsi_bear_div', False))
    rsi      = float(last.get('rsi', 50))

    details = {
        'bull_div': bull_div, 'bear_div': bear_div, 'rsi': round(rsi, 1)
    }

    score  = 0
    aligns = False

    if signal == "buy" and bull_div:
        score  = 20
        aligns = True
    elif signal == "sell" and bear_div:
        score  = 20
        aligns = True

    details['score']  = score
    details['aligns'] = aligns
    return aligns, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  SUPPLY & DEMAND ZONE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def analyse_supply_demand(df: pd.DataFrame, signal: str) -> Tuple[bool, int, Dict]:
    """
    Check if price is at a supply or demand zone.
    Score: 0-20
    """
    last  = df.iloc[-1]
    price = float(last['close'])
    atr   = float(last.get('atr', 1.0))

    dz_h = last.get('demand_zone_high', np.nan)
    dz_l = last.get('demand_zone_low',  np.nan)
    sz_h = last.get('supply_zone_high', np.nan)
    sz_l = last.get('supply_zone_low',  np.nan)

    in_demand  = not np.isnan(dz_h) and (dz_l - atr*0.5) <= price <= (dz_h + atr*0.5)
    in_supply  = not np.isnan(sz_h) and (sz_l - atr*0.5) <= price <= (sz_h + atr*0.5)
    near_demand = not np.isnan(dz_h) and abs(price - dz_h) < atr * 1.2
    near_supply = not np.isnan(sz_l) and abs(price - sz_l) < atr * 1.2

    details = {
        'in_demand': in_demand, 'in_supply': in_supply,
        'near_demand': near_demand, 'near_supply': near_supply,
        'demand_zone': f"{dz_l:.5f}-{dz_h:.5f}" if not np.isnan(dz_h) else "none",
        'supply_zone': f"{sz_l:.5f}-{sz_h:.5f}" if not np.isnan(sz_h) else "none",
    }

    score  = 0
    aligns = False
    if signal == "buy":
        if in_demand:    score += 20; aligns = True
        elif near_demand: score += 10; aligns = True
    elif signal == "sell":
        if in_supply:    score += 20; aligns = True
        elif near_supply: score += 10; aligns = True

    details['score']  = score
    details['aligns'] = aligns
    return aligns, score, details


# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  MASTER CONFLUENCE COUNTER
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def master_confluence_check(df: pd.DataFrame, signal: str, symbol: str,
                             strategy_score: int, state: str) -> Tuple[bool, int, Dict]:
    """
    Runs every analysis module. Counts signal agreements.
    Returns (passes, total_confluence_score, full_breakdown).
    """
    ichi_aligns,   ichi_score,  ichi_d  = analyse_ichimoku(df, signal)
    ob_aligns,     ob_score,    ob_d    = analyse_order_blocks(df, signal)
    div_aligns,    div_score,   div_d   = analyse_rsi_divergence(df, signal)
    sd_aligns,     sd_score,    sd_d    = analyse_supply_demand(df, signal)

    cq_pass, cq_reason, cq_d = candle_quality_gate(df, signal, symbol)

    new_module_agreements = sum([ichi_aligns, ob_aligns, div_aligns, sd_aligns])

    total_score = strategy_score + ichi_score + ob_score + div_score + sd_score

    breakdown = {
        'ichimoku':      {'aligns': ichi_aligns, 'score': ichi_score,  'details': ichi_d},
        'order_blocks':  {'aligns': ob_aligns,   'score': ob_score,    'details': ob_d},
        'rsi_divergence':{'aligns': div_aligns,  'score': div_score,   'details': div_d},
        'supply_demand': {'aligns': sd_aligns,   'score': sd_score,    'details': sd_d},
        'candle_quality':{'passes': cq_pass,     'reason': cq_reason,  'details': cq_d},
        'new_agreements': new_module_agreements,
        'total_score':    total_score,
        'signal':         signal,
        'symbol':         symbol,
        'state':          state,
    }

    passes = cq_pass and (new_module_agreements >= 1 or strategy_score >= 70)

    return passes, total_score, breakdown


def print_full_candle_study(breakdown: Dict, signal: str, symbol: str, entry_price: float):
    is_gold = symbol in GOLD_SYMBOLS
    gold_tag = " 🥇" if is_gold else ""
    arrow = "🟢 BUY" if signal == "buy" else "🔴 SELL"

    print(f"\n{'═'*70}")
    print(f"  📋 FULL CANDLE STUDY — {symbol}{gold_tag} {arrow} @ {entry_price:.5f}")
    print(f"  State: {breakdown.get('state','?')} | Confluence score: {breakdown['total_score']}")
    print(f"{'═'*70}")

    ichi = breakdown['ichimoku']
    id   = ichi['details']
    cloud_pos = "ABOVE ☁️" if id.get('above_cloud') else ("BELOW ☁️" if id.get('below_cloud') else "INSIDE ☁️")
    cloud_dir = "BULL 🐂" if id.get('bull_cloud') else "BEAR 🐻"
    tk_status = "TK↑" if id.get('tk_cross_up') else ("TK↓" if id.get('tk_cross_down') else "TK–")
    print(f"  {'✅' if ichi['aligns'] else '⚪'} ICHIMOKU    +{ichi['score']:>2}  "
          f"Price {cloud_pos} | Cloud {cloud_dir} | {tk_status} | "
          f"T={id.get('tenkan',0):.4f} K={id.get('kijun',0):.4f}")

    ob  = breakdown['order_blocks']
    od  = ob['details']
    ob_status = "IN ZONE" if (od.get('in_buy_ob') or od.get('in_sell_ob')) else ("NEAR" if (od.get('near_buy_ob') or od.get('near_sell_ob')) else "NONE")
    print(f"  {'✅' if ob['aligns'] else '⚪'} ORDER BLOCK +{ob['score']:>2}  "
          f"Status: {ob_status} | "
          f"Buy OB: {od.get('ob_buy_zone','none')} | "
          f"Sell OB: {od.get('ob_sell_zone','none')}")

    div = breakdown['rsi_divergence']
    dd  = div['details']
    div_status = "BULL DIV 🔺" if dd.get('bull_div') else ("BEAR DIV 🔻" if dd.get('bear_div') else "none")
    print(f"  {'✅' if div['aligns'] else '⚪'} RSI DIV     +{div['score']:>2}  "
          f"RSI={dd.get('rsi',0):.1f} | Divergence: {div_status}")

    sd  = breakdown['supply_demand']
    sdd = sd['details']
    sd_status = "IN DEMAND 🟩" if sdd.get('in_demand') else ("IN SUPPLY 🟥" if sdd.get('in_supply') else ("NEAR DEMAND" if sdd.get('near_demand') else ("NEAR SUPPLY" if sdd.get('near_supply') else "none")))
    print(f"  {'✅' if sd['aligns'] else '⚪'} S&D ZONE    +{sd['score']:>2}  "
          f"Status: {sd_status} | "
          f"Demand: {sdd.get('demand_zone','none')} | "
          f"Supply: {sdd.get('supply_zone','none')}")

    cq  = breakdown['candle_quality']
    cqd = cq['details']
    cq_icon = "✅" if cq['passes'] else "❌"
    print(f"  {cq_icon} CANDLE      "
          f"Body {cqd.get('body_ratio',0):.0%} | "
          f"{cqd.get('body_atr_ratio',0):.2f}× ATR | "
          f"Momentum {cqd.get('momentum_ratio',0):.2f}× avg | "
          f"UW {cqd.get('upper_wick_pct',0):.0f}% LW {cqd.get('lower_wick_pct',0):.0f}% | "
          f"{cqd.get('bull_candles_of_3',0)}↑{cqd.get('bear_candles_of_3',0)}↓/3")
    if not cq['passes']:
        print(f"    ⛔ {cq['reason']}")

    agree_count = breakdown['new_agreements']
    print(f"{'─'*70}")
    print(f"  📊 NEW MODULE CONFLUENCE: {agree_count}/4 agree with {signal.upper()}")
    print(f"     Ichimoku {'✅' if breakdown['ichimoku']['aligns'] else '–'}  "
          f"Order Blocks {'✅' if breakdown['order_blocks']['aligns'] else '–'}  "
          f"RSI Div {'✅' if breakdown['rsi_divergence']['aligns'] else '–'}  "
          f"S&D {'✅' if breakdown['supply_demand']['aligns'] else '–'}  "
          f"Candle {'✅' if breakdown['candle_quality']['passes'] else '❌'}")
    print(f"{'═'*70}\n")

# ─────────────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
#  CANDLE QUALITY GATE
# ══════════════════════════════════════════════════════════════════════════════
# ─────────────────────────────────────────────────────────────────────────────
def candle_quality_gate(df: pd.DataFrame, signal: str, symbol: str) -> Tuple[bool, str, Dict]:
    """
    Analyzes the last candle (and recent candles) before allowing a trade.
    Returns: (passes, rejection_reason, details)
    """
    is_gold = symbol in GOLD_SYMBOLS
    details = {}

    if len(df) < 15:
        return True, "OK", {"reason": "insufficient bars — skipping gate"}

    c     = df.iloc[-2]
    atr   = float(df['atr'].iloc[-2])

    o     = float(c['open'])
    h     = float(c['high'])
    l     = float(c['low'])
    cl    = float(c['close'])

    candle_range = h - l
    if candle_range < 1e-8:
        return False, "Zero-range candle — no valid structure", details

    body        = abs(cl - o)
    body_ratio  = body / candle_range
    is_bullish  = cl > o
    is_bearish  = cl < o

    upper_wick  = h - max(cl, o)
    lower_wick  = min(cl, o) - l
    upper_wick_ratio = upper_wick / candle_range
    lower_wick_ratio = lower_wick / candle_range

    body_atr_ratio = body / atr if atr > 0 else 0

    details.update({
        'body_ratio':      round(body_ratio, 3),
        'body_atr_ratio':  round(body_atr_ratio, 3),
        'is_bullish':      is_bullish,
        'upper_wick_pct':  round(upper_wick_ratio * 100, 1),
        'lower_wick_pct':  round(lower_wick_ratio * 100, 1),
        'candle_range':    round(candle_range, 5),
        'body':            round(body, 5),
        'atr':             round(atr, 5),
    })

    recent = df.iloc[-4:-1]   # last 3 completed candles
    bull_count = int((recent['close'] > recent['open']).sum())
    bear_count = int((recent['close'] < recent['open']).sum())
    direction_consistent = (signal == "buy" and bull_count >= 2) or (signal == "sell" and bear_count >= 2)
    details['bull_candles_of_3'] = bull_count
    details['bear_candles_of_3'] = bear_count
    details['direction_consistent'] = direction_consistent

    last10_bodies = (df['close'].iloc[-12:-2] - df['open'].iloc[-12:-2]).abs()
    avg_body_10   = float(last10_bodies.mean()) if len(last10_bodies) > 0 else body
    momentum_ratio = body / avg_body_10 if avg_body_10 > 0 else 1.0
    details['momentum_ratio'] = round(momentum_ratio, 2)
    details['avg_body_10']    = round(avg_body_10, 5)

    if is_gold:
        min_body_ratio = 0.55
        if body_ratio < min_body_ratio:
            return False, (f"Weak candle body ({body_ratio:.0%} < {min_body_ratio:.0%}) "
                          f"— doji/spinning top, no conviction"), details

        min_body_atr = 0.35
        if body_atr_ratio < min_body_atr:
            return False, (f"Candle too small ({body_atr_ratio:.2f}× ATR < {min_body_atr}× ATR) "
                          f"— gold noise, not a real move"), details

        if signal == "buy" and is_bearish:
            return False, (f"Bearish candle (close={cl:.2f} < open={o:.2f}) "
                          f"— wait for bullish candle before buying gold"), details
        if signal == "sell" and is_bullish:
            return False, (f"Bullish candle (close={cl:.2f} > open={o:.2f}) "
                          f"— wait for bearish candle before selling gold"), details

        if signal == "buy":
            max_upper_wick = 0.35
            if upper_wick_ratio > max_upper_wick:
                return False, (f"Large upper wick ({upper_wick_ratio:.0%} > {max_upper_wick:.0%}) "
                              f"— price rejected upward, sellers still active"), details
        else:
            max_lower_wick = 0.35
            if lower_wick_ratio > max_lower_wick:
                return False, (f"Large lower wick ({lower_wick_ratio:.0%} > {max_lower_wick:.0%}) "
                              f"— price rejected downward, buyers still active"), details

        if not direction_consistent:
            count = bull_count if signal == "buy" else bear_count
            return False, (f"Inconsistent momentum ({count}/3 candles in signal direction) "
                          f"— need 2+ candles confirming direction"), details

        min_momentum = 0.70
        if momentum_ratio < min_momentum:
            return False, (f"Low candle momentum ({momentum_ratio:.2f}× avg) "
                          f"— gold move not accelerating"), details

        return True, "OK", details

    else:
        min_body_ratio = 0.40
        if body_ratio < min_body_ratio:
            return False, (f"Weak candle body ({body_ratio:.0%} < {min_body_ratio:.0%}) "
                          f"— indecision candle"), details

        min_body_atr = 0.25
        if body_atr_ratio < min_body_atr:
            return False, (f"Candle too small ({body_atr_ratio:.2f}× ATR < {min_body_atr}× ATR)"), details

        if signal == "buy" and is_bearish:
            return False, f"Bearish candle — wait for bullish candle before buying", details
        if signal == "sell" and is_bullish:
            return False, f"Bullish candle — wait for bearish candle before selling", details

        if not direction_consistent:
            count = bull_count if signal == "buy" else bear_count
            return False, (f"Inconsistent candles ({count}/3 in direction) "
                          f"— mixed momentum"), details

        return True, "OK", details

def evaluate_confluence(df, df_h1, df_h4, df_m15, state, session,
                        symbol: str = "") -> Tuple[str, int, Dict]:
    """
    Returns: (final_signal, total_score, breakdown)
    XAUUSD routes through S4 Gold Engine instead of S1/S2/S3.
    All other symbols use S1+S2+S3 confluence as before.
    """
    is_gold = symbol in GOLD_SYMBOLS
    sess_bonus = {"Overlap": 10, "London": 8, "NewYork": 5}.get(session, 0)

    if is_gold:
        s4_sig, s4_score, s4_d = strategy_gold_engine(df, df_h1, df_h4)
        s2_sig, s2_score, s2_d = strategy_structure_smc(df, state)
        trend_dir = s4_sig if s4_sig != "hold" else "neutral"
        s3_sig, s3_score, s3_d = strategy_momentum(df, trend_dir)
        htf_dir, htf_score = htf_bias(df_m15, df_h1, df_h4)

        gold_confirms = [s for s in [s4_sig, s2_sig, s3_sig] if s != "hold"]
        buy_count  = gold_confirms.count("buy")
        sell_count = gold_confirms.count("sell")

        breakdown = {
            's1': {'signal': 'N/A', 'score': 0, 'details': {}},   # not used for gold
            's2': {'signal': s2_sig, 'score': s2_score, 'details': s2_d},
            's3': {'signal': s3_sig, 'score': s3_score, 'details': s3_d},
            's4': {'signal': s4_sig, 'score': s4_score, 'details': s4_d},
            'htf_dir': htf_dir, 'htf_score': htf_score,
            'buy_count': buy_count, 'sell_count': sell_count,
            'is_gold': True,
        }

        if s4_sig == "buy" and buy_count >= 2:
            final_signal = "buy"
            total = s4_score + (s2_score if s2_sig == "buy" else 0) + (s3_score if s3_sig == "buy" else 0) + htf_score + sess_bonus
        elif s4_sig == "sell" and sell_count >= 2:
            final_signal = "sell"
            total = s4_score + (s2_score if s2_sig == "sell" else 0) + (s3_score if s3_sig == "sell" else 0) + htf_score + sess_bonus
        else:
            final_signal = "hold"
            total = 0

        breakdown['session_bonus'] = sess_bonus
        breakdown['total_score']   = min(MAX_SCORE, total)
        return final_signal, min(MAX_SCORE, total), breakdown

    else:
        s1_sig, s1_score, s1_d = strategy_trend_engine(df, df_h1)
        s2_sig, s2_score, s2_d = strategy_structure_smc(df, state)
        trend_dir = s1_sig if s1_sig != "hold" else "neutral"
        s3_sig, s3_score, s3_d = strategy_momentum(df, trend_dir)
        htf_dir, htf_score = htf_bias(df_m15, df_h1, df_h4)

        signals    = [s for s in [s1_sig, s2_sig, s3_sig] if s != "hold"]
        buy_count  = signals.count("buy")
        sell_count = signals.count("sell")

        breakdown = {
            's1': {'signal': s1_sig, 'score': s1_score, 'details': s1_d},
            's2': {'signal': s2_sig, 'score': s2_score, 'details': s2_d},
            's3': {'signal': s3_sig, 'score': s3_score, 'details': s3_d},
            'htf_dir': htf_dir, 'htf_score': htf_score,
            'buy_count': buy_count, 'sell_count': sell_count,
            'is_gold': False,
        }

        if buy_count >= MIN_STRATEGIES_AGREE:
            final_signal = "buy"
            total = sum([
                s1_score if s1_sig == "buy" else 0,
                s2_score if s2_sig == "buy" else 0,
                s3_score if s3_sig == "buy" else 0,
            ]) + htf_score + sess_bonus
        elif sell_count >= MIN_STRATEGIES_AGREE:
            final_signal = "sell"
            total = sum([
                s1_score if s1_sig == "sell" else 0,
                s2_score if s2_sig == "sell" else 0,
                s3_score if s3_sig == "sell" else 0,
            ]) + htf_score + sess_bonus
        else:
            final_signal = "hold"
            total = 0

        breakdown['session_bonus'] = sess_bonus
        breakdown['total_score']   = min(MAX_SCORE, total)
        return final_signal, min(MAX_SCORE, total), breakdown


def print_confluence(symbol, signal, score, breakdown, state):
    is_gold = breakdown.get('is_gold', False)
    s2 = breakdown['s2']; s3 = breakdown['s3']
    print(f"\n{'─'*65}")
    if signal == "hold":
        agreed = breakdown['buy_count'] + breakdown['sell_count']
        if is_gold:
            s4 = breakdown.get('s4', {})
            print(f"⏸  NO CONFLUENCE: {symbol} 🥇 | "
                  f"S4={s4.get('signal','?').upper()} S2={s2['signal'].upper()} S3={s3['signal'].upper()} "
                  f"({agreed}/2 needed, S4 must lead)")
        else:
            s1 = breakdown['s1']
            print(f"⏸  NO CONFLUENCE: {symbol} | "
                  f"S1={s1['signal'].upper()} S2={s2['signal'].upper()} S3={s3['signal'].upper()} "
                  f"({agreed}/{MIN_STRATEGIES_AGREE} needed)")
    else:
        tag = "✅ APPROVED" if score >= MIN_ENTRY_SCORE else "❌ REJECTED"
        gold_tag = " 🥇" if is_gold else ""
        print(f"{tag}: {symbol}{gold_tag} {signal.upper()} | Score {score}/{MIN_ENTRY_SCORE} | State: {state}")
        if is_gold:
            s4 = breakdown.get('s4', {}); s4d = s4.get('details', {})
            vwap_conflict = (signal == "buy" and not s4d.get('vwap_bullish')) or (signal == "sell" and s4d.get('vwap_bullish'))
            vwap_flag = " ⚠️VWAP" if vwap_conflict else ""
            print(f"   S4 Gold     : {s4.get('signal','?').upper():<5} +{s4.get('score',0):>2}  "
                  f"MACD_cross={'✓' if s4d.get('macd_cross_up') or s4d.get('macd_cross_down') else '✗'} "
                  f"MACD>0={'✓' if s4d.get('macd_above_zero') else '✗'} "
                  f"VWAP={'▲' if s4d.get('vwap_bullish') else '▼'}{vwap_flag} "
                  f"Fib={'✓' if s4d.get('near_fib') else '✗'} "
                  f"H1={s4d.get('h1_dir','?')[:4]} H4={s4d.get('h4_dir','?')[:4]} "
                  f"RSI={s4d.get('rsi',0):.0f} buys={s4d.get('buy_signals',0)} sells={s4d.get('sell_signals',0)}")
        else:
            s1 = breakdown['s1']
            print(f"   S1 Trend    : {s1['signal'].upper():<5} +{s1['score']:>2}  "
                  f"ADX={s1['details'].get('adx',0):.0f} EMA200={'✓' if s1['details'].get('ema200') else '✗'} "
                  f"NearEMA20={'✓' if s1['details'].get('near_ema20') else '✗'}")
        print(f"   S2 Structure: {s2['signal'].upper():<5} +{s2['score']:>2}  "
              f"RSI={s2['details'].get('rsi',0):.0f} Sweep={'✓' if s2['details'].get('sweep_up') or s2['details'].get('sweep_down') else '✗'} "
              f"Squeeze={'✓' if s2['details'].get('bb_squeeze') else '✗'}")
        print(f"   S3 Momentum : {s3['signal'].upper():<5} +{s3['score']:>2}  "
              f"RSI_cross={'✓' if s3['details'].get('rsi_cross_up') or s3['details'].get('rsi_cross_down') else '✗'} "
              f"Stoch_cross={'✓' if s3['details'].get('stoch_cross_up') or s3['details'].get('stoch_cross_down') else '✗'} "
              f"Vol={'✓' if s3['details'].get('vol_surge') else '✗'}")
        print(f"   HTF Bias    : {breakdown['htf_dir'].upper():<8} +{breakdown['htf_score']:>2}  "
              f"Session +{breakdown['session_bonus']:>2}")
        if score < MIN_ENTRY_SCORE:
            print(f"   Missing: {MIN_ENTRY_SCORE - score} pts to reach {MIN_ENTRY_SCORE}")
    print(f"{'─'*65}")


# ─────────────────────────────────────────────────────────────────────────────
# BREAKOUT STATE MACHINE
# ─────────────────────────────────────────────────────────────────────────────
class BreakoutStateMachine:
    def __init__(self):
        self.state_memory: Dict[str, str] = {}

    def pressure_score(self, df, atr_exp, vol_norm) -> int:
        last  = df.iloc[-1]
        rh    = last['range_high_20']; rl = last['range_low_20']
        price = last['close']
        rw    = max(rh - rl, 1e-8)
        pos   = (price - rl) / rw
        prox  = min(pos, 1 - pos) * 2
        s     = int(prox * 35)
        s    += 30 if atr_exp > 1.5 else (20 if atr_exp > 1.2 else (12 if atr_exp > 1.0 else 0))
        mom   = abs(last.get('momentum', 0))
        s    += 20 if mom > 0.8 else (14 if mom > 0.5 else (8 if mom > 0.3 else 0))
        s    += 15 if vol_norm > 1.5 else (10 if vol_norm > 1.2 else (5 if vol_norm > 1.0 else 0))
        return min(100, s)

    def determine_state(self, symbol, df, pres, atr_exp, vol_norm):
        last  = df.iloc[-1]
        price = last['close']
        rh    = last['range_high_20']; rl = last['range_low_20']
        breaking = (price > rh) or (price < rl)
        if breaking and vol_norm > 1.2 and atr_exp > 1.3:  state = "BREAKOUT_ACTIVE"
        elif breaking:                                        state = "BREAKOUT_SETUP"
        elif pres > 55:                                       state = "COMPRESSION"
        else:                                                 state = "RANGE"
        self.state_memory[symbol] = state
        return state, STATE_QUALITY.get(state, 50)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE RECORD
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TradeRecord:
    ticket: int; symbol: str; signal: str
    entry_price: float; sl: float; tp: float
    lot: float; risk_percent: float; confidence: int
    state: str; entry_time: str
    entry_score: int = 0; session: str = ""
    tags: str = ""; edge_strength: str = ""
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    profit: Optional[float] = None
    realized_r: float = 0.0; status: str = "open"
    result: str = "pending"; breakeven_set: bool = False
    partial_closes: List[float] = field(default_factory=list)
    strategies_fired: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class PositionManager:
    def __init__(self, trade_manager):
        self.tm = trade_manager
        self.last_trail: Dict[int, float] = {}

    def manage(self, pos, atr_val: float, pt: float, h1_atr_val: float = None):
        try:
            ticket = pos.ticket; symbol = pos.symbol
            tick   = mt5.symbol_info_tick(symbol)
            if not tick: return
            cur     = tick.bid if pos.type == 0 else tick.ask
            entry   = pos.price_open; sl = pos.sl
            risk_d  = abs(entry - sl)
            profit_d = (cur - entry) if pos.type == 0 else (entry - cur)
            r_mult  = profit_d / risk_d if risk_d > 0 else 0.0

            trade = next((t for t in self.tm.trades.values() if t.ticket == ticket), None)
            if not trade: return

            try:
                entry_dt   = datetime.fromisoformat(trade.entry_time)
                hours_open = (datetime.now() - entry_dt).total_seconds() / 3600
                if hours_open > TIME_EXIT_HOURS and r_mult < 1.0:
                    DP.warn(f"{symbol}: Time exit ({hours_open:.1f}h, R={r_mult:.2f})")
                    self._close_position(pos, tick, "TIME_EXIT")
                    return
            except Exception as te:
                log.warning(f"Time exit check: {te}")

            if r_mult >= BREAKEVEN_CONFIG["activation_r"] and not trade.breakeven_set:
                buf    = BREAKEVEN_CONFIG["add_pips"] * pt
                new_sl = (entry + buf) if pos.type == 0 else (entry - buf)
                res    = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl})
                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                    trade.breakeven_set = True
                    print(f"   🔒 {symbol}: Breakeven locked at +{r_mult:.2f}R")
                    log.info(f"{symbol} breakeven set +{r_mult:.2f}R")

            for tgt in PROFIT_TARGETS:
                if r_mult >= tgt["r_multiple"] and tgt["r_multiple"] not in trade.partial_closes:
                    close_vol = round(pos.volume * tgt["close_percent"], 2)
                    if close_vol >= 0.01:
                        price = tick.bid if pos.type == 0 else tick.ask
                        otype = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
                        req   = {
                            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol,
                            "volume": close_vol, "type": otype, "position": ticket,
                            "price": price, "deviation": 20, "magic": 999999,
                            "comment": f"TP_{tgt['r_multiple']}R",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_RETURN,
                        }
                        res = mt5.order_send(req)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            trade.partial_closes.append(tgt["r_multiple"])
                            pct = int(tgt["close_percent"] * 100)
                            print(f"   💰 {symbol}: Partial close {pct}% at +{tgt['r_multiple']}R")
                            log.info(f"{symbol} partial {pct}% at +{tgt['r_multiple']}R")

            if r_mult >= TRAILING_CONFIG["activation_r"]:
                trail_atr  = h1_atr_val if h1_atr_val else atr_val
                trail_d    = TRAILING_CONFIG["distance_atr"] * trail_atr
                if pos.type == 0:
                    new_sl = cur - trail_d
                    if new_sl > sl and new_sl > self.last_trail.get(ticket, -999):
                        mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl})
                        self.last_trail[ticket] = new_sl
                        print(f"   📈 {symbol}: Trail → {new_sl:.5f} (+{r_mult:.2f}R)")
                else:
                    new_sl = cur + trail_d
                    if new_sl < sl and new_sl < self.last_trail.get(ticket, 999):
                        mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "sl": new_sl})
                        self.last_trail[ticket] = new_sl
                        print(f"   📉 {symbol}: Trail → {new_sl:.5f} (+{r_mult:.2f}R)")

        except Exception as e:
            log.error(f"PositionManager.manage({pos.symbol}): {e}")

    def _close_position(self, pos, tick, comment):
        try:
            price = tick.bid if pos.type == 0 else tick.ask
            otype = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
            mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
                "volume": pos.volume, "type": otype, "position": pos.ticket,
                "price": price, "deviation": 20, "magic": 999999, "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            })
            log.info(f"Closed {pos.symbol} #{pos.ticket} — {comment}")
        except Exception as e:
            log.error(f"_close_position: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class TradeManager:
    def __init__(self):
        self.trades: Dict[int, TradeRecord] = {}
        self.history: List[TradeRecord] = []
        self.daily_count = 0; self.daily_risk_used = 0.0
        self.daily_pnl   = 0.0; self.consec_losses = 0
        self.last_reset_day = datetime.now().date()
        self.state_last_time: Dict[str, float] = {}
        self.symbol_stats: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "total_r": 0.0})

    def _reset_daily(self):
        today = datetime.now().date()
        if today != self.last_reset_day:
            self.daily_count = self.daily_risk_used = self.daily_pnl = 0
            self.last_reset_day = today
            DP.header("DAILY RESET")

    def can_trade_daily(self) -> Tuple[bool, str]:
        self._reset_daily()
        if self.daily_count >= MAX_DAILY_TRADES:    return False, f"Daily trade limit ({self.daily_count})"
        if self.daily_risk_used >= MAX_DAILY_RISK:  return False, f"Daily risk limit ({self.daily_risk_used:.1f}%)"
        if len(self.trades) >= MAX_OPEN_TRADES:     return False, f"Max open trades ({MAX_OPEN_TRADES})"
        return True, "OK"

    def symbol_has_open_trade(self, symbol): 
        return any(t.symbol == symbol and t.status == "open" for t in self.trades.values())

    def can_trade_state(self, state, symbol):
        key = f"{state}_{symbol}"
        elapsed = time.time() - self.state_last_time.get(key, 0)
        return (elapsed >= 120, max(0, int(120 - elapsed)))

    def register(self, trade: TradeRecord, state: str) -> bool:
        ok, msg = self.can_trade_daily()
        if not ok: DP.err(msg); return False
        ok2, rem = self.can_trade_state(state, trade.symbol)
        if not ok2: DP.warn(f"State cooldown {rem}s"); return False
        self.trades[trade.ticket] = trade
        self.state_last_time[f"{state}_{trade.symbol}"] = time.time()
        self.daily_count     += 1
        self.daily_risk_used += trade.risk_percent
        log.info(f"Trade registered: {trade.symbol} {trade.signal} #{trade.ticket}")
        return True

    def sync_mt5(self):
        try:
            open_tickets = {p.ticket for p in (mt5.positions_get() or [])}
            for ticket in list(self.trades.keys()):
                if ticket not in open_tickets:
                    t = self.trades.pop(ticket)
                    t.status = "closed"; self.history.append(t)
        except Exception as e: log.error(f"sync_mt5: {e}")

    def get_sizing_multiplier(self):
        return SIZING_STEP_DOWN.get(min(self.consec_losses, 3), 0.5)

    def show_stats(self):
        if not self.symbol_stats: return
        DP.sub("SYMBOL STATS")
        for k, s in sorted(self.symbol_stats.items()):
            total = s["wins"] + s["losses"]
            if total < 2: continue
            print(f"   {k:<20}: {total} trades | WR {s['wins']/total*100:.0f}% | ΣR {s['total_r']:+.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────────────────
class TradeDB:
    def __init__(self, path=DB_PATH):
        self.path = path; self._init()

    def _init(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER UNIQUE, symbol TEXT, signal TEXT,
                state TEXT, session TEXT, entry_price REAL, exit_price REAL,
                sl REAL, tp REAL, lot REAL, risk_percent REAL,
                confidence INTEGER, entry_score INTEGER,
                spread_at_entry REAL, profit REAL, realized_r REAL,
                holding_minutes REAL, win BOOLEAN,
                tags TEXT, edge_strength TEXT, strategies_fired TEXT,
                entry_time TEXT, exit_time TEXT
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS equity_curve (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, balance REAL, equity REAL, drawdown_percent REAL
            )''')
            conn.commit()
        DP.ok("Database initialized")

    def save_trade(self, d: Dict) -> int:
        try:
            with sqlite3.connect(self.path) as conn:
                cur = conn.execute('''
                    INSERT OR IGNORE INTO trades
                    (ticket,symbol,signal,state,session,entry_price,exit_price,sl,tp,
                     lot,risk_percent,confidence,entry_score,spread_at_entry,
                     profit,realized_r,holding_minutes,win,tags,edge_strength,
                     strategies_fired,entry_time,exit_time)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (d.get('ticket'), d.get('symbol'), d.get('signal'), d.get('state'),
                      d.get('session'), d.get('entry_price'), d.get('exit_price'),
                      d.get('sl'), d.get('tp'), d.get('lot'), d.get('risk_percent'),
                      d.get('confidence'), d.get('entry_score'), d.get('spread_at_entry'),
                      d.get('profit'), d.get('realized_r'), d.get('holding_minutes'),
                      1 if d.get('win') else 0, d.get('tags'), d.get('edge_strength'),
                      d.get('strategies_fired'), d.get('entry_time'), d.get('exit_time')))
                conn.commit(); return cur.lastrowid
        except Exception as e: log.error(f"DB save: {e}"); return 0

    def log_equity(self, balance, equity, dd):
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute("INSERT INTO equity_curve (timestamp,balance,equity,drawdown_percent) VALUES (?,?,?,?)",
                             (datetime.now().isoformat(), balance, equity, dd))
                conn.commit()
        except Exception as e: log.error(f"DB equity: {e}")

    def stats(self) -> Dict:
        try:
            with sqlite3.connect(self.path) as conn:
                total  = conn.execute("SELECT COUNT(*) FROM trades WHERE realized_r IS NOT NULL").fetchone()[0]
                wr_row = conn.execute("SELECT AVG(CASE WHEN win=1 THEN 1.0 ELSE 0.0 END) FROM trades").fetchone()
                ex_row = conn.execute("SELECT AVG(realized_r) FROM trades WHERE realized_r IS NOT NULL").fetchone()
                pf_row = conn.execute("""SELECT ABS(SUM(CASE WHEN profit>0 THEN profit ELSE 0 END)/
                    NULLIF(SUM(CASE WHEN profit<0 THEN profit ELSE 0 END),0)) FROM trades WHERE profit IS NOT NULL""").fetchone()
                tr_row = conn.execute("SELECT SUM(realized_r) FROM trades WHERE realized_r IS NOT NULL").fetchone()
                dd_row = conn.execute("SELECT MIN(drawdown_percent) FROM equity_curve").fetchone()
            return {"total": total, "win_rate": wr_row[0] or 0.0,
                    "expectancy": ex_row[0] or 0.0, "profit_factor": pf_row[0] or 0.0,
                    "total_r": tr_row[0] or 0.0, "max_dd": abs(dd_row[0] or 0.0)}
        except: return {"total":0,"win_rate":0,"expectancy":0,"profit_factor":0,"total_r":0,"max_dd":0}


# ─────────────────────────────────────────────────────────────────────────────
# EQUITY PROTECTION
# ─────────────────────────────────────────────────────────────────────────────
class EquityProtection:
    def __init__(self, initial, db):
        self.peak = initial; self.db = db
        self.state = "NORMAL"; self.consec = 0
        self.reset_day = datetime.now().date()

    def update(self, balance, result=None):
        self.peak = max(self.peak, balance)
        dd = (self.peak - balance) / self.peak if self.peak > 0 else 0
        self.db.log_equity(balance, balance, dd * 100)
        if result == "loss": self.consec += 1
        elif result == "win": self.consec = 0
        today = datetime.now().date()
        if today != self.reset_day: self.reset_day = today; self.consec = 0
        if dd > 0.07:   self.state = "HALT"
        elif dd > 0.05: self.state = "DRAWDOWN"
        elif dd > 0.025: self.state = "CAUTION"
        else:            self.state = "NORMAL"
        return dd

    def risk_mult(self):
        return {"HALT": 0.0, "DRAWDOWN": 0.5, "CAUTION": 0.75, "NORMAL": 1.0}[self.state]

    def should_halt(self):
        if self.state == "HALT": return True, "Equity HALT"
        if self.consec >= MAX_CONSECUTIVE_LOSSES: return True, f"{self.consec} consecutive losses"
        return False, ""


# ─────────────────────────────────────────────────────────────────────────────
# FILTERS
# ─────────────────────────────────────────────────────────────────────────────
class CorrelationFilter:
    def __init__(self): self.matrix = pd.DataFrame(); self.last_update = 0

    def refresh(self):
        returns = []
        for sym in SYMBOLS:
            r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 100)
            returns.append(pd.DataFrame(r)['close'].pct_change().values[-100:] if r is not None else np.zeros(100))
        self.matrix = pd.DataFrame(np.corrcoef(np.array(returns)), index=SYMBOLS, columns=SYMBOLS)
        self.last_update = time.time()

    def can_trade(self, symbol, tm):
        if self.matrix.empty or (time.time() - self.last_update) > 300:
            try: self.refresh()
            except: return True, "OK"
        if symbol in self.matrix.columns:
            for other, corr in self.matrix[symbol].items():
                if other != symbol and abs(corr) > CORRELATION_THRESHOLD and tm.symbol_has_open_trade(other):
                    return False, f"Correlated with {other} ({corr:.2f})"
        return True, "OK"


class ClusterFilter:
    def __init__(self): self.last_time = {}; self.last_price = {}

    def can_trade(self, symbol, price, pt):
        now = time.time()
        if symbol in self.last_time and (now - self.last_time[symbol]) < MIN_TIME_BETWEEN_TRADES_SECONDS:
            return False, f"Time cluster ({now-self.last_time[symbol]:.0f}s)"
        if symbol in self.last_price and pt > 0:
            dist = abs(price - self.last_price[symbol]) / pt
            if dist < MIN_PRICE_DISTANCE_PIPS:
                return False, f"Price cluster ({dist:.1f}p)"
        return True, "OK"

    def record(self, symbol, price):
        self.last_time[symbol] = time.time(); self.last_price[symbol] = price


class CurrencyExposureFilter:
    def can_trade(self, symbol, signal, tm):
        exp = defaultdict(float)
        for t in tm.trades.values():
            if t.status == "open":
                mult = 1 if t.signal == "buy" else -1
                for c, v in CURRENCY_MAP.get(t.symbol, {}).items(): exp[c] += v * mult
        mult = 1 if signal == "buy" else -1
        for c, v in CURRENCY_MAP.get(symbol, {}).items():
            if abs(exp[c] + v * mult) > MAX_CURRENCY_EXPOSURE:
                return False, f"Currency cap {c}"
        return True, "OK"


# ─────────────────────────────────────────────────────────────────────────────
# VOLUME NORMALIZER
# ─────────────────────────────────────────────────────────────────────────────
class VolumeNormalizer:
    def __init__(self): self.data = defaultdict(lambda: defaultdict(list))

    def update(self, symbol, hour, vol):
        buf = self.data[symbol][hour]; buf.append(vol)
        if len(buf) > 7*24: self.data[symbol][hour] = buf[-7*24:]

    def normalize(self, symbol, hour, vol):
        hist = self.data[symbol].get(hour, [])
        if len(hist) < 10: return min(1.5, vol / (vol + 100))
        return float(np.clip(vol / max(np.mean(hist[-50:]), 1e-6), 0.3, 2.5))


# ─────────────────────────────────────────────────────────────────────────────
# KELLY + POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────
def kelly_size(win_rate, avg_win_r, avg_loss_r=1.0):
    if avg_loss_r <= 0 or win_rate <= 0: return 0.005
    b = avg_win_r / avg_loss_r; q = 1 - win_rate
    kelly = (b * win_rate - q) / b
    return max(0.003, min(KELLY_CAP, kelly * 0.25))

def calc_lot(balance, risk_pct, sl_pips, symbol):
    risk_amt = balance * (risk_pct / 100)
    lot = risk_amt / max(sl_pips * pip_value(symbol), 1e-6)
    return round(max(0.01, min(0.05, lot)) / 0.01) * 0.01


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    DP.header(f"BABSBOOKS V46.0 — MECHANICAL MIND EDITION")

    if not mt5.initialize(): DP.err("MT5 init failed"); return
    acc = mt5.account_info()
    if not acc: DP.err("No account info"); return
    DP.ok(f"Connected | Balance: ${acc.balance:.2f} | Equity: ${acc.equity:.2f}")

    db       = TradeDB()
    eq_prot  = EquityProtection(acc.balance, db)
    cluster  = ClusterFilter()
    corr     = CorrelationFilter()
    curr_exp = CurrencyExposureFilter()
    vol_norm = VolumeNormalizer()
    tm       = TradeManager()
    sm       = BreakoutStateMachine()
    pm       = PositionManager(tm)

    for sym in SYMBOLS: mt5.symbol_select(sym, True)

    s = db.stats()
    DP.sub("HISTORICAL STATS")
    if s["total"] > 0:
        print(f"   Trades: {s['total']} | WR: {s['win_rate']*100:.1f}% | "
              f"Exp: {s['expectancy']:+.2f}R | PF: {s['profit_factor']:.2f} | MaxDD: {s['max_dd']:.1f}%")
    else:
        DP.info("No historical data yet — starting fresh")

    kelly_risk_pct = MAX_RISK_PER_TRADE
    cycle = 0

    try:
        while True:
            cycle += 1
            acc = mt5.account_info()
            if not acc: time.sleep(5); continue

            halt, halt_reason = eq_prot.should_halt()
            if halt: DP.warn(f"HALTED: {halt_reason}"); time.sleep(300); continue
            rm = eq_prot.risk_mult()
            if rm == 0: time.sleep(300); continue

            floating = acc.equity - acc.balance
            if floating < 0 and abs(floating) / acc.balance * 100 > FLOATING_LOSS_CAP_PCT:
                DP.warn(f"Floating cap hit (${floating:.2f})"); time.sleep(60); continue

            tm.sync_mt5()
            poll_closed_positions()  # report any MRM-tracked positions that closed since last cycle

            positions = mt5.positions_get() or []
            for pos in positions:
                df_p = safe_rates(pos.symbol, TIMEFRAME, 150)
                if df_p is not None:
                    df_p = add_indicators(df_p)
                    df_h1_p = safe_rates(pos.symbol, mt5.TIMEFRAME_H1, 100)
                    h1_atr = float(add_indicators(df_h1_p)['atr'].iloc[-1]) if df_h1_p is not None else None
                    pm.manage(pos, df_p['atr'].iloc[-1], symbol_point(pos.symbol), h1_atr)

            utc_h = datetime.utcnow().hour
            session, sess_boost = get_session(utc_h)
            if session == "Asian":
                print(f"🕐 {datetime.now().strftime('%H:%M')} — Asian session, waiting...")
                time.sleep(60); continue

            DP.header(f"CYCLE #{cycle} | {datetime.now().strftime('%H:%M:%S')} | {session} ×{sess_boost}")

            df_ref = safe_rates("EURUSD", TIMEFRAME, BARS)
            if df_ref is None: time.sleep(30); continue
            df_ref  = add_indicators(df_ref)
            atr_pct = df_ref['atr_percent'].iloc[-1]
            if atr_pct < MIN_ATR_PERCENT_ABS:
                DP.warn(f"Low ATR {atr_pct:.4f}% — market too quiet"); time.sleep(30); continue

            s = db.stats()
            if s["total"] >= 10 and s["win_rate"] > 0:
                kelly_risk_pct = max(0.3, min(MAX_RISK_PER_TRADE,
                    kelly_size(s["win_rate"], max(s["expectancy"], 0.1)) * 100))

            if SHOW_PERFORMANCE_METRICS:
                DP.sub("CONDITIONS")
                print(f"   ATR: {atr_pct:.4f}% | Session: {session} | Risk Mult: {rm:.2f}x")
                print(f"   Kelly: {kelly_risk_pct:.2f}% | Open: {len(tm.trades)}/{MAX_OPEN_TRADES} | Daily: {tm.daily_count}/{MAX_DAILY_TRADES}")

            approved_count = 0

            for symbol in SYMBOLS:
                try:
                    if tm.symbol_has_open_trade(symbol): continue

                    df = safe_rates(symbol, TIMEFRAME, BARS)
                    if df is None: continue
                    df = add_indicators(df)
                    last = df.iloc[-1]

                    vc = 'tick_volume' if 'tick_volume' in df.columns else 'volume_ratio'
                    raw_vol = float(df[vc].iloc[-1]) if vc in df.columns else 1000.0
                    vol_norm.update(symbol, utc_h, raw_vol)
                    norm_vol = vol_norm.normalize(symbol, utc_h, raw_vol)
                    atr_exp  = float(last['atr_expansion'])
                    pres     = sm.pressure_score(df, atr_exp, norm_vol)
                    mq       = float(last['volume_ratio']) * (pres / 100)

                    if pres < MIN_PRESSURE_SCORE:
                        if SHOW_REJECTION_REASONS: DP.info(f"{symbol} skipped: pressure {pres} < {MIN_PRESSURE_SCORE}")
                        continue
                    if mq < MIN_MARKET_QUALITY:
                        if SHOW_REJECTION_REASONS: DP.info(f"{symbol} skipped: market quality {mq:.3f} < {MIN_MARKET_QUALITY}")
                        continue

                    df_m15, df_h1, df_h4 = get_htf_data(symbol)

                    state, _ = sm.determine_state(symbol, df, pres, atr_exp, norm_vol)

                    if symbol in GOLD_SYMBOLS and session not in GOLD_SESSIONS:
                        if SHOW_REJECTION_REASONS:
                            DP.info(f"{symbol} 🥇 skipped: gold only trades London/Overlap/NY")
                        continue

                    si = mt5.symbol_info(symbol)
                    if not si: continue
                    if symbol in GOLD_SYMBOLS:
                        spread_pips = float(si.spread)  # gold spread in points (e.g. 30-80)
                    else:
                        spread_pips = si.spread / 10.0  # forex spread in pips
                    if spread_pips > SPREAD_LIMITS.get(symbol, 2.5):
                        if SHOW_REJECTION_REASONS:
                            DP.info(f"{symbol} skipped: spread {spread_pips:.1f} (limit {SPREAD_LIMITS.get(symbol,2.5)})")
                        continue

                    signal, score, breakdown = evaluate_confluence(
                        df, df_h1, df_h4, df_m15, state, session, symbol=symbol)

                    breakdown['session'] = session
                    print_confluence(symbol, signal, score, breakdown, state)

                    if signal == "hold": continue
                    if score < MIN_ENTRY_SCORE: continue

                    tick_pre = mt5.symbol_info_tick(symbol)
                    entry_pre = (tick_pre.ask if signal == "buy" else tick_pre.bid) if tick_pre else 0.0

                    mc_passes, mc_score, mc_breakdown = master_confluence_check(
                        df, signal, symbol, score, state)

                    print_full_candle_study(mc_breakdown, signal, symbol, entry_pre)

                    if not mc_passes:
                        log.info(f"Master confluence blocked {symbol} {signal}: "
                                 f"candle={mc_breakdown['candle_quality']['passes']} "
                                 f"modules={mc_breakdown['new_agreements']}/4")
                        continue

                    can_daily, d_msg = tm.can_trade_daily()
                    if not can_daily: DP.warn(d_msg); continue

                    tick = mt5.symbol_info_tick(symbol)
                    if not tick: continue
                    entry = tick.ask if signal == "buy" else tick.bid
                    pt    = symbol_point(symbol)

                    ok_cl, cl_msg = cluster.can_trade(symbol, entry, pt)
                    if not ok_cl: DP.info(f"{symbol} cluster: {cl_msg}"); continue

                    ok_co, co_msg = corr.can_trade(symbol, tm)
                    if not ok_co: DP.info(f"{symbol} correlation: {co_msg}"); continue

                    ok_ce, ce_msg = curr_exp.can_trade(symbol, signal, tm)
                    if not ok_ce: DP.info(f"{symbol} exposure: {ce_msg}"); continue

                    ps    = pip_size(symbol)
                    atr_v = float(df['atr'].iloc[-1])

                    if symbol in GOLD_SYMBOLS:
                        gold_pt       = symbol_point(symbol)   # 0.01 for XAUUSD
                        h1_atr_price  = atr_v * 3.0            # fallback: scale M5 ATR
                        if df_h1 is not None and len(df_h1) > 20:
                            h1_atr_price = float(df_h1['atr'].iloc[-1])
                        sl_mult       = {"BREAKOUT_ACTIVE":1.8,"EXPANSION":1.8,
                                         "BREAKOUT_SETUP":1.5,"COMPRESSION":1.3}.get(state, 1.2)
                        spread_buf_price = spread_pips * gold_pt * 1.5
                        sl_price      = max(8.0, min(25.0, h1_atr_price * sl_mult + spread_buf_price))
                        tp_rr         = TARGET_RISK_REWARD.get(state, TARGET_RISK_REWARD["RANGE"])["target_rr"]
                        tp_price      = sl_price * tp_rr
                        sl_pips       = int(sl_price / gold_pt)    # convert to points for calc_lot
                        tp_pips       = int(tp_price / gold_pt)
                        sl_d          = sl_price
                        tp_d          = tp_price
                        log.info(f"XAUUSD SL calc: H1_ATR=${h1_atr_price:.2f} sl=${sl_price:.2f} tp=${tp_price:.2f}")
                    else:
                        atr_p         = atr_v / ps
                        h1_atr_pips   = atr_p * 3.0
                        if df_h1 is not None and len(df_h1) > 20:
                            h1_atr_pips = float(df_h1['atr'].iloc[-1]) / ps
                        sl_mult       = {"BREAKOUT_ACTIVE":1.5,"EXPANSION":1.5,
                                         "BREAKOUT_SETUP":1.3,"COMPRESSION":1.2}.get(state, 1.0)
                        jpy_mult      = 1.5 if "JPY" in symbol else 1.0
                        spread_buf    = spread_pips * 1.5
                        raw_sl        = int(h1_atr_pips * sl_mult * jpy_mult) + int(spread_buf)
                        sl_floor      = 30 if "JPY" in symbol else 20
                        sl_ceil       = 120 if "JPY" in symbol else 80
                        sl_pips       = max(sl_floor, min(sl_ceil, raw_sl))
                        rr_cfg        = TARGET_RISK_REWARD.get(state, TARGET_RISK_REWARD["RANGE"])
                        tgt_rr        = rr_cfg["target_rr"] * VOLATILITY_RR_MULTIPLIERS.get(volatility_regime(atr_pct), 1.0)
                        tp_pips       = int(sl_pips * tgt_rr)
                        sl_d          = sl_pips * ps
                        tp_d          = tp_pips * ps

                    if signal == "buy":
                        sl_raw = entry - sl_d; tp_raw = entry + tp_d
                    else:
                        sl_raw = entry + sl_d; tp_raw = entry - tp_d

                    _, final_sl, final_tp = validate_stops(symbol, entry, sl_raw, tp_raw)

                    base_risk  = min(MAX_RISK_PER_TRADE, kelly_risk_pct * sess_boost)
                    score_mult = 1.2 if score >= 75 else (1.0 if score >= 55 else 0.8)
                    step_mult  = tm.get_sizing_multiplier()
                    final_risk = min(MAX_RISK_PER_TRADE, base_risk * rm * score_mult * step_mult)
                    lot        = calc_lot(acc.balance, final_risk, sl_pips, symbol)
                    edge       = "strong" if score >= 75 else ("moderate" if score >= 55 else "developing")

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
                        strategy_tag=f"v46_gold_{state.lower()}",
                    )
                    if not mrm_decision.approved:
                        DP.warn(f"MRM rejected {symbol} {signal}: {mrm_decision.reason}")
                        continue
                    if mrm_decision.approved_risk_pct != final_risk:
                        # MRM resized us (e.g. netted against another bot's
                        # opposing position) -- recompute lot at the approved size.
                        final_risk = mrm_decision.approved_risk_pct
                        lot = calc_lot(acc.balance, final_risk, sl_pips, symbol)

                    strats_fired = "+".join([
                        f"S1({breakdown['s1']['signal']})" if breakdown['s1']['signal'] != "hold" else "",
                        f"S2({breakdown['s2']['signal']})" if breakdown['s2']['signal'] != "hold" else "",
                        f"S3({breakdown['s3']['signal']})" if breakdown['s3']['signal'] != "hold" else "",
                    ]).strip("+").replace("++", "+")

                    print(f"\n{'═'*65}")
                    DP.ok(f"EXECUTING: {symbol} {signal.upper()}")
                    print(f"   Score: {score} | State: {state} | Edge: {edge}")
                    print(f"   Strategies: {strats_fired}")
                    print(f"   RR: 1:{tgt_rr:.1f} | SL: {sl_pips}p | TP: {tp_pips}p")
                    print(f"   H1 ATR: {h1_atr_pips:.1f}p | Risk: {final_risk:.2f}% | Lot: {lot}")
                    print(f"   {risk_bar(final_risk)}")
                    print(f"{'═'*65}")

                    req = {
                        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lot,
                        "type": mt5.ORDER_TYPE_BUY if signal == "buy" else mt5.ORDER_TYPE_SELL,
                        "price": entry, "sl": final_sl, "tp": final_tp,
                        "deviation": 20, "magic": 999999,
                        "comment": f"V46_{state[:3]}_{score}",
                        "type_time": mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_FOK,
                    }
                    res = mt5.order_send(req)
                    if not (res and res.retcode == mt5.TRADE_RETCODE_DONE):
                        # Broker rejected/failed the order after MRM approved it
                        # -- release the reserved exposure so it doesn't sit
                        # phantom-locked against the account risk ceiling.
                        risk.reject_fill(mrm_decision.position_id)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        risk.confirm_fill(mrm_decision.position_id)
                        track_reservation(res.order, mrm_decision.position_id)
                        tr = TradeRecord(
                            ticket=res.order, symbol=symbol, signal=signal,
                            entry_price=entry, sl=final_sl, tp=final_tp,
                            lot=lot, risk_percent=final_risk, confidence=score,
                            entry_score=score, state=state,
                            entry_time=datetime.now().isoformat(),
                            session=session, edge_strength=edge,
                            tags=f"{state.lower()},{session.lower()},{edge}",
                            strategies_fired=strats_fired,
                        )
                        if tm.register(tr, state):
                            DP.ok(f"✈️  Executed! Ticket #{res.order}")
                            cluster.record(symbol, entry)
                            db.save_trade({
                                "ticket": res.order, "symbol": symbol, "signal": signal,
                                "state": state, "session": session,
                                "entry_price": entry, "sl": final_sl, "tp": final_tp,
                                "lot": lot, "risk_percent": final_risk,
                                "confidence": score, "entry_score": score,
                                "spread_at_entry": spread_pips, "tags": tr.tags,
                                "edge_strength": edge, "strategies_fired": strats_fired,
                                "entry_time": tr.entry_time,
                            })
                            eq_prot.update(acc.balance)
                            approved_count += 1
                    else:
                        err = res.comment if res else "No result"
                        DP.err(f"Order failed: {err} (retcode={res.retcode if res else '?'})")

                except Exception as sym_err:
                    log.error(f"Symbol loop {symbol}: {sym_err}"); traceback.print_exc(); continue

            if approved_count == 0 and not SHOW_REJECTION_REASONS:
                DP.info("No trades executed this cycle")

            if cycle % 5 == 0: eq_prot.update(acc.balance)
            if cycle % 20 == 0 and SHOW_PERFORMANCE_METRICS:
                s = db.stats()
                DP.header("PERFORMANCE SUMMARY")
                print(f"   Balance: ${acc.balance:.2f} | Equity: ${acc.equity:.2f} | Floating: ${acc.equity-acc.balance:+.2f}")
                print(f"   Today: {tm.daily_count}/{MAX_DAILY_TRADES} | Open: {len(tm.trades)}/{MAX_OPEN_TRADES}")
                print(f"   Eq state: {eq_prot.state} | Kelly: {kelly_risk_pct:.2f}%")
                if s["total"] > 0:
                    print(f"   Trades: {s['total']} | WR: {s['win_rate']*100:.1f}% | "
                          f"Exp: {s['expectancy']:+.2f}R | PF: {s['profit_factor']:.2f}")
                tm.show_stats()
                print(SEPARATOR)

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user")
        s = db.stats()
        DP.header("FINAL REPORT")
        print(f"   Trades: {s['total']} | WR: {s['win_rate']*100:.1f}% | "
              f"Exp: {s['expectancy']:+.2f}R | PF: {s['profit_factor']:.2f} | MaxDD: {s['max_dd']:.1f}%")
        tm.show_stats()
    except Exception as fatal:
        DP.err(f"Fatal: {fatal}"); log.critical(f"Fatal: {fatal}"); traceback.print_exc()
    finally:
        mt5.shutdown(); DP.info("MT5 disconnected")


if __name__ == "__main__":
    main()
