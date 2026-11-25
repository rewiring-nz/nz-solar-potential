"""Turn reports waiting in the inbox bucket into public GitHub issues.

Run by .github/workflows/user-reports.yml every 15 minutes with the
repository's own GITHUB_TOKEN, so issues are filed by github-actions[bot] and
no personal token is stored anywhere. The inbox is written by the Cloud Run
function in services/report_inbox/.

One open issue per building: a report on a building that already has an open
`user-report` issue is added to it as a comment, so repeat reports of one roof
collect in one place. Reports with no building each get their own issue.
A report is moved from inbox/ to filed/ only after GitHub has accepted it, so
a failed run leaves it to be filed next time.

    python tools/user_reports.py            # file everything waiting
    python tools/user_reports.py --dry-run  # print what would be filed
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "report_inbox"))
import report_schema as rs  # noqa: E402

BUCKET = os.environ.get("REPORT_DATA_BUCKET", "solar-potential-map-data")
INBOX = "user-reports/inbox/"
FILED = "user-reports/filed/"
REPO = os.environ.get("GITHUB_REPOSITORY", "rewiring-nz/nz-solar-potential")


class GitHub:
    def __init__(self, token, repo=REPO):
        self.token, self.repo = token, repo

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")

    def open_reports(self):
        """{building id: issue number} for open user-report issues."""
        found, page = {}, 1
        while True:
            batch = self.call("GET", f"/issues?state=open&labels={rs.LABEL}&per_page=100&page={page}")
            for it in batch:
                b = rs.title_building(it["title"])
                if b and b not in found:
                    found[b] = it["number"]
            if len(batch) < 100:
                return found
            page += 1

    def ensure_label(self):
        try:
            self.call("GET", f"/labels/{rs.LABEL}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            self.call("POST", "/labels", {"name": rs.LABEL, "color": "fbca04",
                                          "description": "Sent from the map's Report button"})

    def create(self, title, body):
        return self.call("POST", "/issues", {"title": title, "body": body, "labels": [rs.LABEL]})["number"]

    def comment(self, number, body):
        self.call("POST", f"/issues/{number}/comments", {"body": body})


def plan(reports, open_by_building):
    """[(report, action, issue number or None)] in arrival order.

    Two reports on the same new building in one run: the first opens the
    issue, the rest comment on it (number filled in when it exists).
    """
    seen = dict(open_by_building)
    out = []
    for r in sorted(reports, key=lambda r: r["id"]):
        b = r.get("building_id")
        if b and b in seen:
            out.append((r, "comment", b))
        else:
            out.append((r, "create", b))
            if b:
                seen[b] = None
    return out


def received_words(r):
    return r["received"].replace("T", " ").replace("+00:00", " UTC")


def run(gh, bucket, dry_run=False):
    blobs = sorted(bucket.list_blobs(prefix=INBOX), key=lambda b: b.name)
    reports = []
    for b in blobs:
        r = json.loads(b.download_as_text())
        r["_blob"] = b
        reports.append(r)
    if not reports:
        print("inbox empty")
        return 0
    if not dry_run:
        gh.ensure_label()
    issue_for = gh.open_reports()
    filed = 0
    for r, action, b in plan(reports, issue_for):
        body = rs.body_for(r, received_words(r), r.get("screenshot_url"))
        if dry_run:
            print(f"{action}: {rs.title_for(r) if action == 'create' else '#' + str(issue_for.get(b))}")
            continue
        if action == "comment" and issue_for.get(b):
            gh.comment(issue_for[b], body)
        else:
            n = gh.create(rs.title_for(r), body)
            if b:
                issue_for[b] = n
        blob = r["_blob"]
        bucket.copy_blob(blob, bucket, FILED + blob.name[len(INBOX):])
        blob.delete()
        filed += 1
    print(f"filed {filed} report(s)")
    return filed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from google.cloud import storage
    gh = GitHub(os.environ.get("GITHUB_TOKEN", ""))
    run(gh, storage.Client().bucket(BUCKET), a.dry_run)


if __name__ == "__main__":
    main()
