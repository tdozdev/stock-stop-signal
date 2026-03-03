import pytest

from sss.date_utils import parse_yyyymmdd_to_iso


def test_parse_yyyymmdd_to_iso_ok() -> None:
    assert parse_yyyymmdd_to_iso("20250115") == "2025-01-15"


def test_parse_yyyymmdd_to_iso_invalid() -> None:
    with pytest.raises(ValueError):
        parse_yyyymmdd_to_iso("2025-01-15")
