"""
GREENFIELD DA MALHA DE GERÊNCIAS — V3
======================================

V3 focada em:
- redistribuição das 135 gerências atuais em cenário único;
- 81 GRs como âncoras obrigatórias 1:1 em até 100 km;
- cobertura prioritária, com peso reduzido para municípios < 30 mil;
- raio de referência por faixa populacional como soft constraint;
- cidades >= 300 mil divididas em distritos;
- preferência por sedes em municípios mais populosos;
- múltiplos polos em grandes cidades quando a carga justificar;
- balanceamento de carga 75%–125%;
- continuidade territorial obrigatória e ausência de sobreposição;
- pequenos sem loja e acima de 150 km podem ficar sem atendimento;
- comparação atual x proposto e saídas SQL/Excel/GeoJSON.

Dependências:
pip install pandas numpy geopandas shapely sqlalchemy pyodbc scipy scikit-learn openpyxl

Antes de rodar, aponte a malha municipal oficial:
ARQUIVO_MUNICIPIOS_GEO=<arquivo .gpkg/.shp/.geojson com código IBGE municipal>
"""
from __future__ import annotations

import hashlib
import heapq
import json
import logging
import os
import re
import time
import traceback
import unicodedata
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.neighbors import BallTree
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

EARTH_RADIUS_KM = 6371.0088
PROHIBITED_SERVICE_COST = np.float32(1_000_000_000.0)
BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
SQL_DIR = BASE_DIR / "sql"

UF_POR_CODIGO = {
    "11":"RO","12":"AC","13":"AM","14":"RR","15":"PA","16":"AP","17":"TO",
    "21":"MA","22":"PI","23":"CE","24":"RN","25":"PB","26":"PE","27":"AL",
    "28":"SE","29":"BA","31":"MG","32":"ES","33":"RJ","35":"SP","41":"PR",
    "42":"SC","43":"RS","50":"MS","51":"MT","52":"GO","53":"DF",
}

DESC_AREA_POR_UF = {
    "BA":"NORDESTE 2","AL":"NORDESTE 2","SE":"NORDESTE 2",
    "CE":"NORDESTE 1","PB":"NORDESTE 1","PI":"NORDESTE 1","MA":"NORDESTE 1","RN":"NORDESTE 1","PE":"NORDESTE 1",
    "SP":"SÃO PAULO",
    "DF":"CENTRO OESTE/NORTE","AP":"CENTRO OESTE/NORTE","AC":"CENTRO OESTE/NORTE","RO":"CENTRO OESTE/NORTE","MT":"CENTRO OESTE/NORTE","MS":"CENTRO OESTE/NORTE","TO":"CENTRO OESTE/NORTE","AM":"CENTRO OESTE/NORTE","RR":"CENTRO OESTE/NORTE","PA":"CENTRO OESTE/NORTE","GO":"CENTRO OESTE/NORTE",
    "RS":"SUL","SC":"SUL","PR":"SUL",
    "MG":"SUDESTE","ES":"SUDESTE","RJ":"SUDESTE",
}

T_EXECUCAO = "TB_GREENFIELD_BE_EXECUCAO_V3_IGOR"
T_CENARIO = "TB_GREENFIELD_BE_CENARIO_V3_IGOR"
T_UNIDADE = "TB_GREENFIELD_BE_UNIDADE_V3_IGOR"
T_GERENCIA_PROPOSTA = "TB_GREENFIELD_BE_GERENCIA_PROPOSTA_V3_IGOR"
T_CARTEIRA_UNIDADE = "TB_GREENFIELD_BE_CARTEIRA_UNIDADE_V3_IGOR"
T_CARTEIRA_LOJA = "TB_GREENFIELD_BE_CARTEIRA_LOJA_V3_IGOR"
T_GERENCIA_ATUAL = "TB_GREENFIELD_BE_GERENCIA_ATUAL_V3_IGOR"
T_TRANSICAO = "TB_GREENFIELD_BE_TRANSICAO_V3_IGOR"
T_DIAGNOSTICO = "TB_GREENFIELD_BE_DIAGNOSTICO_V3_IGOR"
T_AUDITORIA_POLOS = "TB_GREENFIELD_BE_AUDITORIA_POLOS_V3_IGOR"
T_COMPARACAO = "TB_GREENFIELD_BE_COMPARACAO_CENARIOS_V3_IGOR"
T_VINCULO_GR = "TB_GREENFIELD_BE_VINCULO_GR_POLO_V3_IGOR"
T_HIERARQUIA_PROPOSTA = "TB_GREENFIELD_BE_HIERARQUIA_PROPOSTA_V3_IGOR"
T_COMPARACAO_HIERARQUIA = "TB_GREENFIELD_BE_COMPARACAO_HIERARQUIA_V3_IGOR"
T_AUDITORIA_TERRITORIAL = "TB_GREENFIELD_BE_AUDITORIA_TERRITORIAL_V3_IGOR"
T_NAO_ATENDIDO = "TB_GREENFIELD_BE_NAO_ATENDIDO_V3_IGOR"


@dataclass(frozen=True)
class SQLConfig:
    server: str = os.getenv("SQL_SERVER", "MZ-VV-BD-182")
    port: str = os.getenv("SQL_PORT", "1433")
    database: str = os.getenv("SQL_DATABASE", "TESTE")
    schema: str = os.getenv("SQL_SCHEMA", "dbo")
    driver: str = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    username: str = os.getenv("SQL_USERNAME", "")
    password: str = os.getenv("SQL_PASSWORD", "")
    trusted_connection: bool = os.getenv("SQL_TRUSTED_CONNECTION", "1").lower() in {"1","true","yes","y"}
    trust_server_certificate: bool = os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "1").lower() in {"1","true","yes","y"}


@dataclass(frozen=True)
class ModelConfig:
    periodo_lojas: int = int(os.getenv("PERIODO_LOJAS", "202607"))
    population_min: int = 0
    small_unit_threshold: int = 30_000
    large_city_threshold: int = 300_000
    current_manager_reference: int = 135
    manager_scenarios: tuple[int, ...] = (135,)
    require_exact_current_manager_count: bool = True
    expected_regional_points: int = 81
    regional_anchor_radius_km: float = 100.0

    district_csv_path: Path = Path(os.getenv(
        "ARQUIVO_DISTRITOS_CSV",
        str(BASE_DIR / "geometria_brasil" / "distritos_brasil" / "saidas" / "distritos_brasil_pop2022_latlon.csv"),
    ))
    district_gpkg_path: Path = Path(os.getenv(
        "ARQUIVO_DISTRITOS_GPKG",
        str(BASE_DIR / "geometria_brasil" / "distritos_brasil" / "saidas" / "distritos_brasil_pop2022.gpkg"),
    ))
    municipal_geometry_path: Path = Path(os.getenv(
        "ARQUIVO_MUNICIPIOS_GEO",
        str(BASE_DIR / "geometria_brasil" / "BR_Municipios_2025" / "BR_Municipios_2025.shp"),
    ))
    excluded_municipalities_path: Path = Path(os.getenv(
        "ARQUIVO_MUNICIPIOS_DESCONSIDERAR",
        str(BASE_DIR / "Municipios para desconsiderar atendimento.xlsx"),
    ))
    excluded_municipalities_column: str = "CDMUNIC"

    # estratégia
    candidate_parent_population_min: int = 30_000
    small_attraction_factor: float = 0.20
    small_non_store_load_factor: float = 0.25

    # raio de referência: soft constraint
    radius_30_50_km: float = 50.0
    radius_50_100_km: float = 100.0
    radius_100_300_km: float = 100.0
    district_service_radius_km: float = 50.0
    service_radius_penalty_weight: float = 1.0

    # qualidade da sede
    site_population_full_attraction: int = 300_000
    site_population_penalty_max_km: float = 35.0

    # distância
    cross_uf_equivalent_km_penalty: float = 40.0
    distance_chunk_size: int = 256

    # carga equivalente, sem produção
    population_equivalent_block: float = 100_000.0
    population_equivalent_weight: float = 1.00
    stores_equivalent_block: float = 25.0
    stores_equivalent_weight: float = 0.50
    area_equivalent_block_km2: float = 10_000.0
    area_equivalent_weight: float = 0.20
    dispersion_equivalent_block_km: float = 50.0
    dispersion_equivalent_weight: float = 0.25
    unit_fixed_load: float = 0.08
    minimum_unit_equivalent: float = 0.10
    maximum_unit_equivalent: float = 6.00

    # metrópoles
    enable_metropolitan_capacity: bool = True
    metropolitan_load_factor: float = 1.10
    minimum_metropolitan_units_per_manager: int = 2

    # otimização
    refine_iterations: int = 12
    minimum_load_factor: float = 0.75
    maximum_load_factor: float = 1.25
    balancing_max_iterations: int = 20_000
    relevant_max_extra_km: float = 150.0
    capacity_soft_tolerance: float = 1.05
    balance_improvement_equivalent_km: float = 60.0

    # topologia
    require_topology_for_v3: bool = True
    min_shared_boundary_m: float = 100.0
    max_topology_gap_m: float = 500.0
    overlap_absolute_tolerance_km2: float = 0.01
    overlap_relative_tolerance: float = 0.0001
    repair_critical_islands: bool = True
    island_repair_capacity_tolerance: float = 1.10
    island_repair_max_extra_weighted_cost: float = 250.0
    contiguous_growth_load_penalty_km: float = 120.0
    allow_unserved_small_components: bool = True

    # diagnóstico
    small_distance_diagnostic_km: float = 150.0
    small_unserved_distance_km: float = 150.0
    pole_audit_search_radius_km: float = 150.0

    # saída
    save_sql: bool = True
    save_excel: bool = True
    save_geojson: bool = True
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR / "saida_greenfield_v3")))
    sql_chunksize: int = 2_000
    model_version: str = "V3.2_GREENFIELD_135_GR_HIERARQUIA"


def load_sql_file(filename: str) -> str:
    path = SQL_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo SQL não encontrado: {path}")
    query = path.read_text(encoding="utf-8-sig").strip()
    if not query:
        raise ValueError(f"Arquivo SQL vazio: {path}")
    return query


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S", force=True)


def log_step(msg: str) -> None:
    logging.info("=" * 88); logging.info(msg); logging.info("=" * 88)


def create_sql_engine(config: SQLConfig) -> Engine:
    q = {"driver": config.driver, "TrustServerCertificate": "yes" if config.trust_server_certificate else "no"}
    if config.trusted_connection:
        q["Trusted_Connection"] = "yes"
        url = URL.create("mssql+pyodbc", host=config.server, port=int(config.port), database=config.database, query=q)
    else:
        if not config.username or not config.password:
            raise ValueError("Defina SQL_USERNAME/SQL_PASSWORD ou SQL_TRUSTED_CONNECTION=1")
        url = URL.create("mssql+pyodbc", username=config.username, password=config.password,
                         host=config.server, port=int(config.port), database=config.database, query=q)
    return create_engine(url, fast_executemany=True, pool_pre_ping=True)


def uppercase_columns(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy(); x.columns = [str(c).strip().upper() for c in x.columns]; return x


def normalize_text_key(value: object) -> str:
    if pd.isna(value): return ""
    text_value=unicodedata.normalize("NFKD",str(value).strip().upper())
    return re.sub(r"\s+"," ","".join(c for c in text_value if not unicodedata.combining(c)))


def canonical_area(value: object) -> str:
    key=re.sub(r"\s*/\s*","/",normalize_text_key(value).replace("-"," "))
    aliases={normalize_text_key(v).replace("-"," "):v for v in sorted(set(DESC_AREA_POR_UF.values()))}
    aliases.update({"SAO PAULO":"SÃO PAULO","NORDESTE I":"NORDESTE 1","NORDESTE II":"NORDESTE 2","CENTRO OESTE NORTE":"CENTRO OESTE/NORTE","CENTRO OESTE/NORTE":"CENTRO OESTE/NORTE"})
    return aliases.get(key,str(value).strip().upper() if not pd.isna(value) else "SEM_AREA")


def digits_only(value: object) -> Optional[str]:
    if pd.isna(value): return None
    s = re.sub(r"\D", "", str(value).strip()); return s or None


def normalize_code_value(value: object, length: int) -> Optional[str]:
    s = digits_only(value)
    if not s: return None
    if len(s) < length: s = s.zfill(length)
    if len(s) > length: s = s[:length]
    return s


def normalize_code(series: pd.Series, length: int) -> pd.Series:
    return series.map(lambda v: normalize_code_value(v, length))


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", ".", regex=False), errors="coerce")


def valid_coordinates(df: pd.DataFrame, lat: str, lon: str) -> pd.Series:
    return numeric(df[lat]).between(-35.5, 6.5) & numeric(df[lon]).between(-75.5, -32.0)


def map_to_ibge(series: pd.Series, municipal_reference: pd.DataFrame) -> pd.Series:
    valid7 = set(municipal_reference["COD_IBGE"].dropna().astype(str))
    six = {c[:6]: c for c in valid7 if len(c) == 7}
    def cv(v):
        c = digits_only(v)
        if not c: return None
        if len(c) >= 7 and c[:7] in valid7: return c[:7]
        if len(c) == 6 and c in six: return six[c]
        return None
    return series.map(cv)


def haversine_arrays(lat1, lon1, lat2, lon2) -> np.ndarray:
    a1,o1,a2,o2 = map(np.radians, (lat1,lon1,lat2,lon2))
    h = np.sin((a2-a1)/2)**2 + np.cos(a1)*np.cos(a2)*np.sin((o2-o1)/2)**2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(h,0,1)))


def pairwise_haversine_matrix(left, right, llat, llon, rlat, rlon) -> np.ndarray:
    a1=np.radians(left[llat].to_numpy(float))[:,None]; o1=np.radians(left[llon].to_numpy(float))[:,None]
    a2=np.radians(right[rlat].to_numpy(float))[None,:]; o2=np.radians(right[rlon].to_numpy(float))[None,:]
    h=np.sin((a2-a1)/2)**2 + np.cos(a1)*np.cos(a2)*np.sin((o2-o1)/2)**2
    return 2*EARTH_RADIUS_KM*np.arcsin(np.sqrt(np.clip(h,0,1)))


def haversine_matrix_float32(demand: pd.DataFrame, candidates: pd.DataFrame, chunk: int) -> np.ndarray:
    dr=np.radians(demand[["LATITUDE","LONGITUDE"]].to_numpy(float)); cr=np.radians(candidates[["LATITUDE","LONGITUDE"]].to_numpy(float))
    out=np.empty((len(demand),len(candidates)),dtype=np.float32)
    lat=dr[:,0][:,None]; lon=dr[:,1][:,None]; cos=np.cos(lat)
    for start in range(0,len(candidates),chunk):
        stop=min(start+chunk,len(candidates)); clat=cr[start:stop,0][None,:]; clon=cr[start:stop,1][None,:]
        h=np.sin((clat-lat)/2)**2 + cos*np.cos(clat)*np.sin((clon-lon)/2)**2
        out[:,start:stop]=(2*EARTH_RADIUS_KM*np.arcsin(np.sqrt(np.clip(h,0,1)))).astype(np.float32)
    logging.info("Matriz Haversine: %s x %s | %.1f MB", *out.shape, out.nbytes/1048576)
    return out


def weighted_percentile(values: pd.Series, weights: pd.Series, q: float) -> float:
    m=values.notna() & weights.notna() & (weights>0)
    if not m.any(): return float("nan")
    v=values[m].to_numpy(float); w=weights[m].to_numpy(float); order=np.argsort(v); v=v[order]; w=w[order]
    c=np.cumsum(w); pos=np.searchsorted(c,q*c[-1],side="left"); return float(v[min(pos,len(v)-1)])


