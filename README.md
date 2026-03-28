# 🎵 Music Export Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.13-2CA5E0?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Container-2496ED?logo=docker&logoColor=white)
![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?logo=postgresql&logoColor=white)

Telegram bot with four modes: **export your Yandex Music library to `.txt`**, **download tracks from SoundCloud as `.mp3`**, **load any shared YM playlist by link or embed code**, and **export / download Spotify playlists and liked tracks**. Self-hosted, containerized, ready for TrueNAS or any Linux server.

[ 🇬🇧 English](#-english) | [ 🇷🇺 Русский](#-русский)

---

## 🇬🇧 English

### ✨ Features

**Yandex Music → .txt / .csv export**
- Export **liked tracks** in one tap
- Export **any playlist** from your library
- Export by **shared `lk.` link** (e.g. `music.yandex.ru/playlists/lk.UUID`)
- OAuth token — stored in session RAM only, never written to disk
- **Session** or **single-use** token retention modes
- Choose export format before receiving the file: **`.txt`** (one line per track) or **`.csv`** (artist, title, album, year — UTF-8 BOM, Excel-compatible)
- After export — inline buttons to **download from SoundCloud** or **filter by artist**

**Export playlist by link / embed** *(new)*
- Send the bot an **iframe embed code** from the Yandex Music app ("Share → HTML code") or any direct playlist link
- Bot parses it (supports `iframe/playlist/USER/KIND`, direct `users/.../playlists/N` links, and `lk.UUID` share links)
- Three actions after loading:
  - **Download all** — batch download every track via SoundCloud
  - **Filter by artist** — enter an artist name, get a `.txt` with matching tracks (features included), then optionally download that filtered list
  - **Start from a specific track** — enter a track name, bot finds its position, confirms next track and total count, then starts batch from there
- If `YM_BOT_TOKEN` is set in env, users don't need to authenticate — bot reads public playlists with the bot-level token

**Filter by artist** *(new)*
- Available after any YM export and inside the "playlist by link" flow
- Fuzzy search across all artists per track (comma-separated features included)
- Returns a `.txt` file + option to batch-download only those tracks

**SoundCloud / YouTube → .mp3 download**
- **Telegram file_id cache**: every downloaded track is saved in PostgreSQL with its Telegram `file_id` (artist, title, normalized key). On repeat requests the bot skips downloading entirely — Telegram serves the file from its own servers in milliseconds
- **Cache-first search flow** with transparent status messages:
  - `🔍 Searching in database…` — fuzzy lookup runs instantly
  - If found: `⚡ Found in cache: Artist — Title` → audio sent immediately, user confirms with **"Yes, download"** button
  - If not found: `🔍 Searching on SoundCloud/YouTube…` → `⏳ Downloading…` → `⏳ Uploading track…` → audio
  - If SC/YT finds a result that exactly matches the cache key: shows `⚡ Found in cache` at that point too
- **Fuzzy cache matching** uses three metrics (rapidfuzz): `partial_ratio` (title only), `token_sort_ratio` and `token_set_ratio` (full artist+title) — order-insensitive, handles "Artist Title" and "Title Artist" equally. Old cache entries without metadata are matched by normalized cache key
- **Search on SoundCloud**: cache lookup → auto-download if confidence ≥ 80%, otherwise top-5 for manual selection
- **Search on YouTube**: same cache-first flow — separate button in the menu. Logged separately as `yt_search`
- **Download by URL**: paste any SoundCloud or YouTube link — track downloads immediately, playlist starts batch download
- **Batch playlist download**: authorize with Yandex Music → pick playlist (including **liked tracks**) → download all tracks
  - Cache checked per track before downloading — instant send if already cached (shown as ⚡ in progress)
  - SoundCloud first, **automatic YouTube fallback** if track not found or download fails
  - Choose download order: **oldest-first** (new tracks appear at top in Telegram) or **newest-first**
  - Resume from any track (fuzzy search inside playlist)
  - Progress updates after each track
  - ⛔ Stop button at any time
  - Tracks not found on either platform are collected and shown at the end
  - **Retry failed tracks**: after batch completes, a **"🔄 Retry not found (N)"** button appears — starts a new batch with only the failed tracks
  - Each failed track is logged individually to the database
  - Concurrency limit: configurable via `SC_MAX_BATCH_DOWNLOADS`
- **Download more** button after every single track
- **Auto-retry**: one automatic retry on network error before giving up

**Spotify integration**
- **Public playlists by link** — paste any `open.spotify.com/playlist/...` URL, no auth required (Client Credentials)
- **Liked tracks** — OAuth via browser: bot generates auth URL → user logs in → copies redirect URL back to bot → bot exchanges code for token and fetches saved tracks
- After loading: **Export to .txt**, **Export to .csv**, **Download via SoundCloud**, **Filter by artist** — same actions as YMShareFlow
- Requires `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`; add `http://localhost:8888/callback` as redirect URI in your Spotify Developer app (Premium account required to create the app)

**Batch access request system**
- Users without batch access see a page explaining the feature instead of a plain "denied" alert
- **"📨 Request access"** button sends a notification to the admin with **approve / reject** buttons
- Admin buttons are **eternal** — they work until clicked, regardless of message age (exempt from the stale-button middleware)
- Deduplication: user can only have one pending request at a time
- After admin decision, user receives an instant notification

**Admin panel** (`/admin`, only for `ADMIN_ID`)
- **📊 Stats** — today / last 7 days: unique users, tracks downloaded, batch sessions, errors + top 5 users by track count
- **📋 Event log** — last 20 events with timestamp, username, action and result
- **📥 Batch whitelist** — add / remove users (by numeric ID or forwarded message) who are allowed to run batch downloads; managed in PostgreSQL alongside the static `BATCH_ALLOWED_USERS` env var
- **🚫 Bans** — ban any user by ID or forwarded message; banned users are blocked at middleware level before any handler runs; one-tap unban from the list
- **📨 Requests** — view all pending batch access requests; approve / reject with one tap; list refreshes in place; counter shown on button when requests are pending

**General**
- Streamlit dashboard — redesigned with **3 tabs**: Yandex Music stats / SC+YouTube stats / Event log. SC and YouTube searches tracked and displayed separately
- PostgreSQL stores events, live batch state, track file_id cache, banned users, batch whitelist and access requests
- Redis FSM storage with graceful fallback to MemoryStorage
- Middleware stack: **Ban check** → Throttling → Stale-button guard (eternal callbacks exempted) → Callback auto-answer
- **Batch access control**: `BATCH_ALLOWED_USERS` env var (`*` / `""` / static list) + DB whitelist managed via admin panel + in-bot request flow for users
- **Bot commands menu** registered on startup — `/start` and `/admin` appear in Telegram's command list

### 🔄 User Flow

```
/start
  → What do you want to do?
     ├─ 📋 Export tracks to .txt
     │    → Choose retention  (⚡ Session | 🔒 Single export)  [Back button]
     │    → Enter OAuth token                                   [Back button]
     │    → Choose export type:
     │       ├─ Liked tracks   → .txt  [+ Download from SC | Filter by artist]
     │       ├─ My playlists   → pick → .txt  [+ Download from SC | Filter by artist]
     │       └─ By link        → paste link → .txt  [+ Download from SC | Filter by artist]
     │          └─ Filter by artist → enter name → .txt  [+ Download filtered]
     │
     ├─ 🎵 Download MP3
     │    ├─ 🔍 Find on SoundCloud  → cache check → (⚡ instant) or search → mp3  [+ Download more]
     │    ├─ 🔍 Find on YouTube     → cache check → (⚡ instant) or search → mp3  [+ Download more]
     │    ├─ 🔗 By URL              → paste SC/YT link → mp3 or batch
     │    └─ 📥 YM playlist         → YM OAuth → pick playlist (incl. liked)
     │                                 → choose order (oldest-first / newest-first)
     │                                 → batch mp3  (SC → YT fallback per track)
     │
     ├─ 🔗 Export playlist by link
     │    → Send iframe HTML or URL
     │    → Bot loads playlist
     │    → Choose action:
     │         ├─ Download all         → batch mp3  [🔄 Retry failed on finish]
     │         ├─ Filter by artist     → enter name → .txt  [+ Download filtered]
     │         └─ Start from track     → enter name → confirm position → batch mp3
     │
     └─ 🎵 Spotify
          ├─ 🔗 Playlist by link  → paste URL → load tracks
          │                          → Export .txt / .csv / Download via SC / Filter by artist
          └─ ❤️ Liked tracks      → OAuth (browser) → copy redirect URL → load tracks
                                     → Export .txt / .csv / Download via SC / Filter by artist
```

### 🗂️ Project Structure

```
music-export-bot/
├── bot/
│   ├── handlers/
│   │   ├── __init__.py      # Combines all sub-routers in priority order
│   │   ├── admin_router.py  # AdminFlow: stats, logs, batch whitelist, bans, access requests
│   │   ├── common.py        # Shared globals, constants and helper functions
│   │   ├── fallback.py      # Fallback handlers (registered last)
│   │   ├── sc_router.py     # SCSearchFlow + SCBatchFlow + download helpers + retry
│   │   ├── spotify_router.py # SpotifyFlow: playlists + liked tracks OAuth
│   │   ├── ym_router.py     # ExportFlow: YM export, delivery, filter
│   │   └── yms_router.py    # YMShareFlow: shared playlist by link/embed
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow + SpotifyFlow + AdminFlow
│   ├── keyboards.py      # Inline keyboards
│   └── middleware.py     # BanMiddleware + Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (extensible)
│   ├── spotify_source.py # Spotify source: public playlists (client creds) + liked tracks (OAuth)
│   ├── ym_source.py      # Yandex Music source + batch fetch + share link parsing
│   └── sc_downloader.py  # yt-dlp wrapper: search() + search_youtube() + download() + extract_url_info()
├── utils/
│   ├── export.py         # Async .txt writer
│   ├── db.py             # PostgreSQL connection pool + schema creation
│   └── event_log.py      # Event logging → PostgreSQL (events + batch_live tables)
├── dashboard.py          # Streamlit analytics dashboard (reads from PostgreSQL)
├── main.py               # Entry point, DB/Redis init
├── config.py             # Settings via pydantic-settings + .env
├── migrate_to_postgres.py  # One-time migration script: events.jsonl → PostgreSQL
├── Dockerfile            # Development image
├── Dockerfile.prod       # Production image (deps only, code via volume)
├── docker-compose.yml    # Dev stack (bot + Redis + dashboard)
├── docker-compose.prod.yml
├── start_all.sh          # TrueNAS: starts bot + dashboard in one container
└── start_dashboard.sh    # TrueNAS: starts dashboard only (standalone)
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

# Spotify integration (optional — leave empty to disable the Spotify button)
# Create an app at developer.spotify.com, add http://localhost:8888/callback as redirect URI
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
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

### 🚀 Deployment

**Docker Compose (recommended for local/dev):**

```bash
cp .env.example .env  # fill in BOT_TOKEN and POSTGRES_URL
docker compose up --build
# Bot + Redis + Streamlit dashboard on :8501
```

**TrueNAS Custom App (production):**

> If Docker Hub is blocked, add a mirror to `/etc/docker/daemon.json` first:
> `"registry-mirrors": ["https://mirror.gcr.io"]`

```bash
# Build on TrueNAS via SSH:
sudo docker build -f /path/to/music-export-bot/Dockerfile.prod -t music-export-bot:latest /path/to/music-export-bot/
```

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

Mounting the source directory as a volume means **code updates apply on container restart** — no rebuild needed (unless `requirements.txt` or `Dockerfile.prod` changes).

**One-time database migration** (run after first build, before starting the bot):

```bash
sudo docker run --rm \
  -v $(pwd):/app \
  -e POSTGRES_URL=postgresql://user:pass@host:5432/music_bot \
  music-export-bot:latest \
  python /app/migrate_to_postgres.py
```

This script creates the `music_bot` database, sets up tables, and migrates any existing `events.jsonl` data.

### 📊 Dashboard

The Streamlit dashboard reads from PostgreSQL and is organized into **3 tabs**:
- **📊 Yandex Music** — all-time/daily export stats, breakdown by export type
- **☁️ SC / YouTube** — separate metrics for SoundCloud and YouTube searches, batch stats, top downloaded tracks
- **📋 Event log** — filterable recent events table with correct SC/YT distinction (`sc_search` vs `yt_search`)
- **🔴 Live batch progress** (always visible at top): real-time progress bar, current track, not-found list

**Local:**
```bash
streamlit run dashboard.py
# Open http://localhost:8501
```

**TrueNAS — unified container (bot + dashboard):**

`start_all.sh` starts the Streamlit dashboard in the background and the bot in the foreground — both in the same container, sharing the same volume and PostgreSQL connection.

| Field | Value |
|---|---|
| Entrypoint | `/bin/sh` |
| Command | `/app/start_all.sh` |
| Host Port → Container Port | `8501 → 8501 TCP` |

Open dashboard at `http://your-nas-ip:8501`

### 📦 Tech Stack

| Component | Technology |
|---|---|
| Bot framework | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| Yandex Music API | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
| Spotify API | [spotipy](https://github.com/spotipy-dev/spotipy) 2.x |
| SoundCloud downloader | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Fuzzy matching | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| Audio processing | ffmpeg (in Docker image) |
| FSM storage | Redis / MemoryStorage fallback |
| Event storage | PostgreSQL (psycopg2) |
| Dashboard | [Streamlit](https://streamlit.io/) + pandas |
| Containerization | Docker |
| Hosting | Aeza VPS / any Linux server |

### 🔒 Security Notes

- OAuth tokens are **never** written to files or databases — RAM/Redis only
- `retention=single` mode clears the token immediately after first export
- User IDs in logs are stored as truncated SHA-256 hashes — no PII
- Never commit `.env`

---
---

## 🇷🇺 Русский

### ✨ Возможности

**Яндекс Музыка → экспорт в .txt / .csv**
- Экспорт **лайкнутых треков** в один клик
- Экспорт **любого плейлиста** из библиотеки
- Экспорт по **`lk.`-ссылке** (например `music.yandex.ru/playlists/lk.UUID`)
- OAuth-токен хранится только в памяти — никогда не пишется на диск
- Два режима хранения токена: **на весь сеанс** или **только один экспорт**
- Выбор формата перед получением файла: **`.txt`** (одна строка — один трек) или **`.csv`** (artist, title, album, year — UTF-8 BOM, совместимо с Excel)
- После экспорта — кнопки «📥 Скачать с SoundCloud» и «Фильтр по исполнителю»

**Экспорт плейлиста по ссылке** *(новое)*
- Отправь боту **iframe-код** из приложения Яндекс Музыки («Поделиться → HTML-код для встраивания») или прямую ссылку на плейлист
- Бот парсит ссылку (поддерживает `iframe/playlist/USER/KIND`, прямые `users/.../playlists/N` и `lk.UUID`)
- Три действия после загрузки:
  - **Скачать все** — батчевое скачивание через SoundCloud
  - **Фильтр по исполнителю** — введи имя, получи `.txt` с треками (включая фиты), затем можно скачать отфильтрованный список
  - **Начать с определённого трека** — введи название, бот найдёт позицию, покажет следующий трек и итоговое количество, запустит батч
- Если задан `YM_BOT_TOKEN`, пользователям не нужно авторизоваться — бот читает публичные плейлисты через бот-токен

**Фильтр по исполнителю** *(новое)*
- Доступен после любого YM-экспорта и внутри флоу «плейлист по ссылке»
- Fuzzy-поиск по всем исполнителям трека (фиты учитываются)
- Возвращает `.txt` + кнопку «Скачать треки этого исполнителя»

**SoundCloud / YouTube → скачивание .mp3**
- **Кэш file_id в Telegram**: каждый скачанный трек сохраняется в PostgreSQL с Telegram `file_id` (artist, title, нормализованный ключ). При повторном запросе скачивание пропускается — Telegram отдаёт файл со своих серверов мгновенно
- **Прозрачный флоу поиска**:
  - `🔍 Ищу в базе…` — быстрый fuzzy-поиск по кэшу
  - При хите: `⚡ Нашёл в базе: Артист — Трек` → аудио мгновенно, пользователь подтверждает кнопкой **"Да, скачать"**
  - При промахе: `🔍 Ищу на SoundCloud/YouTube…` → `⏳ Скачиваю…` → `⏳ Выгружаю трек…` → аудио
  - Если SC/YT находит результат, совпадающий с точным ключом кэша — показывает `⚡ Нашёл в базе` и на этом этапе
- **Fuzzy-поиск по кэшу** использует три метрики (rapidfuzz): `partial_ratio` (только title), `token_sort_ratio` и `token_set_ratio` (artist+title) — нечувствителен к порядку слов: "Артист Трек" и "Трек Артист" дают одинаковый результат. Старые записи без метаданных матчатся по нормализованному ключу
- **Поиск на SoundCloud**: проверка кэша → автоскачивание при совпадении ≥ 80%, иначе — выбор из топ-5
- **Поиск на YouTube**: тот же флоу с кэшем — отдельная кнопка в меню. Логируется отдельно как `yt_search`
- **Скачивание по ссылке**: вставь ссылку на трек или плейлист SoundCloud / YouTube
- **Батчевое скачивание плейлиста**: авторизация в Яндекс Музыке → выбор плейлиста (включая **любимые треки**) → последовательное скачивание
  - Перед каждым треком — проверка кэша, при хите — мгновенная отправка с отметкой ⚡ в прогрессе
  - Сначала SoundCloud, **автоматический фолбэк на YouTube** если трек не найден или не скачался
  - Выбор порядка: **от первого добавленного к последнему** (новые треки окажутся наверху в Telegram) или **от последнего к первому**
  - Возобновление с любого трека (fuzzy-поиск внутри плейлиста)
  - Прогресс после каждого трека
  - Кнопка ⛔ Остановить в любой момент
  - Ненайденные треки (нигде) выводятся в конце, каждый логируется отдельно в БД
  - **Retry**: после завершения батча появляется кнопка **«🔄 Повторить не найденные (N)»** — запускает новый батч только с ними
  - Ограничение параллельности: `SC_MAX_BATCH_DOWNLOADS`
- Кнопка **"Скачать ещё"** после каждого одиночного скачивания
- **Авто-повтор**: одна автоматическая попытка при сетевой ошибке

**Интеграция со Spotify**
- **Публичные плейлисты по ссылке** — вставь любую ссылку `open.spotify.com/playlist/...`, авторизация не нужна (Client Credentials)
- **Мои лайки** — OAuth через браузер: бот генерирует ссылку → пользователь входит → копирует redirect URL обратно в бот → бот обменивает код на токен и загружает сохранённые треки
- После загрузки: **Экспорт в .txt**, **Экспорт в .csv**, **Скачать через SoundCloud**, **Фильтр по исполнителю**
- Требуется `SPOTIFY_CLIENT_ID` и `SPOTIFY_CLIENT_SECRET` в `.env`; нужно добавить `http://localhost:8888/callback` как redirect URI в Spotify Developer Dashboard (требуется аккаунт с Premium для создания приложения)

**Система запросов на batch-доступ**
- Пользователи без доступа к батчу видят страницу с объяснением вместо просто «нет доступа»
- Кнопка **«📨 Запросить доступ»** отправляет уведомление администратору с кнопками **одобрить / отклонить**
- Кнопки администратора **вечные** — работают до нажатия, независимо от возраста сообщения (исключены из stale-button middleware)
- Дедупликация: у пользователя может быть только один pending-запрос
- После решения администратора пользователь получает мгновенное уведомление

**Админ-панель** (`/admin`, только для `ADMIN_ID`)
- **📊 Статистика** — сегодня / 7 дней: уникальных пользователей, треков скачано, батч-сессий, ошибок + топ-5 пользователей по трекам
- **📋 Лог событий** — последние 20 событий с временем, именем пользователя, действием и результатом
- **📥 Batch-вайтлист** — добавление / удаление пользователей (по числовому ID или пересланному сообщению), которым разрешено батчевое скачивание; хранится в PostgreSQL вместе со статическим `BATCH_ALLOWED_USERS`
- **🚫 Баны** — заблокировать любого пользователя по ID или пересланному сообщению; заблокированные отсекаются на уровне middleware до любого хендлера; разблокировка в один тап из списка
- **📨 Запросы** — просмотр всех pending-запросов на batch-доступ; одобрение / отклонение одним тапом; список обновляется на месте; на кнопке показывается счётчик если есть ожидающие запросы

**Общее**
- Streamlit-дашборд — переработан в **3 вкладки**: статистика YM / SC+YouTube / лог событий. SC и YouTube отслеживаются и отображаются раздельно
- PostgreSQL хранит события, состояние батча, кэш file_id треков, заблокированных пользователей, batch-вайтлист и запросы на доступ
- Redis FSM-хранилище с graceful fallback на MemoryStorage
- Стек middleware: **проверка бана** → throttling → защита от устаревших кнопок (вечные колбэки исключены) → авто-ответ на callback
- **Управление доступом к батчу**: env var `BATCH_ALLOWED_USERS` (`*` / `""` / статический список) + DB-вайтлист через админку + система запросов прямо в боте
- **Bot commands menu** регистрируется при старте — `/start` и `/admin` отображаются в списке команд

### 🔄 Флоу пользователя

```
/start
  → Что хочешь сделать?
     ├─ 📋 Экспорт треков в .txt
     │    → Выбор retention  (⚡ На весь сеанс | 🔒 Только один экспорт)  [Назад]
     │    → Ввод OAuth-токена                                               [Назад]
     │    → Выбор типа экспорта:
     │       ├─ Любимые треки  → .txt  [+ Скачать с SC | Фильтр по исполнителю]
     │       ├─ Мои плейлисты  → выбор → .txt  [+ SC | Фильтр]
     │       └─ По ссылке      → вставить ссылку → .txt  [+ SC | Фильтр]
     │          └─ Фильтр → имя исполнителя → .txt  [+ Скачать отфильтрованное]
     │
     ├─ 🎵 Скачать MP3
     │    ├─ 🔍 Найти на SoundCloud  → проверка кэша → (⚡ мгновенно) или поиск → mp3  [+ Скачать ещё]
     │    ├─ 🔍 Найти на YouTube     → проверка кэша → (⚡ мгновенно) или поиск → mp3  [+ Скачать ещё]
     │    ├─ 🔗 По ссылке            → ссылка SC/YT → mp3 или батч
     │    └─ 📥 Плейлист YM          → OAuth → выбор плейлиста
     │                                  → выбор порядка (от первого / от последнего)
     │                                  → батч mp3  (SC → YT фолбэк на каждый трек)
     │
     ├─ 🔗 Экспорт плейлиста по ссылке
     │    → Отправить iframe-код или ссылку
     │    → Бот загружает плейлист
     │    → Выбор действия:
     │         ├─ Скачать все            → батч mp3  [🔄 Retry при ошибках]
     │         ├─ Фильтр по исполнителю → имя → .txt  [+ Скачать отфильтрованное]
     │         └─ Начать с трека         → название → подтверждение → батч mp3
     │
     └─ 🎵 Spotify
          ├─ 🔗 Плейлист по ссылке  → вставить URL → загрузка треков
          │                            → Экспорт .txt / .csv / Скачать через SC / Фильтр
          └─ ❤️ Мои лайки           → OAuth (браузер) → скопировать redirect URL → загрузка
                                       → Экспорт .txt / .csv / Скачать через SC / Фильтр
```

### 🗂️ Структура проекта

```
music-export-bot/
├── bot/
│   ├── handlers/
│   │   ├── __init__.py      # Объединяет все sub-роутеры в правильном порядке
│   │   ├── admin_router.py  # AdminFlow: статистика, логи, вайтлист, баны, запросы на доступ
│   │   ├── common.py        # Общие глобальные переменные, константы и хелперы
│   │   ├── fallback.py      # Fallback-хендлеры (регистрируются последними)
│   │   ├── sc_router.py     # SCSearchFlow + SCBatchFlow + хелперы скачивания + retry
│   │   ├── spotify_router.py # SpotifyFlow: плейлисты + лайки через OAuth
│   │   ├── ym_router.py     # ExportFlow: экспорт YM, доставка файла, фильтр
│   │   └── yms_router.py    # YMShareFlow: плейлист по ссылке/embed
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow + SpotifyFlow + AdminFlow
│   ├── keyboards.py      # Inline-клавиатуры
│   └── middleware.py     # BanMiddleware + Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (расширяемо)
│   ├── spotify_source.py # Источник Spotify: публичные плейлисты + лайки OAuth
│   ├── ym_source.py      # Источник YM + батчевый fetch + парсинг share-ссылок
│   └── sc_downloader.py  # yt-dlp обёртка: search() + search_youtube() + download() + extract_url_info()
├── utils/
│   ├── export.py         # Асинхронная запись .txt
│   ├── db.py             # Пул соединений PostgreSQL + создание схемы
│   └── event_log.py      # Логирование → PostgreSQL (таблицы events + batch_live)
├── dashboard.py          # Streamlit-дашборд (читает из PostgreSQL)
├── main.py               # Точка входа, инициализация БД и Redis
├── config.py             # Настройки через pydantic-settings + .env
├── migrate_to_postgres.py  # Одноразовая миграция events.jsonl → PostgreSQL
├── Dockerfile            # Dev-образ
├── Dockerfile.prod       # Prod-образ (только зависимости, код через volume)
├── docker-compose.yml    # Dev-стек (бот + Redis + дашборд)
├── docker-compose.prod.yml
├── start_all.sh          # TrueNAS: бот + дашборд в одном контейнере
└── start_dashboard.sh    # TrueNAS: только дашборд
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

# Опционально — бот-уровневый токен YM для чтения публичных плейлистов без авторизации пользователя
YM_BOT_TOKEN=

# Управление доступом к батчевому скачиванию плейлистов (дефолт: * = все)
#   ""                     — отключено для всех (DB-вайтлист всё ещё работает)
#   "*"                    — разрешено всем
#   "123456789,@username"  — только перечисленные Telegram user ID или @username + DB-вайтлист
BATCH_ALLOWED_USERS=*

# Telegram user_id владельца бота — даёт доступ к /admin панели (0 = отключено)
ADMIN_ID=0

# Интеграция Spotify (опционально — оставь пустым чтобы скрыть кнопку)
# Создай приложение на developer.spotify.com, добавь http://localhost:8888/callback как redirect URI
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
```

> **Про `SC_PROXY`:** Если Telegram заблокирован у провайдера — переменная обязательна, без неё бот не подключится вообще. Требует `aiohttp-socks` (уже включён в `requirements.txt`).

> **Про `POSTGRES_URL`:** База данных должна существовать до запуска бота. Используй `migrate_to_postgres.py` для создания схемы и переноса данных из `events.jsonl`.

**Как получить OAuth-токен Яндекс Музыки** (бот объясняет это пользователям автоматически):

1. Открой в браузере:
   ```
   https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
   ```
2. Войди в аккаунт Яндекса
3. Скопируй значение `access_token` из URL редиректа (между `#access_token=` и первым `&`)

### 🚀 Развёртывание

**Docker Compose (для разработки):**

```bash
cp .env.example .env  # заполни BOT_TOKEN и POSTGRES_URL
docker compose up --build
# Бот + Redis + Streamlit дашборд на :8501
```

**TrueNAS Custom App (продакшн):**

> Если Docker Hub заблокирован — добавь зеркало в `/etc/docker/daemon.json`:
> `"registry-mirrors": ["https://mirror.gcr.io"]`

```bash
sudo docker build -f /путь/к/music-export-bot/Dockerfile.prod -t music-export-bot:latest /путь/к/music-export-bot/
```

Создай Custom App в TrueNAS UI:

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

**Одноразовая миграция базы данных** (выполни один раз перед первым запуском бота):

```bash
sudo docker run --rm \
  -v $(pwd):/app \
  -e POSTGRES_URL=postgresql://user:pass@host:5432/music_bot \
  music-export-bot:latest \
  python /app/migrate_to_postgres.py
```

Скрипт создаёт базу `music_bot`, таблицы и переносит данные из `events.jsonl`.

### 📊 Дашборд

Streamlit-дашборд читает из PostgreSQL и разбит на **3 вкладки**:
- **📊 Яндекс Музыка** — статистика экспортов за всё время и по дням, разбивка по типам
- **☁️ SC / YouTube** — раздельные метрики для SoundCloud и YouTube, статистика батча, топ скачанных треков
- **📋 Лог событий** — таблица с фильтрацией по периоду, SC и YT поиски отображаются раздельно (`sc_search` / `yt_search`)
- **🔴 Live-прогресс батча** (всегда вверху): прогресс-бар, текущий трек, список ненайденных в реальном времени

**Локально:**
```bash
streamlit run dashboard.py
# Открыть http://localhost:8501
```

**TrueNAS — единый контейнер (бот + дашборд):**

`start_all.sh` запускает Streamlit фоном и бот форграундом в одном контейнере.

| Поле | Значение |
|---|---|
| Entrypoint | `/bin/sh` |
| Command | `/app/start_all.sh` |
| Host Port → Container Port | `8501 → 8501 TCP` |

Открыть дашборд: `http://ip-nas:8501`

### 📦 Технологии

| Компонент | Технология |
|---|---|
| Фреймворк бота | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| API Яндекс Музыки | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
| Загрузчик SoundCloud | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Fuzzy-матч | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| Аудио | ffmpeg (в Docker-образе) |
| FSM-хранилище | Redis / MemoryStorage fallback |
| Хранилище событий | PostgreSQL (psycopg2) |
| Дашборд | [Streamlit](https://streamlit.io/) + pandas |
| Контейнеризация | Docker |
| Хостинг | TrueNAS Scale / любой Linux-сервер |

### 🔒 Безопасность

- OAuth-токены **никогда** не пишутся в файлы или БД — только RAM/Redis
- Режим `retention=single` удаляет токен сразу после первого экспорта
- ID пользователей в логах хранятся как усечённые SHA-256 хеши — без PII
- Никогда не коммить `.env`
