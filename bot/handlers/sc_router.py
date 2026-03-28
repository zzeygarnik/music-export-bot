"""SCSearchFlow + SCBatchFlow handlers, plus SC delivery helpers."""
import asyncio
import logging
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow
from bot.keyboards import (
    service_keyboard,
    sc_menu_keyboard,
    sc_cancel_keyboard,
    sc_results_keyboard,
    sc_playlists_keyboard,
    sc_resume_keyboard,
    sc_resume_confirm_keyboard,
    sc_stop_keyboard,
    sc_offer_keyboard,
    sc_after_download_keyboard,
    sc_batch_token_keyboard,
    cache_results_keyboard,
)
from core.ym_source import YandexMusicSource
from core import sc_downloader
from core.sc_downloader import SCResult
from utils.event_log import log_event, update_batch_live
from utils.db import get_cached_file_id, save_cached_file_id, delete_cached_file_id, search_cache_fuzzy, is_batch_allowed
from config import settings
from rapidfuzz import fuzz
from .common import (
    _get_user_info,
    _make_cache_key,
    _progress_bar,
    _cancel_events,
    _batch_semaphore,
    _TOKEN_GUIDE,
    _SC_MENU_TEXT,
    _SC_URL_TEXT,
    _show_batch_access_page,
)

router = Router()
log = logging.getLogger(__name__)


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
    if not is_batch_allowed(call.from_user.id, call.from_user.username):
        await _show_batch_access_page(call, back_cb="batch_req_back:sc_menu")
        return
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
    await call.message.edit_text("👋 Привет! Что хочешь сделать?", reply_markup=service_keyboard())
    await state.set_state(ExportFlow.choosing_service)


@router.callback_query(F.data == "batch_req_back:sc_menu")
async def on_batch_req_back_sc_menu(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


# ── SC: Inline offer after YM .txt export ─────────────────────────────────────

@router.callback_query(F.data == "sc:batch_from_ym")
async def on_sc_batch_from_ym(call: CallbackQuery, state: FSMContext) -> None:
    if not is_batch_allowed(call.from_user.id, call.from_user.username):
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

    status_msg = await message.answer("🔍 Ищу в базе…")
    cache_hits = search_cache_fuzzy(query)
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

    best_idx, best_score = 0, 0
    for i, r in enumerate(results):
        score = fuzz.token_sort_ratio(query.lower(), f"{r.artist} {r.title}".lower())
        if score > best_score:
            best_score = score
            best_idx = i

    if best_score >= 80:
        best = results[best_idx]
        pre_cached = get_cached_file_id(_make_cache_key(best.artist, best.title))
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
    cache_hits = search_cache_fuzzy(query)
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
        pre_cached = get_cached_file_id(_make_cache_key(best.artist, best.title))
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
            pre_cached = get_cached_file_id(_make_cache_key(best.artist, best.title))
            await status_msg.edit_text(
                f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
                parse_mode="HTML")
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
            pre_cached = get_cached_file_id(_make_cache_key(best.artist, best.title))
            await status_msg.edit_text(
                f"{'⚡ Нашёл в базе' if pre_cached else '⏳ Скачиваю'}: <b>{best.artist} — {best.title}</b>…",
                parse_mode="HTML")
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


@router.callback_query(F.data == "sc:retry_failed")
async def on_sc_retry_failed(call: CallbackQuery, state: FSMContext) -> None:
    user_id = call.from_user.id
    if user_id in _cancel_events:
        await call.answer("⚠️ У тебя уже идёт скачивание.", show_alert=True)
        return
    if _batch_semaphore.locked():
        await call.answer(
            f"⏳ Бот сейчас занят. Попробуй чуть позже.",
            show_alert=True,
        )
        return
    data = await state.get_data()
    retry_tracks = data.get("sc_retry_tracks", [])
    if not retry_tracks:
        await call.answer("Нет треков для повтора.", show_alert=True)
        return
    await state.update_data(sc_tracks=retry_tracks, sc_retry_tracks=[])
    await call.message.edit_text(
        f"🔄 Повторяю скачивание <b>{len(retry_tracks)}</b> не найденных треков.",
        parse_mode="HTML",
        reply_markup=sc_stop_keyboard(),
    )
    await state.set_state(SCBatchFlow.sc_downloading)
    asyncio.create_task(
        _run_batch_download(call.message, state, user_id, call.from_user.username, retry_tracks, 0)
    )


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
    cached_file_id = get_cached_file_id(cache_key) if cache_key else None

    if cached_file_id:
        try:
            await msg.answer_audio(
                audio=cached_file_id,
                title=result.title,
                performer=result.artist,
            )
            log_event(user_id, username, f"{source}_search", "success",
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
            if cache_key:
                delete_cached_file_id(cache_key)

    try:
        path, meta = await sc_downloader.download(result.url, user_id)
    except Exception as e:
        log.warning("SC download failed user=%s url=%s: %s", user_id, result.url, e)
        log_event(user_id, username, f"{source}_search", "error", detail="download_failed")
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
            save_cached_file_id(cache_key, sent_msg.audio.file_id, "manual",
                                artist=result.artist, title=result.title)
        log_event(user_id, username, f"{source}_search", "success",
                  track_count=1, detail=f"{result.artist} — {result.title}")
        try:
            await msg.delete()
        except Exception:
            pass
    except Exception as e:
        log.exception("SC send_audio failed user=%s: %s", user_id, e)
        log_event(user_id, username, f"{source}_search", "error", detail="send_failed")
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
    failed_tracks: list[dict] = []
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
            cache_key = _make_cache_key(artist, title) if (artist or title) else None

            # ── Cache lookup ───────────────────────────────────────────────────
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
                                f"⚡ {_progress_bar(i, total)} — {artist} — {title}",
                                reply_markup=sc_stop_keyboard(),
                            )
                        except Exception:
                            pass
                        continue
                    except Exception as e:
                        log.warning("SC batch send_audio (cache) failed '%s': %s", query, e)
                        delete_cached_file_id(cache_key)
                        # fall through to normal download

            if direct_url:
                try:
                    path, meta = await sc_downloader.download(direct_url, user_id)
                except Exception as e:
                    log.warning("SC batch URL download failed '%s': %s", direct_url, e)
                    not_found.append(f"{artist} — {title}")
                    failed_tracks.append(track)
                    continue
            else:
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
                    save_cached_file_id(cache_key, sent_msg.audio.file_id, "batch",
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
            await progress_msg.answer("Что делаем дальше?", reply_markup=retry_kb)
            await state.set_state(SCBatchFlow.sc_resume_choice)
        else:
            await progress_msg.answer(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
