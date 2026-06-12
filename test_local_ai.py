"""Prueba rapida del cerebro IA contra Ollama local (borrar despues)."""
import json
import time

from app.ai_brain import AIBrain
from app.state import state

snapshot = {
    "symbol": "EURUSD", "timeframe": "M5", "price": 1.0842,
    "rsi_14": 28.4, "atr_14": 0.00085, "adx_14": 31.2, "di_plus": 14.0, "di_minus": 29.5,
    "ema_9": 1.0848, "ema_21": 1.0852, "ema_50": 1.0861,
    "macd": {"line": -0.0006, "signal": -0.0004, "hist": -0.0002},
    "bollinger": {"upper": 1.0868, "middle": 1.0854, "lower": 1.0840, "percent_b": 0.07},
    "high_50_bars": 1.0875, "low_50_bars": 1.0838,
    "spread_points": 12, "spread_vs_atr": 0.14,
    "session": "Londres+NY (maxima liquidez)", "utc_time": "13:40", "weekday": "martes",
    "higher_timeframes": [
        {"timeframe": "H1", "trend": "bajista", "rsi_14": 35.1},
        {"timeframe": "H4", "trend": "bajista", "rsi_14": 41.0},
    ],
}
context = {
    "stats": {"total_trades": 12, "win_rate": 58.3, "total_profit": 41.2,
              "per_symbol": [{"symbol": "EURUSD", "n": 7, "wins": 5, "profit": 38.0}]},
    "calibration": [{"bucket": "70-79%", "n": 6, "wins": 4, "win_rate": 66.7, "profit": 25.0}],
    "rules": ["Nunca operar contra la tendencia de H4 con ADX < 20."],
    "lessons": {"symbol": [{"symbol": "EURUSD",
                            "lesson": "No comprar con RSI>70 en tendencia bajista de H1."}],
                "others": []},
    "open_positions": [],
    "account": {"balance": 1000.0, "equity": 998.5, "margin_free": 950.0,
                "profit": -1.5, "currency": "USD"},
}

brain = AIBrain()
print(f"Proveedor: modelo={brain.model}")
t0 = time.time()
decision, thought_id = brain.decide(snapshot, context)
print(f"Tiempo de respuesta: {time.time() - t0:.1f}s")
print(json.dumps(decision, indent=2, ensure_ascii=False))

print("\n--- Pensamiento registrado ---")
t = state.thoughts_snapshot(limit=1)[0]
print(f"id={t['id']} fase={t['phase']} modelo={t['model']} "
      f"latencia={t['latency_s']}s error={t['error'] or 'no'}")