def config_hash(config: ModelConfig) -> str:
    return hashlib.sha256(json.dumps(asdict(config), sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def safe_sql_frame(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    for c in x.columns:
        if x[c].dtype=="object":
            x[c]=x[c].map(lambda v: json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list,tuple,set)) else v)
    return x


def write_sql_table(engine: Engine, df: pd.DataFrame, name: str, sql: SQLConfig, cfg: ModelConfig) -> None:
    if df.empty: return
    safe_sql_frame(df).to_sql(name, engine, schema=sql.schema, if_exists="append", index=False, chunksize=cfg.sql_chunksize, method=None)
    logging.info("SQL gravado: %s.%s | %s linhas", sql.schema, name, len(df))

# =============================================================================
# GEOGRAFIA / ENTRADAS
# =============================================================================

def normalize_geodataframe_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    geom=gdf.geometry.name
    x=gdf.rename(columns={c:("geometry" if c==geom else str(c).strip().upper()) for c in gdf.columns}).set_geometry("geometry")
    if x.crs is None: raise ValueError("Malha geográfica sem CRS.")
    return x


def load_district_data(cfg: ModelConfig) -> tuple[pd.DataFrame, Optional[gpd.GeoDataFrame]]:
    csv=None; geo=None
    if cfg.district_csv_path.exists():
        csv=uppercase_columns(pd.read_csv(cfg.district_csv_path,dtype=str,encoding="utf-8-sig",low_memory=False))
    if cfg.district_gpkg_path.exists():
        try: geo=gpd.read_file(cfg.district_gpkg_path,engine="pyogrio")
        except (ImportError,ModuleNotFoundError): geo=gpd.read_file(cfg.district_gpkg_path)
        geo=normalize_geodataframe_columns(geo)
        code=next((c for c in ("CD_DIST","CD_DISTRITO","COD_DIST","CODIGO") if c in geo.columns),None)
        if not code: raise ValueError("Malha distrital sem código de distrito.")
        if code!="CD_DIST": geo=geo.rename(columns={code:"CD_DIST"})
        geo["CD_DIST"]=normalize_code(geo["CD_DIST"],9)
        geo["CD_MUN"]=normalize_code(geo["CD_MUN"],7) if "CD_MUN" in geo.columns else geo["CD_DIST"].str[:7]
        geo=geo[geo["CD_DIST"].notna() & geo.geometry.notna() & ~geo.geometry.is_empty].copy()
        if geo["CD_DIST"].duplicated().any():
            cols=[c for c in geo.columns if c not in {"CD_DIST","geometry"}]
            geo=geo.dissolve(by="CD_DIST",as_index=False,aggfunc={c:"first" for c in cols})
        metric=geo.to_crs(epsg=5880)
        geo["AREA_KM2_GEOMETRIA"]=metric.geometry.area.to_numpy()/1_000_000
        rep=gpd.GeoSeries(metric.geometry.representative_point(),index=metric.index,crs=metric.crs).to_crs(epsg=4326)
        geo["LAT_GEOMETRIA"]=rep.y.to_numpy(); geo["LON_GEOMETRIA"]=rep.x.to_numpy()
    if csv is None and geo is None: raise FileNotFoundError("Nenhuma base distrital encontrada.")
    if csv is None: csv=pd.DataFrame(geo.drop(columns="geometry"))
    if "CD_DIST" not in csv.columns:
        code=next((c for c in ("CD_DISTRITO","COD_DIST","CODIGO") if c in csv.columns),None)
        if not code: raise ValueError("CSV distrital sem código.")
        csv=csv.rename(columns={code:"CD_DIST"})
    csv["CD_DIST"]=normalize_code(csv["CD_DIST"],9)
    csv["CD_MUN"]=normalize_code(csv["CD_MUN"],7) if "CD_MUN" in csv.columns else csv["CD_DIST"].str[:7]
    pop_col=next((c for c in ("POP_2022","V0001","POPULACAO","POP_DISTRITO_2022") if c in csv.columns),None)
    if not pop_col: raise ValueError("Base distrital precisa de POP_2022/V0001/POPULACAO.")
    csv["POP_DISTRITO_2022"]=pd.to_numeric(csv[pop_col],errors="coerce")
    if geo is not None:
        attrs=geo[["CD_DIST","AREA_KM2_GEOMETRIA","LAT_GEOMETRIA","LON_GEOMETRIA"]].drop_duplicates("CD_DIST")
        for col in ("AREA_KM2_GEOMETRIA","LAT_GEOMETRIA","LON_GEOMETRIA"):
            if col not in csv.columns: csv=csv.merge(attrs[["CD_DIST",col]],on="CD_DIST",how="left")
    lat=next((c for c in ("LATITUDE","LAT_GEOMETRIA","LATITUDE_DISTRITO") if c in csv.columns),None)
    lon=next((c for c in ("LONGITUDE","LON_GEOMETRIA","LONGITUDE_DISTRITO") if c in csv.columns),None)
    area=next((c for c in ("AREA_KM2_CALCULADA","AREA_KM2_GEOMETRIA","AREA_KM2") if c in csv.columns),None)
    if not lat or not lon: raise ValueError("Base distrital sem LAT/LON.")
    csv["LATITUDE_DISTRITO"]=numeric(csv[lat]); csv["LONGITUDE_DISTRITO"]=numeric(csv[lon]); csv["AREA_KM2_DISTRITO"]=numeric(csv[area]) if area else 0.0
    for c in ("NM_MUN","NM_DIST","NM_UF"):
        if c not in csv.columns: csv[c]=pd.NA
    csv=csv.dropna(subset=["CD_DIST","LATITUDE_DISTRITO","LONGITUDE_DISTRITO"]).drop_duplicates("CD_DIST")
    csv["COD_UF"]=csv["CD_MUN"].str[:2]; csv["UF"]=csv["COD_UF"].map(UF_POR_CODIGO)
    logging.info("Distritos carregados: %s",len(csv))
    return csv.reset_index(drop=True),geo


def load_municipal_geometry(cfg: ModelConfig) -> gpd.GeoDataFrame:
    p=cfg.municipal_geometry_path
    if not p.exists():
        msg=f"Malha municipal não encontrada: {p}. Defina ARQUIVO_MUNICIPIOS_GEO."
        if cfg.require_topology_for_v3: raise FileNotFoundError(msg)
        logging.warning(msg); return gpd.GeoDataFrame(columns=["CD_MUN","geometry"],geometry="geometry",crs="EPSG:4326")
    try: g=gpd.read_file(p,engine="pyogrio")
    except (ImportError,ModuleNotFoundError): g=gpd.read_file(p)
    g=normalize_geodataframe_columns(g)
    code=next((c for c in ("CD_MUN","CD_MUNIC","COD_IBGE","CODIGO_IBGE","GEOCODIGO","CD_GEOCMU","CODMUN","COD_MUN") if c in g.columns),None)
    if not code: raise ValueError("Malha municipal sem código IBGE reconhecível.")
    if code!="CD_MUN": g=g.rename(columns={code:"CD_MUN"})
    g["CD_MUN"]=normalize_code(g["CD_MUN"],7); g=g[g["CD_MUN"].notna() & g.geometry.notna() & ~g.geometry.is_empty].copy()
    if g["CD_MUN"].duplicated().any():
        cols=[c for c in g.columns if c not in {"CD_MUN","geometry"}]
        g=g.dissolve(by="CD_MUN",as_index=False,aggfunc={c:"first" for c in cols})
    if g.crs.to_epsg()!=4326: g=g.to_crs(epsg=4326)
    logging.info("Geometrias municipais: %s",len(g)); return g.reset_index(drop=True)


def load_raw_data(engine: Engine, cfg: ModelConfig) -> dict[str,pd.DataFrame]:
    return {
        "municipalities":uppercase_columns(pd.read_sql(text(load_sql_file("COORDENADAS_MUNICIPIOS.sql")),engine)),
        "population":uppercase_columns(pd.read_sql(text(load_sql_file("POPULACAO.sql")),engine)),
        "stores":uppercase_columns(pd.read_sql(text(load_sql_file("LOJAS.sql")),engine,params={"periodo":cfg.periodo_lojas})),
        "current_poles":uppercase_columns(pd.read_sql(text(load_sql_file("POLOS_ATUAIS.sql")),engine)),
        "regional_points":uppercase_columns(pd.read_sql(text(load_sql_file("BASE_GR.sql")),engine)),
        "current_hierarchy":uppercase_columns(pd.read_sql(text(load_sql_file("HIERARQUIA_ATUAL.sql")),engine)),
    }


def prepare_municipal_reference(municipalities: pd.DataFrame, population: pd.DataFrame, municipal_geo: gpd.GeoDataFrame) -> pd.DataFrame:
    m=municipalities.rename(columns={"CODIGO_IBGE":"COD_IBGE","LATITUDE":"LATITUDE_MUNICIPIO","LONGITUDE":"LONGITUDE_MUNICIPIO"}).copy()
    p=population.rename(columns={"COD_UN_REG":"COD_IBGE","POPULACAO":"POPULACAO_SQL_REFERENCIA"}).copy()
    m["COD_IBGE"]=normalize_code(m["COD_IBGE"],7); p["COD_IBGE"]=normalize_code(p["COD_IBGE"],7)
    m["LATITUDE_MUNICIPIO"]=numeric(m["LATITUDE_MUNICIPIO"]); m["LONGITUDE_MUNICIPIO"]=numeric(m["LONGITUDE_MUNICIPIO"]); p["POPULACAO_SQL_REFERENCIA"]=numeric(p["POPULACAO_SQL_REFERENCIA"])
    m=m.dropna(subset=["COD_IBGE","LATITUDE_MUNICIPIO","LONGITUDE_MUNICIPIO"]); m=m[valid_coordinates(m,"LATITUDE_MUNICIPIO","LONGITUDE_MUNICIPIO")].drop_duplicates("COD_IBGE")
    p=p.dropna(subset=["COD_IBGE","POPULACAO_SQL_REFERENCIA"]).drop_duplicates("COD_IBGE",keep="last")
    x=m.merge(p[["COD_IBGE","POPULACAO_SQL_REFERENCIA"]],on="COD_IBGE",how="left")
    x["POPULACAO_MUNICIPIO"]=x["POPULACAO_SQL_REFERENCIA"].fillna(0).astype(float); x["COD_UF"]=x["COD_IBGE"].str[:2]; x["UF"]=x["COD_UF"].map(UF_POR_CODIGO)

    # Área municipal calculada pela própria malha oficial; evita zerar o componente de área.
    metric=municipal_geo[["CD_MUN","geometry"]].to_crs(epsg=5880).copy()
    geo_attrs=pd.DataFrame({"COD_IBGE":metric["CD_MUN"].astype(str),"AREA_KM2_MUNICIPIO":metric.geometry.area.to_numpy()/1_000_000})
    names=[c for c in ("NM_MUN","NM_MUNICIP","NOME_MUNICIPIO") if c in municipal_geo.columns]
    if names:
        geo_attrs["NM_MUN"]=municipal_geo[names[0]].to_numpy()
    x=x.merge(geo_attrs.drop_duplicates("COD_IBGE"),on="COD_IBGE",how="left")
    if "NM_MUN" not in x.columns: x["NM_MUN"]=pd.NA
    x["AREA_KM2_MUNICIPIO"]=pd.to_numeric(x["AREA_KM2_MUNICIPIO"],errors="coerce").fillna(0.0)
    return x.reset_index(drop=True)


def prepare_regional_points(raw: pd.DataFrame, municipal_geo: gpd.GeoDataFrame, cfg: ModelConfig) -> pd.DataFrame:
    required={"COD_GER_REG","GER_REGIONAL","LATITUDE","LONGITUDE"}
    if not required.issubset(raw.columns): raise ValueError(f"BASE_GR.sql precisa retornar {required}")
    x=raw.copy(); x["COD_GER_REG"]=x["COD_GER_REG"].astype("string").str.strip(); x["GER_REGIONAL"]=x["GER_REGIONAL"].astype("string").str.strip(); x["LATITUDE"]=numeric(x["LATITUDE"]); x["LONGITUDE"]=numeric(x["LONGITUDE"])
    x=x.dropna(subset=["COD_GER_REG","GER_REGIONAL","LATITUDE","LONGITUDE"]); x=x[valid_coordinates(x,"LATITUDE","LONGITUDE")].copy()
    if x["COD_GER_REG"].duplicated().any():
        dup=x.loc[x["COD_GER_REG"].duplicated(False),"COD_GER_REG"].astype(str).unique().tolist()
        raise RuntimeError(f"BASE_GR contém COD_GER_REG duplicado: {dup[:10]}")
    if len(x)!=cfg.expected_regional_points: raise RuntimeError(f"Esperadas exatamente {cfg.expected_regional_points} GRs; retornaram {len(x)}.")
    polys=municipal_geo[["CD_MUN","geometry"]].to_crs(epsg=4326).copy(); pts=gpd.GeoDataFrame(x.reset_index(drop=True),geometry=gpd.points_from_xy(x["LONGITUDE"],x["LATITUDE"]),crs="EPSG:4326")
    joined=gpd.sjoin(pts,polys,how="left",predicate="within").drop_duplicates("COD_GER_REG")
    joined["METODO_UF_GR"]=np.where(joined["CD_MUN"].notna(),"PONTO_DENTRO_MUNICIPIO",pd.NA)
    missing=joined["CD_MUN"].isna()
    if missing.any():
        nearest=gpd.sjoin_nearest(joined.loc[missing,pts.columns].to_crs(epsg=5880),polys.to_crs(epsg=5880),how="left",distance_col="DISTANCIA_AJUSTE_GR_M").to_crs(epsg=4326).drop_duplicates("COD_GER_REG")
        nearest=nearest.set_index("COD_GER_REG"); joined=joined.set_index("COD_GER_REG"); joined.loc[nearest.index,"CD_MUN"]=nearest["CD_MUN"]; joined.loc[nearest.index,"METODO_UF_GR"]="MUNICIPIO_MAIS_PROXIMO"; joined.loc[nearest.index,"DISTANCIA_AJUSTE_GR_M"]=nearest["DISTANCIA_AJUSTE_GR_M"]; joined=joined.reset_index()
    joined["COD_UF_GR"]=joined["CD_MUN"].astype(str).str[:2]; joined["UF_GR"]=joined["COD_UF_GR"].map(UF_POR_CODIGO); joined["DESC_GERENCIA_AREA_GR"]=joined["UF_GR"].map(DESC_AREA_POR_UF)
    if joined[["UF_GR","DESC_GERENCIA_AREA_GR"]].isna().any().any(): raise RuntimeError("Não foi possível derivar UF/área para todas as GRs.")
    return pd.DataFrame(joined.drop(columns=["geometry","index_right"],errors="ignore")).reset_index(drop=True)


def prepare_current_hierarchy(raw: pd.DataFrame) -> pd.DataFrame:
    required={"DESC_GERENCIA_AREA","DESC_COORDENACAO","DESC_SUPERVISAO","CHAVE_SUPERVISAO"}
    if not required.issubset(raw.columns): raise ValueError(f"HIERARQUIA_ATUAL.sql precisa retornar {required}")
    x=raw[list(required)].copy(); x["CHAVE_SUPERVISAO"]=x["CHAVE_SUPERVISAO"].astype("string").str.strip(); x=x.dropna(subset=["CHAVE_SUPERVISAO"])
    x["DESC_GERENCIA_AREA_ATUAL"]=x["DESC_GERENCIA_AREA"].map(canonical_area)
    ambiguous=x.groupby("CHAVE_SUPERVISAO")["DESC_GERENCIA_AREA_ATUAL"].nunique(); ambiguous=ambiguous[ambiguous>1]
    if not ambiguous.empty: raise RuntimeError(f"Supervisões associadas a mais de uma gerência de área: {ambiguous.index.astype(str).tolist()[:10]}")
    return x.drop(columns=["DESC_GERENCIA_AREA"]).sort_values("CHAVE_SUPERVISAO").drop_duplicates("CHAVE_SUPERVISAO").reset_index(drop=True)


def attach_current_hierarchy(current: pd.DataFrame, hierarchy: pd.DataFrame) -> pd.DataFrame:
    x=current.merge(hierarchy,on="CHAVE_SUPERVISAO",how="left")
    missing=x["DESC_GERENCIA_AREA_ATUAL"].isna()
    if missing.any(): raise RuntimeError(f"{int(missing.sum())} gerentes atuais não possuem hierarquia em HIERARQUIA_ATUAL.sql.")
    unknown=sorted(set(x["DESC_GERENCIA_AREA_ATUAL"].astype(str))-set(DESC_AREA_POR_UF.values()))
    if unknown: raise RuntimeError(f"DESC_GERENCIA_AREA atuais fora do domínio previsto: {unknown}")
    return x

def load_excluded_municipalities(ref: pd.DataFrame, cfg: ModelConfig) -> set[str]:
    p=cfg.excluded_municipalities_path
    if not p.exists(): logging.warning("Sem arquivo de desconsiderados: %s",p); return set()
    f=uppercase_columns(pd.read_excel(p) if p.suffix.lower() in {".xlsx",".xls"} else pd.read_csv(p,dtype=str))
    col=cfg.excluded_municipalities_column.upper()
    if col not in f.columns: raise ValueError(f"Arquivo de desconsiderados sem coluna {col}")
    return set(map_to_ibge(f[col],ref).dropna().astype(str))


def prepare_stores(stores: pd.DataFrame, ref: pd.DataFrame) -> pd.DataFrame:
    req={"CHAVE_LOJA","CD_MUNIC","LATITUDE","LONGITUDE","CHAVE_SUPERVISAO"}
    if not req.issubset(stores.columns): raise ValueError(f"QUERY_LOJAS precisa retornar {req}")
    x=stores.copy(); x["CHAVE_LOJA"]=x["CHAVE_LOJA"].astype("string").str.strip(); x["CHAVE_SUPERVISAO"]=x["CHAVE_SUPERVISAO"].astype("string").str.strip()
    x["COD_IBGE"]=map_to_ibge(x["CD_MUNIC"],ref); x["LATITUDE"]=numeric(x["LATITUDE"]); x["LONGITUDE"]=numeric(x["LONGITUDE"])
    x=x.dropna(subset=["CHAVE_LOJA","COD_IBGE","LATITUDE","LONGITUDE"]); x=x[valid_coordinates(x,"LATITUDE","LONGITUDE")].drop_duplicates("CHAVE_LOJA")
    x["COD_UF"]=x["COD_IBGE"].str[:2]; x["UF"]=x["COD_UF"].map(UF_POR_CODIGO); return x.reset_index(drop=True)


def prepare_current_poles(df: pd.DataFrame, cfg: ModelConfig) -> pd.DataFrame:
    x=df.rename(columns={"LAT":"LATITUDE_ATUAL","LON":"LONGITUDE_ATUAL"}).copy(); x["CHAVE_SUPERVISAO"]=x["CHAVE_SUPERVISAO"].astype("string").str.strip()
    x["LATITUDE_ATUAL"]=numeric(x["LATITUDE_ATUAL"]); x["LONGITUDE_ATUAL"]=numeric(x["LONGITUDE_ATUAL"])
    x=x.dropna(subset=["CHAVE_SUPERVISAO","LATITUDE_ATUAL","LONGITUDE_ATUAL"]); x=x[valid_coordinates(x,"LATITUDE_ATUAL","LONGITUDE_ATUAL")].drop_duplicates("CHAVE_SUPERVISAO").reset_index(drop=True)
    x["GERENTE_ATUAL_IDX"]=np.arange(len(x),dtype=int)
    if len(x)!=cfg.current_manager_reference:
        msg=f"Esperados exatamente {cfg.current_manager_reference} polos atuais; retornaram {len(x)}."
        if cfg.require_exact_current_manager_count: raise RuntimeError(msg)
        logging.warning(msg)
    return x


def population_rule(pop: float, unit_type: str, cfg: ModelConfig) -> tuple[str,float,Optional[float]]:
    if unit_type=="DISTRITO": return "DISTRITO_GRANDE_CIDADE",1.0,cfg.district_service_radius_km
    if pop<cfg.small_unit_threshold: return "ABAIXO_30_MIL",cfg.small_attraction_factor,None
    if pop<50_000: return "30_A_50_MIL",1.0,cfg.radius_30_50_km
    if pop<100_000: return "50_A_100_MIL",1.0,cfg.radius_50_100_km
    if pop<cfg.large_city_threshold: return "100_A_300_MIL",1.0,cfg.radius_100_300_km
    return "300_MIL_OU_MAIS",1.0,cfg.district_service_radius_km


def build_hybrid_demand_units(ref: pd.DataFrame, districts: pd.DataFrame, excluded: set[str], cfg: ModelConfig) -> tuple[pd.DataFrame,set[str]]:
    eligible=ref[(ref["POPULACAO_MUNICIPIO"]>=cfg.population_min) & ~ref["COD_IBGE"].isin(excluded)].copy(); groups=districts.groupby("CD_MUN"); split=set(); units=[]
    for r in eligible.itertuples(index=False):
        code=str(r.COD_IBGE); pop=float(r.POPULACAO_MUNICIPIO); do_split=pop>=cfg.large_city_threshold and code in groups.groups
        if do_split:
            split.add(code)
            dg=groups.get_group(code).copy()
            raw=pd.to_numeric(dg["POP_DISTRITO_2022"],errors="coerce").fillna(0).clip(lower=0).to_numpy(float)
            # Proteção metodológica: a soma dos distritos sempre fecha com a população municipal.
            # Se a fonte distrital estiver replicada/imperfeita, o modelo não multiplica artificialmente a cidade.
            if raw.sum()>0:
                normalized=raw*(pop/raw.sum())
                fonte="DISTRITO_NORMALIZADO_AO_TOTAL_MUNICIPAL"
            else:
                area=pd.to_numeric(dg["AREA_KM2_DISTRITO"],errors="coerce").fillna(0).clip(lower=0).to_numpy(float)
                if area.sum()>0: normalized=pop*area/area.sum(); fonte="DISTRITO_RATEADO_POR_AREA"
                else: normalized=np.full(len(dg),pop/max(len(dg),1)); fonte="DISTRITO_RATEADO_IGUAL"
            dg=dg.assign(POP_DISTRITO_MODELO=normalized)
            for d in dg.itertuples(index=False):
                dp=float(d.POP_DISTRITO_MODELO); band,factor,radius=population_rule(dp,"DISTRITO",cfg)
                units.append({"DEMAND_ID":f"DIST-{d.CD_DIST}","TIPO_UNIDADE":"DISTRITO","COD_IBGE":code,"CD_DIST":d.CD_DIST,"NM_MUN":d.NM_MUN if pd.notna(d.NM_MUN) else r.NM_MUN,"NM_DIST":d.NM_DIST,"COD_UF":r.COD_UF,"UF":r.UF,"POPULACAO_MUNICIPIO":pop,"POPULACAO_UNIDADE":dp,"LATITUDE":float(d.LATITUDE_DISTRITO),"LONGITUDE":float(d.LONGITUDE_DISTRITO),"AREA_KM2":float(d.AREA_KM2_DISTRITO) if pd.notna(d.AREA_KM2_DISTRITO) else 0.0,"MUNICIPIO_DIVIDIDO":True,"FONTE_POPULACAO":fonte,"FAIXA_POPULACIONAL":band,"FATOR_ATRACAO_FAIXA":factor,"RAIO_REFERENCIA_KM":radius})
        else:
            band,factor,radius=population_rule(pop,"MUNICIPIO",cfg)
            area=float(getattr(r,"AREA_KM2_MUNICIPIO",0.0)) if pd.notna(getattr(r,"AREA_KM2_MUNICIPIO",0.0)) else 0.0
            units.append({"DEMAND_ID":f"MUN-{code}","TIPO_UNIDADE":"MUNICIPIO","COD_IBGE":code,"CD_DIST":pd.NA,"NM_MUN":r.NM_MUN,"NM_DIST":pd.NA,"COD_UF":r.COD_UF,"UF":r.UF,"POPULACAO_MUNICIPIO":pop,"POPULACAO_UNIDADE":pop,"LATITUDE":float(r.LATITUDE_MUNICIPIO),"LONGITUDE":float(r.LONGITUDE_MUNICIPIO),"AREA_KM2":area,"MUNICIPIO_DIVIDIDO":False,"FONTE_POPULACAO":"IBGE_MUNICIPIO","FAIXA_POPULACIONAL":band,"FATOR_ATRACAO_FAIXA":factor,"RAIO_REFERENCIA_KM":radius})
    x=pd.DataFrame(units)
    if x.empty or x["DEMAND_ID"].duplicated().any(): raise RuntimeError("Erro na criação da base híbrida.")
    x["EH_MUNICIPIO_PEQUENO"]=x["TIPO_UNIDADE"].eq("MUNICIPIO") & x["POPULACAO_UNIDADE"].lt(cfg.small_unit_threshold); x["EH_DISTRITO"]=x["TIPO_UNIDADE"].eq("DISTRITO"); x["EH_UNIDADE_ESTRATEGICA"]=~x["EH_MUNICIPIO_PEQUENO"]
    x=x.sort_values(["COD_UF","COD_IBGE","TIPO_UNIDADE","CD_DIST"],na_position="first").reset_index(drop=True); x["DEMAND_IDX"]=np.arange(len(x),dtype=int)
    logging.info("Demanda híbrida: %s unidades | %s cidades divididas | %s municípios <30 mil",len(x),len(split),int(x["EH_MUNICIPIO_PEQUENO"].sum()))
    return x,split

def assign_split_stores_by_nearest_district(split_stores: pd.DataFrame, district_units: pd.DataFrame) -> pd.DataFrame:
    out=[]
    for code,g in split_stores.groupby("COD_IBGE",sort=False):
        u=district_units[district_units["COD_IBGE"]==code]
        if u.empty: continue
        tree=BallTree(np.radians(u[["LATITUDE","LONGITUDE"]].to_numpy(float)),metric="haversine")
        _,idx=tree.query(np.radians(g[["LATITUDE","LONGITUDE"]].to_numpy(float)),k=1)
        m=g.copy(); m["DEMAND_ID"]=u.iloc[idx[:,0]]["DEMAND_ID"].to_numpy(); m["METODO_ASSOCIACAO_UNIDADE"]="DISTRITO_MAIS_PROXIMO"; out.append(m)
    return pd.concat(out,ignore_index=True) if out else pd.DataFrame(columns=list(split_stores.columns)+["DEMAND_ID","METODO_ASSOCIACAO_UNIDADE"])


def assign_stores_to_demand_units(stores: pd.DataFrame, demand: pd.DataFrame, split: set[str], district_geo: Optional[gpd.GeoDataFrame]) -> pd.DataFrame:
    stores=stores[stores["COD_IBGE"].isin(set(demand["COD_IBGE"]))].copy(); normal=stores[~stores["COD_IBGE"].isin(split)].copy(); normal["DEMAND_ID"]="MUN-"+normal["COD_IBGE"]; normal["METODO_ASSOCIACAO_UNIDADE"]="CODIGO_MUNICIPIO"
    s=stores[stores["COD_IBGE"].isin(split)].copy(); dunit=demand[demand["TIPO_UNIDADE"]=="DISTRITO"][["DEMAND_ID","COD_IBGE","CD_DIST","LATITUDE","LONGITUDE"]]
    sr=pd.DataFrame()
    if not s.empty and district_geo is not None:
        geo=district_geo[district_geo["CD_MUN"].isin(split)][["CD_MUN","CD_DIST","geometry"]].copy(); geo=geo.to_crs(epsg=4326) if geo.crs.to_epsg()!=4326 else geo; geo=geo.merge(dunit[["CD_DIST","DEMAND_ID"]],on="CD_DIST",how="inner")
        pts=gpd.GeoDataFrame(s.copy(),geometry=gpd.points_from_xy(s["LONGITUDE"],s["LATITUDE"]),crs="EPSG:4326")
        j=gpd.sjoin(pts,geo[["CD_MUN","CD_DIST","DEMAND_ID","geometry"]],how="left",predicate="within").sort_values("CHAVE_LOJA").drop_duplicates("CHAVE_LOJA")
        j["METODO_ASSOCIACAO_UNIDADE"]=np.where(j["DEMAND_ID"].notna(),"PONTO_DENTRO_DISTRITO",pd.NA); sr=pd.DataFrame(j.drop(columns=["geometry","index_right"],errors="ignore")); missing=sr["DEMAND_ID"].isna()
        if missing.any():
            fb=assign_split_stores_by_nearest_district(sr.loc[missing,stores.columns].copy(),dunit); sr=pd.concat([sr.loc[~missing],fb],ignore_index=True,sort=False)
    elif not s.empty: sr=assign_split_stores_by_nearest_district(s,dunit)
    x=pd.concat([normal,sr],ignore_index=True,sort=False).drop_duplicates("CHAVE_LOJA"); x=x[x["DEMAND_ID"].isin(set(demand["DEMAND_ID"]))].reset_index(drop=True)
    logging.info("Lojas associadas: %s",len(x)); return x


def enrich_demand_units_with_score(demand: pd.DataFrame, stores: pd.DataFrame, cfg: ModelConfig) -> pd.DataFrame:
    rec=[]; idx=demand.set_index("DEMAND_ID")
    for did,g in stores.groupby("DEMAND_ID",sort=False):
        if did not in idx.index: continue
        u=idx.loc[did]; dist=haversine_arrays(g["LATITUDE"].to_numpy(float),g["LONGITUDE"].to_numpy(float),np.full(len(g),float(u["LATITUDE"])),np.full(len(g),float(u["LONGITUDE"])))
        rec.append({"DEMAND_ID":did,"QTD_LOJAS":int(g["CHAVE_LOJA"].nunique()),"DISPERSAO_MEDIA_LOJAS_KM":float(np.mean(dist)) if len(dist) else 0.0,"DISPERSAO_P90_LOJAS_KM":float(np.quantile(dist,.9)) if len(dist) else 0.0})
    stats=pd.DataFrame(rec); x=demand.merge(stats,on="DEMAND_ID",how="left") if not stats.empty else demand.copy()
    for c in ("QTD_LOJAS","DISPERSAO_MEDIA_LOJAS_KM","DISPERSAO_P90_LOJAS_KM"): x[c]=x.get(c,0.0); x[c]=x[c].fillna(0.0)
    x["COMPONENTE_UE_POPULACAO"]=x["POPULACAO_UNIDADE"]/cfg.population_equivalent_block*cfg.population_equivalent_weight
    x["COMPONENTE_UE_LOJAS"]=x["QTD_LOJAS"]/cfg.stores_equivalent_block*cfg.stores_equivalent_weight
    x["COMPONENTE_UE_AREA"]=np.sqrt(np.maximum(x["AREA_KM2"],0)/cfg.area_equivalent_block_km2)*cfg.area_equivalent_weight
    x["COMPONENTE_UE_DISPERSAO"]=x["DISPERSAO_P90_LOJAS_KM"]/cfg.dispersion_equivalent_block_km*cfg.dispersion_equivalent_weight
    x["COMPONENTE_UE_FIXO"]=cfg.unit_fixed_load
    non_store=x["COMPONENTE_UE_POPULACAO"]+x["COMPONENTE_UE_AREA"]+x["COMPONENTE_UE_DISPERSAO"]+x["COMPONENTE_UE_FIXO"]
    non_store=np.where(x["EH_MUNICIPIO_PEQUENO"].to_numpy(bool),non_store*cfg.small_non_store_load_factor,non_store)
    x["CARGA_EQUIVALENTE_BRUTA"]=x["COMPONENTE_UE_POPULACAO"]+x["COMPONENTE_UE_LOJAS"]+x["COMPONENTE_UE_AREA"]+x["COMPONENTE_UE_DISPERSAO"]+x["COMPONENTE_UE_FIXO"]
    x["CARGA_EQUIVALENTE"]=(non_store+x["COMPONENTE_UE_LOJAS"].to_numpy(float)).clip(cfg.minimum_unit_equivalent,cfg.maximum_unit_equivalent)
    mean=max(float(x["POPULACAO_UNIDADE"].mean()),1); x["PESO_ATRACAO_POPULACIONAL"]=((x["POPULACAO_UNIDADE"]/mean)*x["FATOR_ATRACAO_FAIXA"]).clip(lower=.005); x["UNIDADE_FISICA"]=1
    return x.reset_index(drop=True)


# =============================================================================
# CANDIDATOS / CUSTO / TOPOLOGIA
# =============================================================================

def calculate_site_penalty(population: pd.Series, cfg: ModelConfig) -> np.ndarray:
    pop=population.to_numpy(float); pmin=float(cfg.candidate_parent_population_min); pfull=float(cfg.site_population_full_attraction); den=max(np.log(pfull)-np.log(pmin),1e-9)
    x=np.clip((np.log(np.maximum(pop,pmin))-np.log(pmin))/den,0,1); return cfg.site_population_penalty_max_km*(1-x)**2


def build_candidate_sites(demand: pd.DataFrame, cfg: ModelConfig) -> pd.DataFrame:
    e=demand[demand["POPULACAO_MUNICIPIO"]>=cfg.candidate_parent_population_min].copy()
    c=e[["DEMAND_ID","DEMAND_IDX","TIPO_UNIDADE","COD_IBGE","CD_DIST","NM_MUN","NM_DIST","COD_UF","UF","LATITUDE","LONGITUDE","POPULACAO_UNIDADE","POPULACAO_MUNICIPIO"]].rename(columns={"DEMAND_ID":"DEMAND_ID_ORIGEM_POLO","DEMAND_IDX":"DEMAND_IDX_ORIGEM_POLO","TIPO_UNIDADE":"TIPO_CANDIDATO","POPULACAO_MUNICIPIO":"POPULACAO_SEDE_REFERENCIA"}).reset_index(drop=True)
    c["CANDIDATE_ID"]="POLO-"+c["DEMAND_ID_ORIGEM_POLO"].astype(str); c["CANDIDATE_IDX"]=np.arange(len(c),dtype=int); c["PENALIDADE_SEDE_KM_EQ"]=calculate_site_penalty(c["POPULACAO_SEDE_REFERENCIA"],cfg)
    c["DESC_GERENCIA_AREA_PROPOSTA"]=c["UF"].map(DESC_AREA_POR_UF)
    if c["DESC_GERENCIA_AREA_PROPOSTA"].isna().any(): raise RuntimeError("Candidato sem DESC_GERENCIA_AREA derivada da UF.")
    if c.empty: raise RuntimeError("Nenhum polo candidato.")
    return c


def match_regional_anchors(regional: pd.DataFrame, candidates: pd.DataFrame, cfg: ModelConfig) -> tuple[list[int],pd.DataFrame]:
    distance=pairwise_haversine_matrix(regional,candidates,"LATITUDE","LONGITUDE","LATITUDE","LONGITUDE")
    same_area=regional["DESC_GERENCIA_AREA_GR"].astype(str).to_numpy()[:,None]==candidates["DESC_GERENCIA_AREA_PROPOSTA"].astype(str).to_numpy()[None,:]
    feasible=same_area&(distance<=cfg.regional_anchor_radius_km)
    missing=np.flatnonzero(~feasible.any(axis=1))
    if len(missing):
        detail=[]
        for i in missing[:20]:
            same=np.flatnonzero(same_area[i]); nearest=float(distance[i,same].min()) if len(same) else np.nan
            detail.append({"COD_GER_REG":str(regional.iloc[i]["COD_GER_REG"]),"GER_REGIONAL":str(regional.iloc[i]["GER_REGIONAL"]),"MENOR_DISTANCIA_MESMA_AREA_KM":nearest})
        pd.DataFrame(detail).to_csv(cfg.output_dir/"auditoria_gr_inviavel.csv",index=False,encoding="utf-8-sig")
        raise RuntimeError(f"GRs sem candidato elegível em até {cfg.regional_anchor_radius_km:.0f} km: {json.dumps(detail,ensure_ascii=False)}")
    matching_cost=np.where(feasible,distance+candidates["PENALIDADE_SEDE_KM_EQ"].to_numpy(float)[None,:],float(PROHIBITED_SERVICE_COST))
    rows,cols=linear_sum_assignment(matching_cost)
    if len(rows)!=len(regional) or np.any(~feasible[rows,cols]):
        competition=[]
        for i in range(len(regional)):
            competition.append({"COD_GER_REG":str(regional.iloc[i]["COD_GER_REG"]),"QTD_CANDIDATOS_ELEGIVEIS":int(feasible[i].sum()),"MENOR_DISTANCIA_KM":float(distance[i,feasible[i]].min())})
        pd.DataFrame(competition).to_csv(cfg.output_dir/"auditoria_gr_conflito_pareamento.csv",index=False,encoding="utf-8-sig")
        raise RuntimeError(f"Pareamento 1:1 das 81 GRs inviável: {json.dumps(competition,ensure_ascii=False)}")
    order=np.argsort(rows); rows=rows[order]; cols=cols[order]; selected=[int(candidates.iloc[int(c)]["CANDIDATE_IDX"]) for c in cols]
    audit=regional.iloc[rows].reset_index(drop=True).copy(); chosen=candidates.iloc[cols].reset_index(drop=True)
    audit["CANDIDATE_IDX_ANCHOR"]=chosen["CANDIDATE_IDX"].to_numpy(int); audit["CANDIDATE_ID_ANCHOR"]=chosen["CANDIDATE_ID"].to_numpy(); audit["COD_IBGE_POLO_ANCHOR"]=chosen["COD_IBGE"].to_numpy(); audit["NM_MUN_POLO_ANCHOR"]=chosen["NM_MUN"].to_numpy(); audit["UF_POLO_ANCHOR"]=chosen["UF"].to_numpy(); audit["LATITUDE_POLO_ANCHOR"]=chosen["LATITUDE"].to_numpy(float); audit["LONGITUDE_POLO_ANCHOR"]=chosen["LONGITUDE"].to_numpy(float); audit["DISTANCIA_GR_POLO_KM"]=distance[rows,cols]; audit["DENTRO_RAIO_100KM"]=audit["DISTANCIA_GR_POLO_KM"]<=cfg.regional_anchor_radius_km; audit["STATUS_ANCORA"]="ANCORA_1_PARA_1_VALIDA"
    if len(set(selected))!=cfg.expected_regional_points: raise RuntimeError("Pareamento das GRs reutilizou candidato indevidamente.")
    return selected,audit


def build_service_cost_matrix(demand: pd.DataFrame,candidates: pd.DataFrame,distance: np.ndarray,cfg: ModelConfig) -> np.ndarray:
    cost=distance.astype(np.float32).copy(); cross=demand["COD_UF"].astype(str).to_numpy()[:,None]!=candidates["COD_UF"].astype(str).to_numpy()[None,:]; cost+=cross.astype(np.float32)*np.float32(cfg.cross_uf_equivalent_km_penalty)
    radii=pd.to_numeric(demand["RAIO_REFERENCIA_KM"],errors="coerce").to_numpy(float); valid=np.isfinite(radii)&(radii>0)
    if valid.any():
        excess=np.maximum(distance[valid].astype(float)-radii[valid,None],0); penalty=(excess**2)/np.maximum(radii[valid,None],1); cost[valid]+=(penalty*cfg.service_radius_penalty_weight).astype(np.float32)
    return cost


def build_hybrid_unit_geometry(demand: pd.DataFrame, municipal_geo: gpd.GeoDataFrame, district_geo: Optional[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    mi=demand[demand["TIPO_UNIDADE"]=="MUNICIPIO"][["DEMAND_ID","DEMAND_IDX","TIPO_UNIDADE","COD_IBGE"]]; mg=municipal_geo[["CD_MUN","geometry"]].rename(columns={"CD_MUN":"COD_IBGE"}); parts=[gpd.GeoDataFrame(mi.merge(mg,on="COD_IBGE",how="left"),geometry="geometry",crs=municipal_geo.crs)]
    di=demand[demand["TIPO_UNIDADE"]=="DISTRITO"][["DEMAND_ID","DEMAND_IDX","TIPO_UNIDADE","COD_IBGE","CD_DIST"]]
    if not di.empty:
        if district_geo is None: raise ValueError("Distritos sem geometria.")
        dg=district_geo[["CD_DIST","geometry"]].copy(); dg=dg.to_crs(municipal_geo.crs) if dg.crs.to_epsg()!=municipal_geo.crs.to_epsg() else dg; parts.append(gpd.GeoDataFrame(di.merge(dg,on="CD_DIST",how="left"),geometry="geometry",crs=municipal_geo.crs))
    h=gpd.GeoDataFrame(pd.concat(parts,ignore_index=True),geometry="geometry",crs=municipal_geo.crs); h=h[h.geometry.notna() & ~h.geometry.is_empty].copy(); return h.to_crs(epsg=4326) if h.crs.to_epsg()!=4326 else h


def validate_demand_exclusivity(demand: pd.DataFrame, split: set[str]) -> None:
    if demand["DEMAND_ID"].duplicated().any() or demand["DEMAND_IDX"].duplicated().any(): raise RuntimeError("Unidade territorial duplicada na demanda híbrida.")
    for code,g in demand.groupby("COD_IBGE"):
        if str(code) in split:
            if not g["TIPO_UNIDADE"].eq("DISTRITO").all() or g["CD_DIST"].isna().any(): raise RuntimeError(f"Metrópole {code} contém município agregado junto dos distritos.")
            if g["CD_DIST"].duplicated().any(): raise RuntimeError(f"Metrópole {code} contém distrito duplicado.")
        elif len(g)!=1 or not g["TIPO_UNIDADE"].eq("MUNICIPIO").all(): raise RuntimeError(f"Município comum {code} possui mais de uma unidade territorial.")


def build_territorial_geometry_audit(unit_geo: gpd.GeoDataFrame,municipal_geo: gpd.GeoDataFrame,demand: pd.DataFrame,cfg: ModelConfig) -> pd.DataFrame:
    metric=unit_geo.to_crs(epsg=5880).reset_index(drop=True); sidx=metric.sindex; rows=[]; violations=[]
    for i,r in metric.iterrows():
        gi=r.geometry; area_i=float(gi.area)/1_000_000
        for j in sidx.intersection(gi.bounds):
            if j<=i: continue
            other=metric.iloc[j]
            try: overlap=float(gi.intersection(other.geometry).area)/1_000_000
            except Exception: overlap=0.0
            if overlap<=0: continue
            area_j=float(other.geometry.area)/1_000_000; tolerance=max(cfg.overlap_absolute_tolerance_km2,cfg.overlap_relative_tolerance*min(area_i,area_j)); status="VIOLACAO_SOBREPOSICAO" if overlap>tolerance else "TOLERANCIA_NUMERICA"
            record={"TIPO_AUDITORIA":"SOBREPOSICAO_UNIDADES","DEMAND_ID_1":r.DEMAND_ID,"DEMAND_ID_2":other.DEMAND_ID,"COD_IBGE":pd.NA,"AREA_SOBREPOSTA_KM2":overlap,"TOLERANCIA_KM2":tolerance,"STATUS":status}; rows.append(record)
            if status=="VIOLACAO_SOBREPOSICAO": violations.append(record)
    muni=municipal_geo[["CD_MUN","geometry"]].to_crs(epsg=5880).set_index("CD_MUN")
    district_rows=metric[metric["TIPO_UNIDADE"].eq("DISTRITO")]
    for code,g in district_rows.groupby("COD_IBGE"):
        if str(code) not in muni.index: continue
        parent=muni.loc[str(code),"geometry"]; union=g.geometry.union_all() if hasattr(g.geometry,"union_all") else g.geometry.unary_union; parent_area=max(float(parent.area)/1_000_000,1e-9); gap=float(parent.difference(union).area)/1_000_000; outside=float(union.difference(parent).area)/1_000_000
        rows.append({"TIPO_AUDITORIA":"COBERTURA_DISTRITOS_METROPOLE","DEMAND_ID_1":pd.NA,"DEMAND_ID_2":pd.NA,"COD_IBGE":str(code),"AREA_MUNICIPIO_KM2":parent_area,"LACUNA_DISTRITAL_KM2":gap,"AREA_DISTRITAL_FORA_MUNICIPIO_KM2":outside,"PERC_COBERTURA_DISTRITAL":max(0.0,1.0-gap/parent_area),"STATUS":"AUDITADO"})
    if violations:
        sample=[{k:v for k,v in r.items() if k in {"DEMAND_ID_1","DEMAND_ID_2","AREA_SOBREPOSTA_KM2","TOLERANCIA_KM2"}} for r in violations[:20]]
        raise RuntimeError(f"Sobreposição territorial acima da tolerância: {json.dumps(sample,ensure_ascii=False)}")
    return pd.DataFrame(rows)


def build_adjacency_graph(geo: gpd.GeoDataFrame, demand_count: int, cfg: ModelConfig) -> dict[int,set[int]]:
    neigh={i:set() for i in range(demand_count)}
    if geo.empty: return neigh
    m=geo.to_crs(epsg=5880).reset_index(drop=True); sidx=m.sindex
    for i,r in m.iterrows():
        gi=r.geometry; ui=int(r.DEMAND_IDX)
        search_bounds=gi.buffer(cfg.max_topology_gap_m).bounds
        for j in sidx.intersection(search_bounds):
            if j<=i: continue
            gj=m.iloc[j].geometry; uj=int(m.iloc[j].DEMAND_IDX)
            try: shared=gi.boundary.intersection(gj.boundary).length
            except Exception: shared=0.0
            try: gap=gi.boundary.distance(gj.boundary)
            except Exception: gap=np.inf
            involves_district=str(r.TIPO_UNIDADE)=="DISTRITO" or str(m.iloc[j].TIPO_UNIDADE)=="DISTRITO"
            if shared>=cfg.min_shared_boundary_m or (involves_district and gap<=cfg.max_topology_gap_m): neigh[ui].add(uj); neigh[uj].add(ui)
    logging.info("Grafo territorial: %s arestas",sum(len(v) for v in neigh.values())//2); return neigh


def graph_components(neighbors: dict[int,set[int]], demand_count: int) -> list[set[int]]:
    return components(set(range(demand_count)),neighbors)


def apply_cross_uf_frontier_constraint(
    cost: np.ndarray, demand: pd.DataFrame, candidates: pd.DataFrame,
    neighbors: dict[int,set[int]],
) -> np.ndarray:
    constrained=cost.copy(); unit_uf=demand["UF"].astype(str).to_numpy(); candidate_uf=candidates["UF"].astype(str).to_numpy(); blocked=0
    for unit in range(len(demand)):
        allowed={unit_uf[unit]}; allowed.update(unit_uf[v] for v in neighbors.get(unit,set()))
        invalid=~np.isin(candidate_uf,list(allowed)); blocked+=int(invalid.sum()); constrained[unit,invalid]=PROHIBITED_SERVICE_COST
    logging.info("Restrição de fronteira UF: %s combinações distantes bloqueadas",blocked)
    return constrained


def ensure_strategic_component_seeds(
    preselected: list[int], candidates: pd.DataFrame, neighbors: dict[int,set[int]],
    strategic: np.ndarray, n: int,
) -> tuple[list[int],set[int]]:
    selected=list(dict.fromkeys(int(c) for c in preselected)); locked=set(selected)
    origin=candidates["DEMAND_IDX_ORIGEM_POLO"].to_numpy(int)
    for comp in graph_components(neighbors,len(strategic)):
        units=np.fromiter(comp,dtype=int)
        if not len(units) or not strategic[units].any(): continue
        component_candidates=candidates[candidates["DEMAND_IDX_ORIGEM_POLO"].isin(comp)]
        strategic_ufs=set(component_candidates["UF"].astype(str))
        for uf in strategic_ufs:
            if any(int(origin[c]) in comp and str(candidates.iloc[c]["UF"])==uf for c in selected): continue
            pool=candidates[candidates["DEMAND_IDX_ORIGEM_POLO"].isin(comp)&candidates["UF"].astype(str).eq(uf)].copy()
            if pool.empty:
                raise RuntimeError(f"Componente territorial estratégico da UF {uf} sem município candidato a polo.")
            pool=pool.sort_values(["POPULACAO_SEDE_REFERENCIA","PENALIDADE_SEDE_KM_EQ"],ascending=[False,True])
            chosen=int(pool.iloc[0]["CANDIDATE_IDX"]); selected.append(chosen); locked.add(chosen)
            if len(selected)>n:
                raise RuntimeError(f"São necessários mais de {n} polos para cobrir todos os componentes territoriais estratégicos.")
    return selected,locked


def assign_contiguous_regions(
    cost: np.ndarray, selected: list[int], candidates: pd.DataFrame,
    neighbors: dict[int,set[int]], loads: np.ndarray, strategic: np.ndarray,
    cfg: ModelConfig,
) -> np.ndarray:
    position=np.full(cost.shape[0],-1,dtype=int); cluster_load=np.zeros(len(selected),dtype=float)
    target=max(float(loads.sum())/max(len(selected),1),1e-9); heap=[]

    def offer(unit: int, cluster: int) -> None:
        if position[unit]>=0: return
        if cost[unit,selected[cluster]]>=PROHIBITED_SERVICE_COST: return
        pressure=max((cluster_load[cluster]+loads[unit])/target-1.0,0.0)
        score=float(cost[unit,selected[cluster]])+cfg.contiguous_growth_load_penalty_km*pressure
        heapq.heappush(heap,(score,unit,cluster))

    for cluster,cidx in enumerate(selected):
        unit=int(candidates.iloc[int(cidx)]["DEMAND_IDX_ORIGEM_POLO"])
        if position[unit]>=0: raise RuntimeError("Dois polos selecionados para a mesma unidade territorial.")
        position[unit]=cluster; cluster_load[cluster]+=loads[unit]
    for unit in np.flatnonzero(position>=0):
        cluster=int(position[unit])
        for v in neighbors.get(int(unit),set()): offer(int(v),cluster)

    while heap:
        _,unit,cluster=heapq.heappop(heap)
        if position[unit]>=0: continue
        if not any(int(position[v])==cluster for v in neighbors.get(unit,set())): continue
        position[unit]=cluster; cluster_load[cluster]+=loads[unit]
        for v in neighbors.get(unit,set()): offer(int(v),cluster)

    missing_strategic=np.flatnonzero((position<0)&strategic)
    if len(missing_strategic):
        raise RuntimeError(f"{len(missing_strategic)} unidades estratégicas ficaram fora de componentes com polo.")
    missing_small=int((position<0).sum())
    if missing_small and not cfg.allow_unserved_small_components:
        raise RuntimeError(f"{missing_small} municípios pequenos isolados ficaram sem atendimento.")
    logging.info("Crescimento territorial contíguo: %s atendidas | %s pequenos isolados sem atendimento",int((position>=0).sum()),missing_small)
    return position


def territorial_objective(position: np.ndarray,selected: list[int],cost: np.ndarray,weights: np.ndarray,loads: np.ndarray,opening: np.ndarray,cfg: ModelConfig) -> float:
    served=position>=0; assigned=np.asarray(selected,int)[position[served]]; service=float(np.sum(cost[np.flatnonzero(served),assigned]*weights[served])); opening_cost=float(opening[np.asarray(selected,int)].sum())
    cluster_load=np.zeros(len(selected)); np.add.at(cluster_load,position[served],loads[served]); target=max(float(loads[served].sum())/len(selected),1e-9); imbalance=float(np.abs(cluster_load-target).sum()/target)
    return service+opening_cost+cfg.balance_improvement_equivalent_km*imbalance


def refine_selected_contiguous_v3(
    cost: np.ndarray,weights: np.ndarray,loads: np.ndarray,candidates: pd.DataFrame,
    selected: list[int],locked_candidates: set[int],neighbors: dict[int,set[int]],
    strategic: np.ndarray,opening: np.ndarray,cfg: ModelConfig,
) -> tuple[list[int],np.ndarray]:
    current=list(selected); position=assign_contiguous_regions(cost,current,candidates,neighbors,loads,strategic,cfg); objective=territorial_objective(position,current,cost,weights,loads,opening,cfg)
    demand_to_candidate={int(r.DEMAND_IDX_ORIGEM_POLO):int(r.CANDIDATE_IDX) for r in candidates.itertuples(index=False)}
    for iteration in range(cfg.refine_iterations):
        improved=False
        for cluster in range(len(current)):
            if int(current[cluster]) in locked_candidates: continue
            members=np.flatnonzero(position==cluster); used={int(c) for i,c in enumerate(current) if i!=cluster}; pool=[demand_to_candidate[int(u)] for u in members if int(u) in demand_to_candidate and demand_to_candidate[int(u)] not in used]
            if not pool: continue
            local=(cost[np.ix_(members,np.asarray(pool,int))]*weights[members,None]).sum(axis=0,dtype=np.float64)+opening[np.asarray(pool,int)]; proposal=int(pool[int(np.argmin(local))])
            if proposal==current[cluster]: continue
            trial=list(current); trial[cluster]=proposal
            try: trial_position=assign_contiguous_regions(cost,trial,candidates,neighbors,loads,strategic,cfg)
            except RuntimeError: continue
            trial_objective=territorial_objective(trial_position,trial,cost,weights,loads,opening,cfg)
            if trial_objective+1e-6<objective:
                current=trial; position=trial_position; objective=trial_objective; improved=True; break
        logging.info("Refinamento territorial %s | objetivo %.4f | melhorou=%s",iteration+1,objective,improved)
        if not improved: break
    return current,position

# =============================================================================
# LOCATION-ALLOCATION / METRÓPOLES
# =============================================================================

def calculate_metropolitan_requirements(demand: pd.DataFrame,candidates: pd.DataFrame,n: int,cfg: ModelConfig) -> pd.DataFrame:
    target=float(demand["CARGA_EQUIVALENTE"].sum())/n; metro=demand[demand["POPULACAO_MUNICIPIO"]>=cfg.large_city_threshold]
    if metro.empty: return pd.DataFrame(columns=["COD_IBGE","CARGA_CIDADE","POPULACAO_CIDADE","QTD_UNIDADES_CIDADE","POLOS_DESEJADOS","QTD_CANDIDATOS"])
    city=metro.groupby("COD_IBGE",as_index=False).agg(CARGA_CIDADE=("CARGA_EQUIVALENTE","sum"),POPULACAO_CIDADE=("POPULACAO_MUNICIPIO","max"),QTD_UNIDADES_CIDADE=("DEMAND_ID","nunique")); counts=candidates.groupby("COD_IBGE").size().rename("QTD_CANDIDATOS").reset_index(); city=city.merge(counts,on="COD_IBGE",how="left"); city["QTD_CANDIDATOS"]=city["QTD_CANDIDATOS"].fillna(0).astype(int)
    minimum=max(int(cfg.minimum_metropolitan_units_per_manager),1)
    invalid=city[city["QTD_UNIDADES_CIDADE"]<minimum]
    if not invalid.empty:
        codes=", ".join(invalid["COD_IBGE"].astype(str).tolist()[:10])
        raise RuntimeError(f"Metrópoles sem ao menos {minimum} unidades territoriais: {codes}")
    city["POLOS_DESEJADOS"]=np.ceil(city["CARGA_CIDADE"]/max(target*cfg.metropolitan_load_factor,1e-9)).astype(int).clip(lower=1); city["POLOS_DESEJADOS"]=np.minimum(city["POLOS_DESEJADOS"],city["QTD_CANDIDATOS"])
    max_by_units=(city["QTD_UNIDADES_CIDADE"]//minimum).clip(lower=1)
    city["POLOS_DESEJADOS"]=np.minimum(city["POLOS_DESEJADOS"],max_by_units)
    return city.sort_values(["CARGA_CIDADE","POPULACAO_CIDADE"],ascending=False).reset_index(drop=True)


def _best_local_candidate(city_units: np.ndarray,cand_idx: np.ndarray,selected: list[int],cost: np.ndarray,weights: np.ndarray,opening: np.ndarray) -> int:
    current=np.min(cost[np.ix_(city_units,np.asarray(selected,int))],axis=1) if selected else np.full(len(city_units),np.inf); best=None
    for c in cand_idx:
        c=int(c)
        if c in selected: continue
        score=float(np.sum(np.minimum(current,cost[city_units,c])*weights[city_units])+opening[c])
        if best is None or score<best[0]: best=(score,c)
    if best is None: raise RuntimeError("Sem candidato local disponível.")
    return best[1]


def select_metropolitan_seeds(demand: pd.DataFrame,candidates: pd.DataFrame,cost: np.ndarray,weights: np.ndarray,opening: np.ndarray,req: pd.DataFrame,n: int,cfg: ModelConfig,preselected: Optional[list[int]]=None) -> list[int]:
    selected=list(dict.fromkeys(int(c) for c in (preselected or [])))
    if not cfg.enable_metropolitan_capacity or req.empty: return selected
    metro_codes=set(req["COD_IBGE"].astype(str)); allocated=defaultdict(int)
    for c in selected:
        code=str(candidates.iloc[int(c)]["COD_IBGE"])
        if code in metro_codes: allocated[code]+=1
    for r in req.itertuples(index=False):
        maximum=int(r.QTD_UNIDADES_CIDADE)//max(int(cfg.minimum_metropolitan_units_per_manager),1)
        if allocated[str(r.COD_IBGE)]>maximum: raise RuntimeError(f"As âncoras GR exigem {allocated[str(r.COD_IBGE)]} polos na metrópole {r.COD_IBGE}, mas há distritos para no máximo {maximum}.")
    units={str(k):g["DEMAND_IDX"].to_numpy(int) for k,g in demand.groupby("COD_IBGE")}; cands={str(k):g["CANDIDATE_IDX"].to_numpy(int) for k,g in candidates.groupby("COD_IBGE")}
    for r in req.itertuples(index=False):
        if len(selected)>=n: break
        code=str(r.COD_IBGE)
        if allocated[code]>=1: continue
        if code not in units or code not in cands or not len(cands[code]): continue
        chosen=_best_local_candidate(units[code],cands[code],selected,cost,weights,opening); selected.append(chosen); allocated[code]+=1
    while len(selected)<n:
        p=req.copy(); p["ALOCADOS"]=p["COD_IBGE"].astype(str).map(allocated).fillna(0).astype(int); p=p[p["ALOCADOS"]<p["POLOS_DESEJADOS"]]
        if p.empty: break
        p["GAP"]=p["POLOS_DESEJADOS"]-p["ALOCADOS"]; p["PRESSAO"]=p["CARGA_CIDADE"]/np.maximum(p["ALOCADOS"],1); p=p.sort_values(["GAP","PRESSAO"],ascending=False); added=False
        for r in p.itertuples(index=False):
            code=str(r.COD_IBGE); available=np.array([x for x in cands.get(code,[]) if int(x) not in selected],dtype=int)
            if not len(available): continue
            chosen=_best_local_candidate(units[code],available,selected,cost,weights,opening); selected.append(chosen); allocated[code]+=1; added=True; break
        if not added: break
    shortage=[]
    for r in req.itertuples(index=False):
        code=str(r.COD_IBGE); missing=max(int(r.POLOS_DESEJADOS)-int(allocated[code]),0)
        if missing: shortage.append({"COD_IBGE":code,"POLOS_DESEJADOS":int(r.POLOS_DESEJADOS),"POLOS_PRESELECIONADOS":int(allocated[code]),"FALTANTES":missing})
    if shortage: raise RuntimeError(f"As exigências metropolitanas e as 81 âncoras não cabem em {n} polos: {json.dumps(shortage,ensure_ascii=False)}")
    logging.info("Sementes metropolitanas: %s",len(selected)); return selected


def greedy_p_median_v3(cost: np.ndarray,weights: np.ndarray,opening: np.ndarray,n: int,chunk: int,preselected: Optional[list[int]]=None) -> list[int]:
    nc=cost.shape[1]
    if n>nc: raise ValueError("Mais gerências que candidatos.")
    selected=list(dict.fromkeys(preselected or []))[:n]; available=np.ones(nc,dtype=bool)
    for c in selected: available[c]=False
    best=np.min(cost[:,np.asarray(selected,int)],axis=1).astype(np.float32) if selected else np.full(cost.shape[0],np.inf,dtype=np.float32)
    for step in range(len(selected),n):
        scores=np.full(nc,np.inf,dtype=np.float64)
        for start in range(0,nc,chunk):
            stop=min(start+chunk,nc); block=np.minimum(cost[:,start:stop],best[:,None]); scores[start:stop]=(block*weights[:,None]).sum(axis=0,dtype=np.float64)+opening[start:stop]
        scores[~available]=np.inf; chosen=int(np.argmin(scores))
        if not np.isfinite(scores[chosen]): raise RuntimeError("Falha ao selecionar polo.")
        selected.append(chosen); available[chosen]=False; best=np.minimum(best,cost[:,chosen])
        if (step+1)%25==0 or step+1==n: logging.info("Greedy %s/%s",step+1,n)
    return selected


def force_poles(position: np.ndarray,selected: list[int],candidates: pd.DataFrame) -> np.ndarray:
    x=position.copy()
    for cluster,c in enumerate(selected): x[int(candidates.iloc[int(c)]["DEMAND_IDX_ORIGEM_POLO"])]=cluster
    return x


# =============================================================================
# BALANCEAMENTO E CONTINUIDADE
# =============================================================================

def components(nodes: set[int],neighbors: dict[int,set[int]]) -> list[set[int]]:
    out=[]; left=set(nodes)
    while left:
        s=next(iter(left)); left.remove(s); q=deque([s]); comp={s}
        while q:
            u=q.popleft()
            for v in neighbors.get(u,set()):
                if v in left: left.remove(v); comp.add(v); q.append(v)
        out.append(comp)
    return out


def removal_preserves_connectivity(unit: int,origin: int,position: np.ndarray,neighbors: dict[int,set[int]],strategic: np.ndarray) -> bool:
    nodes={int(i) for i in np.flatnonzero(position==origin) if strategic[int(i)] and int(i)!=unit}; return len(nodes)<=1 or len(components(nodes,neighbors))==1


def removal_preserves_cluster_connectivity(unit: int,origin: int,position: np.ndarray,neighbors: dict[int,set[int]]) -> bool:
    nodes={int(i) for i in np.flatnonzero(position==origin) if int(i)!=unit}; return len(nodes)<=1 or len(components(nodes,neighbors))==1


def receiver_clusters(unit: int,origin: int,position: np.ndarray,neighbors: dict[int,set[int]]) -> set[int]:
    return {int(position[v]) for v in neighbors.get(unit,set()) if int(position[v])!=origin}


def transfer_score(unit: int,origin: int,receiver: int,cost: np.ndarray,selected: list[int],weights: np.ndarray,loads: np.ndarray,cluster_load: np.ndarray,target: float,cfg: ModelConfig) -> tuple[float,float,float]:
    delta=(float(cost[unit,selected[receiver]])-float(cost[unit,selected[origin]]))*float(weights[unit]); before=abs(cluster_load[origin]-target)+abs(cluster_load[receiver]-target); after=abs(cluster_load[origin]-loads[unit]-target)+abs(cluster_load[receiver]+loads[unit]-target); improve=(before-after)/max(target,1e-9); return delta-cfg.balance_improvement_equivalent_km*improve,delta,improve


def balance_load_v3(cost: np.ndarray,distance: np.ndarray,loads: np.ndarray,selected: list[int],position: np.ndarray,neighbors: dict[int,set[int]],strategic: np.ndarray,fixed: set[int],weights: np.ndarray,cfg: ModelConfig) -> tuple[np.ndarray,list[dict[str,Any]]]:
    n=len(selected); served=position>=0; target=float(loads[served].sum())/n; lower=target*cfg.minimum_load_factor; upper=target*cfg.maximum_load_factor; x=position.copy(); cl=np.zeros(n); np.add.at(cl,x[served],loads[served]); moves=[]; moved=defaultdict(int)
    # Fase 1: descarrega acima do teto.
    for _ in range(cfg.balancing_max_iterations):
        over=[c for c in range(n) if cl[c]>upper]
        if not over: break
        origin=max(over,key=lambda c:cl[c]); best=None
        for unit in [int(u) for u in np.flatnonzero(x==origin) if int(u) not in fixed and moved[int(u)]<2]:
            is_strat=bool(strategic[unit]); rec=list(receiver_clusters(unit,origin,x,neighbors))
            if not rec or not removal_preserves_cluster_connectivity(unit,origin,x,neighbors): continue
            for r in rec:
                if r==origin or cl[r]+loads[unit]>upper*cfg.capacity_soft_tolerance: continue
                if cost[unit,selected[r]]>=PROHIBITED_SERVICE_COST: continue
                if is_strat and distance[unit,selected[r]]>distance[unit,selected[origin]]+cfg.relevant_max_extra_km: continue
                score,delta,improve=transfer_score(unit,origin,r,cost,selected,weights,loads,cl,target,cfg); score-=30 if cl[r]<lower else (15 if cl[r]<target else 0)
                cand=(score,unit,r,delta,improve)
                if best is None or cand[0]<best[0]: best=cand
        if best is None: break
        _,u,r,delta,improve=best; o=int(x[u]); x[u]=r; cl[o]-=loads[u]; cl[r]+=loads[u]; moved[u]+=1; moves.append({"DEMAND_IDX":u,"ORIGEM_CLUSTER":o,"DESTINO_CLUSTER":r,"MOTIVO":"BALANCEAMENTO_SAIDA_SOBRECARGA","DELTA_CUSTO_PONDERADO":delta,"MELHORA_CARGA_NORMALIZADA":improve})
    # Fase 2: tenta completar abaixo do piso.
    for _ in range(cfg.balancing_max_iterations):
        under=[c for c in range(n) if cl[c]<lower]
        if not under: break
        receiver=min(under,key=lambda c:cl[c]); best=None
        for origin in sorted([c for c in range(n) if c!=receiver and cl[c]>lower],key=lambda c:cl[c],reverse=True):
            for unit in [int(u) for u in np.flatnonzero(x==origin) if int(u) not in fixed and moved[int(u)]<2]:
                if cl[origin]-loads[unit]<lower or cl[receiver]+loads[unit]>upper*cfg.capacity_soft_tolerance: continue
                is_strat=bool(strategic[unit])
                if receiver not in receiver_clusters(unit,origin,x,neighbors): continue
                if not removal_preserves_cluster_connectivity(unit,origin,x,neighbors): continue
                if cost[unit,selected[receiver]]>=PROHIBITED_SERVICE_COST: continue
                if is_strat and distance[unit,selected[receiver]]>distance[unit,selected[origin]]+cfg.relevant_max_extra_km: continue
                score,delta,improve=transfer_score(unit,origin,receiver,cost,selected,weights,loads,cl,target,cfg); cand=(score,unit,origin,delta,improve)
                if best is None or cand[0]<best[0]: best=cand
        if best is None: break
        _,u,o,delta,improve=best; x[u]=receiver; cl[o]-=loads[u]; cl[receiver]+=loads[u]; moved[u]+=1; moves.append({"DEMAND_IDX":u,"ORIGEM_CLUSTER":o,"DESTINO_CLUSTER":receiver,"MOTIVO":"BALANCEAMENTO_ENTRADA_SUBCARGA","DELTA_CUSTO_PONDERADO":delta,"MELHORA_CARGA_NORMALIZADA":improve})
    logging.info("Balanceamento: %s movimentos | faixa final %.2f–%.2f",len(moves),cl.min(),cl.max()); return x,moves


def repair_critical_islands(position: np.ndarray,selected: list[int],cost: np.ndarray,loads: np.ndarray,weights: np.ndarray,strategic: np.ndarray,neighbors: dict[int,set[int]],fixed: set[int],cfg: ModelConfig) -> tuple[np.ndarray,list[dict[str,Any]]]:
    if not cfg.repair_critical_islands: return position.copy(),[]
    x=position.copy(); n=len(selected); served=position>=0; target=float(loads[served].sum())/n; upper=target*cfg.maximum_load_factor; cl=np.zeros(n); np.add.at(cl,x[served],loads[served]); repairs=[]
    for cluster in range(n):
        nodes={int(u) for u in np.flatnonzero(x==cluster) if strategic[int(u)]}
        if len(nodes)<=1: continue
        comps=components(nodes,neighbors)
        if len(comps)<=1: continue
        fixed_here=fixed & nodes; main=next((c for c in comps if c & fixed_here),max(comps,key=len)) if fixed_here else max(comps,key=len)
        for comp in sorted([c for c in comps if c!=main],key=len):
            rec=set()
            for u in comp: rec|=receiver_clusters(u,cluster,x,neighbors)
            rec.discard(cluster)
            if not rec: continue
            units=np.array(sorted(comp),dtype=int); load=float(loads[units].sum()); best=None
            for r in rec:
                if cl[r]+load>upper*cfg.island_repair_capacity_tolerance: continue
                if np.any(cost[units,selected[r]]>=PROHIBITED_SERVICE_COST): continue
                delta=float(np.sum((cost[units,selected[r]]-cost[units,selected[cluster]])*weights[units])); per=delta/max(float(weights[units].sum()),1e-9)
                if per>cfg.island_repair_max_extra_weighted_cost: continue
                cand=(delta,r,per)
                if best is None or cand[0]<best[0]: best=cand
            if best is None: continue
            _,r,per=best
            for u in units: x[u]=r; repairs.append({"DEMAND_IDX":int(u),"ORIGEM_CLUSTER":cluster,"DESTINO_CLUSTER":r,"MOTIVO":"REPARO_ILHA_CRITICA","DELTA_CUSTO_PONDERADO":per,"MELHORA_CARGA_NORMALIZADA":np.nan})
            cl[cluster]-=load; cl[r]+=load
    logging.info("Reparo de ilhas: %s unidades",len(repairs)); return x,repairs


def ensure_metropolitan_minimum_units(
    position: np.ndarray, selected: list[int], candidates: pd.DataFrame, demand: pd.DataFrame,
    cost: np.ndarray, loads: np.ndarray, weights: np.ndarray, neighbors: dict[int,set[int]],
    fixed: set[int], cfg: ModelConfig,
) -> tuple[np.ndarray,list[dict[str,Any]]]:
    minimum=max(int(cfg.minimum_metropolitan_units_per_manager),1); x=position.copy(); moves=[]
    served=x>=0; target=float(loads[served].sum())/len(selected); upper=target*cfg.maximum_load_factor*cfg.capacity_soft_tolerance
    cluster_load=np.zeros(len(selected)); np.add.at(cluster_load,x[served],loads[served])
    metro_clusters=defaultdict(list)
    for cluster,cidx in enumerate(selected):
        c=candidates.iloc[int(cidx)]
        if float(c.POPULACAO_SEDE_REFERENCIA)>=cfg.large_city_threshold: metro_clusters[str(c.COD_IBGE)].append(cluster)

    for code,clusters_here in metro_clusters.items():
        city_units=demand.index[(demand["COD_IBGE"].astype(str)==code)&demand["TIPO_UNIDADE"].eq("DISTRITO")].to_numpy(int)
        for receiver in clusters_here:
            while int(np.sum(x[city_units]==receiver))<minimum:
                counts={c:int(np.sum(x[city_units]==c)) for c in clusters_here}; best=None
                for unit in city_units:
                    unit=int(unit); origin=int(x[unit])
                    if origin<0 or origin==receiver or unit in fixed: continue
                    if origin in counts and counts[origin]<=minimum: continue
                    if receiver not in receiver_clusters(unit,origin,x,neighbors): continue
                    if not removal_preserves_cluster_connectivity(unit,origin,x,neighbors): continue
                    if cost[unit,selected[receiver]]>=PROHIBITED_SERVICE_COST: continue
                    if cluster_load[receiver]+loads[unit]>upper: continue
                    delta=(float(cost[unit,selected[receiver]])-float(cost[unit,selected[origin]]))*float(weights[unit])
                    cand=(delta,unit,origin)
                    if best is None or cand[0]<best[0]: best=cand
                if best is None:
                    raise RuntimeError(f"Não foi possível garantir {minimum} distritos contíguos para um polo da metrópole {code}.")
                delta,unit,origin=best; x[unit]=receiver; cluster_load[origin]-=loads[unit]; cluster_load[receiver]+=loads[unit]
                moves.append({"DEMAND_IDX":unit,"ORIGEM_CLUSTER":origin,"DESTINO_CLUSTER":receiver,"MOTIVO":"MINIMO_DISTRITOS_METROPOLE","DELTA_CUSTO_PONDERADO":delta,"MELHORA_CARGA_NORMALIZADA":np.nan})
    logging.info("Ajuste mínimo metropolitano: %s movimentos",len(moves)); return x,moves


def apply_small_unserved_policy(
    position: np.ndarray,selected: list[int],demand: pd.DataFrame,distance: np.ndarray,
    neighbors: dict[int,set[int]],fixed: set[int],cfg: ModelConfig,
) -> tuple[np.ndarray,list[dict[str,Any]]]:
    x=position.copy(); moves=[]; served=x>=0; assigned_distance=np.full(len(x),np.nan)
    assigned_distance[served]=distance[np.flatnonzero(served),np.asarray(selected,int)[x[served]]]
    eligible=np.flatnonzero(served&demand["EH_MUNICIPIO_PEQUENO"].to_numpy(bool)&(demand["QTD_LOJAS"].to_numpy(float)<=0)&(assigned_distance>cfg.small_unserved_distance_km))
    for unit in sorted((int(u) for u in eligible),key=lambda u:assigned_distance[u],reverse=True):
        origin=int(x[unit])
        if unit not in fixed and removal_preserves_cluster_connectivity(unit,origin,x,neighbors):
            x[unit]=-1; moves.append({"DEMAND_IDX":unit,"ORIGEM_CLUSTER":origin,"DESTINO_CLUSTER":-1,"MOTIVO":"PEQUENO_SEM_LOJA_ACIMA_150KM","DELTA_CUSTO_PONDERADO":np.nan,"MELHORA_CARGA_NORMALIZADA":np.nan})
        else:
            moves.append({"DEMAND_IDX":unit,"ORIGEM_CLUSTER":origin,"DESTINO_CLUSTER":origin,"MOTIVO":"CORREDOR_CONTIGUIDADE","DELTA_CUSTO_PONDERADO":np.nan,"MELHORA_CARGA_NORMALIZADA":np.nan})
    logging.info("Política de pequenos: %s não atendidos | %s corredores",sum(m["DESTINO_CLUSTER"]==-1 for m in moves),sum(m["MOTIVO"]=="CORREDOR_CONTIGUIDADE" for m in moves)); return x,moves


def validate_solution_constraints(
    demand: pd.DataFrame, candidates: pd.DataFrame, selected: list[int], position: np.ndarray,
    neighbors: dict[int,set[int]], n: int, cfg: ModelConfig,
) -> None:
    if len(selected)!=n: raise RuntimeError(f"O cenário selecionou {len(selected)} polos, mas exige exatamente {n}.")
    pole_population=candidates.iloc[np.asarray(selected,int)]["POPULACAO_SEDE_REFERENCIA"].to_numpy(float)
    if np.any(pole_population<cfg.candidate_parent_population_min):
        raise RuntimeError("Foi selecionado polo em município com menos de 30 mil habitantes.")
    strategic=demand["EH_UNIDADE_ESTRATEGICA"].to_numpy(bool)
    if np.any((position<0)&strategic): raise RuntimeError("Há unidade estratégica sem atendimento.")
    if np.any((position<0)&(demand["QTD_LOJAS"].to_numpy(float)>0)): raise RuntimeError("Há município com loja ativa sem atendimento.")
    for cluster in range(len(selected)):
        nodes={int(u) for u in np.flatnonzero(position==cluster)}
        if nodes and len(components(nodes,neighbors))!=1:
            raise RuntimeError(f"A carteira {manager_id(n,cluster)} não é territorialmente contígua.")
        pole_uf=str(candidates.iloc[int(selected[cluster])]["UF"])
        for unit in nodes:
            if str(demand.iloc[unit]["UF"])==pole_uf: continue
            if not any(str(demand.iloc[v]["UF"])==pole_uf for v in neighbors.get(unit,set())):
                raise RuntimeError(f"A carteira {manager_id(n,cluster)} cruza UF fora de uma fronteira direta.")
    minimum=max(int(cfg.minimum_metropolitan_units_per_manager),1)
    for cluster,cidx in enumerate(selected):
        c=candidates.iloc[int(cidx)]
        if float(c.POPULACAO_SEDE_REFERENCIA)<cfg.large_city_threshold: continue
        city_units=demand.index[(demand["COD_IBGE"].astype(str)==str(c.COD_IBGE))&demand["TIPO_UNIDADE"].eq("DISTRITO")].to_numpy(int)
        if int(np.sum(position[city_units]==cluster))<minimum:
            raise RuntimeError(f"O polo metropolitano {manager_id(n,cluster)} atende menos de {minimum} distritos da própria metrópole.")

# =============================================================================
# RESULTADO / DIAGNÓSTICOS
# =============================================================================

def manager_id(n: int,cluster: int) -> str: return f"G{n}_{cluster+1:03d}"


def build_assignments(demand: pd.DataFrame,candidates: pd.DataFrame,selected: list[int],position: np.ndarray,initial: np.ndarray,distance: np.ndarray,cost: np.ndarray,moves: list[dict[str,Any]],run_id: str,scenario_id: str) -> pd.DataFrame:
    sel=np.asarray(selected,int); served=position>=0; near_pos=np.argmin(distance[:,sel],axis=1); safe_pos=np.where(served,position,near_pos); assigned=sel[safe_pos]; near=sel[near_pos]; rows=np.arange(len(demand)); motive={int(m["DEMAND_IDX"]):str(m["MOTIVO"]) for m in moves}
    x=demand.copy(); x.insert(0,"RUN_ID",run_id); x.insert(1,"CENARIO_ID",scenario_id); x["ATENDIDA"]=served; x["GERENCIA_ID"]=[manager_id(len(sel),int(c)) if ok else pd.NA for c,ok in zip(safe_pos,served)]; x["CLUSTER_IDX"]=pd.array([int(c) if ok else pd.NA for c,ok in zip(safe_pos,served)],dtype="Int64"); x["CANDIDATE_IDX"]=pd.array([int(c) if ok else pd.NA for c,ok in zip(assigned,served)],dtype="Int64")
    assigned_fields=[]
    for target,source in (("CANDIDATE_ID","CANDIDATE_ID"),("COD_IBGE_POLO","COD_IBGE"),("CD_DIST_POLO","CD_DIST"),("NM_MUN_POLO","NM_MUN"),("NM_DIST_POLO","NM_DIST"),("UF_POLO","UF"),("LATITUDE_POLO","LATITUDE"),("LONGITUDE_POLO","LONGITUDE"),("POPULACAO_SEDE_REFERENCIA","POPULACAO_SEDE_REFERENCIA")):
        x[target]=candidates.iloc[assigned][source].to_numpy(); assigned_fields.append(target)
    x.loc[~served,assigned_fields]=pd.NA
    assigned_distance=distance[rows,assigned].astype(float); assigned_cost=cost[rows,assigned].astype(float); assigned_distance[~served]=np.nan; assigned_cost[~served]=np.nan
    x["DISTANCIA_KM"]=assigned_distance; x["DISTANCIA_EQUIVALENTE_KM"]=assigned_cost; x["CRUZA_UF"]=served & (x["COD_UF"].astype(str).to_numpy()!=candidates.iloc[assigned]["COD_UF"].astype(str).to_numpy())
    x["GERENCIA_MAIS_PROXIMA"]=[manager_id(len(sel),int(c)) for c in near_pos]; x["CANDIDATE_ID_MAIS_PROXIMO"]=candidates.iloc[near]["CANDIDATE_ID"].to_numpy(); x["DISTANCIA_MAIS_PROXIMO_KM"]=distance[rows,near].astype(float); x["DELTA_DISTANCIA_VS_MAIS_PROXIMO_KM"]=x["DISTANCIA_KM"]-x["DISTANCIA_MAIS_PROXIMO_KM"]
    x["EH_MAIS_PROXIMO"]=served & (position==near_pos); x["FORA_RAIO_REFERENCIA"]=served & x["RAIO_REFERENCIA_KM"].notna() & (x["DISTANCIA_KM"]>x["RAIO_REFERENCIA_KM"]); x["EXCESSO_RAIO_KM"]=np.where(x["FORA_RAIO_REFERENCIA"],x["DISTANCIA_KM"]-x["RAIO_REFERENCIA_KM"].fillna(0),0.0)
    move_reason=x["DEMAND_IDX"].map(motive); x["METODO_ATRIBUICAO"]=move_reason.fillna(pd.Series(np.where(position==initial,"CRESCIMENTO_TERRITORIAL_CONTIGUO","AJUSTE_OTIMIZACAO_CONTIGUO"),index=x.index)); x.loc[x["EH_MUNICIPIO_PEQUENO"] & served & ~x["EH_MAIS_PROXIMO"] & move_reason.ne("CORREDOR_CONTIGUIDADE"),"METODO_ATRIBUICAO"]="MUNICIPIO_PEQUENO_FRONTEIRA_CONTIGUA"; x.loc[~served,"METODO_ATRIBUICAO"]=np.where(move_reason[~served].eq("PEQUENO_SEM_LOJA_ACIMA_150KM"),"SEM_ATENDIMENTO_DISTANCIA_PEQUENO","SEM_ATENDIMENTO_COMPONENTE_PEQUENO_ISOLADO")
    x["MOTIVO_NAO_ATENDIMENTO"]=pd.NA; x.loc[~served,"MOTIVO_NAO_ATENDIMENTO"]=np.where(move_reason[~served].eq("PEQUENO_SEM_LOJA_ACIMA_150KM"),"PEQUENO_SEM_LOJA_ACIMA_150KM","COMPONENTE_TERRITORIAL_SEM_UNIDADE_ESTRATEGICA")
    x["EH_CORREDOR_CONTIGUIDADE"]=move_reason.eq("CORREDOR_CONTIGUIDADE")
    return x


def build_manager_summary(assignments: pd.DataFrame,candidates: pd.DataFrame,selected: list[int],run_id: str,scenario_id: str,cfg: ModelConfig) -> pd.DataFrame:
    g=assignments.groupby("GERENCIA_ID",as_index=False).agg(QTD_UNIDADES=("DEMAND_ID","nunique"),QTD_MUNICIPIOS=("COD_IBGE","nunique"),QTD_LOJAS=("QTD_LOJAS","sum"),POPULACAO_ATENDIDA=("POPULACAO_UNIDADE","sum"),CARGA_EQUIVALENTE_TOTAL=("CARGA_EQUIVALENTE","sum"),DISTANCIA_MEDIA_SIMPLES_KM=("DISTANCIA_KM","mean"),DISTANCIA_MAXIMA_KM=("DISTANCIA_KM","max"),QTD_CRUZA_UF=("CRUZA_UF","sum"),QTD_UNIDADES_PEQUENAS=("EH_MUNICIPIO_PEQUENO","sum"),QTD_FORA_RAIO=("FORA_RAIO_REFERENCIA","sum"))
    d=assignments.assign(EH_DIST=assignments["TIPO_UNIDADE"].eq("DISTRITO").astype(int)).groupby("GERENCIA_ID",as_index=False)["EH_DIST"].sum().rename(columns={"EH_DIST":"QTD_DISTRITOS"}); g=g.merge(d,on="GERENCIA_ID",how="left")
    wr=[]
    for mid,grp in assignments.groupby("GERENCIA_ID"):
        pop=grp["POPULACAO_UNIDADE"].clip(lower=1); wr.append({"GERENCIA_ID":mid,"DISTANCIA_MEDIA_PONDERADA_POP_KM":float(np.average(grp["DISTANCIA_KM"],weights=pop)),"DISTANCIA_P90_PONDERADA_POP_KM":weighted_percentile(grp["DISTANCIA_KM"],pop,.9)})
    g=g.merge(pd.DataFrame(wr),on="GERENCIA_ID",how="left"); target=float(assignments.loc[assignments["ATENDIDA"],"CARGA_EQUIVALENTE"].sum())/len(selected); g["INDICE_CARGA_EQUIVALENTE"]=g["CARGA_EQUIVALENTE_TOTAL"]/target
    poles=[]
    for cluster,cidx in enumerate(selected):
        c=candidates.iloc[int(cidx)]; poles.append({"GERENCIA_ID":manager_id(len(selected),cluster),"CANDIDATE_ID":c.CANDIDATE_ID,"TIPO_CANDIDATO":c.TIPO_CANDIDATO,"COD_IBGE_POLO":c.COD_IBGE,"CD_DIST_POLO":c.CD_DIST,"NM_MUN_POLO":c.NM_MUN,"NM_DIST_POLO":c.NM_DIST,"UF_POLO":c.UF,"DESC_GERENCIA_AREA_PROPOSTA":c.DESC_GERENCIA_AREA_PROPOSTA,"LATITUDE":float(c.LATITUDE),"LONGITUDE":float(c.LONGITUDE),"POPULACAO_SEDE_REFERENCIA":float(c.POPULACAO_SEDE_REFERENCIA),"PENALIDADE_SEDE_KM_EQ":float(c.PENALIDADE_SEDE_KM_EQ)})
    m=pd.DataFrame(poles).merge(g,on="GERENCIA_ID",how="left"); m.insert(0,"RUN_ID",run_id); m.insert(1,"CENARIO_ID",scenario_id); m["FAIXA_CARGA"]=np.select([m["INDICE_CARGA_EQUIVALENTE"]<cfg.minimum_load_factor,m["INDICE_CARGA_EQUIVALENTE"]>cfg.maximum_load_factor],["ABAIXO_PISO","ACIMA_TETO"],default="DENTRO_FAIXA"); return m


def attach_manager_regional_links(managers: pd.DataFrame,regional: pd.DataFrame,anchor_audit: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    anchors=anchor_audit.set_index("CANDIDATE_ID_ANCHOR",drop=False); rows=[]
    for manager in managers.itertuples(index=False):
        cid=str(manager.CANDIDATE_ID); direct=cid in anchors.index
        if direct:
            gr=anchors.loc[cid]
            if isinstance(gr,pd.DataFrame): gr=gr.iloc[0]
            dist=float(gr.DISTANCIA_GR_POLO_KM); link_type="ANCORA_OBRIGATORIA_1_PARA_1"
        else:
            pool=regional[regional["DESC_GERENCIA_AREA_GR"].astype(str)==str(manager.DESC_GERENCIA_AREA_PROPOSTA)].copy()
            if pool.empty: raise RuntimeError(f"Sem GR na área {manager.DESC_GERENCIA_AREA_PROPOSTA} para vincular o polo {cid}.")
            dist_arr=haversine_arrays(np.full(len(pool),float(manager.LATITUDE)),np.full(len(pool),float(manager.LONGITUDE)),pool["LATITUDE"].to_numpy(float),pool["LONGITUDE"].to_numpy(float)); gr=pool.iloc[int(np.argmin(dist_arr))]; dist=float(np.min(dist_arr)); link_type="REFORCO_GR_MAIS_PROXIMA_NA_AREA"
        rows.append({"RUN_ID":manager.RUN_ID,"CENARIO_ID":manager.CENARIO_ID,"GERENCIA_ID":manager.GERENCIA_ID,"CANDIDATE_ID":cid,"COD_GER_REG":gr.COD_GER_REG,"GER_REGIONAL":gr.GER_REGIONAL,"UF_GR":gr.UF_GR,"DESC_GERENCIA_AREA_GR":gr.DESC_GERENCIA_AREA_GR,"LATITUDE_GR":float(gr.LATITUDE),"LONGITUDE_GR":float(gr.LONGITUDE),"DISTANCIA_POLO_GR_KM":dist,"TIPO_VINCULO_GR":link_type,"EH_ANCORA_GR":direct})
    links=pd.DataFrame(rows); enriched=managers.merge(links.drop(columns=["RUN_ID","CENARIO_ID"]),on=["GERENCIA_ID","CANDIDATE_ID"],how="left")
    if len(enriched)!=len(managers) or enriched["COD_GER_REG"].isna().any(): raise RuntimeError("Falha ao vincular todos os polos propostos às GRs.")
    return enriched,links


def attach_proposed_hierarchy_to_assignments(assignments: pd.DataFrame,managers: pd.DataFrame) -> pd.DataFrame:
    cols=["GERENCIA_ID","DESC_GERENCIA_AREA_PROPOSTA","COD_GER_REG","GER_REGIONAL","TIPO_VINCULO_GR","EH_ANCORA_GR"]
    return assignments.drop(columns=[c for c in cols[1:] if c in assignments.columns]).merge(managers[cols],on="GERENCIA_ID",how="left")


def validate_assignment_output(assignments: pd.DataFrame,managers: pd.DataFrame,cfg: ModelConfig) -> None:
    if len(managers)!=cfg.current_manager_reference or managers["GERENCIA_ID"].nunique()!=cfg.current_manager_reference: raise RuntimeError("A saída não contém exatamente 135 gerências propostas únicas.")
    if assignments["DEMAND_ID"].duplicated().any(): raise RuntimeError("Uma unidade territorial foi emitida mais de uma vez.")
    common=assignments[assignments["TIPO_UNIDADE"].eq("MUNICIPIO")]
    if common["COD_IBGE"].duplicated().any(): raise RuntimeError("Município comum sobreposto entre carteiras.")
    districts=assignments[assignments["TIPO_UNIDADE"].eq("DISTRITO")]
    if districts["CD_DIST"].duplicated().any(): raise RuntimeError("Distrito metropolitano sobreposto entre carteiras.")
    if assignments.loc[assignments["ATENDIDA"],"GERENCIA_ID"].isna().any(): raise RuntimeError("Unidade marcada como atendida sem gerência proposta.")


def build_solution_constraint_audit(scenario: pd.DataFrame,managers: pd.DataFrame,assignments: pd.DataFrame,anchors: pd.DataFrame,cfg: ModelConfig) -> pd.DataFrame:
    summary=scenario.iloc[0]; checks=[
        ("QTD_GERENCIAS",int(managers["GERENCIA_ID"].nunique()),cfg.current_manager_reference,"IGUAL"),
        ("QTD_ANCORAS_GR",int(anchors["COD_GER_REG"].nunique()),cfg.expected_regional_points,"IGUAL"),
        ("MAIOR_DISTANCIA_ANCORA_KM",float(anchors["DISTANCIA_GR_POLO_KM"].max()),cfg.regional_anchor_radius_km,"MENOR_IGUAL"),
        ("MENOR_POPULACAO_SEDE",float(managers["POPULACAO_SEDE_REFERENCIA"].min()),cfg.candidate_parent_population_min,"MAIOR_IGUAL"),
        ("CARTEIRAS_FRAGMENTADAS",int(summary.get("QTD_CARTEIRAS_FRAGMENTADAS_TOTAL",0)),0,"IGUAL"),
        ("UNIDADES_DUPLICADAS",int(assignments["DEMAND_ID"].duplicated().sum()),0,"IGUAL"),
        ("ESTRATEGICAS_NAO_ATENDIDAS",int((~assignments["ATENDIDA"]&assignments["EH_UNIDADE_ESTRATEGICA"]).sum()),0,"IGUAL"),
        ("LOJAS_EM_UNIDADES_NAO_ATENDIDAS",int(assignments.loc[~assignments["ATENDIDA"],"QTD_LOJAS"].sum()),0,"IGUAL"),
    ]; rows=[]
    for metric,value,limit,rule in checks:
        ok=value==limit if rule=="IGUAL" else (value<=limit if rule=="MENOR_IGUAL" else value>=limit)
        rows.append({"TIPO_AUDITORIA":"RESTRICAO_SOLUCAO","METRICA":metric,"VALOR":value,"LIMITE":limit,"REGRA":rule,"STATUS":"OK" if ok else "VIOLACAO"})
    if any(r["STATUS"]=="VIOLACAO" for r in rows): raise RuntimeError(f"Auditoria final encontrou violação: {[r for r in rows if r['STATUS']=='VIOLACAO']}")
    return pd.DataFrame(rows)


def hierarchy_change_status(current: int,proposed: int) -> str:
    if current==0 and proposed>0: return "CRIAR"
    if current>0 and proposed==0: return "REMOVER"
    if proposed>current: return "AMPLIAR"
    if proposed<current: return "REDUZIR"
    return "MANTER"


def build_hierarchy_outputs(current: pd.DataFrame,managers: pd.DataFrame,assignments: pd.DataFrame,run_id: str,scenario_id: str) -> tuple[pd.DataFrame,pd.DataFrame]:
    current_area=current.groupby("DESC_GERENCIA_AREA_ATUAL",as_index=False).agg(QTD_GERENTES_ATUAIS=("CHAVE_SUPERVISAO","nunique"),POPULACAO_ATUAL_ESTIMADA=("POPULACAO_ATUAL_ESTIMADA","sum"),CARGA_ATUAL_ESTIMADA=("CARGA_EQUIVALENTE_ATUAL_ESTIMADA","sum"),QTD_LOJAS_ATUAL=("QTD_LOJAS_ATUAL","sum")).rename(columns={"DESC_GERENCIA_AREA_ATUAL":"DESC_GERENCIA_AREA"})
    manager_area=managers.groupby("DESC_GERENCIA_AREA_PROPOSTA",as_index=False).agg(QTD_GERENTES_PROPOSTOS=("GERENCIA_ID","nunique")).rename(columns={"DESC_GERENCIA_AREA_PROPOSTA":"DESC_GERENCIA_AREA"}); served=assignments[assignments["ATENDIDA"]].copy()
    service_area=served.groupby("DESC_GERENCIA_AREA_PROPOSTA",as_index=False).agg(POPULACAO_ATENDIDA=("POPULACAO_UNIDADE","sum"),CARGA_EQUIVALENTE_PROPOSTA=("CARGA_EQUIVALENTE","sum"),QTD_LOJAS_PROPOSTA=("QTD_LOJAS","sum"),QTD_MUNICIPIOS_PROPOSTA=("COD_IBGE","nunique"),DISTANCIA_MAXIMA_KM=("DISTANCIA_KM","max"),QTD_FORA_RAIO=("FORA_RAIO_REFERENCIA","sum")).rename(columns={"DESC_GERENCIA_AREA_PROPOSTA":"DESC_GERENCIA_AREA"})
    weighted=[]
    for area_name,g in served.groupby("DESC_GERENCIA_AREA_PROPOSTA"):
        pop=g["POPULACAO_UNIDADE"].clip(lower=1); weighted.append({"DESC_GERENCIA_AREA":area_name,"DISTANCIA_MEDIA_PONDERADA_POP_KM":float(np.average(g["DISTANCIA_KM"],weights=pop)),"DISTANCIA_P90_PONDERADA_POP_KM":weighted_percentile(g["DISTANCIA_KM"],pop,.9)})
    proposed_area=manager_area.merge(service_area,on="DESC_GERENCIA_AREA",how="outer").merge(pd.DataFrame(weighted),on="DESC_GERENCIA_AREA",how="left")
    unattended=assignments[~assignments["ATENDIDA"]].copy(); unattended["DESC_GERENCIA_AREA"]=unattended["UF"].map(DESC_AREA_POR_UF); unattended_area=unattended.groupby("DESC_GERENCIA_AREA",as_index=False).agg(QTD_MUNICIPIOS_NAO_ATENDIDOS=("COD_IBGE","nunique"),POPULACAO_NAO_ATENDIDA=("POPULACAO_UNIDADE","sum"),QTD_LOJAS_NAO_ATENDIDAS=("QTD_LOJAS","sum"))
    areas=pd.DataFrame({"DESC_GERENCIA_AREA":sorted(set(DESC_AREA_POR_UF.values()))}); area=areas.merge(current_area,on="DESC_GERENCIA_AREA",how="left").merge(proposed_area,on="DESC_GERENCIA_AREA",how="left").merge(unattended_area,on="DESC_GERENCIA_AREA",how="left")
    numeric_cols=[c for c in area.columns if c!="DESC_GERENCIA_AREA"]; area[numeric_cols]=area[numeric_cols].fillna(0); area["QTD_GERENTES_ATUAIS"]=area["QTD_GERENTES_ATUAIS"].astype(int); area["QTD_GERENTES_PROPOSTOS"]=area["QTD_GERENTES_PROPOSTOS"].astype(int); area["DELTA_GERENTES"]=area["QTD_GERENTES_PROPOSTOS"]-area["QTD_GERENTES_ATUAIS"]; area["VARIACAO_PERCENTUAL_GERENTES"]=np.where(area["QTD_GERENTES_ATUAIS"]>0,area["DELTA_GERENTES"]/area["QTD_GERENTES_ATUAIS"],np.nan); area["RECOMENDACAO"]= [hierarchy_change_status(int(c),int(p)) for c,p in zip(area["QTD_GERENTES_ATUAIS"],area["QTD_GERENTES_PROPOSTOS"])]; area.insert(0,"RUN_ID",run_id); area.insert(1,"CENARIO_ID",scenario_id)
    if int(area["QTD_GERENTES_ATUAIS"].sum())!=135 or int(area["QTD_GERENTES_PROPOSTOS"].sum())!=135: raise RuntimeError("Consolidação por DESC_GERENCIA_AREA não fecha em 135 gerentes atuais e propostos.")
    regional_managers=managers.groupby(["COD_GER_REG","GER_REGIONAL","DESC_GERENCIA_AREA_GR","UF_GR"],as_index=False).agg(QTD_GERENTES_PROPOSTOS=("GERENCIA_ID","nunique"),QTD_ANCORAS=("EH_ANCORA_GR","sum")); regional_service=served.groupby("COD_GER_REG",as_index=False).agg(POPULACAO_ATENDIDA=("POPULACAO_UNIDADE","sum"),CARGA_EQUIVALENTE_PROPOSTA=("CARGA_EQUIVALENTE","sum"),QTD_LOJAS_PROPOSTA=("QTD_LOJAS","sum"),QTD_MUNICIPIOS_PROPOSTA=("COD_IBGE","nunique"),DISTANCIA_MAXIMA_KM=("DISTANCIA_KM","max"),QTD_FORA_RAIO=("FORA_RAIO_REFERENCIA","sum"))
    gr_reference=managers[["COD_GER_REG","GER_REGIONAL","DESC_GERENCIA_AREA_GR","UF_GR","LATITUDE_GR","LONGITUDE_GR"]].drop_duplicates("COD_GER_REG"); current_gr_rows=[]
    for manager in current.itertuples(index=False):
        pool=gr_reference[gr_reference["DESC_GERENCIA_AREA_GR"].astype(str)==str(manager.DESC_GERENCIA_AREA_ATUAL)]
        if pool.empty: raise RuntimeError(f"Sem GR para associar gerente atual da área {manager.DESC_GERENCIA_AREA_ATUAL}.")
        dist=haversine_arrays(np.full(len(pool),float(manager.LATITUDE_ATUAL)),np.full(len(pool),float(manager.LONGITUDE_ATUAL)),pool["LATITUDE_GR"].to_numpy(float),pool["LONGITUDE_GR"].to_numpy(float)); gr=pool.iloc[int(np.argmin(dist))]
        current_gr_rows.append({"COD_GER_REG":gr.COD_GER_REG,"CHAVE_SUPERVISAO":manager.CHAVE_SUPERVISAO,"POPULACAO_ATUAL_ESTIMADA":getattr(manager,"POPULACAO_ATUAL_ESTIMADA",0),"CARGA_ATUAL_ESTIMADA":getattr(manager,"CARGA_EQUIVALENTE_ATUAL_ESTIMADA",0),"QTD_LOJAS_ATUAL":getattr(manager,"QTD_LOJAS_ATUAL",0)})
    current_gr=pd.DataFrame(current_gr_rows).groupby("COD_GER_REG",as_index=False).agg(QTD_GERENTES_ATUAIS=("CHAVE_SUPERVISAO","nunique"),POPULACAO_ATUAL_ESTIMADA=("POPULACAO_ATUAL_ESTIMADA","sum"),CARGA_ATUAL_ESTIMADA=("CARGA_ATUAL_ESTIMADA","sum"),QTD_LOJAS_ATUAL=("QTD_LOJAS_ATUAL","sum")); regional=regional_managers.merge(current_gr,on="COD_GER_REG",how="left").merge(regional_service,on="COD_GER_REG",how="left"); regional=regional.fillna({"QTD_GERENTES_ATUAIS":0,"POPULACAO_ATUAL_ESTIMADA":0,"CARGA_ATUAL_ESTIMADA":0,"QTD_LOJAS_ATUAL":0}); regional["QTD_GERENTES_ATUAIS"]=regional["QTD_GERENTES_ATUAIS"].astype(int); regional["DELTA_GERENTES"]=regional["QTD_GERENTES_PROPOSTOS"]-regional["QTD_GERENTES_ATUAIS"]; regional["RECOMENDACAO"]= [hierarchy_change_status(int(c),int(p)) for c,p in zip(regional["QTD_GERENTES_ATUAIS"],regional["QTD_GERENTES_PROPOSTOS"])]; regional.insert(0,"RUN_ID",run_id); regional.insert(1,"CENARIO_ID",scenario_id)
    if int(regional["QTD_GERENTES_ATUAIS"].sum())!=135 or int(regional["QTD_GERENTES_PROPOSTOS"].sum())!=135: raise RuntimeError("Consolidação por GR não fecha em 135 gerentes atuais e propostos.")
    return area,regional


def fragmentation_stats(assignments: pd.DataFrame,position: np.ndarray,neighbors: dict[int,set[int]],strategic: np.ndarray) -> dict[str,int]:
    frag=0; islands=0; maxc=1; frag_total=0; islands_total=0
    for cluster in sorted(set(position.tolist())):
        if cluster<0: continue
        all_nodes={int(u) for u in np.flatnonzero(position==cluster)}; all_comps=components(all_nodes,neighbors) if all_nodes else []
        frag_total+=int(len(all_comps)>1); islands_total+=max(0,len(all_comps)-1)
        nodes={int(u) for u in np.flatnonzero(position==cluster) if strategic[int(u)]}
        if not nodes: continue
        comps=components(nodes,neighbors); maxc=max(maxc,len(comps)); frag+=int(len(comps)>1); islands+=max(0,len(comps)-1)
    small_islands=0
    for r in assignments[assignments["EH_MUNICIPIO_PEQUENO"] & assignments["ATENDIDA"]].itertuples(index=False):
        if not any(int(position[v])==int(r.CLUSTER_IDX) for v in neighbors.get(int(r.DEMAND_IDX),set())): small_islands+=1
    return {"QTD_CARTEIRAS_FRAGMENTADAS_TOTAL":frag_total,"QTD_ILHAS_TOTAL":islands_total,"QTD_CARTEIRAS_FRAGMENTADAS_CRITICAS":frag,"QTD_ILHAS_CRITICAS":islands,"MAIOR_QTD_COMPONENTES_ESTRATEGICOS":maxc,"QTD_ILHAS_TOLERAVEIS_PEQUENOS":small_islands}


def build_scenario_summary(run_id: str,scenario_id: str,a: pd.DataFrame,m: pd.DataFrame,position: np.ndarray,neighbors: dict[int,set[int]],demand: pd.DataFrame,selected: list[int],elapsed: float,cfg: ModelConfig) -> pd.DataFrame:
    served=a[a["ATENDIDA"]].copy(); unserved=a[~a["ATENDIDA"]].copy(); small=served[served["EH_MUNICIPIO_PEQUENO"]]; rel=served[~served["EH_MUNICIPIO_PEQUENO"]]; pop=served["POPULACAO_UNIDADE"].clip(lower=1); rr=served[served["RAIO_REFERENCIA_KM"].notna()]; denom=float(rr["POPULACAO_UNIDADE"].sum()); ok=float(rr.loc[~rr["FORA_RAIO_REFERENCIA"],"POPULACAO_UNIDADE"].sum()); pp=m["POPULACAO_SEDE_REFERENCIA"]
    r={
        "RUN_ID":run_id,"CENARIO_ID":scenario_id,"MODELO_VERSAO":cfg.model_version,
        "QTD_GERENCIAS_SOLICITADA":len(selected),"QTD_GERENCIAS_SELECIONADA":len(m),
        "QTD_UNIDADES_ATENDIDAS":served["DEMAND_ID"].nunique(),"QTD_MUNICIPIOS_ATENDIDOS":served["COD_IBGE"].nunique(),
        "QTD_UNIDADES_NAO_ATENDIDAS":unserved["DEMAND_ID"].nunique(),"QTD_MUNICIPIOS_NAO_ATENDIDOS":unserved["COD_IBGE"].nunique(),
        "POPULACAO_NAO_ATENDIDA":float(unserved["POPULACAO_UNIDADE"].sum()),"QTD_LOJAS_NAO_ATENDIDAS":int(unserved["QTD_LOJAS"].sum()),
        "QTD_LOJAS_ATENDIDAS":int(served["QTD_LOJAS"].sum()),"POPULACAO_ATENDIDA":float(served["POPULACAO_UNIDADE"].sum()),
        "CARGA_EQUIVALENTE_TOTAL":float(served["CARGA_EQUIVALENTE"].sum()),"CARGA_EQUIVALENTE_ALVO":float(served["CARGA_EQUIVALENTE"].sum())/len(selected),
        "DISTANCIA_MEDIA_SIMPLES_KM":float(served["DISTANCIA_KM"].mean()),"DISTANCIA_MEDIA_PONDERADA_POP_KM":float(np.average(served["DISTANCIA_KM"],weights=pop)),
        "DISTANCIA_P90_PONDERADA_POP_KM":weighted_percentile(served["DISTANCIA_KM"],pop,.9),"DISTANCIA_MAXIMA_KM":float(served["DISTANCIA_KM"].max()),
        "PERC_POPULACAO_DENTRO_RAIO":ok/denom if denom else np.nan,"QTD_RELEVANTES_FORA_RAIO":int(rr["FORA_RAIO_REFERENCIA"].sum()),
        "QTD_30_50_FORA_50KM":int(((served["FAIXA_POPULACIONAL"]=="30_A_50_MIL")&served["FORA_RAIO_REFERENCIA"]).sum()),
        "QTD_50_100_FORA_100KM":int(((served["FAIXA_POPULACIONAL"]=="50_A_100_MIL")&served["FORA_RAIO_REFERENCIA"]).sum()),
        "QTD_100_300_FORA_100KM":int(((served["FAIXA_POPULACIONAL"]=="100_A_300_MIL")&served["FORA_RAIO_REFERENCIA"]).sum()),
        "QTD_PEQUENOS_DISTANTES":int((small["DISTANCIA_KM"]>cfg.small_distance_diagnostic_km).sum()) if not small.empty else 0,
        "POPULACAO_PEQUENOS_DISTANTES":float(small.loc[small["DISTANCIA_KM"]>cfg.small_distance_diagnostic_km,"POPULACAO_UNIDADE"].sum()) if not small.empty else 0.0,
        "DISTANCIA_MEDIA_RELEVANTES_KM":float(rel["DISTANCIA_KM"].mean()) if not rel.empty else np.nan,
        "MENOR_INDICE_CARGA":float(m["INDICE_CARGA_EQUIVALENTE"].min()),"MAIOR_INDICE_CARGA":float(m["INDICE_CARGA_EQUIVALENTE"].max()),"DESVIO_INDICE_CARGA":float(m["INDICE_CARGA_EQUIVALENTE"].std(ddof=0)),
        "QTD_CARTEIRAS_ACIMA_TETO":int((m["INDICE_CARGA_EQUIVALENTE"]>cfg.maximum_load_factor).sum()),"QTD_CARTEIRAS_ABAIXO_PISO":int((m["INDICE_CARGA_EQUIVALENTE"]<cfg.minimum_load_factor).sum()),
        "QTD_CRUZAMENTOS_UF":int(served["CRUZA_UF"].sum()),"QTD_NAO_MAIS_PROXIMO_RELEVANTES":int((~rel["EH_MAIS_PROXIMO"]).sum()) if not rel.empty else 0,"QTD_NAO_MAIS_PROXIMO_PEQUENOS":int((~small["EH_MAIS_PROXIMO"]).sum()) if not small.empty else 0,
        "POPULACAO_MEDIA_POLOS":float(pp.mean()),"MENOR_POPULACAO_POLO":float(pp.min()),"QTD_POLOS_MUNICIPIOS_30_50":int(((pp>=30000)&(pp<50000)).sum()),"QTD_POLOS_MUNICIPIOS_50_100":int(((pp>=50000)&(pp<100000)).sum()),"QTD_POLOS_MUNICIPIOS_100_300":int(((pp>=100000)&(pp<300000)).sum()),"QTD_POLOS_MUNICIPIOS_300_MAIS":int((pp>=300000).sum()),"QTD_CIDADES_COM_MULTIPLOS_POLOS":int((m.groupby("COD_IBGE_POLO").size()>1).sum()),
        "TEMPO_SEGUNDOS":elapsed,"DATA_EXECUCAO":datetime.now(),
    }
    r.update(fragmentation_stats(a,position,neighbors,demand["EH_UNIDADE_ESTRATEGICA"].to_numpy(bool))); return pd.DataFrame([r])


def build_diagnostics(summary: pd.DataFrame,a: pd.DataFrame,m: pd.DataFrame) -> pd.DataFrame:
    run=summary.iloc[0]["RUN_ID"]; scen=summary.iloc[0]["CENARIO_ID"]; rows=[]
    for c,v in summary.iloc[0].items():
        if c not in {"RUN_ID","CENARIO_ID","DATA_EXECUCAO"}: rows.append({"RUN_ID":run,"CENARIO_ID":scen,"TIPO":"CENARIO","DESCRICAO":c,"VALOR":v})
    for faixa,g in a.groupby("FAIXA_POPULACIONAL"): rows.append({"RUN_ID":run,"CENARIO_ID":scen,"TIPO":"FAIXA","DESCRICAO":f"DIST_MEDIA_{faixa}","VALOR":float(g["DISTANCIA_KM"].mean())})
    return pd.DataFrame(rows)


def build_pole_audit(m: pd.DataFrame,a: pd.DataFrame,candidates: pd.DataFrame,selected: list[int],cost: np.ndarray,weights: np.ndarray,cfg: ModelConfig) -> pd.DataFrame:
    audit=m.copy(); audit=audit.merge(m.groupby("COD_IBGE_POLO").size().rename("QTD_POLOS_MESMO_MUNICIPIO"),on="COD_IBGE_POLO",how="left"); extra=[]
    for cluster,cidx in enumerate(selected):
        mid=manager_id(len(selected),cluster); current=candidates.iloc[int(cidx)]; members=a.loc[a["GERENCIA_ID"]==mid,"DEMAND_IDX"].to_numpy(dtype=int)
        higher=candidates[candidates["POPULACAO_SEDE_REFERENCIA"]>float(current.POPULACAO_SEDE_REFERENCIA)].copy()
        if higher.empty:
            extra.append({"GERENCIA_ID":mid,"ALT_MAIS_POPULOSA_CANDIDATE_ID":pd.NA,"ALT_MAIS_POPULOSA_COD_IBGE":pd.NA,"ALT_MAIS_POPULOSA_NM_MUN":pd.NA,"ALT_DISTANCIA_DO_POLO_KM":np.nan,"ALT_DELTA_OBJETIVO_LOCAL":np.nan}); continue
        hd=haversine_arrays(np.full(len(higher),float(current.LATITUDE)),np.full(len(higher),float(current.LONGITUDE)),higher["LATITUDE"].to_numpy(float),higher["LONGITUDE"].to_numpy(float)); higher=higher.assign(_DIST=hd); higher=higher[higher["_DIST"]<=cfg.pole_audit_search_radius_km]
        if higher.empty:
            extra.append({"GERENCIA_ID":mid,"ALT_MAIS_POPULOSA_CANDIDATE_ID":pd.NA,"ALT_MAIS_POPULOSA_COD_IBGE":pd.NA,"ALT_MAIS_POPULOSA_NM_MUN":pd.NA,"ALT_DISTANCIA_DO_POLO_KM":np.nan,"ALT_DELTA_OBJETIVO_LOCAL":np.nan}); continue
        current_obj=float(np.sum(cost[members,int(cidx)]*weights[members])+float(current.PENALIDADE_SEDE_KM_EQ)); best=None
        for _,alt in higher.iterrows():
            ai=int(alt["CANDIDATE_IDX"]); alt_obj=float(np.sum(cost[members,ai]*weights[members])+float(alt["PENALIDADE_SEDE_KM_EQ"])); delta=alt_obj-current_obj; cand=(delta,ai,float(alt["_DIST"]))
            if best is None or cand[0]<best[0]: best=cand
        delta,ai,dist_alt=best; alt=candidates.iloc[ai]
        extra.append({"GERENCIA_ID":mid,"ALT_MAIS_POPULOSA_CANDIDATE_ID":alt.CANDIDATE_ID,"ALT_MAIS_POPULOSA_COD_IBGE":alt.COD_IBGE,"ALT_MAIS_POPULOSA_NM_MUN":alt.NM_MUN,"ALT_MAIS_POPULOSA_POPULACAO_SEDE":float(alt.POPULACAO_SEDE_REFERENCIA),"ALT_DISTANCIA_DO_POLO_KM":dist_alt,"ALT_DELTA_OBJETIVO_LOCAL":delta})
    return audit.merge(pd.DataFrame(extra),on="GERENCIA_ID",how="left")

def solve_scenario(demand: pd.DataFrame,candidates: pd.DataFrame,distance: np.ndarray,cost: np.ndarray,neighbors: dict[int,set[int]],n: int,cfg: ModelConfig,run_id: str,regional_anchor_seeds: list[int],regional_anchor_audit: pd.DataFrame) -> dict[str,pd.DataFrame]:
    scen=f"GREENFIELD_V3_{n}_{uuid.uuid4().hex[:8].upper()}"; started=time.perf_counter(); weights=demand["PESO_ATRACAO_POPULACIONAL"].to_numpy(float); loads=demand["CARGA_EQUIVALENTE"].to_numpy(float); opening=candidates["PENALIDADE_SEDE_KM_EQ"].to_numpy(float)
    strategic=demand["EH_UNIDADE_ESTRATEGICA"].to_numpy(bool)
    req=calculate_metropolitan_requirements(demand,candidates,n,cfg); metro_seeds=select_metropolitan_seeds(demand,candidates,cost,weights,opening,req,n,cfg,regional_anchor_seeds)
    seeds,locked=ensure_strategic_component_seeds(metro_seeds,candidates,neighbors,strategic,n)
    selected=greedy_p_median_v3(cost,weights,opening,n,cfg.distance_chunk_size,seeds); selected,initial=refine_selected_contiguous_v3(cost,weights,loads,candidates,selected,locked,neighbors,strategic,opening,cfg); initial=force_poles(initial,selected,candidates); fixed={int(candidates.iloc[int(c)]["DEMAND_IDX_ORIGEM_POLO"]) for c in selected}
    pos,moves=balance_load_v3(cost,distance,loads,selected,initial,neighbors,strategic,fixed,weights,cfg); pos,repairs=repair_critical_islands(pos,selected,cost,loads,weights,strategic,neighbors,fixed,cfg); moves.extend(repairs)
    pos,metro_moves=ensure_metropolitan_minimum_units(pos,selected,candidates,demand,cost,loads,weights,neighbors,fixed,cfg); moves.extend(metro_moves); pos=force_poles(pos,selected,candidates); pos,small_moves=apply_small_unserved_policy(pos,selected,demand,distance,neighbors,fixed,cfg); moves.extend(small_moves)
    pos,post_small_balance=balance_load_v3(cost,distance,loads,selected,pos,neighbors,strategic,fixed,weights,cfg); moves.extend(post_small_balance); pos,post_small_repairs=repair_critical_islands(pos,selected,cost,loads,weights,strategic,neighbors,fixed,cfg); moves.extend(post_small_repairs); pos,post_small_metro=ensure_metropolitan_minimum_units(pos,selected,candidates,demand,cost,loads,weights,neighbors,fixed,cfg); moves.extend(post_small_metro); pos=force_poles(pos,selected,candidates); validate_solution_constraints(demand,candidates,selected,pos,neighbors,n,cfg)
    a=build_assignments(demand,candidates,selected,pos,initial,distance,cost,moves,run_id,scen); m=build_manager_summary(a,candidates,selected,run_id,scen,cfg); s=build_scenario_summary(run_id,scen,a,m,pos,neighbors,demand,selected,time.perf_counter()-started,cfg); d=build_diagnostics(s,a,m); p=build_pole_audit(m,a,candidates,selected,cost,weights,cfg)
    anchor_out=regional_anchor_audit.copy(); cluster_by_candidate={int(c):i for i,c in enumerate(selected)}; anchor_out.insert(0,"RUN_ID",run_id); anchor_out.insert(1,"CENARIO_ID",scen); anchor_out["GERENCIA_ID"]=[manager_id(n,cluster_by_candidate[int(c)]) for c in anchor_out["CANDIDATE_IDX_ANCHOR"]]
    return {"scenario":s,"managers":m,"assignments":a,"diagnostics":d,"pole_audit":p,"requirements":req.assign(RUN_ID=run_id,CENARIO_ID=scen),"regional_anchors":anchor_out,"position":pd.DataFrame({"DEMAND_IDX":np.arange(len(pos)),"CLUSTER_IDX":pos})}

# =============================================================================
# ESTRUTURA ATUAL / TRANSIÇÃO
# =============================================================================

def assign_stores_to_proposed_managers(stores: pd.DataFrame,a: pd.DataFrame,run_id: str,scenario_id: str) -> pd.DataFrame:
    if stores.empty: return pd.DataFrame()
    cols=[c for c in ["DEMAND_ID","GERENCIA_ID","CANDIDATE_ID","COD_IBGE_POLO","CD_DIST_POLO","NM_MUN_POLO","NM_DIST_POLO","UF_POLO","LATITUDE_POLO","LONGITUDE_POLO","DESC_GERENCIA_AREA_PROPOSTA","COD_GER_REG","GER_REGIONAL"] if c in a.columns]
    x=stores.merge(a[cols].drop_duplicates("DEMAND_ID"),on="DEMAND_ID",how="left"); x.insert(0,"RUN_ID",run_id); x.insert(1,"CENARIO_ID",scenario_id); x["DISTANCIA_LOJA_POLO_KM"]=haversine_arrays(x["LATITUDE"].to_numpy(float),x["LONGITUDE"].to_numpy(float),x["LATITUDE_POLO"].to_numpy(float),x["LONGITUDE_POLO"].to_numpy(float)); return x


def attach_current_pole_reference(current: pd.DataFrame,demand: pd.DataFrame) -> pd.DataFrame:
    tree=BallTree(np.radians(demand[["LATITUDE","LONGITUDE"]].to_numpy(float)),metric="haversine"); dist,idx=tree.query(np.radians(current[["LATITUDE_ATUAL","LONGITUDE_ATUAL"]].to_numpy(float)),k=1); near=demand.iloc[idx[:,0]].reset_index(drop=True); x=current.reset_index(drop=True).copy()
    for target,source in (("DEMAND_ID_REFERENCIA_ATUAL","DEMAND_ID"),("COD_IBGE_REFERENCIA_ATUAL","COD_IBGE"),("CD_DIST_REFERENCIA_ATUAL","CD_DIST"),("NM_MUN_REFERENCIA_ATUAL","NM_MUN"),("NM_DIST_REFERENCIA_ATUAL","NM_DIST"),("UF_REFERENCIA_ATUAL","UF")): x[target]=near[source].to_numpy()
    x["DISTANCIA_POLO_ATUAL_REFERENCIA_KM"]=dist[:,0]*EARTH_RADIUS_KM; return x


def build_current_manager_portfolio(current: pd.DataFrame,stores: pd.DataFrame,demand: pd.DataFrame,run_id: str) -> pd.DataFrame:
    base=current.copy(); base.insert(0,"RUN_ID",run_id); nums=["QTD_UNIDADES_ATUAL","QTD_MUNICIPIOS_ATUAL","QTD_DISTRITOS_ATUAL","QTD_LOJAS_ATUAL","POPULACAO_ATUAL_ESTIMADA","CARGA_EQUIVALENTE_ATUAL_ESTIMADA"]
    valid=stores.dropna(subset=["CHAVE_SUPERVISAO"]); valid=valid[valid["CHAVE_SUPERVISAO"].isin(set(base["CHAVE_SUPERVISAO"]))]
    if valid.empty:
        for c in nums: base[c]=0
        return base
    cnt=valid.groupby(["DEMAND_ID","CHAVE_SUPERVISAO"],as_index=False).agg(QTD_LOJAS_UNIDADE_GERENTE=("CHAVE_LOJA","nunique")); tot=cnt.groupby("DEMAND_ID",as_index=False)["QTD_LOJAS_UNIDADE_GERENTE"].sum().rename(columns={"QTD_LOJAS_UNIDADE_GERENTE":"QTD_LOJAS_UNIDADE_TOTAL"}); cnt=cnt.merge(tot,on="DEMAND_ID",how="left"); cnt["PARTICIPACAO_LOJAS_UNIDADE"]=(cnt["QTD_LOJAS_UNIDADE_GERENTE"]/cnt["QTD_LOJAS_UNIDADE_TOTAL"].replace(0,np.nan)).fillna(0)
    cnt=cnt.merge(demand[["DEMAND_ID","TIPO_UNIDADE","COD_IBGE","POPULACAO_UNIDADE","CARGA_EQUIVALENTE"]],on="DEMAND_ID",how="left"); cnt["POPULACAO_ATUAL_ALOCADA"]=cnt["POPULACAO_UNIDADE"]*cnt["PARTICIPACAO_LOJAS_UNIDADE"]; cnt["CARGA_ATUAL_ALOCADA"]=cnt["CARGA_EQUIVALENTE"]*cnt["PARTICIPACAO_LOJAS_UNIDADE"]; cnt["EH_DIST"]=cnt["TIPO_UNIDADE"].eq("DISTRITO").astype(int)
    s=cnt.groupby("CHAVE_SUPERVISAO",as_index=False).agg(QTD_UNIDADES_ATUAL=("DEMAND_ID","nunique"),QTD_MUNICIPIOS_ATUAL=("COD_IBGE","nunique"),QTD_DISTRITOS_ATUAL=("EH_DIST","sum"),QTD_LOJAS_ATUAL=("QTD_LOJAS_UNIDADE_GERENTE","sum"),POPULACAO_ATUAL_ESTIMADA=("POPULACAO_ATUAL_ALOCADA","sum"),CARGA_EQUIVALENTE_ATUAL_ESTIMADA=("CARGA_ATUAL_ALOCADA","sum")); x=base.merge(s,on="CHAVE_SUPERVISAO",how="left"); x[nums]=x[nums].fillna(0); return x


def movement_band(km: float) -> str:
    if pd.isna(km): return "NAO_APLICAVEL"
    if km<=1: return "MESMO_PONTO_APROXIMADO"
    if km<=50: return "AJUSTE_LOCAL_ATE_50_KM"
    if km<=150: return "MOVIMENTO_REGIONAL_50_A_150_KM"
    if km<=300: return "MOVIMENTO_RELEVANTE_150_A_300_KM"
    return "REDESENHO_ESTRUTURAL_ACIMA_300_KM"


def compare_current_and_proposed(current: pd.DataFrame,proposed: pd.DataFrame,run_id: str,scenario_id: str) -> pd.DataFrame:
    d=pairwise_haversine_matrix(current,proposed,"LATITUDE_ATUAL","LONGITUDE_ATUAL","LATITUDE","LONGITUDE"); cr,pr=linear_sum_assignment(d); mc=set(cr.tolist()); mp=set(pr.tolist()); rows=[]
    for i,j in zip(cr,pr):
        c=current.iloc[int(i)]; p=proposed.iloc[int(j)]; km=float(d[i,j]); status="MANTIDO_MESMO_MUNICIPIO" if str(c.get("COD_IBGE_REFERENCIA_ATUAL"))==str(p.get("COD_IBGE_POLO")) else "MOVIMENTADO"
        rows.append({"RUN_ID":run_id,"CENARIO_ID":scenario_id,"STATUS_TRANSICAO":status,"FAIXA_MOVIMENTO":movement_band(km),"TIPO_MATCHING":"CORRESPONDENCIA_GEOGRAFICA_HUNGARIAN","CHAVE_SUPERVISAO_ATUAL":c.get("CHAVE_SUPERVISAO"),"GERENCIA_ID_PROPOSTA":p.get("GERENCIA_ID"),"LATITUDE_ATUAL":c.get("LATITUDE_ATUAL"),"LONGITUDE_ATUAL":c.get("LONGITUDE_ATUAL"),"COD_IBGE_REFERENCIA_ATUAL":c.get("COD_IBGE_REFERENCIA_ATUAL"),"NM_MUN_REFERENCIA_ATUAL":c.get("NM_MUN_REFERENCIA_ATUAL"),"LATITUDE_PROPOSTA":p.get("LATITUDE"),"LONGITUDE_PROPOSTA":p.get("LONGITUDE"),"COD_IBGE_PROPOSTO":p.get("COD_IBGE_POLO"),"NM_MUN_PROPOSTO":p.get("NM_MUN_POLO"),"DISTANCIA_MOVIMENTO_KM":km,"QTD_UNIDADES_ATUAL":c.get("QTD_UNIDADES_ATUAL",0),"QTD_UNIDADES_PROPOSTA":p.get("QTD_UNIDADES",0),"QTD_LOJAS_ATUAL":c.get("QTD_LOJAS_ATUAL",0),"QTD_LOJAS_PROPOSTA":p.get("QTD_LOJAS",0),"CARGA_EQUIVALENTE_ATUAL_ESTIMADA":c.get("CARGA_EQUIVALENTE_ATUAL_ESTIMADA",0),"CARGA_EQUIVALENTE_PROPOSTA":p.get("CARGA_EQUIVALENTE_TOTAL",0)})
    for i in sorted(set(range(len(current)))-mc):
        c=current.iloc[i]; rows.append({"RUN_ID":run_id,"CENARIO_ID":scenario_id,"STATUS_TRANSICAO":"EXCLUSAO_POSICAO_ATUAL","FAIXA_MOVIMENTO":"NAO_APLICAVEL","TIPO_MATCHING":"CORRESPONDENCIA_GEOGRAFICA_HUNGARIAN","CHAVE_SUPERVISAO_ATUAL":c.get("CHAVE_SUPERVISAO"),"LATITUDE_ATUAL":c.get("LATITUDE_ATUAL"),"LONGITUDE_ATUAL":c.get("LONGITUDE_ATUAL"),"COD_IBGE_REFERENCIA_ATUAL":c.get("COD_IBGE_REFERENCIA_ATUAL"),"NM_MUN_REFERENCIA_ATUAL":c.get("NM_MUN_REFERENCIA_ATUAL")})
    for j in sorted(set(range(len(proposed)))-mp):
        p=proposed.iloc[j]; rows.append({"RUN_ID":run_id,"CENARIO_ID":scenario_id,"STATUS_TRANSICAO":"INCLUSAO_NOVA_POSICAO","FAIXA_MOVIMENTO":"NAO_APLICAVEL","TIPO_MATCHING":"CORRESPONDENCIA_GEOGRAFICA_HUNGARIAN","GERENCIA_ID_PROPOSTA":p.get("GERENCIA_ID"),"LATITUDE_PROPOSTA":p.get("LATITUDE"),"LONGITUDE_PROPOSTA":p.get("LONGITUDE"),"COD_IBGE_PROPOSTO":p.get("COD_IBGE_POLO"),"NM_MUN_PROPOSTO":p.get("NM_MUN_POLO")})
    out=pd.DataFrame(rows)
    current_cols=[c for c in ["CHAVE_SUPERVISAO","DESC_GERENCIA_AREA_ATUAL","DESC_COORDENACAO","DESC_SUPERVISAO"] if c in current.columns]; proposed_cols=[c for c in ["GERENCIA_ID","DESC_GERENCIA_AREA_PROPOSTA","COD_GER_REG","GER_REGIONAL","TIPO_VINCULO_GR"] if c in proposed.columns]
    if current_cols: out=out.merge(current[current_cols].drop_duplicates("CHAVE_SUPERVISAO"),left_on="CHAVE_SUPERVISAO_ATUAL",right_on="CHAVE_SUPERVISAO",how="left").drop(columns=["CHAVE_SUPERVISAO"],errors="ignore")
    if proposed_cols: out=out.merge(proposed[proposed_cols].drop_duplicates("GERENCIA_ID"),left_on="GERENCIA_ID_PROPOSTA",right_on="GERENCIA_ID",how="left").drop(columns=["GERENCIA_ID"],errors="ignore")
    return out


def build_current_baseline(current: pd.DataFrame,demand: pd.DataFrame,stores_units: pd.DataFrame,run_id: str) -> pd.DataFrame:
    # Usa a hierarquia observada nas lojas como primeira referência da carteira atual.
    # Unidades sem loja/sem supervisor válido usam o polo atual geograficamente mais próximo.
    dist=pairwise_haversine_matrix(demand,current,"LATITUDE","LONGITUDE","LATITUDE_ATUAL","LONGITUDE_ATUAL")
    nearest=np.argmin(dist,axis=1); assigned=nearest.copy(); method=np.array(["FALLBACK_POLO_ATUAL_MAIS_PROXIMO"]*len(demand),dtype=object)
    supervisor_to_idx={str(r.CHAVE_SUPERVISAO):i for i,r in current.reset_index(drop=True).iterrows()}
    if not stores_units.empty:
        counts=stores_units.dropna(subset=["CHAVE_SUPERVISAO"]).groupby(["DEMAND_ID","CHAVE_SUPERVISAO"]).size().rename("QTD").reset_index(); counts=counts.sort_values(["DEMAND_ID","QTD"],ascending=[True,False]).drop_duplicates("DEMAND_ID")
        demand_row={str(v):i for i,v in enumerate(demand["DEMAND_ID"].astype(str))}
        for r in counts.itertuples(index=False):
            di=demand_row.get(str(r.DEMAND_ID)); ci=supervisor_to_idx.get(str(r.CHAVE_SUPERVISAO))
            if di is not None and ci is not None: assigned[di]=ci; method[di]="HIERARQUIA_MAIORIA_LOJAS"
    ad=dist[np.arange(len(demand)),assigned]; pop=demand["POPULACAO_UNIDADE"].clip(lower=1); radii=pd.to_numeric(demand["RAIO_REFERENCIA_KM"],errors="coerce"); valid=radii.notna(); ok=valid & (ad<=radii.fillna(np.inf).to_numpy()); den=float(demand.loc[valid,"POPULACAO_UNIDADE"].sum())
    return pd.DataFrame([{"RUN_ID":run_id,"CENARIO_ID":"BASELINE_ATUAL_ESTIMADO","QTD_GERENCIAS_SOLICITADA":len(current),"QTD_GERENCIAS_SELECIONADA":len(current),"QTD_UNIDADES_ATENDIDAS":len(demand),"QTD_MUNICIPIOS_ATENDIDOS":demand["COD_IBGE"].nunique(),"POPULACAO_ATENDIDA":float(demand["POPULACAO_UNIDADE"].sum()),"DISTANCIA_MEDIA_SIMPLES_KM":float(np.mean(ad)),"DISTANCIA_MEDIA_PONDERADA_POP_KM":float(np.average(ad,weights=pop)),"DISTANCIA_P90_PONDERADA_POP_KM":weighted_percentile(pd.Series(ad),pop.reset_index(drop=True),.9),"DISTANCIA_MAXIMA_KM":float(np.max(ad)),"PERC_POPULACAO_DENTRO_RAIO":float(demand.loc[ok,"POPULACAO_UNIDADE"].sum()/den) if den else np.nan,"QTD_RELEVANTES_FORA_RAIO":int((valid & ~ok).sum()),"QTD_UNIDADES_BASELINE_HIERARQUIA":int((method=="HIERARQUIA_MAIORIA_LOJAS").sum()),"QTD_UNIDADES_BASELINE_FALLBACK":int((method=="FALLBACK_POLO_ATUAL_MAIS_PROXIMO").sum()),"DATA_EXECUCAO":datetime.now()}])


# =============================================================================
# GEOJSON / ARQUIVOS
# =============================================================================

def json_value(v: Any) -> Any:
    if pd.isna(v): return None
    if isinstance(v,(np.integer,)): return int(v)
    if isinstance(v,(np.floating,)): return float(v)
    if isinstance(v,(np.bool_,)): return bool(v)
    if isinstance(v,(pd.Timestamp,datetime)): return v.isoformat()
    return v


def dataframe_to_point_geojson(df: pd.DataFrame,lat: str,lon: str,props: list[str]) -> dict[str,Any]:
    f=[]
    for _,r in df.iterrows():
        if pd.isna(r.get(lat)) or pd.isna(r.get(lon)): continue
        f.append({"type":"Feature","geometry":{"type":"Point","coordinates":[float(r[lon]),float(r[lat])]},"properties":{c:json_value(r.get(c)) for c in props}})
    return {"type":"FeatureCollection","features":f}


def assignments_to_line_geojson(a: pd.DataFrame) -> dict[str,Any]:
    f=[]
    valid=a.dropna(subset=["LATITUDE_POLO","LONGITUDE_POLO","LATITUDE","LONGITUDE","GERENCIA_ID"])
    for r in valid.itertuples(index=False):
        f.append({"type":"Feature","geometry":{"type":"LineString","coordinates":[[float(r.LONGITUDE_POLO),float(r.LATITUDE_POLO)],[float(r.LONGITUDE),float(r.LATITUDE)]]},"properties":{"GERENCIA_ID":r.GERENCIA_ID,"DEMAND_ID":r.DEMAND_ID,"DISTANCIA_KM":float(r.DISTANCIA_KM),"POPULACAO_UNIDADE":int(r.POPULACAO_UNIDADE),"EH_MUNICIPIO_PEQUENO":bool(r.EH_MUNICIPIO_PEQUENO),"METODO_ATRIBUICAO":r.METODO_ATRIBUICAO}})
    return {"type":"FeatureCollection","features":f}


def transition_to_line_geojson(t: pd.DataFrame) -> dict[str,Any]:
    f=[]
    for r in t.dropna(subset=["LATITUDE_ATUAL","LONGITUDE_ATUAL","LATITUDE_PROPOSTA","LONGITUDE_PROPOSTA"]).itertuples(index=False):
        f.append({"type":"Feature","geometry":{"type":"LineString","coordinates":[[float(r.LONGITUDE_ATUAL),float(r.LATITUDE_ATUAL)],[float(r.LONGITUDE_PROPOSTA),float(r.LATITUDE_PROPOSTA)]]},"properties":{"CHAVE_SUPERVISAO_ATUAL":json_value(r.CHAVE_SUPERVISAO_ATUAL),"GERENCIA_ID_PROPOSTA":json_value(r.GERENCIA_ID_PROPOSTA),"STATUS_TRANSICAO":r.STATUS_TRANSICAO,"FAIXA_MOVIMENTO":r.FAIXA_MOVIMENTO,"DISTANCIA_MOVIMENTO_KM":json_value(r.DISTANCIA_MOVIMENTO_KM)}})
    return {"type":"FeatureCollection","features":f}


def regional_anchor_lines_geojson(anchor: pd.DataFrame) -> dict[str,Any]:
    features=[]
    for r in anchor.dropna(subset=["LATITUDE","LONGITUDE","LATITUDE_POLO_ANCHOR","LONGITUDE_POLO_ANCHOR"]).itertuples(index=False):
        features.append({"type":"Feature","geometry":{"type":"LineString","coordinates":[[float(r.LONGITUDE),float(r.LATITUDE)],[float(r.LONGITUDE_POLO_ANCHOR),float(r.LATITUDE_POLO_ANCHOR)]]},"properties":{"COD_GER_REG":json_value(r.COD_GER_REG),"GER_REGIONAL":json_value(r.GER_REGIONAL),"GERENCIA_ID":json_value(r.GERENCIA_ID),"DISTANCIA_GR_POLO_KM":json_value(r.DISTANCIA_GR_POLO_KM),"STATUS_ANCORA":json_value(r.STATUS_ANCORA)}})
    return {"type":"FeatureCollection","features":features}


def save_scenario_files(cfg: ModelConfig,result: dict[str,pd.DataFrame],stores: pd.DataFrame,current: pd.DataFrame,transition: pd.DataFrame,unit_geo: gpd.GeoDataFrame) -> None:
    sid=str(result["scenario"].iloc[0]["CENARIO_ID"]); folder=cfg.output_dir/sid; folder.mkdir(parents=True,exist_ok=True)
    if cfg.save_excel:
        with pd.ExcelWriter(folder/f"resultado_{sid}.xlsx",engine="openpyxl") as w:
            sheets=[("cenario",result["scenario"]),("gerencias_propostas",result["managers"]),("unidades_atendidas",result["assignments"]),("lojas_propostas",stores),("gerencias_atuais",current),("transicao",transition),("diagnosticos",result["diagnostics"]),("auditoria_polos",result["pole_audit"]),("metropoles",result["requirements"]),("vinculo_gr_polo",result["regional_links"]),("ancoras_gr",result["regional_anchors"]),("hierarquia_areas",result["hierarchy_area"]),("hierarquia_regionais",result["hierarchy_regional"]),("auditoria_territorial",result["territorial_audit"]),("nao_atendidos",result["unattended"])]
            for name,df in sheets: df.to_excel(w,sheet_name=name[:31],index=False)
    if cfg.save_geojson:
        payloads={
            "gerencias_propostas.geojson":dataframe_to_point_geojson(result["managers"],"LATITUDE","LONGITUDE",["GERENCIA_ID","CANDIDATE_ID","COD_IBGE_POLO","NM_MUN_POLO","UF_POLO","DESC_GERENCIA_AREA_PROPOSTA","COD_GER_REG","GER_REGIONAL","EH_ANCORA_GR","POPULACAO_SEDE_REFERENCIA","INDICE_CARGA_EQUIVALENTE"]),
            "unidades_atendidas.geojson":dataframe_to_point_geojson(result["assignments"],"LATITUDE","LONGITUDE",["ATENDIDA","MOTIVO_NAO_ATENDIMENTO","GERENCIA_ID","DEMAND_ID","TIPO_UNIDADE","FAIXA_POPULACIONAL","COD_IBGE","CD_DIST","POPULACAO_UNIDADE","DISTANCIA_KM","RAIO_REFERENCIA_KM","FORA_RAIO_REFERENCIA","METODO_ATRIBUICAO","EH_MUNICIPIO_PEQUENO","GERENCIA_MAIS_PROXIMA","DELTA_DISTANCIA_VS_MAIS_PROXIMO_KM"]),
            "linhas_atendimento.geojson":assignments_to_line_geojson(result["assignments"]),
            "gerencias_atuais.geojson":dataframe_to_point_geojson(current,"LATITUDE_ATUAL","LONGITUDE_ATUAL",["CHAVE_SUPERVISAO","COD_IBGE_REFERENCIA_ATUAL","NM_MUN_REFERENCIA_ATUAL","QTD_UNIDADES_ATUAL","QTD_LOJAS_ATUAL"]),
            "movimentos_atual_proposto.geojson":transition_to_line_geojson(transition),
            "gr_regionais.geojson":dataframe_to_point_geojson(result["regional_anchors"],"LATITUDE","LONGITUDE",["COD_GER_REG","GER_REGIONAL","UF_GR","DESC_GERENCIA_AREA_GR","GERENCIA_ID","CANDIDATE_ID_ANCHOR","DISTANCIA_GR_POLO_KM"]),
            "linhas_gr_polo.geojson":regional_anchor_lines_geojson(result["regional_anchors"]),
        }
        for fn,p in payloads.items(): (folder/fn).write_text(json.dumps(p,ensure_ascii=False),encoding="utf-8")
        if not unit_geo.empty:
            terr=unit_geo.merge(result["assignments"][["DEMAND_ID","GERENCIA_ID","TIPO_UNIDADE","FAIXA_POPULACIONAL","COD_IBGE","CD_DIST","POPULACAO_UNIDADE","CARGA_EQUIVALENTE","DISTANCIA_KM","METODO_ATRIBUICAO","EH_MUNICIPIO_PEQUENO","EH_CORREDOR_CONTIGUIDADE","MOTIVO_NAO_ATENDIMENTO"]],on="DEMAND_ID",how="inner"); terr.to_file(folder/"carteiras_unidades.geojson",driver="GeoJSON"); served_terr=terr.dropna(subset=["GERENCIA_ID"]); served_terr[["GERENCIA_ID","geometry"]].dissolve(by="GERENCIA_ID",as_index=False).to_file(folder/"carteiras_dissolvidas.geojson",driver="GeoJSON")

# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    configure_logging(); log_step("GREENFIELD V3 — INÍCIO")
    sql=SQLConfig(); cfg=ModelConfig(); cfg.output_dir.mkdir(parents=True,exist_ok=True); run_id=uuid.uuid4().hex.upper(); engine=create_sql_engine(sql)
    execution={"RUN_ID":run_id,"MODELO_VERSAO":cfg.model_version,"CONFIG_HASH":config_hash(cfg),"PERIODO_LOJAS":cfg.periodo_lojas,"PORTE_CANDIDATO":cfg.candidate_parent_population_min,"LIMIAR_CIDADE_GRANDE":cfg.large_city_threshold,"LIMIAR_MUNICIPIO_PEQUENO":cfg.small_unit_threshold,"DATA_INICIO":datetime.now(),"STATUS":"EM_EXECUCAO"}
    try:
        log_step("1/9 Distritos + malha municipal")
        district_ref,district_geo=load_district_data(cfg); municipal_geo=load_municipal_geometry(cfg)

        log_step("2/9 Extração SQL")
        raw=load_raw_data(engine,cfg)

        log_step("3/9 Preparação das bases")
        municipal_ref=prepare_municipal_reference(raw["municipalities"],raw["population"],municipal_geo); regional=prepare_regional_points(raw["regional_points"],municipal_geo,cfg); hierarchy=prepare_current_hierarchy(raw["current_hierarchy"]); excluded=load_excluded_municipalities(municipal_ref,cfg); stores=prepare_stores(raw["stores"],municipal_ref); current=attach_current_hierarchy(prepare_current_poles(raw["current_poles"],cfg),hierarchy)

        log_step("4/9 Demanda híbrida e carga")
        demand,split=build_hybrid_demand_units(municipal_ref,district_ref,excluded,cfg); validate_demand_exclusivity(demand,split); stores_units=assign_stores_to_demand_units(stores,demand,split,district_geo); demand=enrich_demand_units_with_score(demand,stores_units,cfg)

        log_step("5/9 Candidatos, custos e topologia")
        candidates=build_candidate_sites(demand,cfg); anchor_seeds,anchor_audit=match_regional_anchors(regional,candidates,cfg); distance=haversine_matrix_float32(demand,candidates,cfg.distance_chunk_size); cost=build_service_cost_matrix(demand,candidates,distance,cfg); unit_geo=build_hybrid_unit_geometry(demand,municipal_geo,district_geo); territorial_audit_base=build_territorial_geometry_audit(unit_geo,municipal_geo,demand,cfg); neighbors=build_adjacency_graph(unit_geo,len(demand),cfg); cost=apply_cross_uf_frontier_constraint(cost,demand,candidates,neighbors)
        if cfg.require_topology_for_v3:
            geo_idx=set(unit_geo["DEMAND_IDX"].astype(int)); missing=demand[demand["EH_UNIDADE_ESTRATEGICA"] & ~demand["DEMAND_IDX"].isin(geo_idx)]
            if not missing.empty: raise RuntimeError(f"{len(missing)} unidades estratégicas sem geometria. Corrija a malha antes de rodar a V3.")

        log_step("6/9 Estrutura atual e baseline")
        current=attach_current_pole_reference(current,demand); current_portfolio=build_current_manager_portfolio(current,stores_units,demand,run_id); baseline=build_current_baseline(current,demand,stores_units,run_id)

        all_s=[]; all_m=[]; all_a=[]; all_l=[]; all_t=[]; all_d=[]; all_p=[]; all_gr=[]; all_ha=[]; all_hr=[]; all_ta=[]; all_na=[]
        log_step("7/9 Cenários")
        for n in cfg.manager_scenarios:
            logging.info("Cenário %s gerências",n); result=solve_scenario(demand,candidates,distance,cost,neighbors,n,cfg,run_id,anchor_seeds,anchor_audit); sid=str(result["scenario"].iloc[0]["CENARIO_ID"]); result["managers"],result["regional_links"]=attach_manager_regional_links(result["managers"],regional,result["regional_anchors"]); result["assignments"]=attach_proposed_hierarchy_to_assignments(result["assignments"],result["managers"]); validate_assignment_output(result["assignments"],result["managers"],cfg); result["hierarchy_area"],result["hierarchy_regional"]=build_hierarchy_outputs(current_portfolio,result["managers"],result["assignments"],run_id,sid); solution_audit=build_solution_constraint_audit(result["scenario"],result["managers"],result["assignments"],result["regional_anchors"],cfg); result["territorial_audit"]=pd.concat([territorial_audit_base,solution_audit],ignore_index=True,sort=False).assign(RUN_ID=run_id,CENARIO_ID=sid); result["unattended"]=result["assignments"][~result["assignments"]["ATENDIDA"]].copy(); prop_stores=assign_stores_to_proposed_managers(stores_units,result["assignments"],run_id,sid); transition=compare_current_and_proposed(current_portfolio,result["managers"],run_id,sid)
            result["scenario"]["QTD_ANCORAS_GR"]=int(result["regional_anchors"]["COD_GER_REG"].nunique()); result["scenario"]["MAIOR_DISTANCIA_ANCORA_KM"]=float(result["regional_anchors"]["DISTANCIA_GR_POLO_KM"].max()); result["scenario"]["QTD_REFORCOS_GR"]=int((~result["regional_links"]["EH_ANCORA_GR"]).sum())
            if not transition.empty:
                for status,count in transition["STATUS_TRANSICAO"].value_counts().items(): result["scenario"][f"TRANSICAO_{status}"]=int(count)
            save_scenario_files(cfg,result,prop_stores,current_portfolio,transition,unit_geo); all_s.append(result["scenario"]); all_m.append(result["managers"]); all_a.append(result["assignments"]); all_l.append(prop_stores); all_t.append(transition); all_d.append(result["diagnostics"]); all_p.append(result["pole_audit"]); all_gr.append(result["regional_links"]); all_ha.append(result["hierarchy_area"]); all_hr.append(result["hierarchy_regional"]); all_ta.append(result["territorial_audit"]); all_na.append(result["unattended"])

        scenario_df=pd.concat(all_s,ignore_index=True,sort=False); manager_df=pd.concat(all_m,ignore_index=True,sort=False); assignment_df=pd.concat(all_a,ignore_index=True,sort=False); store_df=pd.concat(all_l,ignore_index=True,sort=False); transition_df=pd.concat(all_t,ignore_index=True,sort=False); diagnostic_df=pd.concat(all_d,ignore_index=True,sort=False); pole_audit_df=pd.concat(all_p,ignore_index=True,sort=False); regional_link_df=pd.concat(all_gr,ignore_index=True,sort=False); hierarchy_area_df=pd.concat(all_ha,ignore_index=True,sort=False); hierarchy_regional_df=pd.concat(all_hr,ignore_index=True,sort=False); territorial_audit_df=pd.concat(all_ta,ignore_index=True,sort=False); unattended_df=pd.concat(all_na,ignore_index=True,sort=False); comparison=pd.concat([baseline,scenario_df],ignore_index=True,sort=False)
        execution.update({"DATA_FIM":datetime.now(),"STATUS":"CONCLUIDO","QTD_UNIDADES":len(demand),"QTD_MUNICIPIOS":int(demand["COD_IBGE"].nunique()),"QTD_UNIDADES_PEQUENAS":int(demand["EH_MUNICIPIO_PEQUENO"].sum()),"QTD_LOJAS":len(stores_units),"QTD_CANDIDATOS":len(candidates),"QTD_POLOS_ATUAIS":len(current_portfolio),"QTD_GR":len(regional),"QTD_ANCORAS_GR":len(anchor_seeds),"CENARIOS":json.dumps(list(cfg.manager_scenarios)),"MENSAGEM":"V3.2 concluída com 81 âncoras GR e hierarquia proposta."})

        log_step("8/9 Persistência")
        if cfg.save_sql:
            for name,df in ((T_EXECUCAO,pd.DataFrame([execution])),(T_CENARIO,scenario_df),(T_UNIDADE,demand.assign(RUN_ID=run_id)),(T_GERENCIA_PROPOSTA,manager_df),(T_CARTEIRA_UNIDADE,assignment_df),(T_CARTEIRA_LOJA,store_df),(T_GERENCIA_ATUAL,current_portfolio),(T_TRANSICAO,transition_df),(T_DIAGNOSTICO,diagnostic_df),(T_AUDITORIA_POLOS,pole_audit_df),(T_COMPARACAO,comparison),(T_VINCULO_GR,regional_link_df),(T_HIERARQUIA_PROPOSTA,hierarchy_regional_df),(T_COMPARACAO_HIERARQUIA,hierarchy_area_df),(T_AUDITORIA_TERRITORIAL,territorial_audit_df),(T_NAO_ATENDIDO,unattended_df)):
                write_sql_table(engine,df,name,sql,cfg)
        if cfg.save_excel:
            out=cfg.output_dir/f"resumo_greenfield_v3_{run_id}.xlsx"
            with pd.ExcelWriter(out,engine="openpyxl") as w:
                pd.DataFrame([execution]).to_excel(w,sheet_name="execucao",index=False); comparison.to_excel(w,sheet_name="comparacao_cenarios",index=False); scenario_df.to_excel(w,sheet_name="cenarios",index=False); manager_df.to_excel(w,sheet_name="gerencias_propostas",index=False); transition_df.to_excel(w,sheet_name="transicoes",index=False); hierarchy_area_df.to_excel(w,sheet_name="hierarquia_areas",index=False); hierarchy_regional_df.to_excel(w,sheet_name="hierarquia_regionais",index=False); regional_link_df.to_excel(w,sheet_name="vinculo_gr_polo",index=False); unattended_df.to_excel(w,sheet_name="nao_atendidos",index=False); territorial_audit_df.to_excel(w,sheet_name="auditoria_territorial",index=False); diagnostic_df.to_excel(w,sheet_name="diagnosticos",index=False); pole_audit_df.to_excel(w,sheet_name="auditoria_polos",index=False)
            logging.info("Resumo salvo: %s",out)

        log_step("9/9 CONCLUÍDO")
        logging.info("RUN_ID=%s | saída=%s",run_id,cfg.output_dir)
    except Exception as exc:
        execution.update({"DATA_FIM":datetime.now(),"STATUS":"ERRO","MENSAGEM":str(exc)[:3000]}); logging.error("Falha: %s",exc); logging.debug(traceback.format_exc())
        try:
            if cfg.save_sql: write_sql_table(engine,pd.DataFrame([execution]),T_EXECUCAO,sql,cfg)
        finally: raise


if __name__ == "__main__":
    main()
