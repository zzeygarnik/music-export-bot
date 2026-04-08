"""VK Music source via vkpymusic (Kate Mobile token).

Usage:
    results = await search("Playboi Carti - Sky", count=5)
    path, meta = await download_track(results[0])
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class VKTrack:
    track_id: str   # "{owner_id}_{audio_id}"
    artist: str
    title: str
    duration: int   # seconds
    url: str        # direct MP3 URL (expires ~24h)


def _get_service():
    """Return vkpymusic Service or None if not configured."""
    from config import settings
    token = settings.VK_TOKEN
    if not token:
        return None
    try:
        from vkpymusic import Service
        return Service(user_token=token)
    except Exception as e:
        log.warning("VK Service init failed: %s", e)
        return None


def _search_sync(query: str, count: int) -> list[VKTrack]:
    service = _get_service()
    if not service:
        return []
    try:
        songs = service.search_songs_by_text(query, count=count)
        results = []
        for song in (songs or []):
            url = getattr(song, "url", "") or ""
            if not url:
                continue
            results.append(VKTrack(
                track_id=f"{song.owner_id}_{song.id}",
                artist=getattr(song, "artist", "") or "",
                title=getattr(song, "title", "") or "",
                duration=int(getattr(song, "duration", 0) or 0),
                url=url,
            ))
        return results
    except Exception as e:
        log.warning("VK search '%s' failed: %s", query, e)
        return []


def _download_sync(url: str, user_id: int) -> tuple[str, dict]:
    """Download VK audio URL to /tmp, return (path, meta)."""
    import urllib.request
    ts = int(time.time())
    path = f"/tmp/vk_{user_id}_{ts}.mp3"
    headers = {
        "User-Agent": "VKAndroidApp/5.52-4543 (Android 5.1.1; SDK 22; x86_64; unknown Android SDK built for x86_64; en; 320x240)",
        "Accept": "*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    return path, {}


async def search(query: str, count: int = 5) -> list[VKTrack]:
    """Search VK music. Returns up to `count` VKTrack objects."""
    return await asyncio.to_thread(_search_sync, query, count)


async def download_track(track: VKTrack, user_id: int) -> tuple[str, dict]:
    """Download VKTrack to /tmp. Returns (file_path, meta_dict)."""
    path, _ = await asyncio.to_thread(_download_sync, track.url, user_id)
    meta = {"artist": track.artist, "title": track.title, "duration": track.duration}

    # Write ID3 tags
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio is not None:
            if track.title:
                audio["title"] = track.title
            if track.artist:
                audio["artist"] = track.artist
            audio.save()
    except Exception:
        pass

    return path, meta


def is_configured() -> bool:
    """Return True if VK_TOKEN is set."""
    from config import settings
    return bool(settings.VK_TOKEN)
