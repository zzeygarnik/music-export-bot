from aiogram.fsm.state import State, StatesGroup


class ExportFlow(StatesGroup):
    choosing_service = State()
    choosing_retention = State()   # keep token for session or discard after export
    waiting_for_token = State()
    choosing_export_type = State()
    choosing_playlist = State()
    waiting_for_link = State()
