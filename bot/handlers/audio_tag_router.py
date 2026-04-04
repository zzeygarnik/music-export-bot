"""AudioTagFlow — user sends an audio file, bot re-tags it and returns."""
import html as _html
import logging
import os
import tempfile

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from bot.states import AudioTagFlow
from bot.keyboards import (
    audio_tag_cancel_keyboard,
    audio_tag_back_keyboard,
    service_keyboard,
)
from bot.tracker import set_active_msg
from utils import db

router = Router()
log = logging.getLogger(__name__)

_AUDIO_MIME = {"audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/aac"}


def _apply_tags(path: str, title: str, artist: str) -> None:
    """Write title/artist tags using mutagen (MP3, FLAC, OGG, M4A…)."""
    from mutagen import File  # noqa: PLC0415
    audio = File(path, easy=True)
    if audio is None:
        return
    audio["title"] = title
    audio["artist"] = artist
    audio.save()


def _extract_audio_meta(message: Message) -> tuple[str, str, str, str]:
    """Return (file_id, filename, original_title, original_artist) from a message."""
    audio = message.audio
    doc   = message.document
    if audio:
        return (
            audio.file_id,
            audio.file_name or "track.mp3",
            audio.title or "",
            audio.performer or "",
        )
    return (
        doc.file_id,
        doc.file_name or "track.mp3",
        "",
        "",
    )


# ── Entry via button ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "service:audio_tag")
async def audio_tag_entry(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AudioTagFlow.waiting_for_audio)
    await call.message.edit_text(
        "Трек снова скачался со спамом в названии или неправильными метаданными? "
        "Исправь всё здесь.\n\n"
        "Пришли аудио-файл — прямое прикрепление или пересылка:",
        reply_markup=audio_tag_cancel_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_audio), F.data == "audio_tag:cancel")
async def audio_tag_cancel_waiting(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Отменено.", reply_markup=None)
    sent = await call.message.answer("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, sent.message_id)
    await call.answer()


# ── Audio received while bot asked for it (via button) ────────────────────────

@router.message(
    StateFilter(AudioTagFlow.waiting_for_audio),
    F.audio | (F.document & F.document.mime_type.in_(_AUDIO_MIME)),
)
async def handle_audio_in_flow(message: Message, state: FSMContext) -> None:
    file_id, filename, orig_title, orig_artist = _extract_audio_meta(message)
    await state.set_state(AudioTagFlow.waiting_for_title)
    await state.update_data(
        file_id=file_id,
        original_filename=filename,
        original_title=orig_title,
        original_artist=orig_artist,
    )
    sent = await message.answer(
        "🎵 Введи название трека:",
        reply_markup=audio_tag_cancel_keyboard(),
    )
    set_active_msg(message.chat.id, sent.message_id)


# ── Audio received out of the blue (any other state) ─────────────────────────

@router.message(F.audio | (F.document & F.document.mime_type.in_(_AUDIO_MIME)))
async def handle_audio_received(message: Message, state: FSMContext) -> None:
    """User attached or forwarded audio without going through the menu button."""
    file_id, filename, orig_title, orig_artist = _extract_audio_meta(message)
    await state.set_state(AudioTagFlow.waiting_for_title)
    await state.update_data(
        file_id=file_id,
        original_filename=filename,
        original_title=orig_title,
        original_artist=orig_artist,
    )
    sent = await message.answer(
        "Трек снова скачался со спамом в названии или неправильными метаданными? "
        "Исправь всё здесь - поддерживается прямое прикрепление треков и пересылка.\n\n"
        "🎵 Введи название трека:",
        reply_markup=audio_tag_cancel_keyboard(),
    )
    set_active_msg(message.chat.id, sent.message_id)


# ── Step 2: title ─────────────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_title), F.data == "audio_tag:cancel")
async def audio_tag_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Отменено.", reply_markup=None)
    sent = await call.message.answer("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, sent.message_id)
    await call.answer()


@router.message(StateFilter(AudioTagFlow.waiting_for_title), F.text)
async def audio_tag_got_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    await state.update_data(title=title)
    await state.set_state(AudioTagFlow.waiting_for_artist)
    sent = await message.answer(
        f"Трек: <b>{title}</b>\n\nВведи имя исполнителя:",
        parse_mode="HTML",
        reply_markup=audio_tag_back_keyboard(),
    )
    set_active_msg(message.chat.id, sent.message_id)


# ── Step 3: artist ────────────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_artist), F.data == "audio_tag:back_to_title")
async def audio_tag_back_to_title(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AudioTagFlow.waiting_for_title)
    await call.message.edit_text(
        "🎵 Введи название трека:",
        reply_markup=audio_tag_cancel_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_artist), F.data == "audio_tag:cancel")
async def audio_tag_cancel_from_artist(call: CallbackQuery, state: FSMContext) -> None:
    """Handle stale 'cancel' button from the title step while in artist state."""
    await state.clear()
    await call.message.edit_text("Отменено.", reply_markup=None)
    sent = await call.message.answer("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, sent.message_id)
    await call.answer()


@router.message(StateFilter(AudioTagFlow.waiting_for_artist), F.text)
async def audio_tag_got_artist(message: Message, state: FSMContext) -> None:
    artist = message.text.strip()
    data = await state.get_data()
    title           = data["title"]
    file_id         = data["file_id"]
    original_filename = data.get("original_filename", "track.mp3")
    original_title  = data.get("original_title", "")
    original_artist = data.get("original_artist", "")
    await state.clear()

    progress = await message.answer(
        f"⏳ Теггирую: <b>{_html.escape(artist)} — {_html.escape(title)}</b>…",
        parse_mode="HTML",
    )
    set_active_msg(message.chat.id, progress.message_id)

    suffix = os.path.splitext(original_filename)[1] or ".mp3"
    tmp_in = tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_out = f.name

        await message.bot.download(file_id, destination=tmp_in)

        import shutil
        shutil.copy(tmp_in, tmp_out)
        _apply_tags(tmp_out, title, artist)

        filename = f"{artist} - {title}{suffix}"
        sent = await message.answer_audio(
            audio=FSInputFile(tmp_out, filename=filename),
            title=title,
            performer=artist,
        )
        set_active_msg(message.chat.id, sent.message_id)
        try:
            await progress.delete()
        except Exception:
            pass

        # Log to DB
        await db.log_rename(
            user_id=message.from_user.id,
            username=message.from_user.username,
            original_title=original_title,
            original_artist=original_artist,
            new_title=title,
            new_artist=artist,
        )

    except Exception as e:
        log.exception("AudioTagFlow failed user=%s: %s", message.from_user.id, e)
        try:
            await progress.edit_text("❌ Не удалось обработать файл.", reply_markup=None)
        except Exception:
            pass
        err = await message.answer(
            "❌ Не удалось обработать файл. Попробуй ещё раз или вернись в меню.",
            reply_markup=service_keyboard(),
        )
        set_active_msg(message.chat.id, err.message_id)
    finally:
        for p in (tmp_in, tmp_out):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
