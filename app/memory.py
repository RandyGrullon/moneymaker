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
