from abc import ABC, abstractmethod


class AbstractMusicSource(ABC):
    """Base class for music service integrations."""

    @abstractmethod
    async def get_liked_tracks(self) -> list[dict]:
        """Return list of liked tracks as {'artist': str, 'title': str}."""
        ...

    @abstractmethod
    async def get_playlists(self) -> list[dict]:
        """Return list of playlists as {'kind': str|int, 'title': str}."""
        ...

    @abstractmethod
    async def get_playlist_tracks(self, playlist_id: str | int) -> list[dict]:
        """Return tracks for a specific playlist."""
        ...
