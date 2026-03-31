import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

from config import settings

log = logging.getLogger(__name__)


class SCBanError(Exception):
    """Raised when the error indicates an IP ban or rate-limit (not a per-track access error)."""


# Active proxy for SC downloads; empty string = use server's main IP.
# Managed externally by bot/handlers/common.py proxy rotation logic.
_active_proxy: str = ""


def set_active_proxy(proxy: str) -> None:
    global _active_proxy
    _active_proxy = proxy


def get_active_proxy() -> str:
    return _active_proxy


def _proxy_opts() -> dict:
    """Return yt-dlp proxy option based on the currently active SC proxy."""
    proxy = _active_proxy
    return {"proxy": proxy} if proxy else {}


def _is_ban_error(msg: str) -> bool:
    """Return True if the yt-dlp error looks like an IP ban or rate-limit."""
    msg_low = msg.lower()
    # 429 = rate limiting, almost certainly IP-based
    if "http error 429" in msg_low or "too many requests" in msg_low:
        return True
    # 403 on the *webpage* (not on the track audio itself) = likely IP ban
    if "unable to download webpage" in msg_low and "403" in msg_low:
        return True
    return False


def _cookie_opts() -> dict:
    """Return yt-dlp cookiefile option if SC_COOKIE_FILE is configured."""
    return {"cookiefile": settings.SC_COOKIE_FILE} if settings.SC_COOKIE_FILE else {}


@dataclass
class SCResult:
    url: str
    title: str
    artist: str
    duration: int  # seconds


def _search_sync(query: str, max_results: int = 5, platform: str = "sc") -> list[SCResult]:
    import yt_dlp  # lazy import — optional dep

    # extract_flat breaks scsearch (entries come back without webpage_url).
    # Without it yt-dlp fully resolves each result — slower but reliable.
    opts = {
        "quiet": False,
        "no_warnings": False,
        "noplaylist": False,
        "ignoreerrors": True,
        **_proxy_opts(),
        **_cookie_opts(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"{platform}search{max_results}:{query}", download=False)
    except yt_dlp.utils.DownloadError as e:
        if _is_ban_error(str(e)):
            raise SCBanError(str(e)) from e
        raise

    if not info:
        log.warning("%s search: extract_info returned None for query=%r", platform.upper(), query)
        return []

    entries = info.get("entries") or []
    if not entries:
        log.warning("%s search: no entries for query=%r, info keys=%s", platform.upper(), query, list(info.keys()))
        return []

    results: list[SCResult] = []
    for entry in entries:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not url:
            log.debug("%s search: entry without url, title=%r", platform.upper(), entry.get("title"))
            continue
        results.append(SCResult(
            url=url,
            title=entry.get("title") or "",
            artist=entry.get("uploader") or entry.get("channel") or "",
            duration=int(entry.get("duration") or 0),
        ))
    log.info("%s search query=%r → %d results", platform.upper(), query, len(results))
    return results


def _download_sync(url: str, output_template: str) -> tuple[str, dict]:
    import yt_dlp

    # No FFmpegExtractAudio — SoundCloud already serves mp3/m4a natively.
    # Re-encoding adds CPU time and inflates file size without quality gain.
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": output_template + ".%(ext)s",
        **_proxy_opts(),
        **_cookie_opts(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        if _is_ban_error(str(e)):
            raise SCBanError(str(e)) from e
        raise

    ext = (info.get("ext") or "mp3") if info else "mp3"
    meta: dict = {}
    if info:
        meta["title"] = info.get("title") or ""
        meta["artist"] = info.get("uploader") or info.get("channel") or ""
        meta["duration"] = int(info.get("duration") or 0)
    return ext, meta


def _extract_url_info_sync(url: str) -> dict:
    import yt_dlp

    # extract_flat="in_playlist" → single tracks fully resolved, playlist entries flattened
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        **_proxy_opts(),
        **_cookie_opts(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


async def search(query: str, max_results: int = 5) -> list[SCResult]:
    """Search SoundCloud, return up to max_results SCResult objects."""
    return await asyncio.to_thread(_search_sync, query, max_results, "sc")


async def search_youtube(query: str, max_results: int = 5) -> list[SCResult]:
    """Search YouTube, return up to max_results SCResult objects."""
    return await asyncio.to_thread(_search_sync, query, max_results, "yt")


async def download(url: str, user_id: int) -> tuple[str, dict]:
    """Download audio from url, return (file_path, metadata). Retries once on failure."""
    last_exc: Exception | None = None
    for attempt in range(2):
        ts = int(time.time())
        output_template = f"/tmp/sc_{user_id}_{ts}"
        try:
            ext, meta = await asyncio.to_thread(_download_sync, url, output_template)
            path = f"{output_template}.{ext}"
            meta = await asyncio.to_thread(_fix_metadata_sync, path, meta)
            return path, meta
        except SCBanError:
            raise  # don't retry ban errors — proxy rotation logic handles retries
        except Exception as e:
            last_exc = e
            if attempt == 0:
                log.warning("Download attempt 1 failed url=%s: %s — retrying in 3s", url, e)
                await asyncio.sleep(3)
    raise last_exc  # type: ignore[misc]


def _fix_metadata_sync(path: str, meta: dict) -> dict:
    """
    Read embedded tags from the downloaded file, fix artist/title if needed,
    write corrected tags back. Returns updated meta dict.
    """
    artist = (meta.get("artist") or "").strip()
    title = (meta.get("title") or "").strip()

    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            tag_artist = ((audio.get("artist") or [""])[0] or "").strip()
            tag_title = ((audio.get("title") or [""])[0] or "").strip()
            if tag_title:
                title = tag_title
            if tag_artist:
                artist = tag_artist
    except Exception:
        pass

    # If artist is still missing but title looks like "Artist - Title", split it
    if not artist and " - " in title:
        left, right = title.split(" - ", 1)
        artist = left.strip()
        title = right.strip()

    # Write corrected tags back to file
    if artist or title:
        try:
            import mutagen
            audio = mutagen.File(path, easy=True)
            if audio is not None:
                if title:
                    audio["title"] = title
                if artist:
                    audio["artist"] = artist
                audio.save()
        except Exception:
            pass

    return {"artist": artist, "title": title, "duration": meta.get("duration", 0)}


async def extract_url_info(url: str) -> dict:
    """
    Inspect a URL without downloading.
    Returns {"type": "track", "result": SCResult}
          or {"type": "playlist", "title": str, "entries": list[SCResult]}
    Raises ValueError on unsupported URL or extraction failure.
    Supports SoundCloud and YouTube / YouTube Music URLs.
    """
    raw = await asyncio.to_thread(_extract_url_info_sync, url)
    if not raw:
        raise ValueError("No info returned for URL")

    if raw.get("entries") is not None:
        entries: list[SCResult] = []
        for e in (raw.get("entries") or []):
            if not e:
                continue
            track_url = e.get("url") or e.get("webpage_url") or ""
            if not track_url:
                continue
            entries.append(SCResult(
                url=track_url,
                title=e.get("title") or "",
                artist=e.get("uploader") or e.get("channel") or "",
                duration=int(e.get("duration") or 0),
            ))
        return {
            "type": "playlist",
            "title": raw.get("title") or raw.get("playlist_title") or "плейлист",
            "entries": entries,
        }
    else:
        return {
            "type": "track",
            "result": SCResult(
                url=url,
                title=raw.get("title") or "",
                artist=raw.get("uploader") or raw.get("channel") or "",
                duration=int(raw.get("duration") or 0),
            ),
        }
