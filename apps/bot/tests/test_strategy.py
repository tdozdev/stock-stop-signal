from sss.strategy import absolute_drop_pct, relative_drop_pct


def test_relative_drop_pct() -> None:
    value = relative_drop_pct(
        peak_price=82000,
        current_close=71000,
        kospi_at_peak=2800,
        current_kospi_close=2700,
    )
    assert round(value, 2) == 9.84


def test_absolute_drop_pct() -> None:
    value = absolute_drop_pct(
        buy_price=70000,
        current_close=62000,
    )
    assert round(value, 2) == 11.43
