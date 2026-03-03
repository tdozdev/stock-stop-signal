from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from .calendar import CalendarSettings, ensure_calendar_seeded
from .config import KST, Settings
from .date_utils import parse_yyyymmdd_to_iso, today_kst_date_str
from .db import Database
from .market import KrxApiMarketData, PykrxMarketData
from .notifier import TelegramNotifier
from .service import DailyBatchRunner, SSSService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("sss")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def parse_stop_arg(args: list[str]) -> float:
    if len(args) != 1:
        raise ValueError("사용법: /s 10")
    return float(args[0])


def parse_daily_arg(args: list[str]) -> bool:
    if len(args) != 1 or args[0].lower() not in {"on", "off"}:
        raise ValueError("사용법: /daily on|off")
    return args[0].lower() == "on"


def parse_buy_args(args: list[str]) -> tuple[str, float, str]:
    if len(args) not in {2, 3}:
        raise ValueError("사용법: /c 005930 70000 [YYYYMMDD]")
    symbol = args[0]
    buy_price = float(args[1])
    buy_date = parse_yyyymmdd_to_iso(args[2]) if len(args) == 3 else today_kst_date_str()
    return symbol, buy_price, buy_date


async def register_handlers(app: Application, service: SSSService, batch: DailyBatchRunner) -> None:
    help_text = (
        "📘 SSS 명령어 안내\n\n"
        "기본\n"
        "/start\n"
        "/help\n"
        "/status\n\n"
        "손절 설정\n"
        "/s 10\n"
        "/stop 10\n"
        "/손절 10\n\n"
        "Daily 리포트\n"
        "/daily on\n"
        "/daily off\n\n"
        "종목 관리\n"
        "/c 종목코드 매수가격 매수날짜(YYYYMMDD)\n"
        "예: /c 005930 70000 20250115\n"
        "예: /c 005930 70000  (매수날짜 미입력 시 오늘 KST)\n"
        "/u 종목코드 매수가격 [매수날짜]\n"
        "/d 종목코드\n\n"
        "조회\n"
        "/r\n"
        "/r 종목코드"
    )

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user is None or update.effective_chat is None:
            return
        uid = str(update.effective_chat.id)
        service.ensure_user(uid)
        await update.message.reply_text(
            "SSS 봇이 시작되었습니다.\n"
            "손절 설정: /s 10\n"
            "종목 추가: /c 종목코드 매수가격 매수날짜(YYYYMMDD)\n"
            "상태 확인: /status\n"
            "도움말: /help"
        )

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(help_text)

    async def set_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            pct = parse_stop_arg(context.args)
            service.set_stop_loss(uid, pct)
            await update.message.reply_text(f"매도 기준을 {pct:g}%로 설정했습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def set_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            on = parse_daily_arg(context.args)
            service.set_daily(uid, on)
            await update.message.reply_text(f"Daily 리포트를 {'ON' if on else 'OFF'}로 설정했습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def add_holding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            symbol, price, buy_date = parse_buy_args(context.args)
            saved_symbol, saved_name = service.upsert_holding(uid, symbol, price, buy_date)
            await update.message.reply_text(f"{saved_name}({saved_symbol}) 종목을 저장했습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def update_holding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            if len(context.args) not in {2, 3}:
                raise ValueError("사용법: /u 005930 72000 [YYYYMMDD]")

            symbol = context.args[0]
            price = float(context.args[1])
            if len(context.args) == 3:
                buy_date = parse_yyyymmdd_to_iso(context.args[2])
                saved_symbol, saved_name = service.upsert_holding(uid, symbol, price, buy_date)
            else:
                saved_symbol, saved_name = service.update_buy_price_only(uid, symbol, price)
            await update.message.reply_text(f"{saved_name}({saved_symbol}) 종목을 저장했습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def delete_holding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            if len(context.args) != 1:
                raise ValueError("사용법: /d 005930")
            count, symbol, name = service.delete_holding(uid, context.args[0])
            if count:
                if name:
                    await update.message.reply_text(f"{name}({symbol}) 종목을 삭제했습니다.")
                else:
                    await update.message.reply_text(f"{symbol} 종목을 삭제했습니다.")
            else:
                await update.message.reply_text("삭제할 종목이 없습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            symbol = context.args[0] if context.args else None
            text = service.render_portfolio(uid, symbol=symbol)
            if symbol:
                kb = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("매도완료", callback_data=f"sell:{symbol}")],
                        [InlineKeyboardButton("매도보류", callback_data="view_all")],
                    ]
                )
                await update.message.reply_text(text, reply_markup=kb)
                return

            holdings = service.db.list_holdings(uid)
            if holdings:
                rows = [
                    [
                        InlineKeyboardButton(
                            f"{h['name']}({h['symbol']})",
                            callback_data=f"view:{h['symbol']}",
                        )
                    ]
                    for h in holdings
                ]
                kb = InlineKeyboardMarkup(rows)
                await update.message.reply_text(text, reply_markup=kb)
            else:
                await update.message.reply_text(text)
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.message is None:
            return
        await query.answer()

        uid = str(update.effective_chat.id)
        data = query.data or ""
        try:
            if data.startswith("view:"):
                symbol = data.split(":", 1)[1]
                text = service.render_portfolio(uid, symbol=symbol)
                kb = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("매도완료", callback_data=f"sell:{symbol}")],
                        [InlineKeyboardButton("매도보류", callback_data="view_all")],
                    ]
                )
                await query.message.edit_text(text=text, reply_markup=kb)
                return

            if data == "view_all":
                text = service.render_portfolio(uid)
                holdings = service.db.list_holdings(uid)
                if holdings:
                    rows = [
                        [
                            InlineKeyboardButton(
                                f"{h['name']}({h['symbol']})",
                                callback_data=f"view:{h['symbol']}",
                            )
                        ]
                        for h in holdings
                    ]
                    kb = InlineKeyboardMarkup(rows)
                    await query.message.edit_text(text=text, reply_markup=kb)
                else:
                    await query.message.edit_text(text=text)
                return

            if data == "hold_alert":
                await query.message.edit_text(
                    text=f"{query.message.text}\n\n매도보류로 유지했습니다."
                )
                return

            if data.startswith("sell:"):
                symbol = data.split(":", 1)[1]
                count, deleted_symbol, deleted_name = service.delete_holding(uid, symbol)
                if count:
                    msg = f"{deleted_name}({deleted_symbol}) 종목을 삭제했습니다."
                else:
                    msg = "이미 삭제되었거나 존재하지 않는 종목입니다."
                await query.message.edit_text(text=msg)
                return
        except Exception as exc:
            await query.message.reply_text(str(exc))

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = str(update.effective_chat.id)
        try:
            await update.message.reply_text(service.render_status(uid))
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def stop_korean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        raw = (update.message.text or "").strip().split()
        uid = str(update.effective_chat.id)
        try:
            if len(raw) != 2:
                raise ValueError("사용법: /손절 10")
            service.set_stop_loss(uid, float(raw[1]))
            await update.message.reply_text(f"매도 기준을 {float(raw[1]):g}%로 설정했습니다.")
        except Exception as exc:
            await update.message.reply_text(str(exc))

    async def run_batch_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await batch.run()
        await update.message.reply_text("일일 배치를 실행했습니다.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("s", set_stop))
    app.add_handler(CommandHandler("stop", set_stop))
    app.add_handler(CommandHandler("daily", set_daily))
    app.add_handler(CommandHandler("c", add_holding))
    app.add_handler(CommandHandler("u", update_holding))
    app.add_handler(CommandHandler("d", delete_holding))
    app.add_handler(CommandHandler("r", report))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("runbatch", run_batch_now))
    app.add_handler(MessageHandler(filters.Regex(r"^/손절\s+"), stop_korean))
    app.add_handler(CallbackQueryHandler(on_callback))


