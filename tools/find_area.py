"""Find the LINZ suburb or locality to build, and the region id to pass the pipeline.

    python tools/find_area.py arrowtown
    python tools/find_area.py --council "Queenstown-Lakes District"
    python tools/find_area.py frankton --council queenstown --details

Searches LINZ NZ Suburbs and Localities (config.LINZ_SUBURBS_LOCALITIES_LAYER)
by name, alternative name or council and prints each match's LINZ id, type,
town or city, council, population estimate and region id
(<council>/<town-city>/<suburb-locality>). --details also asks LINZ for the
building outlines in each match and reports the building count, the bbox the
build would fetch, an imagery-download estimate and the configured LiDAR
survey covering it. Nothing is written to disk.

Then build one:  bash quickstart.sh <region-id>
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import localities as L  # noqa: E402

DETAILS_LIMIT = 25


def _survey(bbox, region):
    from src.surveys import survey_for
    try:
        sv = survey_for(bbox, region)
    except LookupError:
        return "none configured (see config.SURVEYS)"
    name = sv.get("name", "default")
    return f"{name} (no LiDAR: synthetic surface)" if sv.get("elevation") == "synthetic" else name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("name", nargs="?", help="part of a suburb or locality name (any case)")
    ap.add_argument("--council", help="part of a territorial authority name, e.g. 'Queenstown-Lakes'")
    ap.add_argument("--details", action="store_true",
                    help=f"count buildings and report bbox, imagery and LiDAR survey "
                         f"(at most {DETAILS_LIMIT} matches)")
    ap.add_argument("--include-water", action="store_true",
                    help="also list Lake and Inland Bay areas, which rarely hold buildings")
    a = ap.parse_args(argv)
    if not a.name and not a.council:
        ap.error("give a name, --council, or both")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.project_secrets import linz_api_key
    api_key = linz_api_key()
    if not api_key:
        raise SystemExit("LINZ_API_KEY not set: see docs/data-maintainers/local-setup.md")

    if a.name:
        found = L.search(api_key, a.name, a.council)
    else:
        found = L.in_territorial_authority(api_key, a.council)
    if not a.include_water:
        found = [p for p in found if p.get("type") not in ("Lake", "Inland Bay")]
    if not found:
        print("No matching LINZ suburbs or localities. Try a shorter name, or "
              "--include-water.")
        return 1

    # A search by name returns every locality with that name, and a council
    # listing returns the whole council, so homonyms that share a plain id
    # are always in `found` together and each gets its LINZ id suffix.
    ambiguous = L.ambiguous_ids(found)
    print(f"{len(found)} LINZ suburb/locality match(es) "
          f"(layer {L.config.LINZ_SUBURBS_LOCALITIES_LAYER}):\n")
    if a.details and len(found) > DETAILS_LIMIT:
        print(f"--details skipped: {len(found)} matches; narrow the search to "
              f"{DETAILS_LIMIT} or fewer.\n")
        a.details = False
    for p in found:
        rid = L.display_region_id(p, ambiguous)
        pop = p.get("population_estimate")
        print(f"  {p['name']}  [{p.get('type')}, LINZ id {p['id']}]")
        print(f"      town/city: {p.get('major_name') or '-'}   council: "
              f"{p.get('territorial_authority') or '-'}   population: "
              f"{pop if pop is not None else '-'}")
        if p.get("additional_name"):
            print(f"      also known as: {p['additional_name']}")
        print(f"      region id: {rid}")
        if a.details:
            try:
                outlines = L.fetch_outlines(api_key, p)
            except Exception as exc:
                print(f"      buildings: lookup failed ({type(exc).__name__})")
            else:
                n = len(outlines["features"])
                if n:
                    bbox = L.outlines_bbox_wgs84(outlines)
                    print(f"      buildings: {n:,}   bbox: {bbox}")
                    print(f"      bbox area: {L.bbox_km2(bbox):.1f} km2   imagery: "
                          f"~{L.imagery_gb_estimate(bbox):.0f} GB   survey: {_survey(bbox, rid)}")
                else:
                    print("      buildings: 0 (nothing to build)")
        print()
    first = next((p for p in found if p.get("type") not in ("Lake", "Inland Bay")), found[0])
    print(f"Build one with:  bash quickstart.sh {L.display_region_id(first, ambiguous)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
