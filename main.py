import asyncio
import json
import logging
import os
import time
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from aiohttp import web as aiohttp_web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer, PRODUCTION
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
_startup_time: float = 0.0


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


def _check_csrf(request: aiohttp_web.Request) -> bool:
    """Origin/Referer check for mutating requests (defense-in-depth on top of SameSite=Strict)."""
    origin = request.headers.get("Origin") or request.headers.get("Referer", "")
    if not origin:
        return True  # same-host direct requests have no Origin
    if request.host in origin:
        return True
    # Also accept when behind a reverse proxy that rewrites Host
    forwarded = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()
    if forwarded and forwarded in origin:
        return True
    return False


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


_LOGIN_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ZGRNK Music \u2014 Login</title>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@900&family=Syne:wght@400;600;700&family=Fira+Code:wght@300;400&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/tsparticles@2.12.0/tsparticles.bundle.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d0d14;--surface:#13131a;--primary:#d2bbff;--accent:#7c3aed;
  --red:#f87171;--text:#e4e1ec;--muted:#9b93a8;--dim:#4d4760;
  --border:rgba(74,68,85,.45);
  --fd:'Big Shoulders Display',sans-serif;--fu:'Syne',sans-serif;--fm:'Fira Code',monospace}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:var(--fu);font-size:14px;-webkit-font-smoothing:antialiased}
body::before{content:'';position:fixed;inset:0;pointer-events:none;
  background-image:linear-gradient(rgba(124,58,237,.035) 1px,transparent 1px),
    linear-gradient(90deg,rgba(124,58,237,.035) 1px,transparent 1px);
  background-size:52px 52px;z-index:0}
body::after{content:'';position:fixed;top:0;right:0;width:400px;height:400px;
  background:radial-gradient(circle at 100% 0,rgba(124,58,237,.1),transparent 65%);
  pointer-events:none;z-index:0}
.wrap{position:relative;z-index:1;height:100%;display:flex;
  align-items:center;justify-content:center}
#tsparticles{position:fixed;inset:0;z-index:0;pointer-events:none}
.login-glow{position:fixed;width:520px;height:520px;border-radius:50%;
  background:radial-gradient(circle,rgba(124,58,237,.13),transparent 65%);
  pointer-events:none;transform:translate(-50%,-50%);z-index:0}
.lbox{position:relative;z-index:1;width:340px;
  transition:transform .14s ease-out;will-change:transform;transform-style:preserve-3d}
.l-title{font-family:var(--fd);font-weight:900;font-size:3.4rem;line-height:.95;
  letter-spacing:.06em;color:var(--primary);text-shadow:0 0 60px rgba(210,187,255,.18)}
.l-sub{font-family:var(--fm);font-size:9px;letter-spacing:.35em;color:var(--dim);
  text-transform:uppercase;margin-top:8px}
.l-rule{width:100%;height:1px;margin:32px 0;
  background:linear-gradient(90deg,var(--accent) 0%,transparent 100%)}
