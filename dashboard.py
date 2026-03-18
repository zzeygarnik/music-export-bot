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

# YM exports
ym_exports = df_success[df_success["action"].str.startswith("export")]
ym_exports_today = ym_exports[ym_exports["ts"].dt.date == today]
ym_exports_week = ym_exports[ym_exports["ts"].dt.date >= week_ago]

# SC events
sc_df = df[df["action"].str.startswith("sc_")]
sc_search_all = df[df["action"] == "sc_search"]
sc_search_ok = sc_search_all[sc_search_all["result"] == "success"]
sc_batch_all = df[df["action"] == "sc_batch"]
sc_batch_ok = sc_batch_all[sc_batch_all["result"].isin(["success", "stopped"])]

# ── All-time stats ────────────────────────────────────────────────────────────
st.subheader("📊 Всё время")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("YM экспортов всего", len(ym_exports))
c2.metric("YM сегодня", len(ym_exports_today))
c3.metric("YM за 7 дней", len(ym_exports_week))
c4.metric("Уникальных юзеров", df["user_hash"].nunique())
total_ym_tracks = int(df_success["track_count"].dropna().sum())
c5.metric("YM треков экспортировано", f"{total_ym_tracks:,}")

st.divider()

# ── Activity chart — YM exports per day ──────────────────────────────────────
st.subheader("📈 YM экспорты по дням")
if not ym_exports.empty:
    by_day = (
        ym_exports.groupby(ym_exports["ts"].dt.date)
        .size()
        .reset_index(name="count")
        .rename(columns={"ts": "Дата", "count": "Экспортов"})
    )
    by_day["Дата"] = pd.to_datetime(by_day["Дата"])
    st.bar_chart(by_day.set_index("Дата")["Экспортов"])
else:
    st.info("Нет данных для графика.")

st.divider()

# ── By action breakdown (YM) ──────────────────────────────────────────────────
col_left, col_right = st.columns(2)

ym_action_labels = {
    "export_liked": "❤️ Любимые треки",
    "export_playlist": "📋 Плейлист",
    "export_by_link": "🔗 По ссылке",
    "auth_ok": "🔑 Авторизация (успех)",
    "auth_fail": "❌ Авторизация (ошибка)",
    "export_error": "⚠️ Ошибка экспорта",
}

with col_left:
    st.subheader("По типу YM экспорта")
    action_counts = (
        ym_exports["action"]
        .map(lambda x: ym_action_labels.get(x, x))
        .value_counts()
        .reset_index()
    )
    action_counts.columns = ["Тип", "Количество"]
    if not action_counts.empty:
        st.bar_chart(action_counts.set_index("Тип"))
    else:
        st.info("Нет данных.")

with col_right:
    st.subheader("Успех vs Ошибки (всё)")
    result_counts = df["result"].value_counts().rename(
        {"success": "✅ Успех", "error": "❌ Ошибка", "stopped": "⛔ Остановлено"}
    )
    st.bar_chart(result_counts)

st.divider()

# ── SoundCloud section ────────────────────────────────────────────────────────
st.subheader("☁️ SoundCloud")

if sc_df.empty:
    st.info("Нет SC-событий пока. Попробуй поиск трека или скачивание плейлиста через SoundCloud.")
else:
    # SC top metrics
    sc_tracks_downloaded = int(sc_search_ok["track_count"].fillna(1).sum())
    sc_batch_tracks = int(sc_batch_ok["track_count"].fillna(0).sum())
    sc_total_tracks = sc_tracks_downloaded + sc_batch_tracks

    sc_search_rate = (
        round(len(sc_search_ok) / len(sc_search_all) * 100)
        if len(sc_search_all) > 0 else 0
    )

    sc_c1, sc_c2, sc_c3, sc_c4, sc_c5 = st.columns(5)
    sc_c1.metric("Поисков треков", len(sc_search_all))
    sc_c2.metric("Успешных поисков", f"{len(sc_search_ok)} ({sc_search_rate}%)")
    sc_c3.metric("Батч-сессий", len(sc_batch_all))
    sc_c4.metric("Треков скачано (батч)", int(sc_batch_ok["track_count"].fillna(0).sum()))
    sc_c5.metric("Треков всего (SC)", sc_total_tracks)

    # SC downloads by day
    st.subheader("SC активность по дням")
    sc_by_day = (
        sc_df.groupby(sc_df["ts"].dt.date)
        .size()
        .reset_index(name="Событий")
        .rename(columns={"ts": "Дата"})
    )
    sc_by_day["Дата"] = pd.to_datetime(sc_by_day["Дата"])
    st.bar_chart(sc_by_day.set_index("Дата")["Событий"])

    sc_col1, sc_col2 = st.columns(2)

    with sc_col1:
        st.subheader("Поиск треков")
        search_result_counts = (
            sc_search_all["result"]
            .map({"success": "✅ Успех", "error": "❌ Ошибка"})
            .value_counts()
            .reset_index()
        )
        search_result_counts.columns = ["Результат", "Количество"]
        if not search_result_counts.empty:
            st.bar_chart(search_result_counts.set_index("Результат"))

        # Top searched tracks
        top_found = (
            sc_search_ok["detail"]
            .dropna()
            .value_counts()
            .head(10)
            .reset_index()
        )
        top_found.columns = ["Трек", "Скачиваний"]
        if not top_found.empty:
            st.caption("Топ треков")
            st.dataframe(top_found, use_container_width=True, hide_index=True)

    with sc_col2:
        st.subheader("Батчевое скачивание")
        batch_result_counts = (
            sc_batch_all["result"]
            .map({"success": "✅ Завершён", "stopped": "⛔ Остановлен", "error": "❌ Ошибка"})
            .value_counts()
            .reset_index()
        )
        batch_result_counts.columns = ["Результат", "Количество"]
        if not batch_result_counts.empty:
            st.bar_chart(batch_result_counts.set_index("Результат"))

        if not sc_batch_ok.empty:
            avg_downloaded = sc_batch_ok["track_count"].fillna(0).mean()
            # Parse not_found count from detail field "not_found:N"
            sc_batch_ok = sc_batch_ok.copy()
            sc_batch_ok["not_found_count"] = (
                sc_batch_ok["detail"]
                .str.extract(r"not_found:(\d+)")
                .astype(float)
                .fillna(0)
            )
            avg_not_found = sc_batch_ok["not_found_count"].mean()
            st.caption(f"Среднее скачано за сессию: **{avg_downloaded:.1f}** треков")
            st.caption(f"Среднее не найдено за сессию: **{avg_not_found:.1f}** треков")

st.divider()

# ── Recent events table ───────────────────────────────────────────────────────
st.subheader("📋 Последние события")

all_action_labels = {
    **ym_action_labels,
    "sc_search": "🔍 SC поиск",
    "sc_batch": "📥 SC батч",
}

period = st.radio("Период", ["Сегодня", "7 дней", "Всё время"], horizontal=True, index=2)
if period == "Сегодня":
    df_view = df[df["ts"].dt.date == today]
elif period == "7 дней":
    df_view = df[df["ts"].dt.date >= week_ago]
else:
    df_view = df

df_view = df_view.sort_values("ts", ascending=False).head(200).copy()
df_view["ts"] = df_view["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
df_view["result"] = df_view["result"].map({"success": "✅", "error": "❌", "stopped": "⛔"})
df_view["action"] = df_view["action"].map(lambda x: all_action_labels.get(x, x))
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
