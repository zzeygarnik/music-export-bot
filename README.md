# 🎵 Yandex Music Export Bot

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.13-2CA5E0?logo=telegram&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Container-2496ED?logo=docker&logoColor=white)
![Streamlit](https://img.shields.io/badge/Dashboard-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Optional-DC382D?logo=redis&logoColor=white)

Telegram bot that exports your Yandex Music library — liked tracks, playlists, or shared links — into a clean `.txt` file. Self-hosted, containerized, ready for TrueNAS or any Linux server.

[ 🇬🇧 English](#-english) | [ 🇷🇺 Русский](#-русский)

---

## 🇬🇧 English

### ✨ Features

- Export **liked tracks** in one tap
- Export **any playlist** from your library
- Export by **shared `lk.` link** (e.g. `music.yandex.ru/playlists/lk.UUID`)
- OAuth token — stored in session RAM only, never written to disk
- **Session** or **single-use** token retention modes
- Streamlit dashboard with usage statistics
- Redis FSM storage with graceful fallback to MemoryStorage
- Throttling middleware — no spam, no double exports

### 🔄 User Flow

```
/start
  → Choose service       (Yandex Music)
  → Choose retention     (⚡ Session | 🔒 Single export)
  → Enter OAuth token
  → Choose export type:
     ├─ Liked tracks      → .txt file
     ├─ My playlists      → pick playlist → .txt file
     └─ By link           → paste link → .txt file
```

### 🗂️ Project Structure

```
music-export-bot/
├── bot/
│   ├── handlers.py       # aiogram handlers + FSM logic
│   ├── states.py         # ExportFlow FSM states
│   ├── keyboards.py      # Inline keyboards
│   └── middleware.py     # ThrottlingMiddleware + CallbackAnswerMiddleware
├── core/
│   ├── base_source.py    # AbstractMusicSource (extensible to Spotify etc.)
│   └── ym_source.py      # Yandex Music source + batch fetch
├── utils/
│   ├── export.py         # Async .txt writer
│   └── event_log.py      # Event logging → logs/events.jsonl
├── dashboard.py          # Streamlit analytics dashboard
├── main.py               # Entry point, Redis/MemoryStorage init
├── config.py             # Settings via pydantic-settings + .env
├── Dockerfile            # Development image
├── Dockerfile.prod       # Production image (deps only, code via volume)
├── docker-compose.yml    # Dev stack (bot + Redis + dashboard)
└── docker-compose.prod.yml
```

### ⚙️ Configuration

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_telegram_bot_token
REDIS_URL=redis://localhost:6379/0   # optional, MemoryStorage used if unavailable
```

**Getting a Yandex Music OAuth token** (the bot explains this to users automatically):

1. Open this URL in your browser:
   ```
   https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
   ```
2. Log in with your Yandex account
3. Copy the `access_token` value from the redirect URL

### 🚀 Deployment

**Docker Compose (recommended for local/dev):**

```bash
cp .env.example .env  # fill in BOT_TOKEN
docker compose up --build
# Bot + Redis + Streamlit dashboard on :8501
```

**TrueNAS Custom App (production):**

Since Docker Hub may be blocked, build the image locally or on the server using a mirror:

```bash
# On TrueNAS — add mirror to /etc/docker/daemon.json first:
# "registry-mirrors": ["https://mirror.gcr.io"]

sudo docker build -f Dockerfile.prod -t music-export-bot:latest .
```

Then create a Custom App in TrueNAS UI:

| Field | Value |
|---|---|
| Image | `music-export-bot` |
| Tag | `latest` |
| Pull Policy | `Never` |
| Restart Policy | `Unless Stopped` |
| Env: `BOT_TOKEN` | your token |
| Env: `REDIS_URL` | `redis://your-nas-ip:6379/0` |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Mounting the source directory as a volume means **code updates apply on container restart** — no rebuild needed.

### 📊 Dashboard

**Local:**
```bash
streamlit run dashboard.py
# Open http://localhost:8501
```

**TrueNAS Custom App (recommended — reads the same logs as the bot):**

First, create a helper script `start_dashboard.sh` in the project root:
```sh
#!/bin/sh
exec streamlit run /app/dashboard.py --server.port=8501 --server.address=0.0.0.0
```

Make it executable on TrueNAS:
```bash
sudo chmod +x /mnt/.../music-export-bot/start_dashboard.sh
```

Then create a second Custom App in TrueNAS UI:

| Field | Value |
|---|---|
| Image | `music-export-bot` |
| Tag | `latest` |
| Pull Policy | `Never` |
| Entrypoint | `/bin/sh` |
| Command | `/app/start_dashboard.sh` |
| Host Port → Container Port | `8501 → 8501 TCP` |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Open at `http://your-nas-ip:8501`

Shows per-user export history, track counts, and action stats from `logs/events.jsonl`.

### 📦 Tech Stack

| Component | Technology |
|---|---|
| Bot framework | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| Yandex Music API | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
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

- Экспорт **лайкнутых треков** в один клик
- Экспорт **любого плейлиста** из библиотеки
- Экспорт по **`lk.`-ссылке** (например `music.yandex.ru/playlists/lk.UUID`)
- OAuth-токен хранится только в памяти сессии — никогда не пишется на диск
- Два режима хранения токена: **на весь сеанс** или **только один экспорт**
- Streamlit-дашборд со статистикой использования
- Redis FSM-хранилище с graceful fallback на MemoryStorage
- Middleware для защиты от спама и двойного запуска экспорта

### 🔄 Флоу пользователя

```
/start
  → Выбор сервиса        (Яндекс Музыка)
  → Выбор retention      (⚡ На весь сеанс | 🔒 Только один экспорт)
  → Ввод OAuth-токена
  → Выбор типа экспорта:
     ├─ Любимые треки     → .txt файл
     ├─ Мои плейлисты     → выбор плейлиста → .txt файл
     └─ По ссылке         → вставить ссылку → .txt файл
```

### 🗂️ Структура проекта

```
music-export-bot/
├── bot/
│   ├── handlers.py       # Хендлеры aiogram + FSM-логика
│   ├── states.py         # Состояния ExportFlow FSM
│   ├── keyboards.py      # Inline-клавиатуры
│   └── middleware.py     # ThrottlingMiddleware + CallbackAnswerMiddleware
├── core/
│   ├── base_source.py    # AbstractMusicSource (расширяемо под Spotify и др.)
│   └── ym_source.py      # Источник Яндекс Музыки + батчевый fetch
├── utils/
│   ├── export.py         # Асинхронная запись .txt
│   └── event_log.py      # Логирование событий → logs/events.jsonl
├── dashboard.py          # Streamlit-дашборд
├── main.py               # Точка входа, инициализация Redis/MemoryStorage
├── config.py             # Настройки через pydantic-settings + .env
├── Dockerfile            # Dev-образ
├── Dockerfile.prod       # Prod-образ (только зависимости, код через volume)
├── docker-compose.yml    # Dev-стек (бот + Redis + дашборд)
└── docker-compose.prod.yml
```

### ⚙️ Конфигурация

Создай `.env` файл в корне проекта:

```env
BOT_TOKEN=токен_твоего_telegram_бота
REDIS_URL=redis://localhost:6379/0   # опционально, без Redis — MemoryStorage
```

**Как получить OAuth-токен Яндекс Музыки** (бот объясняет это пользователям автоматически):

1. Открой в браузере:
   ```
   https://oauth.yandex.ru/authorize?response_type=token&client_id=23cabbbdc6cd418abb4b39c32c41195d
   ```
2. Войди в аккаунт Яндекса
3. Скопируй значение `access_token` из URL редиректа

### 🚀 Развёртывание

**Docker Compose (рекомендуется для разработки):**

```bash
cp .env.example .env  # заполни BOT_TOKEN
docker compose up --build
# Бот + Redis + Streamlit дашборд на :8501
```

**TrueNAS Custom App (продакшн):**

Если Docker Hub заблокирован — добавь зеркало в `/etc/docker/daemon.json`:
```json
"registry-mirrors": ["https://mirror.gcr.io"]
```

Затем собери образ прямо на сервере:
```bash
sudo docker build -f Dockerfile.prod -t music-export-bot:latest .
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
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Монтирование папки с кодом как volume означает — **обновления кода применяются при рестарте контейнера**, пересборка образа не нужна.

### 📊 Дашборд

**Локально:**
```bash
streamlit run dashboard.py
# Открыть http://localhost:8501
```

**TrueNAS Custom App (рекомендуется — читает те же логи что и бот):**

Создай вспомогательный скрипт `start_dashboard.sh` в корне проекта:
```sh
#!/bin/sh
exec streamlit run /app/dashboard.py --server.port=8501 --server.address=0.0.0.0
```

Дай права на выполнение на TrueNAS:
```bash
sudo chmod +x /mnt/.../music-export-bot/start_dashboard.sh
```

Создай второй Custom App в TrueNAS UI:

| Поле | Значение |
|---|---|
| Image | `music-export-bot` |
| Tag | `latest` |
| Pull Policy | `Never` |
| Entrypoint | `/bin/sh` |
| Command | `/app/start_dashboard.sh` |
| Host Port → Container Port | `8501 → 8501 TCP` |
| Host Path | `/mnt/.../music-export-bot` → `/app` |

Открыть по адресу `http://ip-nas:8501`

Показывает историю экспортов по пользователям, количество треков и статистику действий из `logs/events.jsonl`.

### 📦 Технологии

| Компонент | Технология |
|---|---|
| Фреймворк бота | [aiogram 3](https://docs.aiogram.dev/) (async FSM) |
| API Яндекс Музыки | [yandex-music](https://github.com/MarshalX/yandex-music-api) 2.x |
| FSM-хранилище | Redis / MemoryStorage fallback |
| Дашборд | [Streamlit](https://streamlit.io/) + pandas |
| Контейнеризация | Docker |
| Хостинг | TrueNAS Scale / любой Linux-сервер |

### 🔒 Безопасность

- OAuth-токены **никогда** не пишутся в файлы или БД — только RAM/Redis
- Режим `retention=single` удаляет токен сразу после первого экспорта
- ID пользователей в логах хранятся как усечённые SHA-256 хеши — без PII
- Никогда не коммить `.env`
