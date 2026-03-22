from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

_YM_OAUTH_URL = (
    "https://oauth.yandex.ru/authorize"
    "?response_type=token"
    "&client_id=23cabbbdc6cd418abb4b39c32c41195d"
)


def service_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Экспорт в .txt  (Яндекс Музыка)",
            callback_data="service:yandex",
            icon_custom_emoji_id="5870801517140775623",
        )],
        [InlineKeyboardButton(
            text="Скачать MP3  (SoundCloud)",
            callback_data="service:soundcloud",
            icon_custom_emoji_id="6039802767931871481",
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
    ])


def token_guide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Войти через Яндекс",
            url=_YM_OAUTH_URL,
            icon_custom_emoji_id="5963103826075456248",
        )]
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
    ])


def playlists_keyboard(playlists: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=p["title"], callback_data=f"playlist:{p['kind']}")]
        for p in playlists
    ]
    buttons.append([InlineKeyboardButton(text="◁ Назад", callback_data="export:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="action:cancel")]
    ])


# ── SoundCloud keyboards ───────────────────────────────────────────────────────

def sc_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Найти трек",
            callback_data="sc:search",
            icon_custom_emoji_id="6037397706505195857",
        )],
        [InlineKeyboardButton(
            text="Скачать плейлист",
            callback_data="sc:batch",
            icon_custom_emoji_id="6039802767931871481",
        )],
        [InlineKeyboardButton(text="◁ Назад", callback_data="sc:back")],
    ])


def sc_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◁ Назад", callback_data="sc:cancel")]
    ])


def sc_results_keyboard(results: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, r in enumerate(results):
        mins, secs = divmod(r.duration, 60)
        text = f"{r.artist} — {r.title} [{mins}:{secs:02d}]"
        if len(text) > 64:
            text = text[:61] + "..."
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"sc_pick:{i}")])
    buttons.append([InlineKeyboardButton(text="◁ Назад", callback_data="sc:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sc_playlists_keyboard(playlists: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=p["title"], callback_data=f"sc_pl:{p['kind']}")]
        for p in playlists
    ]
    buttons.append([InlineKeyboardButton(text="◁ Назад", callback_data="sc:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def sc_resume_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="С начала",
            callback_data="sc_resume:start",
            icon_custom_emoji_id="5775896410780079073",
        )],
        [InlineKeyboardButton(
            text="Продолжить с...",
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
