"""Shared helpers, constants and globals used across all handler modules."""
import asyncio
import logging
import re
import time

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from rapidfuzz import fuzz

from config import settings
from utils import db
from bot.tracker import set_active_msg

log = logging.getLogger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

# SC download toggle: when False, non-admin users cannot start SC downloads
sc_downloads_enabled: bool = True

# Admin SC-error notification cooldown (prevents spam on consecutive failures)
_sc_error_last_notified: float = 0.0
_SC_ERROR_NOTIFY_COOLDOWN: int = 600  # seconds (10 min)

# ── SC proxy rotation state ───────────────────────────────────────────────────
# List of fallback proxies for SC downloads (from SC_PROXIES env var)
_sc_proxies: list[str] = [p.strip() for p in settings.SC_PROXIES.split(",") if p.strip()] if settings.SC_PROXIES else []
# -1 = main server IP, 0..N-1 = index into _sc_proxies
_sc_proxy_index: int = -1
# Lock to avoid concurrent rotation from parallel requests
_sc_proxy_rotate_lock = asyncio.Lock()
# Detected public IP of the server (populated at startup, falls back to SC_SERVER_IP or generic label)
_detected_server_ip: str = ""

# ── Main IP recovery ──────────────────────────────────────────────────────────
# How often to probe main IP while on a proxy (seconds)
MAIN_IP_CHECK_INTERVAL_SEC: int = 1800  # 30 minutes
# Background task handle; None when not running
_recovery_task: asyncio.Task | None = None
# Timestamps for "next check in N sec" calculation
_recovery_started_at: float | None = None
_last_recovery_check_at: float | None = None


async def detect_and_store_server_ip() -> None:
    """Fetch the server's public IP once at startup and cache it in _detected_server_ip."""
    global _detected_server_ip
    # Static override takes priority
    if settings.SC_SERVER_IP:
        _detected_server_ip = settings.SC_SERVER_IP
        log.info("Server IP (from config): %s", _detected_server_ip)
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                ip = (await resp.text()).strip()
                if ip:
                    _detected_server_ip = ip
                    log.info("Server public IP (auto-detected): %s", ip)
    except Exception as e:
        log.warning("Could not auto-detect server public IP: %s", e)


def get_server_ip_label() -> str:
    """Return the server IP string for use in admin notifications."""
    return _detected_server_ip or "основной IP сервера"

# Cancel events for SC batch downloads keyed by user_id
_cancel_events: dict[int, asyncio.Event] = {}

# Global semaphore limiting concurrent SC batch downloads
_batch_semaphore = asyncio.Semaphore(settings.SC_MAX_BATCH_DOWNLOADS)

# Spotify OAuth codes received via callback server {user_id: code}
_pending_spotify_codes: dict[int, str] = {}


class _BatchQueueItem:
    """Holds everything needed to start a queued batch download."""
    __slots__ = ('user_id', 'username', 'chat_id', 'bot', 'state', 'tracks', 'start_idx')

    def __init__(self, user_id: int, username, chat_id: int, bot, state, tracks: list, start_idx: int):
        self.user_id = user_id
        self.username = username
        self.chat_id = chat_id
        self.bot = bot
        self.state = state
        self.tracks = tracks
        self.start_idx = start_idx


# Queue of users waiting for a free batch download slot
_batch_queue: list[_BatchQueueItem] = []

# ── Text constants ────────────────────────────────────────────────────────────

_TOKEN_GUIDE = (
    '<tg-emoji emoji-id="6037243349675544634">🔑</tg-emoji> Для доступа к твоей музыке нужна авторизация в Яндексе.\n\n'
    "1. Нажми кнопку <b>«Войти через Яндекс»</b> ниже\n"
    "2. Войди в свой аккаунт Яндекса\n"
    "3. Скопируй значение <code>access_token</code> из адресной строки\n"
    "   (часть URL после <code>#access_token=</code> и до первого <code>&amp;</code>)\n"
    "4. Отправь токен сюда"
)

_EXPORT_MENU_TEXT = "Что экспортируем?"

