"""Configuracion central del bot, cargada desde .env"""
import json
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "si")


# IA: "groq" (nube) o "ollama" (local, sin limites de tokens)
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Ollama (IA local)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")

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

# Filtros duros (no dependen de la IA)
# No abrir trades si el spread supera este multiplo del ATR (mercado caro/iliquido)
MAX_SPREAD_ATR_RATIO = float(os.getenv("MAX_SPREAD_ATR_RATIO", "0.25"))
# Tras N perdidas seguidas en un simbolo, pausa de entradas en ese simbolo (0 = off)
COOLDOWN_AFTER_LOSSES = int(os.getenv("COOLDOWN_AFTER_LOSSES", "2"))
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "45"))
# Maximo de posiciones apostando en la misma direccion de una misma divisa
MAX_CURRENCY_EXPOSURE = int(os.getenv("MAX_CURRENCY_EXPOSURE", "2"))
# Cada cuantos trades cerrados la IA consolida sus lecciones en reglas duraderas
CONSOLIDATE_EVERY = int(os.getenv("CONSOLIDATE_EVERY", "10"))

# Noticias en vivo (RSS de muchas fuentes + calendario economico)
NEWS_ENABLED = _bool("NEWS_ENABLED", "true")
NEWS_REFRESH_SECONDS = int(os.getenv("NEWS_REFRESH_SECONDS", "120"))
# Bloqueo de entradas alrededor de eventos de ALTO impacto de las divisas del
# simbolo (minutos antes / despues). 0 y 0 = desactivado.
NEWS_BLOCK_BEFORE_MIN = int(os.getenv("NEWS_BLOCK_BEFORE_MIN", "15"))
NEWS_BLOCK_AFTER_MIN = int(os.getenv("NEWS_BLOCK_AFTER_MIN", "10"))
# Feeds RSS extra del usuario, separados por coma
NEWS_EXTRA_FEEDS = [u.strip() for u in os.getenv("NEWS_EXTRA_FEEDS", "").split(",") if u.strip()]

# Identificador de las ordenes de este bot dentro de MT5
MAGIC_NUMBER = 777001

# UI
UI_PORT = int(os.getenv("UI_PORT", "5000"))
# 127.0.0.1 = solo esta maquina (local). 0.0.0.0 = accesible desde fuera (VPS);
# en ese caso UI_PASSWORD es obligatoria.
UI_HOST = os.getenv("UI_HOST", "127.0.0.1").strip() or "127.0.0.1"
UI_PASSWORD = os.getenv("UI_PASSWORD", "").strip()
UI_SECRET_KEY = os.getenv("UI_SECRET_KEY", "").strip()


def ui_is_local() -> bool:
    return UI_HOST in ("127.0.0.1", "localhost", "::1")


def ui_secret_key() -> str:
    """Clave de firma de sesiones. Se genera sola y se persiste en el .env
    (solo si hay contraseña) para que el login sobreviva a los reinicios."""
    global UI_SECRET_KEY
    if not UI_SECRET_KEY:
        import secrets
        UI_SECRET_KEY = secrets.token_hex(32)
        if UI_PASSWORD:
            _write_env_values({"UI_SECRET_KEY": UI_SECRET_KEY})
    return UI_SECRET_KEY


# Logs a archivo (necesario para correr desatendido en un VPS)
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

# Base de datos
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_memory.db")

