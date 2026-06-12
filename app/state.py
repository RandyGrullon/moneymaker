"""Estado compartido entre el bot (thread) y la UI (Flask). Thread-safe."""
import logging
import logging.handlers
import os
import threading
from collections import deque
from datetime import datetime

import config


def _build_file_logger() -> logging.Logger:
    """Log rotativo en logs/bot.log: imprescindible para correr desatendido en un VPS."""
    logger = logging.getLogger("omnitrade")
    if not logger.handlers:
        try:
            os.makedirs(config.LOG_DIR, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(config.LOG_DIR, "bot.log"),
                maxBytes=5_000_000, backupCount=3, encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


_file_log = _build_file_logger()


class BotState:
    def __init__(self):
        self._lock = threading.Lock()
        self.paused = False
        self.connected = False
        self.trading_enabled = True
        self.status_text = "iniciando..."
        self.account = {}
        self.positions = []
        self.last_decisions = {}   # symbol -> ultima decision de la IA
        self.events = deque(maxlen=1000)
        self.equity_history = deque(maxlen=600)  # puntos {time, equity, balance}
        self.daily_start_balance = 0.0
        self.daily_loss_triggered = False
        # Registro COMPLETO de cada llamada a la IA (prompt, respuesta, resultado)
        self.thoughts = deque(maxlen=400)
        self._thought_seq = 0

    def log(self, kind: str, message: str):
        """kind: info | think | decision | trade | learn | error"""
        with self._lock:
            self.events.appendleft({
                "time": datetime.now().strftime("%H:%M:%S"),
                "kind": kind,
                "message": message,
            })
        _file_log.info("[%s] %s", kind.upper(), message)

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def record_equity(self, equity: float, balance: float):
        """Un punto de la curva de equity por ciclo del bot (para el grafico de la UI)."""
        with self._lock:
            self.equity_history.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "equity": equity,
                "balance": balance,
            })

    def set_decision(self, symbol: str, decision: dict):
        with self._lock:
            self.last_decisions[symbol] = {
                **decision,
                "time": datetime.now().strftime("%H:%M:%S"),
            }

    # ---------- Pensamientos de la IA (transparencia total) ----------

    def add_thought(self, thought: dict) -> int:
        """Guarda el registro completo de una llamada a la IA. Devuelve su id."""
        with self._lock:
            self._thought_seq += 1
            record = {
                "id": self._thought_seq,
                "time": datetime.now().strftime("%H:%M:%S"),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "outcome": "",
                **thought,
            }
            self.thoughts.appendleft(record)
            return record["id"]

    def set_thought_outcome(self, thought_id: int, outcome: str):
        """Anota que hizo el bot con esa llamada (ejecutada, bloqueada por X, etc)."""
        with self._lock:
            for t in self.thoughts:
                if t["id"] == thought_id:
                    t["outcome"] = outcome
                    return

    def thoughts_snapshot(self, limit: int = 60, symbol: str = "", phase: str = "") -> list[dict]:
        with self._lock:
            out = []
            for t in self.thoughts:
                if symbol and t.get("symbol") != symbol:
                    continue
                if phase and t.get("phase") != phase:
                    continue
                out.append(dict(t))
                if len(out) >= limit:
                    break
            return out

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "paused": self.paused,
                "connected": self.connected,
                "trading_enabled": self.trading_enabled,
                "status_text": self.status_text,
                "account": self.account,
                "positions": self.positions,
                "last_decisions": dict(self.last_decisions),
                "events": list(self.events),
                "equity_history": list(self.equity_history),
                "daily_loss_triggered": self.daily_loss_triggered,
            }


# Instancia global compartida
state = BotState()
