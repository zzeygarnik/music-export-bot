from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_YM_OAUTH_URL = (
    "https://oauth.yandex.ru/authorize"
    "?response_type=token"
    "&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)


def service_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Экспорт треков в .txt / .csv",
            callback_data="service:export_pick",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="Скачать MP3",
            callback_data="service:soundcloud",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Плейлист / Альбом по ссылке",
            callback_data="service:share_pick",
            icon_custom_emoji_id="6042011682497106307",
        )],
        [InlineKeyboardButton(
            text="Исправить теги трека",
            callback_data="service:audio_tag",
        )],
    ])


def export_source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Яндекс Музыка", callback_data="service:yandex")],
        [InlineKeyboardButton(text="🟢 Spotify", callback_data="service:spotify")],
        [InlineKeyboardButton(text="← Назад", callback_data="service:back_to_main")],
    ])


def share_source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 ЯМ — по ссылке или embed", callback_data="service:share")],
        [InlineKeyboardButton(text="🎵 ЯМ — мои плейлисты", callback_data="service:ym_playlists")],
        [InlineKeyboardButton(text="🟢 Spotify", callback_data="service:spotify")],
        [InlineKeyboardButton(text="☁️ SoundCloud / YouTube — по ссылке", callback_data="service:sc_url_playlist")],
        [InlineKeyboardButton(text="🎬 YouTube — плейлист по ссылке", callback_data="service:yt_playlist")],
        [InlineKeyboardButton(text="← Назад", callback_data="service:back_to_main")],
    ])


def retention_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="На весь сеанс",
            callback_data="retention:session",
            icon_custom_emoji_id="5983150113483134607",
        )],
        [InlineKeyboardButton(
            text="Только один экспорт",
            callback_data="retention:single",
            icon_custom_emoji_id="6037243349675544634",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="retention:back")],
    ])


def token_guide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Войти через Яндекс",
            url=_YM_OAUTH_URL,
            icon_custom_emoji_id="5963103826075456248",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="retention:back")],
    ])


def export_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Любимые треки",
            callback_data="export:liked",
            icon_custom_emoji_id="6041731551845159060",
        )],
        [InlineKeyboardButton(
            text="Мои плейлисты",
            callback_data="export:playlists",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="Плейлист по ссылке",
            callback_data="export:by_link",
            icon_custom_emoji_id="6042011682497106307",
        )],
        [InlineKeyboardButton(
            text="Экспорт в CSV",
            callback_data="export:set_fmt_csv",
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="export:back_to_source")],
    ])


