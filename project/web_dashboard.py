from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import threading
import time
import os

app = Flask(__name__)

app.config['SECRET_KEY'] = 'babsbooks-secret'

CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading'
)

# ============================================
# MT5 INIT
# ============================================

if not mt5.initialize():
    print("MT5 initialization failed")
else:
    print("MT5 connected successfully")

# ============================================
# GLOBAL DATA
# ============================================

dashboard_data = {
    "balance": 0,
    "equity": 0,
    "positions": [],
    "signals": [],
    "confidence": {},
    "pairs": {}
}

# ============================================
# CONFIDENCE CALCULATION
# ============================================

def calculate_confidence():

    scores = {
        "technical": 50,
        "volume": 50,
        "momentum": 50,
        "fundamental": 65,
        "sentiment": 55
    }

    rates = mt5.copy_rates_from_pos(
        "EURUSD",
        mt5.TIMEFRAME_M5,
        0,
        100
    )

    if rates is not None:

        df = pd.DataFrame(rates)

        atr_pct = (
            (df['high'] - df['low']).mean()
            / df['close'].mean()
        ) * 100

        scores["technical"] = min(
            100,
            max(0, 50 + atr_pct * 300)
        )

        volume_ratio = (
            df['tick_volume'].mean()
            / df['tick_volume'].rolling(20).mean().iloc[-1]
        )

        scores["volume"] = min(100, volume_ratio * 50)

        momentum = (
            (df['close'].iloc[-1] - df['close'].iloc[-5])
            / df['close'].iloc[-5]
        ) * 100

        scores["momentum"] = min(
            100,
            max(0, 50 + momentum * 50)
        )

    scores["overall"] = int(np.mean(list(scores.values())))

    return scores

# ============================================
# BACKGROUND UPDATE THREAD
# ============================================

def updater():

    while True:

        try:

            account = mt5.account_info()

            if account:

                dashboard_data["balance"] = account.balance
                dashboard_data["equity"] = account.equity

            positions = mt5.positions_get()

            active_positions = []

            if positions:

                for pos in positions:

                    active_positions.append({
                        "symbol": pos.symbol,
                        "type": "BUY" if pos.type == 0 else "SELL",
                        "volume": pos.volume,
                        "profit": pos.profit,
                        "price": pos.price_open,
                        "current": pos.price_current
                    })

            dashboard_data["positions"] = active_positions

            symbols = [
                "EURUSD",
                "GBPUSD",
                "USDJPY",
                "AUDUSD"
            ]

            pair_data = {}

            for symbol in symbols:

                tick = mt5.symbol_info_tick(symbol)

                if tick:

                    pair_data[symbol] = {
                        "bid": tick.bid,
                        "ask": tick.ask,
                        "spread": round(
                            (tick.ask - tick.bid) * 10000,
                            2
                        )
                    }

            dashboard_data["pairs"] = pair_data

            dashboard_data["confidence"] = calculate_confidence()

            socketio.emit(
                "dashboard_update",
                dashboard_data
            )

        except Exception as e:
            print("Update Error:", e)

        time.sleep(2)

# ============================================
# ROUTES
# ============================================

@app.route("/")
def home():
    return render_template("dashboard.html")

@app.route("/api/data")
def api_data():
    return jsonify(dashboard_data)

@app.route("/api/order", methods=["POST"])
def place_order():

    data = request.json

    symbol = data["symbol"]
    action = data["action"]
    volume = float(data["volume"])

    tick = mt5.symbol_info_tick(symbol)

    if action == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    request_order = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 999999,
        "comment": "BABSBOOKS WEB",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }

    result = mt5.order_send(request_order)

    if result.retcode == mt5.TRADE_RETCODE_DONE:

        return jsonify({
            "success": True,
            "ticket": result.order
        })

    return jsonify({
        "success": False,
        "error": result.comment
    })

# ============================================
# START THREAD
# ============================================

threading.Thread(
    target=updater,
    daemon=True
).start()

# ============================================
# RUN APP
# ============================================

if __name__ == "__main__":

    print("=" * 60)
    print("BABSBOOKS INSTITUTIONAL PLATFORM")
    print("http://localhost:5000")
    print("=" * 60)

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=True
    )