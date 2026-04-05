"""Admin panel: stats, recent events, batch whitelist management, user bans."""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.states import AdminFlow
from config import settings
from utils import db
from bot.keyboards import admin_batch_request_keyboard, batch_access_pending_keyboard
from bot.tracker import set_active_msg
from . import common as _hcommon

router = Router()
log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return settings.ADMIN_ID != 0 and user_id == settings.ADMIN_ID


# ── Keyboards ─────────────────────────────────────────────────────────────

def _menu_kb() -> InlineKeyboardMarkup:
    sc_label = "🔊 SC: вкл" if _hcommon.sc_downloads_enabled else "🔇 SC: выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="📋 Логи", callback_data="admin:logs"),
        ],
        [
            InlineKeyboardButton(text="📥 Batch-доступ", callback_data="admin:batch"),
            InlineKeyboardButton(text="🚫 Баны", callback_data="admin:bans"),
        ],
        [
            InlineKeyboardButton(text=sc_label, callback_data="admin:sc"),
            InlineKeyboardButton(text="🌐 Сеть", callback_data="admin:network"),
        ],
    ])


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data="admin:menu")]
    ])


def _batch_kb(whitelist: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for u in whitelist:
        name = f"@{u['username']}" if u["username"] else str(u["user_id"])
        rows.append([InlineKeyboardButton(
            text=f"❌ {name}", callback_data=f"admin:batch_rm:{u['user_id']}"
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="admin:batch_add")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bans_kb(banned: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for u in banned:
        name = f"@{u['username']}" if u["username"] else str(u["user_id"])
        rows.append([InlineKeyboardButton(
            text=f"✅ {name}", callback_data=f"admin:unban:{u['user_id']}"
        )])
    rows.append([InlineKeyboardButton(text="➕ Заблокировать", callback_data="admin:ban_add")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── /admin entry point ────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.set_state(AdminFlow.menu)
    sent = await message.answer("🔧 Админ-панель", reply_markup=_menu_kb())
    set_active_msg(message.from_user.id, sent.message_id)


# ── Menu navigation ───────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:menu")
async def on_back_to_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("🔧 Админ-панель", reply_markup=_menu_kb())


# ── Stats ─────────────────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:stats")
async def on_stats(call: CallbackQuery) -> None:
    stats = await db.get_admin_stats()
    if not stats:
        await call.message.edit_text("❌ Не удалось получить статистику (БД недоступна).", reply_markup=_back_kb())
        return

    t, w = stats["today"], stats["week"]
    lines = [
        "📊 <b>Статистика</b>\n",
        "<b>Сегодня:</b>",
        f"• Пользователей: {t['users']}",
        f"• Треков скачано: {t['tracks']}",
        f"• Батч-загрузок: {t['batches']}",
        f"• Ошибок: {t['errors']}",
        "",
        "<b>За 7 дней:</b>",
        f"• Пользователей: {w['users']}",
        f"• Треков скачано: {w['tracks']}",
        f"• Батч-загрузок: {w['batches']}",
        f"• Ошибок: {w['errors']}",
    ]
    top = stats.get("top_users", [])
    if top:
        lines += ["", "<b>Топ по трекам (7 дней):</b>"]
        for i, u in enumerate(top, 1):
            name = f"@{u['username']}" if u["username"] else "аноним"
            lines.append(f"{i}. {name} — {u['tracks']} тр.")

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=_back_kb())


# ── Recent events ─────────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:logs")
async def on_logs(call: CallbackQuery) -> None:
    events = await db.get_recent_events(20)
    if not events:
        await call.message.edit_text("📋 Событий нет.", reply_markup=_back_kb())
        return

    lines = ["📋 <b>Последние события:</b>\n"]
    for e in events:
        ts = str(e["ts"])[:16].replace("T", " ") if e["ts"] else "?"
        user = f"@{e['username']}" if e["username"] else f"#{e['user_hash']}"
        icon = "✅" if e["result"] == "success" else ("⏹" if e["result"] == "stopped" else "❌")
        tracks = f" ({e['track_count']} тр.)" if e["track_count"] else ""
        lines.append(f"<code>{ts}</code> {icon} {user} <b>{e['action']}</b>{tracks}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())


# ── Batch whitelist ───────────────────────────────────────────────────────

def _batch_text(whitelist: list[dict]) -> str:
    mode = settings.BATCH_ALLOWED_USERS.strip()
    mode_text = "все (BATCH_ALLOWED_USERS=*)" if mode == "*" else "по списку ниже"
    return (
        f"📥 <b>Batch-доступ</b>\n"
        f"Режим: {mode_text}\n\n"
        f"Пользователей в списке: {len(whitelist)}"
    )


@router.callback_query(AdminFlow.menu, F.data == "admin:batch")
async def on_batch(call: CallbackQuery) -> None:
    whitelist = await db.get_batch_whitelist()
    await call.message.edit_text(_batch_text(whitelist), parse_mode="HTML", reply_markup=_batch_kb(whitelist))


@router.callback_query(AdminFlow.menu, F.data == "admin:batch_add")
async def on_batch_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.batch_add)
    await call.message.edit_text(
        "➕ Отправь <b>user_id</b> (число) или перешли мне сообщение пользователя.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Отмена", callback_data="admin:cancel_batch_add")]
        ]),
    )


@router.callback_query(AdminFlow.batch_add, F.data == "admin:cancel_batch_add")
async def on_cancel_batch_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.menu)
    whitelist = await db.get_batch_whitelist()
    await call.message.edit_text(_batch_text(whitelist), parse_mode="HTML", reply_markup=_batch_kb(whitelist))


