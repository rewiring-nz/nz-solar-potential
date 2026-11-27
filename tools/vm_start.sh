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
if [ "${1:-}" = "--check-workers" ]; then
  # tools/vm_start.sh --check-workers N: may N more spot workers start under
  # the clone limits below (max_clones alive at once; the month's hours plus
  # clone_max_hours for every live and new one under the cap)? Exits 0 yes,
  # 2 refused, 1 could not check. Starts nothing: tools/fleet.sh up calls it
  # and gives each worker --max-run-duration clone_max_hours.
  N=${2:?how many workers}
  MAXC=$(python3 -c "import json;print(json.load(open('$BUDGET')).get('max_clones',6))")
  MAXH=$(python3 -c "import json;print(json.load(open('$BUDGET')).get('clone_max_hours',12))")
  CLONES=$(gcloud compute instances list --project="$PROJECT" --filter="labels.role=solar-split OR labels.role=solar-worker" --format="value(name)") \
    || { echo "could not count workers: refusing to start blind"; exit 1; }
  ALIVE=$(printf '%s' "$CLONES" | grep -c . || true)
  echo "workers alive: $ALIVE of $MAXC; each new one is deleted after $MAXH h"
  [ $((ALIVE + N)) -le "$MAXC" ] || { echo "REFUSED: $ALIVE alive plus $N would pass the limit of $MAXC ($BUDGET)."; exit 2; }
  python3 -c "import sys;sys.exit(0 if $HOURS + ($ALIVE + $N) * $MAXH <= $CAP else 1)" \
    || { echo "REFUSED: $ALIVE alive plus $N new, at $MAXH h each, would pass the month's cap of $CAP h. The owner raises it in $BUDGET."; exit 2; }
  printf "worst case for these %s: %s h, about NZ\$%s (an estimate, not a spending cap)\n" "$N" "$((N * MAXH))" "$(python3 -c "print(round($N*$MAXH*$RATE))")"
  exit 0
fi
if [ "${1:-}" = "--clone" ]; then
  # A CLONE OF THE BUILD VM, UNDER THE SAME CAP (owner's approval, 17 Nov).
  #   tools/vm_start.sh --clone <name> "<regions>" <startup script> <job script>
  # A spot c2d-standard-16 made from the `solar-worker` image
  # (tools/fleet.sh image), deleted on preemption, that runs the job script
  # on the regions in its metadata. Its hours count toward the month's total
  # like any VM's. C2D in zone -a: T2D quota is 24 cores and the build VM
  # holds 16. pd-standard: the SSD quota is 500 GB and the image is 600 GB.
  #
  # THREE LIMITS, all in data/vm_budget.json, so a raised CPU quota cannot
  # turn into a large bill:
  #   max_clones        clones alive at once (default 6).
  #   clone_max_hours   Google deletes a clone this long after it starts,
  #                     whatever it is doing (default 12): a hung or
  #                     forgotten clone cannot run on.
  #   hours_per_month   the month's hours so far, plus clone_max_hours for
  #                     every clone alive and for this one, must fit under
  #                     the cap: the cap holds even if every clone runs to
  #                     its limit.
  # Worst case for one launch: max_clones x clone_max_hours x nzd_per_hour
  # (6 x 12 x NZ$1 = NZ$72), and never past the monthly cap.
  NAME=${2:?clone name}; REGIONS=${3:?regions}; STARTUP=${4:?startup script}; JOB=${5:?job script}
  MAXC=$(python3 -c "import json;print(json.load(open('$BUDGET')).get('max_clones',6))")
  MAXH=$(python3 -c "import json;print(json.load(open('$BUDGET')).get('clone_max_hours',12))")
  # The listing runs on its own so its failure is seen: inside a pipe the
  # status would be wc's, and a failed listing would count as 0 clones.
  CLONES=$(gcloud compute instances list --project="$PROJECT" --filter="labels.role=solar-split OR labels.role=solar-worker" --format="value(name)") \
    || { echo "could not count clones: refusing to start blind"; exit 1; }
  ALIVE=$(printf '%s' "$CLONES" | grep -c . || true)
  echo "clones alive: $ALIVE of $MAXC; each is deleted after $MAXH h"
  [ "$ALIVE" -lt "$MAXC" ] || { echo "REFUSED: $ALIVE clones are alive, the limit is $MAXC ($BUDGET)."; exit 2; }
  python3 -c "import sys;sys.exit(0 if $HOURS + ($ALIVE + 1) * $MAXH <= $CAP else 1)" \
    || { echo "REFUSED: $ALIVE clone(s) alive plus this one, at $MAXH h each, would pass the month's cap of $CAP h. The owner raises it in $BUDGET."; exit 2; }
  [ "${CLONE_DRY_RUN:-0}" = "1" ] && { echo "dry run: would create $NAME"; exit 0; }
  gcloud compute instances create "$NAME" --project="$PROJECT" --zone "${CLONE_ZONE:-australia-southeast1-a}" \
    --machine-type "${CLONE_MACHINE:-c2d-standard-16}" --image solar-worker --image-project "$PROJECT" \
    --boot-disk-size 600GB --boot-disk-type pd-standard \
    --provisioning-model SPOT --instance-termination-action DELETE \
    --max-run-duration "${MAXH}h" \
    --scopes storage-rw,compute-rw --labels role=solar-split \
    --metadata "regions=$REGIONS" \
    --metadata-from-file "startup-script=$STARTUP,clone-job=$JOB" \
    --quiet --format="value(name,status)"
  exit $?
fi
if python3 -c "import sys;sys.exit(0 if $HOURS < $CAP else 1)"; then
  gcloud compute instances start "$VM" --zone "$ZONE" --project "$PROJECT"
  echo "Started. It powers itself off after 30 idle minutes (tools/vm_idle_stop.sh)."
else
  echo "REFUSED: this month's VM hours are at the cap. The owner raises it in $BUDGET."
  exit 2
fi
