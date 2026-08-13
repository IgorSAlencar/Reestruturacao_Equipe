import json
from collections import defaultdict
from pathlib import Path

from shapely import make_valid, orient_polygons
from shapely.geometry import shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "geometria_brasil" / "Brasil_Municipios.json"
TARGET = ROOT / "public" / "brazil-mask.json"


def exterior_lines(geometry):
    polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    return {
        "type": "MultiLineString",
        "coordinates": [list(polygon.exterior.coords) for polygon in polygons if not polygon.is_empty],
    }


with SOURCE.open(encoding="utf-8") as source_file:
    municipalities = json.load(source_file)

shapes = []
by_uf = defaultdict(list)
for feature in municipalities["features"]:
    geom = make_valid(shape(feature["geometry"]))
    shapes.append(geom)
    uf = str(feature.get("properties", {}).get("id") or "")[:2]
    if uf:
        by_uf[uf].append(geom)

outline = orient_polygons(
    unary_union(shapes).simplify(0.025, preserve_topology=True),
    exterior_cw=False,
)
polygons = list(outline.geoms) if outline.geom_type == "MultiPolygon" else [outline]
world = [[-179.9, -85], [179.9, -85], [179.9, 85], [-179.9, 85], [-179.9, -85]]
holes = [list(reversed(list(polygon.exterior.coords))) for polygon in polygons]

state_lines = []
for uf, geoms in sorted(by_uf.items()):
    dissolved = orient_polygons(
        unary_union(geoms).simplify(0.025, preserve_topology=True),
        exterior_cw=False,
    )
    if dissolved.is_empty:
        continue
    polys = list(dissolved.geoms) if dissolved.geom_type == "MultiPolygon" else [dissolved]
    for polygon in polys:
        if polygon.is_empty:
            continue
        state_lines.append(list(polygon.exterior.coords))

payload = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"kind": "mask"},
            "geometry": {"type": "Polygon", "coordinates": [world, *holes]},
        },
        {
            "type": "Feature",
            "properties": {"kind": "outline"},
            "geometry": exterior_lines(outline),
        },
        {
            "type": "Feature",
            "properties": {"kind": "states"},
            "geometry": {"type": "MultiLineString", "coordinates": state_lines},
        },
    ],
}
TARGET.parent.mkdir(parents=True, exist_ok=True)
with TARGET.open("w", encoding="utf-8") as target_file:
    json.dump(payload, target_file, ensure_ascii=False, separators=(",", ":"))
