import logging
import json
import re
import sqlite3
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import REPORTS_PAGE_LIMIT, get_admin_ids, get_mini_app_api_url, get_mini_app_url
from db import (
    admin_set_profile_active,
    block_user,
    can_interact,
    delete_profile,
    get_admin_stats,
    get_next_candidate,
    get_pending_likes,
    get_profile,
    get_reported_profiles,
    get_stats,
    like_user,
    report_user,
    resolve_reports_for_user,
    set_profile_active,
    update_profile_bio,
    update_profile_filters,
    update_profile_photo,
    upsert_profile,
    was_viewed,
)
from models import ALLOWED_GENDERS, Profile, State
from ui import candidate_keyboard, gender_keyboard, gender_label, photo_caption, profile_text
from user_state import action_allowed, clear_user_state, get_user_state, set_user_state

LIKES_SKIPPED_IDS_KEY = "likes_skipped_ids"
CITY_MIN_LEN = 2
CITY_MAX_LEN = 40
BIO_MIN_LEN = 10
BIO_MAX_LEN = 300


def interaction_error_text(reason: str) -> str:
    if reason == "quota_exceeded":
        return "Р›РёРјРёС‚ Р»Р°Р№РєРѕРІ РЅР° СЃРµРіРѕРґРЅСЏ РёСЃС‡РµСЂРїР°РЅ."
    if reason == "self_action":
        return "РќРµР»СЊР·СЏ РІР·Р°РёРјРѕРґРµР№СЃС‚РІРѕРІР°С‚СЊ СЃРѕ СЃРІРѕРµР№ Р°РЅРєРµС‚РѕР№."
    if reason == "profile_not_found":
        return "Р­С‚Р° Р°РЅРєРµС‚Р° Р±РѕР»СЊС€Рµ РЅРµРґРѕСЃС‚СѓРїРЅР°."
    if reason == "target_inactive":
        return "Р­С‚Р° Р°РЅРєРµС‚Р° СЃРµР№С‡Р°СЃ РЅРµР°РєС‚РёРІРЅР°."
    if reason == "source_inactive":
        return "РЎРЅР°С‡Р°Р»Р° Р°РєС‚РёРІРёСЂСѓР№ СЃРІРѕСЋ Р°РЅРєРµС‚Сѓ С‡РµСЂРµР· /resume."
    if reason == "blocked":
        return "Р’Р·Р°РёРјРѕРґРµР№СЃС‚РІРёРµ РЅРµРґРѕСЃС‚СѓРїРЅРѕ."
    return "РќРµ СѓРґР°Р»РѕСЃСЊ РІС‹РїРѕР»РЅРёС‚СЊ РґРµР№СЃС‚РІРёРµ, РїРѕРїСЂРѕР±СѓР№ РїРѕР·Р¶Рµ."


def parse_admin_target(args: list[str]) -> Optional[int]:
    if not args:
        return None
    raw = args[0].strip()
    if not raw.isdigit():
        return None
    return int(raw)


def parse_webapp_int(raw: Any, min_value: int, max_value: int) -> Optional[int]:
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isdigit():
        value = int(raw)
    else:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def parse_int_in_range(raw: str, min_value: int, max_value: int) -> Optional[int]:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value < min_value or value > max_value:
        return None
    return value


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def validate_city_input(raw: str) -> tuple[bool, str, str]:
    city = normalize_spaces(raw)
    if len(city) < CITY_MIN_LEN or len(city) > CITY_MAX_LEN:
        return False, city, f"\u0413\u043e\u0440\u043e\u0434 \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u043e\u0442 {CITY_MIN_LEN} \u0434\u043e {CITY_MAX_LEN} \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432."
    if city.isdigit():
        return False, city, "\u0413\u043e\u0440\u043e\u0434 \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0441\u043e\u0441\u0442\u043e\u044f\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0438\u0437 \u0447\u0438\u0441\u0435\u043b."
    if not any(ch.isalpha() for ch in city):
        return False, city, "\u0412 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0438 \u0433\u043e\u0440\u043e\u0434\u0430 \u0434\u043e\u043b\u0436\u043d\u044b \u0431\u044b\u0442\u044c \u0431\u0443\u043a\u0432\u044b."
    if not all(ch.isalpha() or ch in " -'" for ch in city):
        return False, city, "\u0413\u043e\u0440\u043e\u0434 \u043c\u043e\u0436\u0435\u0442 \u0441\u043e\u0434\u0435\u0440\u0436\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0431\u0443\u043a\u0432\u044b, \u043f\u0440\u043e\u0431\u0435\u043b, \u0434\u0435\u0444\u0438\u0441 \u0438 \u0430\u043f\u043e\u0441\u0442\u0440\u043e\u0444."
    return True, city, ""


def validate_bio_input(raw: str) -> tuple[bool, str, str]:
    bio = normalize_spaces(raw)
    if len(bio) < BIO_MIN_LEN:
        return False, bio, f"Bio \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u043d\u0435 \u043a\u043e\u0440\u043e\u0447\u0435 {BIO_MIN_LEN} \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432."
    if len(bio) > BIO_MAX_LEN:
        return False, bio, f"\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c {BIO_MAX_LEN} \u0441\u0438\u043c\u0432\u043e\u043b\u043e\u0432."
    if not any(ch.isalpha() for ch in bio):
        return False, bio, "Bio \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u043e\u0434\u0435\u0440\u0436\u0430\u0442\u044c \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u0443 \u0431\u0443\u043a\u0432\u0443."
    return True, bio, ""


