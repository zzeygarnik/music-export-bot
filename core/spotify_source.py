"""Spotify data source: public playlists (client credentials) + liked tracks (user OAuth)."""
import asyncio
import logging
import re

from config import settings

log = logging.getLogger(__name__)

_OAUTH_SCOPE = "user-library-read"


def _parse_spotify_item(url_or_id: str) -> tuple[str, str] | None:
    """Return (type, id) where type is 'playlist' or 'album'."""
    m = re.search(r'spotify\.com/(playlist|album)/([A-Za-z0-9]+)', url_or_id)
    if m:
        return m.group(1), m.group(2)
    # bare ID — assume playlist
    if re.match(r'^[A-Za-z0-9]{10,30}$', url_or_id.strip()):
        return 'playlist', url_or_id.strip()
    return None


def parse_code_from_redirect(redirect_url: str) -> str | None:
    """Extract ?code= from the redirect URL the user pastes back."""
    m = re.search(r'[?&]code=([^&]+)', redirect_url)
    return m.group(1) if m else None


def _collect_tracks(items: list) -> list[dict]:
    tracks = []
    skipped_types: list[str] = []
    for item in items:
        t = item.get("track") if isinstance(item, dict) and "track" in item else item
        if not t:
            skipped_types.append("null")
            continue
        item_type = t.get("type", "unknown")
        if item_type != "track":
            skipped_types.append(item_type)
            continue
        artist = ", ".join(a["name"] for a in t.get("artists", []))
        tracks.append({"artist": artist, "title": t["name"]})
    if skipped_types:
        log.info("Spotify _collect_tracks: skipped %d items by type: %s",
                 len(skipped_types), skipped_types[:10])
    return tracks


class SpotifySource:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    # ── Auth URL ──────────────────────────────────────────────────────────

    def _get_oauth(self):
        import spotipy.oauth2 as oauth2
        import spotipy.cache_handler as cache_handler
        return oauth2.SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=settings.SPOTIFY_REDIRECT_URI,
            scope=_OAUTH_SCOPE,
            cache_handler=cache_handler.MemoryCacheHandler(),
        )

    def _get_auth_url_sync(self, state: str = "") -> str:
        return self._get_oauth().get_authorize_url(state=state)

    async def get_auth_url(self, state: str = "") -> str:
        return await asyncio.to_thread(self._get_auth_url_sync, state)

    # ── Sync internals ────────────────────────────────────────────────────

    def _exchange_code_sync(self, code: str) -> str:
        o = self._get_oauth()
        token_info = o.get_access_token(code, as_dict=True, check_cache=False)
        if not token_info:
            raise ValueError("Spotify не вернул токен.")
        return token_info["access_token"]

    def _fetch_liked_tracks_sync(self, access_token: str) -> list[dict]:
        import spotipy
        sp = spotipy.Spotify(auth=access_token)
        tracks = []
        results = sp.current_user_saved_tracks(limit=50)
        page = 0
        while results:
            items = results.get("items") or []
            collected = _collect_tracks(items)
            log.debug(
                "Spotify liked page %d: api_items=%d collected=%d total_so_far=%d",
                page, len(items), len(collected), len(tracks) + len(collected),
            )
            tracks.extend(collected)
            results = sp.next(results) if results.get("next") else None
            page += 1
        log.info("Spotify liked tracks total: %d", len(tracks))
        return tracks

    def _fetch_album_sync(self, album_id: str) -> tuple[str, list[dict]]:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=self.client_id,
            client_secret=self.client_secret,
        ))
        album = sp.album(album_id)
        title = album.get("name", "Spotify Album")
        tracks: list[dict] = []
        results = sp.album_tracks(album_id, limit=50)
        while results:
            for item in (results.get("items") or []):
                if not item:
                    continue
                artist = ", ".join(a["name"] for a in item.get("artists", []))
                tracks.append({"artist": artist, "title": item["name"]})
            results = sp.next(results) if results.get("next") else None
        return title, tracks

    def _fetch_playlist_sync(self, playlist_id: str) -> tuple[str, list[dict]]:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=self.client_id,
            client_secret=self.client_secret,
        ))
        pl = sp.playlist(playlist_id, fields="name")
        title = pl.get("name", "Spotify Playlist")
        tracks = []
        results = sp.playlist_items(
            playlist_id, limit=100,
            fields="next,items(track(name,artists,type))",
        )
        while results:
            tracks.extend(_collect_tracks(results.get("items", [])))
            results = sp.next(results) if results.get("next") else None
        return title, tracks

    # ── Async public API ──────────────────────────────────────────────────

    async def exchange_code(self, code: str) -> str:
        return await asyncio.to_thread(self._exchange_code_sync, code)

    async def get_liked_tracks(self, access_token: str) -> list[dict]:
        return await asyncio.to_thread(self._fetch_liked_tracks_sync, access_token)

    async def get_playlist(self, url_or_id: str) -> tuple[str, list[dict]]:
        item = _parse_spotify_item(url_or_id)
        if not item:
            raise ValueError("Не удалось распознать ссылку на плейлист или альбом Spotify.")
        item_type, item_id = item
        if item_type == "album":
            return await asyncio.to_thread(self._fetch_album_sync, item_id)
        return await asyncio.to_thread(self._fetch_playlist_sync, item_id)
