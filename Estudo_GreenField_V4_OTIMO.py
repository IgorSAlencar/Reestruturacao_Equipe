"""
GREENFIELD DA MALHA DE GERENCIAS - V4
=====================================

Modelo de localizacao-alocacao capacitado, contiguo e lexicografico.

Principios da V4:
- exatamente 135 polos;
- 81 GRs ancoradas 1:1 a polos da mesma area, em ate 100 km;
- municipios com 300 mil habitantes ou mais substituidos por distritos;
- municipios/distritos obrigatorios e pequenos municipios opcionais;
- cobertura de ao menos 95% das lojas fora da lista de exclusao;
- nenhum peso arbitrario entre populacao e lojas;
- menor desequilibrio possivel, depois habitante-km, depois loja-km;
- contiguidade certificada por cortes validos adicionados ao modelo SCIP;
- realocacao dos 135 gerentes somente depois da malha operacional:
  minimiza primeiro a pior mudanca individual e depois a soma das mudancas.

Dependencias adicionais da V4:
    pip install pyscipopt

O arquivo V3 permanece inalterado e e usado somente como biblioteca para
extracao, normalizacao e geometria. Nenhuma heuristica de carga da V3 entra
na funcao objetivo da V4.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sqlalchemy import text
from sqlalchemy.engine import Engine


def load_local_env(path: Path) -> None:
    """Carrega pares simples CHAVE=VALOR sem sobrescrever o ambiente do processo."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent
load_local_env(BASE_DIR / ".env")

import Estudo_GreenField_V3_COMPLETO as v3

try:
    from pyscipopt import Model, quicksum
except (ImportError, ModuleNotFoundError):  # mensagem amigavel em require_solver()
    Model = None
    quicksum = None


MODEL_VERSION = "V4.0_SCIP_LEXICOGRAFICO_CONTIGUO"

T_EXECUCAO = "TB_GREENFIELD_BE_EXECUCAO_V4_IGOR"
T_CENARIO = "TB_GREENFIELD_BE_CENARIO_V4_IGOR"
T_UNIDADE = "TB_GREENFIELD_BE_UNIDADE_V4_IGOR"
T_GERENCIA = "TB_GREENFIELD_BE_GERENCIA_PROPOSTA_V4_IGOR"
T_CARTEIRA = "TB_GREENFIELD_BE_CARTEIRA_UNIDADE_V4_IGOR"
T_LOJA = "TB_GREENFIELD_BE_CARTEIRA_LOJA_V4_IGOR"
T_GERENCIA_ATUAL = "TB_GREENFIELD_BE_GERENCIA_ATUAL_V4_IGOR"
T_REALOCACAO = "TB_GREENFIELD_BE_REALOCACAO_V4_IGOR"
T_OBJETIVO = "TB_GREENFIELD_BE_OBJETIVO_V4_IGOR"
T_ANCORA = "TB_GREENFIELD_BE_ANCORA_GR_V4_IGOR"
T_VINCULO_GR = "TB_GREENFIELD_BE_VINCULO_GR_POLO_V4_IGOR"
T_AUDITORIA = "TB_GREENFIELD_BE_AUDITORIA_V4_IGOR"
T_AUDITORIA_TERRITORIAL = "TB_GREENFIELD_BE_AUDITORIA_TERRITORIAL_V4_IGOR"
T_EXCLUSAO = "TB_GREENFIELD_BE_EXCLUSAO_V4_IGOR"
T_NAO_ATENDIDO = "TB_GREENFIELD_BE_NAO_ATENDIDO_V4_IGOR"
T_SALDO_REGIONAL = "TB_GREENFIELD_BE_SALDO_REGIONAL_V4_IGOR"
T_FLUXO_REALOCACAO = "TB_GREENFIELD_BE_FLUXO_REALOCACAO_V4_IGOR"


@dataclass(frozen=True)
class V4Config(v3.ModelConfig):
    manager_count: int = 135
    mandatory_population_min: int = 30_000
    optional_service_radius_km: float = 150.0
    minimum_store_coverage: float = 0.95
    expected_excluded_municipalities: int = 484
    excluded_municipalities_table: str = os.getenv("EXCLUDED_MUNICIPALITIES_TABLE", "")
    excluded_municipalities_sql_column: str = os.getenv(
        "EXCLUDED_MUNICIPALITIES_COLUMN", "CD_MUNIC"
    )

    time_limit_seconds: int = int(os.getenv("GREENFIELD_V4_TIME_LIMIT", "7200"))
    mip_gap: float = float(os.getenv("GREENFIELD_V4_MIP_GAP", "0.005"))
    random_seed: int = int(os.getenv("GREENFIELD_V4_SEED", "20260812"))
    lock_relative_tolerance: float = 1e-7
    balance_time_share: float = 0.45
    population_distance_time_share: float = 0.35
    store_distance_time_share: float = 0.20
    solver_threads: int = int(os.getenv("GREENFIELD_V4_THREADS", "1"))

    output_dir: Path = Path(
        os.getenv("OUTPUT_DIR_V4", str(BASE_DIR / "saida_greenfield_v4"))
    )
    model_version: str = MODEL_VERSION


@dataclass
class ModelBundle:
    model: Any
    y: dict[int, Any]
    x: dict[tuple[int, int], Any]
    anchor: dict[tuple[int, int], Any]
    balance: Any
    population_distance: Any
    store_distance: Any
    population_load: dict[int, Any]
    store_load: dict[int, Any]
    feasible_by_unit: list[np.ndarray]
    feasible_by_candidate: list[list[int]]
    anchor_pairs_by_regional: list[np.ndarray]
    distance: np.ndarray
    cut_count: int = 0


