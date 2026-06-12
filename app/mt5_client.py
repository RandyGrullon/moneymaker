"""Cliente de MetaTrader 5: conexion, datos de mercado y ejecucion de ordenes reales."""
import math
import threading
from datetime import datetime, timedelta, timezone

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

# Timeframes superiores que se anaden al snapshot como contexto de tendencia mayor
HIGHER_TIMEFRAMES = {
    "M1": ["M15", "H1"],
    "M5": ["H1", "H4"],
    "M15": ["H1", "H4"],
    "M30": ["H4", "D1"],
    "H1": ["H4", "D1"],
    "H4": ["D1"],
    "D1": [],
}

WEEKDAYS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def _session_utc(now: datetime) -> str:
    """Sesion de mercado aproximada segun la hora UTC."""
    if now.weekday() >= 5:
        return "fin de semana"
    h = now.hour
    if 12 <= h < 16:
        return "Londres+NY (maxima liquidez)"
    if 7 <= h < 12:
        return "Londres"
    if 16 <= h < 21:
        return "Nueva York"
    if h == 21:
        return "cierre de NY (baja liquidez)"
    return "Asia"


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

    def login_account(self, login: int, password: str, server: str) -> dict:
        """Login desde la UI: inicializa el terminal si hace falta y entra a la cuenta."""
        with self.lock:
            if not self.connected:
                kwargs = {"login": login, "password": password, "server": server}
                if config.MT5_PATH:
                    kwargs["path"] = config.MT5_PATH
                if not mt5.initialize(**kwargs):
                    return {"ok": False, "error": str(mt5.last_error())}
                if mt5.account_info() is None:
                    return {"ok": False, "error": "MT5 inicializado pero sin cuenta logueada"}
                missing = [s for s in config.SYMBOLS if not mt5.symbol_select(s, True)]
                self.connected = True
                result = {"ok": True, "account": self.account()}
                if missing:
                    result["warning"] = f"simbolos no disponibles: {', '.join(missing)}"
                return result

        return self.switch_account(login, password, server)

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
        if a is None:
            # El terminal se cerro o perdio la sesion a mitad de ejecucion
            self.connected = False
            raise ConnectionError("MT5 no responde (¿terminal cerrado o sesion perdida?)")
        return {
            "login": a.login,
            "server": a.server,
            "currency": a.currency,
            "balance": a.balance,
            "equity": a.equity,
            "margin_free": a.margin_free,
            "profit": a.profit,
        }

    def candles(self, symbol: str, n: int = 100, timeframe: str | None = None):
        tf = TIMEFRAMES.get(timeframe or config.TIMEFRAME, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
        if rates is None or len(rates) == 0:
            return None
        return rates

    def market_snapshot(self, symbol: str) -> dict | None:
        """Velas + indicadores en varios timeframes, listo para mandarle a la IA."""
        rates = self.candles(symbol, 120)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if rates is None or len(rates) < 60 or info is None or tick is None or not tick.bid:
            return None

        close = rates["close"].astype(float)
        high = rates["high"].astype(float)
        low = rates["low"].astype(float)
        volume = rates["tick_volume"].astype(float)
        d = info.digits

        atr = round(self._atr(high, low, close, 14), d + 2)
        spread = tick.ask - tick.bid
        macd_line, macd_signal, macd_hist = self._macd(close)
        bb_up, bb_mid, bb_low, bb_pb = self._bollinger(close, 20, 2.0)
        adx, di_plus, di_minus = self._adx(high, low, close, 14)
        now_utc = datetime.now(timezone.utc)

        # La vela [-1] se esta formando: niveles y volumen usan velas cerradas
        hi_50 = float(high[-51:-1].max())
        lo_50 = float(low[-51:-1].min())
        avg_vol = float(volume[-22:-2].mean()) if len(volume) >= 22 else 0.0

        return {
            "symbol": symbol,
            "timeframe": config.TIMEFRAME,
            "bar_time": int(rates["time"][-1]),  # uso interno, no se manda a la IA
            "utc_time": now_utc.strftime("%H:%M"),
            "weekday": WEEKDAYS_ES[now_utc.weekday()],
            "session": _session_utc(now_utc),
            "bid": tick.bid,
            "ask": tick.ask,
            "spread_points": int(round(spread / info.point)) if info.point else 0,
            "spread_vs_atr": round(spread / atr, 3) if atr else None,
            "price": round(float(close[-1]), d),
            "change_pct_20_bars": round((close[-1] / close[-21] - 1) * 100, 3),
            "ema_9": round(self._ema(close, 9), d),
            "ema_21": round(self._ema(close, 21), d),
            "ema_50": round(self._ema(close, 50), d),
            "rsi_14": round(self._rsi(close, 14), 1),
            "atr_14": atr,
            "adx_14": round(adx, 1),
            "di_plus": round(di_plus, 1),
            "di_minus": round(di_minus, 1),
            "macd": {
                "line": round(macd_line, d + 2),
                "signal": round(macd_signal, d + 2),
                "hist": round(macd_hist, d + 2),
            },
            "bollinger": {
                "upper": round(bb_up, d),
                "middle": round(bb_mid, d),
                "lower": round(bb_low, d),
                "percent_b": bb_pb,
            },
            "high_50_bars": round(hi_50, d),
            "low_50_bars": round(lo_50, d),
            "dist_to_high_atr": round((hi_50 - close[-1]) / atr, 2) if atr else None,
            "dist_to_low_atr": round((close[-1] - lo_50) / atr, 2) if atr else None,
            "volume_last_vs_avg": round(float(volume[-2]) / avg_vol, 2) if avg_vol else None,
            "last_10_closes": [round(float(c), d) for c in close[-10:]],
            "last_5_candles": [
                {"o": round(float(rates["open"][i]), d), "h": round(float(high[i]), d),
                 "l": round(float(low[i]), d), "c": round(float(close[i]), d)}
                for i in range(-5, 0)
            ],
            "higher_timeframes": [
                s for tf in HIGHER_TIMEFRAMES.get(config.TIMEFRAME, [])
                if (s := self._tf_summary(symbol, tf, d)) is not None
            ],
        }

    def _tf_summary(self, symbol: str, tf: str, digits: int) -> dict | None:
        """Resumen compacto de un timeframe superior para contexto de tendencia."""
        rates = self.candles(symbol, 80, tf)
        if rates is None or len(rates) < 55:
            return None
        close = rates["close"].astype(float)
        high = rates["high"].astype(float)
        low = rates["low"].astype(float)
        ema21 = self._ema(close, 21)
        ema50 = self._ema(close, 50)
        price = float(close[-1])
        if price > ema21 > ema50:
            trend = "alcista"
        elif price < ema21 < ema50:
            trend = "bajista"
        else:
            trend = "lateral/mixta"
        return {
            "timeframe": tf,
            "trend": trend,
            "ema_21": round(ema21, digits),
            "ema_50": round(ema50, digits),
            "rsi_14": round(self._rsi(close, 14), 1),
            "change_pct_20_bars": round((price / close[-21] - 1) * 100, 3),
            "high_50_bars": round(float(high[-51:-1].max()), digits),
            "low_50_bars": round(float(low[-51:-1].min()), digits),
        }

    @staticmethod
    def _ema(values, period):
        k = 2 / (period + 1)
        ema = values[0]
        for v in values[1:]:
            ema = v * k + ema * (1 - k)
        return float(ema)

    @staticmethod
    def _ema_series(values, period):
        k = 2 / (period + 1)
        out = np.empty(len(values))
        out[0] = values[0]
        for i in range(1, len(values)):
            out[i] = values[i] * k + out[i - 1] * (1 - k)
        return out

    def _macd(self, close):
        """MACD 12/26/9: linea, senal e histograma."""
        macd_line = self._ema_series(close, 12) - self._ema_series(close, 26)
        signal = self._ema_series(macd_line, 9)
        return float(macd_line[-1]), float(signal[-1]), float(macd_line[-1] - signal[-1])

    @staticmethod
    def _bollinger(close, period=20, mult=2.0):
        window = close[-period:]
        mid = float(window.mean())
        std = float(window.std())
        upper, lower = mid + mult * std, mid - mult * std
        pb = round((float(close[-1]) - lower) / (upper - lower), 2) if upper > lower else 0.5
        return upper, mid, lower, pb

    @staticmethod
    def _rsi(close, period=14):
        deltas = np.diff(close)
        if len(deltas) < period:
            return 50.0
        gains = np.clip(deltas, 0, None)
        losses = np.clip(-deltas, 0, None)
        # Suavizado de Wilder: mismo valor que muestra el RSI del terminal MT5
        avg_gain = gains[:period].mean()
        avg_loss = losses[:period].mean()
        for g, l in zip(gains[period:], losses[period:]):
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            return 100.0
        return float(100 - 100 / (1 + avg_gain / avg_loss))

    @staticmethod
    def _wilder_series(values, period):
        alpha = 1.0 / period
        out = np.empty(len(values))
        out[0] = values[0]
        for i in range(1, len(values)):
            out[i] = out[i - 1] + alpha * (values[i] - out[i - 1])
        return out

    def _adx(self, high, low, close, period=14):
        """ADX de Wilder + DI+/DI-: fuerza y direccion de la tendencia."""
        up = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])),
        )
        atr_s = self._wilder_series(tr, period)
        atr_s[atr_s == 0] = 1e-12
        di_plus = 100 * self._wilder_series(plus_dm, period) / atr_s
        di_minus = 100 * self._wilder_series(minus_dm, period) / atr_s
        di_sum = di_plus + di_minus
        di_sum[di_sum == 0] = 1e-12
        dx = 100 * np.abs(di_plus - di_minus) / di_sum
        adx = self._wilder_series(dx, period)
        return float(adx[-1]), float(di_plus[-1]), float(di_minus[-1])

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

        # Normalizar al step del broker, hacia abajo: nunca arriesgar de mas.
        # round(lot, 8) solo limpia ruido de coma flotante sin romper el step.
        step = info.volume_step or 0.01
        lot = math.floor(lot / step) * step
        lot = max(info.volume_min, min(info.volume_max, lot))
        return round(lot, 8)

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

    def modify_position_sl(self, ticket: int, new_sl: float) -> dict:
        """Mueve el stop loss de una posicion (p.ej. a break-even)."""
        pos_list = mt5.positions_get(ticket=ticket)
        if not pos_list:
            return {"ok": False, "error": "posicion no encontrada"}
        p = pos_list[0]
        info = mt5.symbol_info(p.symbol)
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": p.symbol,
            "sl": round(new_sl, info.digits),
            "tp": p.tp,
            "magic": config.MAGIC_NUMBER,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = (str(mt5.last_error()) if result is None
                   else f"retcode={result.retcode} {result.comment}")
            return {"ok": False, "error": err}
        return {"ok": True, "sl": request["sl"]}

    def close_position(self, ticket: int) -> dict:
        pos_list = mt5.positions_get(ticket=ticket)
        if not pos_list:
            return {"ok": False, "error": "posicion no encontrada"}
        p = pos_list[0]
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None or not tick.bid:
            return {"ok": False,
                    "error": f"sin precio para {p.symbol} (¿mercado cerrado?)"}

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
