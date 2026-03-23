from aiogram.fsm.state import State, StatesGroup


class ExportFlow(StatesGroup):
    choosing_service = State()
    choosing_retention = State()   # keep token for session or discard after export
    waiting_for_token = State()
    choosing_export_type = State()
    choosing_playlist = State()
    waiting_for_link = State()
    filter_input = State()         # waiting for artist name after export .txt


class YMShareFlow(StatesGroup):
    token = State()        # waiting for user YM token (only when YM_BOT_TOKEN not configured)
    waiting = State()      # waiting for iframe HTML or playlist URL
    actions = State()      # choose: download all / filter by artist / start from track
    filter_input = State() # waiting for artist name to filter playlist
    seek_input = State()   # waiting for track name to start batch from
    seek_confirm = State() # confirming the resolved start track
    downloading = State()  # batch download in progress


class SCSearchFlow(StatesGroup):
    sc_menu = State()
    sc_search_query = State()
    sc_search_results = State()    # waiting for user to choose from top-5
    sc_url_input = State()         # waiting for SC/YouTube URL


class SCBatchFlow(StatesGroup):
    sc_ym_token = State()          # waiting for YM OAuth token input
    sc_ym_playlist = State()       # showing YM playlists to pick from
    sc_resume_choice = State()     # "с начала" or "продолжить с..."
    sc_resume_input = State()      # user types track name to resume from
    sc_resume_confirm = State()    # bot shows found track, user confirms
    sc_downloading = State()       # batch download in progress
