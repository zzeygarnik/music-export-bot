"""
Streamlit dashboard for music-export-bot.
Run: streamlit run dashboard.py

Reads from PostgreSQL — events and batch_live tables.
"""
import os
import time
from datetime import datetime, date, timedelta

import psycopg2
import psycopg2.extras
import streamlit as st
import pandas as pd

REFRESH_INTERVAL = 10
_PG_DSN = os.environ.get("POSTGRES_URL", "")

st.set_page_config(page_title="Music Export Bot", page_icon="🎵", layout="wide")
st.title("🎵 Music Export Bot")


@st.cache_resource
def _get_pg_conn():
    return psycopg2.connect(_PG_DSN)


def _query(sql: str, params=None) -> list[dict]:
    try:
        conn = _get_pg_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        st.error(f"DB error: {e}")
        return []


def load_live() -> dict:
    rows = _query("SELECT * FROM batch_live")
    return {r["user_hash"]: r for r in rows}


def load_events() -> pd.DataFrame:
    rows = _query("SELECT * FROM events ORDER BY ts ASC")
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


df = load_events()
live_data = load_live()

# ── Live batch progress ────────────────────────────────────────────────────────
running_batches = {k: v for k, v in live_data.items() if v.get("status") == "running"}
finished_batches = {k: v for k, v in live_data.items() if v.get("status") in ("done", "stopped")}

if running_batches:
    st.subheader("🔴 Активная загрузка")
    for batch in running_batches.values():
        total = batch.get("total", 1) or 1
        current_idx = batch.get("current_idx", 0)
        downloaded = batch.get("downloaded", 0)
        failed = batch.get("failed", [])
        progress = min(current_idx / total, 1.0)

        col_info, col_failed = st.columns([3, 1])
        with col_info:
            st.markdown(f"**{batch.get('user_label', '?')}** · началось в {batch.get('started_at', '—')}")
            st.progress(progress, text=f"⏳ {current_idx}/{total} — **{batch.get('current_track', '—')}**")
            st.caption(f"✅ Скачано: {downloaded}   ❌ Не найдено: {len(failed)}")
        with col_failed:
            if failed:
                with st.expander(f"Не найдено ({len(failed)})", expanded=False):
                    for t in failed:
                        st.write(f"• {t}")
    st.divider()
elif finished_batches:
    last = max(finished_batches.values(), key=lambda x: str(x.get("finished_at", "")))
    status_label = "✅ Завершена" if last.get("status") == "done" else "⛔ Остановлена"
    failed = last.get("failed") or []
    with st.expander(
        f"{status_label} — {last.get('user_label', '?')} · {last.get('finished_at', '—')} "
        f"· скачано {last.get('downloaded', 0)}/{last.get('total', 0)}",
        expanded=False,
    ):
        if failed:
            st.markdown(f"**Не найдено на SC/YouTube ({len(failed)}):**")
            for t in failed:
                st.write(f"• {t}")
        else:
            st.write("Все треки найдены и скачаны!")
    st.divider()

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
week_ago = today - timedelta(days=7)

df_success = df[df["result"] == "success"]

# YM exports
ym_exports = df_success[df_success["action"].str.startswith("export")]
ym_exports_today = ym_exports[ym_exports["ts"].dt.date == today]
ym_exports_week = ym_exports[ym_exports["ts"].dt.date >= week_ago]

# SC / YT
sc_search_all = df[df["action"] == "sc_search"]
sc_search_ok = sc_search_all[sc_search_all["result"] == "success"]
yt_search_all = df[df["action"] == "yt_search"]
yt_search_ok = yt_search_all[yt_search_all["result"] == "success"]
sc_batch_all = df[df["action"] == "sc_batch"]
sc_batch_ok = sc_batch_all[sc_batch_all["result"].isin(["success", "stopped"])]

# Spotify events
sp_playlist = df[df["action"] == "spotify_playlist_load"]
sp_liked = df[df["action"] == "spotify_liked_load"]
sp_exports = df_success[df_success["action"] == "spotify_export"]
sp_loads = pd.concat([sp_playlist, sp_liked])
sp_loads_ok = sp_loads[sp_loads["result"] == "success"]

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_ym, tab_spotify, tab_sc, tab_log = st.tabs(["📊 Яндекс Музыка", "🟢 Spotify", "☁️ SC / YouTube", "📋 Лог событий"])

# ── Tab 1: YM ─────────────────────────────────────────────────────────────────
with tab_ym:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Экспортов всего", len(ym_exports))
    c2.metric("Сегодня", len(ym_exports_today))
    c3.metric("За 7 дней", len(ym_exports_week))
    total_ym_tracks = int(df_success["track_count"].dropna().sum())
    c4.metric("Треков экспортировано", f"{total_ym_tracks:,}")

    st.divider()

    col_chart, col_breakdown = st.columns([2, 1])

    with col_chart:
        st.subheader("Экспорты по дням")
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

    with col_breakdown:
        st.subheader("По типу")
        ym_action_labels = {
            "export_liked": "❤️ Любимые",
            "export_playlist": "📋 Плейлист",
            "export_by_link": "🔗 По ссылке",
        }
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

