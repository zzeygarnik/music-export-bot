"""Import flow — lets users upload audio files directly to their library via Mini App button."""
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from bot.states import ImportFlow
from bot.handlers.common import log_track_sent

router = Router()
log = logging.getLogger(__name__)

_ALLOWED_AUDIO_MIME = {'audio/mpeg', 'audio/ogg', 'audio/mp3', 'audio/x-mp3', 'audio/m4a', 'audio/aac'}
_ALLOWED_AUDIO_EXT  = {'.mp3', '.ogg', '.m4a', '.flac', '.wav', '.aac', '.opus'}


def _stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Finish import", callback_data="stop_import"),
    ]])


@router.message(F.web_app_data.data == "import")
async def start_import(message: Message, state: FSMContext) -> None:
    """Triggered when user taps the import button in Mini App."""
    await state.set_state(ImportFlow.waiting_for_tracks)
    await state.update_data(import_count=0)
    await message.answer(
        "\U0001f4e5 <b>Import mode activated!</b>\n\n"
        "Send or forward audio files. When done — press the button below.",
        parse_mode="HTML",
        reply_markup=_stop_keyboard(),
    )


@router.message(ImportFlow.waiting_for_tracks, F.audio | F.voice | F.document)
async def handle_import_audio(message: Message, state: FSMContext) -> None:
    """Accept audio/voice/document messages while in import mode."""
    audio = message.audio or message.voice
    doc   = message.document

    if not audio and doc:
        mime = (doc.mime_type or '').lower()
        name = (doc.file_name or '').lower()
        ext  = ('.' + name.rsplit('.', 1)[-1]) if '.' in name else ''
        if mime not in _ALLOWED_AUDIO_MIME and ext not in _ALLOWED_AUDIO_EXT:
            await message.reply("Only audio files are accepted in import mode.")
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

    await log_track_sent(user_id, file_id, artist, title, 'upload', duration, thumb_id)

    data  = await state.get_data()
    count = data.get('import_count', 0) + 1
    await state.update_data(import_count=count)


@router.message(ImportFlow.waiting_for_tracks)
async def import_non_audio(message: Message) -> None:
    """Nudge user when they send something other than audio while in import mode."""
    await message.reply(
        "Send audio files or press ✅ <b>Finish import</b> to complete.",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "stop_import")
async def stop_import(call: CallbackQuery, state: FSMContext) -> None:
    """User pressed Finish import button."""
    current = await state.get_state()
    if current != ImportFlow.waiting_for_tracks:
        await call.answer("Import is not active.", show_alert=False)
        return

    data  = await state.get_data()
    count = data.get('import_count', 0)
    await state.clear()
    await call.answer()
    track_word = "track" if count == 1 else "tracks"
    await call.message.edit_text(
        f"✅ <b>Import complete.</b> Added {count} {track_word}.",
        parse_mode="HTML",
    )