async def send_profile_message(update: Update, row: sqlite3.Row, prefix: str = "") -> None:
    caption = photo_caption(row, prefix)
    if row["photo_file_id"]:
        if update.message:
            await update.message.reply_photo(row["photo_file_id"], caption=caption)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_photo(row["photo_file_id"], caption=caption)
        return

    if update.message:
        await update.message.reply_text(caption)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(caption)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    set_user_state(context, {"step": State.WAIT_AGE})
    await update.message.reply_text(
        "рџ‘‹ Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ РІ Р­РїС€С‚РµР№РЅС‡РёРє\n\n"
        "РЎРµР№С‡Р°СЃ СЃРѕР±РµСЂРµРј С‚РІРѕСЋ Р°РЅРєРµС‚Сѓ.\n"
        "РЁР°Рі 1/6: СЃРєРѕР»СЊРєРѕ С‚РµР±Рµ Р»РµС‚? (18-99)"
    )


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    row = get_profile(user.id)
    if row is None:
        if update.message:
            await update.message.reply_text("рџ“­ РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РСЃРїРѕР»СЊР·СѓР№ /start, С‡С‚РѕР±С‹ СЃРѕР·РґР°С‚СЊ.")
        return

    await send_profile_message(update, row, prefix="рџЄЄ РўРІРѕСЏ Р°РЅРєРµС‚Р°\n\n")


async def send_candidate(update: Update, candidate: sqlite3.Row, *, edit_current: bool) -> None:
    text = "рџ’« РќРѕРІР°СЏ Р°РЅРєРµС‚Р°\n\n" + profile_text(candidate)
    keyboard = candidate_keyboard(candidate["user_id"])

    if candidate["photo_file_id"]:
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_photo(
                candidate["photo_file_id"],
                caption=text,
                reply_markup=keyboard,
            )
        elif update.message:
            await update.message.reply_photo(candidate["photo_file_id"], caption=text, reply_markup=keyboard)
        return

    if update.callback_query and edit_current and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=keyboard)
        return

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)


async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit_current: bool = False) -> None:
    user = update.effective_user
    if user is None:
        return

    me = get_profile(user.id)
    if me is None:
        if update.message:
            await update.message.reply_text("\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u043e\u0437\u0434\u0430\u0439 \u0430\u043d\u043a\u0435\u0442\u0443 \u0447\u0435\u0440\u0435\u0437 /start")
        elif update.callback_query:
            await update.callback_query.answer("\u0421\u043d\u0430\u0447\u0430\u043b\u0430 /start", show_alert=True)
        return

    if me["is_active"] != 1:
        text = "\u0410\u043d\u043a\u0435\u0442\u0430 \u043d\u0430 \u043f\u0430\u0443\u0437\u0435. \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 /resume, \u0447\u0442\u043e\u0431\u044b \u0441\u043d\u043e\u0432\u0430 \u043f\u043e\u044f\u0432\u043b\u044f\u0442\u044c\u0441\u044f \u0432 \u043f\u043e\u0438\u0441\u043a\u0435."
        if update.message:
            await update.message.reply_text(text)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(text)
        return

    candidate = get_next_candidate(user.id)
    if candidate is None:
        text = "рџ§­ РџРѕРєР° Р°РЅРєРµС‚ РїРѕ С‚РІРѕРёРј С„РёР»СЊС‚СЂР°Рј РЅРµС‚ РёР»Рё РІСЃРµ СѓР¶Рµ РїСЂРѕСЃРјРѕС‚СЂРµРЅС‹.\nРџРѕРїСЂРѕР±СѓР№ РїРѕР·Р¶Рµ."
        if update.callback_query and update.callback_query.message and edit_current:
            msg = update.callback_query.message
            try:
                await msg.edit_text(text)
            except BadRequest as exc:
                if "no text in the message to edit" in str(exc).lower():
                    try:
                        await msg.edit_caption(caption=text)
                    except TelegramError:
                        await msg.reply_text(text)
                else:
                    raise
        elif update.message:
            await update.message.reply_text(text)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(text)
        return

    await send_candidate(update, candidate, edit_current=edit_current)


async def browse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await browse(update, context, edit_current=False)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    sent, received, matches = get_stats(user.id)
    await update.message.reply_text(
        "рџ“Љ РўРІРѕСЏ СЃС‚Р°С‚РёСЃС‚РёРєР°\n\n"
        f"вќ¤пёЏ РћС‚РїСЂР°РІР»РµРЅРѕ Р»Р°Р№РєРѕРІ: {sent}\n"
        f"рџ“Ґ РџРѕР»СѓС‡РµРЅРѕ Р»Р°Р№РєРѕРІ: {received}\n"
        f"рџ’ РњСЌС‚С‡РµР№: {matches}"
    )


def format_pending_likes_text(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "рџ“­ РџРѕРєР° РЅРµС‚ РЅРѕРІС‹С… Р»Р°Р№РєРѕРІ Р±РµР· РѕС‚РІРµС‚Р°."

    lines = []
    for row in rows:
        name = f"@{row['username']}" if row["username"] else row["display_name"]
        lines.append(f"- {name}, {row['age']} ({row['city']})")
    return "рџ’Њ РўРµР±СЏ Р»Р°Р№РєРЅСѓР»Рё:\n" + "\n".join(lines)


async def likes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    rows = get_pending_likes(user.id)
    if not rows:
        await update.message.reply_text(format_pending_likes_text(rows))
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("рџ”Ќ РћС‚РєСЂС‹С‚СЊ РєР°СЂС‚РѕС‡РєРё", callback_data="likes:open")]]
    )
    await update.message.reply_text(format_pending_likes_text(rows), reply_markup=keyboard)


