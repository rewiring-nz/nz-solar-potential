#!/bin/bash
# Start the build VM, but only inside this month's hour budget.
#
# WHY. The build VMs ran 376 hours in October 2026 (151 of them idle) and the
# Google Cloud bill was NZ$511 (NZ$440 of it Compute Engine). Nothing checked the month's running total
# before a start. This does: it reads every project's VM uptime for the month
# from Cloud Monitoring and refuses to start once the month's hours reach the
# cap. A start is the only way the VM costs compute, so a capped start is a
# capped bill (disks and storage are the small fixed remainder).
#
# The cap lives in data/vm_budget.json ({"hours_per_month": N,
# "nzd_per_hour": R}); only the owner raises it. nzd_per_hour is measured
# from the October 2026 invoice: spot t2d-standard-16 in Sydney ran about
# NZ$23.5 for a full day, so ~NZ$1.0/h excluding tax. Shown, not billed.
#
#   tools/vm_start.sh            # start if within budget
#   tools/vm_start.sh --status   # month-to-date hours and estimate only
set -eu
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM=${VM:-claude-doing-things}
ZONE=${ZONE:-australia-southeast1-b}
PROJECT=${PROJECT:-solar-potential-map-512020}
# every project holding a build VM: the cap is the month's total, not per VM.
# The old downloads-417521 VM (and its disk and snapshots) was deleted on
# 4 Nov 2026; one VM remains.
PROJECTS="solar-potential-map-512020"
BUDGET="$REPO_ROOT/data/vm_budget.json"
[ -f "$BUDGET" ] || { echo "no $BUDGET: refusing to start without a budget"; exit 1; }
CAP=$(python3 -c "import json;print(json.load(open('$BUDGET'))['hours_per_month'])")
RATE=$(python3 -c "import json;print(json.load(open('$BUDGET'))['nzd_per_hour'])")

TOK=$(gcloud auth print-access-token)
START=$(date -u +%Y-%m-01T00:00:00Z); NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOURS=0
for P in $PROJECTS; do
  h=$(curl -sf -G -H "Authorization: Bearer $TOK" \
      "https://monitoring.googleapis.com/v3/projects/$P/timeSeries" \
      --data-urlencode 'filter=metric.type="compute.googleapis.com/instance/uptime"' \
      --data-urlencode "interval.startTime=$START" --data-urlencode "interval.endTime=$NOW" \
      --data-urlencode "aggregation.alignmentPeriod=3600s" \
      --data-urlencode "aggregation.perSeriesAligner=ALIGN_SUM" \
    | python3 -c "import json,sys;d=json.load(sys.stdin)
print(sum(float(p['value'].get('doubleValue',p['value'].get('int64Value',0))) for t in d.get('timeSeries',[]) for p in t['points'])/3600)") \
    || { echo "could not read uptime for $P: refusing to start blind"; exit 1; }
  HOURS=$(python3 -c "print($HOURS+$h)")
done
EST=$(python3 -c "print(round($HOURS*$RATE))")
printf "VM hours this month: %.0f of %s (about NZ\$%s at NZ\$%s/h, before tax and disks)\n" "$HOURS" "$CAP" "$EST" "$RATE"
[ "${1:-}" = "--status" ] && exit 0
if python3 -c "import sys;sys.exit(0 if $HOURS < $CAP else 1)"; then
  gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT"
  echo "Started. It powers itself off after 30 idle minutes (tools/vm_idle_stop.sh)."
else
  echo "REFUSED: this month's VM hours are at the cap. The owner raises it in $BUDGET."
  exit 2
fi
