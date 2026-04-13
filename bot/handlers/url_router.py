"""Smart URL router — intercepts YM/Spotify links in any state and offers actions."""
import re
import logging

from aiogram import Router, F
from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot.keyboards import service_keyboard, sc_menu_keyboard
from bot.states import (
    SCSearchFlow, SCBatchFlow,
    ExportFlow,
    YMShareFlow,
    SpotifyFlow,
    AudioTagFlow,
    VKSearchFlow,
)
from bot.tracker import set_active_msg
from .common import _get_user_info

router = Router()
log = logging.getLogger(__name__)

# ── URL patterns (specific before general) ────────────────────────────────────

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # YM (specific before general)
    (re.compile(r'music\.yandex\.(ru|com)/album/\d+/track/\d+'), "ym_track"),
    (re.compile(r'music\.yandex\.(ru|com)/users/[^/\s]+/playlists/\d+'), "ym_playlist"),
    (re.compile(r'music\.yandex\.(ru|com)/playlists/[^\s]+'), "ym_playlist"),
    (re.compile(r'music\.yandex\.(ru|com)/album/\d+'), "ym_album"),
    # Spotify
    (re.compile(r'open\.spotify\.com(?:/intl-[a-z]+)?/track/[A-Za-z0-9]+'), "spotify_track"),
    (re.compile(r'open\.spotify\.com(?:/intl-[a-z]+)?/playlist/[A-Za-z0-9]+'), "spotify_playlist"),
    (re.compile(r'open\.spotify\.com(?:/intl-[a-z]+)?/album/[A-Za-z0-9]+'), "spotify_album"),
    # YouTube (video links → direct download)
    (re.compile(r'(?:https?://)?(?:www\.)?youtube\.com/watch[^\s]+'), "yt_video"),
    (re.compile(r'(?:https?://)?youtu\.be/[^\s]+'), "yt_video"),
    (re.compile(r'(?:https?://)?music\.youtube\.com/watch[^\s]+'), "yt_video"),
    # SoundCloud (track or playlist → direct download)
    (re.compile(r'(?:https?://)?(?:www\.)?soundcloud\.com/[^\s]+'), "sc_url"),
]

_TYPE_LABELS = {
    "ym_track":        "🎵 Трек из Яндекс Музыки",
    "ym_album":        "🎵 Альбом в Яндекс Музыке",
    "ym_playlist":     "🎵 Плейлист в Яндекс Музыке",
    "spotify_track":   "🟢 Трек из Spotify",
    "spotify_album":   "🟢 Альбом в Spotify",
    "spotify_playlist":"🟢 Плейлист в Spotify",
    "yt_video":        "🎬 Видео на YouTube",
    "sc_url":          "🔊 Ссылка SoundCloud",
}

# States where the bot itself is waiting for text input — don't intercept there
_SKIP_STATES: set[str] = {
    str(YMShareFlow.token),
    str(YMShareFlow.waiting),
    str(YMShareFlow.filter_input),
    str(YMShareFlow.seek_input),
    str(SpotifyFlow.playlist_waiting),
    str(SpotifyFlow.auth_waiting),
    str(SpotifyFlow.filter_input),
    str(SCSearchFlow.sc_search_query),
    str(SCSearchFlow.yt_search_query),
    str(SCSearchFlow.sc_url_input),
    str(SCBatchFlow.sc_ym_token),
    str(SCBatchFlow.sc_resume_input),
    str(SCBatchFlow.filter_input),
    str(ExportFlow.waiting_for_link),
    str(ExportFlow.waiting_for_token),
    str(ExportFlow.filter_input),
    str(AudioTagFlow.waiting_for_audio),
    str(AudioTagFlow.waiting_for_title),
    str(AudioTagFlow.waiting_for_artist),
    str(AudioTagFlow.waiting_for_cover),
    str(AudioTagFlow.waiting_for_field_selection),
    str(VKSearchFlow.vk_search_query),
}


def _detect(text: str) -> tuple[str, str] | None:
    """Return (url_type, matched_url) or None."""
    for pattern, url_type in _PATTERNS:
        m = pattern.search(text)
        if m:
            return url_type, m.group(0)
    return None


class _SmartUrlFilter(Filter):
    """Fires only when message contains a detectable URL and bot isn't expecting text input."""
    async def __call__(self, message: Message, state: FSMContext) -> bool:
        if not message.text:
            return False
        current = await state.get_state()
        if current in _SKIP_STATES:
            return False
        return bool(_detect(message.text))


