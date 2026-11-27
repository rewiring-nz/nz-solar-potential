"""Shape of a problem report sent from the map, and the GitHub issue it becomes.

Shared by the inbox service (services/report_inbox/main.py), which checks a
report and stores it, and the GitHub Action (tools/user_reports.py), which
turns stored reports into public issues. Pure functions only, so both sides
and tests/test_user_reports.py run the same rules.

Reports are public on GitHub as soon as they are filed, so everything a
visitor types is treated as untrusted text: lengths are capped, @mentions are
broken so a report cannot notify anyone, and the note is quoted, never
rendered as the issue's own markup.
"""
import base64
import re

KINDS = ("note", "flag", "correction")
LABEL = "user-report"
MAX_NOTE = 2000          # characters a visitor can type
MAX_CONTEXT = 6000       # building, view, settings, panel centres
MAX_SCREENSHOT = 400_000 # bytes of JPEG after base64 decoding
ALLOWED_ORIGINS = (
    "https://rewiring-nz.github.io",
    "http://localhost",
    "http://127.0.0.1",
)

_TITLE_MARK = "[map report] building "


class BadReport(ValueError):
    pass


def origin_allowed(origin):
    """True for the published site and for local previews on any port."""
    if not origin:
        return False
    return any(origin == o or origin.startswith(o + ":") for o in ALLOWED_ORIGINS)


def _text(v, cap):
    s = "" if v is None else str(v)
    s = s.replace("\r\n", "\n").replace("\x00", "")
    return s[:cap].strip()


def clean(payload):
    """Check a posted report and return only the fields we keep.

    Raises BadReport for anything a real map page would not send. The
    honeypot field `website` is hidden from people; a form-filling bot
    fills it in.
    """
    if not isinstance(payload, dict):
        raise BadReport("not an object")
    if payload.get("website"):
        raise BadReport("honeypot")
    kind = payload.get("kind")
    if kind not in KINDS:
        raise BadReport("unknown kind")
    note = _text(payload.get("note"), MAX_NOTE)
    if not note:
        raise BadReport("empty note")
    bid = payload.get("building_id")
    if bid in ("", None):
        bid = None
    else:
        try:
            bid = int(bid)
        except (TypeError, ValueError):
            raise BadReport("bad building id")
        if bid <= 0:
            bid = None
    view = payload.get("view") or {}
    try:
        view = {k: round(float(view[k]), 6 if k != "z" else 1) for k in ("lat", "lng", "z")}
    except (KeyError, TypeError, ValueError):
        view = None
    if view and not (-48 <= view["lat"] <= -34 and 166 <= view["lng"] <= 179):
        view = None  # outside New Zealand: keep the report, drop the link
    shot = None
    s = payload.get("screenshot")
    if isinstance(s, str) and s.startswith("data:image/jpeg;base64,"):
        try:
            raw = base64.b64decode(s.split(",", 1)[1], validate=True)
        except (ValueError, base64.binascii.Error):
            raw = b""
        if raw[:3] == b"\xff\xd8\xff" and len(raw) <= MAX_SCREENSHOT:
            shot = raw
    return {
        "kind": kind,
        "note": note,
        "building_id": bid,
        "address": _text(payload.get("address"), 200) or None,
        "view": view,
        "context": _text(payload.get("context"), MAX_CONTEXT),
        "data_version": _text(payload.get("data_version"), 20) or None,
        "page": _text(payload.get("page"), 300) or None,
        "screenshot": shot,
    }


def _defang(s):
    # A zero-width space after @ stops GitHub turning @name into a mention;
    # the text still reads the same.
    return s.replace("@", "@​")


def _quote(s):
    return "\n".join("> " + line if line else ">" for line in _defang(s).split("\n"))


KIND_WORDS = {"note": "Problem report", "flag": "Wrong layout flagged",
              "correction": "Panel layout corrected by hand"}


def title_for(r):
    if r["building_id"]:
        where = f" ({r['address']})" if r["address"] else ""
        return f"{_TITLE_MARK}{r['building_id']}{_defang(where)}"
    first = r["note"].split("\n", 1)[0][:70]
    return "[map report] " + _defang(first)


def title_building(title):
    """The building id a report issue's title names, or None."""
    m = re.match(re.escape(_TITLE_MARK) + r"(\d+)", title or "")
    return int(m.group(1)) if m else None


def map_link(r, site="https://rewiring-nz.github.io/nz-solar-potential/"):
    v = r["view"]
    if not v:
        return None
    q = f"rooftop-solar.html?lat={v['lat']}&lng={v['lng']}&z={v['z']}"
    if r["building_id"]:
        q += f"&b={r['building_id']}"
    return site + q


def body_for(r, received, screenshot_url=None):
    """Markdown for an issue (first report on a building) or a comment."""
    lines = [f"**{KIND_WORDS[r['kind']]}** from a visitor to the map, {received}.", "", _quote(r["note"]), ""]
    if screenshot_url:
        lines += [f"![What they were looking at]({screenshot_url})", ""]
    facts = []
    if r["building_id"]:
        facts.append(f"Building `{r['building_id']}`" + (f", {_defang(r['address'])}" if r["address"] else ""))
    link = map_link(r)
    if link:
        facts.append(f"[Open this view on the map]({link})")
    if r["data_version"]:
        facts.append(f"Data version {r['data_version']}")
    if facts:
        lines += [" · ".join(facts), ""]
    if r["context"]:
        ctx = _defang(r["context"]).replace("```", "'''")
        lines += ["<details><summary>Map state when sent</summary>", "", "```", ctx, "```", "", "</details>", ""]
    lines.append("<sub>Filed automatically from the map's Report button. "
                 "Reports are public; see [how they are triaged](https://github.com/rewiring-nz/nz-solar-potential/blob/main/docs/maintainers/user-reports.md).</sub>")
    return "\n".join(lines)
