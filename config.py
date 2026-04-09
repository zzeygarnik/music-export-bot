from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    REDIS_URL: str = "redis://localhost:6379/0"
    SC_PROXY: str = ""        # proxy for aiogram → Telegram connection, e.g. socks5://user:pass@host:port
    SC_PROXIES: str = ""      # comma-separated proxies for SC downloads fallback (rotated on IP ban)
                              # e.g. "socks5://u:p@h:1080,http://h2:8080"
    SC_SERVER_IP: str = ""    # server's public IP shown in admin proxy-rotation alerts (optional)
    SC_COOKIE_FILE: str = ""  # path to Netscape cookie file for SoundCloud auth (e.g. /app/sc_cookies.txt)
    YT_COOKIE_FILE: str = ""  # path to Netscape cookie file for YouTube auth (e.g. /app/yt_cookies.txt)
    SC_MAX_BATCH_DOWNLOADS: int = 2  # max concurrent SC batch downloads across all users
    YM_BOT_TOKEN: str = ""          # optional bot-level YM token for reading public playlists without user auth
    POSTGRES_URL: str = ""          # postgresql://user:pass@host:5432/music_bot
    # Batch download access control:
    #   ""  — disabled for everyone
    #   "*" — enabled for everyone
    #   "123456789,@username" — only listed user IDs or @usernames
    BATCH_ALLOWED_USERS: str = "*"
    ADMIN_ID: int = 0  # Telegram user_id of the bot admin (0 = disabled)
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_REDIRECT_URI: str = "http://localhost/"   # override in .env with https://DOMAIN/spotify/callback
    SPOTIFY_CALLBACK_PORT: int = 8889                 # local port for OAuth callback + webhook HTTP server
    WEBHOOK_URL: str = ""                             # e.g. https://mus111cbot.duckdns.org — enables webhook mode
    WEBHOOK_SECRET: str = ""                          # secret token for Telegram webhook verification
    DASHBOARD_TOKEN: str = ""                         # secret token for web dashboard access (set to enable)
    VK_TOKEN: str = ""                               # VK Kate Mobile token (vkpymusic) for VK music search/download

    def is_batch_allowed(self, user_id: int, username: str | None) -> bool:
        val = self.BATCH_ALLOWED_USERS.strip()
        if not val:
            return False
        if val == "*":
            return True
        for entry in val.split(","):
            entry = entry.strip()
            if entry.startswith("@"):
                if username and username.lower() == entry[1:].lower():
                    return True
            else:
                try:
                    if int(entry) == user_id:
                        return True
                except ValueError:
                    pass
        return False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