@router.message(AdminFlow.batch_add)
async def on_batch_add_input(message: Message, state: FSMContext) -> None:
    user_id, username = _parse_user_input(message)
    if user_id is None:
        await message.answer("❌ Не удалось определить user_id. Отправь число или перешли сообщение.")
        return
    await db.add_batch_whitelist(user_id, username)
    await state.set_state(AdminFlow.menu)
    whitelist = await db.get_batch_whitelist()
    name = f"@{username}" if username else str(user_id)
    await message.answer(
        f"✅ {name} (<code>{user_id}</code>) добавлен в whitelist.",
        parse_mode="HTML",
        reply_markup=_batch_kb(whitelist),
    )


@router.callback_query(AdminFlow.menu, F.data.startswith("admin:batch_rm:"))
async def on_batch_rm(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[-1])
    await db.remove_batch_whitelist(user_id)
    whitelist = await db.get_batch_whitelist()
    await call.message.edit_text(_batch_text(whitelist), parse_mode="HTML", reply_markup=_batch_kb(whitelist))


# ── Bans ──────────────────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:bans")
async def on_bans(call: CallbackQuery) -> None:
    banned = await db.get_banned_users()
    await call.message.edit_text(
        f"🚫 <b>Заблокированные</b>\nВсего: {len(banned)}",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


@router.callback_query(AdminFlow.menu, F.data == "admin:ban_add")
async def on_ban_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.ban_input)
    await call.message.edit_text(
        "🚫 Отправь <b>user_id</b> (число) или перешли мне сообщение пользователя.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="← Отмена", callback_data="admin:cancel_ban_add")]
        ]),
    )


