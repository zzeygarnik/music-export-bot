"""Handlers package — combines all sub-routers in priority order."""
from aiogram import Router

from .admin_router import router as admin_router
from .ym_router import router as ym_router
from .sc_router import router as sc_router
from .yms_router import router as yms_router
from .spotify_router import router as spotify_router
from .inline_router import router as inline_router
from .audio_tag_router import router as audio_tag_router
from .fallback import router as fallback_router  # must be last

router = Router()
router.include_router(admin_router)
router.include_router(ym_router)
router.include_router(sc_router)
router.include_router(yms_router)
router.include_router(spotify_router)
router.include_router(inline_router)
router.include_router(audio_tag_router)
router.include_router(fallback_router)

__all__ = ["router"]
