import os
from pathlib import Path
from typing import Optional

DB_PATH = Path("epsteinchik.db")
ENV_PATH = Path(".env")
TOKEN_FILE_PATH = Path("bot_token.txt")

MAX_LIKES_PER_DAY = 100
ACTION_COOLDOWN_SECONDS = 1.2
CANDIDATE_POOL_SIZE = 30
REPORT_AUTO_PAUSE_THRESHOLD = 3
REPORTS_PAGE_LIMIT = 20
MINI_APP_URL_FALLBACK = ""
MINI_APP_API_URL_FALLBACK = ""


def read_windows_persistent_env(name: str) -> Optional[str]:
    if os.name != "nt":
        return None

    try:
        import winreg
    except ImportError:
        return None

    key_paths = [
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    ]

    for hive, subkey in key_paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str):
                    value = value.strip()
                    if value:
                        return value
        except OSError:
            continue

    return None


def load_env_value(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value.strip()

    value = read_windows_persistent_env(name)
    if value:
        return value

    if ENV_PATH.exists():
        for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, env_value = line.split("=", 1)
            if key.strip() == name:
                return env_value.strip().strip("\"'")

    return None


def load_token() -> Optional[str]:
    token = load_env_value("TELEGRAM_BOT_TOKEN")
    if token:
        return token

    if TOKEN_FILE_PATH.exists():
        token_from_file = TOKEN_FILE_PATH.read_text(encoding="utf-8").strip()
        if token_from_file:
            return token_from_file

    return None


def get_database_url() -> Optional[str]:
    value = load_env_value("DATABASE_URL")
    if not value:
        return None
    return value.strip()


def get_admin_ids() -> set[int]:
    raw = load_env_value("TELEGRAM_ADMIN_IDS")
    if not raw:
        return set()
    result = set()
    for item in raw.split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result


def get_mini_app_url() -> str:
    value = load_env_value("TELEGRAM_MINI_APP_URL")
    if not value:
        return MINI_APP_URL_FALLBACK
    return value


def get_mini_app_api_url() -> str:
    value = load_env_value("TELEGRAM_MINI_APP_API_URL")
    if not value:
        return MINI_APP_API_URL_FALLBACK
    return value
