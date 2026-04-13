"""YMShareFlow handlers: share link/iframe, filter, seek, batch download."""
import logging

import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from rapidfuzz import fuzz

from bot.states import ExportFlow, SCBatchFlow, YMShareFlow
from bot.keyboards import (
    service_keyboard,
    share_source_keyboard,
    ym_share_token_keyboard,
    ym_share_cancel_keyboard,
    ym_share_actions_keyboard,
    ym_share_back_keyboard,
    ym_share_filter_result_keyboard,
    ym_share_seek_confirm_keyboard,
    sc_resume_keyboard,
    sc_stop_keyboard,
)
from core.ym_source import YandexMusicSource
from utils.export import build_txt_file, cleanup
from utils.event_log import log_event
from utils.db import is_batch_allowed
from config import settings
from .common import (
    _get_user_info,
    _filter_by_artist,
    _parse_ym_share,
    _cancel_events,
    _YMS_INPUT_TEXT,
    _show_batch_access_page,
)
from bot.tracker import set_active_msg
from .sc_router import _run_batch_download, _try_start_or_queue

router = Router()
log = logging.getLogger(__name__)


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
    set_active_msg(user_id, status_msg.message_id)
    try:
        source = YandexMusicSource(token)
        await source._get_client()
    except Exception as e:
        log.warning("YMShare auth failed user=%s: %s", user_id, e)
        await log_event(user_id, username, "auth_fail", "error", detail=type(e).__name__)
        await status_msg.edit_text("❌ Не удалось авторизоваться. Отправь токен ещё раз.")
        return

    await log_event(user_id, username, "auth_ok", "success")
    await state.update_data(yms_token=token)
    await status_msg.edit_text(
        _YMS_INPUT_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=ym_share_cancel_keyboard(),
    )
    await state.set_state(YMShareFlow.waiting)


async def load_ym_url(
    status_msg,
    state: FSMContext,
    url: str,
    user_id: int,
    username: str | None,
) -> bool:
    """Load a YM album/playlist URL into state. Edits status_msg with result. Returns True on success."""
    data = await state.get_data()
    token = settings.YM_BOT_TOKEN or data.get("yms_token", "")
    try:
        title, tracks = await YandexMusicSource(token).get_playlist_by_url(url)
    except ValueError as e:
        log.warning("YMShare load ValueError user=%s url=%s: %s", user_id, url[:80], e)
        await status_msg.edit_text(f"❌ {e}", parse_mode="HTML", reply_markup=ym_share_cancel_keyboard())
        return False
    except Exception as e:
        log.exception("YMShare load failed user=%s url=%s: %s", user_id, url[:80], e)
        await status_msg.edit_text(
            "❌ Не удалось загрузить плейлист. Проверь ссылку и попробуй ещё раз.",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return False
    if not tracks:
        await status_msg.edit_text("😔 Плейлист пуст или недоступен.", reply_markup=ym_share_cancel_keyboard())
        return False
    await log_event(user_id, username, "yms_load", "success", track_count=len(tracks), detail=title)
    await state.update_data(yms_tracks=tracks, yms_playlist_title=title, yms_filter_artists=[], yms_filtered_tracks=None)
    safe_title = title[:50] if title else "Плейлист"
    await status_msg.edit_text(
        f'✅ Загружено <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(),
    )
    await state.set_state(YMShareFlow.actions)
    return True


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
            "❌ Не удалось распознать ссылку.\n\n"
            "Поддерживаются:\n"
            "• <code>music.yandex.ru/album/НОМЕР</code>\n"
            "• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n"
            "• <code>music.yandex.ru/playlists/lk.UUID</code>\n"
            "• Embed-код (iframe) из кнопки «Поделиться»",
            parse_mode="HTML",
            reply_markup=ym_share_cancel_keyboard(),
        )
        return

    status_msg = await message.answer("⏳ Загружаю…")
    set_active_msg(user_id, status_msg.message_id)
    await load_ym_url(status_msg, state, url, user_id, username)


