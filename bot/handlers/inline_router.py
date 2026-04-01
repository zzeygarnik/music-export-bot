"""Inline query handler — searches track_cache and returns cached audio results."""
import base64
import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultCachedAudio,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from utils.db import search_cache_fuzzy

router = Router()
log = logging.getLogger(__name__)


def encode_inline_param(query: str) -> str:
    """Encode query into a valid switch_pm_parameter (base64url, max 64 chars, prefix 'il').

    Limits by bytes (not chars) so Cyrillic doesn't overflow the 64-char limit.
    45 bytes → 60 base64 chars + 2-char prefix = 62 ≤ 64.
    """
    raw = query.encode()[:45]
    return "il" + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_inline_param(param: str) -> str:
    """Decode a switch_pm_parameter back to the original query. Returns '' on error."""
    if not param.startswith("il"):
        return ""
    try:
        return base64.urlsafe_b64decode(param[2:] + "==").decode("utf-8", errors="ignore")
    except Exception:
        return ""


@router.inline_query()
async def on_inline_query(query: InlineQuery) -> None:
    q = (query.query or "").strip()

    if not q:
        await query.answer(
            [],
            switch_pm_text="🔍 Введи название трека",
            switch_pm_parameter="il",
            cache_time=0,
        )
        return

    hits = search_cache_fuzzy(q, threshold=70)
    results = [
        InlineQueryResultCachedAudio(id=str(i), audio_file_id=hit["file_id"])
        for i, hit in enumerate(hits[:5])
    ]

    param = encode_inline_param(q)

    if not results:
        # Show a tappable article so the user knows what to do
        short_q = q if len(q) <= 40 else q[:37] + "…"
        results = [
            InlineQueryResultArticle(
                id="find_in_bot",
                title=f"🔍 Найти «{short_q}»",
                description="Нет в кэше — открыть бота и скачать",
                input_message_content=InputTextMessageContent(
                    message_text=f"🔍 {q}",
                ),
                reply_markup=None,
            )
        ]
        await query.answer(
            results,
            switch_pm_text="Открыть бота →",
            switch_pm_parameter=param,
            cache_time=5,
            is_personal=True,
        )
        return

    await query.answer(
        results,
        switch_pm_text="🔍 Не то? Найти в боте →",
        switch_pm_parameter=param,
        cache_time=30,
        is_personal=True,
    )
