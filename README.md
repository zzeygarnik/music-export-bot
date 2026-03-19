# 🎵 Music Export Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.13-2CA5E0?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Container-2496ED?logo=docker&logoColor=white)
![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Optional-DC382D?logo=redis&logoColor=white)

Telegram bot with two modes: **export your Yandex Music library to `.txt`** and **download tracks from SoundCloud as `.mp3`**. Self-hosted, containerized, ready for TrueNAS or any Linux server.

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
- After playlist export — inline button to download the same tracks from SoundCloud

**SoundCloud → .mp3 download**
- **Single track search**: fuzzy match (rapidfuzz) — auto-download if confidence ≥ 80%, otherwise show top-5 for manual selection
- **Batch playlist download**: authorize with Yandex Music → pick playlist → download all tracks sequentially
  - Resume from any track (fuzzy search inside playlist)
  - Progress updates after each track
  - ⛔ Stop button at any time
  - Tracks not found on SoundCloud are collected and shown at the end
  - Concurrency limit: max simultaneous batch downloads across all users (configurable via `SC_MAX_BATCH_DOWNLOADS`)

**General**
- Streamlit dashboard with usage statistics
- Redis FSM storage with graceful fallback to MemoryStorage
- Throttling + stale-button guard middleware

### 🔄 User Flow

```
/start
  → What do you want to do?
     ├─ 📋 Export to .txt  (Yandex Music)
     │    → Choose retention  (⚡ Session | 🔒 Single export)
     │    → Enter OAuth token
     │    → Choose export type:
     │       ├─ Liked tracks   → .txt file  [+ "📥 Download from SoundCloud" button]
     │       ├─ My playlists   → pick → .txt file  [+ SC button]
     │       └─ By link        → paste link → .txt file  [+ SC button]
     │
     └─ 🎵 Download MP3  (SoundCloud)
          ├─ 🔍 Find track     → type query → mp3
          └─ 📥 Download playlist → YM OAuth → pick playlist → mp3 × N
```

### 🗂️ Project Structure

```
music-export-bot/
├── bot/
│   ├── handlers.py       # aiogram handlers + FSM logic (YM + SC)
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow
│   ├── keyboards.py      # Inline keyboards (YM + SC)
│   └── middleware.py     # Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (extensible)
│   ├── ym_source.py      # Yandex Music source + batch fetch
│   └── sc_downloader.py  # yt-dlp wrapper: search() + download()
├── utils/
│   ├── export.py         # Async .txt writer
│   └── event_log.py      # Event logging → logs/events.jsonl
├── dashboard.py          # Streamlit analytics dashboard
├── main.py               # Entry point, Redis/MemoryStorage init
├── config.py             # Settings via pydantic-settings + .env
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
REDIS_URL=redis://localhost:6379/0   # optional, MemoryStorage used if unavailable
SC_PROXY=http://user:pass@host:port  # required on servers where SoundCloud is blocked
SC_MAX_BATCH_DOWNLOADS=2             # max concurrent SC batch downloads across all users (default: 2)
```

> **Note on `SC_PROXY`:** SoundCloud may be blocked by your ISP or country-level filtering (DPI). Set this to an HTTP or SOCKS5 proxy outside the restricted region. Format: `http://login:password@ip:port` or `socks5://login:password@ip:port`. Leave empty if SoundCloud is accessible directly.

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
cp .env.example .env  # fill in BOT_TOKEN
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
| Env: `REDIS_URL` | `redis://your-nas-ip:6379/0` |
| Env: `SC_PROXY` | `http://login:password@ip:port` |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Mounting the source directory as a volume means **code updates apply on container restart** — no rebuild needed (unless `requirements.txt` or `Dockerfile.prod` changes).

### 📊 Dashboard

**Local:**
```bash
streamlit run dashboard.py
# Open http://localhost:8501
```

**TrueNAS Custom App — unified container (bot + dashboard):**

`start_all.sh` starts the Streamlit dashboard in the background and the bot in the foreground — both in the same container.

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
- После экспорта плейлиста — inline-кнопка «📥 Скачать с SoundCloud»

**SoundCloud → скачивание .mp3**
- **Поиск трека**: fuzzy-матч (rapidfuzz) — автоскачивание при совпадении ≥ 80%, иначе — выбор из топ-5
- **Батчевое скачивание плейлиста**: авторизация в Яндекс Музыке → выбор плейлиста → последовательное скачивание треков
  - Возобновление с любого трека (fuzzy-поиск внутри плейлиста)
  - Прогресс после каждого трека
  - Кнопка ⛔ Остановить в любой момент
  - Ненайденные треки собираются и выводятся в конце
  - Ограничение параллельности: максимум одновременных батч-загрузок на всех пользователей задаётся через `SC_MAX_BATCH_DOWNLOADS`