@router.callback_query(YMShareFlow.actions, F.data == "yms:download_all")
async def on_yms_download_all(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="yms:back_to_actions")
        return
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    await state.update_data(sc_tracks=tracks, sc_resume_back_cb="yms_actions", sc_filter_artists=[], sc_original_tracks=None)
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
    await call.message.edit_text("Выбери источник плейлиста:", reply_markup=share_source_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(F.data == "yms:back_to_actions")
async def on_yms_back_to_actions(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    title = data.get("yms_playlist_title", "Плейлист")
    safe_title = title[:50] if title else "Плейлист"
    filter_artists = data.get("yms_filter_artists") or None
    await call.message.edit_text(
        f'✅ <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(filter_artists=filter_artists),
    )
    await state.set_state(YMShareFlow.actions)


@router.callback_query(YMShareFlow.actions, F.data.startswith("yms:rm_artist:"))
async def on_yms_remove_artist(call: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(call.data.split(":")[-1])
    except (ValueError, IndexError):
        await call.answer()
        return

    data = await state.get_data()
    artists = list(data.get("yms_filter_artists") or [])
    original_tracks = data.get("yms_tracks", [])
    title = data.get("yms_playlist_title", "Плейлист")
    safe_title = title[:50] if title else "Плейлист"

    if 0 <= idx < len(artists):
        artists.pop(idx)

    if artists:
        matched_keys: set[tuple] = set()
        for a in artists:
            for t in _filter_by_artist(original_tracks, a):
                matched_keys.add((t.get("artist", ""), t.get("title", "")))
        union_tracks = [t for t in original_tracks if (t.get("artist", ""), t.get("title", "")) in matched_keys]
    else:
        union_tracks = None

    await state.update_data(yms_filter_artists=artists, yms_filtered_tracks=union_tracks)
    await call.message.edit_text(
        f'✅ <b>«{safe_title}»</b> — {len(original_tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(filter_artists=artists or None),
    )


@router.message(YMShareFlow.filter_input)
async def on_yms_filter_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        msg = await message.answer("❌ Нужно отправить текст — имя исполнителя.", reply_markup=ym_share_back_keyboard())
        set_active_msg(message.from_user.id, msg.message_id)
        return

    query = message.text.strip()
    data = await state.get_data()
    original_tracks = data.get("yms_tracks", [])

    matched = _filter_by_artist(original_tracks, query)
    if not matched:
        msg = await message.answer(
            f"😔 Исполнитель <b>{query}</b> не найден в плейлисте.\n\nПопробуй другое имя.",
            parse_mode="HTML",
            reply_markup=ym_share_back_keyboard(),
        )
        set_active_msg(message.from_user.id, msg.message_id)
        return

    artists = list(data.get("yms_filter_artists") or [])
    if query not in artists:
        artists.append(query)

    # Recompute filtered tracks as union of all added artists
    matched_keys: set[tuple] = set()
    for a in artists:
        for t in _filter_by_artist(original_tracks, a):
            matched_keys.add((t.get("artist", ""), t.get("title", "")))
    union_tracks = [t for t in original_tracks if (t.get("artist", ""), t.get("title", "")) in matched_keys]

    await state.update_data(yms_filter_artists=artists, yms_filtered_tracks=union_tracks)

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
        await log_event(message.from_user.id, message.from_user.username, "export_filtered", "success", track_count=len(matched))
    finally:
        await cleanup(tmp_path)

    title = data.get("yms_playlist_title", "Плейлист")
    safe_title = title[:50] if title else "Плейлист"
    msg = await message.answer(
        f'✅ <b>«{safe_title}»</b> — {len(original_tracks)} треков.\n\nЧто делаем?',
        parse_mode="HTML",
        reply_markup=ym_share_actions_keyboard(filter_artists=artists),
    )
    set_active_msg(message.from_user.id, msg.message_id)
    await state.set_state(YMShareFlow.actions)


@router.callback_query(F.data == "yms:download_filtered")
async def on_yms_download_filtered(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        # Document caption can't be edited as text — send new message
        await _show_batch_access_page(call, back_cb="yms:back_to_actions", use_answer=True)
        return
    data = await state.get_data()
    filtered = data.get("yms_filtered_tracks")
    if not filtered:
        await call.answer("Данные недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.update_data(sc_tracks=filtered, sc_resume_back_cb="yms_actions", sc_filter_artists=[], sc_original_tracks=None)
    msg = await call.message.answer(
        f"📥 Готов скачать <b>{len(filtered)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    set_active_msg(call.from_user.id, msg.message_id)
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.message(YMShareFlow.seek_input)
async def on_yms_seek_input(message: Message, state: FSMContext) -> None:
    if not message.text:
        msg = await message.answer("❌ Нужно отправить текст — название трека.", reply_markup=ym_share_back_keyboard())
        set_active_msg(message.from_user.id, msg.message_id)
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
        msg = await message.answer(
            "😔 Трек не найден в плейлисте. Попробуй другое название.",
            reply_markup=ym_share_back_keyboard(),
        )
        set_active_msg(message.from_user.id, msg.message_id)
        return

    if best_idx + 1 >= len(tracks):
        msg = await message.answer(
            "ℹ️ Это последний трек в плейлисте — нечего скачивать после него.",
            reply_markup=ym_share_back_keyboard(),
        )
        set_active_msg(message.from_user.id, msg.message_id)
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
    msg = await message.answer(confirm_text, parse_mode="HTML", reply_markup=ym_share_seek_confirm_keyboard())
    set_active_msg(message.from_user.id, msg.message_id)
    await state.set_state(YMShareFlow.seek_confirm)


@router.callback_query(YMShareFlow.seek_confirm, F.data == "yms_resume:confirm")
async def on_yms_seek_confirm(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="yms:back_to_actions")
        return
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = data.get("yms_tracks", [])
    start_idx = data.get("yms_resume_idx", 0)
    await state.update_data(sc_tracks=tracks)
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, tracks, start_idx,
        f"▶️ Начинаю с трека {start_idx + 1}/{len(tracks)}…",
    )


@router.callback_query(YMShareFlow.seek_confirm, F.data == "yms_resume:retry")
async def on_yms_seek_retry(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        "⏩ Введи название трека, с которого хочешь начать скачивание:",
        reply_markup=ym_share_back_keyboard(),
    )
    await state.set_state(YMShareFlow.seek_input)
