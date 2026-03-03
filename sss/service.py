from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .calendar import (
    CalendarSettings,
    effective_date_for_run,
    ensure_calendar_fresh,
    latest_close_trading_day,
)
from .config import KST
from .date_utils import today_kst_date_str
from .db import Database
from .market import MarketDataProvider, normalize_symbol
from .notifier import TelegramNotifier
from .strategy import absolute_drop_pct, relative_drop_pct

KOSPI_CACHE_SYMBOL = "KS11"


def fmt_price(value: float) -> str:
    return f"{int(round(value)):,}"


def fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def fmt_pl_pct_from_drop(drop_pct: float) -> str:
    # drop 기준(+ 하락, - 상승)을 손익률 기준(+ 수익, - 손실)으로 반전 표기
    return f"{(-drop_pct):+.2f}%"


def signal_status(drop_pct: float, stop_loss_pct: float) -> str:
    if drop_pct < stop_loss_pct:
        return "정상"
    if drop_pct < stop_loss_pct + 5:
        return "⚠ 매도 검토"
    if drop_pct < stop_loss_pct + 10:
        return "🚨 강한 매도 신호"
    return "🔥 리스크 심각"


def combined_drop_for_status(absolute_drop: float, relative_drop: float) -> float:
    return max(absolute_drop, relative_drop)


@dataclass(slots=True)
class HoldingSnapshot:
    symbol: str
    name: str
    buy_price: float
    buy_date: str
    peak_price: float
    peak_date: str
    current_close: float
    absolute_drop: float
    relative_drop: float
    stop_loss_pct: float

    @property
    def triggered(self) -> bool:
        return self.absolute_drop >= self.stop_loss_pct or self.relative_drop >= self.stop_loss_pct