async def main_async() -> None:
    settings = Settings.from_env()
    db = Database(settings.db_path)
    db.init_schema()
    now = datetime.now(tz=KST).date()
    calendar_settings = CalendarSettings(
        past_days=settings.calendar_past_days,
        future_days=settings.calendar_future_days,
        refill_threshold_days=settings.calendar_refill_threshold_days,
    )
    ensure_calendar_seeded(
        db,
        now - timedelta(days=calendar_settings.past_days),
        now + timedelta(days=calendar_settings.future_days),
    )

    if settings.market_provider.lower() == "krx_api":
        market = KrxApiMarketData(
            base_url=settings.krx_api_base_url,
            api_key=settings.krx_api_key,
            kospi_daily_path=settings.krx_kospi_daily_path,
            kosdaq_daily_path=settings.krx_kosdaq_daily_path,
            etf_daily_path=settings.krx_etf_daily_path,
            kospi_index_daily_path=settings.krx_kospi_index_daily_path,
            date_param=settings.krx_date_param,
            timeout_sec=settings.krx_timeout_sec,
        )
        logger.info("Using KRX official API provider")
    else:
        market = PykrxMarketData()
        logger.info("Using pykrx provider")

    if not settings.telegram_token:
        logger.warning("TELEGRAM_BOT_TOKEN이 없어 텔레그램 연결 없이 대기 모드로 실행합니다.")
        while True:
            await asyncio.sleep(3600)

    application = (
        Application.builder()
        .token(settings.telegram_token)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .build()
    )
    notifier = TelegramNotifier(application.bot)
    service = SSSService(db, market, calendar_settings)
    batch = DailyBatchRunner(db, market, notifier, calendar_settings)

    await register_handlers(application, service, batch)

    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(
        batch.run,
        "cron",
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        id="daily-batch",
        replace_existing=True,
    )
    scheduler.start()

    logger.info(
        "SSS bot started. daily batch at %02d:%02d KST",
        settings.schedule_hour,
        settings.schedule_minute,
    )
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown(wait=False)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        db.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
