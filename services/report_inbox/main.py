"""Inbox for problem reports from the map: check, store, return.

Deployed as a Cloud Run function (tools/deploy_report_inbox.sh). It holds no
GitHub credentials: it only writes each accepted report to the private data
bucket, and the GitHub Action .github/workflows/user-reports.yml turns stored
reports into public issues with the repository's own token.

Limits, all in memory on the single instance (max-instances 1):
  - 20 reports an hour from one address, 300 a day in all;
  - only the published site and local previews may post (Origin header);
  - the request body is capped at 700 KB (a 400 KB screenshot in base64).
"""
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import functions_framework
from google.cloud import storage

import report_schema as rs

DATA_BUCKET = os.environ.get("REPORT_DATA_BUCKET", "solar-potential-map-data")
SHOT_BUCKET = os.environ.get("REPORT_SHOT_BUCKET", "solar-potential-map-tiles")
INBOX = "user-reports/inbox/"
SHOTS = "user-reports/"
PER_ADDRESS_HOUR = 20
PER_DAY = 300
MAX_BODY = 700_000

_by_addr = defaultdict(deque)
_day = {"date": None, "n": 0}
_client = None


def _gcs():
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def _limited(addr, now):
    q = _by_addr[addr]
    while q and now - q[0] > 3600:
        q.popleft()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    if _day["date"] != today:
        _day.update(date=today, n=0)
    if len(q) >= PER_ADDRESS_HOUR or _day["n"] >= PER_DAY:
        return True
    q.append(now)
    _day["n"] += 1
    return False


def _cors(origin):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "3600",
        "Vary": "Origin",
    }


@functions_framework.http
def receive(request):
    origin = request.headers.get("Origin", "")
    if not rs.origin_allowed(origin):
        return ("origin not allowed", 403)
    headers = _cors(origin)
    if request.method == "OPTIONS":
        return ("", 204, headers)
    if request.method != "POST":
        return ("POST only", 405, headers)
    if (request.content_length or 0) > MAX_BODY:
        return ("too large", 413, headers)
    try:
        report = rs.clean(json.loads(request.get_data(as_text=True) or "null"))
    except (ValueError, rs.BadReport) as e:
        # A honeypot hit gets the same answer as success, so a bot learns nothing.
        if str(e) == "honeypot":
            return (json.dumps({"ok": True}), 200, {**headers, "Content-Type": "application/json"})
        return (json.dumps({"ok": False, "error": str(e)}), 400, {**headers, "Content-Type": "application/json"})
    addr = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")
    now = time.time()
    if _limited(addr, now):
        return (json.dumps({"ok": False, "error": "too many reports, try again later"}), 429,
                {**headers, "Content-Type": "application/json"})

    received = datetime.now(timezone.utc)
    rid = received.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    shot = report.pop("screenshot")
    if shot:
        b = _gcs().bucket(SHOT_BUCKET).blob(f"{SHOTS}{rid}.jpg")
        b.cache_control = "public, max-age=31536000, immutable"
        b.upload_from_string(shot, content_type="image/jpeg")
        report["screenshot_url"] = f"https://storage.googleapis.com/{SHOT_BUCKET}/{SHOTS}{rid}.jpg"
    report["id"] = rid
    report["received"] = received.isoformat(timespec="seconds")
    _gcs().bucket(DATA_BUCKET).blob(f"{INBOX}{rid}.json").upload_from_string(
        json.dumps(report, ensure_ascii=False), content_type="application/json")
    return (json.dumps({"ok": True, "id": rid}), 200, {**headers, "Content-Type": "application/json"})
