# 🎵 Music Export Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.13-2CA5E0?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Container-2496ED?logo=docker&logoColor=white)
![Dashboard](https://img.shields.io/badge/Dashboard-Web%20UI-7c3aed?logo=aiohttp&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?logo=postgresql&logoColor=white)

Telegram bot with three main sections: **export your music library to `.txt` / `.csv`** (Yandex Music and Spotify), **download tracks from SoundCloud / YouTube as `.mp3`**, and **load any shared playlist or album by link** (YM, Spotify). Self-hosted, containerized, ready for any Linux VPS or TrueNAS.

[ 🇬🇧 English](#-english) | [ 🇷🇺 Русский](#-русский)

---

## 🇬🇧 English

### ✨ Features

**Export tracks to .txt / .csv**
- Choose source: **Yandex Music** or **Spotify**
- **Yandex Music:** export liked tracks, any playlist, or by shared `lk.` link — OAuth token stored in RAM only, never on disk; session or single-use retention modes
- **Spotify:** export public playlists by link (`open.spotify.com/playlist/...`) or liked tracks via OAuth — fully automatic: user logs in via browser, bot receives the code automatically (no URL copying)
- Choose format: **`.txt`** (one line per track) or **`.csv`** (artist, title, album, year — UTF-8 BOM, Excel-compatible)
- After export — inline buttons to **Download from SoundCloud** or **Filter by artist**

**Playlist / Album by link**
- Choose source: **Yandex Music** or **Spotify**
- **Yandex Music:** send an iframe embed code from the YM app ("Share → HTML code"), a direct playlist link, `lk.UUID` share link, or an **album link** (`music.yandex.ru/album/ID`)
- **Spotify:** send any `open.spotify.com/playlist/...` or **album link** (`open.spotify.com/album/ID`)
- Two actions after loading:
  - **Download all** — opens a pre-download menu: choose order (oldest-first / newest-first), continue from a specific track, **select specific tracks** (search within playlist, toggle individually, add all by artist), or **filter by artist** (narrow the playlist to one artist before starting); then batch-downloads via SoundCloud (with YouTube fallback)
  - **Filter by artist** — enter an artist name, get a `.txt` with matching tracks, then optionally batch-download that filtered list
- If `YM_BOT_TOKEN` is set, users don't need to authenticate — bot reads public playlists with the bot-level token

**Filter by artist**
- Available after any YM or Spotify export and inside the "playlist by link" flow
- Fuzzy search across all artists per track (features included)
- Returns a `.txt` file + option to batch-download only those tracks

**SoundCloud / YouTube → .mp3 download**
- **Telegram file_id cache**: every downloaded track is stored in PostgreSQL with its Telegram `file_id`. On repeat requests the bot skips downloading entirely — Telegram serves the file instantly
- **Cache-first search flow** with transparent status messages:
  - `🔍 Searching in database…` — fuzzy lookup runs instantly
  - If found: `⚡ Found in cache: Artist — Title` → audio sent immediately
  - If not found: `🔍 Searching on SoundCloud/YouTube…` → `⏳ Downloading…` → audio
- **Fuzzy cache matching** uses three metrics (rapidfuzz): `partial_ratio`, `token_sort_ratio`, `token_set_ratio` — order-insensitive
- **Search on SoundCloud**: cache lookup → auto-download if confidence ≥ 80%, otherwise top-5 for manual selection
- **Search on YouTube**: same cache-first flow — separate button in the menu, logged as `yt_search`
- **Download by URL**: paste any SoundCloud or YouTube link — track or album downloads immediately, playlist/album starts batch download
- **Batch playlist download** (via "Playlist by link" or export flow):
  - Cache checked per track — instant send if already cached (shown as ⚡ in progress)
  - SoundCloud first, **automatic YouTube fallback** if track not found or fails
  - Choose download order: **oldest-first** or **newest-first**
  - Resume from any track (fuzzy search inside playlist)
  - **Select specific tracks** — search within the playlist by query, toggle individual tracks on/off, **add all tracks by a single artist** in one tap, paginated selected-tracks view; confirmed selection replaces the full list
  - Progress updates after each track; ⛔ Stop button at any time
  - Tracks not found on either platform shown at the end
  - **Retry failed tracks**: after batch completes, a **"🔄 Retry not found (N)"** button starts a new batch with only the failed tracks
  - **Batch queue**: if all slots are taken (`SC_MAX_BATCH_DOWNLOADS` limit), the user is placed in a numbered queue instead of receiving "bot is busy"; download starts automatically when a slot frees up; users can cancel their position at any time; `/start` also removes them from the queue
  - Concurrency limit: configurable via `SC_MAX_BATCH_DOWNLOADS`
- **Auto-retry**: one automatic retry on network error before giving up

**Spotify integration (automatic OAuth)**
- Spotify is available inside the **Export** and **Playlist/Album by link** sections — not a separate top-level button
- OAuth is fully automatic: bot sends a link → user logs in via browser → callback server receives the code → bot notifies user with an inline button to load liked tracks (no URL copying)
- Requires `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` in `.env`
- For automatic OAuth, host an HTTPS callback endpoint (e.g. via DuckDNS + Let's Encrypt + nginx) and set `SPOTIFY_REDIRECT_URI=https://YOUR_DOMAIN/spotify/callback`; the bot starts a built-in aiohttp server on `SPOTIFY_CALLBACK_PORT` (default: 8889)
- Scopes: `user-library-read` only

**Batch access request system**
- Users without batch access see an explanation page with a **"📨 Request access"** button
- Admin receives a notification with **approve / reject** inline buttons (eternal — work regardless of message age)
- Deduplication: one pending request per user at a time
- After admin decision, user receives an instant notification

**Admin panel** (`/admin`, only for `ADMIN_ID`)
- **📊 Stats** — today / last 7 days: unique users, tracks downloaded, batch sessions, errors + top 5 users by track count
- **📋 Event log** — last 20 events with timestamp (Moscow time), username, action and result
- **📥 Batch whitelist** — add / remove users (by ID or forwarded message); managed in PostgreSQL alongside `BATCH_ALLOWED_USERS` env var
- **🚫 Bans** — ban/unban any user by ID or forwarded message; blocked at middleware level
- **📨 Requests** — view and approve/reject pending batch access requests

**FAQ / Help** (`/faq`)
- Shows bot capabilities overview and privacy policy (what data is stored, Spotify token handling, temporary files)
- **"📨 Contact admin"** button starts a message flow: user writes a message → bot forwards it to admin with user info
- **24 h cooldown** while the message is unanswered — attempting to send again shows remaining time (hours + minutes); cooldown resets immediately once admin replies
- Admin replies by replying to the forwarded message in Telegram → bot delivers the reply to the user

**Personal statistics** (`/mystats`)
- Per-user download and export stats: tracks downloaded (search + batch), exports (with **YM / Spotify breakdown**), playlists downloaded, date of first activity — broken down into **last 7 days** and **all time**

**General**
- **Web dashboard** — **4 tabs**: Yandex Music / Spotify / SC+YouTube / Event log; served by the bot at `/dashboard` (password-protected via `DASHBOARD_TOKEN`)
- PostgreSQL stores events, live batch state, track file_id cache, banned users, batch whitelist, access requests
- Redis FSM storage with graceful fallback to MemoryStorage
- Middleware: **Ban check** → Throttling → Stale-button guard (eternal callbacks exempted) → Callback auto-answer
- **Batch access control**: `BATCH_ALLOWED_USERS` env var (`*` / `""` / static list) + DB whitelist via admin panel + in-bot request flow
- **Bot commands** registered on startup: `/start`, `/faq`, `/mystats` (`/admin` is intentionally omitted from the command list)
- **Webhook mode**: set `WEBHOOK_URL` in `.env` to switch from polling to webhook — bot receives updates instantly via HTTPS; falls back to long polling if not set

### 🔄 User Flow

```
/start
  → What do you want to do?
     ├─ 📋 Export tracks to .txt / .csv
     │    → Choose source: Yandex Music | Spotify
     │    │
     │    ├─ Yandex Music
     │    │    → Choose retention  (⚡ Session | 🔒 Single export)
     │    │    → Enter OAuth token
     │    │    → Choose export type:
     │    │       ├─ Liked tracks   → .txt / .csv  [+ Download from SC | Filter by artist]
     │    │       ├─ My playlists   → pick → .txt / .csv
     │    │       └─ By link        → paste link → .txt / .csv
     │    │
     │    └─ Spotify
     │         ├─ Playlist by link  → paste URL → .txt / .csv  [+ Download via SC | Filter]
     │         └─ Liked tracks      → OAuth (browser, automatic) → .txt / .csv  [+ Download | Filter]
     │
     ├─ 🎵 Download MP3
     │    ├─ 🔍 Find on SoundCloud      → cache check → (⚡ instant) or search → mp3  [+ Download more]
     │    ├─ 🔍 Find on YouTube         → cache check → (⚡ instant) or search → mp3  [+ Download more]
     │    ├─ 🔗 By URL                  → paste SC/YT link → mp3, album or batch
     │    └─ 📥 Playlist from YM        → enter OAuth token → pick playlist (incl. ❤️ Liked) → pre-download menu → batch mp3
     │
     └─ 🔗 Playlist / Album by link
          → Choose source: Yandex Music | Spotify
          │
          ├─ Yandex Music → send iframe / URL / album link → bot loads tracks
          └─ Spotify      → send playlist / album URL     → bot loads tracks
               → Choose action:
                    ├─ Download all     → oldest-first | newest-first
                    │                     | Continue from track…
                    │                     | Select specific tracks  → search / toggle / add by artist
                    │                     | Filter by artist        → enter name → filtered list → same menu
                    │                     → batch mp3  [🔄 Retry failed on finish]
                    └─ Filter by artist → enter name → .txt  [+ Download filtered → same pre-download menu]

/faq     → capabilities + privacy policy  [+ Contact admin → write message → forwarded to admin]
/admin   → admin panel (ADMIN_ID only)
/mystats → personal download & export statistics
```

### 🗂️ Project Structure

```
music-export-bot/
├── bot/
│   ├── handlers/
│   │   ├── __init__.py       # Combines all sub-routers in priority order
│   │   ├── admin_router.py   # AdminFlow: stats, logs, batch whitelist, bans, requests; admin reply forwarding
│   │   ├── common.py         # Shared globals, constants, helper functions, _pending_spotify_codes
│   │   ├── fallback.py       # Fallback handlers (registered last)
│   │   ├── sc_router.py      # SCSearchFlow + SCBatchFlow + download helpers + retry
│   │   ├── spotify_router.py # SpotifyFlow: playlists + liked tracks OAuth + auto callback
│   │   ├── ym_router.py      # ExportFlow: YM export, delivery, filter; /faq + contact flow
│   │   └── yms_router.py     # YMShareFlow: shared playlist/album by link/embed
│   ├── states.py          # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow + SpotifyFlow + AdminFlow + FAQFlow
│   ├── keyboards.py       # Inline keyboards
│   └── middleware.py      # BanMiddleware + Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py     # AbstractMusicSource (extensible)
│   ├── spotify_source.py  # Spotify source: public playlists + albums (client creds) + liked tracks (OAuth)
│   ├── ym_source.py       # Yandex Music source + batch fetch + share/album link parsing
│   └── sc_downloader.py   # yt-dlp wrapper: search() + search_youtube() + download() + extract_url_info(); post-download metadata fix via mutagen
├── utils/
│   ├── export.py          # Async .txt / .csv writer
│   ├── db.py              # PostgreSQL connection pool + schema creation
│   └── event_log.py       # Event logging → PostgreSQL (events + batch_live tables)
├── dashboard_web/
│   └── index.html         # Web dashboard UI (served by bot at /dashboard, auth via DASHBOARD_TOKEN cookie)
├── main.py                # Entry point, DB/Redis init, aiohttp server (Spotify OAuth callback + webhook + dashboard)
├── config.py              # Settings via pydantic-settings + .env
├── migrate_to_postgres.py # One-time migration script: events.jsonl → PostgreSQL
├── Dockerfile             # Development image
├── Dockerfile.prod        # Production image (deps only, code via volume)
├── docker-compose.yml     # Dev stack (bot + Redis + dashboard)
├── docker-compose.prod.yml
├── start_all.sh           # TrueNAS: starts bot + dashboard in one container
└── start_dashboard.sh     # TrueNAS: starts dashboard only (standalone)
```

### ⚙️ Configuration

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_telegram_bot_token

# Optional — MemoryStorage used if unavailable (sessions reset on restart)
REDIS_URL=redis://localhost:6379/0

# Required if Telegram or SoundCloud is blocked by your ISP (e.g. Russia/DPI)
# Used as proxy for BOTH Telegram API connection and SoundCloud downloads
# Format: http://user:pass@host:port  or  socks5://user:pass@host:port
SC_PROXY=

# Max concurrent SC batch downloads across all users (default: 2)
SC_MAX_BATCH_DOWNLOADS=2

# PostgreSQL connection string — required for event logging and dashboard
POSTGRES_URL=postgresql://user:password@host:5432/music_bot

# Optional — bot-level YM token for reading public playlists without user auth
# If set, users can share playlists without logging into Yandex Music
YM_BOT_TOKEN=

# Batch playlist download access control (default: * = everyone)
#   ""                     — disabled for all users (DB whitelist still applies)
#   "*"                    — enabled for all users
#   "123456789,@username"  — only listed Telegram user IDs or @usernames + DB whitelist
BATCH_ALLOWED_USERS=*

# Telegram user_id of the bot admin — grants access to /admin panel (0 = disabled)
ADMIN_ID=0

# Spotify integration (optional — leave empty to disable)
# Create an app at developer.spotify.com
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=

# Redirect URI registered in your Spotify app
# For automatic OAuth (no URL copying): set up HTTPS + nginx proxy, then:
# SPOTIFY_REDIRECT_URI=https://YOUR_DOMAIN/spotify/callback
# For local testing only: SPOTIFY_REDIRECT_URI=http://localhost:8889/spotify/callback
SPOTIFY_REDIRECT_URI=

# Port for the built-in HTTP server (Spotify OAuth callback + webhook, default: 8889)
SPOTIFY_CALLBACK_PORT=8889

# Webhook mode (optional — leave empty to use long polling)
# Set to your public HTTPS domain, e.g. https://yourdomain.duckdns.org
# The bot will register https://WEBHOOK_URL/bot/webhook with Telegram automatically
WEBHOOK_URL=
WEBHOOK_SECRET=

# Web dashboard access token (required to enable /dashboard)
# Generate with: openssl rand -hex 20
DASHBOARD_TOKEN=
```

> **Note on `SC_PROXY`:** If Telegram is blocked by your provider, this variable is required — without it the bot won't connect to Telegram at all. Requires `aiohttp-socks` (already in `requirements.txt`).

> **Note on `POSTGRES_URL`:** The database must exist before starting the bot. Use `migrate_to_postgres.py` to create the schema and optionally migrate existing data from `events.jsonl`.

**Getting a Yandex Music OAuth token** (the bot explains this to users automatically):

1. Open in your browser:
   ```
   https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
   ```
2. Log in with your Yandex account
3. Copy the `access_token` value from the redirect URL (between `#access_token=` and the first `&`)

**Setting up automatic Spotify OAuth** (optional, recommended for production):

1. Get a free subdomain from [DuckDNS](https://www.duckdns.org/) pointing to your VPS IP
2. Install nginx and certbot, get a Let's Encrypt certificate for your domain
3. Add proxy blocks in your nginx config:
   ```nginx
   location /spotify/callback {
       proxy_pass http://127.0.0.1:8889;
       proxy_set_header Host $host;
   }
   location /bot/webhook {
       proxy_pass http://127.0.0.1:8889;
       proxy_set_header Host $host;
   }
   location /dashboard { proxy_pass http://127.0.0.1:8889; }
   location /api/ws {
       proxy_pass http://127.0.0.1:8889;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
   }
   location /api/      { proxy_pass http://127.0.0.1:8889; }
   ```
4. Set `SPOTIFY_REDIRECT_URI=https://YOUR_DOMAIN/spotify/callback` in `.env`
5. Register the same URL as redirect URI in your [Spotify Developer app](https://developer.spotify.com/dashboard)
6. Optionally enable webhook mode: set `WEBHOOK_URL=https://YOUR_DOMAIN` and `WEBHOOK_SECRET=random_string` in `.env`
7. Optionally enable the web dashboard: set `DASHBOARD_TOKEN=random_string` in `.env`, then open `https://YOUR_DOMAIN/dashboard`

### 🚀 Deployment

**Docker Compose (recommended for local/dev):**

```bash
cp .env.example .env  # fill in BOT_TOKEN and POSTGRES_URL
docker compose up --build
# Bot + Redis; dashboard available at /dashboard if DASHBOARD_TOKEN is set
```

**Linux VPS (production, recommended):**

```bash
# Build image:
docker build -f Dockerfile.prod -t music-export-bot:latest .

# Run container (source mounted as volume — code updates apply on restart, no rebuild needed):
docker run -d --name music-bot \
  --restart unless-stopped \
  --network host \
  -v /path/to/music-export-bot:/app \
  --env-file /path/to/music-export-bot/.env \
  music-export-bot:latest
```

> Rebuild the image only when `requirements.txt` or `Dockerfile.prod` changes.

**TrueNAS Custom App:**

Create a Custom App in TrueNAS UI:

| Field | Value |
|---|---|
| Image | `music-export-bot` |
| Tag | `latest` |
| Pull Policy | `Never` |
| Restart Policy | `Unless Stopped` |
| Env: `BOT_TOKEN` | your token |
| Env: `SC_PROXY` | `socks5://user:pass@host:port` |
| Env: `POSTGRES_URL` | `postgresql://postgres:pass@nas-ip:5432/music_bot` |
| Env: `YM_BOT_TOKEN` | your YM token *(optional)* |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

**One-time database migration** (run after first build, before starting the bot):

```bash
docker run --rm \
  -v $(pwd):/app \
  -e POSTGRES_URL=postgresql://user:pass@host:5432/music_bot \
  music-export-bot:latest \
  python /app/migrate_to_postgres.py
```

This script creates the `music_bot` database, sets up tables, and migrates any existing `events.jsonl` data.

### 📊 Dashboard

The web dashboard is served by the bot itself at `/dashboard` (no separate process needed). It reads from PostgreSQL and is organized into **4 tabs**:
- **📊 Yandex Music** — all-time/daily stats for track list exports (titles to .txt/.csv, not audio files)
- **🟢 Spotify** — playlist/album load counts, liked tracks loads, export stats by format
- **☁️ SC / YouTube** — separate metrics for SoundCloud and YouTube searches, batch stats
- **📋 Event log** — filterable recent events table

**Real-time updates via WebSocket** — all numbers, charts, and batch progress update automatically every 5 seconds without page refresh. Charts refresh only when underlying data changes. Auto-reconnects on disconnect.

**UX features:**
- Active tab is saved in the URL hash (`#ym`, `#sc`, `#log`) — survives page refresh
- Logout button in the sidebar — clears auth cookie

**Access:**
Set `DASHBOARD_TOKEN` in `.env` (generate with `openssl rand -hex 20`), then open `https://YOUR_DOMAIN/dashboard`. Login is cookie-based, valid for 30 days.

### 📦 Tech Stack

| Component | Technology |
|---|---|
| Bot framework | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| Yandex Music API | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
| Spotify API | [spotipy](https://github.com/spotipy-dev/spotipy) 2.x |
| SoundCloud downloader | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Fuzzy matching | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| OAuth callback + webhook | aiohttp (built into bot process) |
| Audio metadata | [mutagen](https://github.com/quodlibet/mutagen) |
| Audio processing | ffmpeg (in Docker image) |
| FSM storage | Redis / MemoryStorage fallback |
| Event storage | PostgreSQL (psycopg2) |
| Dashboard | Custom web UI (aiohttp + vanilla JS) |
| Containerization | Docker |
| Hosting | Aeza VPS / TrueNAS Scale / any Linux server |

### 🔒 Security Notes

- OAuth tokens are **never** written to files or databases — RAM/Redis only
- `retention=single` mode clears the YM token immediately after first export
- Spotify token is held in RAM for the session duration (`user-library-read` scope only)
- User IDs in logs are stored as truncated SHA-256 hashes — no PII
- Never commit `.env`

---
---

## 🇷🇺 Русский

### ✨ Возможности

**Экспорт треков в .txt / .csv**
- Выбор источника: **Яндекс Музыка** или **Spotify**
- **Яндекс Музыка:** экспорт лайкнутых треков, любого плейлиста или по `lk.`-ссылке — токен только в RAM, два режима хранения
- **Spotify:** экспорт публичных плейлистов по ссылке или лайков через OAuth — полностью автоматический: пользователь логинится в браузере, бот получает код сам (копировать URL не нужно)
- Выбор формата: **`.txt`** (одна строка — один трек) или **`.csv`** (artist, title, album, year — UTF-8 BOM, совместимо с Excel)
- После экспорта — кнопки «Скачать с SoundCloud» и «Фильтр по исполнителю»

**Плейлист / Альбом по ссылке**
- Выбор источника: **Яндекс Музыка** или **Spotify**
- **Яндекс Музыка:** iframe-код из приложения, прямая ссылка, `lk.UUID` или **ссылка на альбом** (`music.yandex.ru/album/ID`)
- **Spotify:** ссылка на плейлист (`open.spotify.com/playlist/...`) или **альбом** (`open.spotify.com/album/ID`)
- Два действия после загрузки:
  - **Скачать все** — открывает предстартовое меню: выбор порядка (с первого / с последнего), продолжить с конкретного трека, **выбрать конкретные треки** (поиск по плейлисту, переключение по одному, добавить всех от исполнителя) или **фильтр по исполнителю** (сузить плейлист до одного артиста перед стартом); затем батчевое скачивание через SoundCloud (с YouTube-фолбэком)
  - **Фильтр по исполнителю** — введи имя, получи `.txt` с треками, можно скачать отфильтрованный список через то же предстартовое меню
- Если задан `YM_BOT_TOKEN`, пользователям не нужно авторизоваться — бот читает публичные плейлисты через бот-токен

**Фильтр по исполнителю**
- Доступен после любого YM/Spotify-экспорта и внутри флоу «плейлист по ссылке»
- Fuzzy-поиск по всем исполнителям трека (фиты учитываются)
- Возвращает `.txt` + кнопку скачать только треки этого исполнителя

**SoundCloud / YouTube → скачивание .mp3**
- **Кэш file_id**: каждый скачанный трек хранится в PostgreSQL с Telegram `file_id`. При повторном запросе скачивание пропускается — Telegram отдаёт файл мгновенно
- **Прозрачный флоу поиска**:
  - `🔍 Ищу в базе…` → при хите `⚡ Нашёл в базе: Артист — Трек` → аудио мгновенно
  - При промахе: поиск на SC/YT → скачивание → аудио
- **Fuzzy-матч** по кэшу: три метрики rapidfuzz, нечувствителен к порядку слов
- **Поиск на SoundCloud**: кэш → автоскачивание при совпадении ≥ 80%, иначе топ-5
- **Поиск на YouTube**: тот же флоу, отдельная кнопка, логируется как `yt_search`
- **Скачивание по ссылке**: вставь ссылку на трек, альбом или плейлист SC/YT
- **Батчевое скачивание** (через «Плейлист по ссылке» или экспортный флоу):
  - Кэш проверяется на каждый трек, при хите — мгновенно с отметкой ⚡
  - SoundCloud сначала, **автоматический фолбэк на YouTube**
  - Выбор порядка: от первого к последнему или наоборот
  - Возобновление с любого трека (fuzzy-поиск)
  - **Выбор конкретных треков** — поиск по плейлисту, переключение по одному, добавить всех треков исполнителя одной кнопкой, постраничный просмотр выбранного; подтверждённый список заменяет полный
  - Прогресс после каждого трека; кнопка ⛔ в любой момент
  - Ненайденные треки выводятся в конце
  - **Retry**: кнопка **«🔄 Повторить не найденные (N)»** — только проблемные треки
  - **Очередь загрузок**: если все слоты заняты (лимит `SC_MAX_BATCH_DOWNLOADS`), пользователь встаёт в пронумерованную очередь вместо сообщения «бот занят»; скачивание стартует автоматически при освобождении слота; можно выйти из очереди в любой момент; `/start` также снимает с очереди
  - Ограничение параллельности: `SC_MAX_BATCH_DOWNLOADS`
- **Авто-повтор**: одна попытка при сетевой ошибке

**Интеграция со Spotify (автоматический OAuth)**
- Spotify встроен в разделы **Экспорт** и **Плейлист/Альбом по ссылке** — не отдельная кнопка на главном экране
- OAuth полностью автоматический: бот отправляет ссылку → пользователь логинится в браузере → callback-сервер получает код → бот сам уведомляет пользователя с кнопкой загрузки лайков
- Требуется `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` в `.env`
- Для автоматического OAuth нужен HTTPS-эндпоинт (DuckDNS + Let's Encrypt + nginx); бот запускает встроенный aiohttp-сервер на `SPOTIFY_CALLBACK_PORT` (по умолчанию 8889)
- Права доступа: только `user-library-read`

**Система запросов на batch-доступ**
- Пользователи без доступа видят объяснение с кнопкой **«📨 Запросить доступ»**
- Администратор получает уведомление с кнопками одобрить / отклонить (вечные кнопки)
- Дедупликация: один pending-запрос на пользователя
- После решения пользователь получает мгновенное уведомление

**Админ-панель** (`/admin`, только для `ADMIN_ID`)
- **📊 Статистика** — сегодня / 7 дней: пользователи, треки, батч-сессии, ошибки, топ-5
- **📋 Лог событий** — последние 20 записей с временем по Москве (МСК)
- **📥 Batch-вайтлист** — добавление / удаление по ID или пересланному сообщению
- **🚫 Баны** — блокировка/разблокировка на уровне middleware
- **📨 Запросы** — просмотр и обработка запросов на batch-доступ

**FAQ / Помощь** (`/faq`)
- Обзор возможностей бота и политика конфиденциальности (что хранится, токены Spotify, временные файлы)
- Кнопка **«📨 Написать администратору»**: пользователь пишет сообщение → бот пересылает с информацией о пользователе
- **Cooldown 24 ч** пока сообщение не получило ответа — при повторной попытке бот показывает оставшееся время (часы + минуты); cooldown сбрасывается сразу после ответа администратора
- Администратор отвечает, сделав Telegram-ответ на это сообщение → бот доставляет ответ пользователю

**Персональная статистика** (`/mystats`)
- Статистика загрузок и экспортов для текущего пользователя: скачано треков (поиском и плейлистами), экспортировано (с **разбивкой ЯМ / Spotify**), плейлистов скачано, дата первой активности — за **последние 7 дней** и **за всё время**

**Общее**
- **Веб-дашборд** — **4 вкладки**: Яндекс Музыка / Spotify / SC+YouTube / Лог событий; раздаётся ботом по `/dashboard`; **real-time обновления через WebSocket** (каждые 5 сек); hash-навигация по вкладкам; кнопка выхода; защита через `DASHBOARD_TOKEN`
- PostgreSQL хранит события, состояние батча, кэш file_id, банлист, вайтлист, запросы
- Redis FSM с graceful fallback на MemoryStorage
- Стек middleware: **проверка бана** → throttling → защита от устаревших кнопок → авто-ответ на callback
- **Управление доступом к батчу**: env `BATCH_ALLOWED_USERS` + DB-вайтлист + система запросов в боте
- **Bot commands** регистрируются при старте: `/start`, `/faq`, `/mystats` (`/admin` намеренно не добавлен в список подсказок)
- **Webhook-режим**: задай `WEBHOOK_URL` в `.env` для переключения с polling на webhook — бот получает апдейты мгновенно через HTTPS; без этой переменной используется long polling

### 🔄 Флоу пользователя

```
/start
  → Что хочешь сделать?
     ├─ 📋 Экспорт треков в .txt / .csv
     │    → Выбор источника: Яндекс Музыка | Spotify
     │    │
     │    ├─ Яндекс Музыка
     │    │    → Выбор retention  (⚡ На сеанс | 🔒 Только один экспорт)
     │    │    → Ввод OAuth-токена
     │    │    → Тип экспорта:
     │    │       ├─ Любимые треки  → .txt / .csv  [+ Скачать с SC | Фильтр]
     │    │       ├─ Мои плейлисты  → выбор → .txt / .csv
     │    │       └─ По ссылке      → вставить ссылку → .txt / .csv
     │    │
     │    └─ Spotify
     │         ├─ Плейлист по ссылке  → URL → .txt / .csv  [+ Скачать через SC | Фильтр]
     │         └─ Мои лайки           → OAuth (браузер, авто) → .txt / .csv  [+ Скачать | Фильтр]
     │
     ├─ 🎵 Скачать MP3
     │    ├─ 🔍 Найти на SoundCloud  → кэш → (⚡ мгновенно) или поиск → mp3  [+ Скачать ещё]
     │    ├─ 🔍 Найти на YouTube     → кэш → (⚡ мгновенно) или поиск → mp3  [+ Скачать ещё]
     │    ├─ 🔗 По ссылке            → ссылка SC/YT → mp3, альбом или батч
     │    └─ 📥 Плейлист из ЯМ       → OAuth-токен → выбор плейлиста (включая ❤️ Лайки) → предстартовое меню → батч mp3
     │
     └─ 🔗 Плейлист / Альбом по ссылке
          → Выбор источника: Яндекс Музыка | Spotify
          │
          ├─ Яндекс Музыка → iframe / URL / ссылка на альбом → бот загружает треки
          └─ Spotify        → ссылка на плейлист / альбом    → бот загружает треки
               → Выбор действия:
                    ├─ Скачать все        → с первого | с последнего
                    │                        | Продолжить с трека…
                    │                        | Выбрать треки  → поиск / переключение / добавить по исполнителю
                    │                        | Фильтр по исполнителю → имя → отфильтрованный список → то же меню
                    │                        → батч mp3  [🔄 Retry при ошибках]
                    └─ Фильтр по исполнителю → имя → .txt  [+ Скачать отфильтрованное → то же меню]

/faq     → возможности + политика конфиденциальности  [+ Написать администратору]
/admin   → панель администратора (только ADMIN_ID)
/mystats → персональная статистика загрузок и экспортов
```

### 🗂️ Структура проекта

```
music-export-bot/
├── bot/
│   ├── handlers/
│   │   ├── __init__.py       # Объединяет все sub-роутеры в правильном порядке
│   │   ├── admin_router.py   # AdminFlow: статистика, логи, вайтлист, баны, запросы; пересылка ответов
│   │   ├── common.py         # Общие константы, хелперы, _pending_spotify_codes
│   │   ├── fallback.py       # Fallback-хендлеры
│   │   ├── sc_router.py      # SCSearchFlow + SCBatchFlow + хелперы скачивания + retry
│   │   ├── spotify_router.py # SpotifyFlow: плейлисты + лайки OAuth + авто callback
│   │   ├── ym_router.py      # ExportFlow: экспорт YM, доставка, фильтр; /faq + контакт
│   │   └── yms_router.py     # YMShareFlow: плейлист/альбом по ссылке/embed
│   ├── states.py          # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow + SpotifyFlow + AdminFlow + FAQFlow
│   ├── keyboards.py       # Inline-клавиатуры
│   └── middleware.py      # BanMiddleware + Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py     # AbstractMusicSource (расширяемо)
│   ├── spotify_source.py  # Spotify: публичные плейлисты + альбомы + лайки OAuth
│   ├── ym_source.py       # YM: batch fetch + парсинг ссылок плейлистов и альбомов
│   └── sc_downloader.py   # yt-dlp: search() + search_youtube() + download() + extract_url_info(); исправление метаданных через mutagen
├── utils/
│   ├── export.py          # Асинхронная запись .txt / .csv
│   ├── db.py              # Пул соединений PostgreSQL + создание схемы
│   └── event_log.py       # Логирование → PostgreSQL (таблицы events + batch_live)
├── dashboard_web/
│   └── index.html         # Веб-дашборд (раздаётся ботом по /dashboard, авторизация через куки DASHBOARD_TOKEN)
├── main.py                # Точка входа, инициализация БД/Redis, aiohttp-сервер (Spotify OAuth callback + webhook + дашборд)
├── config.py              # Настройки через pydantic-settings + .env
├── migrate_to_postgres.py # Одноразовая миграция events.jsonl → PostgreSQL
├── Dockerfile             # Dev-образ
├── Dockerfile.prod        # Prod-образ (только зависимости, код через volume)
├── docker-compose.yml     # Dev-стек (бот + Redis + дашборд)
├── docker-compose.prod.yml
├── start_all.sh           # TrueNAS: бот + дашборд в одном контейнере
└── start_dashboard.sh     # TrueNAS: только дашборд
```

### ⚙️ Конфигурация

Создай `.env` файл в корне проекта:

```env
BOT_TOKEN=токен_твоего_telegram_бота

# Опционально — без Redis используется MemoryStorage (сессии сбрасываются при рестарте)
REDIS_URL=redis://localhost:6379/0

# Нужен если Telegram или SoundCloud заблокированы провайдером (Россия/ТСПУ)
# Используется как прокси для Telegram API И для загрузок с SoundCloud
# Формат: http://user:pass@host:port  или  socks5://user:pass@host:port
SC_PROXY=

# Макс. одновременных батч-загрузок SC (по умолчанию: 2)
SC_MAX_BATCH_DOWNLOADS=2

# Строка подключения к PostgreSQL — нужна для логирования и дашборда
POSTGRES_URL=postgresql://user:password@host:5432/music_bot

# Опционально — бот-уровневый токен YM для чтения публичных плейлистов без авторизации
YM_BOT_TOKEN=

# Управление доступом к батчевому скачиванию (дефолт: * = все)
#   ""                     — отключено для всех (DB-вайтлист всё ещё работает)
#   "*"                    — разрешено всем
#   "123456789,@username"  — только перечисленные Telegram user ID или @username
BATCH_ALLOWED_USERS=*

# Telegram user_id владельца бота — даёт доступ к /admin (0 = отключено)
ADMIN_ID=0

# Интеграция Spotify (опционально — оставь пустым чтобы отключить)
# Создай приложение на developer.spotify.com
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=

# Redirect URI из настроек Spotify-приложения
# Для автоматического OAuth (без копирования URL): настрой HTTPS + nginx, затем:
# SPOTIFY_REDIRECT_URI=https://ВАШ_ДОМЕН/spotify/callback
SPOTIFY_REDIRECT_URI=

# Порт встроенного HTTP-сервера (Spotify OAuth callback + webhook, по умолчанию: 8889)
SPOTIFY_CALLBACK_PORT=8889

# Webhook-режим (опционально — без этой переменной используется long polling)
# Укажи публичный HTTPS-домен, например https://yourdomain.duckdns.org
# Бот автоматически зарегистрирует https://WEBHOOK_URL/bot/webhook у Telegram
WEBHOOK_URL=
WEBHOOK_SECRET=

# Токен доступа к веб-дашборду (нужен чтобы включить /dashboard)
# Сгенерировать: openssl rand -hex 20
DASHBOARD_TOKEN=
```

> **Про `SC_PROXY`:** Если Telegram заблокирован — переменная обязательна, без неё бот не подключится вообще. Требует `aiohttp-socks` (уже в `requirements.txt`).

> **Про `POSTGRES_URL`:** База должна существовать до запуска бота. Используй `migrate_to_postgres.py` для создания схемы и переноса данных из `events.jsonl`.

**Как получить OAuth-токен Яндекс Музыки** (бот объясняет это пользователям автоматически):

1. Открой в браузере:
   ```
   https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
   ```
2. Войди в аккаунт Яндекса
3. Скопируй значение `access_token` из URL редиректа (между `#access_token=` и первым `&`)

**Настройка автоматического Spotify OAuth** (опционально, рекомендуется для продакшна):

1. Получи бесплатный субдомен на [DuckDNS](https://www.duckdns.org/), привяжи к IP VPS
2. Установи nginx и certbot, получи сертификат Let's Encrypt
3. Добавь в конфиг nginx:
   ```nginx
   location /spotify/callback {
       proxy_pass http://127.0.0.1:8889;
       proxy_set_header Host $host;
   }
   location /bot/webhook {
       proxy_pass http://127.0.0.1:8889;
       proxy_set_header Host $host;
   }
   location /dashboard { proxy_pass http://127.0.0.1:8889; }
   location /api/ws {
       proxy_pass http://127.0.0.1:8889;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
   }
   location /api/      { proxy_pass http://127.0.0.1:8889; }
   ```
4. Пропиши `SPOTIFY_REDIRECT_URI=https://ВАШ_ДОМЕН/spotify/callback` в `.env`
5. Зарегистрируй тот же URI в приложении на [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
6. Опционально включи webhook: задай `WEBHOOK_URL=https://ВАШ_ДОМЕН` и `WEBHOOK_SECRET=случайная_строка` в `.env`
7. Опционально включи веб-дашборд: задай `DASHBOARD_TOKEN=случайная_строка` в `.env`, затем открой `https://ВАШ_ДОМЕН/dashboard`

### 🚀 Развёртывание

**Docker Compose (для разработки):**

```bash
cp .env.example .env  # заполни BOT_TOKEN и POSTGRES_URL
docker compose up --build
# Бот + Redis; дашборд доступен по /dashboard если задан DASHBOARD_TOKEN
```

**Linux VPS (продакшн, рекомендуется):**

```bash
# Сборка образа:
docker build -f Dockerfile.prod -t music-export-bot:latest .

# Запуск (код монтируется как volume — обновления применяются после рестарта, без пересборки):
docker run -d --name music-bot \
  --restart unless-stopped \
  --network host \
  -v /path/to/music-export-bot:/app \
  --env-file /path/to/music-export-bot/.env \
  music-export-bot:latest
```

> Пересобирать образ нужно только при изменении `requirements.txt` или `Dockerfile.prod`.

**TrueNAS Custom App:**

| Поле | Значение |
|---|---|
| Image | `music-export-bot` |
| Tag | `latest` |
| Pull Policy | `Never` |
| Restart Policy | `Unless Stopped` |
| Env: `BOT_TOKEN` | твой токен |
| Env: `SC_PROXY` | `socks5://user:pass@host:port` |
| Env: `POSTGRES_URL` | `postgresql://postgres:pass@ip-nas:5432/music_bot` |
| Env: `YM_BOT_TOKEN` | токен YM *(опционально)* |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

**Одноразовая миграция базы данных** (один раз перед первым запуском):

```bash
docker run --rm \
  -v $(pwd):/app \
  -e POSTGRES_URL=postgresql://user:pass@host:5432/music_bot \
  music-export-bot:latest \
  python /app/migrate_to_postgres.py
```

### 📊 Дашборд

Веб-дашборд раздаётся самим ботом по адресу `/dashboard` (отдельный процесс не нужен). Читает из PostgreSQL, разбит на **4 вкладки**:
- **📊 Яндекс Музыка** — статистика экспортов названий треков в .txt/.csv (не аудиофайлов)
- **🟢 Spotify** — загрузки плейлистов/альбомов, лайков, экспорты по формату
- **☁️ SC / YouTube** — раздельные метрики для SoundCloud и YouTube, статистика батча
- **📋 Лог событий** — таблица последних событий с фильтрацией

**Real-time обновления через WebSocket** — все числа, графики и прогресс батча обновляются автоматически каждые 5 секунд без обновления страницы. Графики перезапрашиваются только при изменении данных. Автопереподключение при обрыве.

**UX:** активная вкладка сохраняется в URL hash (`#ym`, `#sc`, `#log`) и восстанавливается при обновлении; кнопка выхода в сайдбаре.

**Доступ:**
Задай `DASHBOARD_TOKEN` в `.env`, затем открой `https://ВАШ_ДОМЕН/dashboard`. Авторизация через куки — токен остаётся до очистки куков.

### 📦 Технологии

| Компонент | Технология |
|---|---|
| Фреймворк бота | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| API Яндекс Музыки | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
| API Spotify | [spotipy](https://github.com/spotipy-dev/spotipy) 2.x |
| Загрузчик SC/YT | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Fuzzy-матч | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| OAuth callback + webhook | aiohttp (встроен в процесс бота) |
| Метаданные аудио | [mutagen](https://github.com/quodlibet/mutagen) |
| Аудио | ffmpeg (в Docker-образе) |
| FSM-хранилище | Redis / MemoryStorage fallback |
| Хранилище событий | PostgreSQL (psycopg2) |
| Дашборд | Кастомный веб-интерфейс (aiohttp + vanilla JS) |
| Контейнеризация | Docker |
| Хостинг | Aeza VPS / TrueNAS Scale / любой Linux-сервер |

### 🔒 Безопасность

- OAuth-токены **никогда** не пишутся в файлы или БД — только RAM/Redis
- Режим `retention=single` удаляет YM-токен сразу после первого экспорта
- Токен Spotify хранится в RAM на время сессии (только право `user-library-read`)
- ID пользователей в логах — усечённые SHA-256 хеши, без PII
- Никогда не коммить `.env`