class SSSService:
    def __init__(
        self,
        db: Database,
        market: MarketDataProvider,
        calendar_settings: CalendarSettings | None = None,
    ) -> None:
        self.db = db
        self.market = market
        self.calendar_settings = calendar_settings or CalendarSettings()
        self.logger = getLogger("sss.service")

    def ensure_user(self, telegram_id: str) -> None:
        self.db.ensure_user(telegram_id)

    def set_stop_loss(self, telegram_id: str, stop_loss_pct: float) -> None:
        if stop_loss_pct <= 0:
            raise ValueError("손절 기준은 0보다 커야 합니다.")
        self.db.set_stop_loss(telegram_id, stop_loss_pct)

    def set_daily(self, telegram_id: str, on: bool) -> None:
        self.db.set_daily_report(telegram_id, on)

    def upsert_holding(
        self,
        telegram_id: str,
        symbol: str,
        buy_price: float,
        buy_date: str,
    ) -> tuple[str, str]:
        if buy_price <= 0:
            raise ValueError("매수가는 0보다 커야 합니다.")
        symbol = normalize_symbol(symbol)
        last_trading = self._reference_trading_date()
        name = self.market.get_symbol_name(symbol)
        existing = self.db.get_holding(telegram_id, symbol)

        if buy_date <= last_trading:
            peak_price, peak_date = self._resolve_peak_with_fallback(
                telegram_id=telegram_id,
                symbol=symbol,
                buy_price=buy_price,
                buy_date=buy_date,
                last_trading=last_trading,
                existing=existing,
            )
            if (
                existing is not None
                and str(existing["buy_date"]) == buy_date
                and str(existing["peak_date"]) == peak_date
            ):
                kospi_at_peak = float(existing["kospi_at_peak"])
            else:
                kospi_at_peak = self._resolve_kospi_at_peak_with_fallback(peak_date, last_trading)
        else:
            peak_price = buy_price
            peak_date = buy_date
            kospi_at_peak = self._get_kospi_close_with_fallback(last_trading)

        self.db.upsert_holding(
            telegram_id=telegram_id,
            symbol=symbol,
            name=name,
            buy_price=buy_price,
            buy_date=buy_date,
            peak_price=peak_price,
            peak_date=peak_date,
            kospi_at_peak=kospi_at_peak,
        )
        return symbol, name

    def delete_holding(self, telegram_id: str, symbol: str) -> tuple[int, str, str]:
        norm_symbol = normalize_symbol(symbol)
        holding = self.db.get_holding(telegram_id, norm_symbol)
        deleted = self.db.delete_holding(telegram_id, norm_symbol)
        if deleted and holding is not None:
            return deleted, norm_symbol, str(holding["name"])
        return deleted, norm_symbol, ""

    def update_buy_price_only(self, telegram_id: str, symbol: str, buy_price: float) -> tuple[str, str]:
        if buy_price <= 0:
            raise ValueError("매수가는 0보다 커야 합니다.")
        norm_symbol = normalize_symbol(symbol)
        holding = self.db.get_holding(telegram_id, norm_symbol)
        if holding is None:
            raise ValueError("기존 종목이 없습니다. /c 명령으로 먼저 추가하세요.")

        self.db.upsert_holding(
            telegram_id=telegram_id,
            symbol=norm_symbol,
            name=str(holding["name"]),
            buy_price=buy_price,
            buy_date=str(holding["buy_date"]),
            peak_price=float(holding["peak_price"]),
            peak_date=str(holding["peak_date"]),
            kospi_at_peak=float(holding["kospi_at_peak"]),
        )
        return norm_symbol, str(holding["name"])

    def get_portfolio_snapshots(
        self, telegram_id: str, trading_date: str | None = None
    ) -> tuple[str, list[HoldingSnapshot], float, bool]:
        self.ensure_user(telegram_id)
        user = self.db.get_user(telegram_id)
        td = trading_date or self._reference_trading_date()
        kospi_close = self._get_kospi_close_with_fallback(td)

        snapshots: list[HoldingSnapshot] = []
        for h in self.db.list_holdings(telegram_id):
            current_close = self.db.get_price(td, h["symbol"])
            if current_close is None:
                try:
                    current_close = self.market.get_symbol_close(h["symbol"], td)
                    self.db.upsert_price(td, h["symbol"], current_close)
                except Exception:
                    cached = self._get_cached_price_with_relaxed_fallback(td, h["symbol"])
                    if cached is None:
                        raise
                    cached_date, current_close = cached
                    self.logger.warning(
                        "Using cached close for %s at %s from %s",
                        h["symbol"],
                        td,
                        cached_date,
                    )

            abs_drop = absolute_drop_pct(
                buy_price=float(h["buy_price"]),
                current_close=float(current_close),
            )
            rel_drop = relative_drop_pct(
                peak_price=float(h["peak_price"]),
                current_close=float(current_close),
                kospi_at_peak=float(h["kospi_at_peak"]),
                current_kospi_close=float(kospi_close),
            )
            snapshots.append(
                HoldingSnapshot(
                    symbol=h["symbol"],
                    name=h["name"],
                    buy_price=float(h["buy_price"]),
                    buy_date=h["buy_date"],
                    peak_price=float(h["peak_price"]),
                    peak_date=h["peak_date"],
                    current_close=float(current_close),
                    absolute_drop=abs_drop,
                    relative_drop=rel_drop,
                    stop_loss_pct=float(user["stop_loss_pct"]),
                )
            )
        return td, snapshots, float(user["stop_loss_pct"]), bool(user["daily_report"])

    def render_portfolio(self, telegram_id: str, symbol: str | None = None) -> str:
        td, snapshots, stop_loss_pct, daily_on = self.get_portfolio_snapshots(telegram_id)
        if symbol is not None:
            symbol = normalize_symbol(symbol)
            snapshots = [s for s in snapshots if s.symbol == symbol]

        if not snapshots:
            return "등록된 종목이 없습니다. /c 005930 70000 20250115 로 추가하세요."

        lines = [
            "<b>📊 SSS 포트폴리오 현황</b>",
            f"발송일: {today_kst_date_str()}",
            f"기준일: {td} (종가 기준)",
            f"매도 기준: {stop_loss_pct:g}%",
            f"Daily 리포트: {'ON' if daily_on else 'OFF'}",
            "────────────────",
            "",
        ]

        for idx, s in enumerate(snapshots):
            status = signal_status(
                combined_drop_for_status(s.absolute_drop, s.relative_drop),
                s.stop_loss_pct,
            )
            lines.extend(
                [
                    f"<b>{html.escape(s.name)} ({s.symbol})</b>",
                    f"매수가: {fmt_price(s.buy_price)}",
                    f"매수일: {s.buy_date}",
                    f"고점: {fmt_price(s.peak_price)} ({s.peak_date})",
                    f"종가: {fmt_price(s.current_close)}",
                    f"매수가대비 손익률: <b>{fmt_pl_pct_from_drop(s.absolute_drop)}</b>",
                    f"시장대비 손익률: <b>{fmt_pl_pct_from_drop(s.relative_drop)}</b>",
                    f"상태: {status}",
                ]
            )
            if idx != len(snapshots) - 1:
                lines.extend(["", "────────────────", ""])
        return "\n".join(lines)

    def render_status(self, telegram_id: str) -> str:
        self.ensure_user(telegram_id)
        user = self.db.get_user(telegram_id)
        status = self.db.get_job_status()
        today = today_kst_date_str()
        done_text = "✘ 오늘 데이터 수집 미완료"
        trading_date = None
        if status:
            trading_date = status["trading_date"]
            if trading_date == self._reference_trading_date():
                done_text = "✔ 오늘 데이터 수집 완료"

        trigger_text = "✔ 손절 트리거 없음"
        try:
            _, snapshots, _, _ = self.get_portfolio_snapshots(telegram_id)
            if any(s.triggered for s in snapshots):
                trigger_text = "⚠ 손절 트리거 발생"
        except Exception:
            # 상태 확인 실패로 전체 응답을 깨지 않도록 트리거 문구는 기본값 유지.
            pass

        lines = [
            f"<b>📊 SSS 상태 확인 ({today})</b>",
            "",
            done_text,
            trigger_text,
            f"매도 기준: {float(user['stop_loss_pct']):g}%",
            f"등록 종목: {self.db.count_holdings(telegram_id)}개",
            f"Daily 리포트: {'ON' if user['daily_report'] else 'OFF'}",
        ]
        return "\n".join(lines)

    def _reference_trading_date(self) -> str:
        try:
            now = datetime.now(tz=KST)
            today = now.date()
            ensure_calendar_fresh(self.db, today, self.calendar_settings)
            return latest_close_trading_day(today).isoformat()
        except Exception:
            # 캘린더 미시드/예외 상황에서는 기존 로직으로 폴백한다.
            return self.market.get_previous_trading_date()

    def _resolve_peak_with_fallback(
        self,
        telegram_id: str,
        symbol: str,
        buy_price: float,
        buy_date: str,
        last_trading: str,
        existing,
    ) -> tuple[float, str]:
        try:
            return self.market.get_peak_since(symbol, buy_date, last_trading)
        except Exception:
            if existing is not None and str(existing["buy_date"]) == buy_date:
                self.logger.warning(
                    "Using existing peak for %s (%s) due to live peak fetch failure.",
                    telegram_id,
                    symbol,
                )
                return float(existing["peak_price"]), str(existing["peak_date"])

            cached = self.db.get_latest_price_before(last_trading, symbol)
            if cached is not None:
                cached_date, cached_close = cached
                if cached_close >= buy_price:
                    self.logger.warning(
                        "Using cached peak for %s from %s (symbol=%s).",
                        telegram_id,
                        cached_date,
                        symbol,
                    )
                    return float(cached_close), cached_date

            self.logger.warning(
                "Fallback to buy price/date as peak for %s (symbol=%s).",
                telegram_id,
                symbol,
            )
            return float(buy_price), buy_date

    def _resolve_kospi_at_peak_with_fallback(self, peak_date: str, last_trading: str) -> float:
        try:
            return self._get_kospi_close_with_fallback(peak_date)
        except Exception:
            if peak_date != last_trading:
                return self._get_kospi_close_with_fallback(last_trading)
            raise

    def _get_kospi_close_with_fallback(self, trading_date: str) -> float:
        try:
            value = self.market.get_kospi_close(trading_date)
            self.db.upsert_price(trading_date, KOSPI_CACHE_SYMBOL, value)
            return value
        except Exception:
            cached = self._get_cached_price_with_relaxed_fallback(trading_date, KOSPI_CACHE_SYMBOL)
            if cached is None:
                raise
            cached_date, value = cached
            self.logger.warning(
                "Using cached KOSPI close at %s from %s",
                trading_date,
                cached_date,
            )
            return value

    def _get_cached_price_with_relaxed_fallback(
        self, trading_date: str, symbol: str
    ) -> tuple[str, float] | None:
        cached = self.db.get_latest_price_before(trading_date, symbol)
        if cached is not None:
            return cached
        return self.db.get_latest_price(symbol)


