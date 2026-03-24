import asyncio
import logging
import os
import re
from datetime import datetime

import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from rapidfuzz import fuzz

from config import settings
from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow, YMShareFlow
from bot.keyboards import (
    service_keyboard,
    retention_keyboard,
    token_guide_keyboard,
    export_type_keyboard,
    playlists_keyboard,
    cancel_keyboard,
    sc_menu_keyboard,
    sc_cancel_keyboard,
    sc_results_keyboard,
    sc_playlists_keyboard,
    sc_resume_keyboard,
    sc_resume_confirm_keyboard,
    sc_stop_keyboard,
    sc_offer_keyboard,
    sc_offer_extended_keyboard,
    sc_after_download_keyboard,
    sc_batch_token_keyboard,
    export_filter_cancel_keyboard,
    export_filter_result_keyboard,
    ym_share_token_keyboard,
    ym_share_cancel_keyboard,
    ym_share_actions_keyboard,
    ym_share_back_keyboard,
    ym_share_filter_result_keyboard,
    ym_share_seek_confirm_keyboard,
    cache_results_keyboard,
)
from core.ym_source import YandexMusicSource
from core import sc_downloader
from core.sc_downloader import SCResult
from utils.export import build_txt_file, cleanup
from utils.event_log import log_event, update_batch_live
from utils.db import get_cached_file_id, save_cached_file_id, search_cache_fuzzy

router = Router()
log = logging.getLogger(__name__)

# Cancel events for SC batch downloads keyed by user_id
_cancel_events: dict[int, asyncio.Event] = {}

# Global semaphore limiting concurrent SC batch downloads
_batch_semaphore = asyncio.Semaphore(settings.SC_MAX_BATCH_DOWNLOADS)

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


def _parse_ym_share(text: str) -> str | None:
    """
    Extract a resolvable YM playlist URL from iframe HTML or a plain link.
    Handles:
      • <iframe ... src="https://music.yandex.ru/iframe/playlist/USER/KIND" ...>
      • music.yandex.ru/users/USER/playlists/KIND  (direct link)
      • music.yandex.ru/playlists/lk.UUID          (share link)
    """
    # 1. iframe src — highest priority, gives us user+kind directly
    m = _RE_IFRAME_PLAYLIST.search(text)
    if m:
        username, kind = m.group(2), m.group(3)
        return f"https://music.yandex.ru/users/{username}/playlists/{kind}"

    # 2. lk. share link
    m_lk = re.search(r'https?://music\.yandex\.(ru|com)/playlists/lk\.([a-f0-9-]+)', text)
    if m_lk:
        return m_lk.group(0)

    # 3. Direct user playlist link
    m_direct = re.search(
        r'https?://music\.yandex\.(ru|com)/users/([^/\s"\']+)/playlists/(\d+)', text
    )
    if m_direct:
        return m_direct.group(0)

    return None


def _make_cache_key(artist: str, title: str) -> str:
    """Normalised lookup key for the track_cache table."""
    s = f"{artist} {title}".lower()
    return re.sub(r'[^\w\s]', '', s).strip()


def _filter_by_artist(tracks: list[dict], query: str, threshold: int = 70) -> list[dict]:
    """Return tracks where any of the comma-separated artists fuzzy-matches query."""
    q = query.strip().lower()
    matched = []
    for t in tracks:
        parts = [a.strip().lower() for a in t.get("artist", "").split(",")]
        if any(fuzz.partial_ratio(q, p) >= threshold for p in parts):
            matched.append(t)
    return matched


def _get_user_info(event: Message | CallbackQuery) -> tuple[int, str | None]:
    user = event.from_user
    return user.id, user.username


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        '👋 Привет! Что хочешь сделать?',
        parse_mode="HTML",
        reply_markup=service_keyboard(),
    )
    await state.set_state(ExportFlow.choosing_service)


# ── Service selection ─────────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_service, F.data == "service:yandex")
async def on_service_yandex(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_RETENTION_TEXT, parse_mode="HTML", reply_markup=retention_keyboard())
    await state.update_data(service="yandex")
    await state.set_state(ExportFlow.choosing_retention)


@router.callback_query(ExportFlow.choosing_service, F.data == "service:soundcloud")
async def on_service_soundcloud(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.update_data(service="soundcloud")
    await state.set_state(SCSearchFlow.sc_menu)


@router.callback_query(ExportFlow.choosing_service, F.data == "service:share")
async def on_service_share(call: CallbackQuery, state: FSMContext) -> None:
    if settings.YM_BOT_TOKEN:
        await call.message.edit_text(
            _YMS_INPUT_TEXT,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ym_share_cancel_keyboard(),
        )
        await state.set_state(YMShareFlow.waiting)
    else:
        await call.message.edit_text(
            "🔑 Для доступа к плейлистам нужна авторизация в Яндексе.\n\n" + _TOKEN_GUIDE,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=ym_share_token_keyboard(),
        )
        await state.set_state(YMShareFlow.token)


@router.callback_query(ExportFlow.choosing_retention, F.data == "retention:back")
async def on_retention_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        '👋 Привет! Что хочешь сделать?',
        parse_mode="HTML",
        reply_markup=service_keyboard(),
    )
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(ExportFlow.waiting_for_token, F.data == "retention:back")
async def on_token_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_RETENTION_TEXT, parse_mode="HTML", reply_markup=retention_keyboard())
    await state.set_state(ExportFlow.choosing_retention)


# ── Token retention choice ────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_retention, F.data.in_({"retention:session", "retention:single"}))
async def on_retention_chosen(call: CallbackQuery, state: FSMContext) -> None:
    retention = call.data.split(":")[1]  # "session" or "single"
    await state.update_data(retention=retention)
    await call.message.edit_text(
        _TOKEN_GUIDE,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=token_guide_keyboard(),
    )
    await state.set_state(ExportFlow.waiting_for_token)


# ── Token input ───────────────────────────────────────────────────────────────

