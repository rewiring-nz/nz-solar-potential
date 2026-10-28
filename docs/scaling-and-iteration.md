# Scaling to New Zealand, and keeping changes fast

Goal: ready to scale nationally, with fixes and changes still quick.

## Keeping changes fast

Slow fixes usually come from the right code existing in one place while
another caller does not use it. Examples found in one review: boundary
straightening in `lidar_faces` but not in the photo readings; a "sunniest
N% of the roof" figure baked but not used by the headline; region lists
built from disk in the patch drivers but not in `all_areas()`; incremental
rebuilds in a patch tool but not in the district build. All four are fixed.

The feedback loops:

| loop | time | answers |
| --- | --- | --- |
| `tests/run_all.sh --fast` | ~20 s | did I break an invariant |
| `tools/bench.py` | ~1 min | did geometry move against the markup |
| `tools/cases.py check` | ~1 min | is every flagged roof still fixed |
| a front-end change | seconds | |
| a geometry change, few roofs | minutes (incremental build) | |
| a sun-model change | minutes (`--yield-only`) | |

Rules that keep it this way:

1. **Incremental by default.** `run_district_build.sh` rebuilds only
   buildings whose build key changed (reading, markup, geometry code;
   `src/build_keys.py`). `--force` rebuilds everything.
2. **One place per concept, and a check when there are two.**
   `check_diagram.py`, `test_golden`, `test_imports_and_arity`, `bench.py`
   and `cases.py` each catch a kind of drift. Add more of these.
3. **Delete on sight.** `src/` is the library and its stages; one-off
   scripts go in `tools/` or git history.
4. **Keep the comments and the stage guards.** Constants carry the
   measurement that set them. `preflight.py` and `run_stage.py` stop a
   resumed build from shipping stale geometry.

## What a national build needs

The original Queenstown district set was 15,353 buildings (v64 serves
32,838 across Queenstown Lakes). New Zealand has **3,413,097** in the LINZ outlines,
counted cell by cell by `tools/plan_national_regions.py`: about 222 times
that set. Scaled figures below use that ratio.

| thing | Queenstown | New Zealand (x222) | status |
| --- | --- | --- | --- |
| buildings download per street view | 238 kB | same | done: buildings are vector tiles |
| heat map | tiles per region | same per view | done |
| `data/` on disk with inputs kept | 139 GB | ~31 TB | inputs are deleted after publish, packs kept |
| district build | 6.1 h | ~1,347 VM-hours | regions run in parallel |
| face precompute | 6.6 h (16 cores) | ~1,461 VM-hours | sharded (`--shard i/n`) |
| region definitions | hand-written | 1,384, derived | done |

**Regions are derived.** `tools/plan_national_regions.py` grids the country,
asks LINZ how many buildings each cell holds, and splits any over 3,000.
Result: 389 populated cells of 1,836, 1,384 build regions. It plans only.

Watch for this trap: LINZ WFS returns `lon,lat` where the spec says
`lat,lon`, and the wrong order returns a valid response with zero
buildings. The planner first checks a box known to hold thousands.

**Data sources are keyed by location.** `src/surveys.py` resolves each
region's box against `config.SURVEYS`. A region covered by no listed survey
raises an error naming the surveys that exist, instead of quietly fetching
the wrong capture.

**Search** addresses are sharded by prefix when large, so search scales
past a flat index.

### Cost of a national run

One 16-core spot VM, data already on disk, scaled from Queenstown:

| | VM-hours | 1 machine | 20 | 50 | 100 |
| --- | --- | --- | --- | --- | --- |
| face precompute | 1,461 | 61 d | 3.0 d | 1.2 d | 0.6 d |
| region builds | 1,347 | 56 d | 2.8 d | 1.1 d | 0.6 d |
| **total** | **2,808** | **117 d** | **5.8 d** | **2.3 d** | **1.2 d** |

Compute divides cleanly: regions are capped at 3,000 buildings, so none
holds the run open.

**The download does not divide.** Queenstown needed 116 GB from LINZ;
nationally that is about 26 TB from one service:

| LINZ sustains | download takes |
| --- | --- |
| 0.5 Gbit/s | 4.8 days |
| 1 Gbit/s | 2.4 days |
| 2 Gbit/s | 1.2 days |
| 5 Gbit/s | 0.5 days |

More machines do not help and may hit a rate limit. Nobody has measured
what LINZ sustains (`tools/measure_fetch_rate.py`). Measure it on one city
first.

### Cities first

Half the country's buildings are in the densest 20 of 389 cells (Auckland,
Christchurch, Wellington, Hamilton):

| | buildings | compute | download |
| --- | --- | --- | --- |
| densest 20 cells | 1,709,541 (50%) | 1,403 VM-h (1.2 days on 50) | 12.9 TB |
| all 389 cells | 3,413,097 | 2,808 VM-h (2.3 days on 50) | 25.7 TB |

Do the cities one at a time and publish as each lands. Each is roughly a
day.

## Order of work

Done: buildings as tiles, heat map as tiles, incremental builds, survey
registry, derived regions, region packs, the yield-only layer, the output
contract, and per-region emit and combine. Built but not yet run: the
distributed fleet. Its first run is the one-city dress rehearsal
([national-rehearsal.md](national-rehearsal.md), archived plan). Design:
[scale-architecture.md](scale-architecture.md).

None of this needs geometry work to pause; they touch different files.
