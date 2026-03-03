from __future__ import annotations

from dataclasses import dataclass

from sss.db import Database
from sss.service import SSSService


@dataclass
class FakeMarket:
    prev_date: str = "2025-02-03"

    def get_previous_trading_date(self, base_dt=None) -> str:
        return self.prev_date

    def get_symbol_close(self, symbol: str, trading_date: str) -> float:
        return 75000.0

    def get_symbol_name(self, symbol: str) -> str:
        return "삼성전자"

    def get_kospi_close(self, trading_date: str) -> float:
        if trading_date == "2025-02-03":
            return 2800.0
        return 2700.0

    def get_peak_since(self, symbol: str, from_date: str, to_date: str):
        assert from_date == "2025-01-15"
        assert to_date == "2025-02-03"
        return 82000.0, "2025-02-03"


def test_buy_date_peak_calculation(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    service = SSSService(db, FakeMarket())

    service.upsert_holding("u1", "005930", 70000.0, "2025-01-15")

    h = db.get_holding("u1", "005930")
    assert h is not None
    assert float(h["peak_price"]) == 82000.0
    assert h["peak_date"] == "2025-02-03"
    assert float(h["kospi_at_peak"]) == 2800.0


def test_daily_on_off_logic(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()
    service = SSSService(db, FakeMarket())

    service.ensure_user("u1")
    service.set_daily("u1", True)
    assert int(db.get_user("u1")["daily_report"]) == 1

    service.set_daily("u1", False)
    assert int(db.get_user("u1")["daily_report"]) == 0


def test_notification_dedup(tmp_path) -> None:
    db = Database(str(tmp_path / "t.db"))
    db.init_schema()

    inserted_1 = db.insert_notification("2026-02-25", "u1", "005930", "trigger")
    inserted_2 = db.insert_notification("2026-02-25", "u1", "005930", "trigger")

    assert inserted_1 is True
    assert inserted_2 is False
