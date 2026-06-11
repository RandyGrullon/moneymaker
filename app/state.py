"""Estado compartido entre el bot (thread) y la UI (Flask). Thread-safe."""
import threading
from collections import deque
from datetime import datetime


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
        self.events = deque(maxlen=200)
        self.daily_start_balance = 0.0
        self.daily_loss_triggered = False

    def log(self, kind: str, message: str):
        """kind: info | decision | trade | learn | error"""
        with self._lock:
            self.events.appendleft({
                "time": datetime.now().strftime("%H:%M:%S"),
                "kind": kind,
                "message": message,
            })

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def set_decision(self, symbol: str, decision: dict):
        with self._lock:
            self.last_decisions[symbol] = {
                **decision,
                "time": datetime.now().strftime("%H:%M:%S"),
            }

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
                "daily_loss_triggered": self.daily_loss_triggered,
            }


# Instancia global compartida
state = BotState()
