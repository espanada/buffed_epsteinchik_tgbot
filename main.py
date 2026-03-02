import logging

from app_builder import build_app
from config import load_token
from db import init_db


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        level=logging.INFO,
    )

    token = load_token()
    if not token:
        raise RuntimeError(
            "Token not found. Set TELEGRAM_BOT_TOKEN as User/Machine env, or use .env/bot_token.txt"
        )

    init_db()
    app = build_app(token)
    app.run_polling()


if __name__ == "__main__":
    main()