def export_type_csv_keyboard() -> InlineKeyboardMarkup:
    """Same source options but format is CSV — shown after user picks CSV mode."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Любимые треки",
            callback_data="export:liked",
            icon_custom_emoji_id="6041731551845159060",
        )],
        [InlineKeyboardButton(
            text="Мои плейлисты",
            callback_data="export:playlists",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="Плейлист по ссылке",
            callback_data="export:by_link",
            icon_custom_emoji_id="6042011682497106307",
        )],
        [InlineKeyboardButton(
            text="← Назад (TXT)",
            callback_data="export:set_fmt_txt",
        )],
    ])


def playlists_keyboard(playlists: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=p["title"], callback_data=f"playlist:{p['kind']}")]
        for p in playlists
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="export:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="action:cancel")]
    ])


# ── SoundCloud keyboards ───────────────────────────────────────────────────────

def sc_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Найти на SoundCloud",
            callback_data="sc:search",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Найти на YouTube",
            callback_data="sc:yt_search",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="По ссылке  (SC / YouTube)",
            callback_data="sc:url",
            icon_custom_emoji_id="6042011682497106307",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="sc:back")],
    ])


def _cache_display_name(r: dict) -> str:
    if r.get("artist") or r.get("title"):
        return f"{r['artist']} — {r['title']}"
    return r.get("cache_key", "?")


def cache_results_keyboard(results: list[dict], fallback_source: str) -> InlineKeyboardMarkup:
    """Show cached tracks as options; last button falls back to SC or YT search."""
    buttons = []
    for i, r in enumerate(results):
        text = "Да, скачать" if len(results) == 1 else f"Да, скачать ({i + 1})"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"cache_pick:{i}")])
    label = "Нет, искать на SoundCloud" if fallback_source == "sc" else "Нет, искать на YouTube"
    buttons.append([InlineKeyboardButton(text=label, callback_data="cache_miss")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sc_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="sc:cancel")]
    ])


def yt_fallback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Найти на YouTube", callback_data="sc:yt_fallback")],
        [InlineKeyboardButton(text="Назад", callback_data="sc:cancel")],
    ])


def sc_results_keyboard(results: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, r in enumerate(results):
        mins, secs = divmod(r.duration, 60)
        text = f"{r.artist} — {r.title} [{mins}:{secs:02d}]"
        if len(text) > 64:
            text = text[:61] + "..."
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"sc_pick:{i}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="sc:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sc_playlists_keyboard(playlists: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=p["title"], callback_data=f"sc_pl:{p['kind']}")]
        for p in playlists
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="sc:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sc_resume_keyboard(filter_artists: list | None = None) -> InlineKeyboardMarkup:
    has_filter = bool(filter_artists)
    rows = [
        [InlineKeyboardButton(
            text="От первого добавленного к последнему",
            callback_data="sc_resume:start_reversed",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="От последнего добавленного к первому",
            callback_data="sc_resume:start",
            icon_custom_emoji_id="5775896410780079073",
        )],
        [InlineKeyboardButton(
            text="Продолжить с трека...",
            callback_data="sc_resume:seek",
            icon_custom_emoji_id="5345906554510012647",
        )],
        [InlineKeyboardButton(
            text="Выбрать треки",
            callback_data="sc_resume:track_select",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Добавить ещё исполнителя" if has_filter else "Фильтр по исполнителю",
            callback_data="sc_resume:filter_artist",
            icon_custom_emoji_id="6037397706505195857",
        )],
    ]
    if has_filter:
        for i, artist in enumerate(filter_artists):
            rows.append([InlineKeyboardButton(
                text=f"❌ {artist}",
                callback_data=f"sc_resume:rm_artist:{i}",
            )])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="sc_resume:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sc_resume_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Верно",
            callback_data="sc_resume:confirm",
            icon_custom_emoji_id="6041731551845159060",
        )],
        [InlineKeyboardButton(
            text="Нет, ввести заново",
            callback_data="sc_resume:retry",
            icon_custom_emoji_id="5870753782874246579",
        )],
    ])


def sc_cancel_queue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Выйти из очереди", callback_data="sc:cancel_queue")]
    ])


def sc_stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⛔ Остановить", callback_data="sc:stop")]
    ])


def sc_offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать с SoundCloud",
            callback_data="sc:batch_from_ym",
            icon_custom_emoji_id="6039802767931871481",
        )]
    ])


def sc_after_download_keyboard() -> InlineKeyboardMarkup:
    """Shown after a single track is successfully downloaded."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать ещё",
            callback_data="sc:search_again",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="sc:cancel")],
    ])


def sc_batch_token_keyboard() -> InlineKeyboardMarkup:
    """Token guide keyboard for SC batch flow — includes a Back button."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Войти через Яндекс",
            url=_YM_OAUTH_URL,
            icon_custom_emoji_id="5963103826075456248",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="sc:cancel")],
    ])


def sc_offer_extended_keyboard() -> InlineKeyboardMarkup:
    """Shown after YM export .txt — download on SC + filter by artist + CSV."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать с SoundCloud",
            callback_data="sc:batch_from_ym",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Фильтр по исполнителю",
            callback_data="export:filter_artist",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Экспорт в CSV",
            callback_data="export:csv",
        )],
    ])