@router.message(ExportFlow.waiting_for_token)
async def on_token_received(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текст — токен из адресной строки браузера.")
        return

    token = message.text.strip()

    if len(token) < 10:
        await message.answer(
            "❌ Токен выглядит слишком коротким.\n\n"
            "Скопируй значение после <code>#access_token=</code> и до первого <code>&amp;</code>, "
            "или нажми /start чтобы начать заново.",
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ Проверяю токен…")
    try:
        source = YandexMusicSource(token)
        await source._get_client()
    except Exception as e:
        log.warning("Auth failed for user=%s: %s", user_id, e)
        log_event(user_id, username, "auth_fail", "error", detail=type(e).__name__)
        await status_msg.edit_text(
            "❌ Не удалось авторизоваться.\n\n"
            "<b>Отправь токен ещё раз</b> или нажми /start чтобы начать заново.",
            parse_mode="HTML",
        )
        return

    log_event(user_id, username, "auth_ok", "success")
    await state.update_data(token=token)
    await status_msg.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


# ── Export type ───────────────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_export_type, F.data == "export:liked")
async def on_export_liked(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    if data.get("is_exporting"):
        await call.answer("⏳ Уже выполняется экспорт, подожди…", show_alert=True)
        return
    await state.update_data(is_exporting=True)
    await call.message.edit_text("⏳ Загружаю любимые треки…")

    try:
        tracks = await YandexMusicSource(data["token"]).get_liked_tracks()
    except Exception as e:
        log.exception("export_liked failed user=%s: %s", user_id, e)
        log_event(user_id, username, "export_liked", "error", detail=type(e).__name__)
        await call.message.edit_text(
            "❌ Не удалось загрузить треки. Возможно, токен устарел.\n\n"
            "Нажми /start чтобы авторизоваться заново.",
        )
        await state.clear()
        return

    log_event(user_id, username, "export_liked", "success", track_count=len(tracks))
    await _deliver_tracks(call, state, tracks, "liked_tracks.txt")


@router.callback_query(ExportFlow.choosing_export_type, F.data == "export:playlists")
async def on_export_playlists(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    await call.message.edit_text("⏳ Загружаю список плейлистов…")
    data = await state.get_data()

    try:
        playlists = await YandexMusicSource(data["token"]).get_playlists()
    except Exception as e:
        log.exception("get_playlists failed user=%s: %s", user_id, e)
        log_event(user_id, username, "export_playlist", "error", detail=type(e).__name__)
        await call.message.edit_text(
            "❌ Не удалось загрузить плейлисты.\n\nНажми /start чтобы авторизоваться заново.",
        )
        await state.clear()
        return

    if not playlists:
        await call.message.edit_text("😔 Плейлисты не найдены.\n\nВыбери другой вариант:",
                                     reply_markup=export_type_keyboard())
        return

    await state.update_data(playlists=playlists)
    await call.message.edit_text("📋 Выбери плейлист:", reply_markup=playlists_keyboard(playlists))
    await state.set_state(ExportFlow.choosing_playlist)


@router.callback_query(ExportFlow.choosing_export_type, F.data == "export:by_link")
async def on_export_by_link(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "🔗 Отправь ссылку на плейлист Яндекс Музыки.\n\n"
        "Поддерживаемые форматы:\n"
        "• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n"
        "• <code>music.yandex.ru/playlists/lk.UUID</code> (кнопка «Поделиться»)",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(ExportFlow.waiting_for_link)


@router.callback_query(ExportFlow.choosing_export_type, F.data == "export:back")
async def on_export_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())


# ── Link input ────────────────────────────────────────────────────────────────

@router.message(ExportFlow.waiting_for_link)
async def on_link_received(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer(
            "❌ Нужно отправить текстовую ссылку на плейлист.",
            reply_markup=cancel_keyboard(),
        )
        return

    url = message.text.strip()
    data = await state.get_data()

    status_msg = await message.answer("⏳ Загружаю плейлист…")
    try:
        title, tracks = await YandexMusicSource(data["token"]).get_playlist_by_url(url)
    except ValueError as e:
        log.warning("export_by_link ValueError user=%s url=%s: %s", user_id, url[:80], e)
        log_event(user_id, username, "export_by_link", "error", detail="invalid_url_or_not_found")
        await status_msg.edit_text(
            f"❌ {e}\n\n"
            "Отправь другую ссылку или нажми «Отмена» чтобы вернуться в меню.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return
    except Exception as e:
        log.exception("export_by_link failed user=%s url=%s: %s", user_id, url[:80], e)
        log_event(user_id, username, "export_by_link", "error", detail=type(e).__name__)
        await status_msg.edit_text(
            "❌ Не удалось загрузить плейлист.\n\n"
            "Отправь другую ссылку или нажми «Отмена» чтобы вернуться в меню.",
            reply_markup=cancel_keyboard(),
        )
        return

    log_event(user_id, username, "export_by_link", "success", track_count=len(tracks))
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "playlist"
    await _deliver_tracks_msg(status_msg, state, tracks, f"{safe_title}.txt", offer_sc=True)


@router.callback_query(ExportFlow.waiting_for_link, F.data == "action:cancel")
async def on_link_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


# ── Playlist selection ────────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_playlist, F.data.startswith("playlist:"))
async def on_playlist_selected(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    playlist_id = call.data.split(":", 1)[1]
    data = await state.get_data()
    if data.get("is_exporting"):
        await call.answer("⏳ Уже выполняется экспорт, подожди…", show_alert=True)
        return
    await state.update_data(is_exporting=True)

    playlists = data.get("playlists", [])
    title = next((p["title"] for p in playlists if str(p["kind"]) == playlist_id), "playlist")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip() or "playlist"

    await call.message.edit_text(f"⏳ Загружаю треки из «{title}»…")

    try:
        tracks = await YandexMusicSource(data["token"]).get_playlist_tracks(playlist_id)
    except Exception as e:
        log.exception("export_playlist failed user=%s: %s", user_id, e)
        log_event(user_id, username, "export_playlist", "error", detail=type(e).__name__)
        await call.message.edit_text(
            "❌ Не удалось загрузить треки. Плейлист недоступен или токен устарел.\n\n"
            "Нажми /start чтобы авторизоваться заново.",
        )
        await state.clear()
        return

    log_event(user_id, username, "export_playlist", "success", track_count=len(tracks))
    await _deliver_tracks(call, state, tracks, f"{safe_title}.txt", offer_sc=True)


@router.callback_query(ExportFlow.choosing_playlist, F.data == "export:back")
async def on_playlist_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


# ── SC: Service selection ─────────────────────────────────────────────────────

@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:search")
async def on_sc_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(sc_input_mode="search")
    await call.message.edit_text(
        "🔍 Введи запрос для поиска трека:\n\n<i>Например: Linkin Park Numb</i>",
        parse_mode="HTML",
        reply_markup=sc_cancel_keyboard(),
    )
    await state.set_state(SCSearchFlow.sc_search_query)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:yt_search")
async def on_yt_search(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(sc_input_mode="yt_search")
    await call.message.edit_text(
        "🔍 Введи запрос для поиска на YouTube:\n\n<i>Например: Linkin Park Numb</i>",
        parse_mode="HTML",
        reply_markup=sc_cancel_keyboard(),
    )
    await state.set_state(SCSearchFlow.yt_search_query)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:url")
async def on_sc_url(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(sc_input_mode="url")
    await call.message.edit_text(_SC_URL_TEXT, parse_mode="HTML", reply_markup=sc_cancel_keyboard())
    await state.set_state(SCSearchFlow.sc_url_input)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:search_again")
async def on_sc_search_again(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    mode = data.get("sc_input_mode")
    if mode == "url":
        await call.message.edit_text(_SC_URL_TEXT, parse_mode="HTML", reply_markup=sc_cancel_keyboard())
        await state.set_state(SCSearchFlow.sc_url_input)
    elif mode == "yt_search":
        await call.message.edit_text(
            "🔍 Введи запрос для поиска на YouTube:\n\n<i>Например: Linkin Park Numb</i>",
            parse_mode="HTML",
            reply_markup=sc_cancel_keyboard(),
        )
        await state.set_state(SCSearchFlow.yt_search_query)
    else:
        await call.message.edit_text(
            "🔍 Введи запрос для поиска трека:\n\n<i>Например: Linkin Park Numb</i>",
            parse_mode="HTML",
            reply_markup=sc_cancel_keyboard(),
        )
        await state.set_state(SCSearchFlow.sc_search_query)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:batch")
async def on_sc_batch_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "📥 <b>Скачать плейлист с SoundCloud</b>\n\n"
        "Для выбора плейлиста нужна авторизация в Яндекс Музыке.\n\n" + _TOKEN_GUIDE,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=sc_batch_token_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_ym_token)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:back")
async def on_sc_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text("👋 Выбери сервис:", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


# ── SC: Inline offer after YM .txt export ─────────────────────────────────────

@router.callback_query(F.data == "sc:batch_from_ym")
async def on_sc_batch_from_ym(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    sc_tracks = data.get("sc_tracks")
    if not sc_tracks:
        await call.answer(
            "Данные плейлиста недоступны. Введи /start чтобы начать заново.",
            show_alert=True,
        )
        return

    # Remove the offer button from the document message
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await call.message.answer(
        f"📥 Готов скачать <b>{len(sc_tracks)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: Search query ──────────────────────────────────────────────────────────

@router.message(SCSearchFlow.sc_search_query)
async def on_sc_search_query(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текстовый запрос.", reply_markup=sc_cancel_keyboard())
        return

    query = message.text.strip()

    # ── Cache lookup ──────────────────────────────────────────────────────────
    cache_hits = search_cache_fuzzy(query)
    if cache_hits:
        await state.update_data(cache_pending_query=query, cache_fallback_source="sc",
                                cache_hits=cache_hits)
        await state.set_state(SCSearchFlow.sc_cache_results)
        lines = "\n".join(f"• <b>{h['artist']} — {h['title']}</b>" for h in cache_hits)
        await message.answer(
            f"⚡ Нашёл в кэше — это нужный трек?\n\n{lines}",
            parse_mode="HTML",
            reply_markup=cache_results_keyboard(cache_hits, "sc"),
        )
        return

    status_msg = await message.answer("🔍 Ищу на SoundCloud…")

    try:
        results = await sc_downloader.search(query, max_results=5)
    except Exception as e:
        log.exception("SC search error user=%s: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Ошибка поиска. Попробуй ещё раз.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    if not results:
        await status_msg.edit_text(
            "😔 Ничего не найдено. Попробуй другой запрос.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    # Find best match by token_sort_ratio
    best_idx, best_score = 0, 0
    for i, r in enumerate(results):
        score = fuzz.token_sort_ratio(query.lower(), f"{r.artist} {r.title}".lower())
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= 80:
        best = results[best_idx]
        await status_msg.edit_text(
            f"⏳ Найдено: <b>{best.artist} — {best.title}</b>\nСкачиваю…",
            parse_mode="HTML",
        )
        await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username)
    else:
        # Show top-5 for manual selection
        await state.update_data(sc_search_results=[
            {"url": r.url, "title": r.title, "artist": r.artist, "duration": r.duration}
            for r in results
        ])
        await status_msg.edit_text(
            "🔍 Точного совпадения не найдено. Выбери трек из результатов:",
            reply_markup=sc_results_keyboard(results),
        )
        await state.set_state(SCSearchFlow.sc_search_results)


# ── SC: Pick from search results ──────────────────────────────────────────────

@router.callback_query(SCSearchFlow.sc_search_results, F.data.startswith("sc_pick:"))
async def on_sc_pick(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    idx = int(call.data.split(":", 1)[1])
    data = await state.get_data()
    results_data = data.get("sc_search_results", [])

    if idx >= len(results_data):
        await call.answer("Результат не найден.", show_alert=True)
        return

    r = results_data[idx]
    result = SCResult(url=r["url"], title=r["title"], artist=r["artist"], duration=r["duration"])

    await call.message.edit_text(
        f"⏳ Скачиваю: <b>{result.artist} — {result.title}</b>…",
        parse_mode="HTML",
    )
    await _sc_download_and_send(call.message, state, result, user_id, return_to_menu=True, username=username)


# ── YouTube: Search query ─────────────────────────────────────────────────────

@router.message(SCSearchFlow.yt_search_query)
async def on_yt_search_query(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текст — название трека.", reply_markup=sc_cancel_keyboard())
        return

    query = message.text.strip()

    # ── Cache lookup ──────────────────────────────────────────────────────────
    cache_hits = search_cache_fuzzy(query)
    if cache_hits:
        await state.update_data(cache_pending_query=query, cache_fallback_source="yt",
                                cache_hits=cache_hits)
        await state.set_state(SCSearchFlow.sc_cache_results)
        lines = "\n".join(f"• <b>{h['artist']} — {h['title']}</b>" for h in cache_hits)
        await message.answer(
            f"⚡ Нашёл в кэше — это нужный трек?\n\n{lines}",
            parse_mode="HTML",
            reply_markup=cache_results_keyboard(cache_hits, "yt"),
        )
        return

    status_msg = await message.answer("🔍 Ищу на YouTube…")

    try:
        results = await sc_downloader.search_youtube(query, max_results=5)
    except Exception as e:
        log.exception("YT search failed user=%s query=%r: %s", user_id, query, e)
        await status_msg.edit_text("❌ Ошибка поиска. Попробуй ещё раз.", reply_markup=sc_cancel_keyboard())
        return

    if not results:
        await status_msg.edit_text("😔 Ничего не найдено. Попробуй другой запрос.", reply_markup=sc_cancel_keyboard())
        return

    best = results[0]
    best_score = fuzz.token_sort_ratio(query.lower(), f"{best.artist} {best.title}".lower())

    if best_score >= 80:
        await status_msg.edit_text(
            f"⏳ Найдено: <b>{best.artist} — {best.title}</b>\nСкачиваю…",
            parse_mode="HTML",
        )
        await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username)
    else:
        await state.update_data(yt_search_results=[
            {"url": r.url, "title": r.title, "artist": r.artist, "duration": r.duration}
            for r in results
        ])
        await status_msg.edit_text(
            "🔍 Точного совпадения не найдено. Выбери трек из результатов:",
            reply_markup=sc_results_keyboard(results),
        )
        await state.set_state(SCSearchFlow.yt_search_results)


# ── YouTube: Pick from search results ─────────────────────────────────────────

@router.callback_query(SCSearchFlow.yt_search_results, F.data.startswith("sc_pick:"))
async def on_yt_pick(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    idx = int(call.data.split(":", 1)[1])
    data = await state.get_data()
    results_data = data.get("yt_search_results", [])

    if idx >= len(results_data):
        await call.answer("Результат не найден.", show_alert=True)
        return

    r = results_data[idx]
    result = SCResult(url=r["url"], title=r["title"], artist=r["artist"], duration=r["duration"])

    await call.message.edit_text(
        f"⏳ Скачиваю: <b>{result.artist} — {result.title}</b>…",
        parse_mode="HTML",
    )
    await _sc_download_and_send(call.message, state, result, user_id, return_to_menu=True, username=username)


# ── Cache: Pick from cache results ───────────────────────────────────────────

@router.callback_query(SCSearchFlow.sc_cache_results, F.data.startswith("cache_pick:"))
async def on_cache_pick(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    idx = int(call.data.split(":", 1)[1])
    data = await state.get_data()
    hits = data.get("cache_hits", [])

    if idx >= len(hits):
        await call.answer("Результат не найден.", show_alert=True)
        return

    hit = hits[idx]
    await call.message.edit_text(
        f"⚡ Отправляю из кэша: <b>{hit['artist']} — {hit['title']}</b>…",
        parse_mode="HTML",
    )
    try:
        await call.message.answer_audio(
            audio=hit["file_id"],
            title=hit["title"],
            performer=hit["artist"],
        )
        log_event(user_id, username, "sc_search", "success",
                  track_count=1, detail=f"{hit['artist']} — {hit['title']} [cache]")
        try:
            await call.message.delete()
        except Exception:
            pass
    except Exception as e:
        log.warning("Cache send_audio failed user=%s: %s", user_id, e)
        await call.message.edit_text(
            "❌ Не удалось отправить из кэша. Попробуй поискать заново.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    await call.message.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


@router.callback_query(SCSearchFlow.sc_cache_results, F.data == "cache_miss")
async def on_cache_miss(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    query = data.get("cache_pending_query", "")
    source = data.get("cache_fallback_source", "sc")

    if source == "sc":
        status_msg = await call.message.edit_text("🔍 Ищу на SoundCloud…")
        try:
            results = await sc_downloader.search(query, max_results=5)
        except Exception as e:
            log.exception("SC search error user=%s: %s", user_id, e)
            await status_msg.edit_text("❌ Ошибка поиска. Попробуй ещё раз.", reply_markup=sc_cancel_keyboard())
            return
        if not results:
            await status_msg.edit_text("😔 Ничего не найдено.", reply_markup=sc_cancel_keyboard())
            return
        best_idx, best_score = 0, 0
        for i, r in enumerate(results):
            score = fuzz.token_sort_ratio(query.lower(), f"{r.artist} {r.title}".lower())
            if score > best_score:
                best_score, best_idx = score, i
        if best_score >= 80:
            best = results[best_idx]
            await status_msg.edit_text(
                f"⏳ Найдено: <b>{best.artist} — {best.title}</b>\nСкачиваю…", parse_mode="HTML")
            await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username)
        else:
            await state.update_data(sc_search_results=[
                {"url": r.url, "title": r.title, "artist": r.artist, "duration": r.duration}
                for r in results
            ])
            await status_msg.edit_text(
                "🔍 Точного совпадения не найдено. Выбери трек из результатов:",
                reply_markup=sc_results_keyboard(results),
            )
            await state.set_state(SCSearchFlow.sc_search_results)
    else:
        status_msg = await call.message.edit_text("🔍 Ищу на YouTube…")
        try:
            results = await sc_downloader.search_youtube(query, max_results=5)
        except Exception as e:
            log.exception("YT search error user=%s: %s", user_id, e)
            await status_msg.edit_text("❌ Ошибка поиска. Попробуй ещё раз.", reply_markup=sc_cancel_keyboard())
            return
        if not results:
            await status_msg.edit_text("😔 Ничего не найдено.", reply_markup=sc_cancel_keyboard())
            return
        best = results[0]
        best_score = fuzz.token_sort_ratio(query.lower(), f"{best.artist} {best.title}".lower())
        if best_score >= 80:
            await status_msg.edit_text(
                f"⏳ Найдено: <b>{best.artist} — {best.title}</b>\nСкачиваю…", parse_mode="HTML")
            await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username)
        else:
            await state.update_data(yt_search_results=[
                {"url": r.url, "title": r.title, "artist": r.artist, "duration": r.duration}
                for r in results
            ])
            await status_msg.edit_text(
                "🔍 Точного совпадения не найдено. Выбери трек из результатов:",
                reply_markup=sc_results_keyboard(results),
            )
            await state.set_state(SCSearchFlow.yt_search_results)


# ── SC: Download by URL ───────────────────────────────────────────────────────

@router.message(SCSearchFlow.sc_url_input)
async def on_sc_url_input(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text or not message.text.strip().startswith("http"):
        await message.answer("❌ Нужно отправить ссылку (должна начинаться с http).",
                             reply_markup=sc_cancel_keyboard())
        return

    url = message.text.strip()
    status_msg = await message.answer("⏳ Получаю информацию по ссылке…")

    try:
        info = await sc_downloader.extract_url_info(url)
    except Exception as e:
        log.warning("SC URL extract failed user=%s url=%s: %s", user_id, url, e)
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
        await _sc_download_and_send(status_msg, state, result, user_id,
                                    return_to_menu=True, username=username)
    else:
        entries = info["entries"]
        title = info["title"]
        if not entries:
            await status_msg.edit_text(
                "😔 Плейлист пуст или не удалось загрузить треки.",
                reply_markup=sc_cancel_keyboard(),
            )
            return
        # Store tracks with direct URLs — batch will skip search step
        tracks = [{"url": e.url, "artist": e.artist, "title": e.title} for e in entries]
        await state.update_data(sc_tracks=tracks)
        await status_msg.edit_text(
            f'<tg-emoji emoji-id="6039802767931871481">📥</tg-emoji> '
            f'Найдено <b>{len(tracks)}</b> треков в плейлисте «{title}».\n\nС какого трека начать?',
            parse_mode="HTML",
            reply_markup=sc_resume_keyboard(),
        )
        await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: Cancel (back to menu) ─────────────────────────────────────────────────

@router.callback_query(F.data == "sc:cancel")
async def on_sc_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


# ── SC: Batch via YM auth ─────────────────────────────────────────────────────

@router.message(SCBatchFlow.sc_ym_token)
async def on_sc_ym_token(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текст — токен из адресной строки браузера.")
        return

    token = message.text.strip()
    if len(token) < 10:
        await message.answer(
            "❌ Токен слишком короткий. Отправь токен ещё раз.",
            parse_mode="HTML",
        )
        return

    status_msg = await message.answer("⏳ Проверяю токен…")
    try:
        source = YandexMusicSource(token)
        await source._get_client()
    except Exception as e:
        log.warning("SC batch YM auth failed user=%s: %s", user_id, e)
        log_event(user_id, username, "auth_fail", "error", detail=type(e).__name__)
        await status_msg.edit_text(
            "❌ Не удалось авторизоваться. Отправь токен ещё раз.",
            parse_mode="HTML",
        )
        return

    try:
        playlists = await source.get_playlists()
    except Exception as e:
        log.exception("SC batch get_playlists failed user=%s: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить плейлисты. Нажми /start чтобы начать заново.",
        )
        return

    playlists = [{"kind": "liked", "title": "❤️ Любимые треки"}] + playlists

    await state.update_data(sc_ym_token=token, sc_playlists=playlists)
    await status_msg.edit_text("📋 Выбери плейлист:", reply_markup=sc_playlists_keyboard(playlists))
    await state.set_state(SCBatchFlow.sc_ym_playlist)


@router.callback_query(SCBatchFlow.sc_ym_playlist, F.data.startswith("sc_pl:"))
async def on_sc_ym_playlist_selected(call: CallbackQuery, state: FSMContext) -> None:
    user_id, _ = _get_user_info(call)
    playlist_id = call.data.split(":", 1)[1]
    data = await state.get_data()

    token = data.get("sc_ym_token")
    playlists = data.get("sc_playlists", [])
    title = next((p["title"] for p in playlists if str(p["kind"]) == playlist_id), "playlist")

    await call.message.edit_text(f"⏳ Загружаю треки из «{title}»…")

    try:
        source = YandexMusicSource(token)
        if playlist_id == "liked":
            tracks = await source.get_liked_tracks()
        else:
            tracks = await source.get_playlist_tracks(playlist_id)
    except Exception as e:
        log.exception("SC batch playlist load failed user=%s: %s", user_id, e)
        await call.message.edit_text(
            "❌ Не удалось загрузить плейлист. Нажми /start чтобы начать заново.",
        )
        await state.clear()
        return

    if not tracks:
        await call.message.edit_text("😔 Плейлист пуст.", reply_markup=sc_menu_keyboard())
        await state.set_state(SCSearchFlow.sc_menu)
        return

    await state.update_data(sc_tracks=tracks)
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков с SoundCloud из «{title}».\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: Resume choice ─────────────────────────────────────────────────────────

@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:start")
async def on_sc_resume_start(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS} загрузок). Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    await call.message.edit_text(
        f"▶️ Начинаю с первого трека (всего {len(tracks)})…",
        reply_markup=sc_stop_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_downloading)
    asyncio.create_task(_run_batch_download(call.message, state, user_id, call.from_user.username, tracks, 0))


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:start_reversed")
async def on_sc_resume_start_reversed(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS} загрузок). Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    tracks = list(reversed(data.get("sc_tracks", [])))
    await state.update_data(sc_tracks=tracks)
    await call.message.edit_text(
        f"▶️ Начинаю с первого трека (всего {len(tracks)})…",
        reply_markup=sc_stop_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_downloading)
    asyncio.create_task(_run_batch_download(call.message, state, user_id, call.from_user.username, tracks, 0))


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:seek")
async def on_sc_resume_seek(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "⏩ Введи название трека, с которого хочешь продолжить (или его часть):",
        reply_markup=sc_cancel_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_input)


@router.message(SCBatchFlow.sc_resume_input)
async def on_sc_resume_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Нужно отправить текст — название трека.", reply_markup=sc_cancel_keyboard())
        return

    query = message.text.strip()
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])

    best_idx, best_score = 0, 0
    for i, t in enumerate(tracks):
        candidate = f"{t.get('artist', '')} {t.get('title', '')}".lower()
        score = fuzz.token_sort_ratio(query.lower(), candidate)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score < 30:
        await message.answer(
            "😔 Трек не найден в плейлисте. Попробуй другое название.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    next_idx = best_idx + 1
    if next_idx >= len(tracks):
        await message.answer(
            "ℹ️ Это последний трек в плейлисте — нечего скачивать после него.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    found = tracks[best_idx]
    nxt = tracks[next_idx]
    confirm_text = (
        f"Найден трек <b>{best_idx + 1}/{len(tracks)}</b> — "
        f"{found.get('artist')} — {found.get('title')}\n\n"
        f"Начну со следующего: <b>{next_idx + 1}/{len(tracks)}</b> — "
        f"{nxt.get('artist')} — {nxt.get('title')}\n\n"
        "Верно?"
    )
    await state.update_data(sc_resume_idx=next_idx)
    await message.answer(confirm_text, parse_mode="HTML", reply_markup=sc_resume_confirm_keyboard())
    await state.set_state(SCBatchFlow.sc_resume_confirm)


@router.callback_query(SCBatchFlow.sc_resume_confirm, F.data == "sc_resume:confirm")
async def on_sc_resume_confirm(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS} загрузок). Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    start_idx = data.get("sc_resume_idx", 0)
    await call.message.edit_text(
        f"▶️ Начинаю с трека {start_idx + 1}/{len(tracks)}…",
        reply_markup=sc_stop_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_downloading)
    asyncio.create_task(_run_batch_download(call.message, state, user_id, call.from_user.username, tracks, start_idx))


@router.callback_query(SCBatchFlow.sc_resume_confirm, F.data == "sc_resume:retry")
async def on_sc_resume_retry(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "⏩ Введи название трека, с которого хочешь продолжить:",
        reply_markup=sc_cancel_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_input)


# ── SC: Stop button ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "sc:stop")
async def on_sc_stop(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    event = _cancel_events.get(user_id)
    if event and not event.is_set():
        event.set()
        await call.answer("⛔ Останавливаю после текущего трека…")
    else:
        await call.answer("Нет активного скачивания.", show_alert=True)


# ── ExportFlow: artist filter ─────────────────────────────────────────────────

@router.callback_query(F.data == "export:filter_artist")
async def on_export_filter_artist(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("sc_tracks"):
        await call.answer("Данные плейлиста недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "🔍 Введи имя исполнителя для фильтрации:",
        reply_markup=export_filter_cancel_keyboard(),
    )
    await state.set_state(ExportFlow.filter_input)


@router.message(ExportFlow.filter_input)
async def on_export_filter_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Нужно отправить текст — имя исполнителя.", reply_markup=export_filter_cancel_keyboard())
        return

    query = message.text.strip()
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])

    matched = _filter_by_artist(tracks, query)
    if not matched:
        await message.answer(
            f"😔 Исполнитель <b>{query}</b> не найден в плейлисте.\n\nПопробуй другое имя.",
            parse_mode="HTML",
            reply_markup=export_filter_cancel_keyboard(),
        )
        return

    await state.update_data(export_filtered_tracks=matched)
    filename = f"{query}_tracks.txt"
    tmp_path = await build_txt_file(matched, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Найдено треков исполнителя <b>{query}</b>: {len(matched)}.",
            parse_mode="HTML",
            reply_markup=export_filter_result_keyboard(),
        )
    finally:
        await cleanup(tmp_path)

    await state.set_state(ExportFlow.choosing_export_type)


@router.callback_query(F.data == "export:back_to_menu")
async def on_export_back_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


@router.callback_query(F.data == "export:download_filtered")
async def on_export_download_filtered(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    filtered = data.get("export_filtered_tracks")
    if not filtered:
        await call.answer("Данные недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(sc_tracks=filtered)
    await call.message.answer(
        f"📥 Готов скачать <b>{len(filtered)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── YMShareFlow ────────────────────────────────────────────────────────────────

@router.message(YMShareFlow.token)
async def on_yms_token(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текст — токен из адресной строки браузера.")
        return

    token = message.text.strip()
    if len(token) < 10:
        await message.answer("❌ Токен выглядит слишком коротким. Отправь токен ещё раз.")
        return

    status_msg = await message.answer("⏳ Проверяю токен…")
    try:
        source = YandexMusicSource(token)
        await source._get_client()
    except Exception as e:
        log.warning("YMShare auth failed user=%s: %s", user_id, e)
        log_event(user_id, username, "auth_fail", "error", detail=type(e).__name__)
        await status_msg.edit_text("❌ Не удалось авторизоваться. Отправь токен ещё раз.")
        return

    log_event(user_id, username, "auth_ok", "success")
    await state.update_data(yms_token=token)
    await status_msg.edit_text(
        _YMS_INPUT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ym_share_cancel_keyboard(),
    )
    await state.set_state(YMShareFlow.waiting)


@router.message(YMShareFlow.waiting)
async def on_yms_waiting(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer(
            "❌ Нужно отправить текст — ссылку или HTML-код плейлиста.",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return

    url = _parse_ym_share(message.text)
    if not url:
        await message.answer(
            "❌ Не удалось распознать плейлист.\n\n"
            "Отправь HTML-код (‹iframe ...›) из кнопки «Поделиться» в приложении\n"
            "или прямую ссылку вида:\n"
            "• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n"
            "• <code>music.yandex.ru/playlists/lk.UUID</code>",
            parse_mode="HTML",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    token = settings.YM_BOT_TOKEN or data.get("yms_token", "")

    status_msg = await message.answer("⏳ Загружаю плейлист…")
    try:
        title, tracks = await YandexMusicSource(token).get_playlist_by_url(url)
    except ValueError as e:
        log.warning("YMShare load ValueError user=%s url=%s: %s", user_id, url[:80], e)
        await status_msg.edit_text(
            f"❌ {e}",
            parse_mode="HTML",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return
    except Exception as e:
        log.exception("YMShare load failed user=%s url=%s: %s", user_id, url[:80], e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить плейлист. Проверь ссылку и попробуй ещё раз.",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return

    if not tracks:
        await status_msg.edit_text(
            "😔 Плейлист пуст или недоступен.",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return

    log_event(user_id, username, "yms_load", "success", track_count=len(tracks), detail=title)
    await state.update_data(yms_tracks=tracks, yms_playlist_title=title)
    safe_title = title[:50] if title else "Плейлист"
    await status_msg.edit_text(
        f'✅ Загружен плейлист <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(),
    )
    await state.set_state(YMShareFlow.actions)


@router.callback_query(YMShareFlow.actions, F.data == "yms:download_all")
async def on_yms_download_all(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS} загрузок). Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    await state.update_data(sc_tracks=tracks)
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.callback_query(YMShareFlow.actions, F.data == "yms:filter_artist")
async def on_yms_filter_artist(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "🔍 Введи имя исполнителя для фильтрации:",
        reply_markup=ym_share_back_keyboard(),
    )
    await state.set_state(YMShareFlow.filter_input)


@router.callback_query(YMShareFlow.actions, F.data == "yms:seek")
async def on_yms_seek(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "⏩ Введи название трека, с которого хочешь начать скачивание (или его часть):",
        reply_markup=ym_share_back_keyboard(),
    )
    await state.set_state(YMShareFlow.seek_input)


@router.callback_query(YMShareFlow.actions, F.data == "yms:back_to_input")
async def on_yms_back_to_input(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        _YMS_INPUT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ym_share_cancel_keyboard(),
    )
    await state.set_state(YMShareFlow.waiting)


@router.callback_query(F.data == "yms:cancel")
async def on_yms_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(F.data == "yms:back_to_actions")
async def on_yms_back_to_actions(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    title = data.get("yms_playlist_title", "Плейлист")
    safe_title = title[:50] if title else "Плейлист"
    await call.message.edit_text(
        f'✅ Плейлист <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(),
    )
    await state.set_state(YMShareFlow.actions)


@router.message(YMShareFlow.filter_input)
async def on_yms_filter_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Нужно отправить текст — имя исполнителя.", reply_markup=ym_share_back_keyboard())
        return

    query = message.text.strip()
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])

    matched = _filter_by_artist(tracks, query)
    if not matched:
        await message.answer(
            f"😔 Исполнитель <b>{query}</b> не найден в плейлисте.\n\nПопробуй другое имя.",
            parse_mode="HTML",
            reply_markup=ym_share_back_keyboard(),
        )
        return

    await state.update_data(yms_filtered_tracks=matched)
    filename = f"{query}_tracks.txt"
    tmp_path = await build_txt_file(matched, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Найдено треков исполнителя <b>{query}</b>: {len(matched)}.",
            parse_mode="HTML",
            reply_markup=ym_share_filter_result_keyboard(),
        )
    finally:
        await cleanup(tmp_path)

    await state.set_state(YMShareFlow.actions)


@router.callback_query(F.data == "yms:download_filtered")
async def on_yms_download_filtered(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    filtered = data.get("yms_filtered_tracks")
    if not filtered:
        await call.answer("Данные недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(sc_tracks=filtered)
    await call.message.answer(
        f"📥 Готов скачать <b>{len(filtered)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.message(YMShareFlow.seek_input)
async def on_yms_seek_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Нужно отправить текст — название трека.", reply_markup=ym_share_back_keyboard())
        return

    query = message.text.strip()
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])

    best_idx, best_score = 0, 0
    for i, t in enumerate(tracks):
        candidate = f"{t.get('artist', '')} {t.get('title', '')}".lower()
        score = fuzz.token_sort_ratio(query.lower(), candidate)
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score < 30:
        await message.answer(
            "😔 Трек не найден в плейлисте. Попробуй другое название.",
            reply_markup=ym_share_back_keyboard(),
        )
        return

    if best_idx + 1 >= len(tracks):
        await message.answer(
            "ℹ️ Это последний трек в плейлисте — нечего скачивать после него.",
            reply_markup=ym_share_back_keyboard(),
        )
        return

    found = tracks[best_idx]
    nxt = tracks[best_idx + 1]
    confirm_text = (
        f"Найден трек <b>{best_idx + 1}/{len(tracks)}</b> — "
        f"{found.get('artist')} — {found.get('title')}\n\n"
        f"Начну со следующего: <b>{best_idx + 2}/{len(tracks)}</b> — "
        f"{nxt.get('artist')} — {nxt.get('title')}\n\n"
        f"Итого будет скачано: <b>{len(tracks) - best_idx - 1}</b> треков.\n\n"
        "Верно?"
    )
    await state.update_data(yms_resume_idx=best_idx + 1)
    await message.answer(confirm_text, parse_mode="HTML", reply_markup=ym_share_seek_confirm_keyboard())
    await state.set_state(YMShareFlow.seek_confirm)


@router.callback_query(YMShareFlow.seek_confirm, F.data == "yms_resume:confirm")
async def on_yms_seek_confirm(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS} загрузок). Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    start_idx = data.get("yms_resume_idx", 0)
    await state.update_data(sc_tracks=tracks)
    await call.message.edit_text(
        f"▶️ Начинаю с трека {start_idx + 1}/{len(tracks)}…",
        reply_markup=sc_stop_keyboard(),
    )
    await state.set_state(YMShareFlow.downloading)
    asyncio.create_task(
        _run_batch_download(call.message, state, user_id, call.from_user.username, tracks, start_idx)
    )


@router.callback_query(YMShareFlow.seek_confirm, F.data == "yms_resume:retry")
async def on_yms_seek_retry(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "⏩ Введи название трека, с которого хочешь начать скачивание:",
        reply_markup=ym_share_back_keyboard(),
    )
    await state.set_state(YMShareFlow.seek_input)


# ── Fallback handlers ─────────────────────────────────────────────────────────

_STATE_HINTS = {
    None: "Введи /start чтобы начать.",
    ExportFlow.choosing_service: "Нажми на кнопку выше чтобы выбрать сервис.",
    ExportFlow.choosing_retention: "Выбери вариант хранения токена из кнопок выше.",
    ExportFlow.choosing_export_type: "Нажми на кнопку выше чтобы выбрать что экспортировать.",
    ExportFlow.choosing_playlist: "Выбери плейлист из списка выше.",
    ExportFlow.waiting_for_link: "Отправь ссылку на плейлист или нажми «Отмена».",
    ExportFlow.waiting_for_token: "Отправь токен, скопированный из адресной строки браузера.",
    ExportFlow.filter_input: "Введи имя исполнителя для фильтрации или нажми «Назад».",
    SCSearchFlow.sc_menu: "Нажми кнопку в меню SoundCloud.",
    SCSearchFlow.sc_search_query: "Введи название трека для поиска на SoundCloud.",
    SCSearchFlow.sc_search_results: "Выбери трек из результатов или нажми «Назад».",
    SCSearchFlow.sc_url_input: "Отправь ссылку на трек или плейлист (SoundCloud или YouTube).",
    SCSearchFlow.yt_search_query: "Введи название трека для поиска на YouTube.",
    SCSearchFlow.yt_search_results: "Выбери трек из результатов или нажми «Назад».",
    SCBatchFlow.sc_ym_token: "Отправь токен Яндекс Музыки.",
    SCBatchFlow.sc_ym_playlist: "Выбери плейлист из списка выше.",
    SCBatchFlow.sc_resume_choice: "Выбери, с какого трека начать скачивание.",
    SCBatchFlow.sc_resume_input: "Введи название трека для поиска в плейлисте.",
    SCBatchFlow.sc_resume_confirm: "Подтверди начальный трек кнопкой ниже.",
    SCBatchFlow.sc_downloading: "Скачивание идёт. Нажми «⛔ Остановить» чтобы прервать.",
    YMShareFlow.token: "Отправь токен Яндекс Музыки для авторизации.",
    YMShareFlow.waiting: "Отправь ссылку или HTML-код плейлиста Яндекс Музыки.",
    YMShareFlow.actions: "Выбери действие из меню выше.",
    YMShareFlow.filter_input: "Введи имя исполнителя для фильтрации или нажми «Назад».",
    YMShareFlow.seek_input: "Введи название трека для поиска или нажми «Назад».",
    YMShareFlow.seek_confirm: "Подтверди начальный трек кнопкой ниже.",
    YMShareFlow.downloading: "Скачивание идёт. Нажми «⛔ Остановить» чтобы прервать.",
}


@router.message()
async def fallback_message(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    hint = _STATE_HINTS.get(current, "Введи /start чтобы начать заново.")
    await message.answer(f"ℹ️ {hint}")


@router.callback_query()
async def fallback_callback(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Эта кнопка устарела. Введи /start чтобы начать заново.", show_alert=True)


# ── YM delivery helpers ───────────────────────────────────────────────────────

async def _deliver_tracks(
    call: CallbackQuery,
    state: FSMContext,
    tracks: list[dict],
    filename: str,
    offer_sc: bool = False,
) -> None:
    if not tracks:
        await state.update_data(is_exporting=False)
        await call.message.edit_text("😔 Треков не найдено.\n\nВыбери другой вариант:",
                                     reply_markup=export_type_keyboard())
        await state.set_state(ExportFlow.choosing_export_type)
        return

    if offer_sc:
        await state.update_data(sc_tracks=tracks)

    tmp_path = await build_txt_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Готово! Экспортировано треков: {len(tracks)}.",
            reply_markup=sc_offer_extended_keyboard() if offer_sc else None,
        )
        try:
            await call.message.delete()
        except Exception:
            pass
    finally:
        await cleanup(tmp_path)

    # Guard: /start may have been called while export was running
    if await state.get_state() is None:
        return

    data = await state.get_data()
    await state.update_data(is_exporting=False)
    if data.get("retention") == "single":
        await state.clear()
        await call.message.answer("Токен удалён. Введи /start для нового экспорта.")
    else:
        await call.message.answer(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
        await state.set_state(ExportFlow.choosing_export_type)


async def _deliver_tracks_msg(
    status_msg: Message,
    state: FSMContext,
    tracks: list[dict],
    filename: str,
    offer_sc: bool = False,
) -> None:
    if not tracks:
        await status_msg.edit_text(
            "😔 Треков не найдено.\n\nОтправь другую ссылку или нажми «Отмена».",
            reply_markup=cancel_keyboard(),
        )
        return

    if offer_sc:
        await state.update_data(sc_tracks=tracks)

    tmp_path = await build_txt_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await status_msg.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Готово! Экспортировано треков: {len(tracks)}.",
            reply_markup=sc_offer_extended_keyboard() if offer_sc else None,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
    finally:
        await cleanup(tmp_path)

    if await state.get_state() is None:
        return

    data = await state.get_data()
    await state.update_data(is_exporting=False)
    if data.get("retention") == "single":
        await state.clear()
        await status_msg.answer("Токен удалён. Введи /start для нового экспорта.")
    else:
        await status_msg.answer(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
        await state.set_state(ExportFlow.choosing_export_type)


# ── SC delivery helpers ───────────────────────────────────────────────────────

async def _sc_download_and_send(
    msg: Message,
    state: FSMContext,
    result: SCResult,
    user_id: int,
    return_to_menu: bool = True,
    username: str | None = None,
) -> None:
    cache_key = _make_cache_key(result.artist, result.title)
    cached_file_id = get_cached_file_id(cache_key) if cache_key else None

    if cached_file_id:
        try:
            await msg.answer_audio(
                audio=cached_file_id,
                title=result.title,
                performer=result.artist,
            )
            log_event(user_id, username, "sc_search", "success",
                      track_count=1, detail=f"{result.artist} — {result.title}")
            try:
                await msg.delete()
            except Exception:
                pass
            if return_to_menu and await state.get_state() is not None:
                await msg.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
                await state.set_state(SCSearchFlow.sc_menu)
            return
        except Exception as e:
            log.warning("SC send_audio (cache) failed user=%s, falling back to download: %s", user_id, e)

    try:
        path, meta = await sc_downloader.download(result.url, user_id)
    except Exception as e:
        log.warning("SC download failed user=%s url=%s: %s", user_id, result.url, e)
        log_event(user_id, username, "sc_search", "error", detail="download_failed")
        await msg.edit_text(
            "❌ Не удалось скачать трек. Возможно, трек доступен только по подписке Go+.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    try:
        sent_msg = await msg.answer_audio(
            audio=FSInputFile(path, filename=f"{result.artist} - {result.title}.mp3"),
            title=meta.get("title") or result.title,
            performer=meta.get("artist") or result.artist,
        )
        if sent_msg and sent_msg.audio and cache_key:
            save_cached_file_id(cache_key, sent_msg.audio.file_id, "manual",
                                artist=result.artist, title=result.title)
        log_event(user_id, username, "sc_search", "success",
                  track_count=1, detail=f"{result.artist} — {result.title}")
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log.exception("SC send_audio failed user=%s: %s", user_id, e)
        log_event(user_id, username, "sc_search", "error", detail="send_failed")
        await msg.edit_text("❌ Не удалось отправить файл.", reply_markup=sc_cancel_keyboard())
        return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if return_to_menu and await state.get_state() is not None:
        await msg.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
        await state.set_state(SCSearchFlow.sc_menu)


async def _run_batch_download(
    progress_msg: Message,
    state: FSMContext,
    user_id: int,
    username: str | None,
    tracks: list[dict],
    start_idx: int,
) -> None:
    await _batch_semaphore.acquire()
    cancel_event = asyncio.Event()
    _cancel_events[user_id] = cancel_event

    total = len(tracks)
    not_found: list[str] = []
    downloaded_count = 0
    started_at = datetime.now().isoformat(timespec="seconds")

    update_batch_live(user_id, username, {
        "started_at": started_at,
        "total": total,
        "current_idx": start_idx,
        "current_track": "—",
        "downloaded": 0,
        "failed": [],
        "status": "running",
    })

    try:
        for i, track in enumerate(tracks[start_idx:], start=start_idx + 1):
            if cancel_event.is_set():
                break

            artist = track.get("artist", "")
            title = track.get("title", "")

            update_batch_live(user_id, username, {
                "started_at": started_at,
                "total": total,
                "current_idx": i,
                "current_track": f"{artist} — {title}",
                "downloaded": downloaded_count,
                "failed": not_found,
                "status": "running",
            })
            query = f"{artist} {title}"
            direct_url = track.get("url")
            cache_key = _make_cache_key(artist, title) if (artist or title) and not direct_url else None

            # ── Cache lookup (YM-sourced tracks only) ─────────────────────────
            if cache_key:
                cached_file_id = get_cached_file_id(cache_key)
                if cached_file_id:
                    try:
                        await progress_msg.answer_audio(
                            audio=cached_file_id,
                            title=title,
                            performer=artist,
                        )
                        downloaded_count += 1
                        try:
                            await progress_msg.edit_text(
                                f"⏳ {i}/{total} — ⚡ {artist} — {title}",
                                reply_markup=sc_stop_keyboard(),
                            )
                        except Exception:
                            pass
                        continue
                    except Exception as e:
                        log.warning("SC batch send_audio (cache) failed '%s': %s", query, e)
                        # fall through to normal download

            if direct_url:
                # URL-sourced playlist — download directly without search
                try:
                    path, meta = await sc_downloader.download(direct_url, user_id)
                except Exception as e:
                    log.warning("SC batch URL download failed '%s': %s", direct_url, e)
                    not_found.append(f"{artist} — {title}")
                    continue
            else:
                # YM-sourced playlist — try SoundCloud, fallback to YouTube
                path, meta = None, {}
                sc_ok = False
                try:
                    sc_results = await sc_downloader.search(query, max_results=1)
                    if sc_results:
                        try:
                            path, meta = await sc_downloader.download(sc_results[0].url, user_id)
                            sc_ok = True
                        except Exception as e:
                            log.warning("SC batch download failed '%s': %s", query, e)
                except Exception as e:
                    log.warning("SC batch search failed '%s': %s", query, e)

                if not sc_ok:
                    try:
                        yt_results = await sc_downloader.search_youtube(query, max_results=1)
                        if not yt_results:
                            not_found.append(f"{artist} — {title}")
                            continue
                        path, meta = await sc_downloader.download(yt_results[0].url, user_id)
                    except Exception as e:
                        log.warning("YT batch fallback failed '%s': %s", query, e)
                        not_found.append(f"{artist} — {title}")
                        continue

            sent = False
            try:
                sent_msg = await progress_msg.answer_audio(
                    audio=FSInputFile(path, filename=f"{artist} - {title}.mp3"),
                    title=meta.get("title") or title,
                    performer=meta.get("artist") or artist,
                )
                sent = True
                downloaded_count += 1
                if sent_msg and sent_msg.audio and cache_key:
                    save_cached_file_id(cache_key, sent_msg.audio.file_id, "batch",
                                        artist=artist, title=title)
            except Exception as e:
                log.warning("SC batch send_audio failed '%s': %s", query, e)
                not_found.append(f"{artist} — {title}")
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

            if sent:
                try:
                    await progress_msg.edit_text(
                        f"⏳ {i}/{total} — {artist} — {title}",
                        reply_markup=sc_stop_keyboard(),
                    )
                except Exception:
                    pass

    finally:
        _cancel_events.pop(user_id, None)
        _batch_semaphore.release()

    batch_result = "stopped" if cancel_event.is_set() else "success"
    log_event(user_id, username, "sc_batch", batch_result,
              track_count=downloaded_count, detail=f"not_found:{len(not_found)}")

    update_batch_live(user_id, username, {
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "current_idx": total,
        "current_track": "—",
        "downloaded": downloaded_count,
        "failed": not_found,
        "status": "stopped" if cancel_event.is_set() else "done",
    })

    for track_name in not_found:
        log_event(user_id, username, "sc_track_fail", "error", detail=track_name)

    summary = "⛔ Скачивание остановлено." if cancel_event.is_set() else "✅ Плейлист скачан!"
    if not_found:
        nf_list = "\n".join(not_found[:20])
        summary += f"\n\n❌ Не найдено нигде ({len(not_found)}):\n{nf_list}"
        if len(not_found) > 20:
            summary += f"\n...и ещё {len(not_found) - 20}"

    try:
        await progress_msg.edit_text(summary)
    except Exception:
        await progress_msg.answer(summary)

    if await state.get_state() is not None:
        await progress_msg.answer(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
        await state.set_state(SCSearchFlow.sc_menu)
