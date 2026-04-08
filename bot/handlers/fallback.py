"""Fallback handlers — must be registered last."""
import logging

from aiogram import Router
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.states import ExportFlow, SCSearchFlow, SCBatchFlow, YMShareFlow, AudioTagFlow, VKSearchFlow

router = Router()
log = logging.getLogger(__name__)

_STATE_HINTS = {
    None: "Введи /start чтобы начать.",
    ExportFlow.choosing_service: "Нажми на кнопку выше чтобы выбрать сервис.",
    ExportFlow.choosing_retention: "Выбери вариант хранения токена из кнопок выше.",
    ExportFlow.choosing_export_type: "Нажми на кнопку выше чтобы выбрать что экспортировать.",
    ExportFlow.choosing_playlist: "Выбери плейлист из списка выше.",
    ExportFlow.waiting_for_link: "Отправь ссылку на плейлист или нажми «Отмена».",
    ExportFlow.waiting_for_token: "Отправь токен, скопированный из адресной строки браузера.",
    ExportFlow.filter_input: "Введи имя исполнителя для фильтрации или нажми «Назад».",
    SCSearchFlow.sc_menu: "Нажми кнопку в меню SoundCloud.",
    SCSearchFlow.sc_search_query: "Введи название трека для поиска на SoundCloud.",
    SCSearchFlow.sc_search_results: "Выбери трек из результатов или нажми «Назад».",
    SCSearchFlow.sc_url_input: "Отправь ссылку на трек или плейлист (SoundCloud или YouTube).",
    SCSearchFlow.yt_search_query: "Введи название трека для поиска на YouTube.",
    SCSearchFlow.yt_search_results: "Выбери трек из результатов или нажми «Назад».",
    VKSearchFlow.vk_search_query:   "Введи название трека для поиска на VK Музыке.",
    VKSearchFlow.vk_search_results: "Выбери трек из результатов или нажми «Назад».",
    SCBatchFlow.sc_ym_token: "Отправь токен Яндекс Музыки.",
    SCBatchFlow.sc_ym_playlist: "Выбери плейлист из списка выше.",
    SCBatchFlow.sc_resume_choice: "Выбери, с какого трека начать скачивание.",
    SCBatchFlow.sc_resume_input: "Введи название трека для поиска в плейлисте.",
    SCBatchFlow.sc_resume_confirm: "Подтверди начальный трек кнопкой ниже.",
    SCBatchFlow.filter_input: "Введи имя исполнителя для фильтрации или нажми «Назад».",
    SCBatchFlow.sc_downloading: "Скачивание идёт. Нажми «⛔ Остановить» чтобы прервать.",
    YMShareFlow.token: "Отправь токен Яндекс Музыки для авторизации.",
    YMShareFlow.waiting: "Отправь ссылку или HTML-код плейлиста Яндекс Музыки.",
    YMShareFlow.actions: "Выбери действие из меню выше.",
    YMShareFlow.filter_input: "Введи имя исполнителя для фильтрации или нажми «Назад».",
    YMShareFlow.seek_input: "Введи название трека для поиска или нажми «Назад».",
    YMShareFlow.seek_confirm: "Подтверди начальный трек кнопкой ниже.",
    YMShareFlow.downloading: "Скачивание идёт. Нажми «⛔ Остановить» чтобы прервать.",
    AudioTagFlow.waiting_for_audio:  "Пришли аудио-файл или нажми «← Назад».",
    AudioTagFlow.waiting_for_title:  "Введи название трека или нажми «← Назад».",
    AudioTagFlow.waiting_for_artist: "Введи имя исполнителя или нажми «← Назад».",
}


@router.message()
async def fallback_message(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    hint = _STATE_HINTS.get(current, "Введи /start чтобы начать заново.")
    await message.answer(f"ℹ️ {hint}")


@router.callback_query()
async def fallback_callback(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("Эта кнопка устарела. Введи /start чтобы начать заново.", show_alert=True)