def export_options_keyboard(track_count: int) -> InlineKeyboardMarkup:
    """Pre-export screen: choose what to do with loaded tracks."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Все треки ({track_count})",
            callback_data="export:deliver_all",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Фильтр по исполнителю",
            callback_data="export:filter_artist",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Экспорт в CSV",
            callback_data="export:csv",
        )],
        [InlineKeyboardButton(text="← Назад", callback_data="export:back_to_type")],
    ])


def export_filter_cancel_keyboard() -> InlineKeyboardMarkup:
    """Back button for ExportFlow.filter_input state."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="export:back_to_menu")],
    ])


def export_filter_result_keyboard() -> InlineKeyboardMarkup:
    """After artist .txt in ExportFlow — offer to download filtered list."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать треки этого исполнителя",
            callback_data="export:download_filtered",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="export:back_to_menu")],
    ])


# ── YMShare keyboards ──────────────────────────────────────────────────────────

def ym_share_token_keyboard() -> InlineKeyboardMarkup:
    """Token guide for YMShareFlow when no bot-level YM token is configured."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Войти через Яндекс",
            url=_YM_OAUTH_URL,
            icon_custom_emoji_id="5963103826075456248",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="yms:cancel")],
    ])


def ym_share_cancel_keyboard() -> InlineKeyboardMarkup:
    """Back to main menu for early YMShareFlow states."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="yms:cancel")],
    ])


def ym_share_actions_keyboard(filter_artists: list | None = None) -> InlineKeyboardMarkup:
    """Actions after a shared YM playlist is loaded."""
    has_filter = bool(filter_artists)
    rows = [
        [InlineKeyboardButton(
            text="Скачать все треки",
            callback_data="yms:download_all",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Добавить ещё исполнителя" if has_filter else "Фильтр по исполнителю",
            callback_data="yms:filter_artist",
            icon_custom_emoji_id="6037397706505195857",
        )],
    ]
    if has_filter:
        for i, artist in enumerate(filter_artists):
            rows.append([InlineKeyboardButton(
                text=f"❌ {artist}",
                callback_data=f"yms:rm_artist:{i}",
            )])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="yms:back_to_input")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ym_share_back_keyboard() -> InlineKeyboardMarkup:
    """Back to actions for YMShareFlow text-input states."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="yms:back_to_actions")],
    ])


def ym_share_filter_result_keyboard() -> InlineKeyboardMarkup:
    """After artist .txt in YMShareFlow — offer to download filtered list."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать треки этого исполнителя",
            callback_data="yms:download_filtered",
            icon_custom_emoji_id="6039802767931871481",
        )],
    ])


def batch_access_request_keyboard(back_cb: str) -> InlineKeyboardMarkup:
    """Shown to users without batch access — offer to send request to admin."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Запросить доступ", callback_data="batch_req:send")],
        [InlineKeyboardButton(text="← Назад", callback_data=back_cb)],
    ])


def batch_access_pending_keyboard(back_cb: str) -> InlineKeyboardMarkup:
    """Shown when user already has a pending request."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data=back_cb)],
    ])


def admin_batch_request_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """Sent to admin with approve/reject buttons — lives until clicked."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"batch_req:approve:{request_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"batch_req:reject:{request_id}"),
        ]
    ])


def ym_share_seek_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm start track in YMShareFlow seek."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Верно",
            callback_data="yms_resume:confirm",
            icon_custom_emoji_id="6041731551845159060",
        )],
        [InlineKeyboardButton(
            text="Нет, ввести заново",
            callback_data="yms_resume:retry",
            icon_custom_emoji_id="5870753782874246579",
        )],
    ])


# ── Spotify keyboards ──────────────────────────────────────────────────────────

def spotify_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Плейлист по ссылке", callback_data="spotify:playlist")],
        [InlineKeyboardButton(text="❤️ Мои лайки", callback_data="spotify:liked")],
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:back")],
    ])


def spotify_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:to_menu")],
    ])


