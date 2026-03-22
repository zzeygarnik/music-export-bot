import asyncio
import logging
import os

import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile, FSInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from rapidfuzz import fuzz

from config import settings
from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow
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
)
from core.ym_source import YandexMusicSource
from core import sc_downloader
from core.sc_downloader import SCResult
from utils.export import build_txt_file, cleanup
from utils.event_log import log_event

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
    '<tg-emoji emoji-id="5778672437122045013">☁️</tg-emoji> <b>SoundCloud — скачать MP3</b>\n\n'
    '<tg-emoji emoji-id="6037397706505195857">🔍</tg-emoji> <b>Найти трек</b> — поиск по названию, скачать один трек\n'
    '<tg-emoji emoji-id="6039802767931871481">📥</tg-emoji> <b>Скачать плейлист</b> — загрузить список треков из Яндекс Музыки и скачать все через SoundCloud'
)


def _get_user_info(event: Message | CallbackQuery) -> tuple[int, str | None]:
    user = event.from_user
    return user.id, user.username


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        '👋 Привет! Что хочешь сделать?\n\n'
        '<tg-emoji emoji-id="5870801517140775623">📋</tg-emoji> <b>Экспорт в .txt</b> — сохранить список треков из Яндекс Музыки\n'
        '<tg-emoji emoji-id="6039802767931871481">🎵</tg-emoji> <b>Скачать MP3</b> — найти и скачать трек или плейлист через SoundCloud',
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


@router.callback_query(ExportFlow.choosing_retention, F.data == "retention:back")
async def on_retention_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(
        '👋 Привет! Что хочешь сделать?\n\n'
        '<tg-emoji emoji-id="5870801517140775623">📋</tg-emoji> <b>Экспорт в .txt</b> — сохранить список треков из Яндекс Музыки\n'
        '<tg-emoji emoji-id="6039802767931871481">🎵</tg-emoji> <b>Скачать MP3</b> — найти и скачать трек или плейлист через SoundCloud',
        parse_mode="HTML",
        reply_markup=service_keyboard(),
    )
    await state.set_state(ExportFlow.choosing_service)


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
        reply_markup=token_guide_keyboard(),
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
    status_msg = await message.answer("🔍 Ищу на SoundCloud…")
    # username captured above for log_event in _sc_download_and_send

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

    if not playlists:
        await status_msg.edit_text(
            "😔 Плейлисты не найдены.\n\nНажми /start чтобы начать заново.",
        )
        return

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
        tracks = await YandexMusicSource(token).get_playlist_tracks(playlist_id)
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


# ── Fallback handlers ─────────────────────────────────────────────────────────

_STATE_HINTS = {
    None: "Введи /start чтобы начать.",
    ExportFlow.choosing_service: "Нажми на кнопку выше чтобы выбрать сервис.",
    ExportFlow.choosing_retention: "Выбери вариант хранения токена из кнопок выше.",
    ExportFlow.choosing_export_type: "Нажми на кнопку выше чтобы выбрать что экспортировать.",
    ExportFlow.choosing_playlist: "Выбери плейлист из списка выше.",
    ExportFlow.waiting_for_link: "Отправь ссылку на плейлист или нажми «Отмена».",
    ExportFlow.waiting_for_token: "Отправь токен, скопированный из адресной строки браузера.",
    SCSearchFlow.sc_menu: "Нажми кнопку в меню SoundCloud.",
    SCSearchFlow.sc_search_query: "Введи название трека для поиска на SoundCloud.",
    SCSearchFlow.sc_search_results: "Выбери трек из результатов или нажми «Назад».",
    SCBatchFlow.sc_ym_token: "Отправь токен Яндекс Музыки.",
    SCBatchFlow.sc_ym_playlist: "Выбери плейлист из списка выше.",
    SCBatchFlow.sc_resume_choice: "Выбери, с какого трека начать скачивание.",
    SCBatchFlow.sc_resume_input: "Введи название трека для поиска в плейлисте.",
    SCBatchFlow.sc_resume_confirm: "Подтверди начальный трек кнопкой ниже.",
    SCBatchFlow.sc_downloading: "Скачивание идёт. Нажми «⛔ Остановить» чтобы прервать.",
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
            reply_markup=sc_offer_keyboard() if offer_sc else None,
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
            reply_markup=sc_offer_keyboard() if offer_sc else None,
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
        await msg.answer_audio(
            audio=FSInputFile(path, filename=f"{result.artist} - {result.title}.mp3"),
            title=meta.get("title") or result.title,
            performer=meta.get("artist") or result.artist,
        )
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
        await msg.answer(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
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

    try:
        for i, track in enumerate(tracks[start_idx:], start=start_idx + 1):
            if cancel_event.is_set():
                break

            artist = track.get("artist", "")
            title = track.get("title", "")
            query = f"{artist} {title}"

            try:
                results = await sc_downloader.search(query, max_results=1)
            except Exception as e:
                log.warning("SC batch search failed '%s': %s", query, e)
                not_found.append(f"{artist} — {title}")
                continue

            if not results:
                not_found.append(f"{artist} — {title}")
                continue

            try:
                path, meta = await sc_downloader.download(results[0].url, user_id)
            except Exception as e:
                log.warning("SC batch download failed '%s': %s", query, e)
                not_found.append(f"{artist} — {title}")
                continue

            sent = False
            try:
                await progress_msg.answer_audio(
                    audio=FSInputFile(path, filename=f"{artist} - {title}.mp3"),
                    title=meta.get("title") or title,
                    performer=meta.get("artist") or artist,
                )
                sent = True
                downloaded_count += 1
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

    summary = "⛔ Скачивание остановлено." if cancel_event.is_set() else "✅ Плейлист скачан!"
    if not_found:
        nf_list = "\n".join(not_found[:20])
        summary += f"\n\n❌ Не найдено на SoundCloud ({len(not_found)}):\n{nf_list}"
        if len(not_found) > 20:
            summary += f"\n...и ещё {len(not_found) - 20}"

    try:
        await progress_msg.edit_text(summary)
    except Exception:
        await progress_msg.answer(summary)

    if await state.get_state() is not None:
        await progress_msg.answer(_SC_MENU_TEXT, parse_mode="HTML", reply_markup=sc_menu_keyboard())
        await state.set_state(SCSearchFlow.sc_menu)
