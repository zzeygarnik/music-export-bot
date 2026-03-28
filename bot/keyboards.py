from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_YM_OAUTH_URL = (
    "https://oauth.yandex.ru/authorize"
    "?response_type=token"
    "&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)


def service_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Экспорт треков в .txt",
            callback_data="service:yandex",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="Скачать MP3",
            callback_data="service:soundcloud",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Экспорт плейлиста по ссылке",
            callback_data="service:share",
            icon_custom_emoji_id="6042011682497106307",
        )],
        [InlineKeyboardButton(
            text="🎵 Spotify",
            callback_data="service:spotify",
        )],
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
        [InlineKeyboardButton(
            text="Скачать плейлист из Яндекс Музыки",
            callback_data="sc:batch",
            icon_custom_emoji_id="6039802767931871481",
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


def sc_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
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
    ])


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


def ym_share_actions_keyboard() -> InlineKeyboardMarkup:
    """Actions after a shared YM playlist is loaded."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать все треки",
            callback_data="yms:download_all",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(
            text="Фильтр по исполнителю",
            callback_data="yms:filter_artist",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Начать с определённого трека",
            callback_data="yms:seek",
            icon_custom_emoji_id="5345906554510012647",
        )],
        [InlineKeyboardButton(text="Назад", callback_data="yms:back_to_input")],
    ])


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
        [InlineKeyboardButton(text="Назад", callback_data="yms:back_to_actions")],
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


def spotify_filter_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать треки исполнителя", callback_data="spotify:download_filtered")],
        [InlineKeyboardButton(text="← Назад", callback_data="spotify:back_to_actions")],
    ])
