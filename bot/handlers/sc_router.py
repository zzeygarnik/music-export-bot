"""SCSearchFlow + SCBatchFlow handlers, plus SC delivery helpers."""
import asyncio
import logging
import os
from collections import Counter
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow, YMShareFlow, SpotifyFlow
from bot.keyboards import (
    service_keyboard,
    sc_menu_keyboard,
    sc_cancel_keyboard,
    sc_results_keyboard,
    sc_playlists_keyboard,
    sc_resume_keyboard,
    sc_resume_confirm_keyboard,
    sc_stop_keyboard,
    sc_cancel_queue_keyboard,
    sc_offer_keyboard,
    sc_after_download_keyboard,
    sc_batch_token_keyboard,
    yt_fallback_keyboard,
    cache_results_keyboard,
    tsel_panel_keyboard,
    tsel_results_keyboard,
    tsel_selected_keyboard,
    ym_share_actions_keyboard,
    spotify_actions_keyboard,
    export_type_keyboard,
    share_source_keyboard,
)
from core.ym_source import YandexMusicSource
from core import sc_downloader
from core.sc_downloader import SCResult
from utils.event_log import log_event, update_batch_live
from utils.db import get_cached_file_id, save_cached_file_id, delete_cached_file_id, search_cache_fuzzy, is_batch_allowed
from config import settings
from rapidfuzz import fuzz
from . import common as _hcommon
from .common import (
    _get_user_info,
    _make_cache_key,
    _progress_bar,
    _cancel_events,
    _batch_semaphore,
    _batch_queue,
    _BatchQueueItem,
    _TOKEN_GUIDE,
    _SC_MENU_TEXT,
    _SC_URL_TEXT,
    _show_batch_access_page,
    _SC_URL_PLAYLIST_TEXT,
    _filter_by_artist,
    notify_admin_sc_error,
    download_with_proxy_rotation,
    download_yt_with_proxy_rotation,
    search_with_proxy_rotation,
)
from bot.tracker import set_active_msg
from bot.states import ExportFlow

router = Router()
log = logging.getLogger(__name__)


# ── Queue helpers ─────────────────────────────────────────────────────────────

async def _process_queue() -> None:
    """Start the next queued batch download if a slot is available."""
    if not _batch_queue:
        return
    item = _batch_queue.pop(0)

    # Check the user is still waiting (didn't press /start)
    data = await item.state.get_data()
    if not data.get("in_batch_queue"):
        asyncio.create_task(_process_queue())
        return

    await item.state.update_data(in_batch_queue=False)

    # Notify remaining users of their new positions
    for i, queued in enumerate(_batch_queue):
        try:
            await queued.bot.send_message(
                queued.chat_id,
                f"⏳ Ты теперь <b>#{i + 1}</b> в очереди.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    try:
        msg = await item.bot.send_message(
            item.chat_id,
            f"▶️ Твоя очередь! Начинаю скачивание <b>{len(item.tracks)}</b> треков…",
            parse_mode="HTML",
            reply_markup=sc_stop_keyboard(),
        )
        set_active_msg(item.user_id, msg.message_id)
        await item.state.set_state(SCBatchFlow.sc_downloading)
        asyncio.create_task(
            _run_batch_download(msg, item.state, item.user_id, item.username, item.tracks, item.start_idx)
        )
    except Exception as e:
        log.warning("Failed to start queued download for user=%s: %s", item.user_id, e)
        asyncio.create_task(_process_queue())


async def _try_start_or_queue(
    call,
    state,
    user_id: int,
    username,
    tracks: list,
    start_idx: int,
    start_text: str | None = None,
) -> None:
    """Start batch download immediately, or add user to queue if all slots are taken."""
    # Deduplicate tracks by (artist, title) keeping first occurrence
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for t in tracks:
        key = (t.get("artist", "").lower().strip(), t.get("title", "").lower().strip())
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    dup_count = len(tracks) - len(deduped)
    if dup_count > 0:
        log.info("Deduplicated %d duplicate tracks for user=%s (%d→%d)", dup_count, user_id, len(tracks), len(deduped))
        tracks = deduped
        if start_text:
            start_text = start_text.rstrip("…") + f"\n<i>Убрано дублей: {dup_count}.</i>"

    if _batch_semaphore.locked():
        if any(item.user_id == user_id for item in _batch_queue):
            await call.answer("⏳ Ты уже в очереди.", show_alert=True)
            return
        pos = len(_batch_queue) + 1
        _batch_queue.append(_BatchQueueItem(
            user_id=user_id,
            username=username,
            chat_id=call.message.chat.id,
            bot=call.message.bot,
            state=state,
            tracks=tracks,
            start_idx=start_idx,
        ))
        await state.update_data(in_batch_queue=True)
        await call.message.edit_text(
            f"⏳ Все слоты заняты ({settings.SC_MAX_BATCH_DOWNLOADS}/{settings.SC_MAX_BATCH_DOWNLOADS}).\n\n"
            f"Ты <b>#{pos}</b> в очереди — начнём автоматически, как только освободится место.",
            parse_mode="HTML",
            reply_markup=sc_cancel_queue_keyboard(),
        )
        await state.set_state(SCBatchFlow.sc_queued)
    else:
        text = start_text or f"▶️ Начинаю скачивание <b>{len(tracks)}</b> треков…"
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=sc_stop_keyboard())
        await state.set_state(SCBatchFlow.sc_downloading)
        asyncio.create_task(_run_batch_download(call.message, state, user_id, username, tracks, start_idx))


# ── SC: Service selection ─────────────────────────────────────────────────────