_RETENTION_TEXT = (
    '<tg-emoji emoji-id="5345906554510012647">💾</tg-emoji> <b>Как хранить токен авторизации?</b>\n\n'
    '<tg-emoji emoji-id="5983150113483134607">⚡</tg-emoji> <b>На весь сеанс</b>\n'
    "  + Можно экспортировать несколько раз без повторного входа\n"
    "  − Токен остаётся в оперативной памяти бота до перезапуска или /start\n\n"
    '<tg-emoji emoji-id="6037243349675544634">🔒</tg-emoji> <b>Только один экспорт</b>\n'
    "  + Токен удаляется сразу после выгрузки файла\n"
    "  − Придётся авторизоваться заново при каждом экспорте\n\n"
    "<i>В обоих случаях токен хранится только в RAM — никакой записи на диск.</i>"
)

_SC_MENU_TEXT = (
    '<tg-emoji emoji-id="5778672437122045013">☁️</tg-emoji> <b>Скачать MP3</b>\n\n'
    '<tg-emoji emoji-id="6037397706505195857">🔍</tg-emoji> <b>Найти трек</b> — поиск по названию\n'
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>По ссылке</b> — одиночный трек по ссылке SoundCloud / YouTube\n\n'
    '<i>Для плейлистов и альбомов используй раздел «Плейлист / Альбом по ссылке»</i>'
)

_SC_URL_TEXT = (
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>Скачать трек по ссылке</b>\n\n'
    'Поддерживаются одиночные треки:\n'
    '• <b>SoundCloud</b> — ссылка на трек\n'
    '• <b>YouTube</b> — ссылка на видео\n\n'
    '<i>Для плейлистов и альбомов используй раздел «Плейлист / Альбом по ссылке».</i>\n\n'
    'Отправь ссылку:'
)

_SC_URL_PLAYLIST_TEXT = (
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>Плейлист или альбом по ссылке</b>\n\n'
    'Поддерживаются:\n'
    '• <b>SoundCloud</b> — трек, плейлист или альбом\n'
    '• <b>YouTube</b> — трек или плейлист\n\n'
    'Отправь ссылку:'
)

_YT_PLAYLIST_TEXT = (
    '🎬 <b>YouTube — плейлист по ссылке</b>\n\n'
    'Поддерживаются:\n'
    '• <code>youtube.com/playlist?list=...</code>\n'
    '• <code>music.youtube.com/playlist?list=...</code>\n'
    '• Ссылки на отдельные треки тоже работают\n\n'
    'Отправь ссылку:'
)

_YMS_INPUT_TEXT = (
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>Отправь ссылку на плейлист или альбом</b>\n\n'
    '<b>Поддерживаемые форматы:</b>\n'
    '• <code>music.yandex.ru/album/НОМЕР</code>\n'
    '• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n'
    '• <code>music.yandex.ru/playlists/lk.UUID</code> (кнопка «Поделиться»)\n\n'
    '<b>Или embed-код плейлиста из приложения:</b>\n'
    '1. Открой плейлист → <b>···</b> → «Поделиться»\n'
    '2. Выбери <b>«HTML-код для встраивания»</b> и отправь сюда'
)

_SPOTIFY_MENU_TEXT = (
    "🎵 <b>Spotify</b>\n\n"
    "🔗 <b>Плейлист по ссылке</b> — вставь ссылку на любой публичный плейлист\n"
    "❤️ <b>Мои лайки</b> — экспорт или скачивание сохранённых треков"
)

_SPOTIFY_PLAYLIST_TEXT = (
    "🔗 Отправь ссылку на плейлист или альбом Spotify:\n\n"
    "<i>Плейлист: https://open.spotify.com/playlist/37i9dQZF1DX...</i>\n"
    "<i>Альбом: https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy...</i>"
)

_SPOTIFY_AUTH_TEXT = (
    "❤️ <b>Авторизация Spotify</b>\n\n"
    "1. Нажми кнопку <b>«Войти через Spotify»</b> ниже\n"
    "2. Войди в аккаунт и разреши доступ\n"
    "3. Вернись в Telegram — бот автоматически получит доступ и загрузит лайки"
)

