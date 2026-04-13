"""AudioTagFlow — user sends an audio file, bot re-tags it and returns."""
import html as _html
import logging
import os
import tempfile

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from bot.states import AudioTagFlow
from bot.keyboards import (
    audio_tag_cancel_keyboard,
    audio_tag_field_keyboard,
    audio_tag_back_to_selection_keyboard,
    audio_tag_done_keyboard,
    service_keyboard,
)
from bot.tracker import set_active_msg
from utils import db
from core.sc_downloader import resize_for_telegram_sync

router = Router()
log = logging.getLogger(__name__)

_AUDIO_MIME = {"audio/mpeg", "audio/flac", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/aac"}


def _apply_metadata(path: str, title: str, artist: str, cover_bytes: bytes | None = None) -> bool:
    """Detect actual audio format and write title/artist/cover. Returns True on success."""
    import subprocess  # noqa: PLC0415
    import shutil as _shutil  # noqa: PLC0415
    from mutagen import File as MutagenFile  # noqa: PLC0415

    try:
        probe = MutagenFile(path)
    except Exception as exc:
        log.warning("mutagen probe failed %s: %s", path, exc)
        probe = None

    # ── M4A / AAC / MP4 ──────────────────────────────────────────────────────
    try:
        from mutagen.mp4 import MP4  # noqa: PLC0415
        if isinstance(probe, MP4):
            from mutagen.mp4 import MP4Cover  # noqa: PLC0415
            audio = MP4(path)
            audio["\xa9nam"] = [title]
            audio["\xa9ART"] = [artist]
            if cover_bytes:
                audio["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
            return True
    except Exception as exc:
        log.warning("MP4 tag write failed %s: %s", path, exc)
        return False

    # ── FLAC / OGG ───────────────────────────────────────────────────────────
    try:
        from mutagen.flac import FLAC, Picture  # noqa: PLC0415
        if isinstance(probe, FLAC):
            audio = FLAC(path)
            audio["title"] = [title]
            audio["artist"] = [artist]
            if cover_bytes:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.data = cover_bytes
                audio.clear_pictures()
                audio.add_picture(pic)
            audio.save()
            return True
    except Exception as exc:
        log.warning("FLAC tag write failed %s: %s", path, exc)
        return False

    # ── MP3 (via ffmpeg to avoid in-place APIC corruption) ───────────────────
    cover_tmp = out_tmp = None
    try:
        out_tmp = path + ".out.mp3"
        cmd = ["ffmpeg", "-y", "-i", path]
        if cover_bytes:
            cover_tmp = path + ".cover.jpg"
            with open(cover_tmp, "wb") as f:
                f.write(cover_bytes)
            cmd += ["-i", cover_tmp,
                    "-map", "0:a", "-map", "1:v",
                    "-c:a", "copy", "-c:v", "copy",
                    "-disposition:v:0", "attached_pic",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)"]
        else:
            cmd += ["-map", "0:a", "-c:a", "copy"]
        cmd += ["-write_xing", "1", "-id3v2_version", "3",
                "-metadata", f"title={title}",
                "-metadata", f"artist={artist}",
                out_tmp]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            log.warning("ffmpeg failed: %s", result.stderr.decode(errors="replace")[-500:])
            return False
        _shutil.move(out_tmp, path)
        out_tmp = None
        return True
    except Exception as exc:
        log.warning("ffmpeg metadata write failed %s: %s", path, exc)
        return False
    finally:
        for p in (cover_tmp, out_tmp):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


def _extract_audio_meta(message: Message) -> tuple[str, str, str, str, int | None]:
    """Return (file_id, filename, original_title, original_artist, duration) from a message."""
    audio = message.audio
    doc   = message.document
    if audio:
        return (
            audio.file_id,
            audio.file_name or "track.mp3",
            audio.title or "",
            audio.performer or "",
            audio.duration,
        )
    return (
        doc.file_id,
        doc.file_name or "track.mp3",
        "",
        "",
        None,
    )


def _extract_cover_sync(path: str) -> bytes | None:
    """Extract embedded cover art from audio file. Returns raw bytes or None."""
    try:
        from mutagen import File as MutagenFile  # noqa: PLC0415
        audio = MutagenFile(path)
        if audio is None:
            return None
        # MP3 — ID3 APIC frame
        try:
            from mutagen.id3 import ID3  # noqa: PLC0415
            tags = ID3(path)
            frames = tags.getall("APIC")
            if frames:
                return frames[0].data
        except Exception:
            pass
        # MP4 / M4A
        try:
            from mutagen.mp4 import MP4  # noqa: PLC0415
            if isinstance(audio, MP4):
                covers = audio.get("covr")
                if covers:
                    return bytes(covers[0])
        except Exception:
            pass
        # FLAC
        try:
            from mutagen.flac import FLAC  # noqa: PLC0415
            if isinstance(audio, FLAC) and audio.pictures:
                return audio.pictures[0].data
        except Exception:
            pass
    except Exception:
        pass
    return None


async def _process_and_send(message: Message, state: FSMContext, cover_bytes: bytes | None) -> None:
    """Download, retag (+ optional cover), and send the audio file."""
    data = await state.get_data()
    title             = data["title"]
    artist            = data["artist"]
    file_id           = data["file_id"]
    original_filename = data.get("original_filename", "track.mp3")
    original_title    = data.get("original_title", "")
    original_artist   = data.get("original_artist", "")
    duration          = data.get("duration")
    await state.clear()

    progress = await message.answer(
        f"⏳ Теггирую: <b>{_html.escape(artist)} — {_html.escape(title)}</b>…",
        parse_mode="HTML",
    )
    set_active_msg(message.chat.id, progress.message_id)

    suffix = os.path.splitext(original_filename)[1] or ".mp3"
    filename = f"{artist} - {title}{suffix}"

    import asyncio
    thumb_input = None

    tmp_in = tmp_out = None
    sent = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_in = f.name
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            tmp_out = f.name

        await message.bot.download(file_id, destination=tmp_in)

        # Fallback: read duration from file if Telegram didn't provide it (e.g. document)
        if not duration:
            try:
                from mutagen import File as MutagenFile  # noqa: PLC0415
                _mf = await asyncio.to_thread(MutagenFile, tmp_in)
                if _mf and hasattr(_mf, "info") and _mf.info:
                    duration = int(_mf.info.length)
            except Exception:
                pass

        # If user didn't provide a new cover, extract the original one from the file
        if cover_bytes is None:
            cover_bytes = await asyncio.to_thread(_extract_cover_sync, tmp_in)

        if cover_bytes:
            try:
                thumb_bytes = await asyncio.to_thread(resize_for_telegram_sync, cover_bytes)
                thumb_input = BufferedInputFile(thumb_bytes, filename="cover.jpg")
            except Exception:
                pass

        import shutil
        shutil.copy(tmp_in, tmp_out)
        if not _apply_metadata(tmp_out, title, artist, cover_bytes):
            log.warning("_apply_metadata failed user=%s file=%s", message.from_user.id, original_filename)

        sent = await message.answer_audio(
            audio=FSInputFile(tmp_out, filename=filename),
            title=title,
            performer=artist,
            duration=duration,
            thumbnail=thumb_input,
        )

    except Exception as e:
        log.exception("AudioTagFlow download/upload failed user=%s: %s", message.from_user.id, e)
        try:
            sent = await message.answer_audio(
                audio=file_id,
                title=title,
                performer=artist,
                duration=duration,
                thumbnail=thumb_input,
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _field_selection_text(title: str, artist: str, has_cover: bool) -> str:
    cover_line = "🖼 Обложка: <i>будет заменена</i>" if has_cover else "🖼 Обложка: <i>без изменений</i>"
    return (
        f"🎵 <b>{_html.escape(artist or '—')} — {_html.escape(title or '—')}</b>\n"
        f"{cover_line}\n\n"
        "Что хочешь изменить?"
    )


async def _show_field_selection_edit(call: CallbackQuery, state: FSMContext) -> None:
    """Edit the existing bot message to show field selection."""
    data = await state.get_data()
    await state.set_state(AudioTagFlow.waiting_for_field_selection)
    text = _field_selection_text(
        data.get("title", ""),
        data.get("artist", ""),
        bool(data.get("cover_b64")),
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=audio_tag_field_keyboard())
    set_active_msg(call.message.chat.id, call.message.message_id)


async def _show_field_selection_answer(message: Message, state: FSMContext) -> None:
    """Send a new message with field selection (used after user sent a text/photo)."""
    data = await state.get_data()
    await state.set_state(AudioTagFlow.waiting_for_field_selection)
    text = _field_selection_text(
        data.get("title", ""),
        data.get("artist", ""),
        bool(data.get("cover_b64")),
    )
    sent = await message.answer(text, parse_mode="HTML", reply_markup=audio_tag_field_keyboard())
    set_active_msg(message.chat.id, sent.message_id)


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
    await call.message.edit_text("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


# ── Audio received while bot asked for it (via button) ────────────────────────

@router.message(
    StateFilter(AudioTagFlow.waiting_for_audio),
    F.audio | (F.document & F.document.mime_type.in_(_AUDIO_MIME)),
)
async def handle_audio_in_flow(message: Message, state: FSMContext) -> None:
    file_id, filename, orig_title, orig_artist, duration = _extract_audio_meta(message)
    await state.update_data(
        file_id=file_id,
        original_filename=filename,
        original_title=orig_title,
        original_artist=orig_artist,
        title=orig_title,
        artist=orig_artist,
        cover_b64=None,
        duration=duration,
    )
    await _show_field_selection_answer(message, state)


# ── Audio received out of the blue (any other state) ─────────────────────────

@router.message(F.audio | (F.document & F.document.mime_type.in_(_AUDIO_MIME)))
async def handle_audio_received(message: Message, state: FSMContext) -> None:
    """User attached or forwarded audio without going through the menu button."""
    file_id, filename, orig_title, orig_artist, duration = _extract_audio_meta(message)
    await state.update_data(
        file_id=file_id,
        original_filename=filename,
        original_title=orig_title,
        original_artist=orig_artist,
        title=orig_title,
        artist=orig_artist,
        cover_b64=None,
        duration=duration,
    )
    await _show_field_selection_answer(message, state)


# ── Field selection screen ────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_field_selection), F.data == "audio_tag:cancel")
async def audio_tag_cancel_selection(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Выбери действие:", reply_markup=service_keyboard())
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_field_selection), F.data == "audio_tag:edit_title")
async def audio_tag_start_edit_title(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = _html.escape(data.get("title") or "")
    await state.set_state(AudioTagFlow.waiting_for_title)
    await call.message.edit_text(
        f"Текущее название: <b>{current or '—'}</b>\n\n🎵 Введи новое название трека:",
        parse_mode="HTML",
        reply_markup=audio_tag_back_to_selection_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_field_selection), F.data == "audio_tag:edit_artist")
async def audio_tag_start_edit_artist(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    current = _html.escape(data.get("artist") or "")
    await state.set_state(AudioTagFlow.waiting_for_artist)
    await call.message.edit_text(
        f"Текущий исполнитель: <b>{current or '—'}</b>\n\n🎤 Введи нового исполнителя:",
        parse_mode="HTML",
        reply_markup=audio_tag_back_to_selection_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_field_selection), F.data == "audio_tag:edit_cover")
async def audio_tag_start_edit_cover(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    has_cover = bool(data.get("cover_b64"))
    status = "уже установлена" if has_cover else "не задана"
    await state.set_state(AudioTagFlow.waiting_for_cover)
    await call.message.edit_text(
        f"Обложка: <i>{status}</i>\n\n🖼 Пришли новое фото для обложки:",
        parse_mode="HTML",
        reply_markup=audio_tag_back_to_selection_keyboard(),
    )
    set_active_msg(call.message.chat.id, call.message.message_id)
    await call.answer()


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_field_selection), F.data == "audio_tag:apply")
async def audio_tag_apply(call: CallbackQuery, state: FSMContext) -> None:
    import base64
    data = await state.get_data()
    cover_b64 = data.get("cover_b64")
    cover_bytes = base64.b64decode(cover_b64) if cover_b64 else None
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await _process_and_send(call.message, state, cover_bytes=cover_bytes)


# ── Step: title input ────────────────────────────────────────────────────────

_INVISIBLE_RE = __import__("re").compile(r"[\u200b\u200c\u200d\u200e\u200f\u00ad\ufeff]")


@router.callback_query(StateFilter(AudioTagFlow.waiting_for_title), F.data == "audio_tag:back_to_selection")
async def audio_tag_back_from_title(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_field_selection_edit(call, state)


@router.message(StateFilter(AudioTagFlow.waiting_for_title), F.text)
async def audio_tag_got_title(message: Message, state: FSMContext) -> None:
    title = _INVISIBLE_RE.sub("", message.text.strip())
    await state.update_data(title=title)
    await _show_field_selection_answer(message, state)


# ── Step: artist input ───────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_artist), F.data == "audio_tag:back_to_selection")
async def audio_tag_back_from_artist(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_field_selection_edit(call, state)


@router.message(StateFilter(AudioTagFlow.waiting_for_artist), F.text)
async def audio_tag_got_artist(message: Message, state: FSMContext) -> None:
    artist = _INVISIBLE_RE.sub("", message.text.strip())
    await state.update_data(artist=artist)
    await _show_field_selection_answer(message, state)


# ── Step: cover input ────────────────────────────────────────────────────────

@router.callback_query(StateFilter(AudioTagFlow.waiting_for_cover), F.data == "audio_tag:back_to_selection")
async def audio_tag_back_from_cover(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_field_selection_edit(call, state)


@router.message(StateFilter(AudioTagFlow.waiting_for_cover), F.photo)
async def audio_tag_got_cover(message: Message, state: FSMContext) -> None:
    import base64
    photo = message.photo[-1]  # highest resolution
    cover_bytes_io = await message.bot.download(photo.file_id)
    cover_bytes = cover_bytes_io.read()
    await state.update_data(cover_b64=base64.b64encode(cover_bytes).decode())
    await _show_field_selection_answer(message, state)


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
