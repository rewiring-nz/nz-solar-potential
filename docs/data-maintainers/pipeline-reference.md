# Pipeline reference

Look up the stages, reports and outputs of a single-region local run (also
used on a cloud profile VM). Start one with the [quickstart](quickstart.md);
diagnose failures with [Troubleshooting](troubleshooting.md).

## Regions

A region is one of:

- A LINZ suburb or locality, named `<council>/<town>/<suburb>` from the LINZ
  NZ Suburbs and Localities layer, e.g.
  `queenstown-lakes-district/arrowtown/arrowtown`. Find one with
  `tools/find_area.py`; see [Choosing an area](choosing-an-area.md). No
  `config.py` edit is needed. Its outlines and bounding box are written by
  `src/localities.py` to `data/regions/<id>/` (`building_outlines.geojson`,
  `locality.json`) at preflight, selected from the council's LINZ GeoPackage
  (see [Building outlines source](#building-outlines-source)).
- A legacy bounding-box region named in `config.REGIONS`, such as `pilot`
  (the original central Queenstown pilot) or `town_gorge_north`. Deprecated
  for new work: these still build, and the pipeline prints a warning.

Related settings:

- `src/surveys.py`: discovers covering LINZ sources from public layer
  metadata and keeps its metadata cache under ignored `data/sources/linz/`.
  The selected layer IDs are recorded in the estimate and pinned for the job.
  The web estimate counts no separate reference-imagery download when the
  selected reference is the same input as current imagery. Separate reference
  sources are still estimated independently.
- `config.CLOUD_SHARED_BUILD["local_region"]`: the local/profile default
  region, a locality id. Shared-cloud mode defaults to
  `config.CLOUD_SHARED_BUILD["regions"]`. Both sit in the block that build
  keys leave out, so changing a default rebuilds nothing.
  `config.PIPELINE_REGION` is the deprecated fallback when `local_region`
  is absent. Pass a region id to override any default.

Region ids are relative paths: a locality's outputs are under
`data/regions/<council>/<town>/<suburb>/` and `data/out/<council>/<town>/<suburb>/`.
Flat files named after a region (build markers, logs) replace `/` with `__`.

Within one `src/fetch_regions.py` invocation, the first successful survey
selection and acquisition footprints for each area are reused for DSM,
imagery and point-cloud fetching. A failed survey lookup may be retried in a
later pass. The local pipeline passes its preflight-selected layer IDs and
point-cloud metadata to subsequent stage processes through source overrides;
each selected layer is revalidated by the survey resolver.
Existing export-derived regional raster files are still reused without
verifying that they match this invocation's survey selection. Mapped inputs
require matching provenance before an existing mosaic can be reused.

The experimental read-only STAC planner in `src/raster_acquisition_plan.py`
can identify explicitly selected GeoTIFF assets intersecting multiple area
footprints without downloading raster payloads. Batch plans retain area, input
role and LINZ layer identity, deduplicate assets shared across requests, and
report explicit exports fallbacks separately with unknown sizes. Experimental
`src/raster_source_selection.py` turns the selected survey into planner
requests, preserving synthetic elevation, optional imagery and the existing
reference-imagery choice. STAC mappings must be supplied explicitly for each
LDS layer and input role, with the expected collection ID, asset key and
documented evidence; the collection ID is checked, but the code cannot
independently prove the mapping evidence. Unmapped layers remain exports
fallbacks. The standalone planner supplies no mappings by default; the local
web panel supplies the national DSM mapping for its preferred source. The planner classifies
each footprint as fully bounded, partially bounded or outside item bounding
boxes, which does not
establish valid-data coverage; missing asset sizes and cache state remain unknown.
Experimental planned preparation refuses a footprint unless the union of its
selected item bounding boxes covers it. This necessary check does not establish
that the tiles actually contain valid data throughout the footprint.
For surveys without legacy S3 fields, regional fetching constructs a per-area
plan from its selected survey and footprints, reports unmapped layers as
export fallbacks, and uses the planned layer IDs while checking the footprints
have not changed. Local web estimates create a batch plan from selected layer
IDs and currently available footprints using a separate geospatial worker,
preferring national DSM tiles by default. The estimate displays warnings
that export sizes are area-based, not shared-tile cache predictions. Local web
jobs retain the estimate snapshot. Pipeline preflight and the fetcher check the
snapshot digest and batch structure before their respective locality preparation
steps. Mapped plans additionally require a matching active queue reservation.
An incomplete path/digest handoff is an error. The fetcher revalidates
layers, roles and footprints against it before raster acquisition without
refreshing STAC asset metadata during that comparison. If those
choices changed, the job fails and requires a new estimate. Cloud estimates and
legacy S3 selections are not represented by this batch planner. The
experimental path below can use mapped cache tiles for local builds; the web
panel uses it with automatic estimates, while standalone jobs
require the explicit opt-in. Other inputs continue to use exports or their
legacy S3 inputs. Local estimates warn that their disk figures are not
guaranteed peak-space bounds.
For separately queued local jobs, each job retains only its own region's
selection, assets and fallback inputs from the approved batch, without
rediscovering sources; shared assets remain identified by the same source
identity in each job's snapshot.
Export-tile and multi-footprint merges write to disk in Rasterio chunks instead
of materialising the complete mosaic in memory. An export merge reads only
tiles named by that export archive, not leftover files from an earlier attempt.
The local queue persists the submitting process's survey overrides and plan
path and snapshot digest with each task; a worker does not substitute its own
source environment. The fetcher rejects a changed snapshot. Unrelated
environment variables such as `LINZ_API_KEY` are not copied into the task.
An experimental `src/raster_cache.py` provides leased storage and a
cache-only transfer budget for offline validation. Before publication it reads
every band and mask in bounded windows, so unreadable later blocks reject a
download even when its size and checksum match the advertised payload. This
full decode adds local I/O and processing time on a cold download. Its asset
leases protect in-use entries and interrupted replacements are recovered from their matching
metadata. An asset with neither a nonempty revision nor a published checksum
is re-downloaded on each lease rather than treated as fresh solely because its
URL and size match; inspection reports an intact entry as `unversioned`, not
corrupt. Its separate experimental queue cleanup ranks a bounded set of
caller-supplied plans by exact asset membership and distance to their individual
footprints; plans are preferences, not leases. An admitted local queue task
does not run this cleanup: insufficient capacity blocks admission without deleting
cached assets or partials. `src/raster_disk_plan.py` can forecast a conservative sum
of transfers, exports, mosaics, scratch and output allocations by filesystem,
using caller-supplied size bounds and cache inspection. It includes a free-space
reserve and a conservative cache-budget check. Callers may also supply named
additional allocation bounds on other filesystems, such as a separate
temporary or point-cloud directory; each directory is probed and included in
cooperative reservation totals. Those allocations must be identified and
bounded by the caller, not inferred from the forecast. This forecast neither reserves
disk space nor enforces admission, and its accuracy depends on those supplied
bounds. Experimental `src/raster_reservations.py` coordinates forecasts from
cooperating jobs through one local registry and holds OS locks for each job's
lifetime. It includes outstanding reservations when checking free space and
cache budget, counts shared missing assets once and rejects conflicting versions.
Its separate experimental queue-cleanup admission helper first removes expired unlocked partials,
then, if necessary, ranks unleased cache entries against the current plan and
a bounded list of upcoming plans. It re-inspects the cache and re-probes the
filesystems before each admission attempt. It refuses to delete cached assets
when the entire cache would not resolve the shortfall or when a different
filesystem is short. Queue preferences are not pins; active leases are.
An unlocked record blocks all new admissions: its owner may have died while a
consumer continues running. After independently stopping the owner and every
consumer, recover that specific record with
`.venv/bin/python tools/raster_reservation_recover.py REGISTRY ID --confirm-consumers-stopped`.
The ID is the 32-character JSON filename stem reported in the admission error;
the record's `owner_pid` is a diagnostic hint, not proof that descendants have
stopped, and older or incomplete records may not have one. Recovery rejects
locked records but cannot itself verify child-process termination. When overlapping jobs
or multiple inputs need the same unversioned asset, it also reserves room for a
simultaneous committed copy and replacement download; each lease re-downloads
that asset.
Experimental `src/raster_job_supervisor.py` runs a command in a POSIX process
group inside the reservation. When the command exits, it releases only if the
group has disappeared; an extant group or an inconclusive lifetime check leaves
an orphan record and blocks subsequent admission until explicit recovery.
If waiting is interrupted, the supervisor sends the group SIGTERM and then
SIGKILL if it still exists; it retains the record if group termination cannot
be confirmed.
An owner crash also leaves the record in place. This detects surviving members
of that group, not descendants that deliberately create a new session or
process group. The caller must ensure every disk-consuming descendant remains
in the group. It is not connected to the live queue or build.
Because inspection
does not pin cache entries, admission also reserves a possible re-download of
each cached asset until leases or a stronger handoff are integrated. This may
reject a warm-cache job even when it would fit; the read-only forecast still
reports expected transfers without that contingency. It does
not allocate disk blocks, cannot stop unrelated disk use, and relies on all
jobs sharing the same registry, accurate size bounds, a fresh cache inspection
of the same cache and reliable local POSIX locks. In-progress reservations are
conservatively counted even when some bytes have already been written, so they
may refuse work that would fit. Its reservations are not connected to the queue
or builds unless the experimental opt-in below is used.

#### Experimental local queue admission

An operator can opt a local job into cooperative admission by
setting `SOLAR_RASTER_JOB_BOUNDS` to a version 1 JSON document before submitting
the job. The worker must run on POSIX, the task must have an approved raster
snapshot and digest, and it must use
`--no-preview-server`. Mapped inputs additionally require explicitly reviewed
source mappings and per-role processing options, as described below. The panel
inherits this setting, but its generated snapshot, region and options must
match the document for each job; operator-bounded web jobs must be submitted
one area at a time.
Without this setting, the existing queue behavior is unchanged and no
whole-job reservation is made.

The local web panel can alternatively generate an approximate
admission snapshot for its default national DSM path. Its allocation assumptions
and limitations are described in the [web guide](web-pipeline.md); unlike the
operator contract below, they are not workload-specific attestations.

The document must contain these fields:

| Field | Required value |
| --- | --- |
| `version` | Integer `1`. |
| `region`, `workspace`, `plan_sha256` | Exact region, resolved absolute checkout path and approved snapshot SHA-256 digest. |
| `include_imagery`, `paid_referee` | Booleans matching the queued options. |
| `evidence` | Nonempty explanation of the workload-specific estimates and their basis. |
| `cache_policy` | Absolute path to a version 1 cache-policy JSON file explicitly setting `max_bytes` and `min_free_bytes`. Effective cache, cleanup and metadata-refresh settings are frozen at submission. |
| `work_dir`, `output_dir` | Existing absolute directories on the filesystems holding the corresponding allocations. |
| `mosaic_bytes`, `scratch_bytes`, `output_bytes` | Positive integer byte bounds for simultaneous new allocations on those filesystems. |
| `export_bytes` | Positive integer byte bound for each export fallback; the forecast multiplies it by the number of fallbacks. |
| `extra_allocations` | Array of objects with exactly `name`, `directory`, and `bytes`; names must be distinct and not `cache_transfer`, `exports`, `mosaics`, `scratch`, or `outputs`. Directories must exist and be absolute; bytes must be positive integers. An empty array is allowed. |

For mapped inputs, `processing` must contain exactly the mapped roles, each
with `resolution` and `resampling` fields. Roles are `dsm`, `imagery`, or
`reference_imagery`; resolution is null or a positive finite number in metres
and resampling is `nearest`, `bilinear`, `cubic`, or `average`. Choose these
to match the intended existing mosaic grid and validate the results; automatic
equivalence to export-derived mosaics has not been established. For an
export-only job, omit `processing` or use an empty object. Unknown fields are
rejected.

These are **operator attestations**, not measured bounds or enforced limits.
Include all simultaneous incremental allocations, including locality preparation,
point clouds, the wide DEM, exports and extraction, mosaics, processing scratch,
temporary directories and outputs, and account for every filesystem those
operations use. The tool does not verify that directories or amounts cover the
actual workload. A policy unknown-asset bound is likewise an assumption, not a
verified size. Do not opt in without defensible workload-specific bounds.
The reservation is cooperative, not allocated disk space: unrelated users and
jobs without the same registry can still exhaust disk. The registry is under
the configured cache directory, so jobs with different registries do not
coordinate even if they share a filesystem.

Before launching the pipeline, the worker rechecks the approved snapshot and
frozen bounds, inspects the cache and reserves capacity exclusively in that cache's
registry. Another cooperating reservation using the same registry is refused
until the active operation ends. This does not coordinate separate caches or
non-cooperating processes, and standalone cleanup helpers are not globally
excluded by this reservation. Per-tile leases still protect inputs in use.
It does not automatically
clean the cache to make room. The reservation is bound to the frozen bounds
and rechecked during mapped acquisition. When the pipeline process and its
recorded process group have stopped, the worker releases the reservation and
workspace lock and reports the pipeline's result. A later local submission
automatically recovers a stale raster lock when its recorded worker and process
group have stopped and the reservation is unlocked. Recovery is serialized.
Process-group checks cannot detect detached descendants, so builds must keep
all consumers in the supervised group. Incomplete ownership records, active
groups, an active reservation or recovery errors prevent automatic recovery.
If automatic recovery is impossible, independently verify that the worker and
all consumers have stopped, inspect `data/pipeline-workspace.lock/owner.json`
and use the recovery command above for that exact reservation before removing
the specific stale workspace lock. Never recover a live job.
If the approved mapped plan's STAC check is older than its frozen
`metadata_refresh_s`, the worker queries the selected collections again before
admission. Unchanged metadata permits the approved plan to run; changed
metadata or a failed check stops the job and requires investigation or a new
estimate and approval. An overdue check alone does not invalidate cached
payloads. Older plans without a check time are rechecked; older mapped task
snapshots without a frozen refresh interval must be resubmitted. This is an
on-admission check, not a background refresh of idle cache entries.
Experimental `src/raster_region_plan.py` resolves already-prepared regions
using the fetcher's bbox, acquisition-footprint and survey resolvers, then
creates a shared batch plan with caller-supplied explicit mappings. It rejects
selected legacy S3 sources rather than interpreting them as exports. A
synthetic-only selection with imagery excluded has no raster batch. Survey
resolution may refresh its local metadata cache; no raster payload is
downloaded. Public STAC item metadata is fetched with at most eight concurrent
requests per collection, preserving deterministic link order and failing the
plan if any item cannot be read. The web estimate's geospatial worker uses the same regional
planner with its already-selected surveys and `SOLAR_RASTER_SOURCE_MAPPINGS`,
or the national DSM mapping by default for local web builds. Without mappings,
plans describe export fallbacks; mapped execution requires admission as above.
For an already-prepared area, `.venv/bin/python tools/raster_plan.py AREA`
prints an experimental batch plan as JSON. `--no-imagery` excludes imagery.
`--mappings PATH` loads a JSON object with `version: 1` and a `mappings`
array; each entry must contain `layer_id`, `role`, `collection_url`,
`collection_id`, `asset_key` and `evidence`. Mappings are explicit assertions:
record evidence of source equivalence, not merely a similar title. No mappings
are supplied by default, so inputs remain reported export fallbacks. To inspect
the cache, supply `--cache-dir`, `--cache-max-bytes` and
`--unknown-asset-bytes` together, with optional `--min-free-bytes`; all sizes
are bytes. Inspection reads cached content and creates cache bookkeeping
directories but does not transfer raster payloads.

An opt-in mapping for the national 1 m DSM is supplied in
[`tools/national-dsm-mapping.json`](../../tools/national-dsm-mapping.json).
LINZ [layer 122082](https://data.linz.govt.nz/layer/122082-new-zealand-lidar-1m-dsm/)
links to this S3 collection as its source; the collection's `visual` asset
is the elevation GeoTIFF, not a rendered terrain image. LINZ's
[elevation TIFF specification](https://github.com/linz/elevation/blob/master/docs/tiff-specification.md)
describes DSMs as single-band float elevation data. Select the national raster
explicitly through a region's `raster_dsm_layer` survey override and supply this file with
`--mappings` or `SOLAR_RASTER_SOURCE_MAPPINGS`; providing the file alone does
not replace an already selected regional survey. The national S3 composite
may be newer than its manually imported LDS layer. Its item time ranges may
span multiple contributing surveys; use LINZ's
[survey index](https://data.linz.govt.nz/layer/122149-new-zealand-lidar-1m-dsm-survey-index/)
and [capture dates](https://nz-elevation.s3-ap-southeast-2.amazonaws.com/new-zealand/new-zealand/dsm_1m/2193/capture-dates.geojson)
to investigate actual sources and dates. The mapping does not prove complete
valid-pixel coverage or alignment with separately selected point clouds and
imagery. `raster_dsm_layer` changes the raster input while leaving the
regional survey selection, its reference-imagery year and its point-cloud
metadata intact. It revalidates the raster layer's published bounding extent
but does not prove that the national composite and reference survey match.
National-composite output equivalence and benchmark results have not yet been
established; the mapped execution restrictions still apply.
New mapped mosaic provenance retains the selected footprint's published
capture-area and contributing-survey metadata when available. These records
are advisory snapshots of simplified geometries, not evidence that every
pixel came from a particular survey or has valid elevation. Existing verified
mosaics without those optional records are not automatically replaced.

For example, to inspect an already prepared `pilot` with the national DSM,
without downloading raster payloads:

```sh
SOLAR_SURVEY_OVERRIDES='{"pilot":{"raster_dsm_layer":122082}}' \
  .venv/bin/python tools/raster_plan.py pilot --no-imagery \
  --mappings tools/national-dsm-mapping.json
```

The override must also be present when estimating and executing a job; the
approved plan rejects changed selections. Before opting in, review actual
coverage and the complete selected inputs, including the point-cloud and
reference-imagery consequences described above. National DSM plans compare
each footprint with LINZ's published simplified capture-area geometry and
record whether it is covered, intersects or lies outside, alongside the
published metadata checksum. The checksum is recorded but the metadata
download is not independently checksum-verified. This is an advisory boundary, not a valid-pixel
mask: LINZ notes that it can include nodata. The web estimate warns on partial
or absent overlap, and mapped preparation refuses footprints wholly outside
the boundary rather than treating a tile rectangle as coverage. An overdue
admission rechecks that coverage metadata along with asset identities; a
changed result requires a new estimate.
Plans also record the survey IDs, source links and capture dates of LINZ's
simplified capture-date geometries intersecting each footprint. These
intersections are not a per-pixel source attribution. The web estimate warns
when the selected reference survey's name is absent from the intersecting
names; matching names do not establish dataset equivalence. Due plans recheck
these metadata before admission.

Alternatively, `SOLAR_PREFER_NATIONAL_DSM=1` selects layer 122082 as the
raster input for LiDAR areas without an explicit `raster_dsm_layer` override;
synthetic areas remain synthetic. An explicit override, including `null`,
takes precedence. Supply the mapping file separately to use the native cache;
without it the national layer remains an exports fallback. The selected raster
layer is pinned in the estimate and job so changing the environment later does
not silently change an approved job.

The inspected cache summary also classifies logical asset-file bytes as
`leased_bytes`, `reclaimable_bytes` or `unmanaged_bytes`. Reclaimable means
registered and unlocked at the instant of inspection, not guaranteed removable
later: leases, queue preferences, invalid spatial metadata and filesystem
errors can change the outcome. Unmanaged files and interrupted replacements
are excluded from the reclaimable figure; partial downloads and metadata are
not counted in the asset budget. These figures are not physical-block usage.
Alternatively, `--cache-policy PATH` reads a version 1 JSON object and reports
effective values and their sources in `cache_policy`. For example:

```json
{"version": 1, "directory": "raster_cache", "max_bytes": 10737418240}
```

Relative `directory` paths are relative to the policy file; omitted directory
defaults to `raster_cache` beside that file. Omitted `max_bytes` defaults to
10% of the cache filesystem's total capacity, capped at 50 GiB. The default
`min_free_bytes` is 10% of total capacity, capped at 10 GiB. The
`unknown_asset_bytes` default is the smaller of 1 GiB and the cache maximum:
it is a transfer limit, not a guarantee of upstream size; larger assets fail
until an appropriate bound is configured. Other integer overrides are
`high_water_bytes` (default 90% of maximum), `target_bytes` (70%),
`max_unused_age_s` (90 days), `metadata_refresh_s` (one day),
`partial_expiry_s` (one day), `queue_lookahead` (3, including the current
plan), and `near_distance_m` (5000). Thresholds must satisfy
`target_bytes < high_water_bytes <= max_bytes`. These defaults size policy
relative to the machine, not a VM-specific disk, and do not guarantee free
capacity. The planner uses the cache budget, unknown asset bound and free-space
reserve; the experimental admission helper can consume the cleanup policy.
When the cleanup policy is passed to the experimental admission helper, usage at
or above high water triggers a best-effort cleanup toward the target; active
leases remain protected and the cleanup result reports if the target was missed.
The experimental local queue checks the age of approved mapped plans at
admission as described above; idle cache entries are not refreshed in the
background. Configuration alone does not enable mapped acquisition or
admission.
Individual cache CLI options cannot be combined with `--cache-policy`.
To request a read-only filesystem forecast, also supply `--work-dir`,
`--output-dir`, `--mosaic-bytes`, `--scratch-bytes` and `--output-bytes`.
Supply `--export-bytes` as a positive per-fallback bound when exports are
present. Use `--extra-allocation NAME DIRECTORY BYTES` for each additional
allocation such as a separate temporary or point-cloud filesystem; the option
may be repeated, and names must be distinct from each other and the standard
allocation names. Directories must exist (cache inspection creates its own directory);
byte bounds must include all simultaneous allocations expected for the job.
The JSON `disk_forecast` is a point-in-time check, not a reservation or a
guarantee that the job fits; it does not start or admit a build.

Experimental `src/raster_execution.py` accepts that batch plan and explicit
per-input destinations, reserves caller-supplied disk bounds before acquiring
anything, prepares mapped tiles, and invokes a caller-provided export callback
for unmapped layers. Optional `RasterProcessing` values keyed by region and
role specify a mapped input's EPSG:2193 resolution and resampling; reprojecting
a non-NZTM source requires an explicit resolution. Reuse verifies the same
processing values. Existing mapped mosaics are verified before any planned
input is acquired, so an unverified legacy mosaic cannot cause a later
failure after an earlier export has already started. The reservation context
must remain open until the whole
consuming job and its child processes have stopped. The callback is responsible
for honoring the fallback's layer and footprints and for any validation of
existing export mosaics. The live fetcher uses the mapped-input preparation
portion under the queue-held reservation; the separate experimental execution
entry point manages its own reservation.
Experimental `src/raster_regional_execution.py` adapts an approved regional
snapshot to that execution boundary. It re-resolves the selected survey and
footprints using explicitly supplied mappings, or reconstructs the explicit
mappings from the caller-verified snapshot when passed `None`. Snapshot mapping
reconstruction rejects conflicting or incomplete assertions and does not
re-read mapping files or discover sources. It rejects changed selections and
legacy S3 inputs, requires explicit processing options for every mapped input,
and checks that regional destinations are on the reserved work filesystem.
Synthetic-only selections with imagery excluded still enter the reservation
without requiring a raster batch; synthesis remains the consuming job's work.
Mapped inputs use leased native tiles; unmapped inputs use the existing
regional exports fetcher, including its legacy reuse of existing export mosaics
without verified provenance. The caller must verify the snapshot's approval,
provide conservative whole-job allocation bounds, and keep the context open
until all consumers have stopped. This adapter does not independently supervise
a build, is not connected to the live queue, and fails rather than silently
continuing if an optional imagery acquisition fails.
Experimental `src/raster_regional_job.py` combines that adapter with a
supervised POSIX command under the same reservation. It checks the supplied
approved snapshot digest before acquisition, then releases only after the
command's process group has exited. If acquisition is interrupted or fails
after admission, it conservatively retains an orphan reservation because it
cannot prove acquisition subprocesses have stopped. The same explicit
recovery procedure applies. It cannot detect detached descendants of either
acquisition or the command; callers must ensure none escape, supply bounds for
the whole workload and verify that the command actually consumes these
rasters. It is not connected to the live build or queue.
Experimental `src/raster_prepare.py` can prepare a new EPSG:2193 mosaic from
leased cached tiles using the existing S3 crop grid. It refuses to overwrite an
existing mosaic, mixed source identities, incompatible tile grids, bands or
nodata, and uncovered areas when missing pixels cannot be represented by a
mask or nodata. Where STAC provides a native CRS, preparation checks it
against the GeoTIFF header and records the actual header CRS in provenance.
Existing mosaics without that recorded CRS cannot be verified against a plan
that declares one. At least one tile header must overlap the requested
footprint; this is not a valid-pixel coverage test. The LIBERTIFF route rejects
non-alpha per-dataset masks because
preserving them has not been established; those inputs must not be silently
treated as fully valid. This path is not yet a replacement for the regional
fetcher and has not established equivalence with LINZ exports.
Tiles in another native CRS can be reprojected on disk to an explicitly
specified EPSG:2193 resolution and aligned grid. That path requires a common
nodata value and rejects alpha and per-dataset masks; native cached tiles are
not modified. A numerical WGS84-to-NZTM fixture tests this path.
Its `prepare_planned_mosaic` entry point consumes an existing batch plan for
one declared area, role and footprint, selects only intersecting assets for
that input and checks their collection, asset key and layer identity. An
exports fallback or a footprint absent from the plan is rejected rather than
silently switching sources. `prepare_planned_region` additionally merges all
declared footprints and records them and their combined sources in the sidecar;
it selects assets by the individual footprints rather than their enclosing
rectangle. This experimental merge requires matching aligned grids and nodata,
and rejects per-dataset masks. Neither entry point reserves disk for the entire
build or is connected to the regional fetcher.
Experimental `ensure_planned_region` may reuse a prepared mosaic only after
checking its sidecar and output hash against the selected area, role, layer,
footprints, processing and source asset versions. It rejects legacy or orphaned
outputs and assets with neither a revision nor a published checksum; it never
replaces them automatically. A revision or checksum records the metadata
available at planning time, not proof that upstream metadata is current or
authentic. This check is not yet used by live regional fetching.
When explicitly requested, it writes a `<mosaic>.source.json` sidecar with
the selected layer, processing options and actual SHA-256 hashes of each
leased tile and the prepared mosaic. The plan-driven entry point also retains
the selected survey name and mapping evidence; per-tile provenance includes
available capture dates, native grid metadata, licence and providers. An absent
published upstream checksum remains absent; the local hash does not establish
upstream authenticity.
`src/raster_provenance.py` verifies the sidecar fingerprint and mosaic bytes
even after cache eviction. A crash between publishing the sidecar and mosaic
can leave an orphan sidecar; verification fails and an operator must inspect it.
Live regional fetching does not yet produce these sidecars. When a verified
sidecar is present, stage markers and building keys include its source and
output content identities; metadata such as transfer URLs does not change
those identities. Tracked inputs are checked before and after the relevant
stage or layout build, and a mismatch prevents recording completion. Legacy
rasters without sidecars remain untracked and retain the existing timestamp
behaviour. Verification reads the whole tracked mosaic, which can add I/O.
Before rewriting layouts, a full build clears its prior building keys and a
patch clears the keys for the buildings it will change. A failed or interrupted
rewrite therefore cannot be mistaken for a completed incremental build.
The regional packer preserves tracked DSM and imagery lineage through packing
and restore, distinguishing re-encoded DSM from building-area imagery subsets.
Legacy inputs and packs remain untracked. See
[The region pack](../maintainers/scale-architecture.md#the-region-pack) for
publication and verification limits.

## Building outlines source

LINZ [NZ Building Outlines](https://data.linz.govt.nz/layer/101290-nz-building-outlines/)
are the source of truth for which buildings a region holds. `src/linz_exports.py`
downloads them once per territorial authority through the LINZ Exports API
(GeoPackage, EPSG:2193, cropped to the council boundary from LINZ layer 3036)
and caches them:

```
data/sources/linz/layer-101290/<council>/building_outlines.gpkg
data/sources/linz/layer-101290/<council>/manifest.json
```

The manifest records the LINZ layer version, publish date, export id,
download time and feature count. The cached file is reused until LINZ
publishes a new layer version. A locality's outlines are then selected
locally with the same rule the WFS filter used (`suburb_locality`,
`town_city`, `territorial_authority`); both sources give identical
buildings. To fetch or refresh a council by hand:

```sh
.venv/bin/python src/linz_exports.py fetch "Queenstown-Lakes District"
.venv/bin/python src/linz_exports.py fetch "Queenstown-Lakes District" --refresh
```

If the export cannot be used (no export permission on the key, LINZ queue
failure), the locality falls back to LINZ WFS and `locality.json` records the
reason under `outline_source`. Set `SOLAR_OUTLINE_SOURCE=wfs` to use WFS
directly. LINZ also offers GeoParquet exports; the pipeline does not use
them yet.

## Needs

Complete [Local setup](local-setup.md) for system tools, Python and credentials.
Additional operating limits:

- About 10 GB free for a small region.
- Optional: the vision step needs torch, segment-anything, the SAM checkpoint
  and both roof-line models in `data/models/`. Without them it is skipped.

`--mode cloud --profile NAME` runs this sequence in the existing developer-VM
checkout; update that checkout first. Shared cloud mode instead uses the
district engine, not the table below. Cloud commands are in the
[cloud quickstart](quickstart-cloud.md); exact backend differences are
in the [execution reference](../developers/tooling-execution.md#stage-plans-across-engines).

## Local task queue

Local runs now submit a task to a loopback coordinator and start one worker
process by default. No Google account, GCS bucket, `gcloud` or SQLite is
needed for queue operation. Fetching source data still needs the LINZ
credentials and dependencies described in [Local setup](local-setup.md).
Process cleanup is tested locally on macOS; CI is configured to run these
tests on Linux. Use WSL for Windows, not native Windows processes.

```sh
bash quickstart.sh pilot --no-open-browser
bash quickstart.sh pilot --workers 2 --no-open-browser
```

Each invocation still builds **one region**, with the same numbered stages
below. Extra workers do not split that region. Tasks in the same checkout
serialize because they share caches, stage markers and the combined map.
Use separate, human-managed checkouts and data directories for concurrent
builds; do not symlink their writable data trees together. The coordinator
does not create worktrees, allocate CPU/RAM or manage separate environments.

The printed queue directory defaults to `data/pipeline_queues/<id>/`.
`--queue-dir PATH` chooses a persistent directory instead. It must not be
owned by another running coordinator; it is not an option for attaching to
one. A directory can retain prior tasks, including failures; prefer a new
directory for a new run.

### Manually submit and run multiple workers

For debugging, start one coordinator in a terminal:

```sh
.venv/bin/python -m tools.pipeline_queue serve --queue-dir data/my-local-queue
```

In another terminal, submit tasks **before** starting workers:

```sh
.venv/bin/python -m tools.pipeline_queue submit --queue-dir data/my-local-queue \
  pilot --no-open-browser
.venv/bin/python -m tools.pipeline_queue submit --queue-dir data/my-local-queue \
  town_gorge_north --no-open-browser
```

Run each worker command in its own terminal, using distinct names:

```sh
.venv/bin/python -m tools.pipeline_queue worker --queue-dir data/my-local-queue --worker one
.venv/bin/python -m tools.pipeline_queue worker --queue-dir data/my-local-queue --worker two
```

These two tasks serialize in the same checkout. To exercise concurrent
builds, submit a task with `--workspace /absolute/path/to/another/checkout`
and `--python /absolute/path/to/its/.venv/bin/python`. That checkout needs
the queue implementation, its dependencies and its own prepared environment.
Workers exit when the submitted batch is terminal; start workers again
after submitting more tasks.

Inspect status while the coordinator runs:

```sh
.venv/bin/python -m tools.pipeline_queue status --queue-dir data/my-local-queue
```

### Evidence, conflicts and recovery

- `state.json` persists task specifications, worker/attempt ownership and
  terminal outcomes. `attempts/<task>-<attempt>.log` retains subprocess output.
  Existing `data/pipeline_runs/` reports and preview URLs are unchanged.
- Numbered pipeline output streams to the worker terminal. Queue success
  means the pipeline returned zero; read its report for `DEGRADED` steps.
- Tasks record the Git commit, hashes of selected configuration and queue files,
  and a hash of Python and shell source files under `src/` and `tools/`.
  A change before execution rejects the task. This does not detect changes
  made during execution and is not a full dependency/model/environment
  snapshot; developers must still agree on matching inputs and versions.
- The coordinator binds only to loopback. `.connection.json` holds a local
  access token and is owner-readable; never share it or publish queue files.
  This is a trusted local developer tool, not a multi-user/public service.
- One active task per checkout is allowed within a queue. Across queues,
  `data/pipeline-workspace.lock/` detects a conflicting queued build and
  fails it visibly. Older/direct stage scripts do not honor that lock:
  humans must coordinate those operations.
- On POSIX, the queue keeps the workspace lock and fails the task if the
  pipeline's process group still exists after the leader exits or cannot be
  checked. The lock's `owner.json` records the process group when launch
  succeeds. This check cannot detect descendants that leave that group;
  inspect and stop all consumers before manually recovering the lock.
- Ctrl+C stops the foreground coordinator/worker. The pipeline-managed pool
  stops its workers on interruption. Attempt ownership rejects stale
  acknowledgements; there is no automatic lease takeover, retry or failover.
  Failed tasks stay failed and require deliberate resubmission with a new id.
- Clean coordinator shutdown marks pending/running tasks interrupted.
  After a crash, pending tasks remain pending on restart; running tasks
  become interrupted. A hard-killed worker can leave a task running until
  coordinator shutdown/restart.
- After a crash, inspect `state.json`, attempt logs and lock `owner.json`
  files. Stop any surviving workers/child processes before manually removing
  the specific stale `coordinator.lock/` or `pipeline-workspace.lock/`
  directory. Restarting a coordinator marks previously running tasks
  interrupted, never successful. Do not remove locks on a live run.

The existing GCS fleet queue is **not migrated** by this first local slice.
It still has different task, retry and regional-execution semantics; see
the [consolidation boundary](../developers/tooling-execution.md#local-queue-consolidation-boundary).

## Local and profile-VM steps

Steps 04–17 are the shared stage plan ([src/build_plan.py](../../src/build_plan.py)),
the same per-region steps the district and fleet engines run; steps 12 and
18–21 are local-only. The local pipeline writes stage markers but does not
pass `--skip-done`: rerunning creates a new report and reruns the commands,
with any input reuse handled by individual tools.

| # | Step |
|---|---|
| 01 | Preflight: validate area and runtime |
| 02 | Fetch outlines, elevation, imagery, point cloud; ensure the wide DEM (`src/fetch_regions.py`, `src/fetch_dem_wide.py`) |
| 03 | Vision precompute (optional) |
| 04 | Fit outlines to the roof (optional; `SOLAR_OUTLINE_FIT=0` skips) |
| 05 | Agree identical roofs (optional; `SOLAR_REPEAT_ROOFS=0` skips) |
| 06 | Register imagery alignment (optional) |
| 07 | Build roof geometry and layout |
| 08 | Gate panel layouts |
| 09 | Screen demolished buildings (paid referee only if approved, below) |
| 10 | Rerank panel layouts |
| 11 | Derive building solar potential |
| 12 | Render building verification report |
| 13 | Patch roof confidence |
| 14 | Bake per-building horizons |
| 15 | Build per-pixel solar heat map |
| 16 | Add building addresses (optional) |
| 17 | Emit regional map tiles (`src/emit_region.py`) |
| 18 | Combine map dataset (`src/combine_regions.py`) |
| 19 | Build local 3D terrain tiles |
| 20 | Validate map contract |
| 21 | Start local map preview |

**Paid step question.** Before queueing (local) or inside the cost
confirmation (cloud), the pipeline asks whether to use the paid demolition
referee, with an estimate. Only `yes` approves; Enter, a non-interactive
run or `--yes` declines. `--paid-referee` / `--no-paid-referee` answer it in advance.
See [demolition screening](dataset-operations.md#demolition-screening).

**Unattended and interactive runs.** `--yes` (`-y`) accepts the cloud cost
estimate without a prompt; the estimate is still printed and logged. Review
gates never block a run by default: a failed deploy gate on the shared cloud
preview prints `GATE WARNING` and continues. `--interactive` stops and asks
at a failed gate instead (it cannot be combined with `--yes`). The local
pipeline has no review gate, so `--interactive` changes nothing there. See
[cloud quickstart: deploy gate](quickstart-cloud.md#deploy-gate).

Exact definitions: [tools/pipeline.py](../../tools/pipeline.py).

## Outputs

- `data/pipeline_runs/<UTC-run-id>/`: `report.md` (status, timing and effect
  of each step), `status.json` (the same step statuses and timings as JSON,
  without commands; read by the [web pipeline](web-pipeline.md)), `run.log`
  (pipeline start and finish timestamps plus step output tagged `[QS-NN]` and
  run timing tagged `[RUN]`), and the preview server's PID and log. Step
  completion and total runtime are shown as `hh:mm:ss`; the start and finish
  timestamps are UTC. A local run prints `PIPELINE_RUN_DIR=<path>` when it
  creates this folder.
  `--no-preview-server` passes step 21 without starting a server, for callers
  that already serve the map.
- `data/out/<region>/`: the region's emitted tiles and summaries.
- `data/`: the combined map. **Step 18 replaces whatever map data was there**
  with the selected region. Other emitted regions remain under `data/out/`
  but are not included in this combine. Don't run it over map data you want
  to keep. Root `data/solar_potential.geojson` is not a current map input.
- `data/regions/<region>/quickstart_report.html`: per-building check cards.

The preview serves the map and combined data on `127.0.0.1` and needs
internet access for basemaps. It does not expose the pilot-only refit API
([execution boundaries](../developers/tooling-execution.md#deployment-and-ownership)).
The pipeline starts the preview server in a separate session; it keeps running
after the pipeline finishes, and `Ctrl+C` in the pipeline terminal does not
stop it. Inspect the run's recorded PID and verify it is still the preview
server before stopping it:

```sh
cat data/pipeline_runs/<run-id>/map-preview-server.pid
ps -p 12345 -o pid=,command=
kill 12345
```

Replace `12345` with the PID from the first command. Confirm the command names
`tools/pipeline_serve.py` from this checkout before sending the signal. Plain
`kill` sends SIGTERM, which stops the preview even if it inherited an ignored
SIGINT from a background launch. This stops only the preview HTTP server; it
does not cancel a pipeline build or delete generated data. If you started
`tools/pipeline_serve.py` directly in a terminal, `Ctrl+C` there also stops it.

If the PID file is missing
or stale, find the listener on the port shown in the preview URL with
`lsof -nP -iTCP:<port> -sTCP:LISTEN`, inspect its command with `ps`, and stop
it only if it is the preview server. A browser tab whose server is no longer
listening is simply stale; close the tab rather than killing an unrelated
process.

## Statuses

- `PASS`: ran and its output checks passed.
- `DEGRADED`: carried on without a source or capability. Read the effect.
- `SKIPPED`: optional step not run (usually vision).
- `FAIL`: stopped. Later steps show `NOT RUN`.

A finished run can still be degraded. Missing LiDAR weakens the geometry and
can leave the heat map empty; missing imagery, addresses or terrain each lose
their part. Read the report before trusting a result.

## Cleaning up

**Do not delete `data/` wholesale.** It contains curated labels, benchmarks,
verdicts and trained models as well as generated files. Do not delete a
whole region directory without inspecting it either: it can contain review
results and provenance you need to keep. Preserve `.env` and `.venv`.

To reclaim local space, identify regenerable inputs for the selected region
before removing them. The wide DEM and point-cloud tiles can be shared by
neighbouring regions. The
[regional publication cleanup](../maintainers/scale-architecture.md#deleting-inputs)
checks upload sizes and shared-tile ownership; it is not a blanket cleanup
command for a local pipeline run.

`git clean -fd` leaves ignored inputs but deletes untracked work, including
new labels or documentation. Preview with `git clean -nd` and inspect every
path before using it. Do not use `git clean -fdX` to reset this project.