.l-label{display:block;font-family:var(--fm);font-size:9px;letter-spacing:.28em;
  text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.l-field{position:relative;margin-bottom:20px}
.l-field::after{content:'';position:absolute;bottom:0;left:0;width:0;height:2px;
  background:var(--accent);transition:width .3s ease}
.l-field:focus-within::after{width:100%}
.l-input{width:100%;background:transparent;border:none;
  border-bottom:1px solid var(--border);padding:8px 0;
  color:var(--text);font-family:var(--fm);font-size:15px;
  letter-spacing:.12em;outline:none;transition:border-color .25s}
.l-input:focus{border-bottom-color:rgba(124,58,237,.5)}
.l-btn{width:100%;padding:13px 24px;background:transparent;
  border:1px solid var(--accent);color:var(--primary);
  font-family:var(--fu);font-size:11px;font-weight:700;
  letter-spacing:.22em;text-transform:uppercase;cursor:pointer;
  position:relative;overflow:hidden;transition:color .25s;margin-top:6px}
.l-btn::before{content:'';position:absolute;inset:0;background:var(--accent);
  transform:translateX(-100%);transition:transform .28s ease}
.l-btn:hover{color:#fff}
.l-btn:hover::before{transform:translateX(0)}
.l-btn span{position:relative;z-index:1}
.l-err{font-family:var(--fm);font-size:9px;color:var(--red);
  letter-spacing:.15em;margin-top:12px;display:none}
.l-err.on{display:block}
.l-foot{margin-top:28px;font-family:var(--fm);font-size:9px;
  color:var(--dim);letter-spacing:.18em;text-transform:uppercase}
</style>
</head>
<body>
<div id="tsparticles"></div>
<div class="wrap" id="wrap">
  <div class="lbox" id="lbox">
    <div class="l-title">ZGRNK<br>MUSIC</div>
    <div class="l-sub">Music Export Bot</div>
    <div class="l-rule"></div>
    <form id="lf" method="POST" action="/dashboard/login">
      <label class="l-label" for="tok">Access Token</label>
      <div class="l-field">
        <input class="l-input" id="tok" name="token" type="password"
               placeholder="\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7\u00b7" autocomplete="off" spellcheck="false">
      </div>
      <button class="l-btn" type="submit"><span>Authenticate &#8594;</span></button>
      <div class="l-err" id="err">Invalid access token</div>
    </form>
    <div class="l-foot">Admin access only &nbsp;&middot;&nbsp; Rate limited</div>
  </div>
</div>
<script>
const p = new URLSearchParams(location.search);
if (p.get('err')) document.getElementById('err').classList.add('on');

document.addEventListener('DOMContentLoaded', function() {
  tsParticles.load('tsparticles', {
    fpsLimit: 60,
    particles: {
      number: { value: 90, density: { enable: true, area: 900 } },
      color:  { value: '#ffffff' },
      shape:  { type: 'circle' },
      opacity: {
        value: 0.4, random: true,
        animation: { enable: true, speed: 0.8, minimumValue: 0.05, sync: false }
      },
      size: { value: { min: 1, max: 3 } },
      links: { enable: false },
      move: {
        enable: true, speed: 0.45, direction: 'bottom',
        random: true, straight: false,
        outModes: { default: 'out' }
      }
    },
    interactivity: {
      detectsOn: 'window',
      events: {
        onHover: { enable: true, mode: 'grab' },
        onClick: { enable: true, mode: 'push' },
        resize: true
      },
      modes: {
        grab: { distance: 160, links: { opacity: 0.45 } },
        push: { quantity: 3 }
      }
    },
    background: { color: 'transparent' }
  });
});

(function() {
  var wrap = document.getElementById('wrap');
  var lbox = document.getElementById('lbox');
  var glow = document.createElement('div');
  glow.className = 'login-glow';
  glow.style.left = '-999px';
  glow.style.top  = '-999px';
  document.body.appendChild(glow);

  wrap.addEventListener('mousemove', function(e) {
    glow.style.left = e.clientX + 'px';
    glow.style.top  = e.clientY + 'px';
    var r  = wrap.getBoundingClientRect();
    var dx = (e.clientX - r.left  - r.width  / 2) / (r.width  / 2);
    var dy = (e.clientY - r.top   - r.height / 2) / (r.height / 2);
    lbox.style.transform =
      'perspective(900px) rotateY(' + (dx * 6).toFixed(2) + 'deg) rotateX(' + (-dy * 4).toFixed(2) + 'deg)';
  });
  wrap.addEventListener('mouseleave', function() {
    glow.style.left = '-999px';
    lbox.style.transform = 'perspective(900px) rotateY(0deg) rotateX(0deg)';
  });
}());
</script>
</body>
</html>"""


async def _dashboard_login_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if _check_auth(request):
        raise aiohttp_web.HTTPFound("/dashboard")
    return aiohttp_web.Response(text=_LOGIN_PAGE_HTML, content_type="text/html")


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
    raise aiohttp_web.HTTPFound("/dashboard/login?err=1")


async def _dashboard_logout(request: aiohttp_web.Request) -> aiohttp_web.Response:
    response = aiohttp_web.HTTPFound("/dashboard")
    response.del_cookie("dashboard_auth")
    return response


async def _ws_handler(request: aiohttp_web.Request) -> aiohttp_web.WebSocketResponse:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    ws = aiohttp_web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    from core import sc_downloader as _scd
    try:
        while not ws.closed:
            s = await db.get_dashboard_stats()
            b = await db.get_batch_live_data()
            p = {
                "sc": _scd.get_active_proxy() or None,
                "yt": _scd.get_yt_active_proxy() or None,
            }
            try:
                await ws.send_json({"stats": s, "batch": b, "proxies": p})
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


async def _api_cache(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_cache_stats()
    return aiohttp_web.Response(text=json.dumps(data, default=str), content_type="application/json")


async def _api_events(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        limit  = min(int(request.query.get("limit", 50)), 200)
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        limit, offset = 50, 0
    source   = request.query.get("source", "")
    username = request.query.get("username", "").strip()
    data = await db.get_events_dashboard(limit, source, offset, username)
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


async def _api_users(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_users_dashboard()
    return aiohttp_web.Response(text=json.dumps(data, default=str), content_type="application/json")


async def _api_renames(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    try:
        limit  = min(int(request.query.get("limit", 30)), 100)
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        limit, offset = 30, 0
    data = await db.get_renames_dashboard(limit, offset)
    return aiohttp_web.Response(text=json.dumps(data, default=str), content_type="application/json")


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


def _mask_proxy_url(url: str) -> str:
    """Return proxy URL with password replaced by *** for safe display/logging."""
    from urllib.parse import urlparse, urlunparse
    try:
        p = urlparse(url)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}"
            if p.port:
                netloc += f":{p.port}"
            return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        pass
    return url


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
    active_sc_url = hcommon._sc_proxies[hcommon._sc_proxy_index] if (
        0 <= hcommon._sc_proxy_index < len(hcommon._sc_proxies)
    ) else None
    active_yt_url = hcommon._sc_proxies[hcommon._yt_proxy_index] if (
        0 <= hcommon._yt_proxy_index < len(hcommon._sc_proxies)
    ) else None
    result = []
    for i, p in enumerate(proxies):
        result.append({
            **p,
            "url": _mask_proxy_url(p["url"]),
            "active": p["url"] == active_sc_url,
            "active_sc": p["url"] == active_sc_url,
            "active_yt": p["url"] == active_yt_url,
            "index": i,
        })
    return aiohttp_web.Response(text=json.dumps(result), content_type="application/json")


async def _api_proxies_add(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
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
    log.info("SC proxy added via dashboard: %s", _mask_proxy_url(url))

    # Notify admin
    try:
        await bot.send_message(
            settings.ADMIN_ID,
            f"🔌 <b>Новый SC прокси добавлен через дашборд</b>\n\n"
            f"<code>{_mask_proxy_url(url)}</code>\n"
            f"Метка: <b>{_proxy_short_label(url)}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning("Failed to notify admin about new proxy: %s", e)

    return aiohttp_web.Response(text=json.dumps(entry), content_type="application/json", status=201)


async def _api_proxies_delete(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
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
    log.info("SC proxy removed via dashboard: %s", _mask_proxy_url(removed["url"]))

    return aiohttp_web.Response(text=json.dumps({"removed": _mask_proxy_url(removed["url"])}), content_type="application/json")


async def _api_proxies_test(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
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


# ── Moderation API — Bans ─────────────────────────────────────────────────

async def _api_bans_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_banned_users()
    return aiohttp_web.Response(
        text=json.dumps(data, default=str), content_type="application/json"
    )


async def _api_bans_add(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    try:
        body     = await request.json()
        username = (body.get("username") or "").strip().lstrip("@") or None
        reason   = (body.get("reason") or "").strip() or None
        try:
            user_id = int(body.get("user_id") or 0)
        except (ValueError, TypeError):
            user_id = 0
    except Exception:
        return aiohttp_web.Response(status=400, text="Invalid JSON")

    if user_id <= 0:
        if not username:
            return aiohttp_web.Response(status=422, text="Укажите user_id или username")
        resolved = await db.resolve_user_id_by_username(username)
        if not resolved:
            return aiohttp_web.Response(
                status=404,
                text=f"Пользователь @{username} не найден в базе. Используйте числовой ID.",
            )
        user_id = resolved

    await db.ban_user(user_id, username, reason)
    log.info("Dashboard: banned user %d (@%s)", user_id, username)
    return aiohttp_web.Response(
        text=json.dumps({"user_id": user_id, "username": username, "reason": reason}),
        content_type="application/json", status=201,
    )


async def _api_bans_delete(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    try:
        user_id = int(request.match_info["user_id"])
    except (KeyError, ValueError):
        return aiohttp_web.Response(status=400, text="Invalid user_id")
    await db.unban_user(user_id)
    log.info("Dashboard: unbanned user %d", user_id)
    return aiohttp_web.Response(
        text=json.dumps({"unbanned": user_id}), content_type="application/json"
    )


# ── Moderation API — Batch Whitelist ──────────────────────────────────────

async def _api_batch_wl_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_batch_whitelist()
    return aiohttp_web.Response(
        text=json.dumps(data, default=str), content_type="application/json"
    )


async def _api_batch_wl_add(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    try:
        body     = await request.json()
        username = (body.get("username") or "").strip().lstrip("@") or None
        try:
            user_id = int(body.get("user_id") or 0)
        except (ValueError, TypeError):
            user_id = 0
    except Exception:
        return aiohttp_web.Response(status=400, text="Invalid JSON")

    if user_id <= 0:
        if not username:
            return aiohttp_web.Response(status=422, text="Укажите username")
        resolved = await db.resolve_user_id_by_username(username)
        if not resolved:
            return aiohttp_web.Response(
                status=404,
                text=f"Пользователь @{username} не найден в базе. Сначала пользователь должен написать боту.",
            )
        user_id = resolved

    await db.add_batch_whitelist(user_id, username)
    log.info("Dashboard: added batch whitelist user %d (@%s)", user_id, username)
    return aiohttp_web.Response(
        text=json.dumps({"user_id": user_id, "username": username}),
        content_type="application/json", status=201,
    )


async def _api_batch_wl_delete(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    try:
        user_id = int(request.match_info["user_id"])
    except (KeyError, ValueError):
        return aiohttp_web.Response(status=400, text="Invalid user_id")
    await db.remove_batch_whitelist(user_id)
    log.info("Dashboard: removed batch whitelist user %d", user_id)
    return aiohttp_web.Response(
        text=json.dumps({"removed": user_id}), content_type="application/json"
    )


# ── Moderation API — Batch Access Requests ────────────────────────────────

async def _api_batch_requests_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    data = await db.get_pending_requests()
    return aiohttp_web.Response(
        text=json.dumps(data, default=str), content_type="application/json"
    )


async def _api_batch_requests_resolve(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    action = request.match_info.get("action", "")
    if action not in ("approve", "reject"):
        return aiohttp_web.Response(status=400, text="action must be approve or reject")
    try:
        req_id = int(request.match_info["req_id"])
    except (KeyError, ValueError):
        return aiohttp_web.Response(status=400, text="Invalid req_id")

    req = await db.get_request_by_id(req_id)
    if not req:
        return aiohttp_web.Response(status=404, text="Request not found")
    if req["status"] != "pending":
        return aiohttp_web.Response(status=409, text="Request already resolved")

    status = "approved" if action == "approve" else "rejected"
    await db.resolve_batch_request(req_id, status)

    bot: Bot = request.app["bot"]
    name = f"@{req['username']}" if req.get("username") else f"ID {req['user_id']}"

    if action == "approve":
        await db.add_batch_whitelist(req["user_id"], req.get("username"))
        try:
            await bot.send_message(
                req["user_id"],
                "✅ Твой запрос на batch-скачивание <b>одобрен</b>! Можешь начинать.",
                parse_mode="HTML",
            )
        except Exception as e:
            log.warning("Failed to notify user %d on approve: %s", req["user_id"], e)
    else:
        try:
            await bot.send_message(
                req["user_id"],
                "❌ Твой запрос на batch-скачивание был <b>отклонён</b>.",
                parse_mode="HTML",
            )
        except Exception as e:
            log.warning("Failed to notify user %d on reject: %s", req["user_id"], e)

    log.info("Dashboard: batch request %d %sd — user %s", req_id, action, name)
    return aiohttp_web.Response(
        text=json.dumps({"request_id": req_id, "status": status, "user": name}),
        content_type="application/json",
    )

# Cookie API handlers

async def _api_cookies_get(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    result = {}
    for key, path in [("sc", settings.SC_COOKIE_FILE), ("yt", settings.YT_COOKIE_FILE)]:
        if not path:
            result[key] = {"path": None, "content": None, "mtime": None, "error": "Not configured"}
            continue
        try:
            mtime = None
            content_text = ""
            if os.path.exists(path):
                mtime = datetime.fromtimestamp(
                    os.path.getmtime(path), tz=timezone.utc
                ).isoformat(timespec="seconds")
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    content_text = fh.read()
            result[key] = {"path": path, "content": content_text, "mtime": mtime}
        except Exception as e:
            result[key] = {"path": path, "content": None, "mtime": None, "error": str(e)[:120]}
    return aiohttp_web.Response(text=json.dumps(result), content_type="application/json")


async def _api_cookies_post(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)
    if not _check_csrf(request):
        return aiohttp_web.Response(status=403, text='{"error":"CSRF check failed"}', content_type="application/json")
    source = request.match_info["source"]
    if source not in ("sc", "yt"):
        return aiohttp_web.Response(status=404)
    path = settings.SC_COOKIE_FILE if source == "sc" else settings.YT_COOKIE_FILE
    if not path:
        return aiohttp_web.Response(status=422, text=f"{source.upper()}_COOKIE_FILE is not configured")
    try:
        body = await request.json()
        new_content = body.get("content", "")
    except Exception:
        return aiohttp_web.Response(status=400, text="Invalid JSON")
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_content)
        mtime = datetime.fromtimestamp(
            os.path.getmtime(path), tz=timezone.utc
        ).isoformat(timespec="seconds")
        log.info("Cookie file updated via dashboard: source=%s path=%s", source, path)
    except Exception as e:
        return aiohttp_web.Response(status=500, text=f"Failed to write cookie file: {e}")
    return aiohttp_web.Response(
        text=json.dumps({"ok": True, "path": path, "mtime": mtime}),
        content_type="application/json",
    )



# ── System API ────────────────────────────────────────────────────────────

async def _api_system(request: aiohttp_web.Request) -> aiohttp_web.Response:
    if not _check_auth(request):
        return aiohttp_web.Response(status=401)

    db_stats = await db.get_system_stats()

    redis = request.app.get("redis")
    redis_info: dict = {}
    if redis:
        try:
            info = await redis.info("memory")
            server_info = await redis.info("server")
            stats_info  = await redis.info("stats")
            keycount = await redis.dbsize()
            redis_info = {
                "used_memory_human": info.get("used_memory_human", "?"),
                "used_memory_peak_human": info.get("used_memory_peak_human", "?"),
                "uptime_in_seconds": server_info.get("uptime_in_seconds", 0),
                "redis_version": server_info.get("redis_version", "?"),
                "keyspace_hits":   stats_info.get("keyspace_hits", 0),
                "keyspace_misses": stats_info.get("keyspace_misses", 0),
                "total_keys": keycount,
            }
        except Exception as e:
            redis_info = {"error": str(e)[:120]}

    uptime_seconds = int(time.time() - _startup_time) if _startup_time else 0

    return aiohttp_web.Response(
        text=json.dumps({
            "db":     db_stats,
            "redis":  redis_info,
            "uptime_seconds": uptime_seconds,
        }, default=str),
        content_type="application/json",
    )


async def _daily_digest_task(bot: Bot) -> None:
    """Send a daily stats digest to admin at 09:00 MSK."""
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
    if not settings.ADMIN_ID:
        return
    while True:
        now = datetime.now(MSK)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            s = await db.get_daily_digest_stats()
            if not s:
                continue
            date_str = datetime.now(MSK).strftime("%d.%m.%Y")
            top_lines = ""
            if s.get("top_users"):
                top_lines = "\n\n<b>Топ пользователей:</b>\n"
                for i, u in enumerate(s["top_users"], 1):
                    uname = f"@{u['username']}" if u["username"] else "?"
                    top_lines += f"  {i}. {uname} — {u['tracks']} треков\n"
            text = (
                f"📊 <b>Дайджест за {date_str}</b>\n\n"
                f"👤 Пользователей: <b>{s['users']}</b>\n"
                f"🎵 Скачано треков: <b>{s['tracks']}</b>\n"
                f"📦 Батч-сессий: <b>{s['batches']}</b>\n"
                f"📋 Экспортировано: <b>{s['exported']}</b>\n"
                f"❌ Ошибок: <b>{s['errors']}</b>"
                + top_lines
            )
            await bot.send_message(settings.ADMIN_ID, text, parse_mode="HTML")
        except Exception as e:
            log.warning("daily_digest_task failed: %s", e)


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
    global _startup_time
    _startup_time = time.time()
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
        api=TelegramAPIServer.from_base(settings.LOCAL_API_URL, is_local=True) if settings.LOCAL_API_URL else PRODUCTION,
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
            web_app.router.add_get("/api/cache",        _api_cache)
            web_app.router.add_get("/api/events",       _api_events)
            web_app.router.add_get("/api/chart",        _api_chart)
            web_app.router.add_get("/api/batch_live",   _api_batch_live)
            web_app.router.add_get("/api/users",        _api_users)
            web_app.router.add_get("/api/renames",      _api_renames)
            web_app.router.add_get("/api/ws",           _ws_handler)
            web_app.router.add_get("/api/network-status",       _api_network_status)
            web_app.router.add_get("/api/proxies",              _api_proxies_get)
            web_app.router.add_post("/api/proxies",             _api_proxies_add)
            web_app.router.add_delete("/api/proxies/{idx}",     _api_proxies_delete)
            web_app.router.add_post("/api/proxies/{idx}/test",  _api_proxies_test)
            # Moderation — bans
            web_app.router.add_get("/api/bans",                 _api_bans_get)
            web_app.router.add_post("/api/bans",                _api_bans_add)
            web_app.router.add_delete("/api/bans/{user_id}",    _api_bans_delete)
            # Moderation — batch whitelist
            web_app.router.add_get("/api/batch-whitelist",              _api_batch_wl_get)
            web_app.router.add_post("/api/batch-whitelist",             _api_batch_wl_add)
            web_app.router.add_delete("/api/batch-whitelist/{user_id}", _api_batch_wl_delete)
            # Moderation — batch access requests
            web_app.router.add_get("/api/batch-requests",                          _api_batch_requests_get)
            web_app.router.add_post("/api/batch-requests/{req_id}/{action}",       _api_batch_requests_resolve)
            # Cookie file management
            web_app.router.add_get("/api/cookies",              _api_cookies_get)
            web_app.router.add_post("/api/cookies/{source}",    _api_cookies_post)
            # System stats
            web_app.router.add_get("/api/system",               _api_system)
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
    if db._pool:
        await db.cleanup_old_batch_live()
    asyncio.create_task(_check_sc_cookies_task(bot))
    asyncio.create_task(_daily_digest_task(bot))
    log.info("Bot started")
    if settings.WEBHOOK_URL:
        await asyncio.Event().wait()
    else:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "inline_query", "chosen_inline_result"])


if __name__ == "__main__":
    asyncio.run(main())
