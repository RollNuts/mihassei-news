"""Small, public biographies for the fictional cast, separate from frozen issues."""
from __future__ import annotations

from datetime import date
import re
import unicodedata


FICTION_AS_OF_MONTH = "2031-11"
BIOGRAPHY_FIELDS = {"hometown", "career", "strength", "difficulty", "habit"}
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
LINK = re.compile(r"(?:https?://|javascript:|data:|www\.)", re.IGNORECASE)


def _plain_text(value, field, limit):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > limit or any(char in "<>" for char in value)
            or any(unicodedata.category(char).startswith("C") for char in value)
            or LINK.search(value)):
        raise ValueError("Invalid biography plaintext: " + field)
    return value


def _month(value, field):
    if not isinstance(value, str) or not MONTH.fullmatch(value):
        raise ValueError("Invalid biography month: " + field)
    try:
        date.fromisoformat(value + "-01")
    except ValueError as error:
        raise ValueError("Invalid biography month: " + field) from error
    return value


def validate_biography(value, *, context="biography", as_of_month=FICTION_AS_OF_MONTH):
    """Validate optional legacy data; return only fresh, public plaintext fields."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != BIOGRAPHY_FIELDS:
        raise ValueError("Invalid biography fields: " + context)
    as_of_month = _month(as_of_month, context + ".as_of_month")
    career = value["career"]
    if not isinstance(career, list) or not 2 <= len(career) <= 3:
        raise ValueError("Biography career must contain two or three entries: " + context)
    result = {"hometown": _plain_text(value["hometown"], context + ".hometown", 60),
              "career": []}
    previous = ""
    for index, entry in enumerate(career):
        field = context + ".career[" + str(index) + "]"
        if not isinstance(entry, dict) or set(entry) != {"month", "text"}:
            raise ValueError("Invalid biography career fields: " + field)
        month = _month(entry["month"], field)
        if month <= previous or month > as_of_month:
            raise ValueError("Biography career must be chronological and already occur: " + field)
        result["career"].append({"month": month,
                                 "text": _plain_text(entry["text"], field + ".text", 180)})
        previous = month
    for field in ("strength", "difficulty", "habit"):
        result[field] = _plain_text(value[field], context + "." + field, 160)
    return result


def public_biography(model):
    """Keep references, appearance instructions and body designs out of the view."""
    return validate_biography(model.get("biography"), context=str(model.get("id", "model")))
