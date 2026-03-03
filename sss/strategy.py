from __future__ import annotations


def relative_drop_pct(
    peak_price: float,
    current_close: float,
    kospi_at_peak: float,
    current_kospi_close: float,
) -> float:
    if peak_price <= 0 or kospi_at_peak <= 0:
        raise ValueError("peak_price and kospi_at_peak must be positive")

    stock_drop = (peak_price - current_close) / peak_price * 100.0
    market_drop = (kospi_at_peak - current_kospi_close) / kospi_at_peak * 100.0
    return stock_drop - market_drop


def absolute_drop_pct(buy_price: float, current_close: float) -> float:
    if buy_price <= 0:
        raise ValueError("buy_price must be positive")
    return (buy_price - current_close) / buy_price * 100.0