_FAQ_TEXT = (
    "❓ <b>FAQ</b>\n\n"
    "<b>Что умеет бот?</b>\n"
    "• Экспорт лайков и плейлистов из Яндекс Музыки в .txt / .csv\n"
    "• Экспорт плейлистов и альбомов из Spotify в .txt / .csv\n"
    "• Скачивание треков с SoundCloud и YouTube (поиск, по ссылке)\n"
    "• Пакетная загрузка плейлиста или альбома целиком\n\n"
    "🔑 <b>Хранение токенов</b>\n"
    "Токены авторизации (Яндекс, Spotify) хранятся только в оперативной памяти "
    "и удаляются при перезапуске бота или команде /start. На диск не записываются.\n\n"
    "🟢 <b>Spotify: какой доступ запрашивается?</b>\n"
    "Только чтение лайкнутых треков (<code>user-library-read</code>). "
    "Бот не может изменять библиотеку или получать доступ к другим данным аккаунта.\n\n"
    "📁 <b>Временные файлы</b>\n"
    "Скачанные MP3 и сгенерированные .txt / .csv хранятся на сервере только до момента "
    "отправки. После этого автоматически удаляются.\n\n"
    "🗃 <b>База данных</b>\n"
    "Хранится только анонимная статистика (тип действия, счётчик) и список доступа к "
    "пакетной загрузке. Треки, токены и личные данные не сохраняются."
)

_BATCH_ACCESS_TEXT = (
    "🔒 <b>Скачивание плейлистов — функция с ограниченным доступом.</b>\n\n"
    "Она бесплатна, но нуждается в ручной модерации. Отправь запрос администрации, "
    "и она очень быстро его рассмотрит."
)

_BATCH_ACCESS_PENDING_TEXT = (
    "⏳ <b>Запрос уже отправлен.</b>\n\n"
    "Ожидай — администратор рассмотрит его в ближайшее время."
)

_RE_IFRAME_PLAYLIST = re.compile(
    r'music\.yandex\.(ru|com)/iframe/playlist/([^/"?\s]+)/(\d+)'
)

# ── Helper functions ──────────────────────────────────────────────────────────

def _parse_ym_share(text: str) -> str | None:
    """Extract a normalised YM playlist URL from iframe embed code or raw link."""
    iframe_match = _RE_IFRAME_PLAYLIST.search(text)
    if iframe_match:
        user = iframe_match.group(2)
        kind = iframe_match.group(3)
        return f"users/{user}/playlists/{kind}"

    text = text.strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    for domain in ("music.yandex.ru/", "music.yandex.com/"):
        if text.startswith(domain):
            path = text[len(domain):]
            path = path.split("?")[0].split("#")[0]
            if (path.startswith("users/")
                    or path.startswith("playlists/lk.")
                    or path.startswith("album/")):
                return path
    return None


def _make_cache_key(artist: str, title: str) -> str:
    """Normalised lookup key for the track_cache table."""
    s = f"{artist} {title}".lower()
    return re.sub(r'[^\w\s]', '', s).strip()


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    """Return a text progress bar, e.g. '████░░░░░░ 4/10'."""
    filled = round(width * current / total) if total else 0
    return f"{'█' * filled}{'░' * (width - filled)} {current}/{total}"


def _filter_by_artist(tracks: list[dict], query: str, threshold: int = 70) -> list[dict]:
    """Return tracks where any of the comma-separated artists fuzzy-matches query."""
    q = query.strip().lower()
    matched = []
    for t in tracks:
        artists = [a.strip().lower() for a in t.get("artist", "").split(",")]
        if any(fuzz.partial_ratio(q, a) >= threshold for a in artists):
            matched.append(t)
    return matched


def _get_user_info(event: Message | CallbackQuery) -> tuple[int, str | None]:
    user = event.from_user
    return user.id, (user.username or None) if user else (0, None)