def _sc_is_disabled_for(user_id: int) -> bool:
    """Return True if SC downloads are currently disabled for this non-admin user."""
    is_admin = settings.ADMIN_ID != 0 and user_id == settings.ADMIN_ID
    return not is_admin and not _hcommon.sc_downloads_enabled


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:search")
async def on_sc_search(call: CallbackQuery, state: FSMContext) -> None:
    if _sc_is_disabled_for(call.from_user.id):
        await call.answer("⛔ Скачивание с SoundCloud временно недоступно. Попробуй позже.", show_alert=True)
        return
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
    await state.update_data(sc_input_mode="url", sc_url_allow_playlist=False)
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
    if _sc_is_disabled_for(call.from_user.id):
        await call.answer("⛔ Скачивание с SoundCloud временно недоступно. Попробуй позже.", show_alert=True)
        return
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="batch_req_back:sc_menu")
        return
    await call.message.edit_text(
        "📥 <b>Скачать плейлист с SoundCloud</b>\n\n" + _TOKEN_GUIDE,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=sc_batch_token_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_ym_token)


@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:back")
async def on_sc_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(F.data == "batch_req_back:sc_menu")
async def on_batch_req_back_sc_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


# ── SC: Inline offer after YM .txt export ─────────────────────────────────────

@router.callback_query(F.data == "sc:batch_from_ym")
async def on_sc_batch_from_ym(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="batch_req_back:main")
        return
    data = await state.get_data()
    sc_tracks = data.get("sc_tracks")
    if not sc_tracks:
        await call.answer(
            "Данные плейлиста недоступны. Введи /start чтобы начать заново.",
            show_alert=True,
        )
        return

    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.update_data(sc_resume_back_cb="sc_menu")
    msg = await call.message.answer(
        f"📥 Готов скачать <b>{len(sc_tracks)}</b> треков с SoundCloud.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    set_active_msg(call.from_user.id, msg.message_id)
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: Search query ──────────────────────────────────────────────────────────

@router.message(SCSearchFlow.sc_search_query)
async def on_sc_search_query(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text:
        await message.answer("❌ Нужно отправить текстовый запрос.", reply_markup=sc_cancel_keyboard())
        return

    query = message.text.strip()

    status_msg = await message.answer("🔍 Ищу в базе…")
    set_active_msg(user_id, status_msg.message_id)
    cache_hits = await search_cache_fuzzy(query)
    if cache_hits:
        await state.update_data(cache_pending_query=query, cache_fallback_source="sc",
                                cache_hits=cache_hits)
        await state.set_state(SCSearchFlow.sc_cache_results)
        lines = "\n".join(
            f"• <b>{h['artist']} — {h['title']}</b>" if (h.get('artist') or h.get('title'))
            else f"• <b>{h.get('cache_key', '?')}</b>"
            for h in cache_hits
        )
        await status_msg.edit_text(
            f"⚡ Нашёл в кэше — это нужный трек?\n\n{lines}",
            parse_mode="HTML",
            reply_markup=cache_results_keyboard(cache_hits, "sc"),
        )
        return

    await status_msg.edit_text("🔍 Ищу на SoundCloud…")

    try:
        results = await search_with_proxy_rotation(query, max_results=5, bot=message.bot)
    except Exception as e:
        log.exception("SC search error user=%s: %s", user_id, e)
        await state.update_data(sc_yt_fallback_query=query)
        await status_msg.edit_text(
            f"😔 SoundCloud недоступен.\n\nПопробовать найти <b>{query}</b> на YouTube?",
            parse_mode="HTML",
            reply_markup=yt_fallback_keyboard(),
        )
        await state.set_state(SCSearchFlow.sc_menu)
        return

    if not results:
        await status_msg.edit_text(
            "😔 Ничего не найдено. Попробуй другой запрос.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    best_idx, best_score = 0, 0
    for i, r in enumerate(results):
        score = fuzz.token_sort_ratio(query.lower(), f"{r.artist} {r.title}".lower())
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= 80:
        best = results[best_idx]
        pre_cached = await get_cached_file_id(_make_cache_key(best.artist, best.title))
        await status_msg.edit_text(
            f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
            parse_mode="HTML",
        )
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

    status_msg = await message.answer("🔍 Ищу в базе…")
    set_active_msg(user_id, status_msg.message_id)
    cache_hits = await search_cache_fuzzy(query)
    if cache_hits:
        await state.update_data(cache_pending_query=query, cache_fallback_source="yt",
                                cache_hits=cache_hits)
        await state.set_state(SCSearchFlow.sc_cache_results)
        lines = "\n".join(
            f"• <b>{h['artist']} — {h['title']}</b>" if (h.get('artist') or h.get('title'))
            else f"• <b>{h.get('cache_key', '?')}</b>"
            for h in cache_hits
        )
        await status_msg.edit_text(
            f"⚡ Нашёл в кэше — это нужный трек?\n\n{lines}",
            parse_mode="HTML",
            reply_markup=cache_results_keyboard(cache_hits, "yt"),
        )
        return

    await status_msg.edit_text("🔍 Ищу на YouTube…")

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
        pre_cached = await get_cached_file_id(_make_cache_key(best.artist, best.title))
        await status_msg.edit_text(
            f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
            parse_mode="HTML",
        )
        await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username, source="yt")
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
    await _sc_download_and_send(call.message, state, result, user_id, return_to_menu=True, username=username, source="yt")


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
    display = (
        f"{hit['artist']} — {hit['title']}" if (hit.get('artist') or hit.get('title'))
        else hit.get('cache_key', '?')
    )
    await call.message.edit_text(
        f"⚡ Отправляю из кэша: <b>{display}</b>…",
        parse_mode="HTML",
    )
    try:
        await call.message.answer_audio(
            audio=hit["file_id"],
            title=hit["title"],
            performer=hit["artist"],
        )
        await log_event(user_id, username, "sc_search", "success",
                  track_count=1, detail=f"{hit['artist']} — {hit['title']} [cache]")
        try:
            await call.message.delete()
        except Exception:
            pass
    except Exception as e:
        log.warning("Cache send_audio stale file_id user=%s key=%s: %s", user_id, hit["cache_key"], e)
        await delete_cached_file_id(hit["cache_key"])
        query = data.get("cache_pending_query", display)
        source = data.get("cache_fallback_source", "sc")
        await call.message.edit_text("⏳ Кэш устарел, ищу заново…")
        await _run_search_after_cache(call.message, state, query, source, user_id, username)
        return

    done_msg = await call.message.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
    set_active_msg(call.from_user.id, done_msg.message_id)
    await state.set_state(SCSearchFlow.sc_menu)


async def _run_search_after_cache(
    status_msg,
    state: FSMContext,
    query: str,
    source: str,
    user_id: int,
    username: str,
) -> None:
    """Run SC or YT search after a cache miss or stale file_id. Edits status_msg in place."""
    if source == "sc":
        await status_msg.edit_text("🔍 Ищу на SoundCloud…")
        try:
            results = await search_with_proxy_rotation(query, max_results=5, bot=status_msg.bot)
        except Exception as e:
            log.exception("SC search error user=%s: %s", user_id, e)
            await state.update_data(sc_yt_fallback_query=query)
            await status_msg.edit_text(
                f"😔 SoundCloud недоступен.\n\nПопробовать найти <b>{query}</b> на YouTube?",
                parse_mode="HTML",
                reply_markup=yt_fallback_keyboard(),
            )
            await state.set_state(SCSearchFlow.sc_menu)
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
            pre_cached = await get_cached_file_id(_make_cache_key(best.artist, best.title))
            await status_msg.edit_text(
                f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
                parse_mode="HTML",
            )
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
        await status_msg.edit_text("🔍 Ищу на YouTube…")
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
            pre_cached = await get_cached_file_id(_make_cache_key(best.artist, best.title))
            await status_msg.edit_text(
                f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
                parse_mode="HTML",
            )
            await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username, source="yt")
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


@router.callback_query(SCSearchFlow.sc_cache_results, F.data == "cache_miss")
async def on_cache_miss(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    query = data.get("cache_pending_query", "")
    source = data.get("cache_fallback_source", "sc")
    await _run_search_after_cache(call.message, state, query, source, user_id, username)


# ── Inline search: deep link from inline mode ─────────────────────────────────

@router.callback_query(SCSearchFlow.sc_menu, F.data.startswith("inline_search:"))
async def on_inline_search(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    action = call.data.split(":", 1)[1]

    if action == "cancel":
        await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
        await state.clear()
        return

    data = await state.get_data()
    query = data.get("inline_search_query", "")
    if not query:
        await call.answer("Запрос не найден.", show_alert=True)
        return

    source = action  # "sc" or "yt"
    await state.update_data(cache_pending_query=query, cache_fallback_source=source)

    cache_hits = await search_cache_fuzzy(query)
    if cache_hits:
        await state.update_data(cache_hits=cache_hits)
        lines = "\n".join(
            f"• <b>{h['artist']} — {h['title']}</b>" if (h.get("artist") or h.get("title"))
            else f"• <b>{h.get('cache_key', '?')}</b>"
            for h in cache_hits
        )
        await call.message.edit_text(
            f"⚡ Нашёл в кэше — это нужный трек?\n\n{lines}",
            parse_mode="HTML",
            reply_markup=cache_results_keyboard(cache_hits, source),
        )
        await state.set_state(SCSearchFlow.sc_cache_results)
        return

    await _run_search_after_cache(call.message, state, query, source, user_id, username)


# ── SC: Download by URL ───────────────────────────────────────────────────────

@router.message(SCSearchFlow.sc_url_input)
async def on_sc_url_input(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)

    if not message.text or not message.text.strip().startswith("http"):
        await message.answer("❌ Нужно отправить ссылку (должна начинаться с http).",
                             reply_markup=sc_cancel_keyboard())
        return

    url = message.text.strip()
    data = await state.get_data()
    yt_only = data.get("sc_url_yt_only", False)

    if yt_only and not any(h in url for h in ("youtube.com", "youtu.be", "music.youtube.com")):
        await message.answer(
            "❌ Нужна ссылка на YouTube (youtube.com или youtu.be).",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    status_msg = await message.answer("⏳ Получаю информацию по ссылке…")
    set_active_msg(user_id, status_msg.message_id)

    try:
        info = await sc_downloader.extract_url_info(url)
    except Exception as e:
        log.warning("SC URL extract failed user=%s url=%s: %s", user_id, url, e)
        await status_msg.edit_text(
            "❌ Не удалось получить информацию. Проверь ссылку и попробуй ещё раз.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    allow_playlist = data.get("sc_url_allow_playlist", True)

    if info["type"] == "track":
        result = info["result"]
        await status_msg.edit_text(
            f"⏳ Скачиваю: <b>{result.artist} — {result.title}</b>…",
            parse_mode="HTML",
        )
        await _sc_download_and_send(status_msg, state, result, user_id,
                                    return_to_menu=True, username=username)
    else:
        if not allow_playlist:
            await status_msg.edit_text(
                "📋 Это плейлист или альбом.\n\n"
                "Для скачивания плейлистов и альбомов используй раздел "
                "<b>«Плейлист / Альбом по ссылке»</b> в главном меню.",
                parse_mode="HTML",
                reply_markup=sc_cancel_keyboard(),
            )
            return
        entries = info["entries"]
        title = info["title"]
        if not entries:
            await status_msg.edit_text(
                "😔 Плейлист пуст или не удалось загрузить треки.",
                reply_markup=sc_cancel_keyboard(),
            )
            return
        back_cb = data.get("sc_resume_back_cb", "sc_menu")
        tracks = [{"url": e.url, "artist": e.artist, "title": e.title} for e in entries]
        await state.update_data(sc_tracks=tracks, sc_resume_back_cb=back_cb, sc_filter_artists=[], sc_original_tracks=None)
        await status_msg.edit_text(
            f'<tg-emoji emoji-id="6039802767931871481">📥</tg-emoji> '
            f'Найдено <b>{len(tracks)}</b> треков в плейлисте «{title}».\n\nС какого трека начать?',
            parse_mode="HTML",
            reply_markup=sc_resume_keyboard(),
        )
        await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: YouTube fallback ──────────────────────────────────────────────────────

@router.callback_query(F.data == "sc:yt_fallback")
async def on_sc_yt_fallback(call: CallbackQuery, state: FSMContext) -> None:
    user_id, username = _get_user_info(call)
    data = await state.get_data()
    query = (data.get("sc_yt_fallback_query") or "").strip()

    if not query:
        await call.answer("Запрос не найден.", show_alert=True)
        return

    await call.answer()
    status_msg = await call.message.edit_text("🔍 Ищу на YouTube…")

    try:
        results = await sc_downloader.search_youtube(query, max_results=5)
    except Exception as e:
        log.exception("YT fallback search failed user=%s query=%r: %s", user_id, query, e)
        await status_msg.edit_text("❌ Ошибка поиска на YouTube.", reply_markup=sc_cancel_keyboard())
        return

    if not results:
        await status_msg.edit_text("😔 Ничего не найдено на YouTube.", reply_markup=sc_cancel_keyboard())
        return

    best = results[0]
    best_score = fuzz.token_sort_ratio(query.lower(), f"{best.artist} {best.title}".lower())

    if best_score >= 80:
        pre_cached = await get_cached_file_id(_make_cache_key(best.artist, best.title))
        await status_msg.edit_text(
            f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
            parse_mode="HTML",
        )
        await _sc_download_and_send(status_msg, state, best, user_id, return_to_menu=True, username=username, source="yt")
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


# ── SC: Cancel (back to menu) ─────────────────────────────────────────────────

@router.callback_query(F.data == "sc:cancel")
async def on_sc_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("sc_cancel_target") == "share_source":
        await state.update_data(sc_cancel_target=None)
        await call.message.edit_text("Выбери источник плейлиста:", reply_markup=share_source_keyboard())
        await state.set_state(ExportFlow.choosing_service)
        return
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
        await log_event(user_id, username, "auth_fail", "error", detail=type(e).__name__)
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

    await state.update_data(sc_tracks=tracks, sc_resume_back_cb="sc_ym_playlists", sc_filter_artists=[], sc_original_tracks=None)
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков с SoundCloud из «{title}».\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC: Resume choice ─────────────────────────────────────────────────────────

@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:back")
async def on_sc_resume_back(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    back_cb = data.get("sc_resume_back_cb", "sc_menu")

    if back_cb == "sc_ym_playlists":
        playlists = data.get("sc_playlists", [])
        if playlists:
            await call.message.edit_text("📋 Выбери плейлист:", reply_markup=sc_playlists_keyboard(playlists))
            await state.set_state(SCBatchFlow.sc_ym_playlist)
            return
        back_cb = "sc_menu"

    if back_cb == "yms_actions":
        tracks = data.get("yms_tracks", [])
        title = data.get("yms_playlist_title", "Плейлист")
        safe_title = (title[:50] if title else "Плейлист")
        await call.message.edit_text(
            f'✅ <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
            parse_mode="HTML",
            reply_markup=ym_share_actions_keyboard(),
        )
        await state.set_state(YMShareFlow.actions)
        return

    if back_cb == "spotify_actions":
        tracks = data.get("spotify_tracks", [])
        title = data.get("spotify_title", "Spotify")
        safe_title = title[:50]
        await call.message.edit_text(
            f'✅ <b>«{safe_title}»</b> — {len(tracks)} треков.\n\nЧто делаем?',
            parse_mode="HTML",
            reply_markup=spotify_actions_keyboard(),
        )
        await state.set_state(SpotifyFlow.actions)
        return

    if back_cb == "export_actions":
        await call.message.edit_text(
            "Что экспортируем?",
            reply_markup=export_type_keyboard(),
        )
        await state.set_state(ExportFlow.choosing_export_type)
        return

    if back_cb == "share_source":
        await call.message.edit_text("Выбери источник плейлиста:", reply_markup=share_source_keyboard())
        await state.set_state(ExportFlow.choosing_service)
        return

    # default: sc_menu
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:start")
async def on_sc_resume_start(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, tracks, 0,
        f"▶️ Начинаю с первого трека (всего {len(tracks)})…",
    )


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:start_reversed")
async def on_sc_resume_start_reversed(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = list(reversed(data.get("sc_tracks", [])))
    await state.update_data(sc_tracks=tracks)
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, tracks, 0,
        f"▶️ Начинаю с первого трека (всего {len(tracks)})…",
    )


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
        msg = await message.answer(
            "😔 Трек не найден в плейлисте. Попробуй другое название.",
            reply_markup=sc_cancel_keyboard(),
        )
        set_active_msg(message.from_user.id, msg.message_id)
        return

    next_idx = best_idx + 1
    if next_idx >= len(tracks):
        msg = await message.answer(
            "ℹ️ Это последний трек в плейлисте — нечего скачивать после него.",
            reply_markup=sc_cancel_keyboard(),
        )
        set_active_msg(message.from_user.id, msg.message_id)
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
    msg = await message.answer(confirm_text, parse_mode="HTML", reply_markup=sc_resume_confirm_keyboard())
    set_active_msg(message.from_user.id, msg.message_id)
    await state.set_state(SCBatchFlow.sc_resume_confirm)


@router.callback_query(SCBatchFlow.sc_resume_confirm, F.data == "sc_resume:confirm")
async def on_sc_resume_confirm(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    start_idx = data.get("sc_resume_idx", 0)
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, tracks, start_idx,
        f"▶️ Начинаю с трека {start_idx + 1}/{len(tracks)}…",
    )


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


@router.callback_query(SCBatchFlow.sc_queued, F.data == "sc:cancel_queue")
async def on_cancel_queue(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    _batch_queue[:] = [item for item in _batch_queue if item.user_id != user_id]
    await state.update_data(in_batch_queue=False)
    await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(F.data == "sc:retry_failed")
async def on_sc_retry_failed(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    data = await state.get_data()
    retry_tracks = data.get("sc_retry_tracks", [])
    if not retry_tracks:
        await call.answer("Нет треков для повтора.", show_alert=True)
        return
    await state.update_data(sc_tracks=retry_tracks, sc_retry_tracks=[])
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, retry_tracks, 0,
        f"🔄 Повторяю скачивание <b>{len(retry_tracks)}</b> не найденных треков.",
    )


# ── Track selection ───────────────────────────────────────────────────────────

def _tsel_key(t: dict) -> str:
    return f"{t.get('artist', '')}||{t.get('title', '')}"


def _search_in_playlist(query: str, tracks: list[dict]) -> list[dict]:
    q = query.lower().strip()
    scored = []
    for t in tracks:
        artist = (t.get("artist") or "").lower()
        title = (t.get("title") or "").lower()
        score = max(
            fuzz.partial_ratio(q, artist),
            fuzz.partial_ratio(q, title),
            fuzz.token_set_ratio(q, f"{artist} {title}"),
        )
        if score >= 70:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:2]]


def _tsel_panel_text(sel_count: int, total: int) -> str:
    return (
        f"🎯 <b>Выбор треков для скачивания</b>\n\n"
        f"Всего в плейлисте: {total}\n"
        f"Выбрано: <b>{sel_count}</b>\n\n"
        f"Введи название трека или исполнителя для поиска."
    )


def _tsel_results_text(query: str, results: list[dict], selected_keys: set) -> str:
    lines = [f"🔍 <b>«{query}»</b> — найдено {len(results)}:\n"]
    for t in results:
        mark = "✅" if _tsel_key(t) in selected_keys else "•"
        lines.append(f"{mark} {t.get('artist', '?')} — {t.get('title', '?')}")
    return "\n".join(lines)


def _tsel_sel_text(selected: list[dict], page: int, page_size: int = 8) -> str:
    total = len(selected)
    start = page * page_size
    lines = [f"📋 <b>Выбранные треки</b> ({total}):\n"]
    for i, t in enumerate(selected[start:start + page_size], start=start + 1):
        lines.append(f"{i}. {t.get('artist', '?')} — {t.get('title', '?')}")
    pages = (total + page_size - 1) // page_size
    if pages > 1:
        lines.append(f"\n<i>Страница {page + 1} из {pages}</i>")
    return "\n".join(lines)


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:track_select")
async def on_track_select_start(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    if not tracks:
        await call.answer("Нет треков для выбора.", show_alert=True)
        return
    await state.update_data(
        tsel_selected=[], tsel_results=[], tsel_query="",
        tsel_artist_all=None, tsel_msg_id=call.message.message_id,
    )
    await call.message.edit_text(
        _tsel_panel_text(0, len(tracks)),
        parse_mode="HTML",
        reply_markup=tsel_panel_keyboard(0),
    )
    await state.set_state(SCBatchFlow.track_selection)


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data == "sc_resume:filter_artist")
async def on_sc_resume_filter_artist(call: CallbackQuery, state: FSMContext) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="sc_resume:filter_back")]
    ])
    await call.message.edit_text("🔍 Введи имя исполнителя для фильтрации:", reply_markup=kb)
    await state.set_state(SCBatchFlow.filter_input)


@router.callback_query(SCBatchFlow.filter_input, F.data == "sc_resume:filter_back")
async def on_sc_filter_back(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    filter_artists = data.get("sc_filter_artists") or None
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(filter_artists=filter_artists),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.message(SCBatchFlow.filter_input)
async def on_sc_filter_input(message: Message, state: FSMContext) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    if not message.text:
        return
    query = message.text.strip()
    data = await state.get_data()

    # Save original tracks on first filter application
    original_tracks = data.get("sc_original_tracks") or data.get("sc_tracks", [])

    matched = _filter_by_artist(original_tracks, query)
    if not matched:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="sc_resume:filter_back")]
        ])
        msg = await message.answer(
            f"😔 Исполнитель <b>{query}</b> не найден в плейлисте.\n\nПопробуй другое имя.",
            parse_mode="HTML", reply_markup=kb,
        )
        set_active_msg(message.from_user.id, msg.message_id)
        return

    artists = list(data.get("sc_filter_artists") or [])
    if query not in artists:
        artists.append(query)

    # Recompute sc_tracks as union of all filtered artists
    matched_keys: set[tuple] = set()
    for a in artists:
        for t in _filter_by_artist(original_tracks, a):
            matched_keys.add((t.get("artist", ""), t.get("title", "")))
    union_tracks = [t for t in original_tracks if (t.get("artist", ""), t.get("title", "")) in matched_keys]

    await state.update_data(sc_tracks=union_tracks, sc_filter_artists=artists, sc_original_tracks=original_tracks)
    msg = await message.answer(
        f"✅ Найдено <b>{len(matched)}</b> треков исполнителя <b>{query}</b>. "
        f"Всего в фильтре: <b>{len(union_tracks)}</b>.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(filter_artists=artists),
    )
    set_active_msg(message.from_user.id, msg.message_id)
    await state.set_state(SCBatchFlow.sc_resume_choice)


