# Building New Zealand one region at a time

Storage and coordination design for maintainers: why regional assembly,
input packs and a bucket queue bound the work. For capacity estimates, use
[national planning](scaling-and-iteration.md); for commands, use
[dataset operations](data-maintainers/dataset-operations.md). Measurements
below come from the Queenstown build, not a national fleet rehearsal.

## The idea

The unit of work is a region. The unit of output is a tile.

Each region emits its own tiles (`src/emit_region.py`) and a combine step
joins them (`src/combine_regions.py`). Nothing holds more than one region in
memory, so the combine treats 1,384 regions the same as 35. There is no
merged district file; `src/merge_regions.py` is retired.

| output | each region emits | combine does | why it is safe |
| --- | --- | --- | --- |
| `panel_layouts.pmtiles` | its own pmtiles | `tile-join` | regions do not overlap; a border tile holds both |
| `buildings.pmtiles` | its own pmtiles | `tile-join` | same |
| `building_cells.pmtiles` | partial sums per grid cell (JSON) | add the partials, tile once | a sum of sums is a sum |
| `building_detail/13/x/y.json` | its buildings, keyed by tile | merge per tile | keyed by building id |
| `heatmap_tiles/z/x/y.png` | its tiles | copy; composite where two regions share a tile | |
| `addresses/` | its addresses | concatenate, shard by prefix | search reads one shard |
| `seasonal_curves` | nothing | one file per degree of latitude | curve shape depends on latitude |
| `summary.json` | totals and provenance | add | the deploy gate reads these |

Above 200 inputs the join runs in batches, then joins the batches. Density
deciles, terrain masks, panel shrinking and the detail split run per region
inside `emit_region`. Combining Queenstown takes about a minute; a patched
building means re-emitting its region and recombining.

## Where the data lives

Site code and generated data have separate lifecycles: GitHub Pages serves
the page; object storage serves versioned map data and holds private build
inputs. Resource names and access are in [Team access](data-maintainers/team-access.md#what-lives-where).
The [release procedure](data-maintainers/dataset-operations.md#7-publish)
owns retention and rollback instructions.

The data cannot live on GitHub: the repo reached 2.3 GB with old tile
copies in its history, panel tiles pass GitHub's file-size limits, and
Pages caps a site at 1 GB.

Bucket egress is about NZ$0.20 per GB. A street view is about 1.3 MB, so
ten thousand views cost a few dollars.

## Deleting inputs

Inputs can be re-fetched from LINZ, and nationally they would be terabytes.
A region's inputs are deleted only once its outputs are safely in the
bucket:

1. Emit `data/out/<region>/`.
2. Upload it and the region's `selected_faces`.
3. List the upload back and compare sizes.
4. Write `data/regions/<region>/manifest.json` (what was built, from which
   survey, at which git commit), then delete matching regional rasters and
   the uploaded local pack. Keep outlines, layouts, tile lists and other
   regional files; the region directory is not deleted wholesale.
5. Delete point-cloud tiles not listed by another region on this disk
   without a publication manifest (tiles are shared at borders). Emitted
   outputs under `data/out/<region>/` stay for combine.

These are the default actions of [publish_region.py](../src/publish_region.py);
`--no-delete` keeps inputs. Regional upload is not production publication:
it does not run the live-comparison gate or switch the website release.

Peak disk per worker is then one region's working set (42 GB for the
largest Queenstown region) plus anything not yet uploaded.

### The region pack

Geometry keeps improving, so a rebuild should not mean a re-download.
Before inputs go, `src/pack_region.py` keeps what the build reads: all
points within 4 m of each building, ground returns within 23.5 m, imagery
within 13 m (lossless) and the DSM, per source tile. A rebuild from the
pack is byte-identical (`tests/synthetic` checks this). `build_region.sh`
restores the pack instead of fetching. The real pack ratio has not been
measured at scale yet.

## Rebuild boundaries

| layer | what | re-run | cost |
| --- | --- | --- | --- |
| inputs | LiDAR, imagery, DSM, outlines | once, then from the pack | the download |
| geometry | faces, obstructions, panels, shading | stale buildings only (build keys) | CPU |
| sun | kWh, calibration, losses | `run_district_build.sh --yield-only` | minutes, no LiDAR |
| economics | bills, savings, payback | nothing: in the browser | none |

Each building's detail record carries its model version (`mv`: geometry and
sun code hashes). The served names are fixed in `src/output_contract.py`.