async def notify_admin_sc_error(bot, user_id: int, username: str | None, context: str) -> None:
    """Send admin a one-line SC error alert with a disable-toggle button.

    Fires at most once per _SC_ERROR_NOTIFY_COOLDOWN seconds to avoid flooding.
    """
    global _sc_error_last_notified
    if settings.ADMIN_ID == 0:
        return
    now = time.monotonic()
    if now - _sc_error_last_notified < _SC_ERROR_NOTIFY_COOLDOWN:
        return
    _sc_error_last_notified = now

    name = f"@{username}" if username else f"#{user_id}"
    status = "включено" if sc_downloads_enabled else "уже выключено"
    text = (
        f"⚠️ <b>SC: ошибки при скачивании</b>\n\n"
        f"👤 {name} (<code>{user_id}</code>)\n"
        f"📋 {context}\n\n"
        f"Текущее состояние SC: <b>{status}</b>\n"
        f"Если IP заблокирован — выключи SC для пользователей."
    )
    if sc_downloads_enabled:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔇 Выключить SC для пользователей", callback_data="admin:sc_disable"),
        ]])
    else:
        kb = None
    try:
        await bot.send_message(settings.ADMIN_ID, text, parse_mode="HTML", reply_markup=kb)
    except Exception as exc:
        log.warning("Failed to notify admin about SC error: %s", exc)


def get_network_status() -> dict:
    """Return current SC network routing status for display in dashboard / admin."""
    on_proxy = _sc_proxy_index >= 0
    active_proxy = _sc_proxies[_sc_proxy_index] if on_proxy and _sc_proxy_index < len(_sc_proxies) else None
    recovery_running = bool(_recovery_task and not _recovery_task.done())

    next_check_in: int | None = None
    if recovery_running:
        ref = _last_recovery_check_at or _recovery_started_at
        if ref is not None:
            next_check_in = max(0, int(ref + MAIN_IP_CHECK_INTERVAL_SEC - time.time()))

    return {
        "on_proxy": on_proxy,
        "main_ip": get_server_ip_label(),
        "active_proxy": active_proxy,
        "recovery_running": recovery_running,
        "next_check_in": next_check_in,
        "check_interval": MAIN_IP_CHECK_INTERVAL_SEC,
    }


async def _recovery_check_loop(bot) -> None:
    """Periodically probe main IP and switch back when it's no longer banned."""
    global _sc_proxy_index, _last_recovery_check_at
    from core import sc_downloader

    while True:
        await asyncio.sleep(MAIN_IP_CHECK_INTERVAL_SEC)

        if _sc_proxy_index == -1:
            break  # already on main IP (e.g. all proxies exhausted or manual reset)

        _last_recovery_check_at = time.time()
        log.info("SC main IP recovery check: probing…")
        try:
            ok = await sc_downloader.check_main_ip()
        except Exception as exc:
            log.warning("SC main IP recovery check error: %s", exc)
            continue

        if not ok:
            log.info("SC main IP recovery check: still banned, staying on proxy")
            continue

        async with _sc_proxy_rotate_lock:
            if _sc_proxy_index == -1:
                break  # switched back by someone else while we were probing
            old_proxy = _sc_proxies[_sc_proxy_index] if 0 <= _sc_proxy_index < len(_sc_proxies) else "unknown"
            _sc_proxy_index = -1
            sc_downloader.set_active_proxy("")
            log.info("SC main IP recovery: main IP unblocked, switched back from %s", old_proxy)

        if settings.ADMIN_ID:
            try:
                await bot.send_message(
                    settings.ADMIN_ID,
                    f"✅ <b>SC: основной IP разблокирован</b>\n\n"
                    f"Прокси <code>{old_proxy}</code> больше не нужен.\n"
                    f"Возвращаюсь на основной IP сервера.",
                    parse_mode="HTML",
                )
            except Exception as exc:
                log.warning("Failed to notify admin (main IP recovery): %s", exc)
        break