**Общее**
- Streamlit-дашборд со статистикой использования
- Redis FSM-хранилище с graceful fallback на MemoryStorage
- Middleware: throttling + защита от нажатия устаревших кнопок

### 🔄 Флоу пользователя

```
/start
  → Что хочешь сделать?
     ├─ 📋 Экспорт в .txt  (Яндекс Музыка)
     │    → Выбор retention  (⚡ На весь сеанс | 🔒 Только один экспорт)
     │    → Ввод OAuth-токена
     │    → Выбор типа экспорта:
     │       ├─ Любимые треки  → .txt файл  [+ кнопка «📥 Скачать с SoundCloud»]
     │       ├─ Мои плейлисты  → выбор → .txt файл  [+ SC кнопка]
     │       └─ По ссылке      → вставить ссылку → .txt файл  [+ SC кнопка]
     │
     └─ 🎵 Скачать MP3  (SoundCloud)
          ├─ 🔍 Найти трек     → ввести запрос → mp3
          └─ 📥 Скачать плейлист → OAuth YM → выбор плейлиста → mp3 × N
```

### 🗂️ Структура проекта

```
music-export-bot/
├── bot/
│   ├── handlers.py       # Хендлеры aiogram + FSM-логика (YM + SC)
│   ├── states.py         # ExportFlow + SCSearchFlow + SCBatchFlow
│   ├── keyboards.py      # Inline-клавиатуры (YM + SC)
│   └── middleware.py     # Throttling + StaleButton + CallbackAnswer
├── core/
│   ├── base_source.py    # AbstractMusicSource (расширяемо)
│   ├── ym_source.py      # Источник Яндекс Музыки + батчевый fetch
│   └── sc_downloader.py  # yt-dlp обёртка: search() + download()
├── utils/
│   ├── export.py         # Асинхронная запись .txt
│   └── event_log.py      # Логирование → logs/events.jsonl
├── dashboard.py          # Streamlit-дашборд
├── main.py               # Точка входа, инициализация Redis/MemoryStorage
├── config.py             # Настройки через pydantic-settings + .env
├── Dockerfile            # Dev-образ
├── Dockerfile.prod       # Prod-образ (только зависимости, код через volume)
├── docker-compose.yml    # Dev-стек (бот + Redis + дашборд)
├── docker-compose.prod.yml
├── start_all.sh          # TrueNAS: бот + дашборд в одном контейнере
└── start_dashboard.sh    # TrueNAS: только дашборд (отдельный контейнер)
```

### ⚙️ Конфигурация

Создай `.env` файл в корне проекта:

```env
BOT_TOKEN=токен_твоего_telegram_бота
REDIS_URL=redis://localhost:6379/0      # опционально, без Redis — MemoryStorage
SC_PROXY=http://login:password@ip:port  # нужен если SoundCloud заблокирован у провайдера
SC_MAX_BATCH_DOWNLOADS=2                # макс. одновременных батч-загрузок SC (по умолчанию: 2)
```

> **Про `SC_PROXY`:** SoundCloud может блокироваться провайдером через DPI/ТСПУ. Укажи HTTP или SOCKS5 прокси вне заблокированного региона. Формат: `http://login:password@ip:port` или `socks5://login:password@ip:port`. Если SoundCloud доступен напрямую — оставь пустым.

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
cp .env.example .env  # заполни BOT_TOKEN
docker compose up --build
# Бот + Redis + Streamlit дашборд на :8501
```

**TrueNAS Custom App (продакшн):**

> Если Docker Hub заблокирован — добавь зеркало в `/etc/docker/daemon.json`:
> `"registry-mirrors": ["https://mirror.gcr.io"]`

```bash
# Собрать образ на TrueNAS через SSH:
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
| Env: `REDIS_URL` | `redis://ip-nas:6379/0` |
| Env: `SC_PROXY` | `http://login:password@ip:port` |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Монтирование папки с кодом как volume — **обновления кода применяются при рестарте контейнера**, пересборка образа не нужна (если не менялись `requirements.txt` или `Dockerfile.prod`).

### 📊 Дашборд

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
| Дашборд | [Streamlit](https://streamlit.io/) + pandas |
| Контейнеризация | Docker |
| Хостинг | TrueNAS Scale / любой Linux-сервер |

### 🔒 Безопасность

- OAuth-токены **никогда** не пишутся в файлы или БД — только RAM/Redis
- Режим `retention=single` удаляет токен сразу после первого экспорта
- ID пользователей в логах хранятся как усечённые SHA-256 хеши — без PII
- Никогда не коммить `.env`
