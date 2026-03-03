from __future__ import annotations

from datetime import datetime

from .config import KST


def today_kst() -> datetime:
    return datetime.now(tz=KST)


def today_kst_date_str() -> str:
    return today_kst().date().isoformat()


def parse_yyyymmdd_to_iso(raw: str) -> str:
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError("날짜는 YYYYMMDD 형식이어야 합니다.")

    try:
        dt = datetime.strptime(raw, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("유효하지 않은 날짜입니다.") from exc

    return dt.date().isoformat()
