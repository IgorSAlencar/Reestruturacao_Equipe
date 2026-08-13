"""Gera GeoJSON web da malha distrital para municipios com >= 300 mil habitantes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
GPKG = ROOT / "geometria_brasil" / "distritos_brasil" / "saidas" / "distritos_brasil_pop2022.gpkg"
POP_CACHE = ROOT / ".territorios-data" / "population.json"
OUT = ROOT / "geometria_brasil" / "Brasil_Distritos_Metro.json"
THRESHOLD = 300_000
SIMPLIFY_M = 80


def municipality_codes_over_threshold() -> set[str]:
    payload = json.loads(POP_CACHE.read_text(encoding="utf-8"))
    values = payload.get("values") or {}
    cleaned: set[str] = set()
    for code, pop in values.items():
        digits = "".join(ch for ch in str(code) if ch.isdigit()).zfill(7)[-7:]
        if int(pop or 0) >= THRESHOLD and digits != "0000000":
            cleaned.add(digits)
    return cleaned


def main() -> int:
    if not GPKG.exists():
        print(f"Malha distrital não encontrada: {GPKG}", file=sys.stderr)
        return 1
    if not POP_CACHE.exists():
        print(f"Cache de população não encontrado: {POP_CACHE}", file=sys.stderr)
        return 1

    metro = municipality_codes_over_threshold()
    print(f"Municípios >= {THRESHOLD:,}: {len(metro)}".replace(",", "."))

    gdf = gpd.read_file(GPKG)
    gdf["CD_MUN"] = (
        gdf["CD_MUN"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(7).str[-7:]
    )
    gdf["CD_DIST"] = gdf["CD_DIST"].astype(str).str.replace(r"\D", "", regex=True)
    filtered = gdf[gdf["CD_MUN"].isin(metro) & gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    print(f"Distritos filtrados: {len(filtered):,}".replace(",", "."))

    metric = filtered.to_crs(epsg=5880)
    metric["geometry"] = metric.geometry.simplify(SIMPLIFY_M, preserve_topology=True)
    simplified = metric.to_crs(epsg=4326)
    simplified = simplified[simplified.geometry.notna() & ~simplified.geometry.is_empty]

    keep = ["CD_MUN", "CD_DIST", "NM_MUN", "NM_DIST", "POP_2022"]
    export = simplified[keep + ["geometry"]].copy()
    export["POP_2022"] = export["POP_2022"].fillna(0).astype(int)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    geojson = json.loads(export.to_json())
    # Remove bounding boxes / ids extras do GeoPandas
    for feature in geojson.get("features") or []:
        feature.pop("id", None)
        props = feature.get("properties") or {}
        feature["properties"] = {
            "CD_MUN": props.get("CD_MUN"),
            "CD_DIST": props.get("CD_DIST"),
            "NM_MUN": props.get("NM_MUN"),
            "NM_DIST": props.get("NM_DIST"),
            "POP_2022": props.get("POP_2022"),
        }

    OUT.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Salvo: {OUT} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
