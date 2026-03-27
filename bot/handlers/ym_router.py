"""ExportFlow handlers: /start, service selection, YM auth, export, filter, CSV."""
import logging

import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow, YMShareFlow
from bot.keyboards import (
    service_keyboard,
    retention_keyboard,
    token_guide_keyboard,
    export_type_keyboard,
    export_type_csv_keyboard,
    playlists_keyboard,
    cancel_keyboard,
    sc_menu_keyboard,
    sc_resume_keyboard,
    sc_offer_extended_keyboard,
    export_filter_cancel_keyboard,
    export_filter_result_keyboard,
    ym_share_cancel_keyboard,
    ym_share_token_keyboard,
)
from core.ym_source import YandexMusicSource
from utils.export import build_txt_file, build_csv_file, cleanup
from utils.event_log import log_event
from config import settings
from .common import (
    _get_user_info,
    _filter_by_artist,
    _EXPORT_MENU_TEXT,
    _RETENTION_TEXT,
    _TOKEN_GUIDE,
    _SC_MENU_TEXT,
    _YMS_INPUT_TEXT,
)

router = Router()
log = logging.getLogger(__name__)


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
    retention = call.data.split(":")[1]
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


# ── ExportFlow: CSV export ────────────────────────────────────────────────────

@router.callback_query(F.data == "export:csv")
async def on_export_csv(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracks = data.get("sc_tracks") or []
    if not tracks:
        await call.answer("Данные плейлиста недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return

    await call.answer()
    tmp_path = await build_csv_file(tracks)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename="tracks.csv"),
            caption=f"📊 CSV-экспорт: {len(tracks)} треков (artist, title, album, year).",
        )
    finally:
        await cleanup(tmp_path)

    if await state.get_state() is not None:
        await call.message.answer(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
        await state.set_state(ExportFlow.choosing_export_type)


# ── ExportFlow: artist filter ─────────────────────────────────────────────────

@router.callback_query(F.data == "export:filter_artist")
async def on_export_filter_artist(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("sc_tracks"):
        await call.answer("Данные плейлиста недоступны. Введи /start чтобы начать заново.", show_alert=True)
        return
    await call.answer()
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
    await state.update_data(export_format="txt")
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


@router.callback_query(F.data == "export:set_fmt_csv")
async def on_export_set_fmt_csv(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(export_format="csv")
    await call.message.edit_text(
        "📊 Формат: <b>CSV</b> (artist, title, album, year).\n\nВыбери источник:",
        parse_mode="HTML",
        reply_markup=export_type_csv_keyboard(),
    )
    await state.set_state(ExportFlow.choosing_export_type)


@router.callback_query(F.data == "export:set_fmt_txt")
async def on_export_set_fmt_txt(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(export_format="txt")
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

    data_pre = await state.get_data()
    fmt = data_pre.get("export_format", "txt")
    await state.update_data(export_format="txt")  # reset after use

    if fmt == "csv":
        out_filename = filename.replace(".txt", ".csv")
        tmp_path = await build_csv_file(tracks, out_filename)
        caption = f"📊 CSV-экспорт: {len(tracks)} треков (artist, title, album, year)."
        reply_markup = None
    else:
        out_filename = filename
        tmp_path = await build_txt_file(tracks, out_filename)
        caption = f"✅ Готово! Экспортировано треков: {len(tracks)}."
        reply_markup = sc_offer_extended_keyboard() if offer_sc else None

    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename=out_filename),
            caption=caption,
            reply_markup=reply_markup,
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

    data_pre = await state.get_data()
    fmt = data_pre.get("export_format", "txt")
    await state.update_data(export_format="txt")  # reset after use

    if fmt == "csv":
        out_filename = filename.replace(".txt", ".csv")
        tmp_path = await build_csv_file(tracks, out_filename)
        caption = f"📊 CSV-экспорт: {len(tracks)} треков (artist, title, album, year)."
        reply_markup = None
    else:
        out_filename = filename
        tmp_path = await build_txt_file(tracks, out_filename)
        caption = f"✅ Готово! Экспортировано треков: {len(tracks)}."
        reply_markup = sc_offer_extended_keyboard() if offer_sc else None

    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await status_msg.answer_document(
            document=BufferedInputFile(content, filename=out_filename),
            caption=caption,
            reply_markup=reply_markup,
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
