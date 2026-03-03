from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd
from pykrx import stock
import requests
import time
from exchange_calendars import get_calendar

from .config import KST

XKRX = get_calendar("XKRX")


class MarketDataProvider(Protocol):
    def get_previous_trading_date(self, base_dt: datetime | None = None) -> str: ...

    def get_symbol_close(self, symbol: str, trading_date: str) -> float: ...

    def get_symbol_name(self, symbol: str) -> str: ...

    def get_kospi_close(self, trading_date: str) -> float: ...

    def get_peak_since(self, symbol: str, from_date: str, to_date: str) -> tuple[float, str]: ...


class PykrxMarketData:
    KOSPI_INDEX = "1001"

    def get_previous_trading_date(self, base_dt: datetime | None = None) -> str:
        now = base_dt or datetime.now(tz=KST)
        probe = now.date() - timedelta(days=1)
        for _ in range(14):
            iso = probe.isoformat()
            if self._has_kospi_close(iso):
                return iso
            probe -= timedelta(days=1)
        raise RuntimeError("최근 거래일을 찾을 수 없습니다.")

    def get_symbol_close(self, symbol: str, trading_date: str) -> float:
        ymd = trading_date.replace("-", "")
        df = self._with_retry(lambda: self._get_symbol_ohlcv_by_date(symbol, ymd, ymd))
        if df.empty:
            raise RuntimeError(f"{symbol} {trading_date} 종가를 찾을 수 없습니다.")
        return float(df.iloc[-1]["종가"])

    def get_symbol_name(self, symbol: str) -> str:
        try:
            raw = stock.get_market_ticker_name(symbol)
        except Exception:
            raw = None
        market_name = self._extract_name(raw)
        if market_name:
            return market_name

        try:
            etf_raw = stock.get_etf_ticker_name(symbol)
        except Exception:
            etf_raw = None
        etf_name = self._extract_name(etf_raw)
        if etf_name:
            return etf_name

        raise RuntimeError(f"종목코드가 유효하지 않습니다: {symbol}")

    def get_kospi_close(self, trading_date: str) -> float:
        ymd = trading_date.replace("-", "")
        df = self._with_retry(lambda: stock.get_index_ohlcv_by_date(ymd, ymd, self.KOSPI_INDEX))
        if df.empty:
            raise RuntimeError(f"KOSPI {trading_date} 종가를 찾을 수 없습니다.")
        return float(df.iloc[-1]["종가"])

    def _has_kospi_close(self, trading_date: str) -> bool:
        ymd = trading_date.replace("-", "")
        df = stock.get_index_ohlcv_by_date(ymd, ymd, self.KOSPI_INDEX)
        return not df.empty

    def get_peak_since(self, symbol: str, from_date: str, to_date: str) -> tuple[float, str]:
        from_ymd = from_date.replace("-", "")
        to_ymd = to_date.replace("-", "")
        df = self._with_retry(lambda: self._get_symbol_ohlcv_by_date(symbol, from_ymd, to_ymd))
        if df.empty:
            raise RuntimeError(f"{symbol} {from_date}~{to_date} 구간 데이터가 없습니다.")
        peak_idx = df["종가"].idxmax()
        peak_price = float(df.loc[peak_idx]["종가"])
        peak_date = peak_idx.date().isoformat()
        return peak_price, peak_date

    def _get_symbol_ohlcv_by_date(self, symbol: str, from_ymd: str, to_ymd: str) -> pd.DataFrame:
        df = stock.get_market_ohlcv_by_date(from_ymd, to_ymd, symbol)
        if not df.empty:
            return df
        return stock.get_etf_ohlcv_by_date(from_ymd, to_ymd, symbol)

    def _extract_name(self, raw: object) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            name = raw.strip()
            if name and name.lower() != "none":
                return name
            return ""

        # pykrx 버전에 따라 Series/DataFrame이 반환될 수 있다.
        if isinstance(raw, pd.Series):
            if raw.empty:
                return ""
            return str(raw.iloc[0]).strip()

        if isinstance(raw, pd.DataFrame):
            if raw.empty:
                return ""
            first_row = raw.iloc[0]
            if "종목명" in raw.columns:
                return str(first_row["종목명"]).strip()
            return str(first_row.iloc[0]).strip()

        name = str(raw).strip()
        if name and name.lower() != "none":
            return name
        return ""

    def _with_retry(self, fn, retries: int = 2, delay_sec: float = 0.7):
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(delay_sec)
                    continue
                break
        raise RuntimeError("KRX 데이터 조회가 일시적으로 실패했습니다. 잠시 후 다시 시도해주세요.") from last_exc


