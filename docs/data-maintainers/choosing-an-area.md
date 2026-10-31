# Choosing an area

A pipeline region is one LINZ suburb or locality, named by its place in the
LINZ hierarchy. Use this guide to find the area you want and the region id
to pass to [the quickstart](quickstart.md).

For the nationwide list, see [Areas](areas.md): a council-grouped snapshot
with every candidate's LINZ id and pipeline region id. It excludes lakes
and inland bays, as the area finder does by default. Inclusion does not
mean an area has buildings or a configured survey; check readiness below.

## Where the areas come from

Areas are the features of the LINZ
[NZ Suburbs and Localities](https://data.linz.govt.nz/layer/113764-nz-suburbs-and-localities/)
layer (`config.LINZ_SUBURBS_LOCALITIES_LAYER`, 113764). Each feature has:

| LINZ field | Meaning | Region id segment |
|---|---|---|
| `territorial_authority` | District or city council | 1st, e.g. `queenstown-lakes-district` |
| `major_name` | Town or city the locality belongs to | 2nd, e.g. `queenstown`; `no-town` when LINZ gives none |
| `name` | Suburb or locality | 3rd, e.g. `frankton` |

A segment is the LINZ ASCII name in lower case with spaces and punctuation
replaced by `-` (`Hāwea Flat` becomes `hawea-flat`). The region id is the
three segments joined by `/`, and is also the directory the build uses:
`data/regions/<council>/<town>/<suburb>/` and `data/out/<council>/<town>/<suburb>/`.

The buildings in a region are the LINZ
[building outlines](https://data.linz.govt.nz/layer/101290-nz-building-outlines/)
whose `suburb_locality`, `town_city` and `territorial_authority` match the
locality. LINZ assigns each building to exactly one locality, so two
locality regions never share a building. No building-count limit applies.

## Find an area

Search by name, alternative name or council. This needs a LINZ key
([Local setup](local-setup.md)) and writes nothing to disk:

```sh
.venv/bin/python tools/find_area.py arrowtown
.venv/bin/python tools/find_area.py --council "Queenstown-Lakes District"
.venv/bin/python tools/find_area.py frankton --council queenstown --details
```

Each match shows the LINZ id, type, town or city, council, population
estimate, alternative names and the region id, then the command to build the
first match. `--details` (at most 25 matches) also fetches the building
outlines and reports:

- the building count;
- the bounding box the build fetches: the buildings' extent plus 150 m, not
  the locality polygon, which in rural areas reaches far into empty land;
- its area and an imagery-download estimate (about 0.35 GB per km²);
- the LiDAR survey in `config.SURVEYS` that covers it, or that none does.

Lakes and inland bays are hidden unless you pass `--include-water`; they
rarely contain buildings.

To browse instead of search, open the layer on the LINZ Data Service, use
its map to click an area and read the three fields above, or filter the
attribute table by `territorial_authority`.

## Check the area can be built

- **Survey coverage.** The area must sit inside a survey in
  `config.SURVEYS`. `find_area.py --details` names the survey. A survey
  marked `"elevation": "synthetic"` builds on a modelled surface and every
  building carries `pitch_guessed`.
- **Size.** Build small areas locally; district builds belong on the build VM
  (see [Dataset operations](dataset-operations.md)). Rural localities with few
  buildings can still have a large bounding box and imagery download, so check
  `--details` first.

## Special cases

- **No town or city.** Bays, islands and some rural localities have no
  `major_name`; their middle segment is `no-town`. LINZ has two Halfway
  Bays in Queenstown-Lakes District: the inland bay (water, no buildings) is
  `queenstown-lakes-district/no-town/halfway-bay`, and the locality is
  `queenstown-lakes-district/kingston/halfway-bay`.
- **Several councils.** A locality LINZ lists under more than one council
  (`Queenstown-Lakes District, Southland District`) is filed under the first
  one listed, and its buildings in every listed council are included.
- **Duplicate names.** Where two localities in the same council and town
  share a name, the LINZ id is appended: `<council>/<town>/<suburb>-<linz-id>`.
  `find_area.py` prints that form; an id without it names both, and the
  pipeline stops at preflight and lists the alternatives.

## Build it

```sh
bash quickstart.sh queenstown-lakes-district/arrowtown/arrowtown
```

The first stage resolves the id against LINZ and writes
`data/regions/<id>/building_outlines.geojson` and `locality.json` (the LINZ
attributes, bounding box, imagery estimate and the outline source). The
outlines are selected from the council's LINZ building-outlines GeoPackage,
downloaded once per council to `data/sources/linz/layer-101290/<council>/`
and reused until LINZ publishes a new version
([Building outlines source](pipeline-reference.md#building-outlines-source)).
Later runs reuse them. To re-select after LINZ updates the outlines:

```sh
.venv/bin/python src/localities.py prepare <region-id> --refresh
```

## Older regions

Regions defined by a bounding box in `config.REGIONS`, such as `pilot` and
`town_gorge_north`, are deprecated for new work. They still build by name,
with a warning, so published districts need no rebuild. They are not LINZ
areas and are not being migrated: rebuilding published districts as
localities would need full rebuilds on the build VM. Use a locality id for
any new area. Where a locality overlaps an older region, the locality owns
the buildings LINZ assigns to it.
