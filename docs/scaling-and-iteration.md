# Scaling to New Zealand, and why changes take so long

Josh, 20 September 2026: *"I want you to get it ready for scaling to NZ wide.
While making sure new customisations can still happen quickly. It seems to take
a long time to fix things at the moment."*

Two questions, one answer each. Both are measured, not estimated from feel.

## Part 1 — why fixes take so long

The problem is not that the codebase is large. It is that **the right code
usually already exists, and only one of its callers knows about it.**

Four instances found in a single day's review, all independent, all shipped:

| The correct thing | Where it existed | Who was missing it |
| --- | --- | --- |
| Straighten a traced roof boundary in the building's frame | inside `lidar_faces` | `sam_faces` (isotropic simplify only) and `line_faces` (nothing at all) — the source of every roof Josh has called jagged |
| Mean irradiance over the sunniest N% of a roof (`cov_poa_N`) | baked by `bake_density_deciles`, on 15,122 of 15,353 buildings | the headline kW/kWh figure, which used the roof average — so the same panel described the same roof two ways |
| Region list must union the disk, not trust the config | written five times, once per patch driver | `all_areas()`, which every *stage* calls. The drivers were immune; the build was not, and skipped `pilot` twice in silence |
| Rebuild only the buildings whose reading actually changed | `tools/patch_stale_selected.py`, content-hashed and resume-safe | `run_district_build.sh`, which rebuilds every region from scratch |

That last row is the one that costs hours. Everything else in this repo is
already fast:

| Loop | Time | What it answers |
| --- | --- | --- |
| `tests/run_all.sh --fast` | ~20 s | did I break an invariant |
| `tools/bench.py` | 0.9 min | did geometry move against Josh's markup, on 152 roofs |
| `tools/cases.py check` | ~1 min | is every roof Josh has flagged still fixed |
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
   `check_repo_sync.py` and `check_diagram.py` already do exactly this for two
   other kinds of drift, and both have caught real bugs. The same shape of
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
| district build | 4.3 h | **956 VM-hours** | needs incremental + parallel regions |
| face precompute | 7.0 h (16 cores, sharded) | **1,556 VM-hours** | needs sharding (`--shard i/n` added today) |
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

Scaled from what Queenstown consumed today on one 16-core preemptible VM:

| | VM-hours | on one machine | on twenty |
| --- | --- | --- | --- |
| face precompute | 1,556 | 65 days | 3.2 days |
| region builds | 956 | 40 days | 2.0 days |
| **total** | **2,512** | **105 days** | **5.2 days** |

Two things that table does not say. It assumes no preemption — today's run
lost about an hour to one, and at national scale that is a tax, not an
incident, so the resumable runners matter. And it assumes the pipeline stays
as fast per building as it is now; the biggest roofs cost far more than the
median, and the cities are where the biggest roofs are.

The storage line is the one to fix first, because it is the difference
between 31 TB and roughly 80 GB.

### Order to do them in

1. ~~Buildings as vector tiles~~ — done 20 September.
1. Heat-map rasters as tiles (now the largest download).
2. Incremental build as the default (makes every later step iterable).
3. ~~Survey registry keyed by bbox~~ — done 21 September.
4. Region selection from a density grid (replaces the hand-written list).
5. Distributed build — only worth doing once 1–4 are true.

None of this needs the geometry work to pause. They touch different files.

## What is already right, and should not be "simplified" away

- **The comments.** Nearly every constant in this repo carries the measurement
  that set it and the failure that motivated it. That is why a regression can
  be diagnosed in minutes. It is not clutter.
- **The check scripts.** `check_repo_sync`, `check_diagram`, `test_golden`,
  `bench.py`, `cases.py` — five different kinds of drift detector, each
  written after a real escape. More of these, not fewer.
- **`preflight.py` and `run_stage.py`.** Stages that refuse to run against
  stale inputs are the reason a resumed build cannot quietly ship last week's
  geometry. It happened once; it has not happened since.
