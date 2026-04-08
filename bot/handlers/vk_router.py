"""VK Music search and download handlers."""
import asyncio
import logging
import os

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from rapidfuzz import fuzz

from bot.states import VKSearchFlow, SCSearchFlow
from bot.keyboards import sc_menu_keyboard, vk_results_keyboard
from core import vk_source
from utils.db import get_cached_file_id, save_cached_file_id
from utils.event_log import log_event
from .common import (
    _SC_MENU_TEXT,
    _make_cache_key,
    _get_user_info,
)
from bot.tracker import set_active_msg
from config import settings

router = Router()
log = logging.getLogger(__name__)

_VK_SEARCH_TEXT = (
    "🎵 <b>VK Музыка</b>\n\n"
    "Введи запрос для поиска:"
)


# ── Entry point from sc_menu ──────────────────────────────────────────────────

@router.callback_query(SCSearchFlow.sc_menu, F.data == "sc:vk_search")
async def on_vk_search_entry(call: CallbackQuery, state: FSMContext) -> None:
    is_admin = settings.ADMIN_ID != 0 and call.from_user.id == settings.ADMIN_ID
    if not is_admin:
        await call.answer("🚧 Раздел VK Музыки находится в разработке.", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(_VK_SEARCH_TEXT, parse_mode="HTML")
    await state.set_state(VKSearchFlow.vk_search_query)


# ── Query input ───────────────────────────────────────────────────────────────

@router.message(VKSearchFlow.vk_search_query)
async def on_vk_search_query(message: Message, state: FSMContext) -> None:
    user_id, username = _get_user_info(message)
    query = message.text.strip() if message.text else ""
    if not query:
        await message.answer("Введи текстовый запрос.")
        return

    status = await message.answer("🔍 Ищу на VK…")
    set_active_msg(user_id, status.message_id)

    results = await vk_source.search(query, count=5)
    if not results:
        await status.edit_text(
            "😔 Ничего не найдено на VK.\n\nПопробуй другой запрос.",
            reply_markup=sc_menu_keyboard(),
        )
        await state.set_state(SCSearchFlow.sc_menu)
        return

    # Auto-pick if top result is a strong match
    best = results[0]
    best_score = max(
        fuzz.token_sort_ratio(query.lower(), f"{best.artist} {best.title}".lower()),
        fuzz.token_set_ratio(query.lower(), f"{best.artist} {best.title}".lower()),
    )

    if best_score >= 80:
        await status.edit_text(
            f"⏳ Скачиваю: <b>{best.artist} — {best.title}</b>…",
            parse_mode="HTML",
        )
        await _vk_download_and_send(status, state, best, user_id, username)
    else:
        # Show top-5 for user to pick
        await state.update_data(vk_results=[
            {"track_id": r.track_id, "artist": r.artist,
             "title": r.title, "duration": r.duration, "url": r.url}
            for r in results
        ])
        lines = [f"🎵 Результаты по «{query}»:\n"]
        for i, r in enumerate(results, 1):
            dur = f"{r.duration // 60}:{r.duration % 60:02d}" if r.duration else "?"
            lines.append(f"{i}. <b>{r.artist}</b> — {r.title} <i>({dur})</i>")
        await status.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=vk_results_keyboard(results),
        )
        await state.set_state(VKSearchFlow.vk_search_results)


# ── User picks from top-5 ─────────────────────────────────────────────────────

@router.callback_query(VKSearchFlow.vk_search_results, F.data.startswith("vk_pick:"))
async def on_vk_pick(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user_id, username = _get_user_info(call)
    idx = int(call.data.split(":")[1])
    data = await state.get_data()
    results_raw = data.get("vk_results", [])
    if idx >= len(results_raw):
        await call.answer("Устаревший список — попробуй снова.", show_alert=True)
        return

    r = results_raw[idx]
    track = vk_source.VKTrack(
        track_id=r["track_id"], artist=r["artist"], title=r["title"],
        duration=r["duration"], url=r["url"],
    )
    await call.message.edit_text(
        f"⏳ Скачиваю: <b>{track.artist} — {track.title}</b>…",
        parse_mode="HTML",
    )
    await _vk_download_and_send(call.message, state, track, user_id, username)


@router.callback_query(VKSearchFlow.vk_search_results, F.data == "vk:cancel")
async def on_vk_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
    await state.set_state(SCSearchFlow.sc_menu)


# ── Download + send helper ────────────────────────────────────────────────────

async def _vk_download_and_send(
    msg: Message,
    state: FSMContext,
    track: vk_source.VKTrack,
    user_id: int,
    username: str | None,
) -> None:
    cache_key = _make_cache_key(track.artist, track.title) if (track.artist or track.title) else None

    # Cache check
    if cache_key:
        cached_fid = await get_cached_file_id(cache_key)
        if cached_fid:
            try:
                await msg.answer_audio(
                    audio=cached_fid,
                    title=track.title,
                    performer=track.artist,
                )
                await msg.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
                await state.set_state(SCSearchFlow.sc_menu)
                await log_event(user_id, username, "vk_download", "success",
                                detail=f"{track.artist} — {track.title} [cache]")
                return
            except Exception:
                pass  # stale file_id — fall through to re-download

    # Download
    path = None
    try:
        path, meta = await vk_source.download_track(track, user_id)
        artist = meta.get("artist") or track.artist
        title  = meta.get("title")  or track.title

        sent = await msg.answer_audio(
            audio=FSInputFile(path),
            title=title,
            performer=artist,
            duration=track.duration or None,
        )
        if cache_key and sent.audio:
            await save_cached_file_id(cache_key, sent.audio.file_id, "vk", artist, title)

        await msg.edit_text(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
        await state.set_state(SCSearchFlow.sc_menu)
        await log_event(user_id, username, "vk_download", "success",
                        detail=f"{artist} — {title}")

    except Exception as e:
        log.warning("VK download failed '%s — %s': %s", track.artist, track.title, e)
        await msg.edit_text(
            f"❌ Не удалось скачать <b>{track.artist} — {track.title}</b>.\n\n"
            f"VK мог сменить ссылку — попробуй снова.",
            parse_mode="HTML",
            reply_markup=sc_menu_keyboard(),
        )
        await state.set_state(SCSearchFlow.sc_menu)
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
