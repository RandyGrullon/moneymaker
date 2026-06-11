"""Loop principal del bot: analiza, decide con la IA, ejecuta y aprende."""
import json
import time
import traceback
from datetime import date, datetime, timedelta

import config
from app.ai_brain import AIBrain
from app.memory import Memory
from app.mt5_client import MT5Client
from app.state import state


class TradingBot:
    def __init__(self, mt5_client: MT5Client, brain: AIBrain, memory: Memory):
        self.mt5 = mt5_client
        self.brain = brain
        self.memory = memory
        self.current_day = None
        self.last_deal_check = datetime.now() - timedelta(days=3)

    # ---------- Loop principal ----------

    def run_forever(self):
        state.update(connected=True, trading_enabled=config.TRADING_ENABLED)
        state.log("info", f"Bot iniciado. Simbolos: {', '.join(config.SYMBOLS)} | "
                          f"Trading real: {'SI' if config.TRADING_ENABLED else 'NO (solo analisis)'}")
        while True:
            try:
                # El lock evita que la UI cambie de cuenta a mitad de un ciclo
                with self.mt5.lock:
                    self._cycle()
            except Exception as e:
                state.log("error", f"Error en ciclo: {e}")
                traceback.print_exc()
            time.sleep(config.LOOP_SECONDS)

    def _cycle(self):
        self._refresh_day()
        account = self.mt5.account()
        positions = self.mt5.positions()
        state.update(account=account, positions=positions)

        # 1. Aprender de trades que se cerraron desde el ultimo ciclo
        self._learn_from_closed_trades()

        # 2. Freno de emergencia: perdida diaria maxima
        if self._daily_loss_exceeded(account):
            state.update(status_text="PAUSADO: limite de perdida diaria alcanzado")
            return

        if state.paused:
            state.update(status_text="pausado por el usuario")
            return

        # 3. Analizar cada simbolo y decidir
        for symbol in config.SYMBOLS:
            state.update(status_text=f"analizando {symbol}...")
            self._analyze_symbol(symbol, positions)

        state.update(status_text=f"esperando proximo ciclo ({config.LOOP_SECONDS}s)")

    # ---------- Analisis y ejecucion ----------

    def _analyze_symbol(self, symbol: str, positions: list[dict]):
        snapshot = self.mt5.market_snapshot(symbol)
        if snapshot is None:
            state.log("error", f"{symbol}: sin datos de mercado (¿mercado cerrado?)")
            return

        stats = self.memory.stats()
        lessons = self.memory.recent_lessons()

        decision = self.brain.decide(snapshot, stats, lessons, positions)
        state.set_decision(symbol, decision)
        state.log("decision",
                  f"{symbol}: {decision['action'].upper()} "
                  f"(confianza {decision['confidence']}%) — {decision['reason']}")

        if decision["action"] == "hold":
            return

        # Filtros de riesgo antes de ejecutar
        required_conf = self._required_confidence(stats)
        if decision["confidence"] < required_conf:
            state.log("info", f"{symbol}: confianza {decision['confidence']}% < "
                              f"minimo requerido {required_conf}%. No se opera.")
            return
        if any(p["symbol"] == symbol for p in positions):
            state.log("info", f"{symbol}: ya hay una posicion abierta. No se duplica.")
            return
        if len(positions) >= config.MAX_OPEN_POSITIONS:
            state.log("info", f"Maximo de posiciones abiertas ({config.MAX_OPEN_POSITIONS}). "
                              f"No se abre {symbol}.")
            return
        if not config.TRADING_ENABLED:
            state.log("info", f"{symbol}: TRADING_ENABLED=false, orden NO enviada (solo analisis).")
            return

        self._execute(symbol, snapshot, decision)

    def _execute(self, symbol: str, snapshot: dict, decision: dict):
        atr = snapshot["atr_14"]
        sl_distance = atr * decision["stop_loss_atr"]
        tp_distance = atr * decision["take_profit_atr"]

        result = self.mt5.open_trade(
            symbol, decision["action"], sl_distance, tp_distance,
            comment=f"AI {decision['confidence']}%",
        )
        if not result["ok"]:
            state.log("error", f"{symbol}: orden rechazada — {result['error']}")
            return

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
            market_context=json.dumps(snapshot, ensure_ascii=False),
        )
        state.log("trade",
                  f"ORDEN EJECUTADA: {decision['action'].upper()} {result['volume']} "
                  f"{symbol} @ {result['price']} | SL {result['sl']} TP {result['tp']}")

    # ---------- Aprendizaje ----------

    def _learn_from_closed_trades(self):
        check_from = self.last_deal_check
        self.last_deal_check = datetime.now()
        for deal in self.mt5.closed_deals_since(check_from):
            trade = self.memory.close_trade(deal["position_id"], deal["profit"], deal["time"])
            if trade is None:
                continue  # ya procesado o no es nuestro
            outcome = "GANANCIA" if deal["profit"] > 0 else "PERDIDA"
            state.log("trade", f"TRADE CERRADO: {trade['symbol']} {trade['direction']} "
                               f"→ {outcome} {deal['profit']:.2f}")
            try:
                lesson = self.brain.learn_from_trade(trade, deal["profit"])
                if lesson:
                    self.memory.save_lesson(trade["symbol"], lesson, deal["profit"])
                    state.log("learn", f"LECCION APRENDIDA [{trade['symbol']}]: {lesson}")
            except Exception as e:
                state.log("error", f"No se pudo generar leccion: {e}")

    def _required_confidence(self, stats: dict) -> int:
        """Adaptacion automatica: si el bot viene perdiendo, exige mas confianza."""
        required = config.MIN_CONFIDENCE
        if stats["total_trades"] >= 5:
            if stats["win_rate"] < 40:
                required += 15
            elif stats["win_rate"] < 50:
                required += 8
        return min(required, 95)

    # ---------- Riesgo diario ----------

    def reset_daily_baseline(self):
        """Llamado al cambiar de cuenta: el balance inicial del dia ya no aplica."""
        self.current_day = None
        self.last_deal_check = datetime.now()

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
                                   f">= {config.MAX_DAILY_LOSS_PCT}%. Bot pausado hasta manana.")
            return True
        return False
