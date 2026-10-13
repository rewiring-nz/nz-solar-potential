# Handover: put Glenorchy and Kinloch on the live map

**Goal:** Glenorchy and Kinloch show up on the live solar map at qea.nz/every-roof,
with their roofs built.

**Repo:** `rewiring-nz/nz-solar-potential`
**Branch:** `claude/gifted-noether-hibops` (pushed, not merged to `main`)

## Already done (on the branch)

- `config.py`
  - Added two regions to `REGIONS`:
    - `glenorchy` `[168.3720, -44.8620, 168.4020, -44.8380]`
    - `kinloch` `[168.3380, -44.8480, 168.3620, -44.8300]`
  - Added a survey `otago-glenorchy-no-lidar` to `SURVEYS`:
    - bbox `[168.33, -44.87, 168.41, -44.82]`
    - `"elevation": "synthetic"`
    - `imagery_layer: 104460`
- `site-config.js`: both towns added to the map's `towns` search list.
- `tests/test_synthesize_dsm.py`: checks that every no-LiDAR region resolves to a
  synthetic survey. It passes.
- `BACKLOG.md` and `README.md` updated to match.

## Facts already checked

- **No LiDAR covers either town.** The westernmost Otago survey (Queenstown 2016
  and 2021) stops at 168.576 E, and the towns sit at 168.35–168.40 E. Checked
  against LINZ's public elevation catalogue (nz-elevation STAC). Both towns
  therefore use the synthetic-surface path that Cromwell uses
  (`src/synthesize_dsm.py`).
- **Only one photo set covers both towns:** LINZ layer 104460, "Otago 0.3m
  Rural Aerial Photos (2017-2019)". Its tile bboxes were checked at both towns,
  and its 2,328-tile count matches LINZ's description.
  - The 2019-2021 0.3 m set starts at 168.64 E.
  - No 0.1 m urban set and none of the 2023–2025 rural sets cover the towns.
- **The wide terrain model (DEM) grows automatically.** Its extent comes from
  `REGIONS`, so it now reaches 167.89 E. `ensure_dem_wide` re-downloads it on
  the next fetch.

## Not yet checked

- **How many buildings each box catches.** The boxes were drawn around each
  town's street grid with about 150 m of padding. The previous session couldn't
  reach LINZ, so no outlines were counted.

## Steps

1. **Fetch.** Needs `LINZ_API_KEY` in `.env`, and network access to
   `data.linz.govt.nz`.
   ```
   python src/fetch_regions.py glenorchy kinloch
   ```
   - Note the outline count reported for each region.
   - Confirm the log says `no LiDAR (survey otago-glenorchy-no-lidar) -- synthetic surface`.
   - Confirm the photos downloaded.
2. **Check the boxes.**
   - Query LINZ building outlines (layer 101290) over a wider area, about
     168.30–168.45 E and 44.80–44.90 S.
   - Look for clusters of outlines outside both boxes, such as Glenorchy's
     rural edges, Rees Valley Road and the Kinloch road.
   - Widen the boxes to cover any you find, keeping them inside the survey
     bbox. If you widen the survey bbox too, update the comments.
   - Use the house method: 2nd–98th percentile of outline centroids, plus 150 m.
   - Write the counts into the comments in `config.py`.
3. **Build** on the build VM (`claude-doing-things`, zone `australia-southeast1-b`):
   ```
   bash src/run_district_build.sh --regions "glenorchy kinloch"
   ```
   - Afterwards, regenerate the combined map data.
   - Spot-check a few roofs in the local preview:
     - houses should show hip roofs at 20°
     - roofs of 400 m² or more should be flat
     - each building should be flagged `pitch_guessed`
4. **Deploy** from the laptop:
   ```
   tools/deploy_from_vm.sh          # pull + gate
   tools/deploy_from_vm.sh --push   # publish v43 to the tiles bucket and switch site-config.js
   ```
5. **Merge** the branch into `main`, after the data is published. If you merge
   earlier, the new search entries lead to an empty map.
6. **Tidy up.**
   - Move the BACKLOG item from "configured, not yet built" to live.
   - Delete this file.

## Watch out for

- **A region outside its survey's bbox gets skipped with only a warning.** The
  new test catches this. Run `python tests/test_synthesize_dsm.py`.
- **The photos are old (2017–19) and coarse (0.3 m).** Obstructions picked up
  from the photos will be weak. Nothing else depends on them.
- **Never commit the API key.** It lives in `.env`, which is gitignored.