# Cuentas adicionales para cambiar desde la UI
ACCOUNTS_PATH = os.path.join(os.path.dirname(__file__), "accounts.json")


ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _write_env_values(values: dict):
    """Escribe/actualiza claves en el .env conservando el resto del archivo."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()

    pending = {k: str(v) for k, v in values.items()}
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    for key, value in pending.items():
        lines.append(f"{key}={value}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def save_mt5_credentials(login: int, password: str, server: str):
    """Guarda las credenciales MT5 directo en el .env y actualiza la config en memoria."""
    global MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
    _write_env_values({"MT5_LOGIN": login, "MT5_PASSWORD": password, "MT5_SERVER": server})
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER = login, password, server


def runtime_settings() -> dict:
    """Ajustes que la UI puede ver (y algunos editar)."""
    return {
        "trading_enabled": TRADING_ENABLED,
        "min_confidence": MIN_CONFIDENCE,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
        "loop_seconds": LOOP_SECONDS,
        "max_spread_atr_ratio": MAX_SPREAD_ATR_RATIO,
        "cooldown_after_losses": COOLDOWN_AFTER_LOSSES,
        "cooldown_minutes": COOLDOWN_MINUTES,
        "max_currency_exposure": MAX_CURRENCY_EXPOSURE,
        "consolidate_every": CONSOLIDATE_EVERY,
        "news_enabled": NEWS_ENABLED,
        "news_refresh_seconds": NEWS_REFRESH_SECONDS,
        "news_block_before_min": NEWS_BLOCK_BEFORE_MIN,
        "news_block_after_min": NEWS_BLOCK_AFTER_MIN,
        "symbols": SYMBOLS,
        "timeframe": TIMEFRAME,
        "ai_provider": AI_PROVIDER,
        "ai_model": OLLAMA_MODEL if AI_PROVIDER == "ollama" else GROQ_MODEL,
        "ui_port": UI_PORT,
    }


def update_settings(data: dict) -> dict:
    """Aplica ajustes desde la UI (en memoria + .env). Devuelve lo que cambio.

    Lanza ValueError con mensaje claro si algun valor es invalido.
    """
    global TRADING_ENABLED, MIN_CONFIDENCE, RISK_PER_TRADE_PCT
    global MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_PCT, LOOP_SECONDS
    global MAX_SPREAD_ATR_RATIO, COOLDOWN_AFTER_LOSSES, COOLDOWN_MINUTES
    global NEWS_REFRESH_SECONDS, NEWS_BLOCK_BEFORE_MIN, NEWS_BLOCK_AFTER_MIN

    def _num(key, caster, lo, hi, label):
        raw = data[key]
        try:
            value = caster(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: '{raw}' no es un numero valido")
        if not (lo <= value <= hi):
            raise ValueError(f"{label}: debe estar entre {lo} y {hi}")
        return value

    applied = {}
    if "trading_enabled" in data:
        applied["TRADING_ENABLED"] = "true" if bool(data["trading_enabled"]) else "false"
    if "min_confidence" in data:
        applied["MIN_CONFIDENCE"] = _num("min_confidence", int, 0, 95, "Confianza minima")
    if "risk_per_trade_pct" in data:
        applied["RISK_PER_TRADE_PCT"] = _num("risk_per_trade_pct", float, 0.05, 10, "Riesgo por trade %")
    if "max_open_positions" in data:
        applied["MAX_OPEN_POSITIONS"] = _num("max_open_positions", int, 1, 20, "Max posiciones")
    if "max_daily_loss_pct" in data:
        applied["MAX_DAILY_LOSS_PCT"] = _num("max_daily_loss_pct", float, 0.5, 50, "Perdida diaria max %")
    if "loop_seconds" in data:
        applied["LOOP_SECONDS"] = _num("loop_seconds", int, 10, 3600, "Segundos por ciclo")
    if "max_spread_atr_ratio" in data:
        applied["MAX_SPREAD_ATR_RATIO"] = _num("max_spread_atr_ratio", float, 0.05, 2, "Spread maximo vs ATR")
    if "cooldown_after_losses" in data:
        applied["COOLDOWN_AFTER_LOSSES"] = _num("cooldown_after_losses", int, 0, 10, "Cooldown: perdidas seguidas")
    if "cooldown_minutes" in data:
        applied["COOLDOWN_MINUTES"] = _num("cooldown_minutes", int, 5, 480, "Cooldown: minutos")
    if "news_refresh_seconds" in data:
        applied["NEWS_REFRESH_SECONDS"] = _num("news_refresh_seconds", int, 30, 3600, "Refresco de noticias")
    if "news_block_before_min" in data:
        applied["NEWS_BLOCK_BEFORE_MIN"] = _num("news_block_before_min", int, 0, 240, "Bloqueo noticias: min antes")
    if "news_block_after_min" in data:
        applied["NEWS_BLOCK_AFTER_MIN"] = _num("news_block_after_min", int, 0, 240, "Bloqueo noticias: min despues")

    if not applied:
        raise ValueError("no se mando ningun ajuste valido")

    _write_env_values(applied)
    if "TRADING_ENABLED" in applied:
        TRADING_ENABLED = applied["TRADING_ENABLED"] == "true"
    if "MIN_CONFIDENCE" in applied:
        MIN_CONFIDENCE = applied["MIN_CONFIDENCE"]
    if "RISK_PER_TRADE_PCT" in applied:
        RISK_PER_TRADE_PCT = applied["RISK_PER_TRADE_PCT"]
    if "MAX_OPEN_POSITIONS" in applied:
        MAX_OPEN_POSITIONS = applied["MAX_OPEN_POSITIONS"]
    if "MAX_DAILY_LOSS_PCT" in applied:
        MAX_DAILY_LOSS_PCT = applied["MAX_DAILY_LOSS_PCT"]
    if "LOOP_SECONDS" in applied:
        LOOP_SECONDS = applied["LOOP_SECONDS"]
    if "MAX_SPREAD_ATR_RATIO" in applied:
        MAX_SPREAD_ATR_RATIO = applied["MAX_SPREAD_ATR_RATIO"]
    if "COOLDOWN_AFTER_LOSSES" in applied:
        COOLDOWN_AFTER_LOSSES = applied["COOLDOWN_AFTER_LOSSES"]
    if "COOLDOWN_MINUTES" in applied:
        COOLDOWN_MINUTES = applied["COOLDOWN_MINUTES"]
    if "NEWS_REFRESH_SECONDS" in applied:
        NEWS_REFRESH_SECONDS = applied["NEWS_REFRESH_SECONDS"]
    if "NEWS_BLOCK_BEFORE_MIN" in applied:
        NEWS_BLOCK_BEFORE_MIN = applied["NEWS_BLOCK_BEFORE_MIN"]
    if "NEWS_BLOCK_AFTER_MIN" in applied:
        NEWS_BLOCK_AFTER_MIN = applied["NEWS_BLOCK_AFTER_MIN"]
    return applied


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
