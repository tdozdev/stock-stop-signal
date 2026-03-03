import pandas as pd

from sss.market import PykrxMarketData


class DummyStock:
    @staticmethod
    def get_market_ticker_name(symbol: str):
        return pd.DataFrame([{"종목명": "테스트ETF"}], index=[symbol])


def test_get_symbol_name_handles_dataframe(monkeypatch):
    from sss import market as market_module

    monkeypatch.setattr(market_module, "stock", DummyStock)
    md = PykrxMarketData()

    assert md.get_symbol_name("232080") == "테스트ETF"


class DummyStockEtfFallback:
    @staticmethod
    def get_market_ticker_name(symbol: str):
        return None

    @staticmethod
    def get_etf_ticker_name(symbol: str):
        return "TIGER 코스닥150"


def test_get_symbol_name_fallback_to_etf(monkeypatch):
    from sss import market as market_module

    monkeypatch.setattr(market_module, "stock", DummyStockEtfFallback)
    md = PykrxMarketData()

    assert md.get_symbol_name("232080") == "TIGER 코스닥150"


class DummyStockEtfFallbackFromEmptyDf:
    @staticmethod
    def get_market_ticker_name(symbol: str):
        return pd.DataFrame()

    @staticmethod
    def get_etf_ticker_name(symbol: str):
        return "TIGER 코스닥150"


def test_get_symbol_name_fallback_to_etf_from_empty_dataframe(monkeypatch):
    from sss import market as market_module

    monkeypatch.setattr(market_module, "stock", DummyStockEtfFallbackFromEmptyDf)
    md = PykrxMarketData()

    assert md.get_symbol_name("232080") == "TIGER 코스닥150"
