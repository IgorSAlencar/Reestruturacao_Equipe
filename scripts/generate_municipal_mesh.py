"""Gera o GeoJSON web da malha municipal a partir do shapefile oficial do IBGE.

Uso:
    python scripts/generate_municipal_mesh.py
    python scripts/generate_municipal_mesh.py --shapefile geometria_brasil/BR_Municipios_2025/BR_Municipios_2025.shp
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHAPEFILE = (
    ROOT / "geometria_brasil" / "BR_Municipios_2025" / "BR_Municipios_2025.shp"
)
DEFAULT_OUTPUT = ROOT / "geometria_brasil" / "Brasil_Municipios.json"
REFERENCE_FILE = ROOT / "utils" / "municipios.json"
EXPECTED_MUNICIPALITIES = 5571
SIMPLIFY_M = 200
COORD_DECIMALS = 5
OPERATIONAL_CODES = {"4300001", "4300002"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Converte o shapefile IBGE da malha municipal em GeoJSON para o mapa."
    )
    parser.add_argument(
        "--shapefile",
        type=Path,
        default=DEFAULT_SHAPEFILE,
        help="Caminho do .shp (ou .gpkg/.geojson) da malha municipal.",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="GeoJSON de saída usado pelo mapa.",
    )
    parser.add_argument(
        "--simplificacao-m",
        type=float,
        default=SIMPLIFY_M,
        help="Tolerância de simplificação em metros (EPSG:5880). Use 0 para não simplificar.",
    )
    parser.add_argument(
        "--esperado",
        type=int,
        default=EXPECTED_MUNICIPALITIES,
        help="Quantidade oficial de municípios (padrão: 5571).",
    )
    return parser.parse_args()


def normalize_code(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(7)[-7:] if digits else ""


def is_operational_area(code: str, name: str) -> bool:
    cleaned = " ".join(str(name or "").casefold().split())
    return code in OPERATIONAL_CODES or "área operacional" in cleaned or "area operacional" in cleaned


def load_reference_codes() -> dict[str, str]:
    if not REFERENCE_FILE.exists():
        return {}
    payload = json.loads(REFERENCE_FILE.read_text(encoding="utf-8-sig"))
    return {
        normalize_code(item.get("codigo_ibge")): str(item.get("nome") or "")
        for item in payload
        if normalize_code(item.get("codigo_ibge"))
    }


def round_coords(value: object, ndigits: int = COORD_DECIMALS) -> object:
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(float(coord), ndigits) for coord in value]
        return [round_coords(item, ndigits) for item in value]
    return value


def compact_geojson(frame: gpd.GeoDataFrame) -> dict:
    payload = json.loads(frame.to_json())
    features = []
    for feature in payload.get("features") or []:
        feature.pop("id", None)
        props = feature.get("properties") or {}
        code = normalize_code(props.get("CD_MUN"))
        name = str(props.get("NM_MUN") or code)
        geometry = feature.get("geometry") or {}
        if geometry.get("coordinates") is not None:
            geometry["coordinates"] = round_coords(geometry["coordinates"])
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": code,
                    "name": name,
                    "description": name,
                    "CD_MUN": code,
                    "NM_MUN": name,
                    "SIGLA_UF": str(props.get("SIGLA_UF") or code[:2]),
                },
                "geometry": geometry,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    args = parse_args()
    shapefile = args.shapefile if args.shapefile.is_absolute() else ROOT / args.shapefile
    output = args.saida if args.saida.is_absolute() else ROOT / args.saida

    if not shapefile.exists():
        print(f"Shapefile não encontrado: {shapefile}", file=sys.stderr)
        return 1

    print(f"Lendo {shapefile}...")
    raw = gpd.read_file(shapefile)
    if "CD_MUN" not in raw.columns or "NM_MUN" not in raw.columns:
        print(
            "O shapefile precisa das colunas CD_MUN e NM_MUN (malha municipal IBGE).",
            file=sys.stderr,
        )
        return 1

    work = raw.copy()
    work["CD_MUN"] = work["CD_MUN"].map(normalize_code)
    work["NM_MUN"] = work["NM_MUN"].astype(str)
    if "SIGLA_UF" not in work.columns:
        work["SIGLA_UF"] = work["CD_MUN"].str[:2]

    operational = work[
        work.apply(lambda row: is_operational_area(row["CD_MUN"], row["NM_MUN"]), axis=1)
    ]
    work = work[
        ~work.apply(lambda row: is_operational_area(row["CD_MUN"], row["NM_MUN"]), axis=1)
    ]
    work = work[work["CD_MUN"].ne("0000000") & work.geometry.notna() & ~work.geometry.is_empty].copy()

    if operational.empty:
        print("Nenhuma área operacional removida.")
    else:
        for row in operational.itertuples(index=False):
            print(f"Removido (não é município): {row.CD_MUN} {row.NM_MUN}")

    if work["CD_MUN"].duplicated().any():
        work = work.dissolve(by="CD_MUN", as_index=False, aggfunc="first")

    codes = set(work["CD_MUN"])
    reference = load_reference_codes()
    if reference:
        missing = sorted(set(reference) - codes)
        extra = sorted(codes - set(reference))
        if missing or extra:
            print("A malha não fecha com utils/municipios.json:", file=sys.stderr)
            for code in missing:
                print(f"  falta {code} {reference.get(code)}", file=sys.stderr)
            for code in extra:
                print(f"  extra {code}", file=sys.stderr)
            return 1

    if len(codes) != args.esperado:
        print(
            f"Esperados {args.esperado} municípios únicos; obtidos {len(codes)}.",
            file=sys.stderr,
        )
        return 1

    print(f"Municípios válidos: {len(codes)}")
    if work.crs is None:
        work = work.set_crs(epsg=4674)
    metric = work.to_crs(epsg=5880)
    if args.simplificacao_m > 0:
        print(f"Simplificando em {args.simplificacao_m:g} m...")
        metric["geometry"] = metric.geometry.simplify(
            args.simplificacao_m, preserve_topology=True
        )
    simplified = metric.to_crs(epsg=4326)
    simplified = simplified[
        simplified.geometry.notna() & ~simplified.geometry.is_empty
    ].copy()
    if len(simplified) != args.esperado:
        print(
            f"Geometrias vazias após simplificar: {args.esperado - len(simplified)}.",
            file=sys.stderr,
        )
        return 1

    export = simplified[["CD_MUN", "NM_MUN", "SIGLA_UF", "geometry"]].copy()
    geojson = compact_geojson(export)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Salvo: {output} ({size_mb:.1f} MB, {len(geojson['features'])} municípios)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
