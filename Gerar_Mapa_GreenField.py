"""
Gerador independente do mapa GreenField atual x proposto.

O programa NAO consulta banco de dados. Ele le os artefatos gravados por
Estudo_GreenField_V3_COMPLETO.py, incorpora os dados em um HTML Mapbox e,
opcionalmente, serve o arquivo apenas em 127.0.0.1.

Exemplo:
    python Gerar_Mapa_GreenField.py \
        --pasta-cenario saida_greenfield_v3/GREENFIELD_V3_135_XXXXXXXX \
        --servir

Dependencias:
    pip install pandas geopandas shapely openpyxl python-dotenv pyogrio
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import webbrowser
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any, Iterable
from urllib.parse import quote

try:
    import geopandas as gpd
    import pandas as pd
    from dotenv import load_dotenv
    from shapely.geometry import box, mapping
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover - mensagem operacional
    raise SystemExit(
        "Dependencia ausente. Instale: pandas geopandas shapely openpyxl "
        "python-dotenv pyogrio. Detalhe: " + str(exc)
    ) from exc


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_GEOMETRY = (
    BASE_DIR / "geometria_brasil" / "BR_Municipios_2025" / "BR_Municipios_2025.shp"
)
DEFAULT_HTML_NAME = "mapa_greenfield_atual_vs_proposto.html"
MAPBOX_GL_VERSION = "3.25.0"
BRAZIL_BOUNDS = [[-74.2, -34.1], [-34.4, 5.4]]

UF_POR_CODIGO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
    "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}

AREA_COLORS = {
    "NORDESTE 1": "#8b5cf6",
    "NORDESTE 2": "#ec4899",
    "SÃO PAULO": "#f97316",
    "CENTRO OESTE/NORTE": "#10b981",
    "SUL": "#3b82f6",
    "SUDESTE": "#eab308",
    "SEM_AREA": "#94a3b8",
}


@dataclass(frozen=True)
class InputFiles:
    workbook: Path
    portfolios: Path
    gr_points: Path | None
    gr_lines: Path | None


@dataclass(frozen=True)
class MapPayload:
    brazil: dict[str, Any]
    states: dict[str, Any]
    mask: dict[str, Any]
    municipalities: dict[str, Any]
    proposed_units: dict[str, Any]
    current_points: dict[str, Any]
    proposed_points: dict[str, Any]
    movements: dict[str, Any]
    gr_points: dict[str, Any]
    gr_lines: dict[str, Any]
    unattended: dict[str, Any]
    current_municipalities: dict[str, list[str]]
    warnings: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gera HTML Mapbox atual x proposto sem consultar o banco."
    )
    parser.add_argument("--pasta-cenario", type=Path, help="Pasta GREENFIELD_V3_135_*.")
    parser.add_argument("--saida", type=Path, help="Caminho do HTML de saida.")
    parser.add_argument(
        "--geometria-municipios", type=Path, default=DEFAULT_GEOMETRY,
        help="Shapefile/GPKG/GeoJSON da malha municipal.",
    )
    parser.add_argument("--simplificacao-m", type=float, default=750.0)
    parser.add_argument("--porta", type=int, default=8765)
    parser.add_argument("--servir", action="store_true")
    parser.add_argument(
        "--autoteste", action="store_true",
        help="Executa validacoes sinteticas locais, sem banco e sem Mapbox.",
    )
    return parser.parse_args(argv)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(column).strip().upper() for column in out.columns]
    return out


def digits_only(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    digits = re.sub(r"\D", "", str(value).strip())
    return digits or None


def normalize_identifier(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return text


def normalize_municipal_code(value: Any, valid_codes: set[str], six_to_seven: dict[str, str]) -> str | None:
    code = digits_only(value)
    if not code:
        return None
    if len(code) >= 7 and code[:7] in valid_codes:
        return code[:7]
    if len(code) == 6:
        return six_to_seven.get(code)
    padded = code.zfill(7)
    return padded if padded in valid_codes else None


def json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def clean_properties(properties: dict[str, Any]) -> dict[str, Any]:
    return {str(key): json_value(value) for key, value in properties.items()}


def empty_feature_collection() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": []}


def read_geojson(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return empty_feature_collection()
    with path.open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"GeoJSON invalido (esperado FeatureCollection): {path}")
    return data


def discover_inputs(folder: Path) -> InputFiles:
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Pasta do cenario nao encontrada: {folder}")
    workbooks = sorted(folder.glob("resultado_*.xlsx"))
    if not workbooks:
        raise FileNotFoundError(f"Nenhum resultado_*.xlsx encontrado em {folder}")
    if len(workbooks) > 1:
        raise RuntimeError(
            "Mais de um resultado_*.xlsx encontrado. Mantenha somente o workbook do cenario: "
            + ", ".join(path.name for path in workbooks)
        )
    portfolios = folder / "carteiras_unidades.geojson"
    if not portfolios.is_file():
        raise FileNotFoundError(f"GeoJSON obrigatorio nao encontrado: {portfolios}")
    return InputFiles(
        workbook=workbooks[0],
        portfolios=portfolios,
        gr_points=(folder / "gr_regionais.geojson") if (folder / "gr_regionais.geojson").is_file() else None,
        gr_lines=(folder / "linhas_gr_polo.geojson") if (folder / "linhas_gr_polo.geojson").is_file() else None,
    )


def load_workbook(path: Path) -> dict[str, pd.DataFrame]:
    required = {
        "gerencias_propostas",
        "gerencias_atuais",
        "transicao",
        "lojas_propostas",
    }
    book = pd.ExcelFile(path)
    missing = required - set(book.sheet_names)
    if missing:
        raise ValueError(
            f"Workbook {path.name} sem abas obrigatorias: {sorted(missing)}. "
            f"Abas existentes: {book.sheet_names}"
        )
    sheets = {name: normalize_columns(pd.read_excel(book, sheet_name=name)) for name in required}
    if "nao_atendidos" in book.sheet_names:
        sheets["nao_atendidos"] = normalize_columns(pd.read_excel(book, sheet_name="nao_atendidos"))
    else:
        sheets["nao_atendidos"] = pd.DataFrame()
    return sheets


def load_municipal_geometry(path: Path, simplification_m: float) -> gpd.GeoDataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Malha municipal nao encontrada: {path}")
    try:
        geo = gpd.read_file(path, engine="pyogrio")
    except (ImportError, ModuleNotFoundError, ValueError):
        geo = gpd.read_file(path)
    geometry_name = geo.geometry.name
    geo = geo.rename(
        columns={column: ("geometry" if column == geometry_name else str(column).strip().upper()) for column in geo.columns}
    ).set_geometry("geometry")
    if geo.crs is None:
        raise ValueError("Malha municipal sem CRS.")
    code_column = next(
        (column for column in ("CD_MUN", "CD_MUNIC", "COD_IBGE", "CODIGO_IBGE", "CD_GEOCMU", "CODMUN", "COD_MUN") if column in geo.columns),
        None,
    )
    if code_column is None:
        raise ValueError("Malha municipal sem codigo IBGE reconhecivel.")
    if code_column != "CD_MUN":
        geo = geo.rename(columns={code_column: "CD_MUN"})
    geo["CD_MUN"] = geo["CD_MUN"].map(lambda value: (digits_only(value) or "").zfill(7)[:7])
    geo = geo[
        geo["CD_MUN"].str.len().eq(7)
        & geo.geometry.notna()
        & ~geo.geometry.is_empty
    ].copy()
    if geo["CD_MUN"].duplicated().any():
        geo = geo[["CD_MUN", "geometry"]].dissolve(by="CD_MUN", as_index=False)
    if geo.crs.to_epsg() != 4326:
        geo = geo.to_crs(epsg=4326)
    metric = geo[["CD_MUN", "geometry"]].to_crs(epsg=5880)
    if simplification_m > 0:
        metric.geometry = metric.geometry.simplify(simplification_m, preserve_topology=True)
    metric = metric[metric.geometry.notna() & ~metric.geometry.is_empty].copy()
    result = metric.to_crs(epsg=4326)
    result["COD_UF"] = result["CD_MUN"].str[:2]
    result["UF"] = result["COD_UF"].map(UF_POR_CODIGO).fillna(result["COD_UF"])
    return result.reset_index(drop=True)


def simplify_geodataframe(geo: gpd.GeoDataFrame, simplification_m: float) -> gpd.GeoDataFrame:
    if geo.crs is None:
        raise ValueError("GeoJSON de carteiras sem CRS.")
    if geo.crs.to_epsg() != 4326:
        geo = geo.to_crs(epsg=4326)
    metric = geo.to_crs(epsg=5880)
    if simplification_m > 0:
        metric.geometry = metric.geometry.simplify(simplification_m, preserve_topology=True)
    metric = metric[metric.geometry.notna() & ~metric.geometry.is_empty].copy()
    return metric.to_crs(epsg=4326)


def feature_collection_from_gdf(geo: gpd.GeoDataFrame, properties: Iterable[str]) -> dict[str, Any]:
    columns = [column for column in properties if column in geo.columns]
    features: list[dict[str, Any]] = []
    for _, row in geo.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(row.geometry),
                "properties": clean_properties({column: row.get(column) for column in columns}),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def point_feature_collection(df: pd.DataFrame, latitude: str, longitude: str, properties: Iterable[str]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    columns = [column for column in properties if column in df.columns]
    for _, row in df.iterrows():
        lat = pd.to_numeric(pd.Series([row.get(latitude)]), errors="coerce").iloc[0]
        lon = pd.to_numeric(pd.Series([row.get(longitude)]), errors="coerce").iloc[0]
        if pd.isna(lat) or pd.isna(lon):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                "properties": clean_properties({column: row.get(column) for column in columns}),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def line_feature_collection(transition: pd.DataFrame) -> dict[str, Any]:
    required = {"LATITUDE_ATUAL", "LONGITUDE_ATUAL", "LATITUDE_PROPOSTA", "LONGITUDE_PROPOSTA"}
    if not required.issubset(transition.columns):
        raise ValueError(f"Aba transicao sem coordenadas obrigatorias: {sorted(required - set(transition.columns))}")
    props = [
        "CHAVE_SUPERVISAO_ATUAL", "GERENCIA_ID_PROPOSTA", "STATUS_TRANSICAO", "FAIXA_MOVIMENTO",
        "DISTANCIA_MOVIMENTO_KM", "DESC_GERENCIA_AREA_ATUAL", "DESC_GERENCIA_AREA_PROPOSTA",
        "COD_GER_REG", "GER_REGIONAL", "TIPO_VINCULO_GR", "NM_MUN_REFERENCIA_ATUAL", "NM_MUN_PROPOSTO",
    ]
    features: list[dict[str, Any]] = []
    for _, row in transition.iterrows():
        coordinate_columns = ["LATITUDE_ATUAL", "LONGITUDE_ATUAL", "LATITUDE_PROPOSTA", "LONGITUDE_PROPOSTA"]
        coordinates = [pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0] for column in coordinate_columns]
        lat_a, lon_a, lat_p, lon_p = coordinates
        if any(pd.isna(value) for value in coordinates):
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[float(lon_a), float(lat_a)], [float(lon_p), float(lat_p)]],
                },
                "properties": clean_properties({column: row.get(column) for column in props if column in transition.columns}),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def prepare_boundaries(municipal: gpd.GeoDataFrame) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    brazil_geometry = unary_union(municipal.geometry.tolist())
    brazil = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": mapping(brazil_geometry), "properties": {"PAIS": "BRASIL"}}],
    }
    states_gdf = municipal[["COD_UF", "UF", "geometry"]].dissolve(by=["COD_UF", "UF"], as_index=False)
    states = feature_collection_from_gdf(states_gdf, ["COD_UF", "UF"])
    world_mask = box(-179.99, -85.0, 179.99, 85.0).difference(brazil_geometry)
    mask = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": mapping(world_mask), "properties": {}}],
    }
    return brazil, states, mask


def attach_transition_data(
    current: pd.DataFrame, proposed: pd.DataFrame, transition: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    current = current.copy()
    proposed = proposed.copy()
    transition = transition.copy()
    for frame, column in (
        (current, "CHAVE_SUPERVISAO"),
        (proposed, "GERENCIA_ID"),
        (transition, "CHAVE_SUPERVISAO_ATUAL"),
        (transition, "GERENCIA_ID_PROPOSTA"),
    ):
        if column in frame.columns:
            frame[column] = frame[column].map(normalize_identifier)

    current_extra = [
        column for column in (
            "CHAVE_SUPERVISAO_ATUAL", "GERENCIA_ID_PROPOSTA", "STATUS_TRANSICAO", "FAIXA_MOVIMENTO",
            "DISTANCIA_MOVIMENTO_KM", "NM_MUN_PROPOSTO", "DESC_GERENCIA_AREA_PROPOSTA", "COD_GER_REG",
            "GER_REGIONAL", "TIPO_VINCULO_GR", "QTD_UNIDADES_PROPOSTA", "QTD_LOJAS_PROPOSTA",
            "CARGA_EQUIVALENTE_PROPOSTA",
        ) if column in transition.columns
    ]
    proposed_extra = [
        column for column in (
            "GERENCIA_ID_PROPOSTA", "CHAVE_SUPERVISAO_ATUAL", "STATUS_TRANSICAO", "FAIXA_MOVIMENTO",
            "DISTANCIA_MOVIMENTO_KM", "NM_MUN_REFERENCIA_ATUAL", "DESC_GERENCIA_AREA_ATUAL",
            "QTD_UNIDADES_ATUAL", "QTD_LOJAS_ATUAL", "CARGA_EQUIVALENTE_ATUAL_ESTIMADA",
        ) if column in transition.columns
    ]
    if "CHAVE_SUPERVISAO" in current.columns and "CHAVE_SUPERVISAO_ATUAL" in transition.columns:
        current = current.merge(
            transition[current_extra].drop_duplicates("CHAVE_SUPERVISAO_ATUAL"),
            left_on="CHAVE_SUPERVISAO", right_on="CHAVE_SUPERVISAO_ATUAL", how="left", suffixes=("", "_TRANSICAO"),
        )
    if "GERENCIA_ID" in proposed.columns and "GERENCIA_ID_PROPOSTA" in transition.columns:
        proposed = proposed.merge(
            transition[proposed_extra].drop_duplicates("GERENCIA_ID_PROPOSTA"),
            left_on="GERENCIA_ID", right_on="GERENCIA_ID_PROPOSTA", how="left", suffixes=("", "_TRANSICAO"),
        )
    return current, proposed


def current_portfolio_index(
    stores: pd.DataFrame, municipal: gpd.GeoDataFrame
) -> tuple[dict[str, list[str]], list[str]]:
    required = {"CHAVE_SUPERVISAO", "CD_MUNIC"}
    if not required.issubset(stores.columns):
        raise ValueError(f"Aba lojas_propostas sem colunas: {sorted(required - set(stores.columns))}")
    valid_codes = set(municipal["CD_MUN"].astype(str))
    six_to_seven = {code[:6]: code for code in valid_codes}
    work = stores[["CHAVE_SUPERVISAO", "CD_MUNIC"]].copy()
    work["CHAVE_SUPERVISAO"] = work["CHAVE_SUPERVISAO"].map(normalize_identifier)
    work["CD_MUN"] = work["CD_MUNIC"].map(
        lambda value: normalize_municipal_code(value, valid_codes, six_to_seven)
    )
    missing = sorted(
        {
            str(value)
            for value, normalized in zip(work["CD_MUNIC"], work["CD_MUN"])
            if not pd.isna(value) and normalized is None
        }
    )
    work = work.dropna(subset=["CHAVE_SUPERVISAO", "CD_MUN"]).drop_duplicates()
    grouped = {
        str(supervisor): sorted(group["CD_MUN"].astype(str).unique().tolist())
        for supervisor, group in work.groupby("CHAVE_SUPERVISAO", sort=True)
    }
    warnings: list[str] = []
    if missing:
        preview = ", ".join(missing[:20])
        suffix = " ..." if len(missing) > 20 else ""
        warnings.append(
            f"{len(missing)} codigo(s) CD_MUNIC das lojas nao foram encontrados na malha: {preview}{suffix}"
        )
    return grouped, warnings


def prepare_proposed_units(
    path: Path, proposed: pd.DataFrame, simplification_m: float
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    try:
        units = gpd.read_file(path, engine="pyogrio")
    except (ImportError, ModuleNotFoundError, ValueError):
        units = gpd.read_file(path)
    units.columns = [str(column).strip().upper() if column != units.geometry.name else column for column in units.columns]
    if units.geometry.name != "geometry":
        units = units.rename_geometry("geometry")
    if "GERENCIA_ID" not in units.columns:
        raise ValueError("carteiras_unidades.geojson sem GERENCIA_ID.")
    units["GERENCIA_ID"] = units["GERENCIA_ID"].map(normalize_identifier)
    managers = proposed.copy()
    managers["GERENCIA_ID"] = managers["GERENCIA_ID"].map(normalize_identifier)
    join_columns = [
        column for column in (
            "GERENCIA_ID", "DESC_GERENCIA_AREA_PROPOSTA", "COD_GER_REG", "GER_REGIONAL",
            "TIPO_VINCULO_GR", "EH_ANCORA_GR", "NM_MUN_POLO", "UF_POLO",
        ) if column in managers.columns
    ]
    units = units.merge(managers[join_columns].drop_duplicates("GERENCIA_ID"), on="GERENCIA_ID", how="left", suffixes=("", "_POLO"))
    units = simplify_geodataframe(units, simplification_m)
    properties = [
        "GERENCIA_ID", "DEMAND_ID", "TIPO_UNIDADE", "COD_IBGE", "CD_DIST", "NM_MUN", "NM_DIST", "UF",
        "POPULACAO_UNIDADE", "CARGA_EQUIVALENTE", "QTD_LOJAS", "DISTANCIA_KM", "METODO_ATRIBUICAO",
        "EH_CORREDOR_CONTIGUIDADE", "DESC_GERENCIA_AREA_PROPOSTA", "COD_GER_REG", "GER_REGIONAL",
        "TIPO_VINCULO_GR", "EH_ANCORA_GR", "NM_MUN_POLO", "UF_POLO",
    ]
    return units, feature_collection_from_gdf(units, properties)


def prepare_unattended(unattended: pd.DataFrame) -> dict[str, Any]:
    if unattended.empty:
        return empty_feature_collection()
    return point_feature_collection(
        unattended, "LATITUDE", "LONGITUDE",
        ["DEMAND_ID", "TIPO_UNIDADE", "COD_IBGE", "CD_DIST", "NM_MUN", "NM_DIST", "UF", "POPULACAO_UNIDADE", "MOTIVO_NAO_ATENDIMENTO"],
    )


def build_payload(
    files: InputFiles,
    geometry_path: Path,
    simplification_m: float,
) -> MapPayload:
    sheets = load_workbook(files.workbook)
    municipal = load_municipal_geometry(geometry_path, simplification_m)
    brazil, states, mask = prepare_boundaries(municipal)
    current, proposed = attach_transition_data(
        sheets["gerencias_atuais"], sheets["gerencias_propostas"], sheets["transicao"]
    )
    current_index, warnings = current_portfolio_index(sheets["lojas_propostas"], municipal)
    _, proposed_units = prepare_proposed_units(files.portfolios, proposed, simplification_m)

    current_properties = [
        "CHAVE_SUPERVISAO", "DESC_GERENCIA_AREA_ATUAL", "DESC_COORDENACAO", "DESC_SUPERVISAO",
        "COD_IBGE_REFERENCIA_ATUAL", "NM_MUN_REFERENCIA_ATUAL", "UF_REFERENCIA_ATUAL",
        "GERENCIA_ID_PROPOSTA", "NM_MUN_PROPOSTO", "DESC_GERENCIA_AREA_PROPOSTA", "COD_GER_REG",
        "GER_REGIONAL", "TIPO_VINCULO_GR", "STATUS_TRANSICAO", "FAIXA_MOVIMENTO",
        "DISTANCIA_MOVIMENTO_KM", "QTD_UNIDADES_ATUAL", "QTD_LOJAS_ATUAL",
        "POPULACAO_ATUAL_ESTIMADA", "CARGA_EQUIVALENTE_ATUAL_ESTIMADA",
        "QTD_UNIDADES_PROPOSTA", "QTD_LOJAS_PROPOSTA", "CARGA_EQUIVALENTE_PROPOSTA",
    ]
    proposed_properties = [
        "GERENCIA_ID", "CANDIDATE_ID", "COD_IBGE_POLO", "NM_MUN_POLO", "UF_POLO",
        "DESC_GERENCIA_AREA_PROPOSTA", "COD_GER_REG", "GER_REGIONAL", "TIPO_VINCULO_GR",
        "EH_ANCORA_GR", "CHAVE_SUPERVISAO_ATUAL", "NM_MUN_REFERENCIA_ATUAL",
        "DESC_GERENCIA_AREA_ATUAL", "STATUS_TRANSICAO", "FAIXA_MOVIMENTO", "DISTANCIA_MOVIMENTO_KM",
        "QTD_UNIDADES", "QTD_MUNICIPIOS", "QTD_DISTRITOS", "QTD_LOJAS", "POPULACAO_ATENDIDA",
        "CARGA_EQUIVALENTE_TOTAL", "DISTANCIA_MEDIA_KM", "DISTANCIA_P90_KM", "DISTANCIA_MAXIMA_KM",
        "QTD_UNIDADES_ATUAL", "QTD_LOJAS_ATUAL", "CARGA_EQUIVALENTE_ATUAL_ESTIMADA",
    ]
    municipalities = feature_collection_from_gdf(municipal, ["CD_MUN", "COD_UF", "UF"])
    return MapPayload(
        brazil=brazil,
        states=states,
        mask=mask,
        municipalities=municipalities,
        proposed_units=proposed_units,
        current_points=point_feature_collection(current, "LATITUDE_ATUAL", "LONGITUDE_ATUAL", current_properties),
        proposed_points=point_feature_collection(proposed, "LATITUDE", "LONGITUDE", proposed_properties),
        movements=line_feature_collection(sheets["transicao"]),
        gr_points=read_geojson(files.gr_points),
        gr_lines=read_geojson(files.gr_lines),
        unattended=prepare_unattended(sheets["nao_atendidos"]),
        current_municipalities=current_index,
        warnings=warnings,
    )


def json_for_html(value: Any) -> str:
    # Evita encerrar o bloco <script> caso algum texto de origem contenha </script>.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


class HtmlTemplate(Template):
    delimiter = "§"


HTML_TEMPLATE = HtmlTemplate(r'''<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <title>GreenField — atual x proposto</title>
  <link href="https://api.mapbox.com/mapbox-gl-js/v§mapbox_version/mapbox-gl.css" rel="stylesheet">
  <script src="https://api.mapbox.com/mapbox-gl-js/v§mapbox_version/mapbox-gl.js"></script>
  <style>
    :root { --ink:#10233f; --muted:#64748b; --panel:rgba(255,255,255,.96); --blue:#1473e6; --orange:#ff7a18; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,Segoe UI,Arial,sans-serif; color:var(--ink); overflow:hidden; }
    #map { position:absolute; inset:0; background:#dce5ee; }
    .topbar { position:absolute; top:14px; left:14px; right:64px; z-index:3; display:flex; gap:10px; align-items:center; pointer-events:none; }
    .brand,.filters,.metrics,.panel,.legend,.layers { background:var(--panel); border:1px solid rgba(15,35,63,.12); box-shadow:0 8px 25px rgba(15,35,63,.15); backdrop-filter:blur(8px); }
    .brand { border-radius:12px; padding:10px 14px; min-width:225px; pointer-events:auto; }
    .brand strong { display:block; font-size:15px; letter-spacing:.01em; }
    .brand small { color:var(--muted); }
    .filters { border-radius:12px; padding:8px; display:flex; gap:7px; flex:1; pointer-events:auto; }
    input,select,button { font:inherit; border:1px solid #d7e0e9; background:#fff; color:var(--ink); border-radius:8px; padding:8px 9px; min-width:0; }
    input { flex:1; }
    select { max-width:190px; }
    button { cursor:pointer; font-weight:600; }
    button:hover { border-color:#8aa2ba; background:#f6f9fc; }
    .metrics { position:absolute; top:82px; left:14px; z-index:3; border-radius:12px; display:flex; overflow:hidden; }
    .metric { padding:9px 13px; border-right:1px solid #e5ebf1; min-width:92px; }
    .metric:last-child { border-right:0; }
    .metric b { display:block; font-size:17px; }
    .metric span { color:var(--muted); font-size:11px; }
    .panel { position:absolute; z-index:3; top:132px; right:14px; width:330px; max-height:calc(100vh - 150px); overflow:auto; border-radius:14px; padding:16px; }
    .panel h2 { font-size:17px; margin:0 0 5px; }
    .panel .hint { color:var(--muted); font-size:13px; line-height:1.45; }
    .panel-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:12px; }
    .panel-grid div { background:#f5f8fb; border-radius:8px; padding:8px; min-height:54px; }
    .panel-grid span { display:block; color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.04em; }
    .panel-grid b { display:block; font-size:13px; margin-top:3px; overflow-wrap:anywhere; }
    .layers { position:absolute; left:14px; bottom:44px; z-index:3; width:220px; border-radius:12px; padding:10px 12px; }
    .layers strong { display:block; margin-bottom:6px; font-size:12px; text-transform:uppercase; color:var(--muted); }
    .layers label { display:flex; align-items:center; gap:7px; font-size:12px; margin:5px 0; }
    .layers input { flex:0; }
    .legend { position:absolute; left:246px; bottom:44px; z-index:3; border-radius:12px; padding:9px 12px; display:flex; gap:13px; font-size:12px; }
    .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; }
    .line { display:inline-block; width:18px; border-top:3px solid; margin-right:5px; transform:translateY(-2px); }
    #warning { display:none; margin-top:12px; padding:8px; border-radius:8px; background:#fff4d8; color:#754c00; font-size:11px; }
    .mapboxgl-popup-content { border-radius:10px; padding:12px; color:var(--ink); min-width:210px; }
    @media(max-width:900px) { .topbar{right:14px;flex-wrap:wrap}.filters{order:2;flex-basis:100%;overflow:auto}.metrics{display:none}.panel{top:auto;bottom:10px;right:10px;left:10px;width:auto;max-height:38vh}.layers,.legend{display:none}.brand{min-width:auto} }
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="topbar">
    <div class="brand"><strong>GreenField · Brasil</strong><small>Posição atual × posição proposta</small></div>
    <div class="filters">
      <input id="search" placeholder="Gerente, supervisor ou município">
      <select id="area"><option value="">Todas as áreas</option></select>
      <select id="regional"><option value="">Todas as GRs</option></select>
      <select id="movement"><option value="">Todos os movimentos</option></select>
      <select id="kind"><option value="">Todos os polos</option><option value="ANCORA">Âncoras GR</option><option value="REFORCO">Reforços</option></select>
      <button id="reset">Brasil</button>
    </div>
  </div>
  <div class="metrics">
    <div class="metric"><b id="m-current">0</b><span>polos atuais</span></div>
    <div class="metric"><b id="m-proposed">0</b><span>propostos</span></div>
    <div class="metric"><b id="m-moved">0</b><span>movimentados</span></div>
    <div class="metric"><b id="m-mean">—</b><span>média km</span></div>
    <div class="metric"><b id="m-p90">—</b><span>P90 km</span></div>
    <div class="metric"><b id="m-max">—</b><span>máxima km</span></div>
    <div class="metric"><b id="m-anchor">0</b><span>âncoras</span></div>
    <div class="metric"><b id="m-extra">0</b><span>reforços</span></div>
  </div>
  <aside class="panel" id="panel">
    <h2>Selecione um polo</h2>
    <div class="hint">Clique em um polo atual para ver os municípios com lojas vinculadas ao supervisor. Clique em um polo proposto para ver sua carteira territorial.</div>
    <div id="warning"></div>
  </aside>
  <div class="layers"><strong>Camadas</strong>
    <label><input type="checkbox" data-layer="current-points" checked> Polos atuais</label>
    <label><input type="checkbox" data-layer="proposed-points" checked> Polos propostos</label>
    <label><input type="checkbox" data-layer="movement-lines" checked> Movimentos</label>
    <label><input type="checkbox" data-layer="portfolio-fill" checked> Carteiras propostas</label>
    <label><input type="checkbox" data-layer="gr-points" checked> GRs e vínculos</label>
    <label><input type="checkbox" data-layer="state-lines" checked> Estados</label>
    <label><input type="checkbox" data-layer="unattended-points" checked> Não atendidos</label>
  </div>
  <div class="legend">
    <span><i class="dot" style="background:#1473e6"></i>Atual</span>
    <span><i class="dot" style="background:#ff7a18"></i>Proposto</span>
    <span><i class="dot" style="background:#7c3aed"></i>GR</span>
    <span><i class="line" style="border-color:#ef4444"></i>Realocação</span>
  </div>
<script>
const DATA = §payload;
mapboxgl.accessToken = §token;
const boundsBrazil = §bounds;
const map = new mapboxgl.Map({
  container:'map', style:§style, bounds:boundsBrazil, fitBoundsOptions:{padding:35},
  renderWorldCopies:false, minZoom:1.7, attributionControl:true
});
map.addControl(new mapboxgl.NavigationControl({visualizePitch:true}),'top-right');
map.addControl(new mapboxgl.FullscreenControl(),'top-right');

const esc = value => String(value ?? '—').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const fmt = value => { const n=Number(value); return Number.isFinite(n) ? n.toLocaleString('pt-BR',{maximumFractionDigits:1}) : '—'; };
const truthy = value => value === true || value === 1 || String(value).toLowerCase() === 'true';
const norm = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toUpperCase();
const values = (fc, key) => [...new Set(fc.features.map(f=>f.properties?.[key]).filter(v=>v!==null&&v!==undefined&&v!==''))].sort((a,b)=>String(a).localeCompare(String(b),'pt-BR'));
const proposedById = new Map(DATA.proposedPoints.features.map(f=>[String(f.properties.GERENCIA_ID),f]));
const currentById = new Map(DATA.currentPoints.features.map(f=>[String(f.properties.CHAVE_SUPERVISAO),f]));
let activePopup = null;

function fillSelect(id, items) { const el=document.getElementById(id); items.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o);}); }
fillSelect('area',values(DATA.proposedPoints,'DESC_GERENCIA_AREA_PROPOSTA'));
fillSelect('regional',values(DATA.proposedPoints,'GER_REGIONAL'));
fillSelect('movement',values(DATA.movements,'FAIXA_MOVIMENTO'));

function addSource(id,data){ map.addSource(id,{type:'geojson',data}); }
function areaColor(){ return ['match',['coalesce',['get','DESC_GERENCIA_AREA_PROPOSTA'],'SEM_AREA'],§area_expression,'#94a3b8']; }
function layerVisible(id,visible){ if(map.getLayer(id)) map.setLayoutProperty(id,'visibility',visible?'visible':'none'); }

map.on('load',()=>{
  addSource('mask',DATA.mask); addSource('brazil',DATA.brazil); addSource('states',DATA.states);
  addSource('municipalities',DATA.municipalities); addSource('proposed-units',DATA.proposedUnits);
  addSource('current-poles',DATA.currentPoints); addSource('proposed-poles',DATA.proposedPoints);
  addSource('movements',DATA.movements); addSource('gr-points-source',DATA.grPoints); addSource('gr-lines-source',DATA.grLines);
  addSource('unattended',DATA.unattended);

  map.addLayer({id:'world-mask',type:'fill',source:'mask',paint:{'fill-color':'#0b1728','fill-opacity':.68}});
  map.addLayer({id:'portfolio-fill',type:'fill',source:'proposed-units',paint:{'fill-color':areaColor(),'fill-opacity':.13}});
  map.addLayer({id:'portfolio-lines',type:'line',source:'proposed-units',paint:{'line-color':areaColor(),'line-opacity':.25,'line-width':.7}});
  map.addLayer({id:'state-lines',type:'line',source:'states',paint:{'line-color':'#334155','line-width':['interpolate',['linear'],['zoom'],3,.8,7,1.6],'line-opacity':.8}});
  map.addLayer({id:'brazil-line',type:'line',source:'brazil',paint:{'line-color':'#071427','line-width':['interpolate',['linear'],['zoom'],3,2.4,7,4.2],'line-opacity':1}});
  map.addLayer({id:'movement-lines',type:'line',source:'movements',paint:{'line-color':['match',['get','FAIXA_MOVIMENTO'],'MESMO_PONTO_APROXIMADO','#22c55e','AJUSTE_LOCAL_ATE_50_KM','#84cc16','MOVIMENTO_REGIONAL_50_A_150_KM','#eab308','MOVIMENTO_RELEVANTE_150_A_300_KM','#f97316','#ef4444'],'line-width':1.4,'line-opacity':.54}});
  map.addLayer({id:'gr-lines',type:'line',source:'gr-lines-source',paint:{'line-color':'#7c3aed','line-width':1.2,'line-dasharray':[2,2],'line-opacity':.65}});
  map.addLayer({id:'current-selection-fill',type:'fill',source:'municipalities',filter:['in',['get','CD_MUN'],['literal',[]]],paint:{'fill-color':'#1473e6','fill-opacity':.38}});
  map.addLayer({id:'current-selection-line',type:'line',source:'municipalities',filter:['in',['get','CD_MUN'],['literal',[]]],paint:{'line-color':'#0754a8','line-width':2.8,'line-opacity':1}});
  map.addLayer({id:'proposed-selection-fill',type:'fill',source:'proposed-units',filter:['==',['get','GERENCIA_ID'],'__NONE__'],paint:{'fill-color':'#ff7a18','fill-opacity':.43}});
  map.addLayer({id:'proposed-selection-line',type:'line',source:'proposed-units',filter:['==',['get','GERENCIA_ID'],'__NONE__'],paint:{'line-color':'#b94700','line-width':3,'line-opacity':1}});
  map.addLayer({id:'selected-movement',type:'line',source:'movements',filter:['==',['get','GERENCIA_ID_PROPOSTA'],'__NONE__'],paint:{'line-color':'#ef233c','line-width':4,'line-opacity':1}});
  map.addLayer({id:'unattended-points',type:'circle',source:'unattended',paint:{'circle-radius':5,'circle-color':'#dc2626','circle-stroke-color':'#fff','circle-stroke-width':1.2}});
  map.addLayer({id:'gr-points',type:'circle',source:'gr-points-source',paint:{'circle-radius':5,'circle-color':'#7c3aed','circle-stroke-color':'#fff','circle-stroke-width':1.4}});
  map.addLayer({id:'current-points',type:'circle',source:'current-poles',paint:{'circle-radius':['interpolate',['linear'],['zoom'],3,5,8,9],'circle-color':'#1473e6','circle-stroke-color':'#fff','circle-stroke-width':1.8}});
  map.addLayer({id:'proposed-points',type:'circle',source:'proposed-poles',paint:{'circle-radius':['interpolate',['linear'],['zoom'],3,5.5,8,9.5],'circle-color':'#ff7a18','circle-stroke-color':['case',['boolean',['get','EH_ANCORA_GR'],false],'#6d28d9','#fff'],'circle-stroke-width':['case',['boolean',['get','EH_ANCORA_GR'],false],2.8,1.8]}});

  ['current-points','proposed-points','movement-lines','gr-points','unattended-points'].forEach(id=>{
    map.on('mouseenter',id,()=>map.getCanvas().style.cursor='pointer'); map.on('mouseleave',id,()=>map.getCanvas().style.cursor='');
  });
  map.on('click','current-points',e=>{ e.originalEvent.__greenfield=true; selectCurrent(e.features[0]); });
  map.on('click','proposed-points',e=>{ e.originalEvent.__greenfield=true; selectProposed(e.features[0]); });
  map.on('click','movement-lines',e=>{ e.originalEvent.__greenfield=true; const id=String(e.features[0].properties.GERENCIA_ID_PROPOSTA); if(proposedById.has(id)) selectProposed(proposedById.get(id)); });
  map.on('click',e=>{ if(!e.originalEvent.__greenfield) clearSelection(); });
  applyFilters(); updateMetrics(); showWarnings();
});

function currentPanel(p,codes){
  const items=[['Supervisor',p.CHAVE_SUPERVISAO],['Gerência atual',p.DESC_SUPERVISAO],['Município atual',p.NM_MUN_REFERENCIA_ATUAL],['Área atual',p.DESC_GERENCIA_AREA_ATUAL],['Polo proposto',p.NM_MUN_PROPOSTO],['Área proposta',p.DESC_GERENCIA_AREA_PROPOSTA],['GR',p.GER_REGIONAL],['Distância',fmt(p.DISTANCIA_MOVIMENTO_KM)+' km'],['Faixa',p.FAIXA_MOVIMENTO],['Municípios com loja',codes.length],['Lojas atuais',fmt(p.QTD_LOJAS_ATUAL)],['Carga atual',fmt(p.CARGA_EQUIVALENTE_ATUAL_ESTIMADA)]];
  renderPanel('Polo atual',`Território observado pelas lojas da supervisão`,items);
}
function proposedPanel(p){
  const units=DATA.proposedUnits.features.filter(f=>String(f.properties.GERENCIA_ID)===String(p.GERENCIA_ID));
  const municipalities=new Set(units.map(f=>f.properties.COD_IBGE).filter(Boolean));
  const items=[['Gerência proposta',p.GERENCIA_ID],['Município polo',p.NM_MUN_POLO],['UF',p.UF_POLO],['Área proposta',p.DESC_GERENCIA_AREA_PROPOSTA],['GR',p.GER_REGIONAL],['Vínculo GR',p.TIPO_VINCULO_GR],['Supervisor pareado',p.CHAVE_SUPERVISAO_ATUAL],['Origem atual',p.NM_MUN_REFERENCIA_ATUAL],['Distância',fmt(p.DISTANCIA_MOVIMENTO_KM)+' km'],['Municípios',municipalities.size],['Unidades',fmt(p.QTD_UNIDADES)],['Lojas',fmt(p.QTD_LOJAS)],['População',fmt(p.POPULACAO_ATENDIDA)],['Carga',fmt(p.CARGA_EQUIVALENTE_TOTAL)]];
  renderPanel('Polo proposto',truthy(p.EH_ANCORA_GR)?'Âncora obrigatória de GR':'Polo adicional de reforço',items);
}
function renderPanel(title,subtitle,items){
  document.getElementById('panel').innerHTML=`<h2>${esc(title)}</h2><div class="hint">${esc(subtitle)}</div><div class="panel-grid">${items.map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}</div><div id="warning"></div>`;
  showWarnings();
}
function showWarnings(){ const w=document.getElementById('warning'); if(!w)return; if(DATA.warnings.length){w.style.display='block';w.textContent=DATA.warnings.join(' | ');} }
function selectCurrent(feature){
  const p=feature.properties; const id=String(p.CHAVE_SUPERVISAO); const codes=DATA.currentMunicipalities[id]||[];
  map.setFilter('current-selection-fill',['in',['get','CD_MUN'],['literal',codes]]); map.setFilter('current-selection-line',['in',['get','CD_MUN'],['literal',codes]]);
  map.setFilter('proposed-selection-fill',['==',['get','GERENCIA_ID'],'__NONE__']); map.setFilter('proposed-selection-line',['==',['get','GERENCIA_ID'],'__NONE__']);
  map.setFilter('selected-movement',['==',['to-string',['get','CHAVE_SUPERVISAO_ATUAL']],id]); currentPanel(p,codes); showPopup(feature,`<b>Polo atual</b><br>${esc(p.DESC_SUPERVISAO||p.CHAVE_SUPERVISAO)}<br>${esc(p.NM_MUN_REFERENCIA_ATUAL)}<br>${codes.length} município(s) com loja`); zoomToFeatures(codes.map(code=>DATA.municipalities.features.find(f=>f.properties.CD_MUN===code)).filter(Boolean),feature);
}
function selectProposed(feature){
  const p=feature.properties; const id=String(p.GERENCIA_ID);
  map.setFilter('current-selection-fill',['in',['get','CD_MUN'],['literal',[]]]); map.setFilter('current-selection-line',['in',['get','CD_MUN'],['literal',[]]]);
  map.setFilter('proposed-selection-fill',['==',['to-string',['get','GERENCIA_ID']],id]); map.setFilter('proposed-selection-line',['==',['to-string',['get','GERENCIA_ID']],id]);
  map.setFilter('selected-movement',['==',['to-string',['get','GERENCIA_ID_PROPOSTA']],id]); proposedPanel(p); showPopup(feature,`<b>Polo proposto</b><br>${esc(p.NM_MUN_POLO)} · ${esc(p.UF_POLO)}<br>${esc(p.GER_REGIONAL)}<br>${fmt(p.DISTANCIA_MOVIMENTO_KM)} km de realocação`); zoomToFeatures(DATA.proposedUnits.features.filter(f=>String(f.properties.GERENCIA_ID)===id),feature);
}
function showPopup(feature,content){ if(activePopup)activePopup.remove(); activePopup=new mapboxgl.Popup({closeButton:true,closeOnClick:false,offset:10}).setLngLat(feature.geometry.coordinates).setHTML(content).addTo(map); }
function clearSelection(){
  if(!map.loaded())return; map.setFilter('current-selection-fill',['in',['get','CD_MUN'],['literal',[]]]); map.setFilter('current-selection-line',['in',['get','CD_MUN'],['literal',[]]]);
  map.setFilter('proposed-selection-fill',['==',['get','GERENCIA_ID'],'__NONE__']); map.setFilter('proposed-selection-line',['==',['get','GERENCIA_ID'],'__NONE__']); map.setFilter('selected-movement',['==',['get','GERENCIA_ID_PROPOSTA'],'__NONE__']);
  if(activePopup){activePopup.remove();activePopup=null;}
  document.getElementById('panel').innerHTML='<h2>Selecione um polo</h2><div class="hint">Clique em um polo atual para ver os municípios com lojas vinculadas ao supervisor. Clique em um polo proposto para ver sua carteira territorial.</div><div id="warning"></div>'; showWarnings();
}
function eachCoordinate(geometry,fn){ if(!geometry)return; const walk=value=>{ if(typeof value[0]==='number')fn(value); else value.forEach(walk); }; walk(geometry.coordinates); }
function zoomToFeatures(features,fallback){ const b=new mapboxgl.LngLatBounds(); features.forEach(f=>eachCoordinate(f.geometry,c=>b.extend(c))); if(b.isEmpty()&&fallback)eachCoordinate(fallback.geometry,c=>b.extend(c)); if(!b.isEmpty())map.fitBounds(b,{padding:{top:130,bottom:70,left:250,right:365},maxZoom:8,duration:700}); }

function featureMatches(f,type){
  const p=f.properties||{}, q=norm(document.getElementById('search').value), area=document.getElementById('area').value, regional=document.getElementById('regional').value, movement=document.getElementById('movement').value, kind=document.getElementById('kind').value;
  const hay=norm(Object.values(p).join(' ')); if(q&&!hay.includes(q))return false;
  if(area&&p.DESC_GERENCIA_AREA_PROPOSTA!==area)return false; if(regional&&p.GER_REGIONAL!==regional)return false; if(movement&&p.FAIXA_MOVIMENTO!==movement)return false;
  if(kind&&type==='proposed'){const actual=truthy(p.EH_ANCORA_GR)?'ANCORA':'REFORCO';if(actual!==kind)return false;} return true;
}
function expressionFor(type){ const fc=type==='current'?DATA.currentPoints:DATA.proposedPoints; const key=type==='current'?'CHAVE_SUPERVISAO':'GERENCIA_ID'; const ids=fc.features.filter(f=>featureMatches(f,type)).map(f=>String(f.properties[key])); return ['in',['to-string',['get',key]],['literal',ids]]; }
function applyFilters(){ if(!map.loaded())return; map.setFilter('current-points',expressionFor('current')); map.setFilter('proposed-points',expressionFor('proposed')); const ids=DATA.proposedPoints.features.filter(f=>featureMatches(f,'proposed')).map(f=>String(f.properties.GERENCIA_ID)); map.setFilter('movement-lines',['in',['to-string',['get','GERENCIA_ID_PROPOSTA']],['literal',ids]]); updateMetrics(); }
function percentile(values,p){if(!values.length)return NaN;const s=[...values].sort((a,b)=>a-b);return s[Math.min(s.length-1,Math.ceil(p*s.length)-1)];}
function updateMetrics(){ const current=DATA.currentPoints.features.filter(f=>featureMatches(f,'current')), proposed=DATA.proposedPoints.features.filter(f=>featureMatches(f,'proposed')), distances=proposed.map(f=>Number(f.properties.DISTANCIA_MOVIMENTO_KM)).filter(Number.isFinite); const moved=proposed.filter(f=>f.properties.STATUS_TRANSICAO==='MOVIMENTADO').length; const anchors=proposed.filter(f=>truthy(f.properties.EH_ANCORA_GR)).length; document.getElementById('m-current').textContent=current.length;document.getElementById('m-proposed').textContent=proposed.length;document.getElementById('m-moved').textContent=moved;document.getElementById('m-mean').textContent=distances.length?fmt(distances.reduce((a,b)=>a+b,0)/distances.length):'—';document.getElementById('m-p90').textContent=fmt(percentile(distances,.9));document.getElementById('m-max').textContent=fmt(distances.length?Math.max(...distances):NaN);document.getElementById('m-anchor').textContent=anchors;document.getElementById('m-extra').textContent=proposed.length-anchors; }
['area','regional','movement','kind'].forEach(id=>document.getElementById(id).addEventListener('change',()=>{clearSelection();applyFilters();}));
let searchTimer; document.getElementById('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{clearSelection();applyFilters();const q=norm(document.getElementById('search').value);if(q){const f=DATA.proposedPoints.features.find(x=>featureMatches(x,'proposed'))||DATA.currentPoints.features.find(x=>featureMatches(x,'current'));if(f)map.easeTo({center:f.geometry.coordinates,zoom:6,duration:500});}},180);});
document.getElementById('reset').addEventListener('click',()=>{document.getElementById('search').value='';['area','regional','movement','kind'].forEach(id=>document.getElementById(id).value='');clearSelection();applyFilters();map.fitBounds(boundsBrazil,{padding:35,duration:700});});
document.querySelectorAll('[data-layer]').forEach(box=>box.addEventListener('change',()=>{const id=box.dataset.layer;layerVisible(id,box.checked);if(id==='portfolio-fill')layerVisible('portfolio-lines',box.checked);if(id==='gr-points')layerVisible('gr-lines',box.checked);}));
</script>
</body>
</html>''')


def render_html(payload: MapPayload, token: str, mapbox_style: str) -> str:
    area_expression_parts: list[str] = []
    for area, color in AREA_COLORS.items():
        area_expression_parts.extend([json.dumps(area, ensure_ascii=False), json.dumps(color)])
    data = {
        "brazil": payload.brazil,
        "states": payload.states,
        "mask": payload.mask,
        "municipalities": payload.municipalities,
        "proposedUnits": payload.proposed_units,
        "currentPoints": payload.current_points,
        "proposedPoints": payload.proposed_points,
        "movements": payload.movements,
        "grPoints": payload.gr_points,
        "grLines": payload.gr_lines,
        "unattended": payload.unattended,
        "currentMunicipalities": payload.current_municipalities,
        "warnings": payload.warnings,
    }
    return HTML_TEMPLATE.substitute(
        mapbox_version=MAPBOX_GL_VERSION,
        payload=json_for_html(data),
        token=json.dumps(token),
        bounds=json_for_html(BRAZIL_BOUNDS),
        style=json.dumps(mapbox_style),
        area_expression=",".join(area_expression_parts),
    )


def validate_token() -> str:
    load_dotenv(BASE_DIR / ".env", override=False)
    token = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "MAPBOX_ACCESS_TOKEN ausente. Adicione um token publico Mapbox (pk.) ao arquivo .env."
        )
    if not token.startswith("pk."):
        raise RuntimeError(
            "MAPBOX_ACCESS_TOKEN deve ser um token publico iniciado por 'pk.'. Tokens secretos nao podem ir para o HTML."
        )
    return token


def write_html(output: Path, content: str) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(output)
    return output


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        if args and str(args[1]).startswith("2"):
            return
        super().log_message(format, *args)


def serve_html(path: Path, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("--porta deve estar entre 1 e 65535.")
    handler = partial(QuietHandler, directory=str(path.parent))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel iniciar 127.0.0.1:{port}: {exc}") from exc
    url = f"http://127.0.0.1:{port}/{quote(path.name)}"
    print(f"Mapa disponivel em: {url}")
    print("Pressione Ctrl+C para encerrar.")
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()


def synthetic_self_test() -> None:
    from shapely.geometry import Polygon

    municipal = gpd.GeoDataFrame(
        {
            "CD_MUN": ["3500001", "3500002", "3300001"],
            "COD_UF": ["35", "35", "33"],
            "UF": ["SP", "SP", "RJ"],
        },
        geometry=[
            Polygon([(-48, -24), (-47, -24), (-47, -23), (-48, -23), (-48, -24)]),
            Polygon([(-47, -24), (-46, -24), (-46, -23), (-47, -23), (-47, -24)]),
            Polygon([(-46, -24), (-45, -24), (-45, -23), (-46, -23), (-46, -24)]),
        ],
        crs="EPSG:4326",
    )
    stores = pd.DataFrame(
        {
            "CHAVE_SUPERVISAO": [101, 101, 202, 202],
            "CD_MUNIC": ["3500001", "3500001", "3500001", "3300001"],
        }
    )
    index, warnings = current_portfolio_index(stores, municipal)
    assert index == {"101": ["3500001"], "202": ["3300001", "3500001"]}
    assert not warnings
    invalid_stores = pd.DataFrame({"CHAVE_SUPERVISAO": [101], "CD_MUNIC": ["9999999"]})
    _, invalid_warnings = current_portfolio_index(invalid_stores, municipal)
    assert invalid_warnings and "9999999" in invalid_warnings[0]
    brazil, states, mask = prepare_boundaries(municipal)
    assert brazil["features"] and len(states["features"]) == 2 and mask["features"]
    payload = MapPayload(
        brazil=brazil, states=states, mask=mask,
        municipalities=feature_collection_from_gdf(municipal, ["CD_MUN", "COD_UF", "UF"]),
        proposed_units=empty_feature_collection(), current_points=empty_feature_collection(),
        proposed_points=empty_feature_collection(), movements=empty_feature_collection(),
        gr_points=empty_feature_collection(), gr_lines=empty_feature_collection(),
        unattended=empty_feature_collection(), current_municipalities=index, warnings=[],
    )
    rendered = render_html(payload, "pk.TESTE_PUBLICO", "mapbox://styles/mapbox/light-v11")
    assert "world-mask" in rendered and "current-selection-fill" in rendered
    assert "function selectCurrent" in rendered and "function selectProposed" in rendered
    assert "function clearSelection" in rendered
    assert '"202":["3300001","3500001"]' in rendered
    assert "api.mapbox.com/mapbox-gl-js/v3.25.0" in rendered
    with tempfile.TemporaryDirectory() as temp:
        output = write_html(Path(temp) / "mapa.html", rendered)
        assert output.is_file() and output.stat().st_size > 1_000
    print("Autoteste sintetico concluido com sucesso.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.autoteste:
        synthetic_self_test()
        return 0
    if args.pasta_cenario is None:
        raise SystemExit("Informe --pasta-cenario ou use --autoteste.")
    if args.simplificacao_m < 0:
        raise SystemExit("--simplificacao-m nao pode ser negativo.")

    token = validate_token()
    files = discover_inputs(args.pasta_cenario)
    output = args.saida or (args.pasta_cenario / DEFAULT_HTML_NAME)
    mapbox_style = os.getenv("MAPBOX_STYLE", "mapbox://styles/mapbox/light-v11").strip()

    print(f"Lendo resultado: {files.workbook}")
    print(f"Lendo malha municipal: {args.geometria_municipios}")
    payload = build_payload(files, args.geometria_municipios, args.simplificacao_m)
    for warning in payload.warnings:
        print(f"AVISO: {warning}")
    rendered = render_html(payload, token, mapbox_style)
    output = write_html(output, rendered)
    print(f"HTML gerado: {output}")
    print(
        f"Feicoes: {len(payload.current_points['features'])} atuais, "
        f"{len(payload.proposed_points['features'])} propostas, "
        f"{len(payload.proposed_units['features'])} unidades territoriais."
    )
    if args.servir:
        serve_html(output, args.porta)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
