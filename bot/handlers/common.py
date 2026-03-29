"""Shared helpers, constants and globals used across all handler modules."""
import asyncio
import logging
import re

from aiogram.types import Message, CallbackQuery
from rapidfuzz import fuzz

from config import settings
from utils import db

log = logging.getLogger(__name__)

# ── Globals ───────────────────────────────────────────────────────────────────

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


async def _show_batch_access_page(call: CallbackQuery, back_cb: str, use_answer: bool = False) -> None:
    """Show access request page or 'already pending' page depending on user's request status.

    use_answer=True sends a new message instead of editing (needed for document/caption messages).
    """
    from bot.keyboards import batch_access_request_keyboard, batch_access_pending_keyboard

    has_pending = db.get_pending_request(call.from_user.id) is not None

    text = _BATCH_ACCESS_PENDING_TEXT if has_pending else _BATCH_ACCESS_TEXT
    kb = batch_access_pending_keyboard(back_cb) if has_pending else batch_access_request_keyboard(back_cb)

    if use_answer:
        await call.message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