def _action_keyboard(url_type: str) -> InlineKeyboardMarkup:
    rows = []
    if url_type in ("ym_album", "ym_playlist"):
        rows.append([InlineKeyboardButton(text="📥 Загрузить", callback_data="url_detect:load_ym")])
    elif url_type in ("spotify_album", "spotify_playlist"):
        rows.append([InlineKeyboardButton(text="📥 Загрузить", callback_data="url_detect:load_spotify")])
    elif url_type in ("yt_video", "sc_url"):
        rows.append([InlineKeyboardButton(text="📥 Скачать", callback_data="url_detect:download_url")])
    else:
        # YM/Spotify track — redirect to SC search
        rows.append([InlineKeyboardButton(text="🔍 Поиск на SoundCloud", callback_data="url_detect:sc_search")])
        rows.append([InlineKeyboardButton(text="🔍 Поиск на YouTube", callback_data="url_detect:yt_search")])
    rows.append([InlineKeyboardButton(text="← Отмена", callback_data="url_detect:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Message handler ───────────────────────────────────────────────────────────

@router.message(_SmartUrlFilter())
async def on_url_message(message: Message, state: FSMContext) -> None:
    url_type, url = _detect(message.text)
    label = _TYPE_LABELS[url_type]

    await state.update_data(url_detected=url, url_detected_type=url_type)

    sent = await message.answer(
        f"🔗 <b>{label}</b>\n\n<code>{url}</code>\n\nЧто сделать?",
        parse_mode="HTML",
        reply_markup=_action_keyboard(url_type),
    )
    set_active_msg(message.chat.id, sent.message_id)


# ── Callbacks ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "url_detect:load_ym")
async def on_url_load_ym(call: CallbackQuery, state: FSMContext) -> None:
    from .yms_router import load_ym_url  # lazy import avoids circular dependency
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    url = data.get("url_detected", "")
    if not url:
        await call.answer("Ссылка не найдена. Пришли снова.", show_alert=True)
        return
    await call.answer()
    status_msg = await call.message.edit_text("⏳ Загружаю…")
    set_active_msg(user_id, status_msg.message_id)
    await load_ym_url(status_msg, state, url, user_id, username)


@router.callback_query(F.data == "url_detect:load_spotify")
async def on_url_load_spotify(call: CallbackQuery, state: FSMContext) -> None:
    from .spotify_router import load_spotify_url  # lazy import avoids circular dependency
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    url = data.get("url_detected", "")
    if not url:
        await call.answer("Ссылка не найдена. Пришли снова.", show_alert=True)
        return
    await call.answer()
    status_msg = await call.message.edit_text("⏳ Загружаю…")
    set_active_msg(user_id, status_msg.message_id)
    await load_spotify_url(status_msg, state, url, user_id, username)


@router.callback_query(F.data == "url_detect:sc_search")
async def on_url_sc_search(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(SCSearchFlow.sc_menu)
    await call.message.edit_text(
        "Найди трек через поиск — введи название или используй кнопки ниже:",
        reply_markup=sc_menu_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)


@router.callback_query(F.data == "url_detect:yt_search")
async def on_url_yt_search(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(SCSearchFlow.yt_search_query)
    from bot.keyboards import sc_cancel_keyboard
    await call.message.edit_text(
        "🎬 Введи название трека для поиска на YouTube:",
        reply_markup=sc_cancel_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)


@router.callback_query(F.data == "url_detect:download_url")
async def on_url_download(call: CallbackQuery, state: FSMContext) -> None:
    from .sc_router import _sc_download_and_send  # lazy — avoids circular import
    from .common import extract_yt_url_info_with_proxy_rotation
    from core import sc_downloader

    user_id, username = _get_user_info(call)
    data = await state.get_data()
    url = data.get("url_detected", "")
    if not url:
        await call.answer("Ссылка не найдена. Пришли снова.", show_alert=True)
        return
    await call.answer()

    status_msg = await call.message.edit_text("⏳ Получаю информацию по ссылке…")
    set_active_msg(user_id, status_msg.message_id)

    async def _on_geo_block():
        await status_msg.edit_text(
            "⚠️ Видео заблокировано в регионе сервера. Скачиваю через прокси, ожидайте…",
            parse_mode="HTML",
        )

    try:
        if sc_downloader._is_youtube_url(url):
            info = await extract_yt_url_info_with_proxy_rotation(
                url, call.bot, on_geo_block=_on_geo_block
            )
        else:
            info = await sc_downloader.extract_url_info(url)
    except Exception as e:
        log.warning("url_router download extract failed user=%s url=%s: %s", user_id, url, e)
        from bot.keyboards import sc_cancel_keyboard
        await status_msg.edit_text(
            "❌ Не удалось получить информацию. Проверь ссылку и попробуй ещё раз.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    if info["type"] == "track":
        result = info["result"]
        await status_msg.edit_text(
            f"⏳ Скачиваю: <b>{result.artist} — {result.title}</b>…",
            parse_mode="HTML",
        )
        source = "yt" if sc_downloader._is_youtube_url(url) else "sc"
        await _sc_download_and_send(status_msg, state, result, user_id,
                                    return_to_menu=True, username=username, source=source)
    else:
        # playlist/album — hand off to sc batch flow
        entries = info["entries"]
        title = info["title"]
        if not entries:
            await status_msg.edit_text("😔 Плейлист пуст или не удалось загрузить треки.")
            return
        tracks = [{"url": e.url, "artist": e.artist, "title": e.title} for e in entries]
        await state.update_data(sc_tracks=tracks, sc_resume_back_cb="sc_menu",
                                sc_filter_artists=[], sc_original_tracks=None)
        from bot.keyboards import sc_resume_keyboard
        await status_msg.edit_text(
            f"📥 Найдено <b>{len(tracks)}</b> треков в плейлисте «{title}».\n\nС какого трека начать?",
            parse_mode="HTML",
            reply_markup=sc_resume_keyboard(),
        )
        await state.set_state(SCBatchFlow.sc_resume_choice)


@router.callback_query(F.data == "url_detect:cancel")
async def on_url_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.edit_text("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, call.message.message_id)