@router.callback_query(AdminFlow.ban_input, F.data == "admin:cancel_ban_add")
async def on_cancel_ban_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.menu)
    banned = await db.get_banned_users()
    await call.message.edit_text(
        f"🚫 <b>Заблокированные</b>\nВсего: {len(banned)}",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


@router.message(AdminFlow.ban_input)
async def on_ban_input(message: Message, state: FSMContext) -> None:
    user_id, username = _parse_user_input(message)
    if user_id is None:
        await message.answer("❌ Не удалось определить user_id. Отправь число или перешли сообщение.")
        return
    if user_id == settings.ADMIN_ID:
        await message.answer("❌ Нельзя заблокировать самого себя.")
        return
    await db.ban_user(user_id, username)
    await state.set_state(AdminFlow.menu)
    banned = await db.get_banned_users()
    name = f"@{username}" if username else str(user_id)
    await message.answer(
        f"🚫 {name} (<code>{user_id}</code>) заблокирован.",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


@router.callback_query(AdminFlow.menu, F.data.startswith("admin:unban:"))
async def on_unban(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[-1])
    await db.unban_user(user_id)
    banned = await db.get_banned_users()
    await call.message.edit_text(
        f"🚫 <b>Заблокированные</b>\nВсего: {len(banned)}",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


# ── Batch access requests (user-side + admin approve/reject) ─────────────

@router.callback_query(F.data == "batch_req:send")
async def on_batch_req_send(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    username = call.from_user.username

    if await db.get_pending_request(user_id) is not None:
        await call.answer("⏳ Твой запрос уже на рассмотрении.", show_alert=True)
        return

    request_id = await db.create_batch_request(user_id, username)
    if request_id == -1:
        await call.answer("❌ Ошибка при отправке запроса. Попробуй позже.", show_alert=True)
        return

    if settings.ADMIN_ID:
        name = f"@{username}" if username else f"ID {user_id}"
        try:
            msg = await call.bot.send_message(
                settings.ADMIN_ID,
                f"📥 <b>Запрос на batch-доступ</b>\n\nПользователь: {name} (<code>{user_id}</code>)",
                parse_mode="HTML",
                reply_markup=admin_batch_request_keyboard(request_id),
            )
            await db.set_request_admin_msg(request_id, msg.message_id, settings.ADMIN_ID)
        except Exception as e:
            log.warning("Failed to notify admin about batch request %s: %s", request_id, e)

    await call.message.edit_text(
        "✅ Запрос отправлен администратору.\nТы получишь уведомление когда он будет рассмотрен.",
    )


@router.callback_query(F.data.startswith("batch_req:approve:"))
async def on_batch_req_approve(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    request_id = int(call.data.split(":")[-1])
    req = await db.get_request_by_id(request_id)
    if not req:
        await call.answer("Запрос не найден.", show_alert=True)
        return
    if req["status"] != "pending":
        await call.answer("Запрос уже обработан.", show_alert=True)
        return

    await db.resolve_batch_request(request_id, "approved")
    await db.add_batch_whitelist(req["user_id"], req["username"])

    name = f"@{req['username']}" if req["username"] else f"ID {req['user_id']}"
    await call.message.edit_text(
        f"✅ <b>Доступ одобрен</b> — {name} (<code>{req['user_id']}</code>)",
        parse_mode="HTML",
    )
    try:
        await call.bot.send_message(
            req["user_id"],
            "✅ Твой запрос на batch-скачивание <b>одобрен</b>! Можешь начинать.",
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("batch_req:reject:"))
async def on_batch_req_reject(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    request_id = int(call.data.split(":")[-1])
    req = await db.get_request_by_id(request_id)
    if not req:
        await call.answer("Запрос не найден.", show_alert=True)
        return
    if req["status"] != "pending":
        await call.answer("Запрос уже обработан.", show_alert=True)
        return

    await db.resolve_batch_request(request_id, "rejected")

    name = f"@{req['username']}" if req["username"] else f"ID {req['user_id']}"
    await call.message.edit_text(
        f"❌ <b>Отклонено</b> — {name} (<code>{req['user_id']}</code>)",
        parse_mode="HTML",
    )
    try:
        await call.bot.send_message(
            req["user_id"],
            "❌ Твой запрос на batch-скачивание был <b>отклонён</b>.",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ── Network status ────────────────────────────────────────────────────────

def _network_text() -> str:
    import time as _time
    s = _hcommon.get_network_status()
    if s["on_proxy"]:
        lines = [
            "🌐 <b>Сетевой статус SC</b>\n",
            "Режим: <b>прокси</b> (основной IP заблокирован)",
            f"Основной IP сервера: <code>{s['main_ip']}</code>",
            f"Активный прокси: <code>{s['active_proxy'] or '—'}</code>",
        ]
        if s["next_check_in"] is not None:
            m, sec = divmod(s["next_check_in"], 60)
            countdown = f"{m} мин {sec} сек" if m else f"{sec} сек"
            lines.append(f"Следующая проверка основного IP: через <b>{countdown}</b>")
        else:
            lines.append("Проверка основного IP: не запущена")
        lines.append(f"\n<i>Интервал проверки: {s['check_interval'] // 60} мин</i>")
    else:
        lines = [
            "🌐 <b>Сетевой статус SC</b>\n",
            "Режим: <b>основной IP</b> ✅",
            f"IP сервера: <code>{s['main_ip']}</code>",
        ]
    return "\n".join(lines)


@router.callback_query(AdminFlow.menu, F.data == "admin:network")
async def on_network(call: CallbackQuery) -> None:
    await call.message.edit_text(_network_text(), parse_mode="HTML", reply_markup=_back_kb())


# ── SC download toggle ────────────────────────────────────────────────────

def _sc_text() -> str:
    state = "🔊 включено" if _hcommon.sc_downloads_enabled else "🔇 выключено"
    hint = ("Нажми кнопку ниже чтобы запретить обычным пользователям запускать скачивание с SoundCloud."
            if _hcommon.sc_downloads_enabled else
            "Обычные пользователи не могут скачивать треки с SoundCloud. Нажми кнопку чтобы включить обратно.")
    return f"☁️ <b>SoundCloud для пользователей: {state}</b>\n\n{hint}"

def _sc_kb() -> InlineKeyboardMarkup:
    if _hcommon.sc_downloads_enabled:
        toggle = InlineKeyboardButton(text="🔇 Выключить SC для пользователей", callback_data="admin:sc_toggle")
    else:
        toggle = InlineKeyboardButton(text="🔊 Включить SC для пользователей", callback_data="admin:sc_toggle")
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle],
        [InlineKeyboardButton(text="← Назад", callback_data="admin:menu")],
    ])


@router.callback_query(AdminFlow.menu, F.data == "admin:sc")
async def on_sc_control(call: CallbackQuery) -> None:
    await call.message.edit_text(_sc_text(), parse_mode="HTML", reply_markup=_sc_kb())


@router.callback_query(F.data == "admin:sc_toggle")
async def on_sc_toggle(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    _hcommon.sc_downloads_enabled = not _hcommon.sc_downloads_enabled
    action = "включено" if _hcommon.sc_downloads_enabled else "выключено"
    await call.answer(f"SC {action}.", show_alert=False)
    # Update the SC control page if we're still looking at it, otherwise update the menu
    try:
        await call.message.edit_text(_sc_text(), parse_mode="HTML", reply_markup=_sc_kb())
    except Exception:
        pass


@router.callback_query(F.data == "admin:sc_disable")
async def on_sc_disable(call: CallbackQuery) -> None:
    """Fast-disable SC from an error notification message."""
    if not _is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return
    if not _hcommon.sc_downloads_enabled:
        await call.answer("SC уже выключен.", show_alert=True)
        return
    _hcommon.sc_downloads_enabled = False
    await call.answer("SC выключен для пользователей.", show_alert=True)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── Helper ────────────────────────────────────────────────────────────────

def _parse_user_input(message: Message) -> tuple[int | None, str | None]:
    """Extract (user_id, username) from forwarded message or plain numeric text."""
    if message.forward_from:
        u = message.forward_from
        return u.id, u.username
    if message.text:
        try:
            return int(message.text.strip()), None
        except ValueError:
            pass
    return None, None
