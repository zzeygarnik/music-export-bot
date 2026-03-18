import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from bot.handlers import router
from bot.middleware import ThrottlingMiddleware, CallbackAnswerMiddleware

log = logging.getLogger(__name__)


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

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=_build_storage())
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.7))
    dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.7))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(router)

    log.info("Bot started")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
