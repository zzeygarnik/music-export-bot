"""Bot-level middlewares: throttling and auto-answer for callback queries."""
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery


class ThrottlingMiddleware(BaseMiddleware):
    """
    Drop requests that arrive faster than `rate_limit` seconds per user.
    Callback queries get a silent answer; messages are silently dropped.
    """

    def __init__(self, rate_limit: float = 0.7) -> None:
        self._rate = rate_limit
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None and isinstance(event, CallbackQuery):
            user = event.from_user

        if user:
            now = time.monotonic()
            if now - self._last.get(user.id, 0) < self._rate:
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ Подожди секунду…")
                return  # drop silently
            self._last[user.id] = now

        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    """
    Automatically answer every CallbackQuery that hasn't been answered yet.
    Prevents the Telegram loading spinner from hanging after slow handlers.
    Called AFTER the handler so handlers can still call answer() with custom text.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()  # no-op if already answered
            except Exception:
                pass  # already answered or query too old — ignore
        return result
