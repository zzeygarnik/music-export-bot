import asyncio
import logging
import re
from yandex_music import Client

from core.base_source import AbstractMusicSource

log = logging.getLogger(__name__)

_RE_USER_PLAYLIST = re.compile(
    r"music\.yandex\.(ru|com)/users/([^/?#\s]+)/playlists/(\d+)"
)
_RE_PUBLIC_LINK = re.compile(
    r"music\.yandex\.(ru|com)/playlists/lk\.([a-f0-9-]+)"
)
_BATCH_SIZE = 150


class YandexMusicSource(AbstractMusicSource):
    def __init__(self, token: str) -> None:
        self._token = token
        self._client: Client | None = None

    async def _get_client(self) -> Client:
        if self._client is None:
            self._client = await asyncio.to_thread(Client(self._token).init)
        return self._client

    async def get_liked_tracks(self) -> list[dict]:
        client = await self._get_client()

        def _fetch():
            likes = client.users_likes_tracks()
            if likes is None:
                return []
            return _tracks_to_dicts(likes.fetch_tracks())

        return await asyncio.to_thread(_fetch)

    async def get_playlists(self) -> list[dict]:
        client = await self._get_client()

        def _fetch():
            playlists = client.users_playlists_list()
            return [
                {"kind": p.kind, "title": p.title or f"Плейлист {p.kind}"}
                for p in playlists
            ]

        return await asyncio.to_thread(_fetch)

    async def get_playlist_tracks(self, playlist_id: str | int) -> list[dict]:
        client = await self._get_client()

        def _fetch():
            playlist = client.users_playlists(kind=int(playlist_id))
            if isinstance(playlist, list):
                playlist = playlist[0] if playlist else None
            if playlist is None:
                return []
            return _batch_fetch_tracks(client, playlist.tracks or [])

        return await asyncio.to_thread(_fetch)

    async def get_playlist_by_url(self, url: str) -> tuple[str, list[dict]]:
        """
        Fetch any playlist by URL using stored token.
        Returns (title, tracks). Raises ValueError with Russian message on failure.
        """
        m_user = _RE_USER_PLAYLIST.search(url)
        if m_user:
            username, kind = m_user.group(2), int(m_user.group(3))
            return await self._fetch_user_playlist(username, kind)

        m_lk = _RE_PUBLIC_LINK.search(url)
        if m_lk:
            uuid = m_lk.group(2)
            return await self._fetch_lk_playlist(uuid)

        raise ValueError(
            "Не удалось распознать ссылку.\n\n"
            "Поддерживаемые форматы:\n"
            "• <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>\n"
            "• <code>music.yandex.ru/playlists/lk.UUID</code> (кнопка «Поделиться»)"
        )

    async def _fetch_user_playlist(self, username: str, kind: int) -> tuple[str, list[dict]]:
        client = await self._get_client()

        def _sync():
            log.debug("Fetching user playlist: user=%s kind=%s", username, kind)
            playlist = client.users_playlists(kind=kind, user_id=username)
            if isinstance(playlist, list):
                playlist = playlist[0] if playlist else None
            if playlist is None:
                raise ValueError("Плейлист не найден или является приватным.")
            tracks = _batch_fetch_tracks(client, playlist.tracks or [])
            return playlist.title or "Плейлист", tracks

        return await asyncio.to_thread(_sync)

    async def _fetch_lk_playlist(self, uuid: str) -> tuple[str, list[dict]]:
        """Resolve shared lk. link via the library's own request object (proper auth headers)."""
        client = await self._get_client()

        def _sync():
            url = f"https://music.yandex.ru/api/v2.1/handlers/playlist/lk.{uuid}"
            log.debug("Resolving lk. link: %s", url)

            try:
                data = client.request.get(url)
            except Exception as e:
                log.error("lk. resolve request failed: %s", e)
                raise ValueError(
                    "Не удалось разрешить ссылку.\n\n"
                    "Попробуй скопировать прямую ссылку на плейлист:\n"
                    "<code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>"
                ) from e

            log.debug("lk. resolve raw response: %s", str(data)[:300])

            if not data:
                raise ValueError(
                    "Яндекс Музыка не вернула данные по этой ссылке.\n\n"
                    "Возможные причины:\n"
                    "• Плейлист удалён или недоступен\n"
                    "• Ссылка устарела\n\n"
                    "Попробуй прямую ссылку: <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>"
                )

            # Unwrap {"result": {...}} envelope if present
            result = data.get("result", data) if isinstance(data, dict) else data

            if not isinstance(result, dict):
                log.error("Unexpected lk. response type: %s — %s", type(result), str(data)[:300])
                raise ValueError(
                    "Неожиданный ответ от Яндекс Музыки.\n"
                    "Попробуй прямую ссылку: <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>"
                )

            owner = result.get("owner") or {}
            uid = (
                result.get("uid")
                or result.get("userId")
                or owner.get("uid")
                or owner.get("login")
            )
            kind = result.get("kind")

            log.debug("lk. resolved: uid=%s kind=%s", uid, kind)

            if not uid or kind is None:
                log.error("Could not extract uid/kind from lk. response: %s", str(result)[:300])
                raise ValueError(
                    "Не удалось получить идентификатор плейлиста.\n"
                    "Попробуй прямую ссылку: <code>music.yandex.ru/users/ИМЯ/playlists/НОМЕР</code>"
                )

            playlist = client.users_playlists(kind=int(kind), user_id=str(uid))
            if isinstance(playlist, list):
                playlist = playlist[0] if playlist else None
            if playlist is None:
                raise ValueError("Плейлист не найден или является приватным.")

            tracks = _batch_fetch_tracks(client, playlist.tracks or [])
            return playlist.title or "Плейлист", tracks

        return await asyncio.to_thread(_sync)


def _batch_fetch_tracks(client: Client, track_shorts: list) -> list[dict]:
    """Fetch full track info in batches of 150 (one request per batch)."""
    if not track_shorts:
        return []
    ids = [ts.track_id for ts in track_shorts]
    full_tracks: list = []
    for i in range(0, len(ids), _BATCH_SIZE):
        chunk = client.tracks(ids[i: i + _BATCH_SIZE]) or []
        full_tracks.extend(chunk)
    return _tracks_to_dicts(full_tracks)


def _tracks_to_dicts(tracks) -> list[dict]:
    result = []
    for t in tracks:
        if t is None:
            continue
        artists = ", ".join(a.name for a in t.artists) if t.artists else "Неизвестен"
        album = t.albums[0].title if t.albums else ""
        year = str(t.albums[0].year) if t.albums and t.albums[0].year else ""
        result.append({"artist": artists, "title": t.title or "Без названия",
                       "album": album, "year": year})
    return result