def spotify_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Экспорт в .txt", callback_data="spotify:export_txt")],
        [InlineKeyboardButton(text="📊 Экспорт в .csv", callback_data="spotify:export_csv")],
        [InlineKeyboardButton(text="📥 Скачать через SoundCloud", callback_data="spotify:download")],
        [InlineKeyboardButton(text="🔍 Фильтр по исполнителю", callback_data="spotify:filter")],
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:to_menu")],
    ])


def faq_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Написать администрации", callback_data="faq:contact")],
        [InlineKeyboardButton(text="← В меню", callback_data="faq:back")],
    ])


def faq_contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="faq:back_to_faq")],
    ])


def tsel_panel_keyboard(count: int) -> InlineKeyboardMarkup:
    """Main track-selection panel: show selected count + confirm/cancel."""
    rows = []
    if count > 0:
        rows.append([InlineKeyboardButton(
            text=f"📋 Выбранные ({count})", callback_data="tsel:show_sel:0"
        )])
    confirm_text = f"✅ Начать скачивание ({count})" if count > 0 else "✅ Начать скачивание"
    rows.append([InlineKeyboardButton(text=confirm_text, callback_data="tsel:confirm")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="tsel:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tsel_results_keyboard(
    results: list,
    selected_keys: set,
    artist_all: list | None,
    total_selected: int,
) -> InlineKeyboardMarkup:
    """Search results: toggle add/remove per track + optional 'add all by artist'."""
    rows = []
    for i, t in enumerate(results):
        key = f"{t.get('artist', '')}||{t.get('title', '')}"
        in_sel = key in selected_keys
        sym = "✅" if in_sel else "➕"
        action = "rem" if in_sel else "add"
        label = f"{t.get('artist', '')} — {t.get('title', '')}"
        if len(label) > 55:
            label = label[:52] + "…"
        rows.append([InlineKeyboardButton(text=f"{sym} {label}", callback_data=f"tsel:{action}:{i}")])
    if artist_all:
        artist, cnt = artist_all
        short = (artist[:22] + "…") if len(artist) > 22 else artist
        rows.append([InlineKeyboardButton(
            text=f"➕ Все от {short} ({cnt})", callback_data="tsel:add_all"
        )])
    confirm_text = f"✅ Начать ({total_selected})" if total_selected > 0 else "✅ Начать"
    rows.append([
        InlineKeyboardButton(text="← К поиску", callback_data="tsel:back_panel"),
        InlineKeyboardButton(text=confirm_text, callback_data="tsel:confirm"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tsel_selected_keyboard(selected: list, page: int = 0, page_size: int = 8) -> InlineKeyboardMarkup:
    """Selected tracks list with per-track remove buttons and pagination."""
    start = page * page_size
    rows = []
    for i, t in enumerate(selected[start:start + page_size]):
        label = f"{t.get('artist', '')} — {t.get('title', '')}"
        if len(label) > 52:
            label = label[:49] + "…"
        rows.append([InlineKeyboardButton(
            text=f"❌ {label}", callback_data=f"tsel:rem_sel:{start + i}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀ Назад", callback_data=f"tsel:sel_page:{page - 1}"))
    if start + page_size < len(selected):
        nav.append(InlineKeyboardButton(text="Вперёд ▶", callback_data=f"tsel:sel_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="← К поиску", callback_data="tsel:back_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── AudioTagFlow keyboards ─────────────────────────────────────────────────────

def audio_tag_cancel_keyboard() -> InlineKeyboardMarkup:
    """Shown while waiting for track title — Назад cancels the whole flow."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="audio_tag:cancel")],
    ])


def audio_tag_back_keyboard() -> InlineKeyboardMarkup:
    """Shown while waiting for artist — Назад returns to title prompt."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="audio_tag:back_to_title")],
    ])


def spotify_filter_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать треки исполнителя", callback_data="spotify:download_filtered")],
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:back_to_actions")],
    ])