def liker_profile_text(row: sqlite3.Row) -> str:
    return profile_text(row)


def likes_review_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("вќ¤пёЏ Р›Р°Р№Рє РІ РѕС‚РІРµС‚", callback_data=f"likes:like:{user_id}"),
                InlineKeyboardButton("вЏ­пёЏ РџСЂРѕРїСѓСЃС‚РёС‚СЊ", callback_data=f"likes:skip:{user_id}"),
            ],
            [
                InlineKeyboardButton("рџљ« Р‘Р»РѕРє", callback_data=f"likes:block:{user_id}"),
                InlineKeyboardButton("вљ пёЏ Р–Р°Р»РѕР±Р°", callback_data=f"likes:report:{user_id}"),
            ],
        ]
    )


def get_next_pending_like(user_id: int, skipped_ids: set[int]) -> Optional[sqlite3.Row]:
    for row in get_pending_likes(user_id):
        if row["user_id"] not in skipped_ids:
            return row
    return None


async def notify_match(context: ContextTypes.DEFAULT_TYPE, user_id: int, to_user_id: int) -> None:
    liked_user = get_profile(to_user_id)
    me = get_profile(user_id)
    if not liked_user or not me:
        return

    me_username = f"@{me['username']}" if me["username"] else me["display_name"]
    liked_username = f"@{liked_user['username']}" if liked_user["username"] else liked_user["display_name"]
    try:
        await context.bot.send_message(
            to_user_id,
            f"\u0423 \u0432\u0430\u0441 \u0432\u0437\u0430\u0438\u043c\u043d\u044b\u0439 \u043c\u044d\u0442\u0447! \u041d\u0430\u043f\u0438\u0448\u0438 \u043f\u0435\u0440\u0432\u044b\u043c: {me_username}",
        )
        await context.bot.send_message(
            user_id,
            f"\u0423 \u0432\u0430\u0441 \u0432\u0437\u0430\u0438\u043c\u043d\u044b\u0439 \u043c\u044d\u0442\u0447! \u041d\u0430\u043f\u0438\u0448\u0438 \u043f\u0435\u0440\u0432\u044b\u043c: {liked_username}",
        )
    except TelegramError as exc:
        logging.warning("Failed to send match message (%s <-> %s): %s", user_id, to_user_id, exc)


async def on_open_likes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    await query.answer()
    if query.message is None:
        return

    skipped_ids = context.user_data.setdefault(LIKES_SKIPPED_IDS_KEY, set())
    if not isinstance(skipped_ids, set):
        skipped_ids = set()
        context.user_data[LIKES_SKIPPED_IDS_KEY] = skipped_ids

    data = query.data or ""
    if data in ("open_likes", "likes:open"):
        skipped_ids.clear()
    else:
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != "likes":
            return
        action = parts[1]
        try:
            target_user_id = int(parts[2])
        except ValueError:
            return

        pending_ids = {row["user_id"] for row in get_pending_likes(user.id)}
        if target_user_id not in pending_ids and action in {"like", "block", "report"}:
            await query.answer("Р­С‚РѕС‚ Р»Р°Р№Рє СѓР¶Рµ РЅРµР°РєС‚СѓР°Р»РµРЅ.")
            return

        if action == "skip":
            skipped_ids.add(target_user_id)
        elif action == "block":
            block_user(user.id, target_user_id)
            skipped_ids.add(target_user_id)
        elif action == "report":
            ok, auto_paused, _, reason = report_user(user.id, target_user_id)
            if not ok:
                await query.answer(interaction_error_text(reason), show_alert=True)
                return
            if auto_paused:
                await query.answer("РџСЂРѕС„РёР»СЊ РІСЂРµРјРµРЅРЅРѕ СЃРєСЂС‹С‚ РґРѕ РїСЂРѕРІРµСЂРєРё Р¶Р°Р»РѕР±.")
            skipped_ids.add(target_user_id)
        elif action == "like":
            ok, is_match, reason = like_user(user.id, target_user_id)
            if not ok:
                await query.answer(interaction_error_text(reason), show_alert=True)
                return
            skipped_ids.add(target_user_id)
            if reason == "already_liked":
                await query.answer("\u0422\u044b \u0443\u0436\u0435 \u043b\u0430\u0439\u043a\u043d\u0443\u043b(\u0430) \u044d\u0442\u0443 \u0430\u043d\u043a\u0435\u0442\u0443")
            if is_match:
                await notify_match(context, user.id, target_user_id)

    row = get_next_pending_like(user.id, skipped_ids)
    if row is None:
        await query.message.reply_text("\u041d\u043e\u0432\u044b\u0445 \u043b\u0430\u0439\u043a\u043e\u0432 \u0431\u0435\u0437 \u043e\u0442\u0432\u0435\u0442\u0430 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.")
        return

    text = "рџ’Њ РўРµР±СЏ Р»Р°Р№РєРЅСѓР»Рё\n\n" + liker_profile_text(row)
    keyboard = likes_review_keyboard(row["user_id"])
    if row["photo_file_id"]:
        await query.message.reply_photo(row["photo_file_id"], caption=text, reply_markup=keyboard)
    else:
        await query.message.reply_text(text, reply_markup=keyboard)


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    if get_profile(user.id) is None:
        await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430. \u041d\u0430\u0436\u043c\u0438 /start.")
        return

    set_profile_active(user.id, False)
    await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0430 \u043d\u0430 \u043f\u0430\u0443\u0437\u0443.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    if get_profile(user.id) is None:
        await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430. \u041d\u0430\u0436\u043c\u0438 /start.")
        return

    set_profile_active(user.id, True)
    await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u0441\u043d\u043e\u0432\u0430 \u0430\u043a\u0442\u0438\u0432\u043d\u0430.")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    if get_profile(user.id) is None:
        await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u0443\u0436\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u0430 \u0438\u043b\u0438 \u043d\u0435 \u0441\u043e\u0437\u0434\u0430\u043d\u0430.")
        return

    delete_profile(user.id)
    clear_user_state(context)
    await update.message.reply_text("\u0410\u043d\u043a\u0435\u0442\u0430 \u0438 \u0441\u0432\u044f\u0437\u0430\u043d\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u044b.")