@router.callback_query(SCBatchFlow.sc_resume_choice, F.data.startswith("sc_resume:rm_artist:"))
async def on_sc_rm_artist(call: CallbackQuery, state: FSMContext) -> None:
    try:
        idx = int(call.data.split(":")[-1])
    except (ValueError, IndexError):
        await call.answer()
        return

    data = await state.get_data()
    artists = list(data.get("sc_filter_artists") or [])
    original_tracks = data.get("sc_original_tracks") or data.get("sc_tracks", [])

    if 0 <= idx < len(artists):
        artists.pop(idx)

    if artists:
        matched_keys: set[tuple] = set()
        for a in artists:
            for t in _filter_by_artist(original_tracks, a):
                matched_keys.add((t.get("artist", ""), t.get("title", "")))
        union_tracks = [t for t in original_tracks if (t.get("artist", ""), t.get("title", "")) in matched_keys]
    else:
        union_tracks = list(original_tracks)

    await state.update_data(sc_tracks=union_tracks, sc_filter_artists=artists)
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(union_tracks)}</b> треков.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(filter_artists=artists or None),
    )


@router.message(SCBatchFlow.track_selection)
async def on_tsel_search(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    if not message.text:
        return

    query = message.text.strip()
    data = await state.get_data()
    all_tracks = data.get("sc_tracks", [])
    selected = data.get("tsel_selected", [])
    selected_keys = {_tsel_key(t) for t in selected}
    results = _search_in_playlist(query, all_tracks)

    artist_all = None
    if results:
        counts = Counter(t.get("artist", "") for t in results)
        top_artist, top_count = counts.most_common(1)[0]
        if top_count >= 2:
            full_count = sum(
                1 for t in all_tracks
                if (t.get("artist") or "").lower() == top_artist.lower()
            )
            artist_all = [top_artist, full_count]

    await state.update_data(tsel_results=results, tsel_query=query, tsel_artist_all=artist_all)

    if not results:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        text = f"🔍 По запросу <b>«{query}»</b> ничего не найдено.\n\nПопробуй другой запрос."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← К поиску", callback_data="tsel:back_panel")]
        ])
    else:
        text = _tsel_results_text(query, results, selected_keys)
        kb = tsel_results_keyboard(results, selected_keys, artist_all, len(selected))

    msg_id = data.get("tsel_msg_id")
    if msg_id:
        try:
            await message.bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=msg_id,
                parse_mode="HTML", reply_markup=kb,
            )
            return
        except Exception:
            pass
    new_msg = await message.answer(text, parse_mode="HTML", reply_markup=kb)
    set_active_msg(message.from_user.id, new_msg.message_id)
    await state.update_data(tsel_msg_id=new_msg.message_id)