def _start_recovery_check(bot) -> None:
    """Start the main IP recovery background task if not already running."""
    global _recovery_task, _recovery_started_at, _last_recovery_check_at
    if _recovery_task and not _recovery_task.done():
        return
    _recovery_started_at = time.time()
    _last_recovery_check_at = None
    _recovery_task = asyncio.create_task(_recovery_check_loop(bot))
    log.info("SC main IP recovery check task started (interval=%ds)", MAIN_IP_CHECK_INTERVAL_SEC)


def cancel_recovery_check() -> None:
    """Cancel the main IP recovery task (call when returning to main IP manually)."""
    global _recovery_task, _recovery_started_at, _last_recovery_check_at
    if _recovery_task and not _recovery_task.done():
        _recovery_task.cancel()
        log.info("SC main IP recovery check task cancelled")
    _recovery_task = None
    _recovery_started_at = None
    _last_recovery_check_at = None


async def rotate_sc_proxy(bot) -> bool:
    """Switch to the next SC download proxy and notify admin.

    Returns True if switched to a new proxy, False if all proxies were exhausted
    and we reverted to the main IP.
    """
    global _sc_proxy_index
    from core import sc_downloader

    async with _sc_proxy_rotate_lock:
        server_ip = get_server_ip_label()
        old_index = _sc_proxy_index

        if not _sc_proxies:
            # No proxies configured — just notify admin and stay on main IP
            if settings.ADMIN_ID:
                try:
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"⚠️ <b>SC: ошибка с {server_ip}</b>\n\n"
                        f"Прокси не настроены (<code>SC_PROXIES</code> пуст).\n"
                        f"Скачивание с SC может быть недоступно.",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.warning("Failed to notify admin (no proxies): %s", exc)
            return False

        if old_index == -1:
            # Currently on main IP → switch to first proxy
            _sc_proxy_index = 0
            new_proxy = _sc_proxies[0]
            sc_downloader.set_active_proxy(new_proxy)
            log.warning("SC proxy rotation: main IP → proxy[0] = %s", new_proxy)
            if settings.ADMIN_ID:
                try:
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"⚠️ <b>SC: скачивание с {server_ip} не удалось</b>\n\n"
                        f"Похоже на бан по IP. Подключаю прокси:\n"
                        f"<code>{new_proxy}</code>",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.warning("Failed to notify admin (proxy switch): %s", exc)
            _start_recovery_check(bot)
            return True

        next_index = old_index + 1
        if next_index < len(_sc_proxies):
            # Switch to next proxy
            _sc_proxy_index = next_index
            old_proxy = _sc_proxies[old_index]
            new_proxy = _sc_proxies[next_index]
            sc_downloader.set_active_proxy(new_proxy)
            log.warning("SC proxy rotation: proxy[%d] → proxy[%d] = %s", old_index, next_index, new_proxy)
            if settings.ADMIN_ID:
                try:
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"⚠️ <b>SC: прокси перестал работать</b>\n\n"
                        f"<code>{old_proxy}</code> — ошибка (бан?)\n\n"
                        f"Переключаю на следующее прокси:\n"
                        f"<code>{new_proxy}</code>",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.warning("Failed to notify admin (next proxy): %s", exc)
            return True
        else:
            # All proxies exhausted — revert to main IP
            _sc_proxy_index = -1
            old_proxy = _sc_proxies[old_index]
            sc_downloader.set_active_proxy("")
            log.warning("SC proxy rotation: all proxies exhausted, reverting to main IP")
            if settings.ADMIN_ID:
                try:
                    await bot.send_message(
                        settings.ADMIN_ID,
                        f"🔴 <b>SC: все прокси исчерпаны</b>\n\n"
                        f"<code>{old_proxy}</code> — тоже не работает.\n\n"
                        f"Возвращаюсь к {server_ip}.\n"
                        f"Скачивание с SC может быть недоступно.",
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    log.warning("Failed to notify admin (proxies exhausted): %s", exc)
            cancel_recovery_check()
            return False


async def download_with_proxy_rotation(url: str, user_id: int, bot) -> tuple[str, dict]:
    """Download from SC with automatic proxy rotation on IP ban errors.

    Tries the current proxy/IP first. On SCBanError rotates to the next proxy
    and retries. If all proxies are exhausted, raises the last SCBanError.
    """
    from core import sc_downloader
    from core.sc_downloader import SCBanError

    # max attempts = initial + one per proxy (rotation eventually returns False)
    max_attempts = len(_sc_proxies) + 2
    rotated = False
    for attempt in range(max_attempts):
        try:
            result = await sc_downloader.download(url, user_id)
            if rotated:
                proxy_label = sc_downloader.get_active_proxy() or get_server_ip_label()
                await _notify_proxy_result(bot, proxy_label, success=True,
                                           detail="Скачивание прошло успешно.")
            return result
        except SCBanError:
            rotated = True
            had_more = await rotate_sc_proxy(bot)
            if not had_more:
                raise  # all proxies tried, nothing left
        except Exception:
            raise  # non-ban errors propagate immediately
    # Should not be reached, but satisfy type-checker
    return await sc_downloader.download(url, user_id)


async def _notify_proxy_result(bot, proxy_label: str, success: bool, detail: str = "") -> None:
    """Send admin notification about proxy connection result after rotation."""
    if not settings.ADMIN_ID:
        return
    if success:
        text = (
            f"✅ <b>SC: прокси работает</b>\n\n"
            f"Прокси <code>{proxy_label}</code> подключён успешно.\n"
            + (f"{detail}" if detail else "")
        )
    else:
        text = (
            f"❌ <b>SC: прокси тоже не помог</b>\n\n"
            f"Прокси <code>{proxy_label}</code> подключён, но SC всё равно не отвечает.\n"
            + (f"{detail}" if detail else "")
        )
    try:
        await bot.send_message(settings.ADMIN_ID, text, parse_mode="HTML")
    except Exception as exc:
        log.warning("Failed to send proxy result notification: %s", exc)


async def search_with_proxy_rotation(query: str, max_results: int, bot) -> list:
    """Search SC with automatic proxy rotation on IP ban errors.

    Tries the current proxy/IP first. On SCBanError or empty result after rotation,
    switches to the next proxy and retries. If all proxies are exhausted, returns [].
    Sends admin notification on success or when all proxies are exhausted.
    """
    from core import sc_downloader
    from core.sc_downloader import SCBanError

    max_attempts = len(_sc_proxies) + 2
    rotated = False
    for attempt in range(max_attempts):
        try:
            results = await sc_downloader.search(query, max_results)
        except SCBanError:
            rotated = True
            had_more = await rotate_sc_proxy(bot)
            if not had_more:
                raise
            continue
        except Exception:
            raise

        if results:
            if rotated:
                proxy_label = sc_downloader.get_active_proxy() or get_server_ip_label()
                await _notify_proxy_result(bot, proxy_label, success=True,
                                           detail=f"Поиск вернул {len(results)} результатов.")
            return results

        # Empty results
        if not rotated:
            return []  # genuinely no results, no IP issue

        # Rotated but proxy also returned empty — likely blocked too, try next proxy
        had_more = await rotate_sc_proxy(bot)
        if not had_more:
            proxy_label = sc_downloader.get_active_proxy() or get_server_ip_label()
            await _notify_proxy_result(bot, proxy_label, success=False,
                                       detail="Все прокси проверены — результатов нет.")
            return []
        # else: continue loop with next proxy

    return []


async def _show_batch_access_page(call: CallbackQuery, back_cb: str, use_answer: bool = False) -> None:
    """Show access request page or 'already pending' page depending on user's request status.

    use_answer=True sends a new message instead of editing (needed for document/caption messages).
    """
    from bot.keyboards import batch_access_request_keyboard, batch_access_pending_keyboard

    has_pending = await db.get_pending_request(call.from_user.id) is not None

    text = _BATCH_ACCESS_PENDING_TEXT if has_pending else _BATCH_ACCESS_TEXT
    kb = batch_access_pending_keyboard(back_cb) if has_pending else batch_access_request_keyboard(back_cb)

    if use_answer:
        msg = await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
        set_active_msg(call.from_user.id, msg.message_id)
    else:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
