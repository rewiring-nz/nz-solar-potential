#!/bin/bash
# Wait for a district build on the build VM to end, and say how it ended.
#
#   tools/watch_vm_build.sh <build output file on the VM> [interval s] [max hours]
#
# THE RULE. The build has ended when its output file carries the
# "=== complete" line the build script writes last, or when no build
# process is left. The VM being off, the VM being unreachable three checks
# running, and the time limit each end the wait too, with their own exit
# code, so a watcher can never sit on a build that is no longer there.
#
# THE REASON (16 Nov). A watcher that asked `pgrep -f "src/run_district_build.sh"`
# over ssh matched its own ssh command, reported a finished smoke build as
# running for ten hours, and the VM idled off with the next build unstarted.
# The process check here uses a bracketed pattern, which cannot match itself,
# and it is the second signal, not the first.
#
# Exit: 0 complete, 2 VM not running, 3 build process gone without
# "=== complete", 4 VM unreachable, 5 time limit.
set -u
OUT="${1:?build output file on the VM}"
EVERY="${2:-900}"
MAX_H="${3:-20}"
PROJECT=solar-potential-map-512020
ZONE=australia-southeast1-b
VM=claude-doing-things
START=$(date +%s)
misses=0

vm() {
  gcloud compute ssh --tunnel-through-iap "j@$VM" --project="$PROJECT" \
    --zone="$ZONE" --command "$1" 2>/dev/null
}

finish() {
  echo "$(date -u +%H:%M) $1"
  vm "grep -n 'FAILED\|WARN\|=== combine\|=== complete\|guard\|stopping' $OUT | tail -30 | cut -c1-200"
  exit "$2"
}

while true; do
  state=$(gcloud compute instances describe "$VM" --project="$PROJECT" \
    --zone="$ZONE" --format='value(status)' 2>/dev/null)
  [ -n "$state" ] && [ "$state" != "RUNNING" ] && { echo "$(date -u +%H:%M) VM is $state"; exit 2; }

  got=$(vm "grep -c '^=== complete' $OUT; pgrep -f '[r]un_district_build' | wc -l; grep -c FAILED $OUT; grep '^===' $OUT | tail -1")
  done_n=$(echo "$got" | sed -n 1p); procs=$(echo "$got" | sed -n 2p)
  if ! [[ "$done_n" =~ ^[0-9]+$ && "$procs" =~ ^[0-9]+$ ]]; then
    misses=$((misses + 1))
    echo "$(date -u +%H:%M) no answer from the VM ($misses of 3)"
    [ "$misses" -ge 3 ] && exit 4
  else
    misses=0
    echo "$(date -u +%H:%M) failed=$(echo "$got" | sed -n 3p) $(echo "$got" | sed -n 4p | cut -c1-100)"
    [ "$done_n" -gt 0 ] && finish "build complete" 0
    [ "$procs" -eq 0 ] && finish "build process gone without '=== complete'" 3
  fi
  [ $(( ($(date +%s) - START) / 3600 )) -ge "$MAX_H" ] && { echo "time limit ${MAX_H} h"; exit 5; }
  sleep "$EVERY"
done
