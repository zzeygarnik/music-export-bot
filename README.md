# 🎵 Music Export Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.13-2CA5E0?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Container-2496ED?logo=docker&logoColor=white)
![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?logo=postgresql&logoColor=white)

Telegram bot with three modes: **export your Yandex Music library to `.txt`**, **download tracks from SoundCloud as `.mp3`**, and **load any shared YM playlist by link or embed code**. Self-hosted, containerized, ready for TrueNAS or any Linux server.

[ 🇬🇧 English](#-english) | [ 🇷🇺 Русский](#-русский)

---

## 🇬🇧 English

### ✨ Features

**Yandex Music → .txt export**
- Export **liked tracks** in one tap
- Export **any playlist** from your library
- Export by **shared `lk.` link** (e.g. `music.yandex.ru/playlists/lk.UUID`)
- OAuth token — stored in session RAM only, never written to disk
- **Session** or **single-use** token retention modes
- After playlist export — inline buttons to **download from SoundCloud** or **filter by artist**

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
- **Single track search**: fuzzy match (rapidfuzz) — auto-download if confidence ≥ 80%, otherwise show top-5 for manual selection
- **Download by URL**: paste any SoundCloud or YouTube link — track downloads immediately, playlist starts batch download
- **Batch playlist download**: authorize with Yandex Music → pick playlist (including **liked tracks**) → download all tracks via SoundCloud / YouTube
  - Resume from any track (fuzzy search inside playlist)
  - Progress updates after each track
  - ⛔ Stop button at any time
  - Tracks not found are collected and shown at the end
  - Each failed track is logged individually to the database
  - Concurrency limit: configurable via `SC_MAX_BATCH_DOWNLOADS`
- **Download more** button after every single track
- **Auto-retry**: one automatic retry on network error before giving up

**General**
- Streamlit dashboard with real-time batch progress and usage statistics (powered by PostgreSQL)
- Redis FSM storage with graceful fallback to MemoryStorage
- Throttling + stale-button guard middleware

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
     │    ├─ 🔍 Find track      → type query → mp3  [+ Download more]
     │    ├─ 🔗 By URL          → paste SC/YT link → mp3 or batch
     │    └─ 📥 YM playlist     → YM OAuth → pick playlist (incl. liked) → batch mp3
     │
     └─ 🔗 Export playlist by link  (new)
          → Send iframe HTML or URL
          → Bot loads playlist
          → Choose action:
               ├─ Download all         → batch mp3
               ├─ Filter by artist     → enter name → .txt  [+ Download filtered]
               └─ Start from track     → enter name → confirm position → batch mp3
```

### 🗂️ Project Structure

```
music-export-bot/
├── bot/
│   ├── handlers.py       # aiogram handlers + FSM logic (YM + SC + YMShare + filter)
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow
│   ├── keyboards.py      # Inline keyboards
│   └── middleware.py     # Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (extensible)
│   ├── ym_source.py      # Yandex Music source + batch fetch + share link parsing
│   └── sc_downloader.py  # yt-dlp wrapper: search() + download() + extract_url_info()
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

The Streamlit dashboard reads from PostgreSQL and includes:
- All-time and daily export statistics
- SoundCloud search and batch download metrics
- **🔴 Live batch progress**: real-time progress bar, current track being downloaded, not-found list so far
- Recent events table with filtering by period

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
| SoundCloud downloader | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Fuzzy matching | [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) |
| Audio processing | ffmpeg (in Docker image) |
| FSM storage | Redis / MemoryStorage fallback |
| Event storage | PostgreSQL (psycopg2) |
| Dashboard | [Streamlit](https://streamlit.io/) + pandas |
| Containerization | Docker |
| Hosting | TrueNAS Scale / any Linux server |

### 🔒 Security Notes

- OAuth tokens are **never** written to files or databases — RAM/Redis only
- `retention=single` mode clears the token immediately after first export
- User IDs in logs are stored as truncated SHA-256 hashes — no PII
- Never commit `.env`

---
---

## 🇷🇺 Русский

### ✨ Возможности

**Яндекс Музыка → экспорт в .txt**
- Экспорт **лайкнутых треков** в один клик
- Экспорт **любого плейлиста** из библиотеки
- Экспорт по **`lk.`-ссылке** (например `music.yandex.ru/playlists/lk.UUID`)
- OAuth-токен хранится только в памяти — никогда не пишется на диск
- Два режима хранения токена: **на весь сеанс** или **только один экспорт**
- После экспорта плейлиста — кнопки «📥 Скачать с SoundCloud» и «Фильтр по исполнителю»

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
- **Поиск трека**: fuzzy-матч — автоскачивание при совпадении ≥ 80%, иначе — выбор из топ-5
- **Скачивание по ссылке**: вставь ссылку на трек или плейлист SoundCloud / YouTube
- **Батчевое скачивание плейлиста**: авторизация в Яндекс Музыке → выбор плейлиста (включая **любимые треки**) → последовательное скачивание
  - Возобновление с любого трека (fuzzy-поиск внутри плейлиста)
  - Прогресс после каждого трека
  - Кнопка ⛔ Остановить в любой момент
  - Ненайденные треки выводятся в конце, каждый логируется отдельно в БД
  - Ограничение параллельности: `SC_MAX_BATCH_DOWNLOADS`
- Кнопка **"Скачать ещё"** после каждого одиночного скачивания
- **Авто-повтор**: одна автоматическая попытка при сетевой ошибке

**Общее**
- Streamlit-дашборд с живым прогрессом батча и статистикой (на PostgreSQL)
- Redis FSM-хранилище с graceful fallback на MemoryStorage
- Middleware: throttling + защита от нажатия устаревших кнопок

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
     │    ├─ 🔍 Найти трек      → запрос → mp3  [+ Скачать ещё]
     │    ├─ 🔗 По ссылке       → ссылка SC/YT → mp3 или батч
     │    └─ 📥 Плейлист YM     → OAuth → выбор плейлиста → батч mp3
     │
     └─ 🔗 Экспорт плейлиста по ссылке  (новое)
          → Отправить iframe-код или ссылку
          → Бот загружает плейлист
          → Выбор действия:
               ├─ Скачать все            → батч mp3
               ├─ Фильтр по исполнителю → имя → .txt  [+ Скачать отфильтрованное]
               └─ Начать с трека         → название → подтверждение позиции → батч mp3
```

### 🗂️ Структура проекта

```
music-export-bot/
├── bot/
│   ├── handlers.py       # Хендлеры aiogram + FSM (YM + SC + YMShare + фильтр)
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow + YMShareFlow
│   ├── keyboards.py      # Inline-клавиатуры
│   └── middleware.py     # Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (расширяемо)
│   ├── ym_source.py      # Источник YM + батчевый fetch + парсинг share-ссылок
│   └── sc_downloader.py  # yt-dlp обёртка: search() + download() + extract_url_info()
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

Streamlit-дашборд читает из PostgreSQL и включает:
- Статистику экспортов за всё время и по дням
- Метрики SC-поиска и батч-загрузок
- **🔴 Live-прогресс батча**: прогресс-бар, текущий скачиваемый трек, список ненайденных в реальном времени
- Таблицу последних событий с фильтрацией по периоду

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
