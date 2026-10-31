# Scaling to New Zealand, and keeping changes fast

Capacity planning for maintainers, extrapolated from historical Queenstown
measurements. These are planning scenarios, not measured national throughput,
current release statistics or a cloud spending estimate.

## Keeping changes fast

Use narrow geometry checks before paying for regional rebuilds
([reviewer's guide](developers/reviewers-guide.md#check-each-stage-yourself)).
For a rebuild, choose incremental, force or yield-only according to the
[operator guide](data-maintainers/dataset-operations.md#4-build).
The [execution comparison](developers/tooling-execution.md#regional-workflow-differences)
explains why the local, district and worker paths are not interchangeable.

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

These times assume ideal parallelism. The planner targets at most 3,000
buildings per region, but geometry complexity, I/O and retries can still
produce stragglers.

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
contract, and per-region emit and combine. The distributed fleet is
implemented, but a successful multi-worker rehearsal is not established by
the code. Start with a one-city dress rehearsal
([national-rehearsal.md](national-rehearsal.md), archived plan). Design:
[scale-architecture.md](scale-architecture.md).

None of this needs geometry work to pause; they touch different files.
