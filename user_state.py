import time
from typing import Any, Optional

from telegram.ext import ContextTypes

from config import ACTION_COOLDOWN_SECONDS


def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[dict[str, Any]]:
    return context.user_data.get("profile_state")


def set_user_state(context: ContextTypes.DEFAULT_TYPE, state: dict[str, Any]) -> None:
    context.user_data["profile_state"] = state


def clear_user_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("profile_state", None)


def action_allowed(context: ContextTypes.DEFAULT_TYPE) -> bool:
    now = time.time()
    last_ts = float(context.user_data.get("last_action_ts", 0.0))
    if now - last_ts < ACTION_COOLDOWN_SECONDS:
        return False
    context.user_data["last_action_ts"] = now
    return True