@router.callback_query(SCBatchFlow.track_selection, F.data.startswith("tsel:add:"))
async def on_tsel_add(call: CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[-1])
    data = await state.get_data()
    results = data.get("tsel_results", [])
    selected = list(data.get("tsel_selected", []))
    if idx >= len(results):
        await call.answer()
        return
    track = results[idx]
    key = _tsel_key(track)
    selected_keys = {_tsel_key(t) for t in selected}
    if key not in selected_keys:
        selected.append(track)
        await state.update_data(tsel_selected=selected)
        selected_keys.add(key)
        await call.answer("✅ Добавлено")
    else:
        await call.answer("Уже в списке")
    query = data.get("tsel_query", "")
    artist_all = data.get("tsel_artist_all")
    try:
        await call.message.edit_text(
            _tsel_results_text(query, results, selected_keys),
            parse_mode="HTML",
            reply_markup=tsel_results_keyboard(results, selected_keys, artist_all, len(selected)),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data.startswith("tsel:rem:"))
async def on_tsel_rem(call: CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[-1])
    data = await state.get_data()
    results = data.get("tsel_results", [])
    selected = list(data.get("tsel_selected", []))
    if idx >= len(results):
        await call.answer()
        return
    key = _tsel_key(results[idx])
    selected = [t for t in selected if _tsel_key(t) != key]
    await state.update_data(tsel_selected=selected)
    selected_keys = {_tsel_key(t) for t in selected}
    await call.answer("❌ Удалено")
    query = data.get("tsel_query", "")
    artist_all = data.get("tsel_artist_all")
    try:
        await call.message.edit_text(
            _tsel_results_text(query, results, selected_keys),
            parse_mode="HTML",
            reply_markup=tsel_results_keyboard(results, selected_keys, artist_all, len(selected)),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data == "tsel:add_all")
