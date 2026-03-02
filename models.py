from dataclasses import dataclass
from typing import Optional


@dataclass
class Profile:
    user_id: int
    username: str
    display_name: str
    age: int
    city: str
    bio: str
    gender: str
    looking_for: str
    min_age: int
    max_age: int
    photo_file_id: Optional[str]


class State:
    WAIT_AGE = "wait_age"
    WAIT_CITY = "wait_city"
    WAIT_GENDER_PICK = "wait_gender_pick"
    WAIT_LOOKING_PICK = "wait_looking_pick"
    WAIT_MIN_AGE = "wait_min_age"
    WAIT_MAX_AGE = "wait_max_age"
    WAIT_BIO = "wait_bio"
    WAIT_PHOTO = "wait_photo"
    WAIT_EDIT_BIO = "wait_edit_bio"
    WAIT_EDIT_PHOTO = "wait_edit_photo"
    WAIT_EDIT_LOOKING_PICK = "wait_edit_looking_pick"
    WAIT_EDIT_MIN_AGE = "wait_edit_min_age"
    WAIT_EDIT_MAX_AGE = "wait_edit_max_age"


ALLOWED_GENDERS = {"male", "female", "any"}
