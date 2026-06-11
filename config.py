"""Configuracion central del bot, cargada desde .env"""
import json
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "si")


# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# MetaTrader 5
try:
    MT5_LOGIN = int(os.getenv("MT5_LOGIN") or 0)
except ValueError:
    print("AVISO: MT5_LOGIN debe ser un número entero (ID de cuenta). Se ignorará para usar el terminal MT5 abierto.")
    MT5_LOGIN = 0
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

# Trading
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "EURUSD").split(",") if s.strip()]
TIMEFRAME = os.getenv("TIMEFRAME", "M5").upper()
LOOP_SECONDS = int(os.getenv("LOOP_SECONDS", "60"))

# Riesgo
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.5"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "65"))
TRADING_ENABLED = _bool("TRADING_ENABLED", "true")

# Identificador de las ordenes de este bot dentro de MT5
MAGIC_NUMBER = 777001

# UI
UI_PORT = int(os.getenv("UI_PORT", "5000"))

# Base de datos
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_memory.db")

# Cuentas adicionales para cambiar desde la UI
ACCOUNTS_PATH = os.path.join(os.path.dirname(__file__), "accounts.json")


def load_accounts() -> list[dict]:
    """Cuentas disponibles: la del .env (si hay) + las de accounts.json."""
    accounts = []
    if MT5_LOGIN:
        accounts.append({
            "name": "Principal (.env)",
            "login": MT5_LOGIN,
            "password": MT5_PASSWORD,
            "server": MT5_SERVER,
        })
    if os.path.exists(ACCOUNTS_PATH):
        try:
            with open(ACCOUNTS_PATH, encoding="utf-8") as f:
                for acc in json.load(f):
                    if acc.get("login") and acc.get("name"):
                        acc["login"] = int(acc["login"])
                        accounts.append(acc)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"AVISO: accounts.json invalido, se ignora: {e}")
    return accounts
