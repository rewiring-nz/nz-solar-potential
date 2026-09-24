# Scaling to New Zealand, and why changes take so long

The goal: ready to scale NZ-wide, while customisations still happen quickly
and fixes stop taking a long time.

Two questions, one answer each. Both are measured, not estimated from feel.

## Part 1 — why fixes take so long

The problem is not that the codebase is large. It is that **the right code
usually already exists, and only one of its callers knows about it.**

Four instances found in a single day's review, all independent, all shipped:

| The correct thing | Where it existed | Who was missing it |
| --- | --- | --- |
| Straighten a traced roof boundary in the building's frame | inside `lidar_faces` | `sam_faces` (isotropic simplify only) and `line_faces` (nothing at all) — the source of every roof flagged as jagged |
| Mean irradiance over the sunniest N% of a roof (`cov_poa_N`) | baked by `bake_density_deciles`, on 15,122 of 15,353 buildings | the headline kW/kWh figure, which used the roof average — so the same panel described the same roof two ways |
| Region list must union the disk, not trust the config | written five times, once per patch driver | `all_areas()`, which every *stage* calls. The drivers were immune; the build was not, and skipped `pilot` twice in silence |
| Rebuild only the buildings whose reading actually changed | `tools/patch_stale_selected.py`, content-hashed and resume-safe | `run_district_build.sh`, which rebuilds every region from scratch |

That last row is the one that costs hours. Everything else in this repo is
already fast:

| Loop | Time | What it answers |
| --- | --- | --- |
| `tests/run_all.sh --fast` | ~20 s | did I break an invariant |
| `tools/bench.py` | 0.9 min | did geometry move against the markup, on 152 roofs |
| `tools/cases.py check` | ~1 min | is every flagged roof still fixed |
| a frontend change | seconds | — |
| **shipping a geometry change** | **3 h precompute + 4.5 h build** | — |

Measuring is fast. Shipping is slow, and it is slow because the unit of work is
the district: change one line in `face_candidates.py` and all 24 regions rebuild,
including the 47% of buildings that reading cannot possibly have touched.

### What to do about it, in order

1. **Make the incremental path the default.** `patch_stale_selected.py` already
   does the correct thing — it hashes each building's reading against
   `data/built_from.json`, so it is right however many times a preemptible VM
   kills it, and unlike mtimes it survives the patching that rewrites the
   layouts. `run_district_build.sh` should call it, and keep `--force` for the
   full rebuild. A change touching 40 roofs should cost minutes.
2. **One seam per concept, and a check that says when there are two.**
   `check_diagram.py` already does exactly this for one kind of drift (and
   `check_repo_sync.py` did for another until the Wellington copy was
   retired); both caught real bugs. The same shape of
   check should assert that the frontend's coverage steps match the baker's,
   that every candidate reading goes through `regularise`, and so on.
3. **Delete on sight.** 11 scripts in `src/` (1,122 lines) were referenced by
   nothing at all. `src/` is the library and its stages; one-off debug and
   comparison scripts belong in `tools/` or in git history.

## Part 2 — what actually blocks New Zealand

Queenstown, measured today: 15,353 buildings. New Zealand has **3,413,097**
in the LINZ outlines — **222×**, not the 137× this document claimed until
21 September. That earlier figure was a remembered 2.1 million and it was
wrong by 63%; the real one comes from asking LINZ cell by cell
(`tools/plan_national_regions.py`). Every estimate below is scaled from what
Queenstown actually consumed, not from a rate card:

| Thing | Queenstown now | ×137 | Verdict |
| --- | --- | --- | --- |
| ~~`data/solar_potential.geojson`, fetched whole at load~~ | ~~25 MB~~ | ~~3.4 GB~~ | **fixed 20 Sep** — buildings are tiles; 238 kB for a street view, flat with district size |
| `data/heatmaps/*`, positioned images | 9.6 MB per view | same per view | now the biggest download; tile it next |
| `data/panel_layouts.geojson` (merged, pre-tiling) | 480 MB | 65 GB | hard blocker |
| `data/panel_layouts.pmtiles` (what the map reads) | 29 MB | 4 GB | fine — it is tiled, and a client only fetches the tiles it looks at |
| `data/` on disk, inputs kept | 139 GB | **31 TB** | not a laptop, and not one VM disk |
| district build | 6.1 h | **1,347 VM-hours** | needs incremental + parallel regions |
| face precompute | 6.6 h (16 cores, sharded) | **1,461 VM-hours** | needs sharding (`--shard i/n` added today) |
| region definitions | 24 hand-written bboxes | **1,384, derived** | needs to be derived, not typed |

Four things follow from that table.

**Buildings are now vector tiles.** Done 20 September. `data/buildings.pmtiles`
from z13, `data/building_cells.pmtiles` below it carrying exact per-cell totals
at three resolutions, and the per-building horizon blobs split into
`data/building_detail/` and fetched on click. Measured on the live site: a
street view pulls 238 kB of buildings where it used to pull 26 MB, and that
number does not grow when the district does.

Two things this left behind. The heat-map rasters are now the largest download
at 9.6 MB for one view -- they are positioned images, not tiles, and should
become tiles next. And `data/addresses.json` is a flat 0.7 MB index, which is
right for a district and wrong for 2.1 million addresses (~34 MB): national
search needs it sharded by prefix, or a real geocoder.

**Do not keep the inputs.** 31 TB is the figure for hoarding every point
cloud and every orthophoto, and there is no reason to. They are inputs: fetch
a region, build it, emit its tiles, delete them. Peak storage becomes one
region's working set plus the published tiles, which at Queenstown's ratio is
about 80 GB of output for the whole country. This is a change to the fetch
step, not a bigger disk, and it is the single largest saving available.

