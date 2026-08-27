"""
risk_client.py
--------------------------------------------------------------------------
Drop-in helper each bot (V36, V45, V46, simple_trade_bot, ...) imports to
talk to the Master Risk Manager instead of sizing/sending orders on its own
authority.

INTEGRATION -- what changes in each existing bot file
--------------------------------------------------------------------------
Find the point in each bot where it currently does something like:

    if should_enter:
        lot_size = calculate_position_size(risk_pct, stop_loss_pips)
        mt5.order_send(request)

Replace it with:

    from risk_client import RiskClient
    risk = RiskClient(bot_id="bot_1_v36_eurusd")

    decision = risk.request_trade(
        symbol=symbol,
        direction="BUY" if signal_is_long else "SELL",
        proposed_risk_pct=1.0,          # whatever the bot's own logic wanted
        strategy_tag="v36_breakout_state_machine",
    )

    if not decision.approved:
        log.info(f"MRM rejected: {decision.reason}")
        continue   # skip this signal, bot keeps running its own loop

    # MRM approved -- use decision.approved_risk_pct (may be smaller than
    # requested, e.g. if it got netted against an opposing bot's position)
    lot_size = calculate_position_size(decision.approved_risk_pct, stop_loss_pips)
    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        risk.confirm_fill(decision.position_id)
    else:
        risk.reject_fill(decision.position_id)   # release the reservation

And whenever the bot detects a position it opened has closed (in its own
polling loop / on_trade event):

    risk.close_position(decision.position_id, realized_pnl_pct=pnl_as_pct_of_equity)

Each bot keeps ALL of its own signal generation untouched (EMA/RSI/SMC/
confluence logic etc.) -- only the sizing-and-send step routes through
the MRM first.
--------------------------------------------------------------------------
"""

import time
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class Decision:
    approved: bool
    position_id: Optional[str]
    approved_risk_pct: float
    reason: str


class RiskClient:
    def __init__(self, bot_id: str, base_url: str = "http://127.0.0.1:8800",
                 timeout: float = 3.0, retries: int = 2):
        self.bot_id = bot_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _post(self, path: str, payload: dict) -> dict:
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.3 * (attempt + 1))
        # If the MRM is unreachable, FAIL CLOSED -- a bot must never fall back
        # to trading on its own authority just because the risk service is down.
        raise RuntimeError(f"MRM unreachable after {self.retries + 1} attempts: {last_err}")

    def request_trade(self, symbol: str, direction: str, proposed_risk_pct: float,
                       strategy_tag: str = "") -> Decision:
        try:
            data = self._post("/v1/request_trade", {
                "bot_id": self.bot_id,
                "symbol": symbol,
                "direction": direction,
                "proposed_risk_pct": proposed_risk_pct,
                "strategy_tag": strategy_tag,
            })
        except RuntimeError as e:
            # Fail closed: treat an unreachable MRM as a rejection, never a free pass.
            return Decision(approved=False, position_id=None, approved_risk_pct=0.0,
                             reason=f"MRM unreachable, failing closed: {e}")
        return Decision(**data)

    def confirm_fill(self, position_id: str):
        return self._post("/v1/confirm_fill", {"position_id": position_id})

    def reject_fill(self, position_id: str):
        return self._post("/v1/reject_fill", {"position_id": position_id})

    def close_position(self, position_id: str, realized_pnl_pct: float):
        return self._post("/v1/close_position", {
            "position_id": position_id,
            "realized_pnl_pct": realized_pnl_pct,
        })

    def status(self) -> dict:
        r = requests.get(f"{self.base_url}/v1/status", timeout=self.timeout)
        r.raise_for_status()
        return r.json()
