"""SpotifyFlow handlers: public playlists + liked tracks via OAuth."""
import asyncio
import logging

import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow, SCBatchFlow, SpotifyFlow
from bot.keyboards import (
    service_keyboard,
    export_source_keyboard,
    share_source_keyboard,
    spotify_menu_keyboard,
    spotify_cancel_keyboard,
    spotify_actions_keyboard,
    spotify_filter_result_keyboard,
    sc_resume_keyboard,
)
from core.spotify_source import SpotifySource, parse_code_from_redirect
from utils.export import build_txt_file, build_csv_file, cleanup
from utils.db import is_batch_allowed
from utils.event_log import log_event
from config import settings
from .common import (
    _get_user_info,
    _filter_by_artist,
    _cancel_events,
    _pending_spotify_codes,
    _SPOTIFY_MENU_TEXT,
    _SPOTIFY_PLAYLIST_TEXT,
    _SPOTIFY_AUTH_TEXT,
    _show_batch_access_page,
)
from bot.tracker import set_active_msg
from .sc_router import _run_batch_download, _try_start_or_queue

router = Router()
log = logging.getLogger(__name__)


def _spotify_source() -> SpotifySource | None:
    if not settings.SPOTIFY_CLIENT_ID or not settings.SPOTIFY_CLIENT_SECRET:
        return None
    return SpotifySource(settings.SPOTIFY_CLIENT_ID, settings.SPOTIFY_CLIENT_SECRET)


def _auth_keyboard(auth_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Войти через Spotify", url=auth_url)],
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:to_menu")],
    ])


# ── Entry points ──────────────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_service, F.data == "service:spotify")
async def on_spotify_entry(call: CallbackQuery, state: FSMContext) -> None:
    if not _spotify_source():
        await call.answer("Spotify не настроен на этом боте.", show_alert=True)
        return
    await call.message.edit_text(_SPOTIFY_MENU_TEXT, parse_mode="HTML", reply_markup=spotify_menu_keyboard())
    await state.set_state(SpotifyFlow.menu)





# ── Menu ──────────────────────────────────────────────────────────────────────

@router.callback_query(SpotifyFlow.menu, F.data == "spotify:back")
async def on_spotify_back(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    nav_back = data.get("nav_back")
    if nav_back == "export_source":
        await call.message.edit_text("Выбери источник для экспорта:", reply_markup=export_source_keyboard())
    elif nav_back == "share_source":
        await call.message.edit_text("Выбери источник плейлиста:", reply_markup=share_source_keyboard())
    else:
        await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(SpotifyFlow.menu, F.data == "spotify:playlist")
async def on_spotify_playlist(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SPOTIFY_PLAYLIST_TEXT, parse_mode="HTML", reply_markup=spotify_cancel_keyboard())
    await state.set_state(SpotifyFlow.playlist_waiting)


@router.callback_query(SpotifyFlow.menu, F.data == "spotify:liked")
async def on_spotify_liked(call: CallbackQuery, state: FSMContext) -> None:
    source = _spotify_source()
    if not source:
        await call.answer("Spotify не настроен на этом боте.", show_alert=True)
        return
    try:
        auth_url = await source.get_auth_url(state=str(call.from_user.id))
    except Exception as e:
        log.exception("Spotify get_auth_url failed: %s", e)
        await call.answer("❌ Ошибка подключения к Spotify.", show_alert=True)
        return
    await call.message.edit_text(
        _SPOTIFY_AUTH_TEXT, parse_mode="HTML", reply_markup=_auth_keyboard(auth_url)
    )
    await state.set_state(SpotifyFlow.auth_waiting)


# ── Back to menu ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "spotify:to_menu")
async def on_spotify_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SPOTIFY_MENU_TEXT, parse_mode="HTML", reply_markup=spotify_menu_keyboard())
    await state.set_state(SpotifyFlow.menu)


# ── Auto OAuth callback (via aiohttp callback server) ────────────────────────

