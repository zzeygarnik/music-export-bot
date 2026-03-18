import logging
import aiofiles
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow
from bot.keyboards import (
    service_keyboard,
    retention_keyboard,
    token_guide_keyboard,
    export_type_keyboard,
    playlists_keyboard,
    cancel_keyboard,
)
from core.ym_source import YandexMusicSource
from utils.export import build_txt_file, cleanup
from utils.event_log import log_event

router = Router()
log = logging.getLogger(__name__)

_TOKEN_GUIDE = (
    "🔑 Для доступа к твоей музыке нужна авторизация в Яндексе.\n\n"
    "1. Нажми кнопку <b>«Войти через Яндекс»</b> ниже\n"
    "2. Войди в свой аккаунт Яндекса\n"
    "3. Скопируй значение <code>access_token</code> из адресной строки\n"
    "   (часть URL после <code>#access_token=</code> и до первого <code>&amp;</code>)\n"
    "4. Отправь токен сюда"
)

_EXPORT_MENU_TEXT = "Что экспортируем?"

_RETENTION_TEXT = (
    "💾 <b>Как хранить токен авторизации?</b>\n\n"
    "⚡ <b>На весь сеанс</b>\n"
    "  + Можно экспортировать несколько раз без повторного входа\n"
    "  − Токен остаётся в оперативной памяти бота до перезапуска или /start\n\n"
    "🔒 <b>Только один экспорт</b>\n"
    "  + Токен удаляется сразу после выгрузки файла\n"
    "  − Придётся авторизоваться заново при каждом экспорте\n\n"
    "<i>В обоих случаях токен хранится только в RAM — никакой записи на диск.</i>"
)


def _get_user_info(event: Message | CallbackQuery) -> tuple[int, str | None]:
    user = event.from_user
    return user.id, user.username


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Привет! Я помогу экспортировать треки в текстовый файл.\n\nВыбери сервис:",
        reply_markup=service_keyboard(),
    )
    await state.set_state(ExportFlow.choosing_service)


# ── Service selection ─────────────────────────────────────────────────────────

@router.callback_query(ExportFlow.choosing_service, F.data == "service:yandex")
async def on_service_yandex(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_RETENTION_TEXT, parse_mode="HTML", reply_markup=retention_keyboard())
    await state.update_data(service="yandex")
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
    await _deliver_tracks_msg(status_msg, state, tracks, f"{safe_title}.txt")


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
    await _deliver_tracks(call, state, tracks, f"{safe_title}.txt")


@router.callback_query(ExportFlow.choosing_playlist, F.data == "export:back")
async def on_playlist_back(call: CallbackQuery, state: FSMContext) -> None:
    await call.message.edit_text(_EXPORT_MENU_TEXT, reply_markup=export_type_keyboard())
    await state.set_state(ExportFlow.choosing_export_type)


# ── Fallback handlers ─────────────────────────────────────────────────────────

_STATE_HINTS = {
    None: "Введи /start чтобы начать.",
    ExportFlow.choosing_service: "Нажми на кнопку выше чтобы выбрать сервис.",
    ExportFlow.choosing_retention: "Выбери вариант хранения токена из кнопок выше.",
    ExportFlow.choosing_export_type: "Нажми на кнопку выше чтобы выбрать что экспортировать.",
    ExportFlow.choosing_playlist: "Выбери плейлист из списка выше.",
    ExportFlow.waiting_for_link: "Отправь ссылку на плейлист или нажми «Отмена».",
    ExportFlow.waiting_for_token: "Отправь токен, скопированный из адресной строки браузера.",
}


@router.message()
async def fallback_message(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    hint = _STATE_HINTS.get(current, "Введи /start чтобы начать заново.")
    await message.answer(f"ℹ️ {hint}")


@router.callback_query()
async def fallback_callback(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Эта кнопка устарела. Введи /start чтобы начать заново.", show_alert=True)


# ── Delivery helpers ──────────────────────────────────────────────────────────

async def _deliver_tracks(call: CallbackQuery, state: FSMContext, tracks: list[dict], filename: str) -> None:
    if not tracks:
        await state.update_data(is_exporting=False)
        await call.message.edit_text("😔 Треков не найдено.\n\nВыбери другой вариант:",
                                     reply_markup=export_type_keyboard())
        await state.set_state(ExportFlow.choosing_export_type)
        return

    tmp_path = await build_txt_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await call.message.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Готово! Экспортировано треков: {len(tracks)}.",
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


async def _deliver_tracks_msg(status_msg: Message, state: FSMContext, tracks: list[dict], filename: str) -> None:
    if not tracks:
        await status_msg.edit_text(
            "😔 Треков не найдено.\n\nОтправь другую ссылку или нажми «Отмена».",
            reply_markup=cancel_keyboard(),
        )
        return

    tmp_path = await build_txt_file(tracks, filename)
    try:
        async with aiofiles.open(tmp_path, "rb") as f:
            content = await f.read()
        await status_msg.answer_document(
            document=BufferedInputFile(content, filename=filename),
            caption=f"✅ Готово! Экспортировано треков: {len(tracks)}.",
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
