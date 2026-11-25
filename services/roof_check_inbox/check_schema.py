"""Shape of a batch of answers from the roof check page (check_roofs.html).

Shared by the inbox (services/roof_check_inbox/main.py) and
tests/test_roof_check.py. Pure functions only.

A batch is one round of cards from one volunteer. The volunteer id is a
random token the page makes and keeps in the browser: no name, email or
address is asked for or kept. Card ids are opaque hex (the deck's answer
key, kept private, says which roof and aspect each one is), so nothing
here can be traced to a building without the key.
"""
import re

ASPECTS = ("outline", "lines", "obstructions", "panels")
ANSWERS = ("right", "wrong", "unsure")
# Fault chips the page offers, by aspect (a subset of tools/mark_faults.py FAULTS).
FAULTS = ("outline_wrong", "off_roof", "line_wrong", "line_missing", "line_extra",
          "missed_obstruction", "not_obstruction", "obstruction", "ridge",
          "add_panels", "remove", "wrong_way")
MAX_NOTE = 1000
MAX_ANSWERS = 40
MAX_SPOTS = 12
ALLOWED_ORIGINS = (
    "https://rewiring-nz.github.io",
    "http://localhost",
    "http://127.0.0.1",
)
_HEX = re.compile(r"^[0-9a-f]{8,32}$")
_DECK = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


class BadBatch(ValueError):
    pass


def origin_allowed(origin):
    if not origin:
        return False
    return any(origin == o or origin.startswith(o + ":") for o in ALLOWED_ORIGINS)


def _text(v, cap):
    s = "" if v is None else str(v)
    return s.replace("\r\n", "\n").replace("\x00", "")[:cap].strip()


def _spots(v):
    out = []
    for p in (v or [])[:MAX_SPOTS]:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= x <= 1 and 0 <= y <= 1:   # a fraction of the photo, not coordinates
            out.append([round(x, 4), round(y, 4)])
    return out


def clean(payload):
    """Check a posted batch and return only the fields kept. Raises BadBatch."""
    if not isinstance(payload, dict):
        raise BadBatch("not an object")
    if payload.get("website"):
        raise BadBatch("honeypot")
    who = str(payload.get("volunteer") or "")
    if not _HEX.match(who):
        raise BadBatch("bad volunteer id")
    deck = str(payload.get("deck") or "")
    if not _DECK.match(deck):
        raise BadBatch("bad deck")
    raw = payload.get("answers")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ANSWERS:
        raise BadBatch("bad answers")
    answers = []
    for a in raw:
        if not isinstance(a, dict) or not _HEX.match(str(a.get("card") or "")):
            raise BadBatch("bad card")
        if a.get("answer") not in ANSWERS:
            raise BadBatch("bad answer")
        ea = a.get("else_aspect")
        try:
            ms = max(0, min(int(a.get("ms") or 0), 3_600_000))
        except (TypeError, ValueError):
            ms = 0
        answers.append(dict(
            card=a["card"], answer=a["answer"],
            faults=[f for f in (a.get("faults") or []) if f in FAULTS][:len(FAULTS)],
            spots=_spots(a.get("spots")),
            note=_text(a.get("note"), MAX_NOTE),
            else_aspect=ea if ea in ASPECTS + ("other",) else None,
            else_note=_text(a.get("else_note"), MAX_NOTE),
            ms=ms))
    return dict(volunteer=who, deck=deck, answers=answers)