@router.callback_query(F.data == "spotify:load_liked_auto")
async def on_spotify_load_liked_auto(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    code = _pending_spotify_codes.pop(user_id, None)
    if not code:
        await call.answer("Код авторизации не найден. Попробуй войти снова.", show_alert=True)
        return

    source = _spotify_source()
    if not source:
        await call.answer("Spotify не настроен.", show_alert=True)
        return

    status_msg = await call.message.edit_text("⏳ Получаю токен…")
    try:
        access_token = await source.exchange_code(code)
    except Exception as e:
        log.warning("Spotify code exchange failed user=%d: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Ошибка авторизации. Попробуй войти снова.",
            reply_markup=spotify_menu_keyboard(),
        )
        await state.set_state(SpotifyFlow.menu)
        return

    await status_msg.edit_text("⏳ Загружаю лайки…")
    try:
        tracks = await source.get_liked_tracks(access_token)
    except Exception as e:
        log.exception("Spotify liked tracks failed user=%d: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить лайки. Попробуй ещё раз.",
            reply_markup=spotify_menu_keyboard(),
        )
        await state.set_state(SpotifyFlow.menu)
        return

    if not tracks:
        await status_msg.edit_text("😔 Сохранённых треков не найдено.", reply_markup=spotify_cancel_keyboard())
        return

    await log_event(user_id, username, "spotify_liked_load", "success", track_count=len(tracks))
    await state.update_data(spotify_tracks=tracks, spotify_title="Мои лайки Spotify")
    await status_msg.edit_text(
        f'✅ Загружено <b>{len(tracks)}</b> сохранённых треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=spotify_actions_keyboard(),
    )
    await state.set_state(SpotifyFlow.actions)


# ── Playlist URL input ────────────────────────────────────────────────────────

@router.message(SpotifyFlow.playlist_waiting)
async def on_spotify_playlist_input(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Отправь ссылку на плейлист.", reply_markup=spotify_cancel_keyboard())
        return

    url = message.text.strip()
    status_msg = await message.answer("⏳ Загружаю плейлист…")
    set_active_msg(user_id, status_msg.message_id)

    source = _spotify_source()
    try:
        title, tracks = await source.get_playlist(url)
    except ValueError as e:
        await status_msg.edit_text(f"❌ {e}", reply_markup=spotify_cancel_keyboard())
        return
    except Exception as e:
        log.exception("Spotify playlist load failed user=%s: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить плейлист. Проверь ссылку и попробуй ещё раз.",
            reply_markup=spotify_cancel_keyboard(),
        )
        return

    if not tracks:
        await status_msg.edit_text("😔 Плейлист пуст или недоступен.", reply_markup=spotify_cancel_keyboard())
        return

    await log_event(user_id, username, "spotify_playlist_load", "success", track_count=len(tracks), detail=title[:80])
    await state.update_data(spotify_tracks=tracks, spotify_title=title)
    safe_title = title[:50]
    await status_msg.edit_text(
        f'✅ Загружен плейлист <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=spotify_actions_keyboard(),
    )
    await state.set_state(SpotifyFlow.actions)


# ── OAuth redirect URL input ──────────────────────────────────────────────────

@router.message(SpotifyFlow.auth_waiting)
async def on_spotify_auth_input(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    source = _spotify_source()
    if not source:
        await message.answer("❌ Spotify не настроен на этом боте.")
        return

    if not message.text:
        msg = await message.answer(_SPOTIFY_AUTH_TEXT, parse_mode="HTML",
                                   reply_markup=_auth_keyboard(await source.get_auth_url()))
        set_active_msg(user_id, msg.message_id)
        return

    code = parse_code_from_redirect(message.text.strip())
    if not code:
        msg = await message.answer(
            "❌ Не удалось найти код авторизации в ссылке.\n\n"
            "Скопируй <b>полный URL</b> из адресной строки после редиректа.",
            parse_mode="HTML",
            reply_markup=_auth_keyboard(await source.get_auth_url()),
        )
        set_active_msg(user_id, msg.message_id)
        return

    status_msg = await message.answer("⏳ Получаю токен…")
    set_active_msg(user_id, status_msg.message_id)
    try:
        access_token = await source.exchange_code(code)
    except Exception as e:
        log.warning("Spotify code exchange failed user=%s: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Ошибка авторизации. Попробуй войти снова.",
            reply_markup=_auth_keyboard(await source.get_auth_url()),
        )
        return

    await status_msg.edit_text("⏳ Загружаю лайки…")
    try:
        tracks = await source.get_liked_tracks(access_token)
    except Exception as e:
        log.exception("Spotify liked tracks failed user=%s: %s", user_id, e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить лайки. Попробуй ещё раз.",
            reply_markup=_auth_keyboard(source.get_auth_url()),
        )
        return

    if not tracks:
        await status_msg.edit_text("😔 Сохранённых треков не найдено.", reply_markup=spotify_cancel_keyboard())
        return

    await log_event(user_id, username, "spotify_liked_load", "success", track_count=len(tracks))
    await state.update_data(spotify_tracks=tracks, spotify_title="Мои лайки Spotify")
    await status_msg.edit_text(
        f'✅ Загружено <b>{len(tracks)}</b> сохранённых треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=spotify_actions_keyboard(),
    )
    await state.set_state(SpotifyFlow.actions)


# ── Actions ───────────────────────────────────────────────────────────────────

@router.callback_query(SpotifyFlow.actions, F.data == "spotify:back_to_actions")
async def on_spotify_back_to_actions(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("spotify_tracks", [])
    title = data.get("spotify_title", "Spotify")
    safe_title = title[:50]
    await call.message.edit_text(
        f'✅ <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=spotify_actions_keyboard(),
    )
    await state.set_state(SpotifyFlow.actions)


@router.callback_query(SpotifyFlow.actions, F.data == "spotify:export_txt")
async def on_spotify_export_txt(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("spotify_tracks", [])
    title = data.get("spotify_title", "spotify")
    filename = f"{title[:40]}.txt"

    tmp_path = await build_txt_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"📄 <b>{title[:50]}</b> — {len(tracks)} треков",
            parse_mode="HTML",
            reply_markup=spotify_actions_keyboard(),
        )
        await log_event(call.from_user.id, call.from_user.username, "spotify_export", "success",
                  track_count=len(tracks), detail="txt")
    finally:
        await cleanup(tmp_path)


@router.callback_query(SpotifyFlow.actions, F.data == "spotify:export_csv")
async def on_spotify_export_csv(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("spotify_tracks", [])
    title = data.get("spotify_title", "spotify")
    filename = f"{title[:40]}.csv"

    tmp_path = await build_csv_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"📊 <b>{title[:50]}</b> — {len(tracks)} треков",
            parse_mode="HTML",
            reply_markup=spotify_actions_keyboard(),
        )
        await log_event(call.from_user.id, call.from_user.username, "spotify_export", "success",
                  track_count=len(tracks), detail="csv")
    finally:
        await cleanup(tmp_path)


@router.callback_query(SpotifyFlow.actions, F.data == "spotify:download")
async def on_spotify_download(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="spotify:back_to_actions")
        return
    if call.from_user.id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = data.get("spotify_tracks", [])
    await state.update_data(sc_tracks=tracks, sc_resume_back_cb="spotify_actions", sc_filter_artists=[], sc_original_tracks=None)
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.callback_query(SpotifyFlow.actions, F.data == "spotify:filter")
async def on_spotify_filter(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "🔍 Введи имя исполнителя для фильтрации:",
        reply_markup=spotify_cancel_keyboard(),
    )
    await state.set_state(SpotifyFlow.filter_input)


# ── Filter input ──────────────────────────────────────────────────────────────

@router.message(SpotifyFlow.filter_input)
async def on_spotify_filter_input(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if not message.text:
        msg = await message.answer("❌ Введи имя исполнителя.", reply_markup=spotify_cancel_keyboard())
        set_active_msg(user_id, msg.message_id)
        return

    query = message.text.strip()
    data = await state.get_data()
    tracks = data.get("spotify_tracks", [])

    matched = _filter_by_artist(tracks, query)
    if not matched:
        msg = await message.answer(
            f"😔 Исполнитель <b>{query}</b> не найден.\n\nПопробуй другое имя.",
            parse_mode="HTML",
            reply_markup=spotify_cancel_keyboard(),
        )
        set_active_msg(user_id, msg.message_id)
        return

    await state.update_data(spotify_filtered_tracks=matched)
    filename = f"{query}_tracks.txt"
    tmp_path = await build_txt_file(matched, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        doc_msg = await message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Треков исполнителя <b>{query}</b>: {len(matched)}.",
            parse_mode="HTML",
            reply_markup=spotify_filter_result_keyboard(),
        )
        set_active_msg(user_id, doc_msg.message_id)
    finally:
        await cleanup(tmp_path)

    await state.set_state(SpotifyFlow.actions)


@router.callback_query(F.data == "spotify:download_filtered")
async def on_spotify_download_filtered(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="spotify:back_to_actions", use_answer=True)
        return
    if call.from_user.id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    filtered = data.get("spotify_filtered_tracks")
    if not filtered:
        await call.answer("Данные недоступны. Начни заново.", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(sc_tracks=filtered, sc_resume_back_cb="spotify_actions", sc_filter_artists=[], sc_original_tracks=None)
    msg = await call.message.answer(
        f"📥 Готов скачать <b>{len(filtered)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    set_active_msg(call.from_user.id, msg.message_id)
    await state.set_state(SCBatchFlow.sc_resume_choice)
