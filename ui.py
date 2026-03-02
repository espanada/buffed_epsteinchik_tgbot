import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def gender_label(value: str) -> str:
    labels = {"male": "муж", "female": "жен", "any": "любой"}
    return labels.get(value, value)


def gender_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👨 Муж", callback_data=f"set:{prefix}:male")],
            [InlineKeyboardButton("👩 Жен", callback_data=f"set:{prefix}:female")],
            [InlineKeyboardButton("✨ Любой", callback_data=f"set:{prefix}:any")],
        ]
    )


def candidate_keyboard(candidate_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❤️ Лайк", callback_data=f"like:{candidate_id}"),
                InlineKeyboardButton("⏭️ Пропустить", callback_data=f"skip:{candidate_id}"),
            ],
            [
                InlineKeyboardButton("🚫 Блок", callback_data=f"block:{candidate_id}"),
                InlineKeyboardButton("⚠️ Жалоба", callback_data=f"report:{candidate_id}"),
            ],
        ]
    )


def quote_block(text: str) -> str:
    lines = text.splitlines()
    body = "\n".join(f"│ {line}" if line else "│" for line in lines)
    return f"❝\n{body}\n❞"


def profile_text(row: sqlite3.Row) -> str:
    username_line = f"@{row['username']}" if row["username"] else "без username"
    text = (
        f"👤 {row['display_name']}, {row['age']}\n"
        f"🏙️ Город: {row['city']}\n"
        f"⚧ Пол: {gender_label(row['gender'])}\n"
        f"🔎 Ищу: {gender_label(row['looking_for'])}\n"
        f"🎯 Возрастной фильтр: {row['min_age']}-{row['max_age']}\n"
        f"📎 Контакт: {username_line}\n\n"
        f"{row['bio']}"
    )
    return quote_block(text)


def photo_caption(row: sqlite3.Row, prefix: str = "") -> str:
    text = profile_text(row)
    return f"{prefix}{text}" if prefix else text
