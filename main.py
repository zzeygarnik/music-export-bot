import asyncio
import json
import logging
import os

from aiohttp import web as aiohttp_web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from config import settings
from bot.handlers import router
from bot.handlers.common import _pending_spotify_codes
from bot.middleware import BanMiddleware, ThrottlingMiddleware, StaleButtonMiddleware, CallbackAnswerMiddleware
from utils import db

log = logging.getLogger(__name__)

_DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_web")

_LOGIN_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Login</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0d0d14;color:#e4e1ec;font-family:Inter,sans-serif;
        display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:rgba(19,19,26,.9);border:1px solid rgba(124,58,237,.2);
         border-radius:1.5rem;padding:2.5rem;width:100%;max-width:360px;
         box-shadow:0 8px 32px rgba(0,0,0,.4)}}
  h1{{font-size:1.1rem;font-weight:800;letter-spacing:.2em;text-transform:uppercase;
      color:#d2bbff;margin-bottom:.4rem}}
  p{{font-size:.65rem;color:#8a8198;margin-bottom:2rem;letter-spacing:.1em;text-transform:uppercase}}
  input{{width:100%;background:#1f1f26;border:1px solid #4a4455;border-radius:.75rem;
         color:#e4e1ec;padding:.875rem 1rem;font-size:.875rem;outline:none;
         margin-bottom:1rem;transition:border-color .2s}}
  input:focus{{border-color:#7c3aed}}
  button{{width:100%;background:#7c3aed;color:#fff;border:none;border-radius:.75rem;
          padding:.875rem;font-size:.65rem;font-weight:700;letter-spacing:.2em;
          text-transform:uppercase;cursor:pointer;transition:filter .2s}}
  button:hover{{filter:brightness(1.1)}}
  .err{{color:#f87171;font-size:.65rem;margin-bottom:1rem;text-align:center}}
</style>
</head>
<body>
<div class="card">
  <h1>ZGRNK Music</h1>
  <p>Admin access only</p>
  {error}
  <form method="POST" action="/dashboard/login">
    <input type="password" name="token" placeholder="Access token" autofocus>
    <button type="submit">Enter</button>
  </form>
</div>
</body>
</html>"""


def _check_auth(request: aiohttp_web.Request) -> bool:
    token = settings.DASHBOARD_TOKEN
    if not token:
        return False
    return request.cookies.get("dashboard_auth", "") == token


async def _dashboard_handler(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        raise aiohttp_web.HTTPFound("/dashboard/login")
    html_path = os.path.join(_DASHBOARD_DIR, "index.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return aiohttp_web.Response(text="Dashboard HTML not found", status=404)
    return aiohttp_web.Response(text=content, content_type="text/html")


async def _dashboard_login_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    return aiohttp_web.Response(
        text=_LOGIN_HTML.format(error=""), content_type="text/html"
    )


async def _dashboard_login_post(request: aiohttp_web.Request) -> aiohttp_web.Response:
    data = await request.post()
    token = data.get("token", "")
    if token and token == settings.DASHBOARD_TOKEN:
        response = aiohttp_web.HTTPFound("/dashboard")
        response.set_cookie(
            "dashboard_auth", token,
            max_age=86400 * 30, httponly=True, samesite="Strict",
        )
        return response
    return aiohttp_web.Response(
        text=_LOGIN_HTML.format(error='<p class="err">Wrong token</p>'),
        content_type="text/html",
    )


async def _dashboard_logout(request: aiohttp_web.Request) -> aiohttp_web.Response:
    response = aiohttp_web.HTTPFound("/dashboard/login")
    response.del_cookie("dashboard_auth")
    return response


async def _api_stats(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await asyncio.to_thread(db.get_dashboard_stats)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


async def _api_events(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        limit  = min(int(request.query.get("limit", 50)), 200)
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        limit, offset = 50, 0
    source = request.query.get("source", "")
    data = await asyncio.to_thread(db.get_events_dashboard, limit, source, offset)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


async def _api_chart(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    source = request.query.get("source", "sc")
    try:
        days = min(int(request.query.get("days", 7)), 30)
    except ValueError:
        days = 7
    data = await asyncio.to_thread(db.get_chart_data, source, days)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


async def _api_batch_live(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await asyncio.to_thread(db.get_batch_live_data)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


_SPOTIFY_CALLBACK_HTML_OK = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Spotify</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px">
<h2>Авторизация успешна. Вы можете вернуться в Telegram.</h2>
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
        BotCommand(command="mystats", description="Моя статистика"),
    ])
    need_server = bool(
        (settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CALLBACK_PORT)
        or settings.WEBHOOK_URL
        or settings.DASHBOARD_TOKEN
    )
    if need_server:
        web_app = aiohttp_web.Application()

        if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CALLBACK_PORT:
            web_app.router.add_get("/spotify/callback", _make_spotify_callback(bot))
            log.info("Spotify OAuth callback registered at /spotify/callback")

        if settings.DASHBOARD_TOKEN:
            web_app.router.add_get("/dashboard",         _dashboard_handler)
            web_app.router.add_get("/dashboard/login",   _dashboard_login_get)
            web_app.router.add_post("/dashboard/login",  _dashboard_login_post)
            web_app.router.add_get("/dashboard/logout",  _dashboard_logout)
            web_app.router.add_get("/api/stats",        _api_stats)
            web_app.router.add_get("/api/events",       _api_events)
            web_app.router.add_get("/api/chart",        _api_chart)
            web_app.router.add_get("/api/batch_live",   _api_batch_live)
            log.info("Dashboard registered at /dashboard")

        if settings.WEBHOOK_URL:
            webhook_path = "/bot/webhook"
            await bot.set_webhook(
                url=f"{settings.WEBHOOK_URL.rstrip('/')}{webhook_path}",
                secret_token=settings.WEBHOOK_SECRET or None,
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True,
            )
            SimpleRequestHandler(
                dispatcher=dp,
                bot=bot,
                secret_token=settings.WEBHOOK_SECRET or None,
            ).register(web_app, path=webhook_path)
            log.info("Webhook mode: %s%s", settings.WEBHOOK_URL, webhook_path)

        runner = aiohttp_web.AppRunner(web_app)
        await runner.setup()
        site = aiohttp_web.TCPSite(runner, "0.0.0.0", settings.SPOTIFY_CALLBACK_PORT)
        await site.start()
        log.info("HTTP server started on port %d", settings.SPOTIFY_CALLBACK_PORT)

    log.info("Bot started")
    if settings.WEBHOOK_URL:
        await asyncio.Event().wait()
    else:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
