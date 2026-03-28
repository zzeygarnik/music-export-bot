import asyncio
import logging

from aiohttp import web as aiohttp_web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton

from config import settings
from bot.handlers import router
from bot.handlers.common import _pending_spotify_codes
from bot.middleware import BanMiddleware, ThrottlingMiddleware, StaleButtonMiddleware, CallbackAnswerMiddleware
from utils import db

log = logging.getLogger(__name__)

_SPOTIFY_CALLBACK_HTML_OK = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Spotify</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h2>✅ Авторизация прошла успешно!</h2>
<p>Вернись в Telegram — бот уже получил доступ и загружает твои лайки.</p>
</body></html>"""

_SPOTIFY_CALLBACK_HTML_ERR = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Spotify</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h2>❌ Авторизация отменена.</h2>
<p>Вернись в Telegram и попробуй снова.</p>
</body></html>"""


def _make_spotify_callback(bot: Bot):
    async def handler(request: aiohttp_web.Request) -> aiohttp_web.Response:
        code = request.query.get("code")
        error = request.query.get("error")
        state = request.query.get("state", "")
        try:
            user_id = int(state)
        except (ValueError, TypeError):
            return aiohttp_web.Response(text="Bad request", status=400)

        if error or not code:
            try:
                await bot.send_message(user_id, "❌ Авторизация Spotify отменена.")
            except Exception:
                pass
            return aiohttp_web.Response(text=_SPOTIFY_CALLBACK_HTML_ERR, content_type="text/html")

        _pending_spotify_codes[user_id] = code
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎵 Загрузить лайки", callback_data="spotify:load_liked_auto"),
        ]])
        try:
            await bot.send_message(
                user_id,
                "✅ <b>Авторизация прошла успешно!</b>\n\nНажми кнопку для загрузки лайков.",
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            log.warning("Failed to notify user %d after Spotify OAuth: %s", user_id, e)

        return aiohttp_web.Response(text=_SPOTIFY_CALLBACK_HTML_OK, content_type="text/html")

    return handler


def _build_storage():
    """Use Redis if reachable, fall back to MemoryStorage with a warning."""
    try:
        import redis as redis_sync
        r = redis_sync.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        r.close()
        storage = RedisStorage.from_url(settings.REDIS_URL)
        log.info("FSM storage: Redis (%s) — sessions survive restarts", settings.REDIS_URL)
        return storage
    except Exception as e:
        log.warning(
            "Redis unavailable (%s) — using MemoryStorage (sessions will NOT survive restarts)",
            e,
        )
        return MemoryStorage()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if settings.POSTGRES_URL:
        try:
            db.init_pool(settings.POSTGRES_URL)
        except Exception as e:
            log.error("Failed to connect to PostgreSQL: %s — event logging will be unavailable", e)
    else:
        log.warning("POSTGRES_URL not set — event logging disabled")

    session = AiohttpSession(
        proxy=settings.SC_PROXY or None,
        timeout=600,  # 10 min — SC download via proxy can be slow
    )
    bot = Bot(token=settings.BOT_TOKEN, session=session)
    dp = Dispatcher(storage=_build_storage())
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.7))
    dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.7))
    dp.callback_query.middleware(StaleButtonMiddleware())
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="faq", description="FAQ и обратная связь"),
        BotCommand(command="admin", description="Админ-панель"),
    ])
    if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CALLBACK_PORT:
        web_app = aiohttp_web.Application()
        web_app.router.add_get("/spotify/callback", _make_spotify_callback(bot))
        runner = aiohttp_web.AppRunner(web_app)
        await runner.setup()
        site = aiohttp_web.TCPSite(runner, "0.0.0.0", settings.SPOTIFY_CALLBACK_PORT)
        await site.start()
        log.info("Spotify OAuth callback server started on port %d", settings.SPOTIFY_CALLBACK_PORT)

    log.info("Bot started")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