@dataclass
class SolvedState:
    selected: list[int]
    assignment: np.ndarray
    anchor_candidate: dict[int, int]
    solution: Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GreenField V4: localizacao-alocacao lexicografica com SCIP."
    )
    parser.add_argument("--periodo", type=int, help="Periodo YYYYMM das lojas ativas.")
    parser.add_argument(
        "--time-limit",
        type=int,
        default=None,
        help="Limite global em segundos (padrao: 7200).",
    )
    parser.add_argument(
        "--mip-gap", type=float, default=None, help="Gap relativo alvo (padrao: 0.005)."
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Semente deterministica do SCIP."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Diretorio de saida da V4."
    )
    parser.add_argument(
        "--sem-sql",
        action="store_true",
        help="Nao persistir as tabelas V4 no SQL Server.",
    )
    parser.add_argument(
        "--sem-excel", action="store_true", help="Nao gerar workbooks Excel."
    )
    parser.add_argument(
        "--sem-geojson", action="store_true", help="Nao gerar arquivos GeoJSON."
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> V4Config:
    cfg = V4Config()
    updates: dict[str, Any] = {}
    if args.periodo is not None:
        updates["periodo_lojas"] = args.periodo
    if args.time_limit is not None:
        updates["time_limit_seconds"] = args.time_limit
    if args.mip_gap is not None:
        updates["mip_gap"] = args.mip_gap
    if args.seed is not None:
        updates["random_seed"] = args.seed
    if args.output_dir is not None:
        updates["output_dir"] = args.output_dir.resolve()
    if args.sem_sql:
        updates["save_sql"] = False
    if args.sem_excel:
        updates["save_excel"] = False
    if args.sem_geojson:
        updates["save_geojson"] = False
    cfg = replace(cfg, **updates)
    if cfg.manager_count != 135:
        raise ValueError("A V4 exige exatamente 135 polos.")
    if cfg.time_limit_seconds <= 0:
        raise ValueError("--time-limit deve ser positivo.")
    if not 0 <= cfg.mip_gap < 1:
        raise ValueError("--mip-gap deve estar no intervalo [0, 1).")
    if not np.isclose(
        cfg.balance_time_share
        + cfg.population_distance_time_share
        + cfg.store_distance_time_share,
        1.0,
    ):
        raise ValueError("As parcelas de tempo dos objetivos devem somar 1.")
    return cfg


def require_solver() -> None:
    if Model is None or quicksum is None:
        raise RuntimeError(
            "PySCIPOpt nao esta instalado. Instale com 'pip install pyscipopt' "
            "antes de executar Estudo_GreenField_V4_OTIMO.py."
        )


def _sql_identifier(value: str, label: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{label} SQL invalido: {value!r}")
    return f"[{value}]"


def _qualified_sql_table(value: str) -> str:
    parts = [part.strip() for part in str(value).split(".") if part.strip()]
    if not 1 <= len(parts) <= 3:
        raise ValueError(
            "EXCLUDED_MUNICIPALITIES_TABLE deve ser tabela, schema.tabela ou banco.schema.tabela."
        )
    return ".".join(_sql_identifier(part, "Identificador de tabela") for part in parts)


def load_sql_exclusions(
    engine: Engine, municipal_reference: pd.DataFrame, cfg: V4Config
) -> tuple[set[str], pd.DataFrame]:
    if not cfg.excluded_municipalities_table.strip():
        raise RuntimeError(
            "Defina EXCLUDED_MUNICIPALITIES_TABLE no .env com a tabela dos 484 municipios excluidos."
        )
    table = _qualified_sql_table(cfg.excluded_municipalities_table)
    column = _sql_identifier(
        cfg.excluded_municipalities_sql_column, "Coluna de exclusao"
    )
    raw = v3.uppercase_columns(
        pd.read_sql(text(f"SELECT {column} AS CD_MUNIC FROM {table}"), engine)
    )
    if "CD_MUNIC" not in raw.columns:
        raise RuntimeError("A consulta de exclusoes nao retornou CD_MUNIC.")
    mapped = v3.map_to_ibge(raw["CD_MUNIC"], municipal_reference)
    invalid = (
        raw.loc[raw["CD_MUNIC"].notna() & mapped.isna(), "CD_MUNIC"]
        .astype(str)
        .unique()
        .tolist()
    )
    if invalid:
        raise RuntimeError(
            f"Codigos de exclusao invalidos ou fora da referencia IBGE: {invalid[:20]}"
        )
    codes = set(mapped.dropna().astype(str))
    if len(codes) != cfg.expected_excluded_municipalities:
        raise RuntimeError(
            f"Esperados exatamente {cfg.expected_excluded_municipalities} municipios excluidos; "
            f"a tabela retornou {len(codes)} codigos unicos validos."
        )
    audit = municipal_reference[municipal_reference["COD_IBGE"].isin(codes)][
        ["COD_IBGE", "NM_MUN", "UF", "POPULACAO_MUNICIPIO"]
    ].copy()
    audit["ORIGEM_EXCLUSAO"] = cfg.excluded_municipalities_table
    return codes, audit.sort_values("COD_IBGE").reset_index(drop=True)


def validate_metropolitan_districts(
    municipal_reference: pd.DataFrame,
    district_reference: pd.DataFrame,
    district_geo: gpd.GeoDataFrame | None,
    excluded: set[str],
    cfg: V4Config,
) -> None:
    metro = municipal_reference[
        (municipal_reference["POPULACAO_MUNICIPIO"] >= cfg.large_city_threshold)
        & ~municipal_reference["COD_IBGE"].isin(excluded)
    ]
    expected = set(metro["COD_IBGE"].astype(str))
    available = set(district_reference["CD_MUN"].dropna().astype(str))
    missing = sorted(expected - available)
    if missing:
        raise RuntimeError(
            "Municipios >=300 mil sem dados distritais; a V4 nao permite agrega-los: "
            + ", ".join(missing[:30])
        )
    if district_geo is None:
        raise RuntimeError(
            "A malha distrital e obrigatoria para municipios com 300 mil habitantes ou mais."
        )
    geo_codes = set(district_geo["CD_MUN"].dropna().astype(str))
    missing_geo = sorted(expected - geo_codes)
    if missing_geo:
        raise RuntimeError(
            "Municipios >=300 mil sem geometria distrital: "
            + ", ".join(missing_geo[:30])
        )


def prepare_v4_demand(
    municipal_reference: pd.DataFrame,
    district_reference: pd.DataFrame,
    district_geo: gpd.GeoDataFrame | None,
    stores: pd.DataFrame,
    excluded: set[str],
    cfg: V4Config,
) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    validate_metropolitan_districts(
        municipal_reference, district_reference, district_geo, excluded, cfg
    )
    demand, split = v3.build_hybrid_demand_units(
        municipal_reference, district_reference, excluded, cfg
    )
    v3.validate_demand_exclusivity(demand, split)

    expected_metro = set(
        municipal_reference.loc[
            (municipal_reference["POPULACAO_MUNICIPIO"] >= cfg.large_city_threshold)
            & ~municipal_reference["COD_IBGE"].isin(excluded),
            "COD_IBGE",
        ].astype(str)
    )
    emitted_as_municipality = set(
        demand.loc[
            demand["COD_IBGE"].isin(expected_metro)
            & demand["TIPO_UNIDADE"].eq("MUNICIPIO"),
            "COD_IBGE",
        ].astype(str)
    )
    if emitted_as_municipality:
        raise RuntimeError(
            "Metropoles emitidas indevidamente como municipio agregado: "
            + ", ".join(sorted(emitted_as_municipality))
        )

    stores_by_unit = v3.assign_stores_to_demand_units(
        stores, demand, split, district_geo
    )
    store_count = (
        stores_by_unit.groupby("DEMAND_ID")["CHAVE_LOJA"].nunique().rename("QTD_LOJAS")
        if not stores_by_unit.empty
        else pd.Series(dtype="int64", name="QTD_LOJAS")
    )
    demand = demand.merge(store_count, on="DEMAND_ID", how="left")
    demand["QTD_LOJAS"] = demand["QTD_LOJAS"].fillna(0).astype(int)
    demand["ATENDIMENTO_OBRIGATORIO"] = demand["TIPO_UNIDADE"].eq("DISTRITO") | (
        demand["POPULACAO_UNIDADE"] >= cfg.mandatory_population_min
    )
    demand["ATENDIMENTO_OPCIONAL"] = ~demand["ATENDIMENTO_OBRIGATORIO"]
    demand["CARGA_EQUIVALENTE"] = pd.NA
    demand["METODO_CARGA"] = "NAO_UTILIZADA_V4"

    if set(demand["COD_IBGE"].astype(str)) & excluded:
        raise RuntimeError("Municipio da tabela de exclusao apareceu na demanda V4.")
    metro_demand = demand[demand["COD_IBGE"].isin(expected_metro)]
    if not metro_demand["TIPO_UNIDADE"].eq("DISTRITO").all():
        raise RuntimeError(
            "Nem todas as unidades metropolitanas foram emitidas como distritos."
        )
    if not metro_demand["ATENDIMENTO_OBRIGATORIO"].all():
        raise RuntimeError(
            "Todo distrito metropolitano deve ter atendimento obrigatorio."
        )

    return demand.reset_index(drop=True), stores_by_unit.reset_index(drop=True), split


def graph_component_index(neighbors: dict[int, set[int]], count: int) -> np.ndarray:
    component = np.full(count, -1, dtype=int)
    for component_id, nodes in enumerate(v3.graph_components(neighbors, count)):
        component[np.fromiter(nodes, dtype=int)] = component_id
    if np.any(component < 0):
        raise RuntimeError("Falha ao identificar componentes do grafo territorial.")
    return component


def build_state_adjacency(
    demand: pd.DataFrame, neighbors: dict[int, set[int]]
) -> dict[str, set[str]]:
    unit_uf = demand["UF"].astype(str).to_numpy()
    adjacency = {uf: {uf} for uf in sorted(set(unit_uf))}
    for unit, adjacent_units in neighbors.items():
        source_uf = unit_uf[int(unit)]
        for adjacent in adjacent_units:
            target_uf = unit_uf[int(adjacent)]
            adjacency[source_uf].add(target_uf)
            adjacency[target_uf].add(source_uf)
    return adjacency


def build_official_state_adjacency(
    municipal_geo: gpd.GeoDataFrame, cfg: V4Config
) -> dict[str, set[str]]:
    municipalities = municipal_geo[["CD_MUN", "geometry"]].copy()
    municipalities["UF"] = (
        municipalities["CD_MUN"].astype(str).str[:2].map(v3.UF_POR_CODIGO)
    )
    municipalities = municipalities.dropna(subset=["UF"]).reset_index(drop=True)
    adjacency = {
        str(uf): {str(uf)} for uf in sorted(municipalities["UF"].astype(str).unique())
    }
    contacts = gpd.sjoin(
        municipalities[["UF", "geometry"]],
        municipalities[["UF", "geometry"]],
        how="inner",
        predicate="intersects",
        lsuffix="ORIGEM",
        rsuffix="DESTINO",
    )
    metric = municipalities.to_crs(epsg=5880)
    for left_index, row in contacts.iterrows():
        left_uf = str(row.UF_ORIGEM)
        right_uf = str(row.UF_DESTINO)
        if left_uf != right_uf and right_uf not in adjacency[left_uf]:
            right_index = int(row["index_DESTINO"])
            try:
                shared_boundary_m = float(
                    metric.loc[left_index]
                    .geometry.boundary.intersection(
                        metric.loc[right_index].geometry.boundary
                    )
                    .length
                )
            except Exception:
                shared_boundary_m = 0.0
            if shared_boundary_m < cfg.min_shared_boundary_m:
                continue
            adjacency[left_uf].add(right_uf)
            adjacency[right_uf].add(left_uf)
    if len(adjacency) != len(v3.UF_POR_CODIGO):
        missing = sorted(set(v3.UF_POR_CODIGO.values()) - set(adjacency))
        raise RuntimeError(
            f"Nao foi possivel derivar fronteiras para todas as UFs: {missing}"
        )
    logging.info(
        "Adjacencia oficial de UFs: %s fronteiras",
        sum(len(values) - 1 for values in adjacency.values()) // 2,
    )
    return adjacency


def build_v4_candidates(demand: pd.DataFrame, cfg: V4Config) -> pd.DataFrame:
    candidates = v3.build_candidate_sites(demand, cfg).copy()
    candidates["PENALIDADE_SEDE_KM_EQ"] = 0.0
    candidates["METODO_SELECAO_V4"] = "VARIAVEL_BINARIA_SCIP"
    if len(candidates) < cfg.manager_count:
        raise RuntimeError(
            f"Ha somente {len(candidates)} candidatos para {cfg.manager_count} polos obrigatorios."
        )
    if candidates["DEMAND_IDX_ORIGEM_POLO"].duplicated().any():
        raise RuntimeError(
            "Mais de um candidato foi criado para a mesma unidade territorial."
        )
    return candidates.reset_index(drop=True)


def build_feasible_assignment_pairs(
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    distance: np.ndarray,
    neighbors: dict[int, set[int]],
    cfg: V4Config,
    state_adjacency: dict[str, set[str]] | None = None,
) -> tuple[list[np.ndarray], list[list[int]], np.ndarray]:
    component = graph_component_index(neighbors, len(demand))
    candidate_root = candidates["DEMAND_IDX_ORIGEM_POLO"].to_numpy(int)
    candidate_component = component[candidate_root]
    candidate_uf = candidates["UF"].astype(str).to_numpy()
    demand_uf = demand["UF"].astype(str).to_numpy()
    mandatory = demand["ATENDIMENTO_OBRIGATORIO"].to_numpy(bool)

    state_adjacency = state_adjacency or build_state_adjacency(demand, neighbors)
    allowed_ufs = [state_adjacency[demand_uf[unit]] for unit in range(len(demand))]

    by_unit: list[np.ndarray] = []
    by_candidate: list[list[int]] = [[] for _ in range(len(candidates))]
    for unit in range(len(demand)):
        mask = candidate_component == component[unit]
        mask &= np.isin(candidate_uf, list(allowed_ufs[unit]))
        if not mandatory[unit]:
            mask &= distance[unit] <= cfg.optional_service_radius_km + 1e-7
        feasible = np.flatnonzero(mask).astype(int)
        if mandatory[unit] and not len(feasible):
            raise RuntimeError(
                f"Unidade obrigatoria {demand.iloc[unit]['DEMAND_ID']} sem polo candidato viavel."
            )
        by_unit.append(feasible)
        for candidate in feasible:
            by_candidate[int(candidate)].append(unit)

    for candidate, root in enumerate(candidate_root):
        if candidate not in set(by_unit[int(root)].tolist()):
            raise RuntimeError(
                f"O candidato {candidate} nao pode atender sua propria unidade-sede."
            )

    component_candidates = defaultdict(int)
    for value in candidate_component:
        component_candidates[int(value)] += 1
    for component_id in np.unique(
        component[demand["ATENDIMENTO_OBRIGATORIO"].to_numpy(bool)]
    ):
        if component_candidates[int(component_id)] == 0:
            raise RuntimeError(
                f"Componente territorial obrigatorio {component_id} sem candidato a polo."
            )

    total_stores = int(demand["QTD_LOJAS"].sum())
    potentially_served = int(
        demand.loc[
            demand["ATENDIMENTO_OBRIGATORIO"]
            | pd.Series([len(values) > 0 for values in by_unit], index=demand.index),
            "QTD_LOJAS",
        ].sum()
    )
    required = int(np.ceil(cfg.minimum_store_coverage * total_stores - 1e-9))
    if potentially_served < required:
        raise RuntimeError(
            f"Cobertura de {cfg.minimum_store_coverage:.1%} inviavel: no maximo {potentially_served} "
            f"de {total_stores} lojas possuem atribuicao territorial elegivel."
        )
    return by_unit, by_candidate, component


def build_anchor_pairs(
    regional: pd.DataFrame,
    candidates: pd.DataFrame,
    cfg: V4Config,
) -> tuple[list[np.ndarray], np.ndarray]:
    distance = v3.pairwise_haversine_matrix(
        regional, candidates, "LATITUDE", "LONGITUDE", "LATITUDE", "LONGITUDE"
    )
    same_area = (
        regional["DESC_GERENCIA_AREA_GR"].astype(str).to_numpy()[:, None]
        == candidates["DESC_GERENCIA_AREA_PROPOSTA"].astype(str).to_numpy()[None, :]
    )
    feasible_mask = same_area & (distance <= cfg.regional_anchor_radius_km + 1e-7)
    by_regional = [
        np.flatnonzero(feasible_mask[row]).astype(int) for row in range(len(regional))
    ]
    missing = [row for row, values in enumerate(by_regional) if not len(values)]
    if missing:
        details = regional.iloc[missing][
            ["COD_GER_REG", "GER_REGIONAL", "UF_GR"]
        ].to_dict("records")
        raise RuntimeError(
            f"GRs sem candidato da mesma area em ate 100 km: {details[:20]}"
        )

    # Pre-validacao Hall por matching bipartido. O pareamento definitivo continua
    # dentro do MIP e, portanto, participa conjuntamente da escolha da malha.
    matching_cost = np.where(feasible_mask, distance, 1e9)
    rows, cols = linear_sum_assignment(matching_cost)
    if len(rows) != len(regional) or np.any(~feasible_mask[rows, cols]):
        raise RuntimeError(
            "Nao existe pareamento 1:1 viavel entre as 81 GRs e candidatos distintos."
        )
    return by_regional, distance


def _set_scip_param(model: Any, name: str, value: Any) -> None:
    try:
        model.setParam(name, value)
    except Exception as exc:
        logging.warning("Parametro SCIP ignorado (%s=%s): %s", name, value, exc)


def configure_scip(model: Any, cfg: V4Config) -> None:
    _set_scip_param(model, "limits/gap", cfg.mip_gap)
    _set_scip_param(model, "randomization/randomseedshift", cfg.random_seed)
    _set_scip_param(model, "randomization/permutationseed", cfg.random_seed)
    _set_scip_param(model, "parallel/maxnthreads", cfg.solver_threads)
    _set_scip_param(model, "display/verblevel", 4)


def build_optimization_model(
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    regional: pd.DataFrame,
    distance: np.ndarray,
    neighbors: dict[int, set[int]],
    cfg: V4Config,
    state_adjacency: dict[str, set[str]] | None = None,
) -> ModelBundle:
    require_solver()
    feasible_by_unit, feasible_by_candidate, _ = build_feasible_assignment_pairs(
        demand, candidates, distance, neighbors, cfg, state_adjacency
    )
    anchor_pairs, _ = build_anchor_pairs(regional, candidates, cfg)

    model = Model("GREENFIELD_V4")
    configure_scip(model, cfg)
    candidate_count = len(candidates)
    unit_count = len(demand)

    y = {
        candidate: model.addVar(vtype="B", name=f"y_{candidate}")
        for candidate in range(candidate_count)
    }
    x: dict[tuple[int, int], Any] = {}
    for unit, feasible in enumerate(feasible_by_unit):
        for candidate in feasible:
            candidate = int(candidate)
            x[(unit, candidate)] = model.addVar(vtype="B", name=f"x_{unit}_{candidate}")

    anchor: dict[tuple[int, int], Any] = {}
    for regional_idx, feasible in enumerate(anchor_pairs):
        for candidate in feasible:
            candidate = int(candidate)
            anchor[(regional_idx, candidate)] = model.addVar(
                vtype="B", name=f"a_{regional_idx}_{candidate}"
            )

    model.addCons(
        quicksum(y.values()) == cfg.manager_count, name="EXATAMENTE_135_POLOS"
    )

    mandatory = demand["ATENDIMENTO_OBRIGATORIO"].to_numpy(bool)
    for unit, feasible in enumerate(feasible_by_unit):
        expression = quicksum(x[(unit, int(candidate))] for candidate in feasible)
        if mandatory[unit]:
            model.addCons(expression == 1, name=f"ATENDIMENTO_OBRIGATORIO_{unit}")
        else:
            model.addCons(expression <= 1, name=f"ATENDIMENTO_OPCIONAL_{unit}")

    candidate_root = candidates["DEMAND_IDX_ORIGEM_POLO"].to_numpy(int)
    for candidate, units in enumerate(feasible_by_candidate):
        # Formulacao forte padrao de p-mediana. O vinculo agregado
        # sum(x) <= M*y seria correto para inteiros, mas produziria um limite
        # linear muito fraco e um gap artificialmente alto na escala nacional.
        for unit in units:
            model.addCons(x[(unit, candidate)] <= y[candidate])
        root = int(candidate_root[candidate])
        model.addCons(
            x[(root, candidate)] == y[candidate], name=f"POLO_ATENDE_SEDE_{candidate}"
        )

    for regional_idx, feasible in enumerate(anchor_pairs):
        model.addCons(
            quicksum(anchor[(regional_idx, int(candidate))] for candidate in feasible)
            == 1,
            name=f"UMA_ANCORA_POR_GR_{regional_idx}",
        )
    anchor_by_candidate: dict[int, list[Any]] = defaultdict(list)
    for (regional_idx, candidate), variable in anchor.items():
        anchor_by_candidate[candidate].append(variable)
        model.addCons(variable <= y[candidate])
    for candidate, variables in anchor_by_candidate.items():
        model.addCons(
            quicksum(variables) <= y[candidate],
            name=f"ANCORA_DISTINTA_{candidate}",
        )

    stores = demand["QTD_LOJAS"].to_numpy(float)
    populations = demand["POPULACAO_UNIDADE"].to_numpy(float)
    total_stores = float(stores.sum())
    total_population = float(populations.sum())
    if total_stores <= 0 or total_population <= 0:
        raise RuntimeError(
            "Populacao e quantidade de lojas devem ser positivas para otimizar a V4."
        )

    served_store_expression = quicksum(
        float(stores[unit]) * x[(unit, int(candidate))]
        for unit, feasible in enumerate(feasible_by_unit)
        for candidate in feasible
        if stores[unit] > 0
    )
    model.addCons(
        served_store_expression >= cfg.minimum_store_coverage * total_stores,
        name="COBERTURA_MINIMA_95_PORCENTO_LOJAS",
    )

    population_load: dict[int, Any] = {}
    store_load: dict[int, Any] = {}
    for candidate, units in enumerate(feasible_by_candidate):
        population_load[candidate] = quicksum(
            float(populations[unit]) * x[(unit, candidate)] for unit in units
        )
        store_load[candidate] = quicksum(
            float(stores[unit]) * x[(unit, candidate)] for unit in units
        )

    balance = model.addVar(
        lb=0.0,
        ub=float(cfg.manager_count),
        vtype="C",
        name="DESVIO_MAX_RELATIVO_A_MEDIA",
    )

    served_population_average = quicksum(population_load.values()) / cfg.manager_count
    served_store_average = quicksum(store_load.values()) / cfg.manager_count
    population_scale = total_population / cfg.manager_count
    store_scale = total_stores / cfg.manager_count
    for candidate in range(candidate_count):
        model.addCons(
            population_load[candidate] - served_population_average
            <= population_scale * balance + total_population * (1 - y[candidate])
        )
        model.addCons(
            served_population_average - population_load[candidate]
            <= population_scale * balance + total_population * (1 - y[candidate])
        )
        model.addCons(
            store_load[candidate] - served_store_average
            <= store_scale * balance + total_stores * (1 - y[candidate])
        )
        model.addCons(
            served_store_average - store_load[candidate]
            <= store_scale * balance + total_stores * (1 - y[candidate])
        )

    population_distance = quicksum(
        (float(populations[unit]) / total_population)
        * float(distance[unit, int(candidate)])
        * x[(unit, int(candidate))]
        for unit, feasible in enumerate(feasible_by_unit)
        for candidate in feasible
        if populations[unit] > 0
    )
    store_distance = quicksum(
        (float(stores[unit]) / total_stores)
        * float(distance[unit, int(candidate)])
        * x[(unit, int(candidate))]
        for unit, feasible in enumerate(feasible_by_unit)
        for candidate in feasible
        if stores[unit] > 0
    )

    logging.info(
        "Modelo V4: %s unidades | %s candidatos | %s atribuicoes binarias | %s pares de ancora",
        unit_count,
        candidate_count,
        len(x),
        len(anchor),
    )
    return ModelBundle(
        model=model,
        y=y,
        x=x,
        anchor=anchor,
        balance=balance,
        population_distance=population_distance,
        store_distance=store_distance,
        population_load=population_load,
        store_load=store_load,
        feasible_by_unit=feasible_by_unit,
        feasible_by_candidate=feasible_by_candidate,
        anchor_pairs_by_regional=anchor_pairs,
        distance=distance,
    )


def add_contiguous_warm_start(
    bundle: ModelBundle,
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    regional: pd.DataFrame,
    neighbors: dict[int, set[int]],
    cfg: V4Config,
) -> bool:
    """Entrega ao SCIP uma solucao inicial viavel; nao altera os objetivos V4."""
    try:
        anchor_pairs, anchor_distance = build_anchor_pairs(regional, candidates, cfg)
        anchor_cost = np.full(anchor_distance.shape, 1e9, dtype=float)
        for regional_idx, feasible in enumerate(anchor_pairs):
            anchor_cost[regional_idx, feasible] = anchor_distance[
                regional_idx, feasible
            ]
        regional_rows, anchor_candidates = linear_sum_assignment(anchor_cost)
        if len(regional_rows) != len(regional):
            return False
        preselected = [int(candidate) for candidate in anchor_candidates]

        mandatory = demand["ATENDIMENTO_OBRIGATORIO"].to_numpy(bool)
        preselected, _ = v3.ensure_strategic_component_seeds(
            preselected,
            candidates,
            neighbors,
            mandatory,
            cfg.manager_count,
        )
        population = demand["POPULACAO_UNIDADE"].to_numpy(float)
        stores = demand["QTD_LOJAS"].to_numpy(float)
        # Normalizacao usada apenas para obter rapidamente um incumbente. Ela
        # nao entra no modelo nem em nenhum dos tres objetivos certificados.
        warm_priority = population / max(float(population.sum()), 1.0)
        warm_priority += stores / max(float(stores.sum()), 1.0)
        warm_priority = np.maximum(warm_priority, 1e-9)

        feasible_cost = np.full(
            bundle.distance.shape, v3.PROHIBITED_SERVICE_COST, dtype=np.float32
        )
        for unit, feasible in enumerate(bundle.feasible_by_unit):
            feasible_cost[unit, feasible] = bundle.distance[unit, feasible]
        selected = v3.greedy_p_median_v3(
            feasible_cost,
            warm_priority,
            np.zeros(len(candidates), dtype=float),
            cfg.manager_count,
            cfg.distance_chunk_size,
            preselected,
        )
        position = v3.assign_contiguous_regions(
            feasible_cost,
            selected,
            candidates,
            neighbors,
            warm_priority,
            mandatory,
            cfg,
        )
        if np.any((position < 0) & mandatory):
            return False
        assignment = np.full(len(demand), -1, dtype=int)
        served = position >= 0
        assignment[served] = np.asarray(selected, dtype=int)[position[served]]
        coverage = float(stores[served].sum()) / max(float(stores.sum()), 1.0)
        if coverage + 1e-12 < cfg.minimum_store_coverage:
            return False

        solution = bundle.model.createSol()
        selected_set = set(selected)
        for candidate, variable in bundle.y.items():
            bundle.model.setSolVal(solution, variable, float(candidate in selected_set))
        for (unit, candidate), variable in bundle.x.items():
            bundle.model.setSolVal(
                solution, variable, float(assignment[unit] == candidate)
            )
        anchor_choice = {
            int(regional_idx): int(candidate)
            for regional_idx, candidate in zip(regional_rows, anchor_candidates)
        }
        for (regional_idx, candidate), variable in bundle.anchor.items():
            bundle.model.setSolVal(
                solution, variable, float(anchor_choice[regional_idx] == candidate)
            )

        population_loads = np.array(
            [population[assignment == candidate].sum() for candidate in selected],
            dtype=float,
        )
        store_loads = np.array(
            [stores[assignment == candidate].sum() for candidate in selected],
            dtype=float,
        )
        served_population_average = float(population_loads.mean())
        served_store_average = float(store_loads.mean())
        balance = max(
            float(np.max(np.abs(population_loads - served_population_average)))
            / max(float(population.sum()) / cfg.manager_count, 1e-9),
            float(np.max(np.abs(store_loads - served_store_average)))
            / max(float(stores.sum()) / cfg.manager_count, 1e-9),
        )
        bundle.model.setSolVal(solution, bundle.balance, balance)
        accepted = bool(bundle.model.addSol(solution, free=True))
        logging.info(
            "Warm start contiguo %s | cobertura lojas %.2f%% | equilibrio %.6f",
            "aceito" if accepted else "rejeitado",
            coverage * 100,
            balance,
        )
        return accepted
    except Exception as exc:
        logging.warning("Nao foi possivel gerar warm start contiguo: %s", exc)
        return False


def extract_solved_state(bundle: ModelBundle) -> SolvedState:
    model = bundle.model
    solution = model.getBestSol()
    if solution is None:
        raise RuntimeError("O SCIP nao encontrou nenhuma solucao inteira viavel.")
    selected = sorted(
        candidate
        for candidate, variable in bundle.y.items()
        if model.getSolVal(solution, variable) > 0.5
    )
    assignment = np.full(len(bundle.feasible_by_unit), -1, dtype=int)
    for unit, feasible in enumerate(bundle.feasible_by_unit):
        chosen = [
            int(candidate)
            for candidate in feasible
            if model.getSolVal(solution, bundle.x[(unit, int(candidate))]) > 0.5
        ]
        if len(chosen) > 1:
            raise RuntimeError(
                f"Solucao SCIP atribuiu a unidade {unit} a mais de um polo."
            )
        if chosen:
            assignment[unit] = chosen[0]
    anchor_candidate: dict[int, int] = {}
    for (regional_idx, candidate), variable in bundle.anchor.items():
        if model.getSolVal(solution, variable) > 0.5:
            if regional_idx in anchor_candidate:
                raise RuntimeError(f"GR {regional_idx} recebeu mais de uma ancora.")
            anchor_candidate[regional_idx] = candidate
    return SolvedState(
        selected=selected,
        assignment=assignment,
        anchor_candidate=anchor_candidate,
        solution=solution,
    )


def disconnected_cut_specs(
    state: SolvedState,
    candidates: pd.DataFrame,
    neighbors: dict[int, set[int]],
    bundle: ModelBundle,
) -> list[tuple[int, int, list[int]]]:
    candidate_root = candidates["DEMAND_IDX_ORIGEM_POLO"].to_numpy(int)
    specs: list[tuple[int, int, list[int]]] = []
    for candidate in state.selected:
        nodes = {int(unit) for unit in np.flatnonzero(state.assignment == candidate)}
        root = int(candidate_root[candidate])
        if root not in nodes:
            raise RuntimeError(f"Polo {candidate} nao atende sua unidade-sede {root}.")
        cluster_components = v3.components(nodes, neighbors)
        if len(cluster_components) <= 1:
            continue
        root_component = next(part for part in cluster_components if root in part)
        for part in cluster_components:
            if part is root_component or root in part:
                continue
            representative = min(part)
            boundary = sorted(
                {
                    int(adjacent)
                    for unit in part
                    for adjacent in neighbors.get(int(unit), set())
                    if adjacent not in part and (int(adjacent), candidate) in bundle.x
                }
            )
            specs.append((candidate, representative, boundary))
    return specs


def add_connectivity_cuts(
    bundle: ModelBundle,
    specs: list[tuple[int, int, list[int]]],
    stage_name: str,
) -> None:
    if not specs:
        return
    bundle.model.freeTransform()
    for candidate, representative, boundary in specs:
        rhs = quicksum(bundle.x[(unit, candidate)] for unit in boundary)
        bundle.model.addCons(
            bundle.x[(representative, candidate)] <= rhs,
            name=f"CONEXAO_{stage_name}_{bundle.cut_count}",
        )
        bundle.cut_count += 1
    logging.info("%s: adicionados %s cortes de contiguidade", stage_name, len(specs))


def _solver_metric(callable_value: Any, default: Any = np.nan) -> Any:
    try:
        return callable_value()
    except Exception:
        return default


def solve_objective_stage(
    bundle: ModelBundle,
    candidates: pd.DataFrame,
    neighbors: dict[int, set[int]],
    objective: Any,
    stage_name: str,
    deadline: float,
    cfg: V4Config,
) -> tuple[SolvedState, dict[str, Any]]:
    model = bundle.model
    model.freeTransform()
    model.setObjective(objective, "minimize")
    stage_started = time.monotonic()
    solve_calls = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.25:
            raise RuntimeError(
                f"O tempo reservado ao objetivo {stage_name} terminou antes de obter solucao contigua."
            )
        _set_scip_param(model, "limits/time", max(remaining, 0.5))
        _set_scip_param(model, "limits/gap", cfg.mip_gap)
        logging.info(
            "SCIP %s | chamada %s | tempo restante %.1fs",
            stage_name,
            solve_calls + 1,
            remaining,
        )
        model.optimize()
        solve_calls += 1
        state = extract_solved_state(bundle)
        specs = disconnected_cut_specs(state, candidates, neighbors, bundle)
        if not specs:
            break
        add_connectivity_cuts(bundle, specs, stage_name)

    objective_value = float(model.getSolVal(state.solution, objective))
    status = str(model.getStatus())
    dual_bound = float(_solver_metric(model.getDualbound))
    gap = float(_solver_metric(model.getGap))
    report = {
        "ETAPA": stage_name,
        "ORDEM_LEXICOGRAFICA": {
            "EQUILIBRIO_POPULACAO_LOJAS": 1,
            "DISTANCIA_PONDERADA_POPULACAO": 2,
            "DISTANCIA_PONDERADA_LOJAS": 3,
        }[stage_name],
        "VALOR_OBJETIVO": objective_value,
        "LIMITE_DUAL": dual_bound,
        "GAP_RELATIVO": gap,
        "STATUS_SCIP": status,
        "OTIMO_COMPROVADO": status.lower() == "optimal",
        "TEMPO_ETAPA_SEGUNDOS": time.monotonic() - stage_started,
        "TEMPO_SCIP_ACUMULADO_SEGUNDOS": float(
            _solver_metric(model.getSolvingTime, 0.0)
        ),
        "NOS_SCIP": int(_solver_metric(model.getNNodes, 0)),
        "SOLUCOES_SCIP": int(_solver_metric(model.getNSols, 0)),
        "CHAMADAS_SOLVER": solve_calls,
        "CORTES_CONTIGUIDADE_ACUMULADOS": bundle.cut_count,
    }
    logging.info(
        "SCIP %s concluido | valor %.8f | status %s | gap %.6f",
        stage_name,
        objective_value,
        status,
        gap,
    )
    return state, report


def add_objective_lock(
    model: Any, objective: Any, value: float, label: str, cfg: V4Config
) -> None:
    tolerance = cfg.lock_relative_tolerance * max(1.0, abs(value))
    model.freeTransform()
    model.addCons(objective <= value + tolerance, name=f"FIXA_OTIMO_{label}")


def solve_lexicographic(
    bundle: ModelBundle,
    candidates: pd.DataFrame,
    neighbors: dict[int, set[int]],
    cfg: V4Config,
) -> tuple[SolvedState, pd.DataFrame]:
    global_started = time.monotonic()
    balance_deadline = global_started + cfg.time_limit_seconds * cfg.balance_time_share
    population_deadline = balance_deadline + (
        cfg.time_limit_seconds * cfg.population_distance_time_share
    )
    store_deadline = global_started + cfg.time_limit_seconds

    _, balance_report = solve_objective_stage(
        bundle,
        candidates,
        neighbors,
        bundle.balance,
        "EQUILIBRIO_POPULACAO_LOJAS",
        balance_deadline,
        cfg,
    )
    add_objective_lock(
        bundle.model,
        bundle.balance,
        float(balance_report["VALOR_OBJETIVO"]),
        "EQUILIBRIO",
        cfg,
    )

    _, population_report = solve_objective_stage(
        bundle,
        candidates,
        neighbors,
        bundle.population_distance,
        "DISTANCIA_PONDERADA_POPULACAO",
        population_deadline,
        cfg,
    )
    add_objective_lock(
        bundle.model,
        bundle.population_distance,
        float(population_report["VALOR_OBJETIVO"]),
        "DISTANCIA_POPULACAO",
        cfg,
    )

    final_state, store_report = solve_objective_stage(
        bundle,
        candidates,
        neighbors,
        bundle.store_distance,
        "DISTANCIA_PONDERADA_LOJAS",
        store_deadline,
        cfg,
    )
    reports = pd.DataFrame([balance_report, population_report, store_report])
    final_objectives = [
        bundle.balance,
        bundle.population_distance,
        bundle.store_distance,
    ]
    reports["VALOR_NA_SOLUCAO_FINAL"] = [
        float(bundle.model.getSolVal(final_state.solution, objective))
        for objective in final_objectives
    ]
    reports["TEMPO_GLOBAL_SEGUNDOS"] = time.monotonic() - global_started
    return final_state, reports


def validate_final_solution(
    state: SolvedState,
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    regional: pd.DataFrame,
    anchor_distance: np.ndarray,
    neighbors: dict[int, set[int]],
    excluded: set[str],
    cfg: V4Config,
    state_adjacency: dict[str, set[str]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    def check(
        metric: str, value: Any, expected: Any, ok: bool, detail: str = ""
    ) -> None:
        rows.append(
            {
                "METRICA": metric,
                "VALOR": value,
                "ESPERADO": expected,
                "STATUS": "OK" if ok else "VIOLACAO",
                "DETALHE": detail,
            }
        )

    check(
        "QTD_POLOS",
        len(state.selected),
        cfg.manager_count,
        len(state.selected) == cfg.manager_count
        and len(set(state.selected)) == cfg.manager_count,
    )
    mandatory = demand["ATENDIMENTO_OBRIGATORIO"].to_numpy(bool)
    unserved_mandatory = int(np.sum(mandatory & (state.assignment < 0)))
    check(
        "UNIDADES_OBRIGATORIAS_NAO_ATENDIDAS",
        unserved_mandatory,
        0,
        unserved_mandatory == 0,
    )

    served = state.assignment >= 0
    assigned_distance = np.full(len(demand), np.nan)
    # A atribuicao usa indices globais de candidato; a matriz e reconstruida
    # abaixo sem depender de qualquer custo artificial.
    if served.any():
        assigned_distance[served] = v3.haversine_arrays(
            demand.loc[served, "LATITUDE"].to_numpy(float),
            demand.loc[served, "LONGITUDE"].to_numpy(float),
            candidates.iloc[state.assignment[served]]["LATITUDE"].to_numpy(float),
            candidates.iloc[state.assignment[served]]["LONGITUDE"].to_numpy(float),
        )
    optional_too_far = int(
        np.sum(
            served
            & ~mandatory
            & (assigned_distance > cfg.optional_service_radius_km + 1e-5)
        )
    )
    check("OPCIONAIS_ACIMA_150_KM", optional_too_far, 0, optional_too_far == 0)

    total_stores = int(demand["QTD_LOJAS"].sum())
    served_stores = int(demand.loc[served, "QTD_LOJAS"].sum())
    coverage = served_stores / total_stores if total_stores else 1.0
    check(
        "COBERTURA_LOJAS",
        coverage,
        f">={cfg.minimum_store_coverage}",
        coverage + 1e-12 >= cfg.minimum_store_coverage,
    )

    excluded_emitted = len(set(demand["COD_IBGE"].astype(str)) & excluded)
    check("MUNICIPIOS_EXCLUIDOS_EMITIDOS", excluded_emitted, 0, excluded_emitted == 0)

    candidate_root = candidates["DEMAND_IDX_ORIGEM_POLO"].to_numpy(int)
    disconnected = 0
    root_failures = 0
    invalid_uf = 0
    demand_uf = demand["UF"].astype(str).to_numpy()
    state_adjacency = state_adjacency or build_state_adjacency(demand, neighbors)
    for candidate in state.selected:
        nodes = {int(unit) for unit in np.flatnonzero(state.assignment == candidate)}
        root = int(candidate_root[candidate])
        root_failures += int(root not in nodes)
        disconnected += int(bool(nodes) and len(v3.components(nodes, neighbors)) != 1)
        pole_uf = str(candidates.iloc[candidate]["UF"])
        for unit in nodes:
            if demand_uf[unit] == pole_uf:
                continue
            invalid_uf += int(pole_uf not in state_adjacency[demand_uf[unit]])
    check("CARTEIRAS_DESCONTIGUAS", disconnected, 0, disconnected == 0)
    check("POLOS_SEM_DISTRITO_OU_MUNICIPIO_SEDE", root_failures, 0, root_failures == 0)
    check("ATRIBUICOES_UF_NAO_VIZINHA", invalid_uf, 0, invalid_uf == 0)

    anchor_count = len(state.anchor_candidate)
    anchor_candidates = list(state.anchor_candidate.values())
    anchor_invalid = 0
    for regional_idx, candidate in state.anchor_candidate.items():
        same_area = str(regional.iloc[regional_idx]["DESC_GERENCIA_AREA_GR"]) == str(
            candidates.iloc[candidate]["DESC_GERENCIA_AREA_PROPOSTA"]
        )
        within = (
            anchor_distance[regional_idx, candidate]
            <= cfg.regional_anchor_radius_km + 1e-5
        )
        selected_anchor = candidate in state.selected
        anchor_invalid += int(not (same_area and within and selected_anchor))
    check(
        "QTD_ANCORAS_GR",
        anchor_count,
        cfg.expected_regional_points,
        anchor_count == cfg.expected_regional_points,
    )
    check(
        "ANCORAS_REUTILIZADAS",
        anchor_count - len(set(anchor_candidates)),
        0,
        len(set(anchor_candidates)) == anchor_count,
    )
    check("ANCORAS_INVALIDAS", anchor_invalid, 0, anchor_invalid == 0)

    metro = demand[demand["POPULACAO_MUNICIPIO"] >= cfg.large_city_threshold]
    metro_aggregates = int(metro["TIPO_UNIDADE"].ne("DISTRITO").sum())
    metro_unserved = int((state.assignment[metro.index.to_numpy(int)] < 0).sum())
    check("METROPOLES_NAO_DISTRITALIZADAS", metro_aggregates, 0, metro_aggregates == 0)
    check(
        "DISTRITOS_METROPOLITANOS_NAO_ATENDIDOS", metro_unserved, 0, metro_unserved == 0
    )

    audit = pd.DataFrame(rows)
    violations = audit[audit["STATUS"] == "VIOLACAO"]
    if not violations.empty:
        raise RuntimeError(
            "Auditoria independente da solucao V4 encontrou violacoes: "
            + json.dumps(violations.to_dict("records"), ensure_ascii=False, default=str)
        )
    return audit


def manager_id(cluster: int, cfg: V4Config) -> str:
    return f"G{cfg.manager_count}_{cluster + 1:03d}"


def build_assignments(
    state: SolvedState,
    demand: pd.DataFrame,
    candidates: pd.DataFrame,
    distance: np.ndarray,
    run_id: str,
    scenario_id: str,
    cfg: V4Config,
) -> pd.DataFrame:
    selected = np.asarray(state.selected, dtype=int)
    cluster_by_candidate = {
        candidate: cluster for cluster, candidate in enumerate(state.selected)
    }
    served = state.assignment >= 0
    nearest_position = np.argmin(distance[:, selected], axis=1)
    nearest_candidate = selected[nearest_position]

    out = demand.copy()
    out.insert(0, "RUN_ID", run_id)
    out.insert(1, "CENARIO_ID", scenario_id)
    out["ATENDIDA"] = served
    out["GERENCIA_ID"] = [
        manager_id(cluster_by_candidate[int(candidate)], cfg)
        if candidate >= 0
        else pd.NA
        for candidate in state.assignment
    ]
    out["CLUSTER_IDX"] = pd.array(
        [
            cluster_by_candidate[int(candidate)] if candidate >= 0 else pd.NA
            for candidate in state.assignment
        ],
        dtype="Int64",
    )
    out["CANDIDATE_IDX"] = pd.array(
        [int(candidate) if candidate >= 0 else pd.NA for candidate in state.assignment],
        dtype="Int64",
    )

    candidate_fields = {
        "CANDIDATE_ID": "CANDIDATE_ID",
        "COD_IBGE_POLO": "COD_IBGE",
        "CD_DIST_POLO": "CD_DIST",
        "NM_MUN_POLO": "NM_MUN",
        "NM_DIST_POLO": "NM_DIST",
        "UF_POLO": "UF",
        "LATITUDE_POLO": "LATITUDE",
        "LONGITUDE_POLO": "LONGITUDE",
        "POPULACAO_SEDE_REFERENCIA": "POPULACAO_SEDE_REFERENCIA",
        "DESC_GERENCIA_AREA_PROPOSTA": "DESC_GERENCIA_AREA_PROPOSTA",
    }
    safe_candidate = np.where(served, state.assignment, nearest_candidate)
    for target, source in candidate_fields.items():
        values = candidates.iloc[safe_candidate][source].to_numpy(copy=True)
        if values.dtype.kind in {"i", "u", "f"}:
            values = values.astype(object)
        values[~served] = pd.NA
        out[target] = values

    rows = np.arange(len(demand))
    assigned_distance = np.full(len(demand), np.nan)
    assigned_distance[served] = distance[rows[served], state.assignment[served]]
    nearest_distance = distance[rows, nearest_candidate]
    out["DISTANCIA_KM"] = assigned_distance
    out["DISTANCIA_EQUIVALENTE_KM"] = assigned_distance
    out["GERENCIA_MAIS_PROXIMA"] = [
        manager_id(int(position), cfg) for position in nearest_position
    ]
    out["CANDIDATE_ID_MAIS_PROXIMO"] = candidates.iloc[nearest_candidate][
        "CANDIDATE_ID"
    ].to_numpy()
    out["DISTANCIA_MAIS_PROXIMO_KM"] = nearest_distance
    out["DELTA_DISTANCIA_VS_MAIS_PROXIMO_KM"] = assigned_distance - nearest_distance
    out["EH_MAIS_PROXIMO"] = served & (state.assignment == nearest_candidate)
    out["CRUZA_UF"] = served & (
        out["UF"].astype(str).to_numpy() != out["UF_POLO"].astype(str).to_numpy()
    )
    radius = pd.to_numeric(out["RAIO_REFERENCIA_KM"], errors="coerce")
    out["FORA_RAIO_REFERENCIA"] = (
        served & radius.notna() & (out["DISTANCIA_KM"] > radius)
    )
    out["EXCESSO_RAIO_KM"] = np.where(
        out["FORA_RAIO_REFERENCIA"], out["DISTANCIA_KM"] - radius.fillna(0), 0.0
    )
    out["METODO_ATRIBUICAO"] = np.where(
        served,
        "SCIP_OTIMIZACAO_LEXICOGRAFICA_CONTIGUA",
        "NAO_ATENDIMENTO_OPCIONAL_SCIP",
    )
    out["MOTIVO_NAO_ATENDIMENTO"] = pd.NA
    out.loc[~served, "MOTIVO_NAO_ATENDIMENTO"] = "MUNICIPIO_ABAIXO_30_MIL_OPCIONAL"
    out["EH_CORREDOR_CONTIGUIDADE"] = (
        served & out["ATENDIMENTO_OPCIONAL"] & (out["QTD_LOJAS"] == 0)
    )
    return out


def build_manager_summary(
    state: SolvedState,
    assignments: pd.DataFrame,
    candidates: pd.DataFrame,
    run_id: str,
    scenario_id: str,
    cfg: V4Config,
) -> pd.DataFrame:
    served = assignments[assignments["ATENDIDA"]].copy()
    average_population = float(served["POPULACAO_UNIDADE"].sum()) / cfg.manager_count
    average_stores = float(served["QTD_LOJAS"].sum()) / cfg.manager_count
    rows: list[dict[str, Any]] = []
    for cluster, candidate_idx in enumerate(state.selected):
        gerencia = manager_id(cluster, cfg)
        members = served[served["GERENCIA_ID"] == gerencia]
        candidate = candidates.iloc[candidate_idx]
        population_weights = members["POPULACAO_UNIDADE"].clip(lower=1)
        rows.append(
            {
                "RUN_ID": run_id,
                "CENARIO_ID": scenario_id,
                "GERENCIA_ID": gerencia,
                "CANDIDATE_IDX": candidate_idx,
                "CANDIDATE_ID": candidate.CANDIDATE_ID,
                "TIPO_CANDIDATO": candidate.TIPO_CANDIDATO,
                "COD_IBGE_POLO": candidate.COD_IBGE,
                "CD_DIST_POLO": candidate.CD_DIST,
                "NM_MUN_POLO": candidate.NM_MUN,
                "NM_DIST_POLO": candidate.NM_DIST,
                "UF_POLO": candidate.UF,
                "DESC_GERENCIA_AREA_PROPOSTA": candidate.DESC_GERENCIA_AREA_PROPOSTA,
                "LATITUDE": float(candidate.LATITUDE),
                "LONGITUDE": float(candidate.LONGITUDE),
                "POPULACAO_SEDE_REFERENCIA": float(candidate.POPULACAO_SEDE_REFERENCIA),
                "QTD_UNIDADES": int(members["DEMAND_ID"].nunique()),
                "QTD_MUNICIPIOS": int(members["COD_IBGE"].nunique()),
                "QTD_DISTRITOS": int(members["TIPO_UNIDADE"].eq("DISTRITO").sum()),
                "QTD_LOJAS": int(members["QTD_LOJAS"].sum()),
                "POPULACAO_ATENDIDA": float(members["POPULACAO_UNIDADE"].sum()),
                "INDICE_POPULACAO_VS_MEDIA": float(members["POPULACAO_UNIDADE"].sum())
                / max(average_population, 1e-9),
                "INDICE_LOJAS_VS_MEDIA": float(members["QTD_LOJAS"].sum())
                / max(average_stores, 1e-9),
                "DISTANCIA_MEDIA_KM": float(members["DISTANCIA_KM"].mean()),
                "DISTANCIA_MEDIA_PONDERADA_POP_KM": float(
                    np.average(members["DISTANCIA_KM"], weights=population_weights)
                ),
                "DISTANCIA_P90_KM": v3.weighted_percentile(
                    members["DISTANCIA_KM"], population_weights, 0.9
                ),
                "DISTANCIA_MAXIMA_KM": float(members["DISTANCIA_KM"].max()),
                "CARGA_EQUIVALENTE_TOTAL": np.nan,
                "METODO_CARGA": "NAO_UTILIZADA_V4",
            }
        )
    return pd.DataFrame(rows)


def build_anchor_outputs(
    state: SolvedState,
    managers: pd.DataFrame,
    regional: pd.DataFrame,
    candidates: pd.DataFrame,
    anchor_distance: np.ndarray,
    run_id: str,
    scenario_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    manager_by_candidate = managers.set_index("CANDIDATE_IDX")
    anchor_rows: list[dict[str, Any]] = []
    direct_by_candidate: dict[int, pd.Series] = {}
    for regional_idx, candidate_idx in sorted(state.anchor_candidate.items()):
        gr = regional.iloc[regional_idx]
        candidate = candidates.iloc[candidate_idx]
        manager = manager_by_candidate.loc[candidate_idx]
        row = gr.to_dict()
        row.update(
            {
                "RUN_ID": run_id,
                "CENARIO_ID": scenario_id,
                "GERENCIA_ID": manager.GERENCIA_ID,
                "CANDIDATE_IDX_ANCHOR": candidate_idx,
                "CANDIDATE_ID_ANCHOR": candidate.CANDIDATE_ID,
                "COD_IBGE_POLO_ANCHOR": candidate.COD_IBGE,
                "NM_MUN_POLO_ANCHOR": candidate.NM_MUN,
                "UF_POLO_ANCHOR": candidate.UF,
                "LATITUDE_POLO_ANCHOR": float(candidate.LATITUDE),
                "LONGITUDE_POLO_ANCHOR": float(candidate.LONGITUDE),
                "DISTANCIA_GR_POLO_KM": float(
                    anchor_distance[regional_idx, candidate_idx]
                ),
                "DENTRO_RAIO_100KM": True,
                "STATUS_ANCORA": "ANCORA_1_PARA_1_OTIMIZADA_NO_MIP",
            }
        )
        anchor_rows.append(row)
        direct_by_candidate[candidate_idx] = gr
    anchors = pd.DataFrame(anchor_rows)

    link_rows: list[dict[str, Any]] = []
    for manager in managers.itertuples(index=False):
        candidate_idx = int(manager.CANDIDATE_IDX)
        direct = candidate_idx in direct_by_candidate
        if direct:
            gr = direct_by_candidate[candidate_idx]
            regional_idx = int(
                regional.index[regional["COD_GER_REG"] == gr.COD_GER_REG][0]
            )
            link_distance = float(anchor_distance[regional_idx, candidate_idx])
            link_type = "ANCORA_OBRIGATORIA_1_PARA_1_SCIP"
        else:
            pool = regional[
                regional["DESC_GERENCIA_AREA_GR"].astype(str)
                == str(manager.DESC_GERENCIA_AREA_PROPOSTA)
            ]
            if pool.empty:
                raise RuntimeError(f"Sem GR na area do polo {manager.CANDIDATE_ID}.")
            distances = v3.haversine_arrays(
                np.full(len(pool), float(manager.LATITUDE)),
                np.full(len(pool), float(manager.LONGITUDE)),
                pool["LATITUDE"].to_numpy(float),
                pool["LONGITUDE"].to_numpy(float),
            )
            gr = pool.iloc[int(np.argmin(distances))]
            link_distance = float(np.min(distances))
            link_type = "REFORCO_GR_MAIS_PROXIMA_NA_AREA"
        link_rows.append(
            {
                "RUN_ID": run_id,
                "CENARIO_ID": scenario_id,
                "GERENCIA_ID": manager.GERENCIA_ID,
                "CANDIDATE_ID": manager.CANDIDATE_ID,
                "COD_GER_REG": gr.COD_GER_REG,
                "GER_REGIONAL": gr.GER_REGIONAL,
                "UF_GR": gr.UF_GR,
                "DESC_GERENCIA_AREA_GR": gr.DESC_GERENCIA_AREA_GR,
                "LATITUDE_GR": float(gr.LATITUDE),
                "LONGITUDE_GR": float(gr.LONGITUDE),
                "DISTANCIA_POLO_GR_KM": link_distance,
                "TIPO_VINCULO_GR": link_type,
                "EH_ANCORA_GR": direct,
            }
        )
    links = pd.DataFrame(link_rows)
    enriched = managers.merge(
        links.drop(columns=["RUN_ID", "CENARIO_ID"]),
        on=["GERENCIA_ID", "CANDIDATE_ID"],
        how="left",
    )
    return enriched, links, anchors


def attach_hierarchy_to_assignments(
    assignments: pd.DataFrame, managers: pd.DataFrame
) -> pd.DataFrame:
    columns = [
        "GERENCIA_ID",
        "COD_GER_REG",
        "GER_REGIONAL",
        "UF_GR",
        "DESC_GERENCIA_AREA_GR",
        "TIPO_VINCULO_GR",
        "EH_ANCORA_GR",
    ]
    return assignments.merge(managers[columns], on="GERENCIA_ID", how="left")


def build_current_portfolio(
    current: pd.DataFrame,
    stores_by_unit: pd.DataFrame,
    demand: pd.DataFrame,
    run_id: str,
) -> pd.DataFrame:
    compatibility = demand.copy()
    compatibility["CARGA_EQUIVALENTE"] = 0.0
    portfolio = v3.build_current_manager_portfolio(
        current, stores_by_unit, compatibility, run_id
    )
    portfolio["CARGA_EQUIVALENTE_ATUAL_ESTIMADA"] = np.nan
    portfolio["METODO_CARGA"] = "NAO_UTILIZADA_V4"
    return portfolio


def bottleneck_assignment(distance: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    if distance.shape[0] != distance.shape[1]:
        raise ValueError(
            "O matching de realocacao exige a mesma quantidade de gerentes e polos."
        )
    thresholds = np.unique(distance.astype(float))

    def feasible(threshold: float) -> bool:
        allowed = distance <= threshold + 1e-9
        rows, cols = linear_sum_assignment(np.where(allowed, 0.0, 1.0))
        return len(rows) == distance.shape[0] and bool(np.all(allowed[rows, cols]))

    low, high = 0, len(thresholds) - 1
    while low < high:
        middle = (low + high) // 2
        if feasible(float(thresholds[middle])):
            high = middle
        else:
            low = middle + 1
    threshold = float(thresholds[low])
    allowed = distance <= threshold + 1e-9
    penalty = max(1e9, float(np.nanmax(distance)) * distance.shape[0] * 10)
    rows, cols = linear_sum_assignment(np.where(allowed, distance, penalty))
    if not np.all(allowed[rows, cols]):
        raise RuntimeError(
            "Falha ao construir matching perfeito no menor gargalo provado."
        )
    return rows.astype(int), cols.astype(int), threshold


def build_manager_reallocation(
    current: pd.DataFrame,
    managers: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> tuple[pd.DataFrame, dict[str, float]]:
    if len(current) != len(managers):
        raise RuntimeError(
            f"Realocacao exige 135 origens e 135 destinos; recebeu {len(current)} e {len(managers)}."
        )
    distance = v3.pairwise_haversine_matrix(
        current, managers, "LATITUDE_ATUAL", "LONGITUDE_ATUAL", "LATITUDE", "LONGITUDE"
    )
    current_rows, proposed_rows, bottleneck = bottleneck_assignment(distance)
    records: list[dict[str, Any]] = []
    for current_idx, proposed_idx in zip(current_rows, proposed_rows):
        source = current.iloc[int(current_idx)]
        target = managers.iloc[int(proposed_idx)]
        movement = float(distance[current_idx, proposed_idx])
        same_municipality = str(source.get("COD_IBGE_REFERENCIA_ATUAL")) == str(
            target.get("COD_IBGE_POLO")
        )
        records.append(
            {
                "RUN_ID": run_id,
                "CENARIO_ID": scenario_id,
                "STATUS_TRANSICAO": "MANTIDO_MESMO_MUNICIPIO"
                if same_municipality
                else "MOVIMENTADO",
                "FAIXA_MOVIMENTO": v3.movement_band(movement),
                "TIPO_MATCHING": "MINIMAX_DEPOIS_MENOR_SOMA_HUNGARIAN",
                "CHAVE_SUPERVISAO_ATUAL": source.CHAVE_SUPERVISAO,
                "GERENCIA_ID_PROPOSTA": target.GERENCIA_ID,
                "LATITUDE_ATUAL": float(source.LATITUDE_ATUAL),
                "LONGITUDE_ATUAL": float(source.LONGITUDE_ATUAL),
                "COD_IBGE_REFERENCIA_ATUAL": source.get("COD_IBGE_REFERENCIA_ATUAL"),
                "NM_MUN_REFERENCIA_ATUAL": source.get("NM_MUN_REFERENCIA_ATUAL"),
                "UF_REFERENCIA_ATUAL": source.get("UF_REFERENCIA_ATUAL"),
                "DESC_GERENCIA_AREA_ATUAL": source.get("DESC_GERENCIA_AREA_ATUAL"),
                "DESC_COORDENACAO": source.get("DESC_COORDENACAO"),
                "DESC_SUPERVISAO": source.get("DESC_SUPERVISAO"),
                "LATITUDE_PROPOSTA": float(target.LATITUDE),
                "LONGITUDE_PROPOSTA": float(target.LONGITUDE),
                "COD_IBGE_PROPOSTO": target.COD_IBGE_POLO,
                "NM_MUN_PROPOSTO": target.NM_MUN_POLO,
                "UF_PROPOSTA": target.UF_POLO,
                "DESC_GERENCIA_AREA_PROPOSTA": target.DESC_GERENCIA_AREA_PROPOSTA,
                "COD_GER_REG": target.COD_GER_REG,
                "GER_REGIONAL": target.GER_REGIONAL,
                "TIPO_VINCULO_GR": target.TIPO_VINCULO_GR,
                "DISTANCIA_MOVIMENTO_KM": movement,
                "LIMITE_OTIMO_PIOR_MOVIMENTO_KM": bottleneck,
                "QTD_UNIDADES_ATUAL": source.get("QTD_UNIDADES_ATUAL", 0),
                "QTD_LOJAS_ATUAL": source.get("QTD_LOJAS_ATUAL", 0),
                "POPULACAO_ATUAL_ESTIMADA": source.get("POPULACAO_ATUAL_ESTIMADA", 0),
                "CARGA_EQUIVALENTE_ATUAL_ESTIMADA": np.nan,
                "QTD_UNIDADES_PROPOSTA": target.QTD_UNIDADES,
                "QTD_LOJAS_PROPOSTA": target.QTD_LOJAS,
                "POPULACAO_PROPOSTA": target.POPULACAO_ATENDIDA,
                "CARGA_EQUIVALENTE_PROPOSTA": np.nan,
            }
        )
    result = pd.DataFrame(records)
    metrics = {
        "PIOR_MOVIMENTO_KM": bottleneck,
        "SOMA_MOVIMENTOS_KM": float(result["DISTANCIA_MOVIMENTO_KM"].sum()),
        "MEDIA_MOVIMENTOS_KM": float(result["DISTANCIA_MOVIMENTO_KM"].mean()),
        "P90_MOVIMENTOS_KM": float(result["DISTANCIA_MOVIMENTO_KM"].quantile(0.9)),
    }
    return result, metrics


def assign_stores_to_solution(
    stores_by_unit: pd.DataFrame,
    assignments: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> pd.DataFrame:
    if stores_by_unit.empty:
        return pd.DataFrame()
    columns = [
        "DEMAND_ID",
        "ATENDIDA",
        "GERENCIA_ID",
        "CANDIDATE_ID",
        "COD_IBGE_POLO",
        "CD_DIST_POLO",
        "NM_MUN_POLO",
        "NM_DIST_POLO",
        "UF_POLO",
        "DESC_GERENCIA_AREA_PROPOSTA",
        "COD_GER_REG",
        "GER_REGIONAL",
    ]
    out = stores_by_unit.merge(assignments[columns], on="DEMAND_ID", how="left")
    out.insert(0, "RUN_ID", run_id)
    out.insert(1, "CENARIO_ID", scenario_id)
    out["MOTIVO_NAO_ATENDIMENTO"] = np.where(
        out["ATENDIDA"].fillna(False), pd.NA, "UNIDADE_OPCIONAL_NAO_ATENDIDA"
    )
    return out


def build_regional_movement_outputs(
    current: pd.DataFrame,
    managers: pd.DataFrame,
    reallocation: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    current_count = (
        current.groupby("DESC_GERENCIA_AREA_ATUAL")["CHAVE_SUPERVISAO"]
        .nunique()
        .rename("QTD_GERENTES_ATUAIS")
    )
    proposed_count = (
        managers.groupby("DESC_GERENCIA_AREA_PROPOSTA")["GERENCIA_ID"]
        .nunique()
        .rename("QTD_GERENTES_PROPOSTOS")
    )
    areas = sorted(
        set(v3.DESC_AREA_POR_UF.values())
        | set(current_count.index)
        | set(proposed_count.index)
    )
    balance = pd.DataFrame({"DESC_GERENCIA_AREA": areas})
    balance = balance.merge(
        current_count.rename_axis("DESC_GERENCIA_AREA").reset_index(),
        on="DESC_GERENCIA_AREA",
        how="left",
    ).merge(
        proposed_count.rename_axis("DESC_GERENCIA_AREA").reset_index(),
        on="DESC_GERENCIA_AREA",
        how="left",
    )
    balance[["QTD_GERENTES_ATUAIS", "QTD_GERENTES_PROPOSTOS"]] = (
        balance[["QTD_GERENTES_ATUAIS", "QTD_GERENTES_PROPOSTOS"]].fillna(0).astype(int)
    )
    balance["DELTA_GERENTES"] = (
        balance["QTD_GERENTES_PROPOSTOS"] - balance["QTD_GERENTES_ATUAIS"]
    )
    balance["RECOMENDACAO"] = [
        v3.hierarchy_change_status(int(current_value), int(proposed_value))
        for current_value, proposed_value in zip(
            balance["QTD_GERENTES_ATUAIS"], balance["QTD_GERENTES_PROPOSTOS"]
        )
    ]
    balance.insert(0, "RUN_ID", run_id)
    balance.insert(1, "CENARIO_ID", scenario_id)

    flow = (
        reallocation.groupby(
            ["DESC_GERENCIA_AREA_ATUAL", "DESC_GERENCIA_AREA_PROPOSTA"], dropna=False
        )
        .agg(
            QTD_GERENTES=("CHAVE_SUPERVISAO_ATUAL", "nunique"),
            DISTANCIA_MEDIA_KM=("DISTANCIA_MOVIMENTO_KM", "mean"),
            DISTANCIA_MAXIMA_KM=("DISTANCIA_MOVIMENTO_KM", "max"),
        )
        .reset_index()
    )
    flow.insert(0, "RUN_ID", run_id)
    flow.insert(1, "CENARIO_ID", scenario_id)
    if (
        int(balance["QTD_GERENTES_ATUAIS"].sum()) != 135
        or int(balance["QTD_GERENTES_PROPOSTOS"].sum()) != 135
    ):
        raise RuntimeError(
            "O saldo regional da V4 nao fecha em 135 gerentes atuais e propostos."
        )
    return balance, flow


def build_scenario_summary(
    assignments: pd.DataFrame,
    managers: pd.DataFrame,
    objectives: pd.DataFrame,
    movement_metrics: dict[str, float],
    run_id: str,
    scenario_id: str,
    elapsed: float,
    cfg: V4Config,
) -> pd.DataFrame:
    served = assignments[assignments["ATENDIDA"]].copy()
    unserved = assignments[~assignments["ATENDIDA"]].copy()
    population = served["POPULACAO_UNIDADE"].clip(lower=1)
    total_stores = int(assignments["QTD_LOJAS"].sum())
    served_stores = int(served["QTD_LOJAS"].sum())
    eligible_population_average = (
        float(assignments["POPULACAO_UNIDADE"].sum()) / cfg.manager_count
    )
    eligible_store_average = float(total_stores) / cfg.manager_count
    population_range = float(
        managers["POPULACAO_ATENDIDA"].max() - managers["POPULACAO_ATENDIDA"].min()
    )
    store_range = float(managers["QTD_LOJAS"].max() - managers["QTD_LOJAS"].min())
    objective_by_name = objectives.set_index("ETAPA")
    row: dict[str, Any] = {
        "RUN_ID": run_id,
        "CENARIO_ID": scenario_id,
        "MODELO_VERSAO": cfg.model_version,
        "QTD_GERENCIAS_SOLICITADA": cfg.manager_count,
        "QTD_GERENCIAS_SELECIONADA": len(managers),
        "QTD_ANCORAS_GR": int(managers["EH_ANCORA_GR"].sum()),
        "QTD_UNIDADES_ATENDIDAS": int(served["DEMAND_ID"].nunique()),
        "QTD_UNIDADES_NAO_ATENDIDAS": int(unserved["DEMAND_ID"].nunique()),
        "QTD_MUNICIPIOS_ATENDIDOS": int(served["COD_IBGE"].nunique()),
        "QTD_MUNICIPIOS_NAO_ATENDIDOS": int(unserved["COD_IBGE"].nunique()),
        "QTD_DISTRITOS_ATENDIDOS": int(served["TIPO_UNIDADE"].eq("DISTRITO").sum()),
        "POPULACAO_ATENDIDA": float(served["POPULACAO_UNIDADE"].sum()),
        "POPULACAO_NAO_ATENDIDA": float(unserved["POPULACAO_UNIDADE"].sum()),
        "QTD_LOJAS_ATENDIDAS": served_stores,
        "QTD_LOJAS_NAO_ATENDIDAS": total_stores - served_stores,
        "PERC_LOJAS_ATENDIDAS": served_stores / total_stores if total_stores else 1.0,
        "MENOR_POPULACAO_CARTEIRA": float(managers["POPULACAO_ATENDIDA"].min()),
        "MAIOR_POPULACAO_CARTEIRA": float(managers["POPULACAO_ATENDIDA"].max()),
        "MENOR_QTD_LOJAS_CARTEIRA": int(managers["QTD_LOJAS"].min()),
        "MAIOR_QTD_LOJAS_CARTEIRA": int(managers["QTD_LOJAS"].max()),
        "AMPLITUDE_POPULACAO_VS_MEDIA_ELEGIVEL": population_range
        / max(eligible_population_average, 1e-9),
        "AMPLITUDE_LOJAS_VS_MEDIA_ELEGIVEL": store_range
        / max(eligible_store_average, 1e-9),
        "DISTANCIA_MEDIA_SIMPLES_KM": float(served["DISTANCIA_KM"].mean()),
        "DISTANCIA_MEDIA_PONDERADA_POP_KM": float(
            np.average(served["DISTANCIA_KM"], weights=population)
        ),
        "DISTANCIA_P90_PONDERADA_POP_KM": v3.weighted_percentile(
            served["DISTANCIA_KM"], population, 0.9
        ),
        "DISTANCIA_MAXIMA_KM": float(served["DISTANCIA_KM"].max()),
        "OBJETIVO_EQUILIBRIO": float(
            objective_by_name.loc[
                "EQUILIBRIO_POPULACAO_LOJAS", "VALOR_NA_SOLUCAO_FINAL"
            ]
        ),
        "OBJETIVO_HABITANTE_KM_NORMALIZADO": float(
            objective_by_name.loc[
                "DISTANCIA_PONDERADA_POPULACAO", "VALOR_NA_SOLUCAO_FINAL"
            ]
        ),
        "OBJETIVO_LOJA_KM_NORMALIZADO": float(
            objective_by_name.loc["DISTANCIA_PONDERADA_LOJAS", "VALOR_NA_SOLUCAO_FINAL"]
        ),
        "TODAS_ETAPAS_OTIMAS_COMPROVADAS": bool(objectives["OTIMO_COMPROVADO"].all()),
        "MAIOR_GAP_RELATIVO": float(objectives["GAP_RELATIVO"].max()),
        "CORTES_CONTIGUIDADE": int(objectives["CORTES_CONTIGUIDADE_ACUMULADOS"].max()),
        "TEMPO_SEGUNDOS": elapsed,
        "DATA_EXECUCAO": datetime.now(),
    }
    row.update(movement_metrics)
    return pd.DataFrame([row])


def enrich_exclusion_audit(
    exclusion_audit: pd.DataFrame,
    all_stores: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> pd.DataFrame:
    counts = (
        all_stores.groupby("COD_IBGE")["CHAVE_LOJA"]
        .nunique()
        .rename("QTD_LOJAS_EXCLUIDAS")
    )
    out = exclusion_audit.merge(
        counts, left_on="COD_IBGE", right_index=True, how="left"
    )
    out["QTD_LOJAS_EXCLUIDAS"] = out["QTD_LOJAS_EXCLUIDAS"].fillna(0).astype(int)
    out.insert(0, "RUN_ID", run_id)
    out.insert(1, "CENARIO_ID", scenario_id)
    out["STATUS"] = "REMOVIDO_ANTES_DA_GERACAO_DA_DEMANDA"
    return out


def save_scenario_outputs(
    cfg: V4Config,
    scenario: pd.DataFrame,
    managers: pd.DataFrame,
    assignments: pd.DataFrame,
    stores: pd.DataFrame,
    current: pd.DataFrame,
    reallocation: pd.DataFrame,
    objectives: pd.DataFrame,
    anchors: pd.DataFrame,
    links: pd.DataFrame,
    audit: pd.DataFrame,
    exclusions: pd.DataFrame,
    regional_balance: pd.DataFrame,
    regional_flow: pd.DataFrame,
    territorial_audit: pd.DataFrame,
    unit_geo: gpd.GeoDataFrame,
) -> Path:
    scenario_id = str(scenario.iloc[0]["CENARIO_ID"])
    folder = cfg.output_dir / scenario_id
    folder.mkdir(parents=True, exist_ok=True)

    if cfg.save_excel:
        workbook = folder / f"resultado_{scenario_id}.xlsx"
        sheets = [
            ("cenario", scenario),
            ("gerencias_propostas", managers),
            ("unidades_atendidas", assignments),
            ("lojas_propostas", stores),
            ("gerencias_atuais", current),
            ("transicao", reallocation),
            ("objetivos_solver", objectives),
            ("ancoras_gr", anchors),
            ("vinculo_gr_polo", links),
            ("auditoria_solucao", audit),
            ("auditoria_territorial", territorial_audit),
            ("municipios_excluidos", exclusions),
            ("saldo_regional", regional_balance),
            ("fluxo_realocacao", regional_flow),
            ("nao_atendidos", assignments[~assignments["ATENDIDA"]]),
        ]
        with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
            for name, frame in sheets:
                frame.to_excel(writer, sheet_name=name[:31], index=False)

    if cfg.save_geojson:
        payloads = {
            "gerencias_propostas.geojson": v3.dataframe_to_point_geojson(
                managers,
                "LATITUDE",
                "LONGITUDE",
                [
                    "GERENCIA_ID",
                    "CANDIDATE_ID",
                    "COD_IBGE_POLO",
                    "CD_DIST_POLO",
                    "NM_MUN_POLO",
                    "NM_DIST_POLO",
                    "UF_POLO",
                    "DESC_GERENCIA_AREA_PROPOSTA",
                    "COD_GER_REG",
                    "GER_REGIONAL",
                    "EH_ANCORA_GR",
                    "POPULACAO_ATENDIDA",
                    "QTD_LOJAS",
                    "INDICE_POPULACAO_VS_MEDIA",
                    "INDICE_LOJAS_VS_MEDIA",
                ],
            ),
            "unidades_atendidas.geojson": v3.dataframe_to_point_geojson(
                assignments,
                "LATITUDE",
                "LONGITUDE",
                [
                    "ATENDIDA",
                    "MOTIVO_NAO_ATENDIMENTO",
                    "GERENCIA_ID",
                    "DEMAND_ID",
                    "TIPO_UNIDADE",
                    "COD_IBGE",
                    "CD_DIST",
                    "POPULACAO_UNIDADE",
                    "QTD_LOJAS",
                    "DISTANCIA_KM",
                    "ATENDIMENTO_OBRIGATORIO",
                    "METODO_ATRIBUICAO",
                ],
            ),
            "linhas_atendimento.geojson": v3.assignments_to_line_geojson(assignments),
            "gerencias_atuais.geojson": v3.dataframe_to_point_geojson(
                current,
                "LATITUDE_ATUAL",
                "LONGITUDE_ATUAL",
                [
                    "CHAVE_SUPERVISAO",
                    "COD_IBGE_REFERENCIA_ATUAL",
                    "NM_MUN_REFERENCIA_ATUAL",
                    "QTD_UNIDADES_ATUAL",
                    "QTD_LOJAS_ATUAL",
                ],
            ),
            "movimentos_atual_proposto.geojson": v3.transition_to_line_geojson(
                reallocation
            ),
            "gr_regionais.geojson": v3.dataframe_to_point_geojson(
                anchors,
                "LATITUDE",
                "LONGITUDE",
                [
                    "COD_GER_REG",
                    "GER_REGIONAL",
                    "UF_GR",
                    "DESC_GERENCIA_AREA_GR",
                    "GERENCIA_ID",
                    "CANDIDATE_ID_ANCHOR",
                    "DISTANCIA_GR_POLO_KM",
                ],
            ),
            "linhas_gr_polo.geojson": v3.regional_anchor_lines_geojson(anchors),
        }
        for filename, payload in payloads.items():
            (folder / filename).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

        territory_columns = [
            "DEMAND_ID",
            "GERENCIA_ID",
            "ATENDIDA",
            "POPULACAO_UNIDADE",
            "QTD_LOJAS",
            "DISTANCIA_KM",
            "METODO_ATRIBUICAO",
            "MOTIVO_NAO_ATENDIMENTO",
        ]
        territory = unit_geo.merge(
            assignments[territory_columns], on="DEMAND_ID", how="inner"
        )
        territory.to_file(folder / "carteiras_unidades.geojson", driver="GeoJSON")
        served_territory = territory[territory["ATENDIDA"]].copy()
        served_territory[["GERENCIA_ID", "geometry"]].dissolve(
            by="GERENCIA_ID", as_index=False
        ).to_file(folder / "carteiras_dissolvidas.geojson", driver="GeoJSON")
    return folder


def persist_sql_outputs(
    engine: Engine,
    sql_cfg: v3.SQLConfig,
    cfg: V4Config,
    frames: Iterable[tuple[str, pd.DataFrame]],
) -> None:
    if not cfg.save_sql:
        return
    for table, frame in frames:
        v3.write_sql_table(engine, frame, table, sql_cfg, cfg)


def build_district_reconciliation_audit(
    demand: pd.DataFrame,
    municipal_reference: pd.DataFrame,
    split: set[str],
) -> pd.DataFrame:
    official = municipal_reference.set_index("COD_IBGE")["POPULACAO_MUNICIPIO"]
    rows: list[dict[str, Any]] = []
    for code in sorted(split):
        units = demand[
            (demand["COD_IBGE"].astype(str) == code)
            & demand["TIPO_UNIDADE"].eq("DISTRITO")
        ]
        district_total = float(units["POPULACAO_UNIDADE"].sum())
        municipality_total = float(official.loc[code])
        difference = district_total - municipality_total
        tolerance = max(1e-6, abs(municipality_total) * 1e-9)
        rows.append(
            {
                "METRICA": "RECONCILIACAO_POPULACAO_DISTRITAL",
                "COD_IBGE": code,
                "QTD_DISTRITOS": len(units),
                "POPULACAO_MUNICIPAL_OFICIAL": municipality_total,
                "POPULACAO_DISTRITAL_MODELO": district_total,
                "DIFERENCA": difference,
                "ESPERADO": "DIFERENCA_ZERO",
                "STATUS": "OK"
                if abs(difference) <= tolerance and len(units) > 0
                else "VIOLACAO",
                "DETALHE": "Municipio agregado removido; distritos normalizados ao total oficial.",
            }
        )
    audit = pd.DataFrame(rows)
    if not audit.empty and audit["STATUS"].eq("VIOLACAO").any():
        raise RuntimeError(
            "A populacao distrital nao fecha com a populacao municipal: "
            + json.dumps(
                audit[audit["STATUS"] == "VIOLACAO"].head(20).to_dict("records"),
                ensure_ascii=False,
            )
        )
    return audit


def main(argv: list[str] | None = None) -> None:
    v3.configure_logging()
    args = parse_args(argv)
    cfg = config_from_args(args)
    require_solver()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    sql_cfg = v3.SQLConfig()
    engine: Engine | None = None
    run_id = uuid.uuid4().hex.upper()
    scenario_id = f"GREENFIELD_V4_{cfg.manager_count}_{uuid.uuid4().hex[:8].upper()}"
    started = time.monotonic()
    execution: dict[str, Any] = {
        "RUN_ID": run_id,
        "CENARIO_ID": scenario_id,
        "MODELO_VERSAO": cfg.model_version,
        "CONFIG_HASH": v3.config_hash(cfg),
        "PERIODO_LOJAS": cfg.periodo_lojas,
        "QTD_GERENCIAS": cfg.manager_count,
        "LIMIAR_DISTRITALIZACAO": cfg.large_city_threshold,
        "LIMIAR_ATENDIMENTO_OBRIGATORIO": cfg.mandatory_population_min,
        "COBERTURA_MINIMA_LOJAS": cfg.minimum_store_coverage,
        "LIMITE_TEMPO_SEGUNDOS": cfg.time_limit_seconds,
        "MIP_GAP_ALVO": cfg.mip_gap,
        "SEED": cfg.random_seed,
        "DATA_INICIO": datetime.now(),
        "STATUS": "EM_EXECUCAO",
    }

    try:
        v3.log_step("GREENFIELD V4 - 1/8 GEOMETRIAS E EXTRACAO")
        engine = v3.create_sql_engine(sql_cfg)
        district_reference, district_geo = v3.load_district_data(cfg)
        municipal_geo = v3.load_municipal_geometry(cfg)
        raw = v3.load_raw_data(engine, cfg)

        v3.log_step("GREENFIELD V4 - 2/8 REFERENCIAS E EXCLUSOES SQL")
        municipal_reference = v3.prepare_municipal_reference(
            raw["municipalities"], raw["population"], municipal_geo
        )
        regional = v3.prepare_regional_points(
            raw["regional_points"], municipal_geo, cfg
        )
        hierarchy = v3.prepare_current_hierarchy(raw["current_hierarchy"])
        all_stores = v3.prepare_stores(raw["stores"], municipal_reference)
        excluded, exclusion_audit = load_sql_exclusions(
            engine, municipal_reference, cfg
        )

        v3.log_step("GREENFIELD V4 - 3/8 DEMANDA HIBRIDA MUNICIPIO/DISTRITO")
        demand, stores_by_unit, split = prepare_v4_demand(
            municipal_reference,
            district_reference,
            district_geo,
            all_stores,
            excluded,
            cfg,
        )
        district_audit = build_district_reconciliation_audit(
            demand, municipal_reference, split
        )
        exclusion_audit = enrich_exclusion_audit(
            exclusion_audit, all_stores, run_id, scenario_id
        )

        v3.log_step("GREENFIELD V4 - 4/8 TOPOLOGIA, CANDIDATOS E MATRIZES")
        unit_geo = v3.build_hybrid_unit_geometry(demand, municipal_geo, district_geo)
        geo_indices = set(unit_geo["DEMAND_IDX"].astype(int))
        missing_geo = demand[~demand["DEMAND_IDX"].isin(geo_indices)]
        if not missing_geo.empty:
            raise RuntimeError(
                f"{len(missing_geo)} unidades da V4 estao sem geometria territorial."
            )
        territorial_audit = v3.build_territorial_geometry_audit(
            unit_geo, municipal_geo, demand, cfg
        )
        neighbors = v3.build_adjacency_graph(unit_geo, len(demand), cfg)
        state_adjacency = build_official_state_adjacency(municipal_geo, cfg)
        candidates = build_v4_candidates(demand, cfg)
        distance = v3.haversine_matrix_float32(
            demand, candidates, cfg.distance_chunk_size
        )
        _, anchor_distance = build_anchor_pairs(regional, candidates, cfg)

        v3.log_step("GREENFIELD V4 - 5/8 ESTRUTURA ATUAL")
        current = v3.attach_current_hierarchy(
            v3.prepare_current_poles(raw["current_poles"], cfg), hierarchy
        )
        current = v3.attach_current_pole_reference(current, demand)
        current_portfolio = build_current_portfolio(
            current, stores_by_unit, demand, run_id
        )

        v3.log_step("GREENFIELD V4 - 6/8 SCIP LEXICOGRAFICO")
        bundle = build_optimization_model(
            demand,
            candidates,
            regional,
            distance,
            neighbors,
            cfg,
            state_adjacency,
        )
        add_contiguous_warm_start(bundle, demand, candidates, regional, neighbors, cfg)
        state, objectives = solve_lexicographic(bundle, candidates, neighbors, cfg)
        objectives.insert(0, "RUN_ID", run_id)
        objectives.insert(1, "CENARIO_ID", scenario_id)

        solution_audit = validate_final_solution(
            state,
            demand,
            candidates,
            regional,
            anchor_distance,
            neighbors,
            excluded,
            cfg,
            state_adjacency,
        )
        solution_audit = pd.concat(
            [solution_audit, district_audit], ignore_index=True, sort=False
        )
        solution_audit.insert(0, "RUN_ID", run_id)
        solution_audit.insert(1, "CENARIO_ID", scenario_id)
        if not territorial_audit.empty:
            territorial_audit = territorial_audit.copy()
            territorial_audit.insert(0, "RUN_ID", run_id)
            territorial_audit.insert(1, "CENARIO_ID", scenario_id)

        v3.log_step("GREENFIELD V4 - 7/8 SAIDAS E REALOCACAO MINIMAX")
        assignments = build_assignments(
            state, demand, candidates, distance, run_id, scenario_id, cfg
        )
        managers = build_manager_summary(
            state, assignments, candidates, run_id, scenario_id, cfg
        )
        managers, regional_links, anchors = build_anchor_outputs(
            state,
            managers,
            regional,
            candidates,
            anchor_distance,
            run_id,
            scenario_id,
        )
        assignments = attach_hierarchy_to_assignments(assignments, managers)
        proposed_stores = assign_stores_to_solution(
            stores_by_unit, assignments, run_id, scenario_id
        )
        reallocation, movement_metrics = build_manager_reallocation(
            current_portfolio, managers, run_id, scenario_id
        )
        regional_balance, regional_flow = build_regional_movement_outputs(
            current_portfolio,
            managers,
            reallocation,
            run_id,
            scenario_id,
        )
        scenario = build_scenario_summary(
            assignments,
            managers,
            objectives,
            movement_metrics,
            run_id,
            scenario_id,
            time.monotonic() - started,
            cfg,
        )

        folder = save_scenario_outputs(
            cfg,
            scenario,
            managers,
            assignments,
            proposed_stores,
            current_portfolio,
            reallocation,
            objectives,
            anchors,
            regional_links,
            solution_audit,
            exclusion_audit,
            regional_balance,
            regional_flow,
            territorial_audit,
            unit_geo,
        )

        execution.update(
            {
                "DATA_FIM": datetime.now(),
                "STATUS": "CONCLUIDO",
                "TEMPO_TOTAL_SEGUNDOS": time.monotonic() - started,
                "QTD_UNIDADES": len(demand),
                "QTD_DISTRITOS": int(demand["TIPO_UNIDADE"].eq("DISTRITO").sum()),
                "QTD_METROPOLES_DISTRITALIZADAS": len(split),
                "QTD_CANDIDATOS": len(candidates),
                "QTD_PARES_ATRIBUICAO": len(bundle.x),
                "QTD_CORTES_CONTIGUIDADE": bundle.cut_count,
                "QTD_MUNICIPIOS_EXCLUIDOS": len(excluded),
                "TODAS_ETAPAS_OTIMAS_COMPROVADAS": bool(
                    objectives["OTIMO_COMPROVADO"].all()
                ),
                "MAIOR_GAP_RELATIVO": float(objectives["GAP_RELATIVO"].max()),
                "PASTA_SAIDA": str(folder),
                "MENSAGEM": "V4 concluida com modelo lexicografico e realocacao minimax.",
            }
        )

        v3.log_step("GREENFIELD V4 - 8/8 PERSISTENCIA SQL")
        persist_sql_outputs(
            engine,
            sql_cfg,
            cfg,
            [
                (T_EXECUCAO, pd.DataFrame([execution])),
                (T_CENARIO, scenario),
                (T_UNIDADE, demand.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_GERENCIA, managers),
                (T_CARTEIRA, assignments),
                (T_LOJA, proposed_stores),
                (T_GERENCIA_ATUAL, current_portfolio.assign(CENARIO_ID=scenario_id)),
                (T_REALOCACAO, reallocation),
                (T_OBJETIVO, objectives),
                (T_ANCORA, anchors),
                (T_VINCULO_GR, regional_links),
                (T_AUDITORIA, solution_audit),
                (T_AUDITORIA_TERRITORIAL, territorial_audit),
                (T_EXCLUSAO, exclusion_audit),
                (T_NAO_ATENDIDO, assignments[~assignments["ATENDIDA"]]),
                (T_SALDO_REGIONAL, regional_balance),
                (T_FLUXO_REALOCACAO, regional_flow),
            ],
        )
        logging.info("RUN_ID=%s | CENARIO=%s | SAIDA=%s", run_id, scenario_id, folder)
    except Exception as exc:
        execution.update(
            {
                "DATA_FIM": datetime.now(),
                "STATUS": "ERRO",
                "TEMPO_TOTAL_SEGUNDOS": time.monotonic() - started,
                "MENSAGEM": str(exc)[:3000],
            }
        )
        logging.error("Falha na V4: %s", exc)
        logging.debug(traceback.format_exc())
        error_file = cfg.output_dir / f"erro_{run_id}.json"
        error_file.write_text(
            json.dumps(execution, ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
        if engine is not None and cfg.save_sql:
            try:
                v3.write_sql_table(
                    engine, pd.DataFrame([execution]), T_EXECUCAO, sql_cfg, cfg
                )
            except Exception:
                logging.error("Tambem falhou a persistencia do erro no SQL.")
        raise


if __name__ == "__main__":
    main()
