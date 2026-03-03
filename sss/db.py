from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .config import KST


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  telegram_id TEXT PRIMARY KEY,
  stop_loss_pct REAL DEFAULT 10,
  daily_report INTEGER DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  telegram_id TEXT,
  symbol TEXT,
  name TEXT,
  buy_price REAL,
  buy_date TEXT,
  peak_price REAL,
  peak_date TEXT,
  kospi_at_peak REAL,
  created_at TEXT,
  updated_at TEXT,
  UNIQUE(telegram_id, symbol)
);

CREATE TABLE IF NOT EXISTS price_cache (
  trading_date TEXT,
  symbol TEXT,
  close REAL,
  PRIMARY KEY(trading_date, symbol)
);

CREATE TABLE IF NOT EXISTS notifications (
  trading_date TEXT,
  telegram_id TEXT,
  symbol TEXT,
  type TEXT,
  PRIMARY KEY(trading_date, telegram_id, symbol, type)
);

CREATE TABLE IF NOT EXISTS job_status (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  trading_date TEXT,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS trading_calendar (
  date TEXT PRIMARY KEY,
  is_session INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def now_iso(self) -> str:
        return datetime.now(tz=KST).isoformat()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def ensure_user(self, telegram_id: str) -> sqlite3.Row:
        now = self.now_iso()
        self.conn.execute(
            """
            INSERT INTO users (telegram_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO NOTHING
            """,
            (telegram_id, now, now),
        )
        self.conn.commit()
        return self.get_user(telegram_id)

    def get_user(self, telegram_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row is None:
            raise KeyError("user not found")
        return row

    def list_users(self) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY telegram_id").fetchall()
        return list(rows)

    def set_stop_loss(self, telegram_id: str, pct: float) -> None:
        self.ensure_user(telegram_id)
        self.conn.execute(
            "UPDATE users SET stop_loss_pct = ?, updated_at = ? WHERE telegram_id = ?",
            (pct, self.now_iso(), telegram_id),
        )
        self.conn.commit()

    def set_daily_report(self, telegram_id: str, on: bool) -> None:
        self.ensure_user(telegram_id)
        self.conn.execute(
            "UPDATE users SET daily_report = ?, updated_at = ? WHERE telegram_id = ?",
            (1 if on else 0, self.now_iso(), telegram_id),
        )
        self.conn.commit()

    def upsert_holding(
        self,
        telegram_id: str,
        symbol: str,
        name: str,
        buy_price: float,
        buy_date: str,
        peak_price: float,
        peak_date: str,
        kospi_at_peak: float,
    ) -> None:
        self.ensure_user(telegram_id)
        now = self.now_iso()
        self.conn.execute(
            """
            INSERT INTO holdings (
              telegram_id, symbol, name, buy_price, buy_date,
              peak_price, peak_date, kospi_at_peak, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, symbol)
            DO UPDATE SET
              name = excluded.name,
              buy_price = excluded.buy_price,
              buy_date = excluded.buy_date,
              peak_price = excluded.peak_price,
              peak_date = excluded.peak_date,
              kospi_at_peak = excluded.kospi_at_peak,
              updated_at = excluded.updated_at
            """,
            (
                telegram_id,
                symbol,
                name,
                buy_price,
                buy_date,
                peak_price,
                peak_date,
                kospi_at_peak,
                now,
                now,
            ),
        )
        self.conn.commit()

    def delete_holding(self, telegram_id: str, symbol: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM holdings WHERE telegram_id = ? AND symbol = ?",
            (telegram_id, symbol),
        )
        self.conn.commit()
        return cur.rowcount

    def get_holding(self, telegram_id: str, symbol: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM holdings WHERE telegram_id = ? AND symbol = ?",
            (telegram_id, symbol),
        ).fetchone()

    def list_holdings(self, telegram_id: str) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM holdings WHERE telegram_id = ? ORDER BY symbol",
            (telegram_id,),
        ).fetchall()
        return list(rows)

    def count_holdings(self, telegram_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM holdings WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return int(row[0])

    def list_symbols(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT symbol FROM holdings ORDER BY symbol").fetchall()
        return [r[0] for r in rows]

    def list_holdings_by_symbol(self, symbol: str) -> list[sqlite3.Row]:
        rows = self.conn.execute("SELECT * FROM holdings WHERE symbol = ?", (symbol,)).fetchall()
        return list(rows)

    def update_peak(
        self,
        holding_id: int,
        peak_price: float,
        peak_date: str,
        kospi_at_peak: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE holdings
            SET peak_price = ?, peak_date = ?, kospi_at_peak = ?, updated_at = ?
            WHERE id = ?
            """,
            (peak_price, peak_date, kospi_at_peak, self.now_iso(), holding_id),
        )
        self.conn.commit()

    def upsert_price(self, trading_date: str, symbol: str, close: float) -> None:
        self.conn.execute(
            """
            INSERT INTO price_cache (trading_date, symbol, close)
            VALUES (?, ?, ?)
            ON CONFLICT(trading_date, symbol) DO UPDATE SET close = excluded.close
            """,
            (trading_date, symbol, close),
        )
        self.conn.commit()

    def get_price(self, trading_date: str, symbol: str) -> float | None:
        row = self.conn.execute(
            "SELECT close FROM price_cache WHERE trading_date = ? AND symbol = ?",
            (trading_date, symbol),
        ).fetchone()
        return float(row[0]) if row else None

    def get_latest_price_before(self, trading_date: str, symbol: str) -> tuple[str, float] | None:
        row = self.conn.execute(
            """
            SELECT trading_date, close
            FROM price_cache
            WHERE symbol = ? AND trading_date <= ?
            ORDER BY trading_date DESC
            LIMIT 1
            """,
            (symbol, trading_date),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), float(row[1])

    def get_latest_price(self, symbol: str) -> tuple[str, float] | None:
        row = self.conn.execute(
            """
            SELECT trading_date, close
            FROM price_cache
            WHERE symbol = ?
            ORDER BY trading_date DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), float(row[1])

    def insert_notification(
        self, trading_date: str, telegram_id: str, symbol: str, ntype: str
    ) -> bool:
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO notifications (trading_date, telegram_id, symbol, type)
            VALUES (?, ?, ?, ?)
            """,
            (trading_date, telegram_id, symbol, ntype),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def has_notification(
        self, trading_date: str, telegram_id: str, symbol: str, ntype: str
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM notifications
            WHERE trading_date = ? AND telegram_id = ? AND symbol = ? AND type = ?
            """,
            (trading_date, telegram_id, symbol, ntype),
        ).fetchone()
        return row is not None

    def count_notifications(self, trading_date: str, telegram_id: str, ntype: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) FROM notifications
            WHERE trading_date = ? AND telegram_id = ? AND type = ?
            """,
            (trading_date, telegram_id, ntype),
        ).fetchone()
        return int(row[0])

    def set_job_status(self, trading_date: str) -> None:
        self.conn.execute(
            """
            INSERT INTO job_status (id, trading_date, completed_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              trading_date = excluded.trading_date,
              completed_at = excluded.completed_at
            """,
            (trading_date, self.now_iso()),
        )
        self.conn.commit()

    def get_job_status(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM job_status WHERE id = 1").fetchone()
        return dict(row) if row else None

    def upsert_trading_session(self, date_str: str, is_session: int = 1) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO trading_calendar (date, is_session)
            VALUES (?, ?)
            """,
            (date_str, int(is_session)),
        )
        self.conn.commit()

    def upsert_trading_sessions(self, date_list: list[str]) -> None:
        if not date_list:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO trading_calendar (date, is_session)
            VALUES (?, 1)
            """,
            [(d,) for d in date_list],
        )
        self.conn.commit()

    def is_trading_day(self, date_str: str) -> bool:
        row = self.conn.execute(
            "SELECT is_session FROM trading_calendar WHERE date = ?",
            (date_str,),
        ).fetchone()
        return bool(row and int(row[0]) == 1)

    def latest_trading_calendar_date(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(date) FROM trading_calendar WHERE is_session = 1"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return str(row[0])
