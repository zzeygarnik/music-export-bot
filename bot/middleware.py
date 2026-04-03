"""Bot-level middlewares: throttling, stale-button guard, auto-answer for callback queries."""
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import settings
from utils import db
from bot.tracker import _active_msg

# Callback prefixes that are exempt from stale check (must work until explicitly acted on).
_ETERNAL_CALLBACK_PREFIXES = ("batch_req:approve:", "batch_req:reject:", "batch_req:send")


class BanMiddleware(BaseMiddleware):
    """Block banned users before any handler runs. Admin is always exempt."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user and user.id != settings.ADMIN_ID:
            try:
                if await db.is_banned(user.id):
                    if isinstance(event, CallbackQuery):
                        await event.answer("⛔ Ты заблокирован.", show_alert=True)
                    elif isinstance(event, Message):
                        await event.answer("⛔ Ты заблокирован и не можешь пользоваться этим ботом.")
                    return
            except Exception:
                pass  # DB unavailable — fail open
        return await handler(event, data)


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


class StaleButtonMiddleware(BaseMiddleware):
    """
    Reject callback queries that don't come from the user's current active message.
    Prevents users from pressing buttons on old bot messages mid-flow.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.message:
            if not any(
                (event.data or "").startswith(p) for p in _ETERNAL_CALLBACK_PREFIXES
            ):
                user_id = event.from_user.id
                tracked = _active_msg.get(user_id)
                if tracked is not None and event.message.message_id != tracked:
                    await event.answer("Используй последнее сообщение бота.")
                    return
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
