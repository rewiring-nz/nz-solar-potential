# Problem reports from the map

People report problems from the map itself. Each report becomes a public
GitHub issue labelled
[`user-report`](https://github.com/rewiring-nz/nz-solar-potential/issues?q=label%3Auser-report).
No form service, no account, no cost.

## What a visitor can send

| Where on the map | Kind | What it says |
| --- | --- | --- |
| 🐞 Report an issue (corner button) | `note` | Their own words about what they see |
| ⚑ Report a wrong layout (roof card) | `flag` | One click, then an optional reason chip |
| Advanced › Edit panels › Send | `correction` | Panel count before and after, and every panel's centre |

Each report also carries the building and address, the map view and
settings, the data version and a picture of the map as it was on screen.
The visitor is told reports are public.

## How a report travels

1. `report.js` posts the report as JSON to the inbox, a Cloud Run function
   (`services/report_inbox/`, URL in `site-config.js` `reportUrl`).
2. The inbox checks it (`report_schema.clean`), stores it in
   `gs://solar-potential-map-data/user-reports/inbox/`, and stores the picture
   publicly in `gs://solar-potential-map-tiles/user-reports/`. It holds no
   GitHub credentials.
3. Every 15 minutes, `.github/workflows/user-reports.yml` runs
   `tools/user_reports.py`, which files each waiting report with the
   repository's own token, then moves it to `user-reports/filed/`.
4. **One issue per building.** A report on a building that already has an
   open `user-report` issue is added to it as a comment, so repeat reports
   and follow-up reasons collect in one place. Reports with no building get
   their own issue.

## Limits

- Only the live site and local previews can post (the browser's Origin).
- 20 reports an hour from one address, 300 a day in all (one instance).
- Notes up to 2,000 characters, pictures up to 400 KB.
- A hidden field that people never see and bots fill in: those reports are
  dropped without saying so.
- `@` in a report is broken with a zero-width space so a report cannot
  notify anyone; the note is quoted, never rendered as the issue's own markup.

## Triage

Anyone with write access can triage; nothing waits on one person.

- A roof the map draws wrong: add `roof-case` and a pipeline-area label
  (`geometry`, `model`, `no-lidar`), and log it in `data/roof_cases.json`.
- A website bug: add `bug`.
- Spam or not actionable: close it as not planned.
- Fixed in a released data version: close it with that version in the comment.

## Set up or change it

- Deploy or redeploy the inbox: `tools/deploy_report_inbox.sh` (needs an
  owner of project `solar-potential-map-512020`; writes the URL into
  `site-config.js`).
- File waiting reports now: run the **File user reports** workflow by hand
  in GitHub Actions, or `python tools/user_reports.py --dry-run` locally to
  see what it would file.
- Tests: `tests/test_user_reports.py`.
