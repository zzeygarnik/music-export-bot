from aiogram.fsm.state import State, StatesGroup


class ExportFlow(StatesGroup):
    choosing_service = State()
    choosing_retention = State()   # keep token for session or discard after export
    waiting_for_token = State()
    choosing_export_type = State()
    choosing_playlist = State()
    waiting_for_link = State()


class SCSearchFlow(StatesGroup):
    sc_menu = State()
    sc_search_query = State()
    sc_search_results = State()    # waiting for user to choose from top-5


class SCBatchFlow(StatesGroup):
    sc_ym_token = State()          # waiting for YM OAuth token input
    sc_ym_playlist = State()       # showing YM playlists to pick from
    sc_resume_choice = State()     # "с начала" or "продолжить с..."
    sc_resume_input = State()      # user types track name to resume from
    sc_resume_confirm = State()    # bot shows found track, user confirms
    sc_downloading = State()       # batch download in progress
