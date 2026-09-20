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

Queenstown, measured today: 15,353 buildings. New Zealand has roughly 2.1
million in the LINZ outlines — about **137×**. Multiplying what exists:

| Thing | Queenstown now | ×137 | Verdict |
| --- | --- | --- | --- |
| ~~`data/solar_potential.geojson`, fetched whole at load~~ | ~~25 MB~~ | ~~3.4 GB~~ | **fixed 20 Sep** — buildings are tiles; 238 kB for a street view, flat with district size |
| `data/heatmaps/*`, positioned images | 9.6 MB per view | same per view | now the biggest download; tile it next |
| `data/panel_layouts.geojson` (merged, pre-tiling) | 480 MB | 65 GB | hard blocker |
| `data/panel_layouts.pmtiles` (what the map reads) | 29 MB | 4 GB | fine — it is tiled, and a client only fetches the tiles it looks at |
| `data/` on disk | 133 GB | 18 TB | not a laptop, and not one VM disk |
| district build, serial | 4.5 h | 26 days | needs incremental + parallel regions |
| face precompute, serial | 18 h | 100 days | needs sharding (`--shard i/n` added today) |
| region definitions | 24 hand-written bboxes | ~3,000 | needs to be derived, not typed |

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

**Nothing may be merged into one file.** The fan-in
(`merge_regions → bake_density_deciles → …`) exists because the frontend wants
one file. Once buildings are tiles, each region can be tiled independently and
the tiles combined, so the 65 GB intermediate never exists.

**A region must be self-describing.** Fixed today: `area_bbox_wgs84()` derives a
region's bbox from its own outlines when the config does not list one, so a
region that has data is buildable. The remaining manual step is deciding *which*
areas to build; at national scale that has to come from a population or
building-density grid, not from typing bboxes.

**Data sources must be keyed by location.** `config.py` hardcodes one LINZ layer
per product — `LINZ_DSM_LAYER = 105855` is *Otago Queenstown 2021*, and
`POINTCLOUD_BULK_URL` points at the Otago store. Wellington needed a different
store and was silently pointed at Otago until 31 August. NZ LiDAR is a patchwork
of surveys by year and region, so this has to become a lookup: given a bbox,
which survey covers it, and where does it live.

### Order to do them in

1. ~~Buildings as vector tiles~~ — done 20 September.
1. Heat-map rasters as tiles (now the largest download).
2. Incremental build as the default (makes every later step iterable).
3. Survey registry keyed by bbox (replaces four hardcoded constants).
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
