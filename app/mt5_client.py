"""Cliente de MetaTrader 5: conexion, datos de mercado y ejecucion de ordenes reales."""
import threading
from datetime import datetime, timedelta

import MetaTrader5 as mt5
import numpy as np

import config

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class MT5Client:
    def __init__(self):
        self.connected = False
        # Evita que un cambio de cuenta ocurra en medio de un ciclo del bot
        self.lock = threading.Lock()

    # ---------- Conexion ----------

    def connect(self) -> bool:
        kwargs = {}
        if config.MT5_PATH:
            kwargs["path"] = config.MT5_PATH
        if config.MT5_LOGIN:
            kwargs["login"] = config.MT5_LOGIN
            kwargs["password"] = config.MT5_PASSWORD
            kwargs["server"] = config.MT5_SERVER

        if not mt5.initialize(**kwargs):
            raise ConnectionError(f"No se pudo conectar a MT5: {mt5.last_error()}")

        info = mt5.account_info()
        if info is None:
            raise ConnectionError("MT5 inicializado pero sin cuenta logueada")

        # Asegurar que los simbolos esten visibles en Market Watch
        for symbol in config.SYMBOLS:
            if not mt5.symbol_select(symbol, True):
                raise ValueError(f"El simbolo '{symbol}' no existe en este broker")

        self.connected = True
        return True

    def shutdown(self):
        mt5.shutdown()
        self.connected = False

    def switch_account(self, login: int, password: str, server: str) -> dict:
        """Cambia de cuenta dentro del mismo terminal MT5, sin reiniciar el bot."""
        with self.lock:
            previous = mt5.account_info()
            if previous and previous.login == login:
                return {"ok": True, "account": self.account(),
                        "note": "ya estabas en esa cuenta"}

            if not mt5.login(login, password=password, server=server):
                err = str(mt5.last_error())
                # Intentar volver a dejar el terminal en un estado usable
                still = mt5.account_info()
                if still is None:
                    self.connected = False
                return {"ok": False, "error": err}

            missing = [s for s in config.SYMBOLS if not mt5.symbol_select(s, True)]
            self.connected = True
            result = {"ok": True, "account": self.account()}
            if missing:
                result["warning"] = f"simbolos no disponibles en esta cuenta: {', '.join(missing)}"
            return result

    # ---------- Datos ----------

    def account(self) -> dict:
        a = mt5.account_info()
        return {
            "login": a.login,
            "server": a.server,
            "currency": a.currency,
            "balance": a.balance,
            "equity": a.equity,
            "margin_free": a.margin_free,
            "profit": a.profit,
        }

    def candles(self, symbol: str, n: int = 100):
        tf = TIMEFRAMES.get(config.TIMEFRAME, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
        if rates is None or len(rates) == 0:
            return None
        return rates

    def market_snapshot(self, symbol: str) -> dict | None:
        """Velas + indicadores calculados, listo para mandarle a la IA."""
        rates = self.candles(symbol, 100)
        if rates is None:
            return None
        close = rates["close"].astype(float)
        high = rates["high"].astype(float)
        low = rates["low"].astype(float)

        tick = mt5.symbol_info_tick(symbol)
        return {
            "symbol": symbol,
            "timeframe": config.TIMEFRAME,
            "bid": tick.bid,
            "ask": tick.ask,
            "price": close[-1],
            "change_pct_20_bars": round((close[-1] / close[-21] - 1) * 100, 3),
            "ema_9": round(self._ema(close, 9), 5),
            "ema_21": round(self._ema(close, 21), 5),
            "ema_50": round(self._ema(close, 50), 5),
            "rsi_14": round(self._rsi(close, 14), 1),
            "atr_14": self._atr(high, low, close, 14),
            "last_10_closes": [round(c, 5) for c in close[-10:]],
        }

    @staticmethod
    def _ema(values, period):
        k = 2 / (period + 1)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return float(ema)

    @staticmethod
    def _rsi(close, period=14):
        deltas = np.diff(close)
        gains = np.clip(deltas, 0, None)
        losses = np.clip(-deltas, 0, None)
        avg_gain = gains[-period:].mean()
        avg_loss = losses[-period:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))

    @staticmethod
    def _atr(high, low, close, period=14):
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])),
        )
        return float(tr[-period:].mean())

    # ---------- Posiciones ----------

    def positions(self) -> list[dict]:
        result = []
        for p in mt5.positions_get() or []:
            if p.magic != config.MAGIC_NUMBER:
                continue
            result.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "direction": "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
            })
        return result

    def closed_deals_since(self, since: datetime) -> list[dict]:
        """Deals de salida (cierres) del bot desde una fecha, para aprender de ellos."""
        deals = mt5.history_deals_get(since, datetime.now() + timedelta(days=1)) or []
        result = []
        for d in deals:
            if d.magic != config.MAGIC_NUMBER or d.entry != mt5.DEAL_ENTRY_OUT:
                continue
            result.append({
                "position_id": d.position_id,
                "symbol": d.symbol,
                "profit": d.profit + d.swap + d.commission,
                "price": d.price,
                "time": datetime.fromtimestamp(d.time).isoformat(timespec="seconds"),
            })
        return result

    # ---------- Ordenes ----------

    def calc_lot(self, symbol: str, sl_distance: float) -> float:
        """Lote segun riesgo: arriesga RISK_PER_TRADE_PCT del equity hasta el SL."""
        info = mt5.symbol_info(symbol)
        equity = mt5.account_info().equity
        risk_amount = equity * config.RISK_PER_TRADE_PCT / 100

        # Valor monetario de 1.0 de movimiento de precio por lote
        value_per_unit = info.trade_tick_value / info.trade_tick_size
        lot = risk_amount / (sl_distance * value_per_unit)

        # Normalizar al step del broker
        step = info.volume_step
        lot = max(info.volume_min, min(info.volume_max, round(lot / step) * step))
        return round(lot, 2)

    def open_trade(self, symbol: str, direction: str, sl_distance: float,
                   tp_distance: float, comment: str = "") -> dict:
        """Manda una orden a mercado real. Devuelve dict con ok, ticket, etc."""
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)

        if direction == "buy":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
            sl = price - sl_distance
            tp = price + tp_distance
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
            sl = price + sl_distance
            tp = price - tp_distance

        lot = self.calc_lot(symbol, sl_distance)
        digits = info.digits

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "deviation": 20,
            "magic": config.MAGIC_NUMBER,
            "comment": comment[:25],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        # Algunos brokers no aceptan IOC: reintentar con FOK
        if result and result.retcode == 10030:
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(request)

        if result is None:
            return {"ok": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "error": f"retcode={result.retcode} {result.comment}"}

        return {
            "ok": True,
            "ticket": result.order,
            "position_id": result.order,
            "price": result.price,
            "volume": result.volume,
            "sl": request["sl"],
            "tp": request["tp"],
        }

    def close_position(self, ticket: int) -> dict:
        pos_list = mt5.positions_get(ticket=ticket)
        if not pos_list:
            return {"ok": False, "error": "posicion no encontrada"}
        p = pos_list[0]
        tick = mt5.symbol_info_tick(p.symbol)

        if p.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": config.MAGIC_NUMBER,
            "comment": "bot close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == 10030:
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = str(mt5.last_error()) if result is None else result.comment
            return {"ok": False, "error": err}
        return {"ok": True}
