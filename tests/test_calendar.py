from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta

from sss.calendar import (
    XKRX,
    CalendarSettings,
    effective_date_for_run,
    ensure_calendar_seeded,
    is_trading_day,
)
from sss.config import KST
from sss.db import Database
from sss.service import DailyBatchRunner


class NoopNotifier:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, chat_id: str, text: str, reply_markup=None) -> None:
        self.calls += 1


class StrictMarket:
    def __init__(self) -> None:
        self.calls = 0

    def _touch(self) -> None:
        self.calls += 1

    def get_previous_trading_date(self, base_dt=None) -> str:
        self._touch()
        return "2026-01-01"

    def get_symbol_close(self, symbol: str, trading_date: str) -> float:
        self._touch()
        return 0.0

    def get_symbol_name(self, symbol: str) -> str:
        self._touch()
        return "X"

    def get_kospi_close(self, trading_date: str) -> float:
        self._touch()
        return 0.0

    def get_peak_since(self, symbol: str, from_date: str, to_date: str):
        self._touch()
        return 0.0, from_date


def _count_rows(db: Database, table: str) -> int:
    row = db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def test_trading_calendar_seeding_inserts_rows(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()

    ensure_calendar_seeded(db, "2026-03-01", "2026-03-31")

    assert _count_rows(db, "trading_calendar") > 0


def test_is_trading_day_weekend_false(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    ensure_calendar_seeded(db, "2026-03-01", "2026-03-31")

    assert is_trading_day(db, "2026-03-07") is False  # Saturday
    assert is_trading_day(db, "2026-03-08") is False  # Sunday


def test_effective_date_for_run_monday_and_saturday(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    ensure_calendar_seeded(db, "2026-01-01", "2026-12-31")

    sessions = XKRX.sessions_in_range("2026-01-01", "2026-12-31")
    monday = None
    friday = None
    for ts in sessions:
        d = ts.date()
        if d.weekday() != 0:
            continue
        prev = XKRX.previous_session(ts).date()
        if prev.weekday() == 4 and (d - prev).days == 3:
            monday = d
            friday = prev
            break

    assert monday is not None and friday is not None

    monday_dt = datetime.combine(monday, time(8, 10), tzinfo=KST)
    saturday_dt = datetime.combine(monday + timedelta(days=5), time(8, 10), tzinfo=KST)

    assert effective_date_for_run(monday_dt, db) == friday
    assert effective_date_for_run(saturday_dt, db) is None


def test_batch_skips_on_non_trading_day(tmp_path, monkeypatch) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    ensure_calendar_seeded(db, "2026-01-01", "2026-12-31")

    weekend_now = datetime(2026, 3, 7, 8, 10, tzinfo=KST)  # Saturday

    from sss import service as service_module

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return weekend_now.replace(tzinfo=None)
            return weekend_now.astimezone(tz)

    monkeypatch.setattr(service_module, "datetime", FixedDateTime)

    market = StrictMarket()
    notifier = NoopNotifier()
    runner = DailyBatchRunner(db, market, notifier, CalendarSettings())

    asyncio.run(runner.run())

    assert market.calls == 0
    assert notifier.calls == 0
    assert _count_rows(db, "price_cache") == 0
    assert _count_rows(db, "notifications") == 0
