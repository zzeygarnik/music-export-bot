from aiogram.fsm.state import State, StatesGroup


class ExportFlow(StatesGroup):
    choosing_service = State()
    choosing_retention = State()   # keep token for session or discard after export
    waiting_for_token = State()
    choosing_export_type = State()
    export_options = State()       # pre-export: choose all / filter / csv
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
    yt_search_query = State()
    yt_search_results = State()    # waiting for user to choose from top-5 YouTube results
    sc_cache_results = State()     # showing cache hits, waiting for user to pick or reject


class SCBatchFlow(StatesGroup):
    sc_ym_token = State()          # waiting for YM OAuth token input
    sc_ym_playlist = State()       # showing YM playlists to pick from
    sc_resume_choice = State()     # "с начала" or "продолжить с..."
    sc_resume_input = State()      # user types track name to resume from
    sc_resume_confirm = State()    # bot shows found track, user confirms
    filter_input = State()         # waiting for artist name to filter download playlist
    sc_downloading = State()       # batch download in progress
    track_selection = State()      # user searches and picks specific tracks
    sc_queued = State()            # waiting in batch download queue


class SpotifyFlow(StatesGroup):
    menu = State()              # choose: public playlist / liked tracks
    playlist_waiting = State()  # waiting for playlist URL
    auth_waiting = State()      # waiting for OAuth redirect URL (liked tracks)
    actions = State()           # loaded tracks — choose action
    filter_input = State()      # waiting for artist name
    downloading = State()       # batch in progress


class AdminFlow(StatesGroup):
    menu = State()       # browsing admin panel (stats/logs/batch/bans)
    batch_add = State()  # waiting for user_id to add to batch whitelist
    ban_input = State()  # waiting for user_id to ban


class FAQFlow(StatesGroup):
    contact_waiting = State()  # waiting for user's message to admin


class AudioTagFlow(StatesGroup):
    waiting_for_audio          = State()  # user clicked button, waiting for file
    waiting_for_field_selection = State() # audio received; user picks what to change
    waiting_for_title          = State()  # waiting for track title input
    waiting_for_artist         = State()  # waiting for artist name input
    waiting_for_cover          = State()  # waiting for cover image

class VKSearchFlow(StatesGroup):
    vk_search_query   = State()   # waiting for search query
    vk_search_results = State()   # showing top-5 results, waiting for pick