**Nothing may be merged into one file.** The fan-in
(`merge_regions → bake_density_deciles → …`) exists because the frontend wants
one file. Once buildings are tiles, each region can be tiled independently and
the tiles combined, so the 65 GB intermediate never exists.

**Regions are derived now, not typed.** Done 21 September.
`tools/plan_national_regions.py` lays a grid over the country, asks LINZ how
many buildings are in each cell, keeps the populated ones and splits anything
over 3,000 buildings — Queenstown's largest working region is 2,712. Result:
**389 populated cells of 1,836, 1,384 build regions**. It plans only; nothing
is fetched or built.

It also found a trap worth knowing about. LINZ's WFS returns `lon,lat` where
the spec says `lat,lon`, and the wrong order returns a valid 200 reporting
**zero buildings** — an empty New Zealand with no error anywhere. The planner
now counts a bbox known to hold thousands before trusting any answer.

**The country is not uniform, and the plan should not be either.** Half of New
Zealand's buildings sit in the densest **20 of 389 cells**: Auckland,
Christchurch, Wellington, Hamilton. A national rollout is not one 105-day run
— it is Auckland first, then the next nineteen, publishing as each lands. Each
of those is roughly a Queenstown, which is a day.

**Data sources are keyed by location now.** Done 21 September.
`src/surveys.py` resolves a region's bbox against `config.SURVEYS`, and the
DSM, DEM, imagery, tile-index and point-cloud fetchers all go through it. A
region covered by no listed survey raises an error naming the surveys that
exist, instead of quietly fetching someone else's data — which is what happened
to Wellington until 31 August, pointed at the Otago point-cloud store with
every download 404ing and regions falling back to the 1 m DSM. With no registry
configured the lookup returns the old constants, so nothing had to be
re-fetched to land it. What remains manual is maintaining the list; what is
gone is the chance of a region silently inheriting the wrong capture.

### What a national run actually costs

Scaled from what Queenstown consumed on 20–21 September, one 16-core
preemptible VM, data already on disk:

| | VM-hours | 1 machine | 20 | 50 | 100 |
| --- | --- | --- | --- | --- | --- |
| face precompute | 1,461 | 61 d | 3.0 d | 1.2 d | 0.6 d |
| region builds | 1,347 | 56 d | 2.8 d | 1.1 d | 0.6 d |
| **total compute** | **2,808** | **117 d** | **5.8 d** | **2.3 d** | **1.2 d** |

Compute divides cleanly. The planner caps every region at 3,000 buildings, so
the 1,384 regions are close to even and there is no straggler that holds the
whole run open — that even split is the reason more machines keep paying.

**The download does not divide.** That table is compute on data already
fetched. Queenstown needed 116 GB of point cloud, elevation and imagery;
nationally that is **26 TB**, all of it from LINZ, which is one service:

| LINZ serves, in aggregate | download takes |
| --- | --- |
| 0.5 Gbit/s | 4.8 days |
| 1 Gbit/s | 2.4 days |
| 2 Gbit/s | 1.2 days |
| 5 Gbit/s | 0.5 days |

Fifty machines do not make that faster. They may make it slower — fifty
clients on one API is how you find its rate limiter. Nobody has measured what
LINZ will actually sustain, and that number, not the machine count, sets the
floor for a national run. **Measure it on one city before buying parallelism
for the whole country.**

**And the fan-in is serial**, which is a second reason not to think of this as
one run: merge, bake, tile, deploy. At district scale it is minutes. At
national scale it is the 65 GB merged file that must not exist at all (see
above), so it needs redesigning before it is timed.

### Do the cities, not the country

Half of New Zealand's buildings are in the densest **20 of 389 cells**:

| | buildings | compute | download |
| --- | --- | --- | --- |
| densest 20 cells | 1,709,541 (50%) | 1,403 VM-h — **1.2 days on 50** | 12.9 TB |
| all 389 cells | 3,413,097 | 2,808 VM-h — 2.3 days on 50 | 25.7 TB |

Half the country for half the cost, publishing as each city lands, and the
second half can wait for the storage fix and a measured LINZ rate.

### Order to do them in

1. ~~Buildings as vector tiles~~ — done 20 September.
2. ~~Heat-map rasters as tiles~~ — done (emit_region writes them per region).
3. ~~Incremental build as the default~~ — done 24 September: per-building
   build keys (reading + markup + geometry code) and code-aware stage markers
   (`src/build_keys.py`); a patched building is byte-identical to a full build.
4. ~~Survey registry keyed by bbox~~ — done 21 September.
5. ~~Region selection from a density grid~~ — done 21 September.
6. ~~The layers~~ — done 24 September: region packs keep what geometry needs
   before inputs are deleted (`src/pack_region.py`); kWh is its own layer
   (`src/apply_yield.py`, `--yield-only`); the served output has a contract
   (`src/output_contract.py`).
7. Distributed build — built; its first real run is the dress rehearsal,
   [national-rehearsal.md](national-rehearsal.md).

None of this needs the geometry work to pause. They touch different files.

## What is already right, and should not be "simplified" away

- **The comments.** Nearly every constant in this repo carries the measurement
  that set it and the failure that motivated it. That is why a regression can
  be diagnosed in minutes. It is not clutter.
- **The check scripts.** `check_diagram`, `test_golden`, `test_imports_and_arity`,
  `bench.py`, `cases.py` — five different kinds of drift detector, each
  written after a real escape. More of these, not fewer.
- **`preflight.py` and `run_stage.py`.** Stages that refuse to run against
  stale inputs are the reason a resumed build cannot quietly ship last week's
  geometry. It happened once; it has not happened since.
