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


async def search(query: str, max_results: int = 5) -> list[SCResult]:
    """Search SoundCloud, return up to max_results SCResult objects."""
    return await asyncio.to_thread(_search_sync, query, max_results)


async def download(url: str, user_id: int) -> tuple[str, dict]:
    """Download audio from url, return (file_path, metadata)."""
    ts = int(time.time())
    output_template = f"/tmp/sc_{user_id}_{ts}"
    ext, meta = await asyncio.to_thread(_download_sync, url, output_template)
    return f"{output_template}.{ext}", meta
