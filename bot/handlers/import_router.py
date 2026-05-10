"""Import flow — lets users upload audio files directly to their library via Mini App button."""
import logging
from datetime import datetime, timezone

from aiohttp import web as _web
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from bot.states import ImportFlow
from bot.handlers.common import log_track_sent
from config import settings
from utils import db

router = Router()
log = logging.getLogger(__name__)

# In-memory set of user_ids currently in import mode.
# Checked by audio_tag_router to redirect audio that arrives
# with a mismatched FSM StorageKey (e.g. forwarded album messages).
importing_users: set[int] = set()

_ALLOWED_AUDIO_MIME = {'audio/mpeg', 'audio/ogg', 'audio/mp3', 'audio/x-mp3', 'audio/m4a', 'audio/aac'}
_ALLOWED_AUDIO_EXT  = {'.mp3', '.ogg', '.m4a', '.flac', '.wav', '.aac', '.opus'}


def _stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Завершить импорт", callback_data="stop_import"),
    ]])


async def _save_imported_audio(message, state_storage=None) -> None:
    """Save one audio/voice/document as an import upload.

    Called from handle_import_audio (FSM path) and from audio_tag_router
    when a forwarded album message bypasses the FSM filter (mismatched StorageKey).
    state_storage: FSMContext.storage — used to read activation_msg_id for live counter.
    """
    audio = message.audio or message.voice
    doc   = message.document

    if not audio and doc:
        mime = (doc.mime_type or '').lower()
        name = (doc.file_name or '').lower()
        ext  = ('.' + name.rsplit('.', 1)[-1]) if '.' in name else ''
        if mime not in _ALLOWED_AUDIO_MIME and ext not in _ALLOWED_AUDIO_EXT:
            return
        audio = doc

    if not audio:
        return

    user_id  = message.from_user.id
    file_id  = audio.file_id
    artist   = getattr(audio, 'performer', '') or ''
    title    = getattr(audio, 'title', '') or ''
    duration = getattr(audio, 'duration', None)
    thumb_id = None
    thumb    = getattr(audio, 'thumbnail', None) or getattr(audio, 'thumb', None)
    if thumb:
        thumb_id = thumb.file_id

    # Dedup: two webhook sources (cloud + local bot-api) deliver same update with different update_ids.
    # Redis SETNX is atomic — only the first concurrent delivery wins.
    try:
        import redis.asyncio as _aioredis
        _redis = _aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        _dedup_key = f"import_dedup:{user_id}:{file_id[-20:]}"
        _is_new = await _redis.set(_dedup_key, "1", nx=True, ex=5)
        await _redis.aclose()
        if not _is_new:
            log.info("_save_imported_audio skip dup: user=%s fid=...%s", user_id, file_id[-8:])
            return
    except Exception as _e:
        log.warning("_save_imported_audio redis dedup failed: %s", _e)

    await log_track_sent(user_id, file_id, artist, title, 'upload', duration, thumb_id)
    log.info("_save_imported_audio saved: user=%s file_id=%s title=%s", user_id, file_id[:20], title)

    if state_storage is None:
        return
    try:
        bot_id = int(settings.BOT_TOKEN.split(":")[0])
        key = StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)
        data = await state_storage.get_data(key=key)
        started_at = data.get("import_started_at", "")
        activation_msg_id = data.get("activation_msg_id")
        if started_at and activation_msg_id:
            count = await db.count_uploaded_since(user_id, started_at)
            try:
                await message.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=activation_msg_id,
                    text=f"\U0001f4e5 <b>Режим импорта:</b> получено {count} трек(ов)\n\n"
                         "Когда закончишь — нажми кнопку ниже.",
                    parse_mode="HTML",
                    reply_markup=_stop_keyboard(),
                )
            except Exception:
                pass
    except Exception as e:
        log.warning("_save_imported_audio counter update failed: %s", e)


