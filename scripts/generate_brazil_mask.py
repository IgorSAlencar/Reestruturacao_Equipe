import json
from pathlib import Path

from shapely import make_valid, orient_polygons
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "geometria_brasil" / "Brasil_Municipios.json"
TARGET = ROOT / "public" / "brazil-mask.json"


with SOURCE.open(encoding="utf-8") as source_file:
    municipalities = json.load(source_file)

outline = orient_polygons(
    unary_union([make_valid(shape(feature["geometry"])) for feature in municipalities["features"]])
    .simplify(0.025, preserve_topology=True),
    exterior_cw=False,
)
polygons = list(outline.geoms) if outline.geom_type == "MultiPolygon" else [outline]
world = [[-179.9, -85], [179.9, -85], [179.9, 85], [-179.9, 85], [-179.9, -85]]
holes = [list(reversed(list(polygon.exterior.coords))) for polygon in polygons]
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
            "geometry": mapping(outline),
        },
    ],
}
TARGET.parent.mkdir(parents=True, exist_ok=True)
with TARGET.open("w", encoding="utf-8") as target_file:
    json.dump(payload, target_file, ensure_ascii=False, separators=(",", ":"))
