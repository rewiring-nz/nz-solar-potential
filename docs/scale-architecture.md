# Building New Zealand one region at a time

Josh, 22 September 2026: *"How can you make it not all one file? Maybe set
that up first, and solve [deleting inputs] and [coordinating machines]. Then
I'll decide what city to do. Make sure you think through in detail what the
best option is for scaling and coordinating the process cleanly."*

This is that thinking. Every number is measured on the Queenstown build or on
the VM as it stands today; every choice names the alternative it beat and why.

## The one idea

**The unit of work is a region, and the unit of output is a tile.**

Today the build fans in: 29 regions each write a GeoJSON, `merge_regions`
concatenates them into one `solar_potential.geojson` and one
`panel_layouts.geojson`, and everything the map serves is cut from those two
files. At Queenstown scale that intermediate is 400 MB. At New Zealand scale it
is 65 GB, and every stage after the merge -- deciles, terrain masks, tiles,
detail split, addresses -- reads the whole of it into memory.

The merge exists for one reason: the browser used to want one file. It no
longer does. Since 20 September the map reads **tiles** for buildings, panels
and the heat map, and tiles have the property the merge never had -- **every
one of them is built from a small area, and combining tilesets is a mechanical
join, not a re-read of the country.**

So each region finishes by emitting its own tiles, and a "combine" step joins
them. Nothing ever holds more than one region in memory. The combine handles
1,384 regions the same way it handles 29, because all it does is:

| output | per region emits | combine does | why it is associative |
| --- | --- | --- | --- |
| `panel_layouts.pmtiles` | its own pmtiles | `tile-join` over a list of files | regions do not overlap; a boundary tile just holds both regions' features |
| `buildings.pmtiles` | its own pmtiles | `tile-join` | same |
| `building_cells.pmtiles` | partial **sums** per grid cell, as JSON | add the partials, then tile once | a sum of sums is a sum |
| `building_detail/13/x/y.json` | its buildings, keyed by tile | merge the dicts per tile | keyed by building id; no collisions |
| `heatmap_tiles/z/x/y.png` | its tiles | copy; alpha-composite where two regions touch a tile | exactly what the old image-source version did implicitly |
| `addresses/` | its addresses | concatenate, then shard by prefix | search reads one shard |
| `seasonal_curves` | nothing | one file per **latitude band**, computed in combine | curve shape depends on latitude, not on the region |
| `summary.json` | its totals and provenance | add them | the deploy gate reads this instead of diffing a 26 MB file |

`tile-join` on the VM (tippecanoe 2.82) was tested reading pmtiles from a
list file and writing pmtiles: it works. Above a few hundred inputs the join
runs hierarchically -- batches of 200, then a join of the batches -- because
that is cheaper than one process holding 1,384 files open.

**What this replaces, precisely.** Four stages that ran on the merged file --
`bake_density_deciles`, `build_terrain_masks`, `shrink_panels_for_tiles`,
`split_building_detail` -- were per-building all along and read the merged
file only because it was there. They become region stages. `merge_regions`
leaves the ship path (it stays as a debugging tool). `build_seasonal_curves`
was the one thing that genuinely was not per-building: it is per
(slope, aspect) for one latitude, so it becomes per latitude band -- thirteen
files for the whole country, and Invercargill stops inheriting Queenstown's
sun.

**What it costs.** Nothing at Queenstown scale: the combine of 29 regions is
about a minute. A patched building means re-emitting its region and
recombining, which is minutes rather than the old chunked rewrite of a 400 MB
file.

## Where the tiles live

GitHub Pages caps a file at 100 MB. Queenstown's tiles are 60 MB; Auckland's
would be about 1.5 GB. So tiles are published to a bucket of
their own -- `gs://rewiring-solar-tiles/v<version>/data/`, created 22 Sep
with CORS for Range requests and still private until there is something to
serve (`tools/publish_served.py <version> --public` grants public read) --
and the site points at them through `site-config.js` (`dataBase`). The data
bucket, which holds models and inputs, stays private. The
pmtiles format is built for exactly this -- a browser fetches byte ranges of
one large file -- and it is the same code path the map uses today, with a
different base URL. Queenstown on Pages keeps working unchanged: `dataBase`
defaults to the site's own origin.

Egress from the bucket is about NZ$0.20 per GB. A street view is ~1.3 MB, so
ten thousand views is a few dollars. Cloudflare R2 has no egress fee at all,
but it would need an account Josh sets up; the bucket needs nothing from him
and can be swapped later by changing one URL.

## Deleting inputs (Josh's #1)

Inputs are 200 GB of the VM's 217 GB. Nationally they would be terabytes, and
they are all re-fetchable from LINZ. So a region's inputs are deleted **the
moment its outputs are safely in the bucket**, and not before:

1. emit `data/out/<region>/` (tiles, region GeoJSONs, summary)
2. upload it, and the region's face readings (`selected_faces`), to the bucket
3. verify the upload by listing it back and comparing sizes
4. delete `data/regions/<region>/` except a small `manifest.json` saying what
   was built, from which survey, at which git sha