class DailyBatchRunner:
    def __init__(
        self,
        db: Database,
        market: MarketDataProvider,
        notifier: TelegramNotifier,
        calendar_settings: CalendarSettings,
    ) -> None:
        self.db = db
        self.market = market
        self.notifier = notifier
        self.calendar_settings = calendar_settings
        self.logger = getLogger("sss.batch")

    async def run(self) -> None:
        now_kst = datetime.now(tz=KST)
        today = now_kst.date()
        send_date = today.isoformat()
        ensure_calendar_fresh(self.db, today, self.calendar_settings)

        effective_date = effective_date_for_run(now_kst, self.db)
        if effective_date is None:
            self.logger.info("Skip batch: %s is not a KRX trading day.", send_date)
            return

        trading_date = effective_date.isoformat()
        symbols = self.db.list_symbols()
        kospi_close = self._get_kospi_close_with_fallback(trading_date)

        for symbol in symbols:
            close = self._get_symbol_close_with_fallback(symbol, trading_date)
            if close is None:
                self.logger.warning("Skip symbol %s: no live/cached close for %s", symbol, trading_date)
                continue
            self.db.upsert_price(trading_date, symbol, close)

        for symbol in symbols:
            close = self.db.get_price(trading_date, symbol)
            if close is None:
                continue
            for h in self.db.list_holdings_by_symbol(symbol):
                if float(close) > float(h["peak_price"]):
                    self.db.update_peak(h["id"], float(close), trading_date, float(kospi_close))

        users = self.db.list_users()
        for user in users:
            telegram_id = user["telegram_id"]
            holdings = self.db.list_holdings(telegram_id)
            triggered: list[dict[str, str | float]] = []
            for h in holdings:
                close = self.db.get_price(trading_date, h["symbol"])
                if close is None:
                    continue
                abs_drop = absolute_drop_pct(
                    buy_price=float(h["buy_price"]),
                    current_close=float(close),
                )
                rel_drop = relative_drop_pct(
                    peak_price=float(h["peak_price"]),
                    current_close=float(close),
                    kospi_at_peak=float(h["kospi_at_peak"]),
                    current_kospi_close=float(kospi_close),
                )
                if abs_drop >= float(user["stop_loss_pct"]) or rel_drop >= float(user["stop_loss_pct"]):
                    inserted = self.db.insert_notification(
                        trading_date,
                        telegram_id,
                        h["symbol"],
                        "trigger",
                    )
                    if inserted:
                        triggered.append(
                            {
                                "name": h["name"],
                                "symbol": h["symbol"],
                                "absolute_drop": abs_drop,
                                "relative_drop": rel_drop,
                                "peak_price": float(h["peak_price"]),
                                "peak_date": h["peak_date"],
                                "current_close": float(close),
                            }
                        )

            if triggered and self.db.insert_notification(
                trading_date, telegram_id, "__ALL__", "summary"
            ):
                await self.notifier.send_message(
                    telegram_id,
                    self.render_trigger_summary(
                        send_date,
                        trading_date,
                        float(user["stop_loss_pct"]),
                        triggered,
                    ),
                    reply_markup=self.build_trigger_actions(triggered),
                )

            if (
                int(user["daily_report"]) == 1
                and not triggered
                and self.db.insert_notification(trading_date, telegram_id, "__ALL__", "daily_report")
            ):
                await self.notifier.send_message(
                    telegram_id,
                    self.render_daily_report(
                        send_date,
                        trading_date,
                        float(user["stop_loss_pct"]),
                        len(holdings),
                    ),
                )

        self.db.set_job_status(trading_date)

    def render_trigger_summary(
        self,
        send_date: str,
        trading_date: str,
        stop_loss_pct: float,
        items: list[dict[str, str | float]],
    ) -> str:
        lines = [
            "<b>🚨 매도 고민 알림</b>",
            f"발송일: {send_date}",
            f"기준일: {trading_date} (종가 기준)",
            "",
            "다음 종목은 매도를 고민해보세요:",
            "",
        ]
        for it in items:
            status = signal_status(
                combined_drop_for_status(float(it["absolute_drop"]), float(it["relative_drop"])),
                stop_loss_pct,
            )
            lines.extend(
                [
                    f"• <b>{html.escape(str(it['name']))} ({it['symbol']})</b>",
                    f"  매수가대비 손익률: <b>{fmt_pl_pct_from_drop(float(it['absolute_drop']))}</b>",
                    f"  시장대비 손익률: <b>{fmt_pl_pct_from_drop(float(it['relative_drop']))}</b>",
                    f"  상태: {status}",
                    f"  고점: {fmt_price(float(it['peak_price']))} ({it['peak_date']})",
                    f"  종가: {fmt_price(float(it['current_close']))}",
                    "",
                ]
            )
        lines.extend([f"설정 매도 기준: {stop_loss_pct:g}%", "", "────────────────"])
        return "\n".join(lines)

    def render_daily_report(
        self, send_date: str, trading_date: str, stop_loss_pct: float, count: int
    ) -> str:
        return "\n".join(
            [
                "<b>📊 SSS 일일 리포트</b>",
                f"발송일: {send_date}",
                f"기준일: {trading_date} (종가 기준)",
                "",
                "오늘 매도 고민 종목은 없습니다.",
                "안정적인 흐름입니다.",
                "",
                f"매도 기준: {stop_loss_pct:g}%",
                f"등록 종목: {count}개",
            ]
        )

    def build_trigger_actions(self, items: list[dict[str, str | float]]) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        seen: set[str] = set()
        for it in items:
            symbol = str(it["symbol"])
            if symbol in seen:
                continue
            seen.add(symbol)
            rows.append([InlineKeyboardButton(f"매도완료 {symbol}", callback_data=f"sell:{symbol}")])
        rows.append([InlineKeyboardButton("매도보류", callback_data="hold_alert")])
        return InlineKeyboardMarkup(rows)

    def _get_symbol_close_with_fallback(self, symbol: str, trading_date: str) -> float | None:
        try:
            return self.market.get_symbol_close(symbol, trading_date)
        except Exception:
            cached = self._get_cached_price_with_relaxed_fallback(trading_date, symbol)
            if cached is None:
                return None
            cached_date, value = cached
            self.logger.warning(
                "Using cached close for %s at %s from %s",
                symbol,
                trading_date,
                cached_date,
            )
            return value

    def _get_kospi_close_with_fallback(self, trading_date: str) -> float:
        try:
            value = self.market.get_kospi_close(trading_date)
            self.db.upsert_price(trading_date, KOSPI_CACHE_SYMBOL, value)
            return value
        except Exception:
            cached = self._get_cached_price_with_relaxed_fallback(trading_date, KOSPI_CACHE_SYMBOL)
            if cached is None:
                raise
            cached_date, value = cached
            self.logger.warning(
                "Using cached KOSPI close at %s from %s",
                trading_date,
                cached_date,
            )
            return value

    def _get_cached_price_with_relaxed_fallback(
        self, trading_date: str, symbol: str
    ) -> tuple[str, float] | None:
        cached = self.db.get_latest_price_before(trading_date, symbol)
        if cached is not None:
            return cached
        return self.db.get_latest_price(symbol)
