"""Inline query handler — searches track_cache and returns cached audio results."""
import base64
import logging

from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultCachedAudio

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
        await query.answer([], cache_time=0)
        return

    hits = search_cache_fuzzy(q, threshold=70)
    results = [
        InlineQueryResultCachedAudio(id=str(i), audio_file_id=hit["file_id"])
        for i, hit in enumerate(hits[:5])
    ]

    param = encode_inline_param(q)

    if not results:
        # No cache hit — show only the switch_pm button with the query in its label.
        # InlineQueryResultArticle is intentionally NOT used: it sends text to chat on tap.
        short_q = q if len(q) <= 30 else q[:27] + "…"
        await query.answer(
            [],
            switch_pm_text=f"🔍 Найти «{short_q}» в боте",
            switch_pm_parameter=param,
            cache_time=0,
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
