#!/bin/bash
# The fleet: N spot VMs that each run src/worker.sh until the queue is empty
# and then delete themselves.
#
#   tools/fleet.sh image          # snapshot the build VM's disk as the worker image
#   tools/fleet.sh up 6           # start 6 workers (asks about the paid referee; default no)
#   tools/fleet.sh status         # queue counts + who is building what
#   tools/fleet.sh list           # the worker VMs
#   tools/fleet.sh down           # delete every worker VM
#
# WHY C2D IN ZONE -a. T2D quota in Sydney is 24 cores and the build VM holds
# 16 of them; C2D has 100 free and c2d-standard-16 exists only in -a
# (measured 22 Sep). The regional CPU ceiling is 100, so six 16-core workers
# is the most the project can run today without a quota request; past that,
# request "C2D CPUs" for australia-southeast1 in the console.
#
# SCOPES. The build VM's service account carries storage READ-ONLY scope,
# fixed at creation, so it cannot publish. Workers are created with
# storage-rw (to publish) and compute-rw (to delete themselves).
set -u
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
PY=.venv/bin/python
. tools/cloud_profile.sh

PROFILE_PROJECT=""
PROFILE_ZONE=""
PROFILE_SOURCE_VM=""
PROFILE_SOURCE_ZONE=""
PROFILE_IMAGE=""
PROFILE_MACHINE=""
PROFILE_DISK=""
PROFILE_BUCKET=""
PROFILE_PREFIX=""
if [ -n "${SOLAR_CLOUD_PROFILE:-}" ]; then
  PROFILE_PROJECT=$(cloud_profile_value project) || exit 2
  PROFILE_ZONE=$(cloud_profile_value fleet_zone 2>/dev/null || cloud_profile_value zone) || exit 2
  PROFILE_SOURCE_VM=$(cloud_profile_value source_vm 2>/dev/null || cloud_profile_value instance_name) || exit 2
  PROFILE_SOURCE_ZONE=$(cloud_profile_value source_zone 2>/dev/null || cloud_profile_value zone) || exit 2
  PROFILE_IMAGE=$(cloud_profile_value worker_image 2>/dev/null || echo solar-worker)
  PROFILE_MACHINE=$(cloud_profile_value fleet_machine_type 2>/dev/null || cloud_profile_value machine_type) || exit 2
  PROFILE_DISK=$(cloud_profile_value fleet_disk_gb 2>/dev/null || cloud_profile_value disk_gb) || exit 2
  PROFILE_BUCKET=$(cloud_profile_value bucket 2>/dev/null || true)
  PROFILE_PREFIX=$(cloud_profile_value build_prefix 2>/dev/null || true)
  if [ -z "$PROFILE_BUCKET" ] && [ -z "${SOLAR_BUCKET:-}" ]; then
    echo "Cloud profile '$SOLAR_CLOUD_PROFILE' must define bucket before running fleet tooling." >&2
    exit 2
  fi
fi

PROJECT=${FLEET_PROJECT:-${PROFILE_PROJECT:-$(gcloud config get-value project 2>/dev/null)}}
ZONE=${FLEET_ZONE:-${PROFILE_ZONE:-australia-southeast1-a}}
SRC_VM=${FLEET_SOURCE_VM:-${PROFILE_SOURCE_VM:-claude-doing-things}}
SRC_ZONE=${FLEET_SOURCE_ZONE:-${PROFILE_SOURCE_ZONE:-australia-southeast1-b}}
IMAGE=${FLEET_IMAGE:-${PROFILE_IMAGE:-solar-worker}}
MACHINE=${FLEET_MACHINE:-${PROFILE_MACHINE:-c2d-standard-16}}
DISK_GB=${FLEET_DISK_GB:-${PROFILE_DISK:-400}}
BUCKET=${SOLAR_BUCKET:-${PROFILE_BUCKET:-gs://solar-potential-map-data}}
BUILD_PREFIX=${SOLAR_BUILD_PREFIX:-${PROFILE_PREFIX:-build}}
export SOLAR_BUCKET="$BUCKET" SOLAR_BUILD_PREFIX="$BUILD_PREFIX"
PREFIX=solar-worker
# REFEREE is the paid demolition referee decision (src/build_plan.py), asked
# once by `up` because workers have no terminal and so decline on their own.
make_startup() {
  cat <<EOS
#!/bin/bash
# runs as root at boot; the checkout and venv live in j's home on the image
su - j -c 'cd ~/solar-map && git pull -q 2>/dev/null; SOLAR_BUCKET="$BUCKET" SOLAR_BUILD_PREFIX="$BUILD_PREFIX" SOLAR_DEMOLISHED_REFEREE=${REFEREE:-0} SOLAR_SELF_DELETE=1 ./src/worker.sh' \
  >> /var/log/solar-worker.log 2>&1
EOS
}

case "${1:-}" in
  image)
    echo "imaging $SRC_VM's disk as $IMAGE in project $PROJECT (the VM keeps running; --force allows that)"
    gcloud compute images delete "$IMAGE" --project="$PROJECT" --quiet 2>/dev/null || true
    gcloud compute images create "$IMAGE" --source-disk "$SRC_VM" \
      --project="$PROJECT" --source-disk-zone "$SRC_ZONE" --force --quiet
    ;;
  up)
    N=${2:?how many workers}
    # Paid step, decided before any worker starts: yes/no, default no. The
    # number of regions the workers claim is not known here, so the estimate
    # is per region.
    REFEREE=$($PY src/build_plan.py confirm-paid --per-region) || REFEREE=0
    STARTUP=$(make_startup)
    for i in $(seq 1 "$N"); do
      NAME="$PREFIX-$(date -u +%m%d)-$i-$RANDOM"
      gcloud compute instances create "$NAME" --project="$PROJECT" --zone "$ZONE" \
        --machine-type "$MACHINE" --image "$IMAGE" \
        --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced \
        --provisioning-model SPOT --instance-termination-action DELETE \
        --scopes storage-rw,compute-rw \
        --labels role=solar-worker \
        --metadata startup-script="$STARTUP" --quiet \
        --format="value(name,status)" || echo "  could not create worker $i (quota?)"
    done
    ;;
  status)
    cd "$(dirname "$0")/.." && .venv/bin/python src/gcs_queue.py
    echo "workers:"; gcloud compute instances list --project="$PROJECT" --filter="labels.role=solar-worker" \
      --format="table(name,zone.basename(),machineType.basename(),status,creationTimestamp.date('%H:%M'))"
    ;;
  list)
    gcloud compute instances list --project="$PROJECT" --filter="labels.role=solar-worker"
    ;;
  down)
    NAMES=$(gcloud compute instances list --project="$PROJECT" --filter="labels.role=solar-worker" --format="value(name,zone.basename())")
    [ -n "$NAMES" ] || { echo "no workers"; exit 0; }
    echo "$NAMES" | while read -r name zone; do
      gcloud compute instances delete "$name" --project="$PROJECT" --zone "$zone" --quiet &
    done; wait
    ;;
  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
