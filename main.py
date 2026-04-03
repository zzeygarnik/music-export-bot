import asyncio
import json
import logging
import os
import time
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timezone

from aiohttp import web as aiohttp_web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from config import settings
from bot.handlers import router
from bot.handlers.common import _pending_spotify_codes, detect_and_store_server_ip
from bot.tracker import set_active_msg
from bot.middleware import BanMiddleware, ThrottlingMiddleware, StaleButtonMiddleware, CallbackAnswerMiddleware, DeduplicateUpdateMiddleware
from utils import db

log = logging.getLogger(__name__)

_DASHBOARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_web")

_RATE_LIMIT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard — Too Many Attempts</title>
<script src="https://cdn.jsdelivr.net/npm/tsparticles@2.12.0/tsparticles.bundle.min.js"></script>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#0d0d14;color:#e4e1ec;font-family:Inter,sans-serif;
        display:flex;align-items:center;justify-content:center;min-height:100vh}}
  #tsparticles{{position:fixed;width:100%;height:100%;top:0;left:0;z-index:0;pointer-events:none}}
  .card{{background:rgba(19,19,26,.9);border:1px solid rgba(239,68,68,.2);
         border-radius:1.5rem;padding:2.5rem;width:100%;max-width:360px;
         box-shadow:0 8px 32px rgba(0,0,0,.4);text-align:center;position:relative;z-index:1}}
  h1{{font-size:1.1rem;font-weight:800;letter-spacing:.2em;text-transform:uppercase;
      color:#f87171;margin-bottom:1rem}}
  p{{font-size:.75rem;color:#8a8198;margin-bottom:1.5rem}}
  .timer{{font-size:2.5rem;font-weight:700;color:#d2bbff;margin-bottom:.5rem}}
  .label{{font-size:.65rem;color:#8a8198;letter-spacing:.1em;text-transform:uppercase}}
</style>
</head>
<body>
<div id="tsparticles"></div>
<div class="card">
  <h1>Too Many Attempts</h1>
  <p>5 неверных попыток. Попробуй снова через:</p>
  <div class="timer" id="t">{seconds}</div>
  <div class="label">секунд</div>
</div>
<script>
  var s = {seconds};
  var el = document.getElementById('t');
  var iv = setInterval(function() {{
    s--;
    if (s <= 0) {{ clearInterval(iv); window.location = '/dashboard'; return; }}
    el.textContent = s;
  }}, 1000);
  document.addEventListener("DOMContentLoaded", () => {{
    tsParticles.load("tsparticles", {{
      fpsLimit: 60,
      particles: {{
        number: {{ value: 90, density: {{ enable: true, area: 900 }} }},
        color:  {{ value: "#ffffff" }},
        shape:  {{ type: "circle" }},
        opacity: {{
          value: 0.45, random: true,
          anim: {{ enable: true, speed: 0.8, opacity_min: 0.05, sync: false }}
        }},
        size: {{ value: 3, random: {{ enable: true, minimumValue: 1 }} }},
        links: {{ enable: false }},
        move: {{
          enable: true, speed: 0.5, direction: "bottom",
          random: true, straight: false,
          outModes: {{ default: "out" }}
        }}
      }},
      interactivity: {{
        detectsOn: "window",
        events: {{
          onHover: {{ enable: true, mode: "grab" }},
          onClick: {{ enable: true, mode: "push" }},
          resize: true
        }},
        modes: {{
          grab: {{ distance: 160, links: {{ opacity: 0.5 }} }},
          push: {{ quantity: 3 }}
        }}
      }},
      background: {{ color: "transparent" }}
    }});
  }});
</script>
</body>
</html>"""

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 600  # seconds


def _get_client_ip(request: aiohttp_web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote or "unknown")


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Returns (is_blocked, seconds_until_unblocked)."""
    now = time.time()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _RATE_LIMIT_MAX:
        wait = int(_RATE_LIMIT_WINDOW - (now - min(attempts))) + 1
        return True, wait
    return False, 0


def _record_login_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.time())


def _check_auth(request: aiohttp_web.Request) -> bool:
    token = settings.DASHBOARD_TOKEN
    if not token:
        return False
    return request.cookies.get("dashboard_auth", "") == token


async def _dashboard_handler(request: aiohttp_web.Request) -> aiohttp_web.Response:
    html_path = os.path.join(_DASHBOARD_DIR, "index.html")
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return aiohttp_web.Response(text="Dashboard HTML not found", status=404)
    return aiohttp_web.Response(text=content, content_type="text/html")


async def _dashboard_login_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    raise aiohttp_web.HTTPFound("/dashboard")


async def _dashboard_login_post(request: aiohttp_web.Request) -> aiohttp_web.Response:
    ip = _get_client_ip(request)
    blocked, wait = _check_rate_limit(ip)
    if blocked:
        return aiohttp_web.Response(
            text=_RATE_LIMIT_HTML.format(seconds=wait),
            content_type="text/html",
            status=429,
        )
    data = await request.post()
    token = data.get("token", "")
    if token and token == settings.DASHBOARD_TOKEN:
        response = aiohttp_web.HTTPFound("/dashboard")
        response.set_cookie(
            "dashboard_auth", token,
            max_age=86400 * 30, httponly=True, samesite="Strict",
        )
        return response
    _record_login_attempt(ip)
    blocked, wait = _check_rate_limit(ip)
    if blocked:
        return aiohttp_web.Response(
            text=_RATE_LIMIT_HTML.format(seconds=wait),
            content_type="text/html",
            status=429,
        )
    return aiohttp_web.Response(status=401)


async def _dashboard_logout(request: aiohttp_web.Request) -> aiohttp_web.Response:
    response = aiohttp_web.HTTPFound("/dashboard")
    response.del_cookie("dashboard_auth")
    return response


async def _ws_handler(request: aiohttp_web.Request) -> aiohttp_web.WebSocketResponse:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    ws = aiohttp_web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    try:
        while not ws.closed:
            s = await db.get_dashboard_stats()
            b = await db.get_batch_live_data()
            try:
                await ws.send_json({"stats": s, "batch": b})
            except Exception:
                break
            await asyncio.sleep(5)
    finally:
        if not ws.closed:
            await ws.close()
    return ws


async def _api_stats(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_dashboard_stats()
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
    data = await db.get_events_dashboard(limit, source, offset)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


async def _api_chart(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    source = request.query.get("source", "sc")
    try:
        days = min(int(request.query.get("days", 7)), 30)
    except ValueError:
        days = 7
    data = await db.get_chart_data(source, days)
    return aiohttp_web.Response(text=json.dumps(data), content_type="application/json")


async def _health(request: aiohttp_web.Request) -> aiohttp_web.Response:
    return aiohttp_web.Response(text="ok")


async def _api_batch_live(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_batch_live_data()
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
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Загрузить лайки", callback_data="spotify:load_liked_auto")],
            [InlineKeyboardButton(text="← Назад", callback_data="spotify:to_menu")],
        ])
        try:
            sent = await bot.send_message(
                user_id,
                "✅ <b>Авторизация прошла успешно!</b>\n\nНажми кнопку для загрузки лайков.",
                parse_mode="HTML",
                reply_markup=kb,
            )
            set_active_msg(user_id, sent.message_id)
        except Exception as e:
            log.warning("Failed to notify user %d after Spotify OAuth: %s", user_id, e)

        return aiohttp_web.Response(text=_SPOTIFY_CALLBACK_HTML_OK, content_type="text/html")

    return handler


_SC_PROXY_REDIS_KEY = "sc:proxies"

_PROXY_SCHEMES = ("http://", "https://", "socks4://", "socks5://")


def _normalize_proxy_url(raw: str) -> str | None:
    """Normalize a free-form proxy string to a valid scheme://... URL.

    Accepts:
      - Already valid:   http://user:pass@host:port, socks5://host:port, …
      - Missing slashes: socks5:host:port  →  socks5://host:port
      - No scheme:       host:port         →  http://host:port
                         user:pass@host:port →  http://user:pass@host:port
    Returns None if the string cannot be interpreted as a proxy URL.
    """
    url = raw.strip()
    if not url:
        return None

    # Already has a recognised scheme
    if any(url.startswith(s) for s in _PROXY_SCHEMES):
        parsed = urlparse(url)
        return url if parsed.hostname else None

    # Scheme present but missing the double-slash (e.g. socks5:host:1080)
    for scheme in ("socks5", "socks4", "https", "http"):
        if url.lower().startswith(scheme + ":") and not url.startswith(scheme + "://"):
            candidate = f"{scheme}://{url[len(scheme) + 1:]}"
            parsed = urlparse(candidate)
            return candidate if parsed.hostname else None

    # No scheme: bare host:port or user:pass@host:port
    # Require at least one colon or an @-sign so we don't accept random strings
    if ":" in url or "@" in url:
        candidate = f"http://{url}"
        parsed = urlparse(candidate)
        return candidate if parsed.hostname else None

    return None


async def _test_proxy_url(proxy_url: str) -> tuple[bool, str]:
    """Test a proxy by fetching api.ipify.org through it. Returns (ok, detail)."""
    import aiohttp
    from urllib.parse import urlparse
    try:
        if proxy_url.lower().startswith("socks"):
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(proxy_url)
            async with aiohttp.ClientSession(connector=connector) as s:
                async with s.get("https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True, f"OK — exit IP: {(await resp.text()).strip()}"
                    return False, f"HTTP {resp.status}"
        else:
            # aiohttp does NOT auto-extract credentials from proxy URL — pass them separately
            parsed = urlparse(proxy_url)
            proxy_auth = None
            if parsed.username:
                proxy_auth = aiohttp.BasicAuth(parsed.username, parsed.password or "")
                clean_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            else:
                clean_url = proxy_url
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.ipify.org",
                    proxy=clean_url,
                    proxy_auth=proxy_auth,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True, f"OK — exit IP: {(await resp.text()).strip()}"
                    return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)[:120]


def _proxy_short_label(url: str) -> str:
    """Extract host:port from proxy URL for display."""
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
        return f"{p.hostname}:{p.port}" if p.port else (p.hostname or url[:30])
    except Exception:
        return url[:30]


async def _load_proxies_from_redis(redis) -> list[dict]:
    raw = await redis.get(_SC_PROXY_REDIS_KEY)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return []


async def _save_proxies_to_redis(redis, proxies: list[dict]) -> None:
    await redis.set(_SC_PROXY_REDIS_KEY, json.dumps(proxies))


async def _init_sc_proxies(redis) -> None:
    """Seed Redis proxy list from SC_PROXIES env if Redis list is empty, then load into runtime."""
    from bot.handlers import common as hcommon
    proxies = await _load_proxies_from_redis(redis)
    if not proxies and settings.SC_PROXIES:
        # First run: import env proxies into Redis
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        proxies = [
            {"url": p.strip(), "added_at": now, "last_test_at": None,
             "last_test_ok": None, "last_test_detail": None}
            for p in settings.SC_PROXIES.split(",") if p.strip()
        ]
        await _save_proxies_to_redis(redis, proxies)
        log.info("SC proxies: seeded %d proxies from env into Redis", len(proxies))
    # Apply to runtime
    urls = [p["url"] for p in proxies]
    hcommon._sc_proxies[:] = urls
    log.info("SC proxies loaded: %d proxies", len(urls))


async def _check_sc_cookies_task(bot: Bot) -> None:
    """Check SC cookie validity at startup and every 24 h. Alert admin if cookies look expired."""
    from core import sc_downloader
    from core.sc_downloader import SCBanError
    import http.cookiejar

    if not settings.SC_COOKIE_FILE or not settings.ADMIN_ID:
        return

    # Small delay so bot is fully running before the check
    await asyncio.sleep(15)

    while True:
        try:
            # Step 1: parse cookie file
            jar = http.cookiejar.MozillaCookieJar()
            jar.load(settings.SC_COOKIE_FILE, ignore_discard=True, ignore_expires=True)
            oauth_cookies = [c for c in jar if c.name == "oauth_token"]
            if not oauth_cookies:
                await bot.send_message(
                    settings.ADMIN_ID,
                    "⚠️ <b>SC cookies: oauth_token не найден</b>\n\n"
                    f"Файл <code>{settings.SC_COOKIE_FILE}</code> загружен, но cookie "
                    f"<code>oauth_token</code> отсутствует.\n"
                    "Скорее всего куки устарели — нужно обновить.",
                    parse_mode="HTML",
                )
                log.warning("SC cookie healthcheck: oauth_token missing")
            else:
                # Step 2: test actual search
                try:
                    results = await sc_downloader.search("billie eilish", max_results=1)
                    if results:
                        log.info("SC cookie healthcheck: OK")
                    else:
                        # No results might mean cookies are expired (SC blocks scraping)
                        log.warning("SC cookie healthcheck: search returned empty — cookies might be stale")
                        await bot.send_message(
                            settings.ADMIN_ID,
                            "⚠️ <b>SC cookies: тест-поиск вернул пустой результат</b>\n\n"
                            "Возможно куки устарели — SoundCloud не возвращает результаты.\n"
                            "Рекомендуется обновить <code>sc_cookies.txt</code>.",
                            parse_mode="HTML",
                        )
                except SCBanError:
                    log.info("SC cookie healthcheck: IP ban detected, skipping cookie alert")
                except Exception as e:
                    log.warning("SC cookie healthcheck search error: %s", e)
        except FileNotFoundError:
            log.warning("SC cookie healthcheck: file not found: %s", settings.SC_COOKIE_FILE)
            try:
                await bot.send_message(
                    settings.ADMIN_ID,
                    f"⚠️ <b>SC cookies: файл не найден</b>\n\n"
                    f"<code>{settings.SC_COOKIE_FILE}</code> не существует.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        except Exception as e:
            log.warning("SC cookie healthcheck failed: %s", e)

        await asyncio.sleep(86400)  # repeat every 24 h


# ── Proxy API handlers ────────────────────────────────────────────────────────

async def _api_network_status(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    from bot.handlers import common as hcommon
    return aiohttp_web.Response(
        text=json.dumps(hcommon.get_network_status()),
        content_type="application/json",
    )


async def _api_proxies_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    redis = request.app["redis"]
    from bot.handlers import common as hcommon
    proxies = await _load_proxies_from_redis(redis)
    active_url = hcommon._sc_proxies[hcommon._sc_proxy_index] if (
        0 <= hcommon._sc_proxy_index < len(hcommon._sc_proxies)
    ) else None
    result = []
    for i, p in enumerate(proxies):
        result.append({**p, "active": p["url"] == active_url, "index": i})
    return aiohttp_web.Response(text=json.dumps(result), content_type="application/json")


async def _api_proxies_add(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        body = await request.json()
        url = (body.get("url") or "").strip()
    except Exception:
        return aiohttp_web.Response(status=400, text="Invalid JSON")

    url = _normalize_proxy_url(url) or ""
    if not url:
        return aiohttp_web.Response(status=422, text="Invalid proxy URL")

    redis = request.app["redis"]
    bot: Bot = request.app["bot"]
    from bot.handlers import common as hcommon

    proxies = await _load_proxies_from_redis(redis)
    if any(p["url"] == url for p in proxies):
        return aiohttp_web.Response(status=409, text="Proxy already exists")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {"url": url, "added_at": now, "last_test_at": None,
             "last_test_ok": None, "last_test_detail": None}
    proxies.append(entry)
    await _save_proxies_to_redis(redis, proxies)

    # Update runtime list
    hcommon._sc_proxies.append(url)
    log.info("SC proxy added via dashboard: %s", url)

    # Notify admin
    try:
        await bot.send_message(
            settings.ADMIN_ID,
            f"🔌 <b>Новый SC прокси добавлен через дашборд</b>\n\n"
            f"<code>{url}</code>\n"
            f"Метка: <b>{_proxy_short_label(url)}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("Failed to notify admin about new proxy: %s", e)

    return aiohttp_web.Response(text=json.dumps(entry), content_type="application/json", status=201)


async def _api_proxies_delete(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        idx = int(request.match_info["idx"])
    except (KeyError, ValueError):
        return aiohttp_web.Response(status=400, text="Invalid index")

    redis = request.app["redis"]
    from bot.handlers import common as hcommon
    from core import sc_downloader

    proxies = await _load_proxies_from_redis(redis)
    if idx < 0 or idx >= len(proxies):
        return aiohttp_web.Response(status=404, text="Proxy not found")

    removed = proxies.pop(idx)
    await _save_proxies_to_redis(redis, proxies)

    # Rebuild runtime list and reset active proxy to avoid stale index
    hcommon._sc_proxies[:] = [p["url"] for p in proxies]
    hcommon._sc_proxy_index = -1
    sc_downloader.set_active_proxy("")
    hcommon.cancel_recovery_check()
    log.info("SC proxy removed via dashboard: %s", removed["url"])

    return aiohttp_web.Response(text=json.dumps({"removed": removed["url"]}), content_type="application/json")


async def _api_proxies_test(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        idx = int(request.match_info["idx"])
    except (KeyError, ValueError):
        return aiohttp_web.Response(status=400, text="Invalid index")

    redis = request.app["redis"]
    proxies = await _load_proxies_from_redis(redis)
    if idx < 0 or idx >= len(proxies):
        return aiohttp_web.Response(status=404, text="Proxy not found")

    url = proxies[idx]["url"]
    ok, detail = await _test_proxy_url(url)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    proxies[idx]["last_test_at"] = now
    proxies[idx]["last_test_ok"] = ok
    proxies[idx]["last_test_detail"] = detail
    await _save_proxies_to_redis(redis, proxies)

    log.info("SC proxy test [%d] %s: ok=%s detail=%s", idx, url, ok, detail)
    return aiohttp_web.Response(
        text=json.dumps({"ok": ok, "detail": detail}),
        content_type="application/json",
    )


def _build_storage():
    """Connect to Redis for FSM storage. Raises on failure — no silent fallback."""
    import redis as redis_sync
    r = redis_sync.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    r.ping()
    r.close()
    storage = RedisStorage.from_url(settings.REDIS_URL)
    log.info("FSM storage: Redis (%s)", settings.REDIS_URL)
    return storage


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if settings.POSTGRES_URL:
        try:
            await db.init_pool(settings.POSTGRES_URL)
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
    dp.update.outer_middleware(DeduplicateUpdateMiddleware(settings.REDIS_URL))
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
    # Shared async Redis client (proxy manager + other dashboard APIs)
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    # Initialise SC proxy list from Redis (seeds from env SC_PROXIES on first run)
    await _init_sc_proxies(_redis)

    need_server = bool(
        (settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CALLBACK_PORT)
        or settings.WEBHOOK_URL
        or settings.DASHBOARD_TOKEN
    )
    if need_server:
        web_app = aiohttp_web.Application()
        web_app["bot"]   = bot
        web_app["redis"] = _redis

        if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CALLBACK_PORT:
            web_app.router.add_get("/spotify/callback", _make_spotify_callback(bot))
            log.info("Spotify OAuth callback registered at /spotify/callback")

        web_app.router.add_get("/health", _health)

        if settings.DASHBOARD_TOKEN:
            web_app.router.add_get("/dashboard",         _dashboard_handler)
            web_app.router.add_get("/dashboard/login",   _dashboard_login_get)
            web_app.router.add_post("/dashboard/login",  _dashboard_login_post)
            web_app.router.add_get("/dashboard/logout",  _dashboard_logout)
            web_app.router.add_get("/api/stats",        _api_stats)
            web_app.router.add_get("/api/events",       _api_events)
            web_app.router.add_get("/api/chart",        _api_chart)
            web_app.router.add_get("/api/batch_live",   _api_batch_live)
            web_app.router.add_get("/api/ws",           _ws_handler)
            web_app.router.add_get("/api/network-status",       _api_network_status)
            web_app.router.add_get("/api/proxies",              _api_proxies_get)
            web_app.router.add_post("/api/proxies",             _api_proxies_add)
            web_app.router.add_delete("/api/proxies/{idx}",     _api_proxies_delete)
            web_app.router.add_post("/api/proxies/{idx}/test",  _api_proxies_test)
            log.info("Dashboard registered at /dashboard")

        if settings.WEBHOOK_URL:
            webhook_path = "/bot/webhook"
            await bot.set_webhook(
                url=f"{settings.WEBHOOK_URL.rstrip('/')}{webhook_path}",
                secret_token=settings.WEBHOOK_SECRET or None,
                allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"],
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

    await detect_and_store_server_ip()
    asyncio.create_task(_check_sc_cookies_task(bot))
    log.info("Bot started")
    if settings.WEBHOOK_URL:
        await asyncio.Event().wait()
    else:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"])


if __name__ == "__main__":
    asyncio.run(main())
