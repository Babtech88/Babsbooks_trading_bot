# BabsBooks Master Risk Manager (MRM)

Central risk authority for running V36 / V45 / V46 / simple_trade_bot as a fleet
against one shared MT5 account. Each bot keeps its own signal-generation logic
completely untouched — the MRM only owns the sizing/approve/reject decision
and the account-wide risk bookkeeping.

## Files

- `master_risk_manager.py` — the FastAPI service. Run this once, alongside your bots.
- `risk_client.py` — the HTTP client each bot imports. `pip install requests`.
- `bot_risk_config.py` — shared per-instance config: symbol policy (free vs XAUUSD-only) and a generic close-detection poller, driven entirely by `BOT_ID`/`SYMBOL_MODE` env vars.
- `example_bot_integration.py` — minimal before/after illustration.
- `simple_trade_bot_v46_gold.py` — your V46.1 mechanical-mind-gold bot (S1/S2/S3 forex confluence + dedicated S4 Gold Engine for XAUUSD), **already wired** to the MRM.
- `professional_trading_bot_v45.py` — your V45 bot, **already wired** to the MRM.
- `babsbooks_trading_bot_v36.py` — your V36.0 Complete Edition **with HQT multi-entry DCA**, **already wired** to the MRM. Note: has no XAUUSD/gold logic of its own, so don't assign this one as the `SYMBOL_MODE=xauusd_only` instance.
- `babsbooks_trading_bot_v36_no_hqt.py` — the same V36.0 Complete Edition **without** the HQT DCA system (single-entry only), **already wired**. Uses separate DB/position/cluster files (`*_v36_nohqt.*`) so it can run alongside the HQT version from the same directory without file collisions. Same gold-logic caveat as above.
- `v46_confluence_bot.py` — your V46 Confluence Edition (Ichimoku Cloud, Order Block detection, RSI Divergence, Supply & Demand zones, 8-signal confluence counter with full candle-study printout), **already wired** to the MRM. Same note as V36: no gold-specific logic, don't use as the XAUUSD-only instance.
- `babsbooks_trading_bot_v36_itafx.py` — your newest V36 variant (ITAFX prop-firm compliance system with account-mode switching, 40+ indicator suite, 35-signal candle intelligence engine with 20 candle patterns, pre-entry candle study printout), **already wired** to the MRM. Two real bugs fixed during wiring — see notes below. Same gold caveat as the others.

### Symbol policy (as requested)

Every bot instance trades its own normal symbol list freely, **except one
dedicated instance restricted to XAUUSD only**. This is controlled purely by
environment variables at launch — no code differs between instances:

```bash
# the MRM (run once)
uvicorn master_risk_manager:app --host 127.0.0.1 --port 8800

# free-trading instances (however many you want, any wired bot file)
BOT_ID=bot_1_v46_a python simple_trade_bot_v46_gold.py
BOT_ID=bot_2_v46_b python simple_trade_bot_v46_gold.py
BOT_ID=bot_3_v45_a python professional_trading_bot_v45.py
BOT_ID=bot_4_v45_b python professional_trading_bot_v45.py

# the one dedicated XAUUSD-only instance
BOT_ID=bot_7_xauusd SYMBOL_MODE=xauusd_only python simple_trade_bot_v46_gold.py
```

Each `BOT_ID` must be unique — that's how the MRM's `/v1/status` and audit
log tell instances apart.

### What changed inside `simple_trade_bot_v46_gold.py` and `professional_trading_bot_v45.py`

Only three things, in each file — all their actual signal-generation logic
(state machine, 7-gate hierarchy, SMC, confluence scoring, etc.) is
untouched:

