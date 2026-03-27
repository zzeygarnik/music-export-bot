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

router = Router()
log = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return settings.ADMIN_ID != 0 and user_id == settings.ADMIN_ID


# ── Keyboards ─────────────────────────────────────────────────────────────

def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="Логи", callback_data="admin:logs"),
        ],
        [
            InlineKeyboardButton(text="Batch-доступ", callback_data="admin:batch"),
            InlineKeyboardButton(text="🚫 Баны", callback_data="admin:bans"),
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
    await message.answer("🔧 Админ-панель", reply_markup=_menu_kb())


# ── Menu navigation ───────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:menu")
async def on_back_to_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("🔧 Админ-панель", reply_markup=_menu_kb())


# ── Stats ─────────────────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:stats")
async def on_stats(call: CallbackQuery) -> None:
    stats = db.get_admin_stats()
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
    events = db.get_recent_events(20)
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
    whitelist = db.get_batch_whitelist()
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
    whitelist = db.get_batch_whitelist()
    await call.message.edit_text(_batch_text(whitelist), parse_mode="HTML", reply_markup=_batch_kb(whitelist))


@router.message(AdminFlow.batch_add)
async def on_batch_add_input(message: Message, state: FSMContext) -> None:
    user_id, username = _parse_user_input(message)
    if user_id is None:
        await message.answer("❌ Не удалось определить user_id. Отправь число или перешли сообщение.")
        return
    db.add_batch_whitelist(user_id, username)
    await state.set_state(AdminFlow.menu)
    whitelist = db.get_batch_whitelist()
    name = f"@{username}" if username else str(user_id)
    await message.answer(
        f"✅ {name} (<code>{user_id}</code>) добавлен в whitelist.",
        parse_mode="HTML",
        reply_markup=_batch_kb(whitelist),
    )


@router.callback_query(AdminFlow.menu, F.data.startswith("admin:batch_rm:"))
async def on_batch_rm(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[-1])
    db.remove_batch_whitelist(user_id)
    whitelist = db.get_batch_whitelist()
    await call.message.edit_text(_batch_text(whitelist), parse_mode="HTML", reply_markup=_batch_kb(whitelist))


# ── Bans ──────────────────────────────────────────────────────────────────

@router.callback_query(AdminFlow.menu, F.data == "admin:bans")
async def on_bans(call: CallbackQuery) -> None:
    banned = db.get_banned_users()
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
    banned = db.get_banned_users()
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
    db.ban_user(user_id, username)
    await state.set_state(AdminFlow.menu)
    banned = db.get_banned_users()
    name = f"@{username}" if username else str(user_id)
    await message.answer(
        f"🚫 {name} (<code>{user_id}</code>) заблокирован.",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


@router.callback_query(AdminFlow.menu, F.data.startswith("admin:unban:"))
async def on_unban(call: CallbackQuery) -> None:
    user_id = int(call.data.split(":")[-1])
    db.unban_user(user_id)
    banned = db.get_banned_users()
    await call.message.edit_text(
        f"🚫 <b>Заблокированные</b>\nВсего: {len(banned)}",
        parse_mode="HTML",
        reply_markup=_bans_kb(banned),
    )


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
