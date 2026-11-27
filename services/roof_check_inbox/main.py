"""Inbox for answers from the roof check page: check, store, return.

The same shape as the map's problem-report inbox (services/report_inbox/,
PR #76): a Cloud Run function with no other credentials than writing to the
private data bucket. One JSON file per round under roof-checks/inbox/;
tools/roof_check.py tally reads them. Answers are never published as they
come in: until a tally they are only evidence.

Limits, in memory on the single instance (max-instances 1): 60 rounds an
hour from one address, 3,000 rounds a day in all; only the published site
and local previews may post; the body is capped at 64 KB.
Deploy: gcloud functions deploy roof-check-inbox --gen2 --runtime python312
  --region australia-southeast1 --source services/roof_check_inbox
  --entry-point receive --trigger-http --allow-unauthenticated --max-instances 1
"""
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import functions_framework
from google.cloud import storage

import check_schema as cs

DATA_BUCKET = os.environ.get("CHECK_DATA_BUCKET", "solar-potential-map-data")
INBOX = "roof-checks/inbox/"
PER_ADDRESS_HOUR = 60
PER_DAY = 3000
MAX_BODY = 64_000

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


@functions_framework.http
def receive(request):
    origin = request.headers.get("Origin", "")
    if not cs.origin_allowed(origin):
        return ("origin not allowed", 403)
    headers = {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST, OPTIONS",
               "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "3600",
               "Vary": "Origin"}
    if request.method == "OPTIONS":
        return ("", 204, headers)
    if request.method != "POST":
        return ("POST only", 405, headers)
    if (request.content_length or 0) > MAX_BODY:
        return ("too large", 413, headers)
    js = {**headers, "Content-Type": "application/json"}
    try:
        batch = cs.clean(json.loads(request.get_data(as_text=True) or "null"))
    except (ValueError, cs.BadBatch) as e:
        if str(e) == "honeypot":
            return (json.dumps({"ok": True}), 200, js)
        return (json.dumps({"ok": False, "error": str(e)}), 400, js)
    addr = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "?")
    if _limited(addr, time.time()):
        return (json.dumps({"ok": False, "error": "too many rounds, try again later"}), 429, js)
    received = datetime.now(timezone.utc)
    rid = received.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    batch["id"] = rid
    batch["received"] = received.isoformat(timespec="seconds")
    _gcs().bucket(DATA_BUCKET).blob(f"{INBOX}{rid}.json").upload_from_string(
        json.dumps(batch, ensure_ascii=False), content_type="application/json")
    return (json.dumps({"ok": True, "id": rid}), 200, js)