## Many machines

### Why a bucket queue

Options for running 1,384 independent jobs on spot machines:

| option | needs from an administrator | gives |
| --- | --- | --- |
| Google Cloud Batch | enable the API, grant IAM | managed retries and spot handling |
| a queue in the bucket + plain VMs | bucket object access, VM permissions and quota | application-managed claims, heartbeats and retries |
| Pub/Sub or Cloud Tasks | enable two APIs, IAM | a queue, plus a subscriber to write |

The bucket queue avoids an additional queue-service API, but still needs
permissions to read/write/delete build objects and create/manage workers.
Authorized users can inspect `queue/`, `claims/`, `done/`, `failed/`;
the build bucket is not a public dashboard. Object counts are the progress
report. Queue behaviour is implemented in [gcs_queue.py](../src/gcs_queue.py).

### How the queue works

Cloud Storage create-if-absent is atomic (`if-generation-match: 0`), so two
workers claiming one region get one success and one failure, with no lock
server.

- `queue/<region>.json`: what to build (bbox, survey, building count).
  Written by `tools/enqueue_regions.py` from `data/national_regions.json`
  or `config.REGIONS`.
- `claims/<region>`: who is building it. Created to claim; rewritten every
  five minutes.
- `done/<region>.json`: the region's summary, on success. The claim is
  deleted.
- `failed/<region>.json`: log tail and attempt count. Three tries, then a
  person looks.

A spot VM can be taken at any time. The queue code attempts takeover of
claims whose heartbeat is older than 45 minutes, using generation-conditional
deletion and create-if-absent. Work can reuse `run_stage` markers if the
disk survives; otherwise the region needs its inputs restored or fetched.
This is implemented recovery logic, not evidence of a completed multi-worker
failure-recovery rehearsal.

### The fleet

`tools/fleet.sh up N` creates N spot VMs from an image of the build machine,
each running the worker loop; `down` deletes them. Workers delete themselves
when the queue is empty. Machine, zone, image and storage defaults are in
[fleet.sh](../tools/fleet.sh), with profile/environment overrides.
The script requests read-write storage and compute scopes; the effective
service-account IAM permissions and project quota must also permit upload
and self-deletion. Current quota, account grants and shared-VM scopes are
deployment facts to verify before a run, not guaranteed by this script.

Workers publish region artefacts to the build bucket. Fleet startup does
not assemble a combined map or publish a production release: collecting
regional outputs, combining, gating and releasing remain separate work.

`tools/status.py` prints one line, for example
`done 412 / claimed 6 / queued 966 / failed 0`, and what each worker is on.

## Status

Implemented: emit and combine, upload and input cleanup, region packs, the
yield-only layer, the output contract, queue, worker, fleet and status.
The archived [national rehearsal plan](national-rehearsal.md) records that
a multi-worker fleet had not been exercised at that point. Source inspection
does not establish a later successful rehearsal; validate throughput,
recovery and release assembly before treating national operation as proven.

## Considered and not done

- **PostGIS instead of files.** A server to run, back up and pay for, when
  the outputs are static tiles.
- **Keeping a merged GeoJSON as well.** Two ship paths once shipped stale
  geometry with fresh timestamps. One path.
- **Managed instance groups.** A restarted worker re-claims from the queue
  anyway, so the group adds a moving part without removing one.
