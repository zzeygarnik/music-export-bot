"""Shared helpers, constants and globals used across all handler modules."""
import asyncio
import logging
import re

from aiogram.types import Message, CallbackQuery
from rapidfuzz import fuzz

from config import settings

log = logging.getLogger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

# Cancel events for SC batch downloads keyed by user_id
_cancel_events: dict[int, asyncio.Event] = {}

# Global semaphore limiting concurrent SC batch downloads
_batch_semaphore = asyncio.Semaphore(settings.SC_MAX_BATCH_DOWNLOADS)

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
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>По ссылке</b> — трек или плейлист по ссылке SoundCloud / YouTube\n'
    '<tg-emoji emoji-id="6039802767931871481">📥</tg-emoji> <b>Скачать плейлист из Яндекс Музыки</b> — выгрузить плейлист YM и скачать через SoundCloud'
)

_SC_URL_TEXT = (
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>Скачать по ссылке</b>\n\n'
    'Поддерживаются:\n'
    '• <b>SoundCloud</b> — трек или плейлист\n'
    '• <b>YouTube</b> — трек или плейлист\n\n'
    'Отправь ссылку:'
)

_YMS_INPUT_TEXT = (
    '<tg-emoji emoji-id="6042011682497106307">🔗</tg-emoji> <b>Отправь ссылку или embed-код плейлиста</b>\n\n'
    '<b>Как получить embed-код в приложении Яндекс Музыки:</b>\n'
    '1. Открой нужный плейлист\n'
    '2. Нажми <b>···</b> (три точки) → «Поделиться»\n'
    '3. Выбери <b>«HTML-код для встраивания»</b>\n'
    '4. Скопируй и отправь сюда\n\n'
    '<b>Также принимаются прямые ссылки:</b>\n'
    '• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n'
    '• <code>music.yandex.ru/playlists/lk.UUID</code> (кнопка «Поделиться»)'
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
            if path.startswith("users/") or path.startswith("playlists/lk."):
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
