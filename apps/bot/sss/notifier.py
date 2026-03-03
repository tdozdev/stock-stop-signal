from __future__ import annotations

import asyncio
import time
from typing import Any

from telegram import Bot
from telegram.error import RetryAfter


class TelegramNotifier:
    def __init__(self, bot: Bot, max_per_sec: int = 25) -> None:
        self.bot = bot
        self.max_per_sec = max_per_sec
        self._sent_count = 0
        self._window_start = time.monotonic()

    async def send_message(self, chat_id: str, text: str, reply_markup: Any | None = None) -> None:
        await self._wait_rate_slot()
        while True:
            try:
                await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
                return
            except RetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after))

    async def _wait_rate_slot(self) -> None:
        now = time.monotonic()
        elapsed = now - self._window_start
        if elapsed >= 1:
            self._window_start = now
            self._sent_count = 0
        if self._sent_count >= self.max_per_sec:
            wait_s = 1 - elapsed
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            self._window_start = time.monotonic()
            self._sent_count = 0
        self._sent_count += 1
