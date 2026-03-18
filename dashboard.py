"""
Streamlit dashboard for music-export-bot.
Run: streamlit run dashboard.py

Reads logs/events.jsonl — the file accumulates across all bot restarts,
so all stats here are all-time totals.
"""
import json
import time
from pathlib import Path
from datetime import datetime, date, timedelta

import streamlit as st
import pandas as pd

LOG_FILE = Path("logs/events.jsonl")
REFRESH_INTERVAL = 10

st.set_page_config(page_title="Music Export Bot", page_icon="🎵", layout="wide")
st.title("🎵 Music Export Bot — Dashboard")
st.caption("Статистика накапливается за всё время работы бота (лог-файл сохраняется между перезапусками)")


def load_events() -> pd.DataFrame:
    if not LOG_FILE.exists():
        return pd.DataFrame()
    rows = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


df = load_events()

# ── Auto-refresh toggle ───────────────────────────────────────────────────────
col_title, col_refresh = st.columns([5, 1])
with col_refresh:
    auto = st.toggle("Auto-refresh", value=True)
st.caption(f"Обновлено: {datetime.now().strftime('%H:%M:%S')}  |  "
           f"Записей в логе: {len(df) if not df.empty else 0}")

if df.empty:
    st.info("Пока нет событий. Запусти бота и попробуй что-нибудь экспортировать.")
    if auto:
        time.sleep(REFRESH_INTERVAL)
        st.rerun()
    st.stop()

today = date.today()
yesterday = today - timedelta(days=1)
week_ago = today - timedelta(days=7)

df_success = df[df["result"] == "success"]
exports = df_success[df_success["action"].str.startswith("export")]
exports_today = exports[exports["ts"].dt.date == today]
exports_week = exports[exports["ts"].dt.date >= week_ago]

# ── All-time stats ────────────────────────────────────────────────────────────
st.subheader("📊 Всё время")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Экспортов всего", len(exports))
c2.metric("Сегодня", len(exports_today))
c3.metric("За 7 дней", len(exports_week))
c4.metric("Уникальных юзеров", df["user_hash"].nunique())
total_tracks = int(df_success["track_count"].dropna().sum())
c5.metric("Треков экспортировано", f"{total_tracks:,}")

st.divider()

# ── Activity chart — exports per day ─────────────────────────────────────────
st.subheader("📈 Экспорты по дням")
if not exports.empty:
    by_day = (
        exports.groupby(exports["ts"].dt.date)
        .size()
        .reset_index(name="count")
        .rename(columns={"ts": "Дата", "count": "Экспортов"})
    )
    by_day["Дата"] = pd.to_datetime(by_day["Дата"])
    st.bar_chart(by_day.set_index("Дата")["Экспортов"])
else:
    st.info("Нет данных для графика.")

st.divider()

# ── By action breakdown ───────────────────────────────────────────────────────
col_left, col_right = st.columns(2)

action_labels = {
    "export_liked": "❤️ Любимые треки",
    "export_playlist": "📋 Плейлист",
    "export_by_link": "🔗 По ссылке",
    "auth_ok": "🔑 Авторизация (успех)",
    "auth_fail": "❌ Авторизация (ошибка)",
    "export_error": "⚠️ Ошибка экспорта",
}

with col_left:
    st.subheader("По типу экспорта")
    action_counts = (
        exports["action"]
        .map(lambda x: action_labels.get(x, x))
        .value_counts()
        .reset_index()
    )
    action_counts.columns = ["Тип", "Количество"]
    if not action_counts.empty:
        st.bar_chart(action_counts.set_index("Тип"))
    else:
        st.info("Нет данных.")

with col_right:
    st.subheader("Успех vs Ошибки")
    result_counts = df["result"].value_counts().rename({"success": "✅ Успех", "error": "❌ Ошибка"})
    st.bar_chart(result_counts)

st.divider()

# ── Recent events table ───────────────────────────────────────────────────────
st.subheader("📋 Последние события")

period = st.radio("Период", ["Сегодня", "7 дней", "Всё время"], horizontal=True, index=2)
if period == "Сегодня":
    df_view = df[df["ts"].dt.date == today]
elif period == "7 дней":
    df_view = df[df["ts"].dt.date >= week_ago]
else:
    df_view = df

df_view = df_view.sort_values("ts", ascending=False).head(200).copy()
df_view["ts"] = df_view["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
df_view["result"] = df_view["result"].map({"success": "✅", "error": "❌"})
df_view["action"] = df_view["action"].map(lambda x: action_labels.get(x, x))
df_view["user"] = df_view.apply(
    lambda r: f"@{r['username']}" if r.get("username") else f"#{r['user_hash']}", axis=1
)
df_view["track_count"] = (
    df_view["track_count"].fillna("—").astype(str).str.replace(r"\.0$", "", regex=True)
)

st.dataframe(
    df_view[["ts", "user", "action", "result", "track_count", "detail"]].rename(columns={
        "ts": "Время",
        "user": "Пользователь",
        "action": "Действие",
        "result": "",
        "track_count": "Треков",
        "detail": "Детали",
    }),
    use_container_width=True,
    hide_index=True,
)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto:
    time.sleep(REFRESH_INTERVAL)
    st.rerun()
