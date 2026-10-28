# Building New Zealand one region at a time

How the build works region by region, deletes inputs as it goes, and
spreads work over many machines. Figures are measured on the Queenstown
build.

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

The site (code and page) is on GitHub Pages. The data is in the tiles
bucket, one folder per release: `gs://solar-potential-map-tiles/v<N>/`
(public, CORS for range requests, year-long cache).
`tools/publish_served.py` uploads a release and keeps the newest three;
`site-config.js` (`dataBase`, `dataVersion`) points the site at one.
Rolling back is pointing it at an older folder. A separate private data
bucket holds models and build inputs.

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
4. Delete `data/regions/<region>/` except a `manifest.json` (what was built,
   from which survey, at which git commit).
5. Delete point-cloud tiles no other region on this disk still lists
   (tiles are shared at borders).

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

## Three layers, three costs

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
| a queue in the bucket + plain VMs | nothing | the same, in ~200 readable lines |
| Pub/Sub or Cloud Tasks | enable two APIs, IAM | a queue, plus a subscriber to write |

The bucket queue needs no extra permissions, and its state is folders anyone
can open: `queue/`, `claims/`, `done/`, `failed/`. The object counts are
the progress report. Batch suits a project with a full-time operator; this
is not one.

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

A spot VM can be taken at any time. Its claim then goes stale; after 45
minutes another worker takes it over by deleting the claim with its
generation number, so only one taker wins. Work resumes from `run_stage`
markers if the disk survives, otherwise from the fetch. The worst case is
losing one region, about 2.5 hours for the largest.

### The fleet

`tools/fleet.sh up N` creates N spot VMs from an image of the build machine,
each running the worker loop; `down` deletes them. Workers delete themselves
when the queue is empty. Machines are C2D in zone `-a`, where the quota is.
Fleet VMs get read-write storage and compute scopes so they can publish and
retire themselves (the build VM's storage scope is read-only). Creating VMs
with write scopes has been tested and works; if it is ever refused, the fix
is one IAM grant (`compute.instanceAdmin` and `serviceAccountUser` on
`claude-batch`).

`tools/status.py` prints one line, for example
`done 412 / claimed 6 / queued 966 / failed 0`, and what each worker is on.

## Status

Built: emit and combine, upload and delete, region packs, the yield-only
layer, the output contract, the queue, worker, fleet and status. The fleet
has never run as a fleet. The first real run is the dress rehearsal on one
city ([national-rehearsal.md](national-rehearsal.md), archived plan).

## Considered and not done

- **PostGIS instead of files.** A server to run, back up and pay for, when
  the outputs are static tiles.
- **Keeping a merged GeoJSON as well.** Two ship paths once shipped stale
  geometry with fresh timestamps. One path.
- **Managed instance groups.** A restarted worker re-claims from the queue
  anyway, so the group adds a moving part without removing one.