5. delete point-cloud tiles that **no other region still on this disk**
   lists -- tiles are shared at region borders, and a worker holding two
   regions at once must not pull the ground out from under the second

Peak disk on a worker is then one region's working set -- 42 GB at the worst
Queenstown region -- plus what it has not yet uploaded. The 400 GB disk stops
being the thing that decides how big the country can be.

## Coordinating machines (Josh's #3)

### The choice

Three ways to run 1,384 independent jobs on N spot machines:

| option | what it needs from Josh | what it gives |
| --- | --- | --- |
| **Google Cloud Batch** | enable the Batch API, grant IAM to a service account | retries, spot handling and a task list, all managed |
| **a queue in the bucket + plain VMs** | nothing -- compute and storage already work | the same properties, in ~200 lines we can read |
| Pub/Sub or Cloud Tasks | enable two APIs, IAM | a queue, plus a subscriber to write anyway |

The service account this session runs as **cannot enable APIs or read IAM**
-- both were tried. So Batch means a console session from Josh before
anything moves, and every later change to permissions is his too. The bucket
queue needs nothing from him, and its state is a folder he can open in the
console: `queue/`, `claims/`, `done/`, `failed/`. Counting the objects in
each is the progress report. That legibility is worth more here than Batch's
polish, because the person deciding whether the run is healthy does not read
logs.

Batch is the right answer at a scale where someone is operating this full
time. That is not this project.

### How the queue works

Bucket objects are the queue, and Google Cloud Storage gives one primitive
that makes it correct: **create-if-absent is atomic** (`if-generation-match:
0`). Two workers that try to claim the same region at the same instant get one
success and one failure, guaranteed by the storage service, with no lock
server.

- `queue/<region>.json` -- what to build: bbox, survey, building count.
  Written once by `tools/enqueue_regions.py` from `data/national_regions.json`
  or `config.REGIONS`.
- `claims/<region>` -- who is building it and when they last said so. A
  worker claims by creating this object; it rewrites it every five minutes
  while working.
- `done/<region>.json` -- the region's summary. Written on success; the claim
  is deleted.
- `failed/<region>.json` -- the log tail and an attempt counter. A region is
  retried three times, then left for a person to look at.

**Preemption.** A spot VM can be taken away at any moment. Its claim then
stops being refreshed, and after 45 minutes another worker treats it as
abandoned and takes it over -- atomically, by deleting the stale claim with
its generation number, so only one taker can win. Work inside a region resumes
from `run_stage` markers if the disk survives (`STOP` on preemption, as the
current VM is configured) and restarts from the fetch if it does not. The
worst case is losing one region's work, about two and a half hours for the
largest.

### The fleet

`tools/fleet.sh up N` creates N spot VMs from a disk image of the current
build machine, each with a startup script that runs the worker loop; `down`
deletes them. Workers **delete themselves when the queue is empty**, so an
idle fleet costs nothing. Machine family is C2D in zone `-a`, because that is
where the quota is (100 cores free today, against 8 for T2D).

One thing the existing VM cannot do is upload: its service account carries
`storage read-only` scope, fixed at creation. Fleet VMs are created with
read-write storage and compute scopes so they can publish their region and
retire themselves. Creating VMs from this session's account has not been
tried yet; if it is refused, that is a single IAM grant from Josh
(`compute.instanceAdmin` and `serviceAccountUser` on `claude-batch`), and
it is the only thing on his side of the line.

### What "the run is healthy" looks like

`tools/status.py` prints one line: `done 412 / claimed 6 / queued 966 /
failed 0`, and per-worker, what it is on and for how long. The same numbers
are the object counts in the bucket folders. A failed region names its log.

## Status, 22 September

1 and 2 below are built and tested piecewise (BACKLOG.md has the evidence);
3 is built and its queue is tested on the real bucket; the fleet itself has
not yet been run as a fleet. Creating VMs with write scopes from this
session's account was tried and works, so nothing here needs Josh.

## Order of work

1. **Emit and combine** -- `src/emit_region.py`, `src/combine_regions.py`,
   latitude-band curves, address shards. Prove it on Queenstown: the combined
   output must match the live site building for building before anything
   else changes. This is the piece that makes everything after it possible.
2. **Upload and delete** -- `src/publish_region.py`, the manifest, the
   shared-tile rule for point clouds.
3. **Queue, worker, fleet, status.**
4. Josh picks a city.

Kingston and Wanaka, already running on the old path, land on the old path;
they are then re-emitted through the new one, which is minutes.

## What was considered and not done

- **One database (PostGIS) instead of files.** Correct in the abstract, and
  it would mean a server to run, back up and pay for, in a project whose
  outputs are static tiles. Tiles are the database.
- **Keeping the merged GeoJSON as well.** Two ship paths is how the 2 Sep
  deploy shipped stale geometry with fresh mtimes. One path.
- **Managed instance groups.** They restart preempted VMs for you, but a
  restarted worker re-claims from the queue anyway, so the group adds a
  moving part without removing one.
