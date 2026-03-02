import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl

from flask import Flask, jsonify, redirect, request, send_from_directory

from config import load_token
from db import (
    get_profile,
    init_db,
    set_profile_active,
    update_profile_bio,
    update_profile_filters,
)

app = Flask(__name__)
MINIAPP_DIR = Path(__file__).resolve().parent / "miniapp"


def json_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def parse_int_in_range(raw: Any, min_value: int, max_value: int) -> Optional[int]:
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isdigit():
        value = int(raw)
    else:
        return None
    if min_value <= value <= max_value:
        return value
    return None


def validate_bio_input(raw: str) -> tuple[bool, str, str]:
    bio = " ".join(raw.split())
    if len(bio) < 10:
        return False, bio, "Bio должен быть не короче 10 символов."
    if len(bio) > 300:
        return False, bio, "Максимум 300 символов."
    if not any(ch.isalpha() for ch in bio):
        return False, bio, "Bio должен содержать хотя бы одну букву."
    return True, bio, ""


def verify_init_data(init_data: str, bot_token: str) -> Optional[int]:
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    hash_value = data.pop("hash", None)
    if not hash_value:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    computed = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(computed, hash_value):
        return None

    user_raw = data.get("user")
    if not user_raw:
        return None
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError:
        return None
    user_id = user.get("id")
    if isinstance(user_id, int):
        return user_id
    return None


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/", methods=["GET"])
def root():
    return redirect("/miniapp/index.html", code=302)


@app.route("/miniapp", methods=["GET"])
def miniapp_root():
    return redirect("/miniapp/index.html", code=302)


@app.route("/miniapp/<path:filename>", methods=["GET"])
def miniapp_static(filename: str):
    return send_from_directory(MINIAPP_DIR, filename)


@app.route("/api/miniapp/profile/load", methods=["POST", "OPTIONS"])
def load_profile():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(silent=True) or {}
    init_data = body.get("init_data")
    if not isinstance(init_data, str) or not init_data.strip():
        return json_error("init_data is required")

    bot_token = load_token()
    if not bot_token:
        return json_error("Bot token is not configured", 500)

    user_id = verify_init_data(init_data, bot_token)
    if user_id is None:
        return json_error("Invalid init_data", 403)

    row = get_profile(user_id)
    if row is None:
        return jsonify({"ok": True, "exists": False, "profile": None})

    profile = {
        "bio": row["bio"] or "",
        "looking_for": row["looking_for"] or "any",
        "min_age": int(row["min_age"]),
        "max_age": int(row["max_age"]),
        "is_active": row["is_active"] == 1,
    }
    return jsonify({"ok": True, "exists": True, "profile": profile})


@app.route("/api/miniapp/profile/save", methods=["POST", "OPTIONS"])
def save_profile():
    if request.method == "OPTIONS":
        return ("", 204)

    body = request.get_json(silent=True) or {}
    init_data = body.get("init_data")
    payload = body.get("payload")
    if not isinstance(init_data, str) or not init_data.strip():
        return json_error("init_data is required")
    if not isinstance(payload, dict):
        return json_error("payload must be an object")

    bot_token = load_token()
    if not bot_token:
        return json_error("Bot token is not configured", 500)

    user_id = verify_init_data(init_data, bot_token)
    if user_id is None:
        return json_error("Invalid init_data", 403)

    row = get_profile(user_id)
    if row is None:
        return json_error("Profile not found", 404)

    bio_raw = payload.get("bio")
    if isinstance(bio_raw, str):
        valid, bio, err = validate_bio_input(bio_raw)
        if not valid:
            return json_error(err)
        update_profile_bio(user_id, bio)

    looking_for = payload.get("looking_for")
    min_age = parse_int_in_range(payload.get("min_age"), 18, 99)
    max_age = parse_int_in_range(payload.get("max_age"), 18, 99)
    if looking_for is not None or min_age is not None or max_age is not None:
        if not isinstance(looking_for, str) or looking_for not in {"male", "female", "any"}:
            return json_error("Invalid looking_for")
        if min_age is None or max_age is None or max_age < min_age:
            return json_error("Invalid age range")
        update_profile_filters(user_id, looking_for, min_age, max_age)

    is_active = payload.get("is_active")
    if is_active is not None:
        if not isinstance(is_active, bool):
            return json_error("Invalid is_active")
        set_profile_active(user_id, is_active)

    updated = get_profile(user_id)
    if updated is None:
        return json_error("Profile not found", 404)

    return jsonify(
        {
            "ok": True,
            "profile": {
                "bio": updated["bio"] or "",
                "looking_for": updated["looking_for"] or "any",
                "min_age": int(updated["min_age"]),
                "max_age": int(updated["max_age"]),
                "is_active": updated["is_active"] == 1,
            },
        }
    )


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
