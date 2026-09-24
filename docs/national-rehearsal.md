# Dress rehearsal: one city, end to end, before the country

The national build has only ever run region by region on one VM. The fleet
(`tools/fleet.sh`, `src/gcs_queue.py`) has run single workers but never as a
fleet, and nobody knows how fast the data sources will serve. This page is the
run that answers both, on one city, before anything is sized for the country.
Everything it uses is in the repo; what it needs from outside is a LINZ API
key, the bucket and quota.

Suggested city: **Hamilton** (one survey, ~70k buildings, roughly 25 build
regions). It's big enough to exercise the fleet and small enough to rerun in a
day. Christchurch is the harder second test: several surveys and a lot of
hillside.

## 0. Before anything moves

On the VM, from a clean checkout of the branch being rehearsed:

```bash
bash tests/run_all.sh          # includes the synthetic-region build, ~1 min
.venv/bin/python tests/test_golden.py --record   # after the geometry fixes of 24 Sep; say why in the commit
```

Re-recording the goldens is **required** before the rehearsal. The 24 Sep
geometry fixes change real layouts (skylights on clean faces, pocket panels at
100%, hip ridges now snapping), so the recorded goldens are stale. Record them,
look at the renders of any building whose count moved, and commit with the
reason.

## 1. Measure the sources (from one machine, then from several at once)

```bash
.venv/bin/python tools/measure_fetch_rate.py --bbox 175.24 -37.82 175.32 -37.75
```

Record the MB/s at each stream count and the LINZ export time. Then start the
same command on 3–4 machines at the same moment. If the per-machine rate holds,
the store is not the limit and more workers will help. If it drops, the
aggregate is the ceiling, and **that** sets the national timeline, not the
machine count (26 TB ÷ aggregate rate).

## 2. Plan and queue the city

```bash
HAM="175.15 -37.87 175.40 -37.68"
.venv/bin/python tools/plan_national_regions.py --bbox $HAM --count   # writes data/national_regions.json
.venv/bin/python tools/enqueue_regions.py --plan data/national_regions.json --bbox $HAM --dry-run
.venv/bin/python tools/enqueue_regions.py --plan data/national_regions.json --bbox $HAM
.venv/bin/python tools/status.py        # queued N / claimed 0 / done 0 / failed 0
```

## 3. Run the fleet

```bash
tools/fleet.sh up 8
watch -n 60 .venv/bin/python tools/status.py
```

Each worker runs `src/build_region.sh` per region: fetch (or restore a pack),
predict faces, build, emit, **pack**, publish. Workers delete themselves when
the queue is empty.

Record, per region, from `done/<region>.json` and the manifest:
- wall time and the stage that dominated it
- `pack.point_bytes_kept / point_bytes_in` and `imagery_bytes`. This is the
  real pack ratio, replacing the estimate of "a fraction of the survey"
- the survey density the layout stage logged (`survey: N returns/m2`) and
  the worker count it chose

## 4. Check what came out

```bash
.venv/bin/python src/combine_regions.py
.venv/bin/python src/invariants.py <each region>      # watchlists, per region
.venv/bin/python tools/predeploy_check.py
.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from src.output_contract import validate_region as v; print(v('data/out/<region>'))"
```

Then open the map on `site-config.js` pointed at the rehearsal bucket and look
at 20 roofs by eye. Count them, don't just skim.

## 5. The two rebuild paths, for real

These are what make "keep improving the geometry after launch" affordable.
Both are proved byte-identical on the synthetic region; this proves them at
city scale.

1. **Rebuild one region from its pack** (inputs deleted by publish):
   ```bash
   .venv/bin/python src/pack_region.py <region> --restore
   bash src/run_district_build.sh --regions <region> --force
   ```
   Compare `data/out/<region>/summary.json` with the one from step 3: counts
   and kWh must match exactly.
2. **Yield-only rebuild** (no LiDAR, no LINZ): change nothing and run
   ```bash
   bash src/run_district_build.sh --regions "<all>" --yield-only
   ```
   Every region should report `0 changed`.

## 6. Write down

- aggregate download rate and the machine count where it stops helping
- pack ratio, measured
- VM-hours per 1,000 buildings, measured
- anything that failed, and why

Those four numbers replace the estimates in `docs/scaling-and-iteration.md`
and decide how the national run is sized.
