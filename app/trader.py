"""Loop principal del bot: analiza, decide con la IA, ejecuta, gestiona y aprende."""
import json
import re
import time
import traceback
from datetime import date, datetime, timedelta

import config
from app.ai_brain import AIBrain
from app.memory import Memory
from app.mt5_client import MT5Client
from app.state import state


class TradingBot:
    def __init__(self, mt5_client: MT5Client, brain: AIBrain, memory: Memory,
                 news=None):
        self.mt5 = mt5_client
        self.brain = brain
        self.memory = memory
        self.news = news               # NewsCenter (opcional)
        self.current_day = None
        self.last_deal_check = datetime.now() - timedelta(days=3)
        self.last_bar_time = {}        # symbol -> vela ya analizada (anti-ruido)
        self.last_analyzed_price = {}  # symbol -> precio del ultimo analisis
        self.cooldown_until = {}       # symbol -> sin entradas nuevas hasta esa hora
        self._spread_logged_bar = {}   # symbol -> vela en la que ya se aviso del spread
        self._news_logged_event = {}   # symbol -> evento por el que ya se aviso del bloqueo
        self._last_consolidated = 0    # nº de cierres en la ultima consolidacion
        self._reconnect_attempts = 0   # reintentos de conexion MT5 seguidos
        self._last_reconnect = 0.0     # epoch del ultimo intento de reconexion

    # ---------- Loop principal ----------

    def run_forever(self):
        state.update(connected=True, trading_enabled=config.TRADING_ENABLED)
        state.log("info", f"Bot iniciado. Simbolos: {', '.join(config.SYMBOLS)} | "
                          f"Trading real: {'SI' if config.TRADING_ENABLED else 'NO (solo analisis)'}")
        while True:
            try:
                if not self.mt5.connected:
                    self._try_reconnect()
                    time.sleep(3)
                    continue
                self._reconnect_attempts = 0
                state.update(connected=True)
                # El lock evita que la UI cambie de cuenta a mitad de un ciclo
                with self.mt5.lock:
                    self._cycle()
            except Exception as e:
                state.log("error", f"Error en ciclo: {e}")
                traceback.print_exc()
            time.sleep(config.LOOP_SECONDS)

    def _try_reconnect(self):
        """Reconexion automatica a MT5 (VPS desatendido: si el terminal se cae, vuelve solo)."""
        state.update(connected=False,
                     status_text="sin sesion MT5 — reconectando automaticamente...")
        if time.time() - self._last_reconnect < 30:
            return
        self._last_reconnect = time.time()
        self._reconnect_attempts += 1
        try:
            with self.mt5.lock:
                self.mt5.connect()
            state.log("info", f"Reconectado a MT5 automaticamente "
                              f"(tras {self._reconnect_attempts} intento(s)).")
        except Exception as e:
            # Loguear el 1er fallo y luego 1 de cada 10 (cada ~5 min) para no inundar
            if self._reconnect_attempts == 1 or self._reconnect_attempts % 10 == 0:
                state.log("error", f"Reconexion MT5 fallida (intento "
                                   f"{self._reconnect_attempts}): {e}. Reintento cada 30s; "
                                   f"tambien puedes iniciar sesion desde la UI.")

    def _cycle(self):
        self._refresh_day()
        account = self.mt5.account()
        positions = self.mt5.positions()
        state.update(account=account, positions=positions)
        state.record_equity(account["equity"], account["balance"])

        # 1. Aprender de trades que se cerraron desde el ultimo ciclo
        self._learn_from_closed_trades()

        # 2. ¿Modo "solo gestionar"? (pausa manual o freno de perdida diaria).
        #    En ambos casos la IA sigue vigilando las posiciones abiertas.
        daily_stop = self._daily_loss_exceeded(account)
        manage_only = state.paused or daily_stop

        # 3. Analizar cada simbolo (un error en uno no debe frenar a los demas)
        open_symbols = {p["symbol"] for p in positions}
        for symbol in config.SYMBOLS:
            if manage_only and symbol not in open_symbols:
                continue
            state.update(status_text=f"analizando {symbol}...")
            try:
                self._analyze_symbol(symbol, positions, account, manage_only)
            except Exception as e:
                state.log("error", f"{symbol}: error en el analisis: {e}")
                traceback.print_exc()

        if daily_stop:
            state.update(status_text="PAUSADO: limite de perdida diaria (solo gestiona posiciones)")
        elif state.paused:
            state.update(status_text="pausado: solo gestiona posiciones abiertas")
        else:
            state.update(status_text=f"esperando proximo ciclo ({config.LOOP_SECONDS}s)")

    # ---------- Analisis por simbolo ----------

    def _analyze_symbol(self, symbol: str, positions: list[dict],
                        account: dict | None, manage_only: bool = False):
        open_pos = next((p for p in positions if p["symbol"] == symbol), None)
        if open_pos is None and manage_only:
            return

        snapshot = self.mt5.market_snapshot(symbol)
        if snapshot is None:
            state.log("error", f"{symbol}: sin datos de mercado (¿mercado cerrado?)")
            return

        if open_pos is not None:
            self._manage_position(open_pos, snapshot, account)
        elif not manage_only:
            self._consider_entry(symbol, snapshot, positions, account)

    # ---------- Entradas nuevas ----------

    def _consider_entry(self, symbol: str, snapshot: dict,
                        positions: list[dict], account: dict | None):
        # Filtros baratos ANTES de gastar una llamada a la IA
        until = self.cooldown_until.get(symbol)
        if until and datetime.now() < until:
            return  # en cooldown por racha de perdidas (se logueo al activarse)

        bar = snapshot["bar_time"]
        spread_ratio = snapshot.get("spread_vs_atr")
        if spread_ratio is not None and spread_ratio > config.MAX_SPREAD_ATR_RATIO:
            if self._spread_logged_bar.get(symbol) != bar:
                self._spread_logged_bar[symbol] = bar
                state.log("info", f"{symbol}: spread alto ({spread_ratio}xATR > "
                                  f"{config.MAX_SPREAD_ATR_RATIO}). Sin entradas hasta que baje.")
            return

        # Filtro duro de noticias: nada de entradas alrededor de un dato de ALTO impacto
        if self.news is not None:
            event = self.news.blocking_event(symbol)
            if event is not None:
                if self._news_logged_event.get(symbol) != event["time_utc"]:
                    self._news_logged_event[symbol] = event["time_utc"]
                    when = (f"en {event['minutes']} min" if event["minutes"] >= 0
                            else f"hace {-event['minutes']} min")
                    state.log("info", f"{symbol}: entradas BLOQUEADAS por noticia de alto "
                                      f"impacto — {event['currency']} {event['title']} ({when}). "
                                      f"Ventana: {config.NEWS_BLOCK_BEFORE_MIN} min antes / "
                                      f"{config.NEWS_BLOCK_AFTER_MIN} min despues.")
                return

        # Anti-ruido: una consulta a la IA por vela nueva (o movimiento brusco)
        price = snapshot["price"]
        atr = snapshot.get("atr_14") or 0
        same_bar = self.last_bar_time.get(symbol) == bar
        moved = atr and abs(price - self.last_analyzed_price.get(symbol, price)) >= 0.8 * atr
        if same_bar and not moved:
            return
        self.last_bar_time[symbol] = bar
        self.last_analyzed_price[symbol] = price

        stats = self.memory.stats()
        lessons = self.memory.lessons_for(symbol)
        rules = self.memory.get_rules()
        calibration = self.memory.confidence_calibration()
        news_ctx = self.news.context_for(symbol) if self.news else None

        htf = ", ".join(f"{t['timeframe']} {t['trend']}"
                        for t in snapshot.get("higher_timeframes", []))
        n_news = len((news_ctx or {}).get("headlines", []))
        n_events = len((news_ctx or {}).get("upcoming_events", []))
        state.log("think",
                  f"{symbol}: analizando vela nueva de {snapshot['timeframe']} — "
                  f"precio {price}, RSI {snapshot['rsi_14']}, ADX {snapshot['adx_14']}, "
                  f"tendencia mayor: {htf or 'n/d'}, sesion: {snapshot['session']}. "
                  f"Consultando a la IA ({len(rules)} reglas, "
                  f"{len(lessons['symbol']) + len(lessons['others'])} lecciones, "
                  f"{n_news} titulares, {n_events} eventos proximos)...")

        context = {
            "stats": stats,
            "lessons": lessons,
            "rules": rules,
            "calibration": calibration,
            "open_positions": positions,
            "account": account,
            "news": news_ctx,
        }
        decision, thought_id = self.brain.decide(snapshot, context)
        decision["thought_id"] = thought_id
        state.set_decision(symbol, decision)
        state.log("decision",
                  f"{symbol}: {decision['action'].upper()} "
                  f"(confianza {decision['confidence']}%, "
                  f"SL {decision['stop_loss_atr']}xATR / TP {decision['take_profit_atr']}xATR) "
                  f"— {decision['reason']}")

        if decision["action"] == "hold":
            state.set_thought_outcome(thought_id, "sin operacion: hold")
            return

        # Filtros de riesgo antes de ejecutar
        required = self._required_confidence(stats, symbol)
        if decision["confidence"] < required:
            state.set_thought_outcome(
                thought_id, f"bloqueada: confianza {decision['confidence']}% < minimo {required}%")
            state.log("info", f"{symbol}: confianza {decision['confidence']}% < "
                              f"minimo requerido {required}%. No se opera.")
            return
        if len(positions) >= config.MAX_OPEN_POSITIONS:
            state.set_thought_outcome(
                thought_id, f"bloqueada: maximo de posiciones ({config.MAX_OPEN_POSITIONS})")
            state.log("info", f"Maximo de posiciones abiertas ({config.MAX_OPEN_POSITIONS}). "
                              f"No se abre {symbol}.")
            return
        conflict = self._currency_conflict(symbol, decision["action"], positions)
        if conflict:
            state.set_thought_outcome(thought_id, f"bloqueada: exposicion de divisa ({conflict})")
            state.log("info", f"{symbol}: abrir esto apilaria mas de "
                              f"{config.MAX_CURRENCY_EXPOSURE} apuestas en la misma direccion "
                              f"de {conflict}. No se opera.")
            return
        if not config.TRADING_ENABLED:
            state.set_thought_outcome(thought_id, "bloqueada: TRADING_ENABLED=false (solo analisis)")
            state.log("info", f"{symbol}: TRADING_ENABLED=false, orden NO enviada (solo analisis).")
            return

        self._execute(symbol, snapshot, decision, thought_id, news_ctx)

    def _execute(self, symbol: str, snapshot: dict, decision: dict, thought_id: int,
                 news_ctx: dict | None = None):
        atr = snapshot["atr_14"]
        sl_distance = atr * decision["stop_loss_atr"]
        tp_distance = atr * decision["take_profit_atr"]

        result = self.mt5.open_trade(
            symbol, decision["action"], sl_distance, tp_distance,
            comment=f"AI {decision['confidence']}%",
        )
        if not result["ok"]:
            state.set_thought_outcome(thought_id, f"orden rechazada: {result['error']}")
            state.log("error", f"{symbol}: orden rechazada — {result['error']}")
            return

        # El contexto guardado incluye las noticias que se vieron al abrir,
        # para que la leccion del cierre pueda referenciarlas
        market_context = dict(snapshot)
        if news_ctx:
            market_context["news_at_open"] = {
                "headlines": news_ctx.get("headlines", [])[:5],
                "upcoming_events": news_ctx.get("upcoming_events", [])[:3],
            }
        self.memory.save_trade(
            position_id=result["position_id"],
            symbol=symbol,
            direction=decision["action"],
            volume=result["volume"],
            entry_price=result["price"],
            sl=result["sl"],
            tp=result["tp"],
            confidence=decision["confidence"],
            reason=decision["reason"],
            market_context=json.dumps(market_context, ensure_ascii=False),
        )
        state.set_thought_outcome(
            thought_id, f"EJECUTADA: {decision['action'].upper()} {result['volume']} @ {result['price']}")
        state.log("trade",
                  f"ORDEN EJECUTADA: {decision['action'].upper()} {result['volume']} "
                  f"{symbol} @ {result['price']} | SL {result['sl']} TP {result['tp']}")

    # ---------- Gestion de posiciones abiertas ----------

    def _manage_position(self, position: dict, snapshot: dict, account: dict | None):
        symbol = position["symbol"]
        original = self.memory.get_trade(position["ticket"])
        news_ctx = self.news.context_for(symbol) if self.news else None
        decision, thought_id = self.brain.manage(snapshot, position, original, account, news_ctx)
        state.set_decision(symbol, {**decision, "thought_id": thought_id, "kind": "gestion"})
        state.log("think",
                  f"{symbol}: gestion de la posicion {position['direction'].upper()} "
                  f"(P&L {position['profit']:.2f}) → {decision['action']} "
                  f"({decision['confidence']}%) — {decision['reason']}")

        if decision["action"] == "close":
            if decision["confidence"] < 60:
                state.set_thought_outcome(
                    thought_id, f"senal de cierre debil ({decision['confidence']}% < 60%): se mantiene")
                return
            if not config.TRADING_ENABLED:
                state.set_thought_outcome(thought_id, "cerraria, pero TRADING_ENABLED=false")
                state.log("info", f"{symbol}: la IA cerraria la posicion, "
                                  f"pero TRADING_ENABLED=false.")
                return
            result = self.mt5.close_position(position["ticket"])
            if result["ok"]:
                state.set_thought_outcome(thought_id, "POSICION CERRADA por decision de la IA")
                state.log("trade", f"POSICION CERRADA por la IA: {symbol} "
                                   f"{position['direction']} (P&L {position['profit']:.2f}) "
                                   f"— {decision['reason']}")
            else:
                state.set_thought_outcome(thought_id, f"cierre fallido: {result['error']}")
                state.log("error", f"{symbol}: no se pudo cerrar — {result['error']}")
            return

        if decision["action"] == "breakeven":
            self._move_to_breakeven(position, snapshot, decision, thought_id)
            return

        state.set_thought_outcome(thought_id, "mantener posicion")

    def _move_to_breakeven(self, position: dict, snapshot: dict,
                           decision: dict, thought_id: int):
        symbol = position["symbol"]
        entry = position["price_open"]
        current = position["price_current"]
        atr = snapshot.get("atr_14") or 0
        spread = (snapshot.get("ask") or 0) - (snapshot.get("bid") or 0)
        # Colchon minimo de beneficio para no ser barrido al instante
        min_gain = max(2 * spread, 0.2 * atr)

        buy = position["direction"] == "buy"
        gain = (current - entry) if buy else (entry - current)
        already = position["sl"] and (
            (buy and position["sl"] >= entry) or (not buy and position["sl"] <= entry))

        if already:
            state.set_thought_outcome(thought_id, "el SL ya estaba en break-even o mejor")
            return
        if gain < min_gain:
            state.set_thought_outcome(thought_id, "break-even pospuesto: beneficio aun insuficiente")
            return
        if not config.TRADING_ENABLED:
            state.set_thought_outcome(thought_id, "moveria el SL a break-even, pero TRADING_ENABLED=false")
            return
        result = self.mt5.modify_position_sl(position["ticket"], entry)
        if result["ok"]:
            state.set_thought_outcome(thought_id, f"SL movido a break-even ({result['sl']})")
            state.log("trade", f"SL A BREAK-EVEN: {symbol} {position['direction']} "
                               f"— riesgo eliminado. {decision['reason']}")
        else:
            state.set_thought_outcome(thought_id, f"no se pudo mover el SL: {result['error']}")
            state.log("error", f"{symbol}: no se pudo mover SL a break-even — {result['error']}")

    # ---------- Filtros duros ----------

    @staticmethod
    def _symbol_currencies(symbol: str):
        """('EUR','USD') para pares FX/metales tipo EURUSD o XAUUSD; None si no aplica."""
        s = re.sub(r"[._\-]", "", symbol.upper())
        if len(s) >= 6 and s[:6].isalpha():
            return s[:3], s[3:6]
        return None

    def _currency_conflict(self, symbol: str, action: str,
                           positions: list[dict]) -> str | None:
        """Evita apilar posiciones que apuestan a lo mismo (p.ej. 3 largos contra USD)."""
        pair = self._symbol_currencies(symbol)
        if pair is None:
            return None
        exposure = {}
        for p in positions:
            ppair = self._symbol_currencies(p["symbol"])
            if ppair is None:
                continue
            sign = 1 if p["direction"] == "buy" else -1
            exposure[ppair[0]] = exposure.get(ppair[0], 0) + sign
            exposure[ppair[1]] = exposure.get(ppair[1], 0) - sign
        sign = 1 if action == "buy" else -1
        for ccy, delta in ((pair[0], sign), (pair[1], -sign)):
            if abs(exposure.get(ccy, 0) + delta) > config.MAX_CURRENCY_EXPOSURE:
                return ccy
        return None

    def _required_confidence(self, stats: dict, symbol: str) -> int:
        """Adaptacion automatica: si el bot viene perdiendo, exige mas confianza."""
        required = config.MIN_CONFIDENCE
        if stats["total_trades"] >= 5:
            if stats["win_rate"] < 40:
                required += 15
            elif stats["win_rate"] < 50:
                required += 8
        # Y mas exigencia extra si ESTE simbolo en concreto viene mal
        for s in stats.get("per_symbol", []):
            if s["symbol"] == symbol and s["n"] >= 4 and (s["wins"] / s["n"]) < 0.4:
                required += 7
                break
        return min(required, 95)

    # ---------- Aprendizaje ----------

    def _learn_from_closed_trades(self):
        # Solapamiento de 10 min: si un ciclo fallo, ningun cierre queda sin
        # procesar (close_trade es idempotente: ignora lo ya cerrado en la DB).
        now = datetime.now()
        deals = self.mt5.closed_deals_since(self.last_deal_check - timedelta(minutes=10))
        for deal in deals:
            trade = self.memory.close_trade(deal["position_id"], deal["profit"], deal["time"])
            if trade is None:
                continue  # ya procesado o no es nuestro
            outcome = "GANANCIA" if deal["profit"] > 0 else "PERDIDA"
            state.log("trade", f"TRADE CERRADO: {trade['symbol']} {trade['direction']} "
                               f"→ {outcome} {deal['profit']:.2f}")
            trade["closed_at"] = deal["time"]
            self._maybe_start_cooldown(trade["symbol"])
            try:
                exit_snapshot = self.mt5.market_snapshot(trade["symbol"])
                lesson, thought_id = self.brain.learn_from_trade(trade, deal["profit"], exit_snapshot)
                if lesson:
                    self.memory.save_lesson(trade["symbol"], lesson, deal["profit"])
                    state.set_thought_outcome(thought_id, "leccion guardada en memoria")
                    state.log("learn", f"LECCION APRENDIDA [{trade['symbol']}]: {lesson}")
                self._maybe_consolidate()
            except Exception as e:
                state.log("error", f"No se pudo generar leccion: {e}")
        self.last_deal_check = now

    def _maybe_start_cooldown(self, symbol: str):
        n = config.COOLDOWN_AFTER_LOSSES
        if n <= 0:
            return
        last = self.memory.last_closed_profits(symbol, n)
        if len(last) >= n and all(p <= 0 for p in last):
            until = datetime.now() + timedelta(minutes=config.COOLDOWN_MINUTES)
            self.cooldown_until[symbol] = until
            state.log("info", f"COOLDOWN: {symbol} acumula {n} perdidas seguidas. "
                              f"Sin entradas nuevas en ese simbolo hasta las "
                              f"{until.strftime('%H:%M')}.")

    def _maybe_consolidate(self):
        closed = self.memory.closed_count()
        if (closed and closed % config.CONSOLIDATE_EVERY == 0
                and closed != self._last_consolidated):
            self._last_consolidated = closed
            lessons = self.memory.recent_lessons(30)
            rules, thought_id = self.brain.consolidate_lessons(self.memory.stats(), lessons)
            if rules:
                self.memory.replace_rules(rules)
                state.set_thought_outcome(thought_id, f"{len(rules)} reglas consolidadas y guardadas")
                state.log("learn", "REGLAS CONSOLIDADAS: " + " | ".join(rules))
            else:
                state.set_thought_outcome(thought_id, "consolidacion sin resultado: se mantienen las reglas anteriores")

    # ---------- Riesgo diario ----------

    def reset_daily_baseline(self):
        """Llamado al cambiar de cuenta: el balance inicial del dia ya no aplica."""
        self.current_day = None
        self.last_deal_check = datetime.now()
        self.last_bar_time = {}
        self.last_analyzed_price = {}

    def _refresh_day(self):
        today = date.today()
        if self.current_day != today:
            self.current_day = today
            balance = self.mt5.account()["balance"]
            state.update(daily_start_balance=balance, daily_loss_triggered=False)
            state.log("info", f"Nuevo dia de trading. Balance inicial: {balance:.2f}")

    def _daily_loss_exceeded(self, account: dict) -> bool:
        start = state.daily_start_balance
        if start <= 0:
            return False
        loss_pct = (start - account["equity"]) / start * 100
        if loss_pct >= config.MAX_DAILY_LOSS_PCT:
            if not state.daily_loss_triggered:
                state.update(daily_loss_triggered=True)
                state.log("error", f"LIMITE DIARIO: perdida del {loss_pct:.1f}% "
                                   f">= {config.MAX_DAILY_LOSS_PCT}%. Sin trades nuevos "
                                   f"hasta manana (las posiciones abiertas se siguen gestionando).")
            return True
        return False