1. `SYMBOLS = [...]` wrapped in `get_symbols([...])` to apply the fleet symbol policy.
2. The real `order_send()` entry call gated with `risk.request_trade(...)` — rejects skip the signal; approvals may come back resized (lot recalculated at the approved risk %).
3. The main loop now calls `poll_closed_positions()` once per cycle, so closed trades release their reserved exposure back to the MRM automatically (neither file had an explicit close callback — trades exit via broker SL/TP — so this diffs MT5's live position list each cycle instead).

## Setup

```bash
pip install fastapi uvicorn pydantic requests MetaTrader5
uvicorn master_risk_manager:app --host 127.0.0.1 --port 8800
```

On startup the MRM tries to read live equity from MT5 directly (source of
truth — bots are never trusted to self-report P&L for the halt logic). If
`MetaTrader5` isn't installed/connected, call `POST /v1/set_equity` once to
bootstrap it (useful for testing off the trading machine).

## Integration checklist per bot file

1. `from risk_client import RiskClient`, instantiate once with a unique `bot_id`.
2. At the point where the bot currently sizes and sends an order, call
   `risk.request_trade(...)` first. If `decision.approved` is `False`, skip
   the signal — do not fall back to sizing it yourself.
3. Use `decision.approved_risk_pct` (not your original requested risk) when
   calculating lot size — the MRM may have shrunk it.
4. After `mt5.order_send()`, call `risk.confirm_fill(decision.position_id)`
   on success or `risk.reject_fill(decision.position_id)` on failure, so the
   MRM's reserved exposure matches reality.
5. When your bot detects the position closed, call
   `risk.close_position(decision.position_id, realized_pnl_pct=...)`.

See `example_bot_integration.py` for the full before/after shape.

## What it enforces (see Config class in master_risk_manager.py to tune)

| Control | Default | Notes |
|---|---|---|
| Max account-wide open risk | 5.0% | Sum across every bot's open + reserved positions |
| Max daily drawdown | 2.0% | Vs today's start equity → triggers a fleet-wide halt |
| Max peak drawdown | 8.0% | Vs all-time peak equity → triggers a fleet-wide halt |
| Max single-trade risk | 1.0% | Hard ceiling regardless of what a bot requests |
| Max currency exposure | 1.5x single-trade cap | Aggregated across all bots, per currency (EUR, USD, JPY, etc.) |
| Correlated-group risk cap | 1.5% | Positions with |correlation| ≥ 0.70 share one bucket |
| Min trade spacing | 180s | Within a correlated group, prevents clustered entries |
| Fleet consecutive losses | 4 | Triggers a 60-min cooldown across all bots |

**Fail-closed:** if a bot can't reach the MRM, `risk_client.py` treats that as
a rejection, never as permission to trade on its own authority.

**Same-symbol netting:** if two bots disagree on direction for the same
symbol, the MRM nets the exposure rather than approving both — see
`net_opposing_exposure()`.

## Operational endpoints

- `GET /v1/status` — live snapshot (equity, open positions, exposure, halt state). Good base for a dashboard.
- `POST /v1/halt {"reason": "..."}` — manual kill switch.
- `POST /v1/resume {"confirm": true}` — resume after any halt (manual or automatic). Requires explicit confirm.
- `POST /v1/reset_day` — manual daily counter reset (auto-resets on date change too).

## Known simplifications worth revisiting before scaling further

- The correlation matrix (`Config.CORRELATION_MATRIX`) is static/approximate,
  not live-computed from rolling price data. Fine as a risk *gate*, not a
  signal.
- Unlisted symbol pairs default to correlation 0.0 (uncorrelated). Flip this
  default if you'd rather be conservative about pairs you haven't mapped yet.
- The daily-drawdown check runs on every `request_trade` and after every
  `close_position`, but not continuously — if you want it to also react to
  *floating* (unrealized) loss between events, add a periodic `/v1/mark`
  tick from a scheduler that just re-checks live equity against the caps.

## Bugs found and fixed in `babsbooks_trading_bot_v36_itafx.py`

While wiring this file I ran it (not just syntax-checked it) and found two real problems that would have hurt you in production:

1. **Crash on every trade about to execute.** In the tags/edge section right before `mt5.order_send()`, the original code referenced a variable called `patterns` — but that variable was never defined anywhere in `main()`. The actual candle-pattern data was stored in a variable called `candles`. This is a `NameError` that would fire on literally every signal that passed all the way through to execution — meaning the bot would never successfully place a trade; it would crash right at the finish line every single time. Fixed by changing `patterns[...]` to `candles[...]` (4 occurrences), matching the variable that's actually in scope.
2. **Deprecated pandas API — `fillna(method="ffill")`.** Your sandbox is running pandas 3.0.2, where this call signature was removed entirely (it was deprecated for several major versions before that). It appears twice in `add_indicators()`, computing the Order Block zones (`bull_ob_high`/`bull_ob_low`/`bear_ob_high`/`bear_ob_low`). Left as-is, `add_indicators()` — which runs on every symbol, every cycle — would throw immediately on startup. Fixed by switching to `.ffill()`, the current equivalent.

Both fixes were verified by actually running the affected functions (`add_indicators`, `candle_intelligence`, `smc_analysis`, `market_structure`) against 300 bars of synthetic OHLCV data — all four completed cleanly and produced sane output (111 indicator columns, valid RSI/SMC/candle-grade values) before wiring in the MRM calls.
