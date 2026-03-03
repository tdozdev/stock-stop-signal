from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import exchange_calendars as xcals
import pandas as pd

from .config import KST
from .db import Database

XKRX = xcals.get_calendar("XKRX")


@dataclass(frozen=True, slots=True)
class CalendarSettings:
    past_days: int = 365
    future_days: int = 730
    refill_threshold_days: int = 90


def _to_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def ensure_calendar_seeded(db: Database, start_date: date | str, end_date: date | str) -> None:
    start = _to_date(start_date)
    end = _to_date(end_date)
    if start > end:
        start, end = end, start

    first_session = XKRX.first_session.date()
    last_session = XKRX.last_session.date()
    if end < first_session or start > last_session:
        return

    start = max(start, first_session)
    end = min(end, last_session)

    sessions = XKRX.sessions_in_range(start.isoformat(), end.isoformat())
    session_dates = [ts.date().isoformat() for ts in sessions]
    db.upsert_trading_sessions(session_dates)


def seed_for_window(db: Database, base_day: date, settings: CalendarSettings) -> None:
    ensure_calendar_seeded(
        db,
        base_day - timedelta(days=settings.past_days),
        base_day + timedelta(days=settings.future_days),
    )


def ensure_calendar_fresh(db: Database, today: date, settings: CalendarSettings) -> None:
    latest = db.latest_trading_calendar_date()
    if latest is None:
        seed_for_window(db, today, settings)
        return

    latest_dt = date.fromisoformat(latest)
    threshold = today + timedelta(days=settings.refill_threshold_days)
    if latest_dt < threshold:
        seed_for_window(db, today, settings)


def is_trading_day(db: Database, day: date | str) -> bool:
    return db.is_trading_day(_to_date(day).isoformat())


def previous_trading_day(day: date | str) -> date:
    ref = pd.Timestamp(_to_date(day))
    prev = XKRX.previous_session(ref)
    return prev.date()


def latest_close_trading_day(today: date | str) -> date:
    today_date = _to_date(today)
    sessions = XKRX.sessions_in_range(
        (today_date - timedelta(days=366)).isoformat(),
        today_date.isoformat(),
    )
    if len(sessions) == 0:
        raise RuntimeError("거래일 캘린더에서 기준일을 찾을 수 없습니다.")

    last = sessions[-1].date()
    if last < today_date:
        return last
    if len(sessions) < 2:
        raise RuntimeError("직전 거래일을 찾을 수 없습니다.")
    return sessions[-2].date()


def effective_date_for_run(today_kst: datetime, db: Database) -> date | None:
    today = today_kst.astimezone(KST).date()
    if not is_trading_day(db, today):
        return None
    return previous_trading_day(today)