def normalize_symbol(raw: str) -> str:
    stripped = raw.strip()
    if len(stripped) != 6 or not stripped.isdigit():
        raise ValueError("종목코드는 6자리 숫자여야 합니다.")
    return stripped


def iso_date(d: date) -> str:
    return d.isoformat()


class KrxApiMarketData:
    """KRX 공식 API(일별 전종목/지수) 기반 market provider."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        kospi_daily_path: str,
        kosdaq_daily_path: str,
        etf_daily_path: str,
        kospi_index_daily_path: str,
        date_param: str = "BAS_DD",
        timeout_sec: float = 10.0,
        retries: int = 2,
        retry_delay_sec: float = 0.7,
    ) -> None:
        if not base_url:
            raise ValueError("SSS_KRX_API_BASE_URL이 필요합니다.")
        if not kospi_daily_path or not kosdaq_daily_path or not kospi_index_daily_path:
            raise ValueError("KRX API path(코스피/코스닥/지수) 3개가 필요합니다.")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.kospi_daily_path = kospi_daily_path
        self.kosdaq_daily_path = kosdaq_daily_path
        self.etf_daily_path = etf_daily_path
        self.kospi_index_daily_path = kospi_index_daily_path
        self.date_param = date_param
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.retry_delay_sec = retry_delay_sec
        self.session = requests.Session()

        self._stock_cache: dict[str, dict[str, tuple[str, float]]] = {}
        self._index_cache: dict[str, float] = {}
        self._name_cache: dict[str, str] = {}

    def get_previous_trading_date(self, base_dt: datetime | None = None) -> str:
        now = base_dt or datetime.now(tz=KST)
        return XKRX.previous_session(pd.Timestamp(now.date())).date().isoformat()

    def get_symbol_close(self, symbol: str, trading_date: str) -> float:
        symbol = normalize_symbol(symbol)
        daily = self._load_stock_daily(trading_date)
        if symbol not in daily:
            raise RuntimeError(f"{symbol} {trading_date} 종가를 찾을 수 없습니다.")
        return float(daily[symbol][1])

    def get_symbol_name(self, symbol: str) -> str:
        symbol = normalize_symbol(symbol)
        cached = self._name_cache.get(symbol)
        if cached:
            return cached

        today = datetime.now(tz=KST).date()
        sessions = XKRX.sessions_in_range(
            (today - timedelta(days=40)).isoformat(),
            today.isoformat(),
        )
        for ts in reversed(list(sessions)):
            iso = ts.date().isoformat()
            try:
                daily = self._load_stock_daily(iso)
            except Exception:
                continue
            if symbol in daily:
                return daily[symbol][0]
        raise RuntimeError(f"종목코드가 유효하지 않습니다: {symbol}")

    def get_kospi_close(self, trading_date: str) -> float:
        if trading_date in self._index_cache:
            return self._index_cache[trading_date]
        rows = self._fetch_outblock_1(self.kospi_index_daily_path, trading_date)
        close = self._pick_kospi_index_close(rows)
        self._index_cache[trading_date] = close
        return close

    def get_peak_since(self, symbol: str, from_date: str, to_date: str) -> tuple[float, str]:
        symbol = normalize_symbol(symbol)
        sessions = XKRX.sessions_in_range(from_date, to_date)
        best_price: float | None = None
        best_date: str | None = None

        for ts in sessions:
            day = ts.date().isoformat()
            daily = self._load_stock_daily(day)
            row = daily.get(symbol)
            if row is None:
                continue
            _, close = row
            if best_price is None or close > best_price:
                best_price = close
                best_date = day

        if best_price is None or best_date is None:
            raise RuntimeError(f"{symbol} {from_date}~{to_date} 구간 데이터가 없습니다.")
        return best_price, best_date

    def _load_stock_daily(self, trading_date: str) -> dict[str, tuple[str, float]]:
        if trading_date in self._stock_cache:
            return self._stock_cache[trading_date]

        rows = []
        rows.extend(self._fetch_outblock_1(self.kospi_daily_path, trading_date))
        rows.extend(self._fetch_outblock_1(self.kosdaq_daily_path, trading_date))
        if self.etf_daily_path:
            rows.extend(self._fetch_outblock_optional(self.etf_daily_path, trading_date))

        out: dict[str, tuple[str, float]] = {}
        for r in rows:
            raw_symbol = str(r.get("ISU_CD", "")).strip()
            symbol = self._coerce_symbol(raw_symbol)
            if not symbol:
                continue
            name = str(r.get("ISU_NM", "")).strip()
            close = self._to_float(r.get("TDD_CLSPRC"))
            if close is None:
                continue
            out[symbol] = (name or symbol, close)
            if name:
                self._name_cache[symbol] = name

        self._stock_cache[trading_date] = out
        return out

    def _fetch_outblock_1(self, path: str, trading_date: str) -> list[dict]:
        params = {self.date_param: trading_date.replace("-", "")}
        url = f"{self.base_url}/{path.lstrip('/')}"

        def _call() -> list[dict]:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["AUTH_KEY"] = self.api_key
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout_sec)
            if resp.status_code in {401, 403}:
                raise RuntimeError(
                    f"KRX API 인증에 실패했습니다. AUTH_KEY 또는 권한을 확인해주세요. (status={resp.status_code})"
                )
            if resp.status_code == 404:
                raise RuntimeError(
                    f"KRX API 경로를 찾을 수 없습니다. base_url/path 설정을 확인해주세요. ({url})"
                )
            if resp.status_code >= 400:
                raise RuntimeError(f"KRX API 호출 실패(status={resp.status_code}): {url}")

            try:
                payload = resp.json()
            except Exception as exc:
                raise RuntimeError("KRX API 응답이 JSON 형식이 아닙니다.") from exc
            rows = payload.get("OutBlock_1")
            if isinstance(rows, list):
                return rows
            raise RuntimeError("KRX API 응답 형식이 올바르지 않습니다.")

        return self._with_retry(_call)

    def _fetch_outblock_optional(self, path: str, trading_date: str) -> list[dict]:
        try:
            return self._fetch_outblock_1(path, trading_date)
        except Exception as exc:
            msg = str(exc)
            if "인증에 실패" in msg or "경로를 찾을 수 없습니다" in msg:
                return []
            raise

    def _pick_kospi_index_close(self, rows: list[dict]) -> float:
        preferred = ("코스피", "코스피지수", "KOSPI")

        for name in preferred:
            for r in rows:
                if str(r.get("IDX_NM", "")).strip() == name:
                    value = self._to_float(r.get("CLSPRC_IDX"))
                    if value is not None:
                        return value

        for r in rows:
            if str(r.get("IDX_CLSS", "")).strip().upper() != "KOSPI":
                continue
            idx_name = str(r.get("IDX_NM", "")).strip()
            if "코스피" not in idx_name and "KOSPI" not in idx_name.upper():
                continue
            value = self._to_float(r.get("CLSPRC_IDX"))
            if value is not None:
                return value

        raise RuntimeError("KOSPI 지수 종가를 찾을 수 없습니다.")

    def _coerce_symbol(self, raw: str) -> str:
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) < 6:
            return ""
        return digits[-6:]

    def _to_float(self, value: object) -> float | None:
        if value is None:
            return None
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _with_retry(self, fn):
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                if "인증에 실패" in msg or "경로를 찾을 수 없습니다" in msg:
                    raise RuntimeError(msg) from exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay_sec)
                    continue
                break
        if last_exc is not None:
            raise RuntimeError(f"KRX 데이터 조회가 일시적으로 실패했습니다: {last_exc}") from last_exc
        raise RuntimeError("KRX 데이터 조회가 일시적으로 실패했습니다. 잠시 후 다시 시도해주세요.")