async def admin_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return

    admins = get_admin_ids()
    if user.id not in admins:
        await update.message.reply_text("\u0414\u043e\u0441\u0442\u0443\u043f \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d.")
        return

    users, active, likes, matches = get_admin_stats()
    await update.message.reply_text(
        f"\u0410\u0434\u043c\u0438\u043d-\u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430:\n"
        f"\u0412\u0441\u0435\u0433\u043e \u0430\u043d\u043a\u0435\u0442: {users}\n"
        f"\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0445: {active}\n"
        f"\u041b\u0430\u0439\u043a\u043e\u0432: {likes}\n"
        f"\u041c\u044d\u0442\u0447\u0435\u0439: {matches}"
    )


async def admin_reports_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    if user.id not in get_admin_ids():
        await message.reply_text("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return

    rows = get_reported_profiles(REPORTS_PAGE_LIMIT)
    if not rows:
        await message.reply_text("РћС‚РєСЂС‹С‚С‹С… Р¶Р°Р»РѕР± РЅРµС‚.")
        return

    lines = ["РћС‡РµСЂРµРґСЊ Р¶Р°Р»РѕР± (open):"]
    for row in rows:
        name = f"@{row['username']}" if row["username"] else row["display_name"]
        status = "active" if row["is_active"] == 1 else "paused"
        lines.append(
            f"- {row['to_user_id']} | {name} | reports={row['pending_reports']} | {status}"
        )
    lines.append("")
    lines.append("РљРѕРјР°РЅРґС‹: /admin_ban <id> [reason], /admin_unban <id>")
    await message.reply_text("\n".join(lines))


async def admin_ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    if user.id not in get_admin_ids():
        await message.reply_text("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return

    target_user_id = parse_admin_target(context.args)
    if target_user_id is None:
        await message.reply_text("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /admin_ban <user_id> [reason]")
        return

    reason = " ".join(context.args[1:]).strip()
    ok = admin_set_profile_active(user.id, target_user_id, False, reason)
    if not ok:
        await message.reply_text("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ.")
        return

    resolved_count = resolve_reports_for_user(user.id, target_user_id, "moderated_ban")
    await message.reply_text(
        f"РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ {target_user_id} Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ. Р—Р°РєСЂС‹С‚Рѕ Р¶Р°Р»РѕР±: {resolved_count}."
    )


async def admin_unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    if user.id not in get_admin_ids():
        await message.reply_text("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ.")
        return

    target_user_id = parse_admin_target(context.args)
    if target_user_id is None:
        await message.reply_text("РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ: /admin_unban <user_id> [reason]")
        return

    reason = " ".join(context.args[1:]).strip()
    ok = admin_set_profile_active(user.id, target_user_id, True, reason)
    if not ok:
        await message.reply_text("РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ.")
        return

    await message.reply_text(f"РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ {target_user_id} СЂР°Р·Р±Р»РѕРєРёСЂРѕРІР°РЅ.")


async def edit_bio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    if get_profile(user.id) is None:
        await message.reply_text("РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РќР°Р¶РјРё /start.")
        return
    set_user_state(context, {"step": State.WAIT_EDIT_BIO})
    await message.reply_text("РџСЂРёС€Р»Рё РЅРѕРІС‹Р№ С‚РµРєСЃС‚ bio (10-300 СЃРёРјРІРѕР»РѕРІ).")


async def edit_photo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    if get_profile(user.id) is None:
        await message.reply_text("РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РќР°Р¶РјРё /start.")
        return
    set_user_state(context, {"step": State.WAIT_EDIT_PHOTO})
    await message.reply_text(
        "РџСЂРёС€Р»Рё РЅРѕРІРѕРµ С„РѕС‚Рѕ. Р•СЃР»Рё РЅСѓР¶РЅРѕ СѓР±СЂР°С‚СЊ С„РѕС‚Рѕ РёР· Р°РЅРєРµС‚С‹, РёСЃРїРѕР»СЊР·СѓР№ /removephoto."
    )


async def remove_photo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    if get_profile(user.id) is None:
        await message.reply_text("РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РќР°Р¶РјРё /start.")
        return
    update_profile_photo(user.id, None)
    clear_user_state(context)
    await message.reply_text("Р¤РѕС‚Рѕ СѓРґР°Р»РµРЅРѕ РёР· Р°РЅРєРµС‚С‹.")


async def edit_filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    if get_profile(user.id) is None:
        await message.reply_text("РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РќР°Р¶РјРё /start.")
        return
    set_user_state(context, {"step": State.WAIT_EDIT_LOOKING_PICK})
    await message.reply_text("РљРѕРіРѕ РёС‰РµС€СЊ?", reply_markup=gender_keyboard("looking"))


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    clear_user_state(context)
    await update.message.reply_text("РўРµРєСѓС‰РµРµ РґРµР№СЃС‚РІРёРµ РѕС‚РјРµРЅРµРЅРѕ.")


async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    profile = get_profile(user.id)
    if profile is None:
        await message.reply_text("Анкета не найдена. Нажми /start.")
        return

    url = get_mini_app_url()
    if not url:
        await message.reply_text(
            "Mini App URL не настроен. Укажи TELEGRAM_MINI_APP_URL в окружении."
        )
        return

    # Передаем текущее состояние анкеты в mini-app через query params.
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "bio": profile["bio"] or "",
            "looking_for": profile["looking_for"] or "any",
            "min_age": str(profile["min_age"]),
            "max_age": str(profile["max_age"]),
            "is_active": "1" if profile["is_active"] == 1 else "0",
        }
    )
    api_base = get_mini_app_api_url()
    if api_base:
        query["api_base"] = api_base
    url_with_state = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment)
    )

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=url_with_state))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.reply_text(
        "Открой Mini App, чтобы редактировать bio, фильтры и статус анкеты.",
        reply_markup=keyboard,
    )


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if message is None or user is None or message.web_app_data is None:
        return

    profile = get_profile(user.id)
    if profile is None:
        await message.reply_text("РђРЅРєРµС‚Р° РЅРµ РЅР°Р№РґРµРЅР°. РЎРЅР°С‡Р°Р»Р° /start.")
        return

    raw = message.web_app_data.data
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        await message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ РїСЂРѕС‡РёС‚Р°С‚СЊ РґР°РЅРЅС‹Рµ Mini App.")
        return
    if not isinstance(payload, dict):
        await message.reply_text("РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚ РґР°РЅРЅС‹С… Mini App.")
        return

    updated_parts: list[str] = []

    bio_raw = payload.get("bio")
    if isinstance(bio_raw, str):
        valid, bio, err = validate_bio_input(bio_raw)
        if not valid:
            await message.reply_text(err)
            return
        update_profile_bio(user.id, bio)
        updated_parts.append("bio")

    looking_raw = payload.get("looking_for")
    min_age_raw = payload.get("min_age")
    max_age_raw = payload.get("max_age")
    if looking_raw is not None or min_age_raw is not None or max_age_raw is not None:
        if not isinstance(looking_raw, str) or looking_raw not in ALLOWED_GENDERS:
            await message.reply_text("РќРµРєРѕСЂСЂРµРєС‚РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ looking_for.")
            return
        min_age = parse_webapp_int(min_age_raw, 18, 99)
        max_age = parse_webapp_int(max_age_raw, 18, 99)
        if min_age is None or max_age is None or max_age < min_age:
            await message.reply_text("РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РІРѕР·СЂР°СЃС‚РЅРѕР№ С„РёР»СЊС‚СЂ.")
            return
        update_profile_filters(user.id, looking_raw, min_age, max_age)
        updated_parts.append("filters")

    is_active_raw = payload.get("is_active")
    if is_active_raw is not None:
        if not isinstance(is_active_raw, bool):
            await message.reply_text("РќРµРєРѕСЂСЂРµРєС‚РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ is_active.")
            return
        set_profile_active(user.id, is_active_raw)
        updated_parts.append("status")

    if not updated_parts:
        await message.reply_text("РќРµС‚ РёР·РјРµРЅРµРЅРёР№ РґР»СЏ РїСЂРёРјРµРЅРµРЅРёСЏ.")
        return

    await message.reply_text(f"РР·РјРµРЅРµРЅРёСЏ СЃРѕС…СЂР°РЅРµРЅС‹: {', '.join(updated_parts)}.")


