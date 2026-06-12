"""Memoria persistente del bot (SQLite): trades, lecciones aprendidas y estadisticas.

Esto es lo que hace que la IA "aprenda": cada trade cerrado genera una leccion,
y las lecciones + estadisticas se inyectan en el prompt de las decisiones futuras.
"""
import sqlite3
import threading
from datetime import datetime

import config

_lock = threading.Lock()


class Memory:
    def __init__(self):
        self.conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        with _lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER UNIQUE,
                symbol TEXT,
                direction TEXT,
                volume REAL,
                entry_price REAL,
                sl REAL,
                tp REAL,
                confidence INTEGER,
                reason TEXT,
                market_context TEXT,
                opened_at TEXT,
                closed_at TEXT,
                profit REAL,
                status TEXT DEFAULT 'open'  -- open | closed
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                lesson TEXT,
                from_profit REAL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule TEXT,
                created_at TEXT
            );
            """)
            self.conn.commit()

    # ---------- Trades ----------

    def save_trade(self, position_id, symbol, direction, volume, entry_price,
                   sl, tp, confidence, reason, market_context):
        with _lock:
            self.conn.execute(
                """INSERT OR IGNORE INTO trades
                   (position_id, symbol, direction, volume, entry_price, sl, tp,
                    confidence, reason, market_context, opened_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (position_id, symbol, direction, volume, entry_price, sl, tp,
                 confidence, reason, market_context,
                 datetime.now().isoformat(timespec="seconds")),
            )
            self.conn.commit()

    def close_trade(self, position_id, profit, closed_at):
        """Marca un trade como cerrado. Devuelve el trade o None si no era nuestro."""
        with _lock:
            cur = self.conn.execute(
                "SELECT * FROM trades WHERE position_id=? AND status='open'",
                (position_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE trades SET status='closed', profit=?, closed_at=? WHERE position_id=?",
                (profit, closed_at, position_id),
            )
            self.conn.commit()
            return dict(row)

    def recent_trades(self, limit=30) -> list[dict]:
        with _lock:
            cur = self.conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]

    def get_trade(self, position_id) -> dict | None:
        """Trade por id de posicion (para recuperar la tesis original al gestionar)."""
        with _lock:
            cur = self.conn.execute(
                "SELECT * FROM trades WHERE position_id=?", (position_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def closed_count(self) -> int:
        with _lock:
            cur = self.conn.execute(
                "SELECT COUNT(*) AS n FROM trades WHERE status='closed'"
            )
            return cur.fetchone()["n"] or 0

    def last_closed_profits(self, symbol: str, n: int = 2) -> list[float]:
        """Profits de los ultimos N cierres de un simbolo (para el cooldown)."""
        with _lock:
            cur = self.conn.execute(
                """SELECT profit FROM trades WHERE symbol=? AND status='closed'
                   ORDER BY id DESC LIMIT ?""",
                (symbol, n),
            )
            return [r["profit"] or 0.0 for r in cur.fetchall()]

    # ---------- Lecciones (el aprendizaje) ----------

    def save_lesson(self, symbol, lesson, from_profit):
        with _lock:
            self.conn.execute(
                "INSERT INTO lessons (symbol, lesson, from_profit, created_at) VALUES (?,?,?,?)",
                (symbol, lesson, from_profit,
                 datetime.now().isoformat(timespec="seconds")),
            )
            self.conn.commit()

    def recent_lessons(self, limit=12) -> list[dict]:
        with _lock:
            cur = self.conn.execute(
                "SELECT * FROM lessons ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]

    def lessons_for(self, symbol: str, limit_each: int = 6) -> dict:
        """Lecciones recientes del simbolo + de los demas simbolos, por separado."""
        with _lock:
            own = self.conn.execute(
                "SELECT * FROM lessons WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, limit_each),
            ).fetchall()
            others = self.conn.execute(
                "SELECT * FROM lessons WHERE symbol<>? ORDER BY id DESC LIMIT ?",
                (symbol, limit_each),
            ).fetchall()
        return {"symbol": [dict(r) for r in own], "others": [dict(r) for r in others]}

    # ---------- Reglas consolidadas ----------

    def replace_rules(self, rules: list[str]):
        """Sustituye el set de reglas duraderas por el nuevo (consolidacion)."""
        with _lock:
            now = datetime.now().isoformat(timespec="seconds")
            self.conn.execute("DELETE FROM rules")
            self.conn.executemany(
                "INSERT INTO rules (rule, created_at) VALUES (?,?)",
                [(r, now) for r in rules],
            )
            self.conn.commit()

    def get_rules(self) -> list[str]:
        with _lock:
            cur = self.conn.execute("SELECT rule FROM rules ORDER BY id")
            return [r["rule"] for r in cur.fetchall()]

    # ---------- Estadisticas ----------

    def stats(self) -> dict:
        with _lock:
            cur = self.conn.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(profit), 0) AS total_profit
                FROM trades WHERE status='closed'
            """)
            r = cur.fetchone()
            total = r["total"] or 0
            wins = r["wins"] or 0

            cur = self.conn.execute("""
                SELECT symbol,
                       COUNT(*) AS n,
                       SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins,
                       ROUND(COALESCE(SUM(profit), 0), 2) AS profit
                FROM trades WHERE status='closed' GROUP BY symbol
            """)
            per_symbol = [dict(row) for row in cur.fetchall()]

        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_profit": round(r["total_profit"], 2),
            "per_symbol": per_symbol,
        }

    def confidence_calibration(self) -> list[dict]:
        """Win rate real por tramo de confianza declarada: para calibrar a la IA."""
        with _lock:
            cur = self.conn.execute("""
                SELECT (MIN(confidence, 99) / 10) * 10 AS bucket,
                       COUNT(*) AS n,
                       SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins,
                       ROUND(COALESCE(SUM(profit), 0), 2) AS profit
                FROM trades WHERE status='closed' AND confidence IS NOT NULL
                GROUP BY bucket ORDER BY bucket
            """)
            rows = cur.fetchall()
        out = []
        for r in rows:
            n = r["n"] or 0
            wins = r["wins"] or 0
            out.append({
                "bucket": f"{r['bucket']}-{r['bucket'] + 9}%",
                "n": n,
                "wins": wins,
                "win_rate": round(wins / n * 100, 1) if n else 0,
                "profit": r["profit"],
            })
        return out
