#!/bin/bash
# Startup script for the build VM: power off after 30 idle minutes.
#
# WHY. October 2026: the build VMs were up 376 hours and 151 of them sat
# under 5% CPU -- a job had finished and nobody stopped the VM. The VM's own
# limit (max-run-duration) only stops it many hours after it started, so a
# forgotten VM billed until then. This stops it 30 minutes after the work
# does, whoever forgot.
#
# Installed as the instance's startup-script metadata, so it runs on every
# boot without the VM needing to be up to install it:
#   gcloud compute instances add-metadata claude-doing-things \
#     --zone australia-southeast1-b --project solar-potential-map-512020 \
#     --metadata-from-file startup-script=tools/vm_idle_stop.sh
#
# IDLE means, at a check every 5 minutes: the 5-minute load average is under
# 1.0 on 16 cores, and no build, fetch or copy process is running (a download
# waits on the network at near-zero CPU and must not count as idle). Six idle
# checks in a row (30 min) power the VM off. A boot starts the count at zero,
# so a VM started and then not used also stops after 30 minutes.
#
# To keep the VM up while idle on purpose: touch /tmp/keep-awake (removed by
# the next reboot); the watchdog then only logs.
set -u
cat > /usr/local/bin/idle-stop-check <<'EOF'
#!/bin/bash
STATE=/run/idle-stop.count
LOG=/var/log/idle-stop.log
load=$(cut -d' ' -f2 /proc/loadavg)
# project work only: Ubuntu keeps system Python daemons running at all times
busy=$(pgrep -f -c -E 'solar-map|\.venv/bin/python|pdal|gdal|ogr2ogr|rsync|scp|gsutil|gcloud storage|tippecanoe|pmtiles|wget|curl .*-o' || true)
if awk "BEGIN{exit !($load < 1.0)}" && [ "${busy:-0}" -eq 0 ]; then
  n=$(( $(cat $STATE 2>/dev/null || echo 0) + 1 ))
else
  n=0
fi
echo $n > $STATE
echo "$(date -u +%FT%TZ) load=$load busy=$busy idle_checks=$n" >> $LOG
if [ $n -ge 6 ]; then
  if [ -e /tmp/keep-awake ]; then echo "$(date -u +%FT%TZ) idle but /tmp/keep-awake set" >> $LOG; exit 0; fi
  echo "$(date -u +%FT%TZ) idle 30 min: powering off" >> $LOG
  /sbin/shutdown -h now
fi
EOF
chmod +x /usr/local/bin/idle-stop-check
echo 0 > /run/idle-stop.count
cat > /etc/systemd/system/idle-stop.service <<'EOF'
[Unit]
Description=Power off the build VM after 30 idle minutes
[Service]
Type=oneshot
ExecStart=/usr/local/bin/idle-stop-check
EOF
cat > /etc/systemd/system/idle-stop.timer <<'EOF'
[Unit]
Description=Idle check every 5 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now idle-stop.timer