def registration_profile_from_state(
    user_id: int,
    username: str,
    first_name: str,
    state_data: dict[str, Any],
    photo_file_id: Optional[str],
) -> Profile:
    return Profile(
        user_id=user_id,
        username=username or "",
        display_name=first_name or "\u0411\u0435\u0437 \u0438\u043c\u0435\u043d\u0438",
        age=int(state_data["age"]),
        city=state_data["city"],
        bio=state_data["bio"],
        gender=state_data["gender"],
        looking_for=state_data["looking_for"],
        min_age=int(state_data["min_age"]),
        max_age=int(state_data["max_age"]),
        photo_file_id=photo_file_id,
    )


async def finalize_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, photo_file_id: Optional[str]) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    state_data = get_user_state(context)
    if not state_data:
        await message.reply_text("\u0421\u043d\u0430\u0447\u0430\u043b\u0430 /start")
        return

    profile = registration_profile_from_state(
        user.id,
        user.username or "",
        user.first_name or "\u0411\u0435\u0437 \u0438\u043c\u0435\u043d\u0438",
        state_data,
        photo_file_id,
    )
    upsert_profile(profile)
    clear_user_state(context)

    await message.reply_text(
        "вњ… РђРЅРєРµС‚Р° РіРѕС‚РѕРІР°!\n\n"
        "Р§С‚Рѕ РґР°Р»СЊС€Рµ:\n"
        "/browse - СЃРјРѕС‚СЂРµС‚СЊ Р°РЅРєРµС‚С‹\n"
        "/likes - РєС‚Рѕ С‚РµР±СЏ Р»Р°Р№РєРЅСѓР»\n"
        "/profile - РјРѕСЏ Р°РЅРєРµС‚Р°\n"
        "/stats - СЃС‚Р°С‚РёСЃС‚РёРєР°\n"
        "/miniapp - РѕС‚РєСЂС‹С‚СЊ mini app\n"
        "/edit_bio /edit_photo /edit_filters\n"
        "/pause /resume /delete"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    state_data = get_user_state(context)
    if not state_data:
        await message.reply_text(
            "РљРѕРјР°РЅРґС‹: /start, /browse, /profile, /likes, /stats, /miniapp, /edit_bio, /edit_photo, /edit_filters"
        )
        return

    step = state_data["step"]
    text = (message.text or "").strip()

    if step == State.WAIT_AGE:
        age = parse_int_in_range(text, 18, 99)
        if age is None:
            await message.reply_text("РќСѓР¶РµРЅ РІРѕР·СЂР°СЃС‚ С‡РёСЃР»РѕРј РѕС‚ 18 РґРѕ 99.")
            return

        state_data["age"] = age
        state_data["step"] = State.WAIT_CITY
        await message.reply_text("РЁР°Рі 2/6: РёР· РєР°РєРѕРіРѕ С‚С‹ РіРѕСЂРѕРґР°?")
        return

    if step == State.WAIT_CITY:
        is_valid_city, city, city_error = validate_city_input(text)
        if not is_valid_city:
            await message.reply_text(city_error)
            return

        state_data["city"] = city
        state_data["step"] = State.WAIT_GENDER_PICK
        await message.reply_text("РЁР°Рі 3/6: РІС‹Р±РµСЂРё СЃРІРѕР№ РїРѕР».", reply_markup=gender_keyboard("gender"))
        return

    if step == State.WAIT_MIN_AGE:
        min_age = parse_int_in_range(text, 18, 99)
        if min_age is None:
            await message.reply_text("Р’РІРµРґРё С‡РёСЃР»Рѕ РѕС‚ 18 РґРѕ 99.")
            return

        state_data["min_age"] = min_age
        state_data["step"] = State.WAIT_MAX_AGE
        await message.reply_text("РЁР°Рі 5/6: РјР°РєСЃРёРјР°Р»СЊРЅС‹Р№ РІРѕР·СЂР°СЃС‚ (18-99)?")
        return

    if step == State.WAIT_MAX_AGE:
        max_age = parse_int_in_range(text, 18, 99)
        if max_age is None:
            await message.reply_text("Р’РІРµРґРё С‡РёСЃР»Рѕ РѕС‚ 18 РґРѕ 99.")
            return

        min_age = int(state_data["min_age"])
        if max_age < min_age:
            await message.reply_text(f"\u041c\u0430\u043a\u0441\u0438\u043c\u0443\u043c \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c >= {min_age} \u0438 <= 99.")
            return

        state_data["max_age"] = max_age
        state_data["step"] = State.WAIT_BIO
        await message.reply_text("РЁР°Рі 6/6: РєРѕСЂРѕС‚РєРѕ Рѕ СЃРµР±Рµ (РґРѕ 300 СЃРёРјРІРѕР»РѕРІ).")
        return

    if step == State.WAIT_BIO:
        is_valid_bio, bio, bio_error = validate_bio_input(text)
        if not is_valid_bio:
            await message.reply_text(bio_error)
            return

        state_data["bio"] = bio
        state_data["step"] = State.WAIT_PHOTO
        await message.reply_text("Р¤РёРЅР°Р»СЊРЅС‹Р№ С€Р°Рі: РѕС‚РїСЂР°РІСЊ 1 С„РѕС‚Рѕ Р°РЅРєРµС‚С‹ РёР»Рё /skipphoto.")
        return

    if step == State.WAIT_EDIT_BIO:
        is_valid_bio, bio, bio_error = validate_bio_input(text)
        if not is_valid_bio:
            await message.reply_text(bio_error)
            return
        update_profile_bio(user.id, bio)
        clear_user_state(context)
        await message.reply_text("Bio РѕР±РЅРѕРІР»РµРЅ.")
        return

    if step == State.WAIT_EDIT_MIN_AGE:
        min_age = parse_int_in_range(text, 18, 99)
        if min_age is None:
            await message.reply_text("Р’РІРµРґРё С‡РёСЃР»Рѕ 18-99.")
            return
        state_data["min_age"] = min_age
        state_data["step"] = State.WAIT_EDIT_MAX_AGE
        await message.reply_text("РњР°РєСЃРёРјР°Р»СЊРЅС‹Р№ РІРѕР·СЂР°СЃС‚ (18-99):")
        return

    if step == State.WAIT_EDIT_MAX_AGE:
        max_age = parse_int_in_range(text, 18, 99)
        if max_age is None:
            await message.reply_text("Р’РІРµРґРё С‡РёСЃР»Рѕ 18-99.")
            return

        min_age = int(state_data["min_age"])
        if max_age < min_age:
            await message.reply_text(f"РњР°РєСЃРёРјСѓРј РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= {min_age}.")
            return

        looking_for = state_data.get("looking_for")
        if not isinstance(looking_for, str) or looking_for not in ALLOWED_GENDERS:
            await message.reply_text("РќРµ СѓРґР°Р»РѕСЃСЊ РѕР±РЅРѕРІРёС‚СЊ С„РёР»СЊС‚СЂС‹. РџРѕРІС‚РѕСЂРё /edit_filters.")
            clear_user_state(context)
            return

        update_profile_filters(user.id, looking_for, min_age, max_age)
        clear_user_state(context)
        await message.reply_text("Р¤РёР»СЊС‚СЂС‹ РѕР±РЅРѕРІР»РµРЅС‹.")
        return

    if step in (State.WAIT_GENDER_PICK, State.WAIT_LOOKING_PICK, State.WAIT_EDIT_LOOKING_PICK):
        await message.reply_text("Р’С‹Р±РµСЂРё РІР°СЂРёР°РЅС‚ РєРЅРѕРїРєР°РјРё РІС‹С€Рµ.")
        return

    if step == State.WAIT_PHOTO:
        await message.reply_text("РћС‚РїСЂР°РІСЊ С„РѕС‚Рѕ РёР»Рё РёСЃРїРѕР»СЊР·СѓР№ /skipphoto.")
        return

    if step == State.WAIT_EDIT_PHOTO:
        await message.reply_text("РћС‚РїСЂР°РІСЊ С„РѕС‚Рѕ РёР»Рё РёСЃРїРѕР»СЊР·СѓР№ /removephoto.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.photo:
        return

    state_data = get_user_state(context)
    step = state_data.get("step") if state_data else None
    if step == State.WAIT_EDIT_PHOTO:
        file_id = message.photo[-1].file_id
        update_profile_photo(user.id, file_id)
        clear_user_state(context)
        await message.reply_text("Р¤РѕС‚Рѕ Р°РЅРєРµС‚С‹ РѕР±РЅРѕРІР»РµРЅРѕ.")
        return

    if step != State.WAIT_PHOTO:
        await message.reply_text("\u0424\u043e\u0442\u043e \u0443\u0447\u0438\u0442\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u043f\u043e\u0441\u043b\u0435 /start \u043f\u0440\u0438 \u0441\u043e\u0437\u0434\u0430\u043d\u0438\u0438 \u0430\u043d\u043a\u0435\u0442\u044b.")
        return

    file_id = message.photo[-1].file_id
    await finalize_profile(update, context, file_id)


async def skip_photo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return

    state_data = get_user_state(context)
    if not state_data or state_data.get("step") != State.WAIT_PHOTO:
        await message.reply_text("\u0421\u0435\u0439\u0447\u0430\u0441 \u044d\u0442\u043e\u0442 \u0448\u0430\u0433 \u043d\u0435\u0430\u043a\u0442\u0443\u0430\u043b\u0435\u043d.")
        return

    await finalize_profile(update, context, None)


async def on_setup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()

    state_data = get_user_state(context)
    if not state_data:
        await query.answer("\u0421\u043d\u0430\u0447\u0430\u043b\u0430 /start", show_alert=True)
        return

    try:
        _, field, value = query.data.split(":", 2)
    except (ValueError, AttributeError):
        return

    if value not in ALLOWED_GENDERS:
        return

    if field == "gender":
        if state_data.get("step") != State.WAIT_GENDER_PICK:
            await query.answer("\u0421\u0435\u0439\u0447\u0430\u0441 \u043d\u0435 \u044d\u0442\u043e\u0442 \u0448\u0430\u0433", show_alert=True)
            return
        state_data["gender"] = value
        state_data["step"] = State.WAIT_LOOKING_PICK
        await query.message.edit_text("РЁР°Рі 4/6: РєРѕРіРѕ РёС‰РµС€СЊ?", reply_markup=gender_keyboard("looking"))
        return

    if field == "looking":
        step = state_data.get("step")
        if step == State.WAIT_LOOKING_PICK:
            state_data["looking_for"] = value
            state_data["step"] = State.WAIT_MIN_AGE
            await query.message.edit_text("РЁР°Рі 5/6: РјРёРЅРёРјР°Р»СЊРЅС‹Р№ РІРѕР·СЂР°СЃС‚ (18-99)?")
            return
        if step == State.WAIT_EDIT_LOOKING_PICK:
            state_data["looking_for"] = value
            state_data["step"] = State.WAIT_EDIT_MIN_AGE
            await query.message.edit_text("РњРёРЅРёРјР°Р»СЊРЅС‹Р№ РІРѕР·СЂР°СЃС‚ (18-99):")
            return
        await query.answer("\u0421\u0435\u0439\u0447\u0430\u0441 \u043d\u0435 \u044d\u0442\u043e\u0442 \u0448\u0430\u0433", show_alert=True)


async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    await query.answer()

    if not action_allowed(context):
        await query.answer("\u0421\u043b\u0438\u0448\u043a\u043e\u043c \u0431\u044b\u0441\u0442\u0440\u043e, \u043f\u043e\u0434\u043e\u0436\u0434\u0438 \u0441\u0435\u043a\u0443\u043d\u0434\u0443", show_alert=True)
        return

    try:
        action, to_user_raw = query.data.split(":", 1)
        to_user_id = int(to_user_raw)
    except (ValueError, AttributeError):
        return
    if action not in {"like", "skip", "block", "report"}:
        return

    if to_user_id == user.id:
        await query.answer("\u041d\u0435\u043b\u044c\u0437\u044f \u0432\u0437\u0430\u0438\u043c\u043e\u0434\u0435\u0439\u0441\u0442\u0432\u043e\u0432\u0430\u0442\u044c \u0441\u043e \u0441\u0432\u043e\u0435\u0439 \u0430\u043d\u043a\u0435\u0442\u043e\u0439", show_alert=True)
        return

    if not was_viewed(user.id, to_user_id):
        await query.answer("Р­С‚Р° РєР°СЂС‚РѕС‡РєР° СѓСЃС‚Р°СЂРµР»Р°. РџРѕРєР°Р¶Сѓ СЃР»РµРґСѓСЋС‰СѓСЋ.", show_alert=True)
        await browse(update, context, edit_current=True)
        return

    ok, reason = can_interact(user.id, to_user_id)
    if not ok:
        await query.answer(interaction_error_text(reason), show_alert=True)
        await browse(update, context, edit_current=True)
        return

    if action == "like":
        ok, is_match, reason = like_user(user.id, to_user_id)
        if not ok:
            await query.answer(interaction_error_text(reason), show_alert=True)
            return

        if reason == "already_liked":
            await query.answer("\u0422\u044b \u0443\u0436\u0435 \u043b\u0430\u0439\u043a\u043d\u0443\u043b(\u0430) \u044d\u0442\u0443 \u0430\u043d\u043a\u0435\u0442\u0443")

        if reason == "ok" and not is_match:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("\u041f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c", callback_data="likes:open")]]
            )
            try:
                await context.bot.send_message(
                    to_user_id,
                    "\u0422\u0435\u0431\u044f \u043b\u0430\u0439\u043a\u043d\u0443\u043b\u0438! "
                    "\u0427\u0442\u043e\u0431\u044b \u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c \u0430\u043d\u043a\u0435\u0442\u0443, \u043d\u0430\u0436\u043c\u0438 \u043a\u043d\u043e\u043f\u043a\u0443 \u043d\u0438\u0436\u0435.",
                    reply_markup=keyboard,
                )
            except TelegramError as exc:
                logging.warning("Failed to send like notification (%s -> %s): %s", user.id, to_user_id, exc)

        if is_match:
            await notify_match(context, user.id, to_user_id)

    if action == "block":
        block_user(user.id, to_user_id)
        logging.info("User %s blocked %s", user.id, to_user_id)

    if action == "report":
        ok, auto_paused, _, reason = report_user(user.id, to_user_id)
        if not ok:
            await query.answer(interaction_error_text(reason), show_alert=True)
            return
        if auto_paused:
            await query.answer("РџСЂРѕС„РёР»СЊ РІСЂРµРјРµРЅРЅРѕ СЃРєСЂС‹С‚ РґРѕ РїСЂРѕРІРµСЂРєРё Р¶Р°Р»РѕР±.")
        logging.info("User %s reported %s", user.id, to_user_id)

    await browse(update, context, edit_current=True)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    await update.message.reply_text(
        "рџ“љ РљРѕРјР°РЅРґС‹\n\n"
        "РџСЂРѕС„РёР»СЊ:\n"
        "/start - СЃРѕР·РґР°С‚СЊ/РѕР±РЅРѕРІРёС‚СЊ Р°РЅРєРµС‚Сѓ\n"
        "/profile - РјРѕСЏ Р°РЅРєРµС‚Р°\n"
        "/edit_bio - РёР·РјРµРЅРёС‚СЊ bio\n"
        "/edit_photo - РёР·РјРµРЅРёС‚СЊ С„РѕС‚Рѕ\n"
        "/removephoto - СѓР±СЂР°С‚СЊ С„РѕС‚Рѕ\n"
        "/edit_filters - РёР·РјРµРЅРёС‚СЊ С„РёР»СЊС‚СЂС‹\n\n"
        "РџРѕРёСЃРє:\n"
        "/browse - СЃРјРѕС‚СЂРµС‚СЊ Р°РЅРєРµС‚С‹\n"
        "/likes - РєС‚Рѕ С‚РµР±СЏ Р»Р°Р№РєРЅСѓР»\n"
        "/stats - СЃС‚Р°С‚РёСЃС‚РёРєР°\n\n"
        "Mini App:\n"
        "/miniapp - РѕС‚РєСЂС‹С‚СЊ web-РёРЅС‚РµСЂС„РµР№СЃ\n\n"
        "РЈРїСЂР°РІР»РµРЅРёРµ:\n"
        "/pause - СЃРєСЂС‹С‚СЊ Р°РЅРєРµС‚Сѓ\n"
        "/resume - РІРєР»СЋС‡РёС‚СЊ Р°РЅРєРµС‚Сѓ\n"
        "/delete - СѓРґР°Р»РёС‚СЊ Р°РЅРєРµС‚Сѓ\n"
        "/cancel - РѕС‚РјРµРЅРёС‚СЊ С‚РµРєСѓС‰РµРµ РґРµР№СЃС‚РІРёРµ\n"
        "/skipphoto - РїСЂРѕРїСѓСЃС‚РёС‚СЊ С„РѕС‚Рѕ РїСЂРё СЂРµРіРёСЃС‚СЂР°С†РёРё"
    )