async def on_tsel_add_all(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    all_tracks = data.get("sc_tracks", [])
    selected = list(data.get("tsel_selected", []))
    artist_all = data.get("tsel_artist_all")
    if not artist_all:
        await call.answer("Нет данных об артисте.", show_alert=True)
        return
    artist_name = artist_all[0]
    selected_keys = {_tsel_key(t) for t in selected}
    added = 0
    for t in all_tracks:
        if (t.get("artist") or "").lower() == artist_name.lower():
            if _tsel_key(t) not in selected_keys:
                selected.append(t)
                selected_keys.add(_tsel_key(t))
                added += 1
    await state.update_data(tsel_selected=selected)
    await call.answer(f"✅ Добавлено {added} треков")
    total = len(all_tracks)
    try:
        await call.message.edit_text(
            _tsel_panel_text(len(selected), total),
            parse_mode="HTML",
            reply_markup=tsel_panel_keyboard(len(selected)),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data.startswith("tsel:show_sel:"))
async def on_tsel_show_sel(call: CallbackQuery, state: FSMContext) -> None:
    page = int(call.data.split(":")[-1])
    data = await state.get_data()
    selected = data.get("tsel_selected", [])
    if not selected:
        await call.answer("Список пуст.", show_alert=True)
        return
    try:
        await call.message.edit_text(
            _tsel_sel_text(selected, page),
            parse_mode="HTML",
            reply_markup=tsel_selected_keyboard(selected, page),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data.startswith("tsel:rem_sel:"))
async def on_tsel_rem_sel(call: CallbackQuery, state: FSMContext) -> None:
    idx = int(call.data.split(":")[-1])
    data = await state.get_data()
    selected = list(data.get("tsel_selected", []))
    if idx >= len(selected):
        await call.answer()
        return
    removed = selected.pop(idx)
    await state.update_data(tsel_selected=selected)
    await call.answer(f"❌ {removed.get('title', '?')}")
    total = len(data.get("sc_tracks", []))
    if not selected:
        try:
            await call.message.edit_text(
                _tsel_panel_text(0, total),
                parse_mode="HTML",
                reply_markup=tsel_panel_keyboard(0),
            )
        except Exception:
            pass
        return
    page_size = 8
    page = idx // page_size
    if page * page_size >= len(selected):
        page = max(0, page - 1)
    try:
        await call.message.edit_text(
            _tsel_sel_text(selected, page),
            parse_mode="HTML",
            reply_markup=tsel_selected_keyboard(selected, page),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data.startswith("tsel:sel_page:"))
async def on_tsel_sel_page(call: CallbackQuery, state: FSMContext) -> None:
    page = int(call.data.split(":")[-1])
    data = await state.get_data()
    selected = data.get("tsel_selected", [])
    try:
        await call.message.edit_text(
            _tsel_sel_text(selected, page),
            parse_mode="HTML",
            reply_markup=tsel_selected_keyboard(selected, page),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data == "tsel:back_panel")
async def on_tsel_back_panel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("tsel_selected", [])
    total = len(data.get("sc_tracks", []))
    try:
        await call.message.edit_text(
            _tsel_panel_text(len(selected), total),
            parse_mode="HTML",
            reply_markup=tsel_panel_keyboard(len(selected)),
        )
    except Exception:
        pass


@router.callback_query(SCBatchFlow.track_selection, F.data == "tsel:confirm")
async def on_tsel_confirm(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("tsel_selected", [])
    if not selected:
        await call.answer("Выбери хотя бы один трек.", show_alert=True)
        return
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    await state.update_data(sc_tracks=selected)
    await _try_start_or_queue(
        call, state, user_id, call.from_user.username, selected, 0,
        f"▶️ Начинаю скачивание <b>{len(selected)}</b> выбранных треков…",
    )


@router.callback_query(SCBatchFlow.track_selection, F.data == "tsel:cancel")
async def on_tsel_cancel(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("sc_tracks", [])
    filter_artists = data.get("sc_filter_artists") or None
    await call.message.edit_text(
        f"📥 Готов скачать <b>{len(tracks)}</b> треков.\n\nС какого трека начать?",
        parse_mode="HTML",
        reply_markup=sc_resume_keyboard(filter_artists=filter_artists),
    )
    await state.set_state(SCBatchFlow.sc_resume_choice)


# ── SC delivery helpers ───────────────────────────────────────────────────────

async def _sc_download_and_send(
    msg: Message,
    state: FSMContext,
    result: SCResult,
    user_id: int,
    return_to_menu: bool = True,
    username: str | None = None,
    source: str = "sc",
) -> None:
    cache_key = _make_cache_key(result.artist, result.title)
    cached_file_id = await get_cached_file_id(cache_key) if cache_key else None

    if cached_file_id:
        try:
            await msg.answer_audio(
                audio=cached_file_id,
                title=result.title,
                performer=result.artist,
            )
            await log_event(user_id, username, f"{source}_search", "success",
                      track_count=1, detail=f"{result.artist} — {result.title}")
            try:
                await msg.delete()
            except Exception:
                pass
            if return_to_menu and await state.get_state() is not None:
                done_msg = await msg.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
                set_active_msg(msg.chat.id, done_msg.message_id)
                await state.set_state(SCSearchFlow.sc_menu)
            return
        except Exception as e:
            log.warning("SC send_audio (cache) failed user=%s, falling back to download: %s", user_id, e)
            if cache_key:
                await delete_cached_file_id(cache_key)

    try:
        if source == "sc":
            path, meta = await download_with_proxy_rotation(result.url, user_id, msg.bot)
        else:
            path, meta = await sc_downloader.download(result.url, user_id)
    except sc_downloader.SCBanError:
        log.warning("SC download ban, all proxies exhausted user=%s url=%s", user_id, result.url)
        await log_event(user_id, username, "sc_search", "error", detail="ban_all_proxies")
        fallback_query = f"{result.artist} — {result.title}" if (result.artist and result.title) else (result.title or result.artist or "")
        await state.update_data(sc_yt_fallback_query=fallback_query)
        await msg.edit_text(
            f"😔 SoundCloud недоступен.\n\nПопробовать найти <b>{fallback_query}</b> на YouTube?",
            parse_mode="HTML",
            reply_markup=yt_fallback_keyboard(),
        )
        return
    except Exception as e:
        log.warning("SC download failed user=%s url=%s: %s", user_id, result.url, e)
        await log_event(user_id, username, f"{source}_search", "error", detail="download_failed")
        if source == "sc":
            track_label = f"{result.artist} — {result.title}" if (result.artist or result.title) else result.url
            asyncio.create_task(notify_admin_sc_error(
                msg.bot, user_id, username,
                f"Одиночное скачивание не удалось: {track_label}",
            ))
        await msg.edit_text(
            "❌ Не удалось скачать трек. Возможно, трек доступен только по подписке Go+.",
            reply_markup=sc_cancel_keyboard(),
        )
        return

    try:
        await msg.edit_text(
            f"⏳ Выгружаю трек: <b>{result.artist} — {result.title}</b>…",
            parse_mode="HTML",
        )
    except Exception:
        pass

    try:
        sent_msg = await msg.answer_audio(
            audio=FSInputFile(path, filename=f"{result.artist} - {result.title}.mp3"),
            title=meta.get("title") or result.title,
            performer=meta.get("artist") or result.artist,
        )
        if sent_msg and sent_msg.audio and cache_key:
            await save_cached_file_id(cache_key, sent_msg.audio.file_id, "manual",
                                artist=result.artist, title=result.title)
        await log_event(user_id, username, f"{source}_search", "success",
                  track_count=1, detail=f"{result.artist} — {result.title}")
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log.exception("SC send_audio failed user=%s: %s", user_id, e)
        await log_event(user_id, username, f"{source}_search", "error", detail="send_failed")
        await msg.edit_text("❌ Не удалось отправить файл.", reply_markup=sc_cancel_keyboard())
        return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if return_to_menu and await state.get_state() is not None:
        done_msg = await msg.answer("✅ Готово! Скачать ещё?", reply_markup=sc_after_download_keyboard())
        set_active_msg(msg.chat.id, done_msg.message_id)
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
    failed_tracks: list[dict] = []
    downloaded_count = 0
    cache_hits_count = 0  # tracks served from file_id cache without re-downloading
    sc_error_count = 0   # SC-specific download failures
    yt_error_count = 0   # YT direct download failures
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    await update_batch_live(user_id, username, {
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

            await update_batch_live(user_id, username, {
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
            cache_key = _make_cache_key(artist, title) if (artist or title) else None

            # ── Cache lookup ───────────────────────────────────────────────────
            if cache_key:
                cached_file_id = await get_cached_file_id(cache_key)
                if cached_file_id:
                    try:
                        await progress_msg.answer_audio(
                            audio=cached_file_id,
                            title=title,
                            performer=artist,
                        )
                        downloaded_count += 1
                        cache_hits_count += 1
                        try:
                            await progress_msg.edit_text(
                                f"⚡ {_progress_bar(i, total)} — {artist} — {title}",
                                reply_markup=sc_stop_keyboard(),
                            )
                        except Exception:
                            pass
                        continue
                    except Exception as e:
                        log.warning("SC batch send_audio (cache) failed '%s': %s", query, e)
                        await delete_cached_file_id(cache_key)
                        # fall through to normal download

            if direct_url:
                if sc_downloader._is_youtube_url(direct_url):
                    # YT direct URL — independent proxy rotation from SC
                    try:
                        path, meta = await download_yt_with_proxy_rotation(direct_url, user_id, progress_msg.bot)
                    except Exception as e:
                        log.warning("YT batch direct download failed '%s': %s", direct_url, e)
                        yt_error_count += 1
                        not_found.append(f"{artist} — {title}")
                        failed_tracks.append(track)
                        continue
                else:
                    try:
                        path, meta = await download_with_proxy_rotation(direct_url, user_id, progress_msg.bot)
                    except Exception as e:
                        log.warning("SC batch URL download failed '%s': %s", direct_url, e)
                        not_found.append(f"{artist} — {title}")
                        failed_tracks.append(track)
                        continue
            else:
                path, meta = None, {}
                sc_ok = False
                try:
                    sc_results = await search_with_proxy_rotation(query, max_results=1, bot=progress_msg.bot)
                    if sc_results:
                        try:
                            path, meta = await download_with_proxy_rotation(sc_results[0].url, user_id, progress_msg.bot)
                            sc_ok = True
                        except Exception as e:
                            log.warning("SC batch download failed '%s': %s", query, e)
                            sc_error_count += 1
                except Exception as e:
                    log.warning("SC batch search failed '%s': %s", query, e)
                    sc_error_count += 1

                if not sc_ok:
                    try:
                        yt_results = await sc_downloader.search_youtube(query, max_results=1)
                        if not yt_results:
                            not_found.append(f"{artist} — {title}")
                            failed_tracks.append(track)
                            continue
                        path, meta = await sc_downloader.download(yt_results[0].url, user_id)
                    except Exception as e:
                        log.warning("YT batch fallback failed '%s': %s", query, e)
                        not_found.append(f"{artist} — {title}")
                        failed_tracks.append(track)
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
                    await save_cached_file_id(cache_key, sent_msg.audio.file_id, "batch",
                                        artist=artist, title=title)
            except Exception as e:
                log.warning("SC batch send_audio failed '%s': %s", query, e)
                not_found.append(f"{artist} — {title}")
                failed_tracks.append(track)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

            if sent:
                try:
                    await progress_msg.edit_text(
                        f"⏳ {_progress_bar(i, total)} — {artist} — {title}",
                        reply_markup=sc_stop_keyboard(),
                    )
                except Exception:
                    pass

    finally:
        _cancel_events.pop(user_id, None)
        _batch_semaphore.release()

    # Kick off next queued download (if any) now that a slot is free
    asyncio.create_task(_process_queue())

    # Notify admin if SC repeatedly failed during this batch
    if sc_error_count >= 3:
        asyncio.create_task(notify_admin_sc_error(
            progress_msg.bot, user_id, username,
            f"Батч: {sc_error_count} из {total} треков не скачались с SC",
        ))

    # Notify admin if YT direct downloads repeatedly failed
    if yt_error_count >= 3 and settings.ADMIN_ID:
        asyncio.create_task(progress_msg.bot.send_message(
            settings.ADMIN_ID,
            f"⚠️ <b>YouTube: ошибки при батч-скачивании</b>\n\n"
            f"👤 {'@' + username if username else f'#{user_id}'}\n"
            f"❌ {yt_error_count} из {total} треков не скачались с YouTube.\n\n"
            f"Возможно: бан по IP, требование куки или недоступность видео.",
            parse_mode="HTML",
        ))

    batch_result = "stopped" if cancel_event.is_set() else "success"
    await log_event(user_id, username, "sc_batch", batch_result,
              track_count=downloaded_count, detail=f"not_found:{len(not_found)}")

    await update_batch_live(user_id, username, {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": total,
        "current_idx": total,
        "current_track": "—",
        "downloaded": downloaded_count,
        "failed": not_found,
        "status": "stopped" if cancel_event.is_set() else "done",
    })

    for track_name in not_found:
        await log_event(user_id, username, "sc_track_fail", "error", detail=track_name)

    summary = "⛔ Скачивание остановлено." if cancel_event.is_set() else "✅ Плейлист скачан!"
    summary += f"\n\n📊 Скачано: {downloaded_count}/{total}"
    if cache_hits_count:
        summary += f"  ·  ⚡ {cache_hits_count} из кэша"
    if not_found:
        nf_list = "\n".join(not_found[:20])
        summary += f"\n❌ Не найдено нигде ({len(not_found)}):\n{nf_list}"
        if len(not_found) > 20:
            summary += f"\n...и ещё {len(not_found) - 20}"

    try:
        await progress_msg.edit_text(summary)
    except Exception:
        await progress_msg.answer(summary)

    if await state.get_state() is not None:
        if failed_tracks:
            await state.update_data(sc_retry_tracks=failed_tracks)
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            retry_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"🔄 Повторить не найденные ({len(failed_tracks)})",
                    callback_data="sc:retry_failed",
                )],
                [InlineKeyboardButton(text="← В меню", callback_data="sc:cancel")],
            ])
            next_msg = await progress_msg.answer("Что делаем дальше?", reply_markup=retry_kb)
            set_active_msg(progress_msg.chat.id, next_msg.message_id)
            await state.set_state(SCBatchFlow.sc_resume_choice)
        else:
            next_msg = await progress_msg.answer(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
            set_active_msg(progress_msg.chat.id, next_msg.message_id)
            await state.set_state(SCSearchFlow.sc_menu)
