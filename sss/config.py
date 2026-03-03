from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


@dataclass(slots=True)
class Settings:
    telegram_token: str
    db_path: str = "./sss.db"
    schedule_hour: int = 8
    schedule_minute: int = 10
    market_provider: str = "pykrx"
    krx_api_base_url: str = "https://data-dbg.krx.co.kr"
    krx_api_key: str = ""
    krx_kospi_daily_path: str = "/svc/apis/sto/stk_bydd_trd"
    krx_kosdaq_daily_path: str = "/svc/apis/sto/ksq_bydd_trd"
    krx_etf_daily_path: str = "/svc/apis/etp/etf_bydd_trd"
    krx_kospi_index_daily_path: str = "/svc/apis/idx/kospi_dd_trd"
    krx_date_param: str = "basDd"
    krx_timeout_sec: float = 10.0
    calendar_past_days: int = 365
    calendar_future_days: int = 730
    calendar_refill_threshold_days: int = 90

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        db_path = os.getenv("SSS_DB_PATH", "./sss.db")
        hour = int(os.getenv("SSS_SCHEDULE_HOUR", "8"))
        minute = int(os.getenv("SSS_SCHEDULE_MINUTE", "10"))
        default_provider = "krx_api" if os.getenv("SSS_KRX_API_KEY") else "pykrx"
        market_provider = os.getenv("SSS_MARKET_PROVIDER", default_provider)
        krx_api_base_url = os.getenv("SSS_KRX_API_BASE_URL", "https://data-dbg.krx.co.kr").rstrip("/")
        krx_api_key = os.getenv("SSS_KRX_API_KEY", "")
        krx_kospi_daily_path = os.getenv("SSS_KRX_KOSPI_DAILY_PATH", "/svc/apis/sto/stk_bydd_trd")
        krx_kosdaq_daily_path = os.getenv("SSS_KRX_KOSDAQ_DAILY_PATH", "/svc/apis/sto/ksq_bydd_trd")
        krx_etf_daily_path = os.getenv("SSS_KRX_ETF_DAILY_PATH", "/svc/apis/etp/etf_bydd_trd")
        krx_kospi_index_daily_path = os.getenv(
            "SSS_KRX_KOSPI_INDEX_DAILY_PATH",
            "/svc/apis/idx/kospi_dd_trd",
        )
        krx_date_param = os.getenv("SSS_KRX_DATE_PARAM", "basDd")
        krx_timeout_sec = float(os.getenv("SSS_KRX_TIMEOUT_SEC", "10"))
        cal_past = int(os.getenv("SSS_CALENDAR_PAST_DAYS", "365"))
        cal_future = int(os.getenv("SSS_CALENDAR_FUTURE_DAYS", "730"))
        cal_refill = int(os.getenv("SSS_CALENDAR_REFILL_THRESHOLD_DAYS", "90"))
        return cls(
            telegram_token=token,
            db_path=db_path,
            schedule_hour=hour,
            schedule_minute=minute,
            market_provider=market_provider,
            krx_api_base_url=krx_api_base_url,
            krx_api_key=krx_api_key,
            krx_kospi_daily_path=krx_kospi_daily_path,
            krx_kosdaq_daily_path=krx_kosdaq_daily_path,
            krx_etf_daily_path=krx_etf_daily_path,
            krx_kospi_index_daily_path=krx_kospi_index_daily_path,
            krx_date_param=krx_date_param,
            krx_timeout_sec=krx_timeout_sec,
            calendar_past_days=cal_past,
            calendar_future_days=cal_future,
            calendar_refill_threshold_days=cal_refill,
        )
