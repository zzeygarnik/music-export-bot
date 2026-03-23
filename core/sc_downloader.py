import asyncio
import logging
import time
from dataclasses import dataclass

from config import settings

log = logging.getLogger(__name__)


def _proxy_opts() -> dict:
    """Return yt-dlp proxy option if SC_PROXY is configured."""
    return {"proxy": settings.SC_PROXY} if settings.SC_PROXY else {}


@dataclass
class SCResult:
    url: str
    title: str
    artist: str
    duration: int  # seconds


def _search_sync(query: str, max_results: int = 5) -> list[SCResult]:
    import yt_dlp  # lazy import — optional dep

    # extract_flat breaks scsearch (entries come back without webpage_url).
    # Without it yt-dlp fully resolves each result — slower but reliable.
    opts = {
        "quiet": False,
        "no_warnings": False,
        "noplaylist": False,
        "ignoreerrors": True,
        **_proxy_opts(),
    }
    search_query = f"scsearch{max_results}:{query}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_query, download=False)

    if not info:
        log.warning("SC search: extract_info returned None for query=%r", query)
        return []

    entries = info.get("entries") or []
    if not entries:
        log.warning("SC search: no entries for query=%r, info keys=%s", query, list(info.keys()))
        return []

    results: list[SCResult] = []
    for entry in entries:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not url:
            log.debug("SC search: entry without url, title=%r", entry.get("title"))
            continue
        results.append(SCResult(
            url=url,
            title=entry.get("title") or "",
            artist=entry.get("uploader") or entry.get("channel") or "",
            duration=int(entry.get("duration") or 0),
        ))
    log.info("SC search query=%r → %d results", query, len(results))
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
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

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
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


async def search(query: str, max_results: int = 5) -> list[SCResult]:
    """Search SoundCloud, return up to max_results SCResult objects."""
    return await asyncio.to_thread(_search_sync, query, max_results)


def _yt_search_sync(query: str, max_results: int = 5) -> list[SCResult]:
    import yt_dlp

    opts = {
        "quiet": False,
        "no_warnings": False,
        "noplaylist": False,
        "ignoreerrors": True,
        **_proxy_opts(),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

    if not info:
        log.warning("YT search: extract_info returned None for query=%r", query)
        return []

    entries = info.get("entries") or []
    results: list[SCResult] = []
    for entry in entries:
        if not entry:
            continue
        url = entry.get("webpage_url") or entry.get("url") or ""
        if not url:
            continue
        results.append(SCResult(
            url=url,
            title=entry.get("title") or "",
            artist=entry.get("uploader") or entry.get("channel") or "",
            duration=int(entry.get("duration") or 0),
        ))
    log.info("YT search query=%r → %d results", query, len(results))
    return results


async def search_youtube(query: str, max_results: int = 5) -> list[SCResult]:
    """Search YouTube, return up to max_results SCResult objects."""
    return await asyncio.to_thread(_yt_search_sync, query, max_results)


async def download(url: str, user_id: int) -> tuple[str, dict]:
    """Download audio from url, return (file_path, metadata). Retries once on failure."""
    last_exc: Exception | None = None
    for attempt in range(2):
        ts = int(time.time())
        output_template = f"/tmp/sc_{user_id}_{ts}"
        try:
            ext, meta = await asyncio.to_thread(_download_sync, url, output_template)
            return f"{output_template}.{ext}", meta
        except Exception as e:
            last_exc = e
            if attempt == 0:
                log.warning("Download attempt 1 failed url=%s: %s — retrying in 3s", url, e)
                await asyncio.sleep(3)
    raise last_exc  # type: ignore[misc]


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
