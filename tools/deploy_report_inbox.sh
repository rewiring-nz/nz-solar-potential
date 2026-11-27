#!/usr/bin/env bash
# Deploy the problem-report inbox (services/report_inbox/) as a public Cloud
# Run function and point the map at it. Run with an account that owns project
# solar-potential-map-512020; safe to run again (every step is idempotent).
#
# What it sets up:
#   - service account report-inbox@: may create objects in the data bucket
#     (inbox) and the tiles bucket (screenshots), nothing else;
#   - the function `report-inbox`, australia-southeast1, max 1 instance,
#     256 MB, open to the public (the map posts to it from any browser);
#   - github-builder@ (the GitHub Actions identity) may read and move
#     reports in the data bucket, for .github/workflows/user-reports.yml;
#   - site-config.js reportUrl set to the function's URL.
# Cost: Cloud Run's free tier covers well over 100,000 reports a month.
set -euo pipefail
cd "$(dirname "$0")/.."
P=solar-potential-map-512020
R=australia-southeast1
SA=report-inbox@$P.iam.gserviceaccount.com
GH=github-builder@$P.iam.gserviceaccount.com

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com --project $P
gcloud iam service-accounts describe $SA --project $P >/dev/null 2>&1 || \
  gcloud iam service-accounts create report-inbox --project $P \
    --display-name "Map problem-report inbox"
for B in solar-potential-map-data solar-potential-map-tiles; do
  gcloud storage buckets add-iam-policy-binding gs://$B \
    --member "serviceAccount:$SA" --role roles/storage.objectCreator >/dev/null
done
gcloud storage buckets add-iam-policy-binding gs://solar-potential-map-data \
  --member "serviceAccount:$GH" --role roles/storage.objectUser >/dev/null

gcloud run deploy report-inbox --project $P --region $R \
  --source services/report_inbox --function receive --base-image python312 \
  --service-account $SA --allow-unauthenticated \
  --max-instances 1 --memory 256Mi --timeout 30 --quiet

URL=$(gcloud run services describe report-inbox --project $P --region $R --format 'value(status.url)')
python3 - "$URL" <<'PY'
import re, sys
p = "site-config.js"; s = open(p).read()
s2 = re.sub(r'reportUrl: "[^"]*"', f'reportUrl: "{sys.argv[1]}"', s)
open(p, "w").write(s2)
print("site-config.js reportUrl ->", sys.argv[1], "(changed)" if s2 != s else "(already set)")
PY
echo "Commit site-config.js and push to main to switch the live map over."
