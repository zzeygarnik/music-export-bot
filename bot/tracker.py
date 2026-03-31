"""Tracks the active keyboard message per user for stale-button detection."""
_active_msg: dict[int, int] = {}


def set_active_msg(user_id: int, msg_id: int) -> None:
    """Record that `msg_id` is now the active keyboard message for `user_id`."""
    _active_msg[user_id] = msg_id