# ── Tab 2: Spotify ────────────────────────────────────────────────────────────
with tab_spotify:
    sp_c1, sp_c2, sp_c3, sp_c4 = st.columns(4)
    sp_c1.metric("Плейлистов загружено", len(sp_playlist[sp_playlist["result"] == "success"]))
    sp_c2.metric("Лайков загружено", len(sp_liked[sp_liked["result"] == "success"]))
    sp_c3.metric("Экспортов", len(sp_exports))
    sp_c4.metric("Треков экспортировано", int(sp_exports["track_count"].fillna(0).sum()))

    st.divider()

    sp_col1, sp_col2 = st.columns(2)

    with sp_col1:
        st.subheader("Загрузки по дням")
        if not sp_loads_ok.empty:
            by_day = (
                sp_loads_ok.groupby([sp_loads_ok["ts"].dt.date, "action"])
                .size()
                .reset_index(name="count")
                .rename(columns={"ts": "Дата", "action": "Тип"})
            )
            by_day["Дата"] = pd.to_datetime(by_day["Дата"])
            by_day["Тип"] = by_day["Тип"].map({
                "spotify_playlist_load": "Плейлист/Альбом",
                "spotify_liked_load": "Лайки",
            })
            pivot = by_day.pivot(index="Дата", columns="Тип", values="count").fillna(0)
            st.bar_chart(pivot)
        else:
            st.info("Нет данных.")

    with sp_col2:
        st.subheader("Экспорты по формату")
        if not sp_exports.empty:
            fmt_counts = (
                sp_exports["detail"]
                .map({"txt": "📄 TXT", "csv": "📊 CSV"})
                .value_counts()
                .reset_index()
            )
            fmt_counts.columns = ["Формат", "Количество"]
            st.bar_chart(fmt_counts.set_index("Формат"))

            st.subheader("Топ плейлистов")
            top_pl = (
                sp_playlist[sp_playlist["result"] == "success"]["detail"]
                .dropna()
                .value_counts()
                .head(10)
                .reset_index()
            )
            top_pl.columns = ["Плейлист / Альбом", "Загрузок"]
            if not top_pl.empty:
                st.dataframe(top_pl, use_container_width=True, hide_index=True)
        else:
            st.info("Нет данных.")


# ── Tab 3: SC / YouTube ───────────────────────────────────────────────────────
with tab_sc:
    sc_tracks_downloaded = int(sc_search_ok["track_count"].fillna(1).sum())
    yt_tracks_downloaded = int(yt_search_ok["track_count"].fillna(1).sum())
    sc_batch_tracks = int(sc_batch_ok["track_count"].fillna(0).sum())

    sc_c1, sc_c2, sc_c3, sc_c4, sc_c5 = st.columns(5)
    sc_c1.metric("SC поисков", len(sc_search_all))
    sc_c2.metric("YT поисков", len(yt_search_all))
    sc_c3.metric("Батч-сессий", len(sc_batch_all))
    sc_c4.metric("Треков (батч)", sc_batch_tracks)
    sc_c5.metric("Треков (поиск)", sc_tracks_downloaded + yt_tracks_downloaded)

    st.divider()

    sc_col1, sc_col2 = st.columns(2)

    with sc_col1:
        st.subheader("Поиск SC / YT")

        search_combined = pd.concat([
            sc_search_all.assign(platform="SoundCloud"),
            yt_search_all.assign(platform="YouTube"),
        ])
        if not search_combined.empty:
            by_day = (
                search_combined.groupby([search_combined["ts"].dt.date, "platform"])
                .size()
                .reset_index(name="Поисков")
                .rename(columns={"ts": "Дата"})
            )
            by_day["Дата"] = pd.to_datetime(by_day["Дата"])
            pivot = by_day.pivot(index="Дата", columns="platform", values="Поисков").fillna(0)
            st.bar_chart(pivot)

        top_found = (
            pd.concat([sc_search_ok, yt_search_ok])["detail"]
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
            sc_batch_ok_copy = sc_batch_ok.copy()
            sc_batch_ok_copy["not_found_count"] = (
                sc_batch_ok_copy["detail"]
                .str.extract(r"not_found:(\d+)")
                .astype(float)
                .fillna(0)
            )
            avg_not_found = sc_batch_ok_copy["not_found_count"].mean()
            st.caption(f"Среднее скачано за сессию: **{avg_downloaded:.1f}** треков")
            st.caption(f"Среднее не найдено за сессию: **{avg_not_found:.1f}** треков")

# ── Tab 3: Лог событий ────────────────────────────────────────────────────────
with tab_log:
    all_action_labels = {
        "export_liked": "❤️ YM: Любимые треки",
        "export_playlist": "📋 YM: Плейлист",
        "export_by_link": "🔗 YM: По ссылке",
        "auth_ok": "🔑 YM: Авторизация (успех)",
        "auth_fail": "❌ YM: Авторизация (ошибка)",
        "export_error": "⚠️ YM: Ошибка экспорта",
        "yms_load": "🔗 YM: Плейлист/Альбом по ссылке",
        "sc_search": "🔍 SC: Поиск",
        "yt_search": "🔍 YT: Поиск",
        "sc_batch": "📥 Батч-загрузка (SC/YT)",
        "sc_track_fail": "❌ Трек не найден (батч)",
        "spotify_playlist_load": "🟢 Spotify: Плейлист/Альбом",
        "spotify_liked_load": "🟢 Spotify: Лайки",
        "spotify_export": "🟢 Spotify: Экспорт",
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
