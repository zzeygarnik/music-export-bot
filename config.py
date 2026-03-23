from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    REDIS_URL: str = "redis://localhost:6379/0"
    SC_PROXY: str = ""  # proxy for yt-dlp SoundCloud requests, e.g. socks5://user:pass@host:port
    SC_MAX_BATCH_DOWNLOADS: int = 2  # max concurrent SC batch downloads across all users
    YM_BOT_TOKEN: str = ""          # optional bot-level YM token for reading public playlists without user auth
    POSTGRES_URL: str = ""          # postgresql://user:pass@host:5432/music_bot

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