def _validate_init_data(init_data: str) -> int | None:
    """Validate Telegram WebApp initData HMAC. Returns user_id or None."""
    import hashlib, hmac as _hmac, json as _json
    from urllib.parse import parse_qsl
    try:
        params = dict(parse_qsl(init_data, strict_parsing=True))
        hash_value = params.pop("hash", None)
        if not hash_value:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret = _hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed = _hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(computed, hash_value):
            return None
        user = _json.loads(params.get("user", "{}"))
        uid = user.get("id")
        return int(uid) if uid else None
    except Exception as e:
        log.warning("_validate_init_data error: %s", e)
        return None


async def _api_player_import_start(request: _web.Request) -> _web.Response:
    """POST /api/player/import/start — activate ImportFlow FSM for the Mini App user."""
    init_data = request.headers.get("X-Tg-Init-Data", "")
    user_id = _validate_init_data(init_data)
    if not user_id:
        return _web.Response(status=401, text='{"error":"unauthorized"}',
                             content_type="application/json")
    bot = request.app["bot"]
    storage = request.app.get("storage")
    if storage is None:
        return _web.Response(status=503, text='{"error":"storage unavailable"}',
                             content_type="application/json")
    try:
        bot_id = int(settings.BOT_TOKEN.split(":")[0])
        key = StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)
        started_at = datetime.now(timezone.utc).isoformat()
        importing_users.add(user_id)
        await storage.set_state(key=key, state=ImportFlow.waiting_for_tracks)
        sent = await bot.send_message(
            user_id,
            "\U0001f4e5 <b>Режим импорта активирован!</b>\n\n"
            "Отправляй или пересылай аудиофайлы. Когда закончишь — нажми кнопку ниже.",
            parse_mode="HTML",
            reply_markup=_stop_keyboard(),
        )
        await storage.set_data(key=key, data={
            "import_started_at": started_at,
            "activation_msg_id": sent.message_id,
        })
        return _web.Response(status=200, text='{"ok":true}', content_type="application/json")
    except Exception as e:
        log.warning("_api_player_import_start error: %s", e)
        return _web.Response(status=500, text='{"error":"internal"}',
                             content_type="application/json")


@router.message(F.web_app_data.data == "import")
async def start_import(message: Message, state: FSMContext) -> None:
    """Triggered when user taps the import button in Mini App."""
    started_at = datetime.now(timezone.utc).isoformat()
    importing_users.add(message.from_user.id)
    await state.set_state(ImportFlow.waiting_for_tracks)
    sent = await message.answer(
        "\U0001f4e5 <b>Режим импорта активирован!</b>\n\n"
        "Отправляй или пересылай аудиофайлы. Когда закончишь — нажми кнопку ниже.",
        parse_mode="HTML",
        reply_markup=_stop_keyboard(),
    )
    await state.update_data(import_started_at=started_at, activation_msg_id=sent.message_id)


@router.message(ImportFlow.waiting_for_tracks, F.audio | F.voice | F.document)
async def handle_import_audio(message: Message, state: FSMContext) -> None:
    """Accept audio/voice/document messages while in import mode."""
    log.info("handle_import_audio called: user=%s media_group=%s content_type=%s",
             message.from_user.id, message.media_group_id, message.content_type)
    await _save_imported_audio(message, state.storage)


@router.message(ImportFlow.waiting_for_tracks)
async def import_non_audio(message: Message) -> None:
    """Nudge user when they send something other than audio while in import mode."""
    log.info("import_non_audio: user=%s content_type=%s media_group=%s",
             message.from_user.id, message.content_type, message.media_group_id)
    await message.reply(
        "Отправляй аудиофайлы или нажми ✅ <b>Завершить импорт</b>.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "stop_import")
async def stop_import(call: CallbackQuery, state: FSMContext) -> None:
    """User pressed Finish import button."""
    current = await state.get_state()
    if current != ImportFlow.waiting_for_tracks:
        await call.answer("Импорт не активен.", show_alert=False)
        return

    data = await state.get_data()
    started_at = data.get("import_started_at", "")
    user_id = call.from_user.id
    importing_users.discard(user_id)
    await state.clear()
    count = await db.count_uploaded_since(user_id, started_at) if started_at else 0
    await call.answer()
    await call.message.edit_text(
        f"✅ <b>Импорт завершён.</b> Добавлено треков: {count}.",
        parse_mode="HTML",
    )
