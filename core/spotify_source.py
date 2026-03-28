"""Spotify data source: public playlists (client credentials) + liked tracks (user OAuth)."""
import asyncio
import logging
import re

log = logging.getLogger(__name__)

REDIRECT_URI = "http://localhost:8888/callback"
_OAUTH_SCOPE = "user-library-read"


def _parse_playlist_id(url_or_id: str) -> str | None:
    """Extract Spotify playlist ID from URL or return bare ID as-is."""
    m = re.search(r'spotify\.com/playlist/([A-Za-z0-9]+)', url_or_id)
    if m:
        return m.group(1)
    if re.match(r'^[A-Za-z0-9]{10,30}$', url_or_id.strip()):
        return url_or_id.strip()
    return None


def parse_code_from_redirect(redirect_url: str) -> str | None:
    """Extract ?code= from the redirect URL the user pastes back."""
    m = re.search(r'[?&]code=([^&]+)', redirect_url)
    return m.group(1) if m else None


def _collect_tracks(items: list) -> list[dict]:
    tracks = []
    for item in items:
        t = item.get("track") if isinstance(item, dict) and "track" in item else item
        if not t or t.get("type") != "track":
            continue
        artist = ", ".join(a["name"] for a in t.get("artists", []))
        tracks.append({"artist": artist, "title": t["name"]})
    return tracks


class SpotifySource:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    # ── Auth URL ──────────────────────────────────────────────────────────

    def get_auth_url(self) -> str:
        import spotipy.oauth2 as oauth2
        o = oauth2.SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=REDIRECT_URI,
            scope=_OAUTH_SCOPE,
        )
        return o.get_authorize_url()

    # ── Sync internals ────────────────────────────────────────────────────

    def _exchange_code_sync(self, code: str) -> str:
        import spotipy.oauth2 as oauth2
        o = oauth2.SpotifyOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=REDIRECT_URI,
            scope=_OAUTH_SCOPE,
        )
        token_info = o.get_access_token(code, as_dict=True, check_cache=False)
        return token_info["access_token"]

    def _fetch_liked_tracks_sync(self, access_token: str) -> list[dict]:
        import spotipy
        sp = spotipy.Spotify(auth=access_token)
        tracks = []
        results = sp.current_user_saved_tracks(limit=50)
        while results:
            tracks.extend(_collect_tracks(results["items"]))
            results = sp.next(results) if results.get("next") else None
        return tracks

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
        playlist_id = _parse_playlist_id(url_or_id)
        if not playlist_id:
            raise ValueError("Не удалось распознать ссылку на плейлист Spotify.")
        return await asyncio.to_thread(self._fetch_playlist_sync, playlist_id)
