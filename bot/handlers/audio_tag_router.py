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
    audio_tag_cover_keyboard,
    audio_tag_done_keyboard,
    service_keyboard,
)
from bot.tracker import set_active_msg
from utils import db

router = Router()
log = logging.getLogger(__name__)

_AUDIO_MIME = {"audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/aac"}


def _apply_tags(path: str, title: str, artist: str) -> bool:
    """Write title/artist tags using mutagen. Returns True on success."""
    try:
        from mutagen import File  # noqa: PLC0415
        audio = File(path, easy=True)
        if audio is None:
            return False
        audio["title"] = title
        audio["artist"] = artist
        audio.save()
        return True
    except Exception as exc:
        log.warning("mutagen could not tag %s: %s", path, exc)
        return False


def _apply_cover(path: str, cover_bytes: bytes) -> bool:
    """Embed cover art into audio file. Supports MP3 and FLAC."""
    try:
        suffix = os.path.splitext(path)[1].lower()
        if suffix == ".mp3":
            from mutagen.id3 import ID3, APIC, ID3NoHeaderError  # noqa: PLC0415
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags["APIC"] = APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=cover_bytes,
            )
            tags.save(path, v2_version=3)
            return True
        if suffix in (".flac", ".ogg"):
            import base64  # noqa: PLC0415
            from mutagen.flac import FLAC, Picture  # noqa: PLC0415
            audio = FLAC(path)
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.data = cover_bytes
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()
            return True
        log.info("Cover embedding not supported for %s", suffix)
        return False
    except Exception as exc:
        log.warning("mutagen could not embed cover in %s: %s", path, exc)
        return False


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


async def _process_and_send(message: Message, state: FSMContext, cover_bytes: bytes | None) -> None:
    """Download, retag (+ optional cover), and send the audio file."""
    data = await state.get_data()
    title             = data["title"]
    artist            = data["artist"]
    file_id           = data["file_id"]
    original_filename = data.get("original_filename", "track.mp3")
    original_title    = data.get("original_title", "")
    original_artist   = data.get("original_artist", "")
    await state.clear()

    progress = await message.answer(
        f"⏳ Теггирую: <b>{_html.escape(artist)} — {_html.escape(title)}</b>…",
        parse_mode="HTML",
    )
    set_active_msg(message.chat.id, progress.message_id)

    suffix = os.path.splitext(original_filename)[1] or ".mp3"
    filename = f"{artist} - {title}{suffix}"
    tmp_in = tmp_out = None
    sent = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_out = f.name

        await message.bot.download(file_id, destination=tmp_in)

        import shutil
        shutil.copy(tmp_in, tmp_out)
        _apply_tags(tmp_out, title, artist)
        if cover_bytes:
            _apply_cover(tmp_out, cover_bytes)

        sent = await message.answer_audio(
            audio=FSInputFile(tmp_out, filename=filename),
            title=title,
            performer=artist,
        )

    except Exception as e:
        log.exception("AudioTagFlow download/upload failed user=%s: %s", message.from_user.id, e)
        try:
            sent = await message.answer_audio(
                audio=file_id,
                title=title,
                performer=artist,
            )
            log.info("AudioTagFlow fallback (file_id resend) succeeded user=%s", message.from_user.id)
        except Exception as e2:
            log.exception("AudioTagFlow fallback also failed user=%s: %s", message.from_user.id, e2)

    finally:
        for p in (tmp_in, tmp_out):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass

    if sent:
        set_active_msg(message.chat.id, sent.message_id)
        try:
            await progress.delete()
        except Exception:
            pass
        done = await message.answer("Готово! Что дальше?", reply_markup=audio_tag_done_keyboard())
        set_active_msg(message.chat.id, done.message_id)
        await db.log_rename(
            user_id=message.from_user.id,
            username=message.from_user.username,
            original_title=original_title,
            original_artist=original_artist,
            new_title=title,
            new_artist=artist,
        )
    else:
        err_text = "❌ Не удалось обработать файл. Попробуй ещё раз или вернись в меню."
        edited = False
        try:
            await progress.edit_text(err_text, reply_markup=service_keyboard())
            edited = True
        except Exception:
            pass
        if not edited:
            err = await message.answer(err_text, reply_markup=service_keyboard())
            set_active_msg(message.chat.id, err.message_id)


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
    title = data["title"]
    await state.update_data(artist=artist)
    await state.set_state(AudioTagFlow.waiting_for_cover)
    sent = await message.answer(
        f"Трек: <b>{_html.escape(artist)} — {_html.escape(title)}</b>\n\n"
        "🖼 Пришли обложку (фото), или нажми «Пропустить»:",
        parse_mode="HTML",
        reply_markup=audio_tag_cover_keyboard(),
    )
    set_active_msg(message.chat.id, sent.message_id)


# ── Step 4: cover ─────────────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_cover), F.data == "audio_tag:back_to_artist")
async def audio_tag_back_to_artist(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("title", "")
    await state.set_state(AudioTagFlow.waiting_for_artist)
    await call.message.edit_text(
        f"Трек: <b>{_html.escape(title)}</b>\n\nВведи имя исполнителя:",
        parse_mode="HTML",
        reply_markup=audio_tag_back_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_cover), F.data == "audio_tag:skip_cover")
async def audio_tag_skip_cover(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _process_and_send(call.message, state, cover_bytes=None)


@router.message(StateFilter(AudioTagFlow.waiting_for_cover), F.photo)
async def audio_tag_got_cover(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]  # highest resolution
    cover_bytes_io = await message.bot.download(photo.file_id)
    cover_bytes = cover_bytes_io.read()
    await _process_and_send(message, state, cover_bytes=cover_bytes)


# ── Post-send actions ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "audio_tag:tag_another")
async def audio_tag_another(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AudioTagFlow.waiting_for_audio)
    await call.message.edit_text(
        "Пришли следующий аудио-файл — прямое прикрепление или пересылка:",
        reply_markup=audio_tag_cancel_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(F.data == "audio_tag:to_menu")
async def audio_tag_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()
