"""
GreenField V5 - modelo heuristico nacional com 135 gerencias.

Principios:
- exatamente 135 polos;
- populacao como relevancia principal e lojas com reforco de 25%;
- todos os municipios/distritos fora da lista SQL sao obrigatorios;
- cidades com 300 mil habitantes ou mais sao modeladas por distritos;
- carteiras contiguas, construidas no grafo territorial;
- nenhuma distancia maxima obrigatoria;
- as 81 GRs sao vinculadas aos polos, com compartilhamento permitido;
- comparacao atual X proposta Y por municipio/distrito;
- construcao heuristica recuperavel, sem MIP nacional e sem prova de otimo.

O modulo reutiliza somente as rotinas estaveis de entrada, normalizacao e
geografia da V3. A selecao e a territorializacao sao proprias da V5.
"""
from __future__ import annotations

import argparse
import heapq
import json
import logging
import os
import re
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

import geopandas as gpd
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

import Estudo_GreenField_V3_COMPLETO as v3


MODEL_VERSION = "V5.0_HEURISTICO_POPULACAO_CONTIGUO"

T_EXECUCAO = "TB_GREENFIELD_BE_EXECUCAO_V5_IGOR"
T_CENARIO = "TB_GREENFIELD_BE_CENARIO_V5_IGOR"
T_UNIDADE = "TB_GREENFIELD_BE_UNIDADE_V5_IGOR"
T_GERENCIA = "TB_GREENFIELD_BE_GERENCIA_PROPOSTA_V5_IGOR"
T_CARTEIRA = "TB_GREENFIELD_BE_CARTEIRA_UNIDADE_V5_IGOR"
T_LOJA = "TB_GREENFIELD_BE_CARTEIRA_LOJA_V5_IGOR"
T_GR = "TB_GREENFIELD_BE_VINCULO_GR_POLO_V5_IGOR"
T_BASELINE = "TB_GREENFIELD_BE_BASELINE_DISTANCIA_V5_IGOR"
T_COMPARACAO = "TB_GREENFIELD_BE_COMPARACAO_RAIO_V5_IGOR"
T_EXCLUSAO = "TB_GREENFIELD_BE_EXCLUSAO_V5_IGOR"
T_AUDITORIA = "TB_GREENFIELD_BE_AUDITORIA_V5_IGOR"


@dataclass(frozen=True)
class V5Config(v3.ModelConfig):
    manager_count: int = 135
    store_emphasis: float = 0.25
    expected_regional_points: int = 81
    large_city_threshold: int = 300_000
    candidate_parent_population_min: int = 0
    allow_unserved_small_components: bool = False
    require_topology_for_v3: bool = True
    refine_iterations_v5: int = int(os.getenv("GREENFIELD_V5_REFINE_ITERATIONS", "4"))
    random_seed: int = int(os.getenv("GREENFIELD_V5_SEED", "20260813"))
    excluded_municipalities_table: str = os.getenv(
        "EXCLUDED_MUNICIPALITIES_TABLE", ""
    )
    excluded_municipalities_sql_column: str = os.getenv(
        "EXCLUDED_MUNICIPALITIES_COLUMN", "CD_MUNIC"
    )
    save_checkpoints: bool = True
    output_dir: Path = Path(
        os.getenv("OUTPUT_DIR_V5", str(BASE_DIR / "saida_greenfield_v5"))
    )
    model_version: str = MODEL_VERSION


@dataclass
class TerritorialState:
    selected_units: list[int]
    position: np.ndarray
    predecessor: np.ndarray
    path_distance: np.ndarray
    direct_distance: np.ndarray


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GreenField V5: heuristica populacional contigua com 135 polos."
    )
    parser.add_argument("--periodo", type=int, help="Periodo YYYYMM das lojas.")
    parser.add_argument("--output-dir", type=Path, help="Diretorio de saida da V5.")
    parser.add_argument(
        "--refine-iterations", type=int, help="Ciclos curtos de melhoria local."
    )
    parser.add_argument("--sem-sql", action="store_true", help="Nao gravar no SQL.")
    parser.add_argument(
        "--sem-excel", action="store_true", help="Nao gerar arquivos Excel."
    )
    parser.add_argument(
        "--sem-geojson", action="store_true", help="Nao gerar GeoJSON."
    )
    parser.add_argument(
        "--sem-checkpoint", action="store_true", help="Nao salvar solucao inicial."
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> V5Config:
    updates: dict[str, Any] = {}
    if args.periodo is not None:
        updates["periodo_lojas"] = args.periodo
    if args.output_dir is not None:
        updates["output_dir"] = args.output_dir.resolve()
    if args.refine_iterations is not None:
        updates["refine_iterations_v5"] = args.refine_iterations
    if args.sem_sql:
        updates["save_sql"] = False
    if args.sem_excel:
        updates["save_excel"] = False
    if args.sem_geojson:
        updates["save_geojson"] = False
    if args.sem_checkpoint:
        updates["save_checkpoints"] = False
    cfg = replace(V5Config(), **updates)
    if cfg.manager_count != 135:
        raise ValueError("A V5 exige exatamente 135 polos.")
    if not 0 <= cfg.store_emphasis < 1:
        raise ValueError("store_emphasis deve estar no intervalo [0, 1).")
    if cfg.refine_iterations_v5 < 0:
        raise ValueError("refine_iterations_v5 nao pode ser negativo.")
    return cfg


def _sql_identifier(value: str, label: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{label} SQL invalido: {value!r}")
    return f"[{value}]"


def _qualified_sql_table(value: str) -> str:
    parts = [part.strip() for part in str(value).split(".") if part.strip()]
    if not 1 <= len(parts) <= 3:
        raise ValueError(
            "EXCLUDED_MUNICIPALITIES_TABLE deve ser tabela, schema.tabela "
            "ou banco.schema.tabela."
        )
    return ".".join(_sql_identifier(part, "Identificador de tabela") for part in parts)


def load_sql_exclusions(
    engine: Engine, municipal_reference: pd.DataFrame, cfg: V5Config
) -> tuple[set[str], pd.DataFrame]:
    if not cfg.excluded_municipalities_table.strip():
        raise RuntimeError(
            "Defina EXCLUDED_MUNICIPALITIES_TABLE no .env. A lista SQL e a fonte "
            "obrigatoria das exclusoes da V5."
        )
    table = _qualified_sql_table(cfg.excluded_municipalities_table)
    column = _sql_identifier(
        cfg.excluded_municipalities_sql_column, "Coluna de exclusao"
    )
    raw = v3.uppercase_columns(
        pd.read_sql(text(f"SELECT {column} AS CD_MUNIC FROM {table}"), engine)
    )
    mapped = v3.map_to_ibge(raw["CD_MUNIC"], municipal_reference)
    invalid = raw.loc[raw["CD_MUNIC"].notna() & mapped.isna(), "CD_MUNIC"]
    if not invalid.empty:
        raise RuntimeError(
            "Codigos invalidos na lista SQL de exclusoes: "
            + ", ".join(invalid.astype(str).unique().tolist()[:20])
        )
    codes = set(mapped.dropna().astype(str))
    audit = municipal_reference[municipal_reference["COD_IBGE"].isin(codes)][
        ["COD_IBGE", "NM_MUN", "UF", "POPULACAO_MUNICIPIO"]
    ].copy()
    audit["ORIGEM_EXCLUSAO"] = cfg.excluded_municipalities_table
    audit["STATUS"] = "EXCLUSAO_DELIBERADA_SQL"
    logging.info("Municipios excluidos pela lista SQL: %s", len(codes))
    return codes, audit.sort_values("COD_IBGE").reset_index(drop=True)


def validate_metropolitan_districts(
    municipal_reference: pd.DataFrame,
    district_reference: pd.DataFrame,
    district_geo: gpd.GeoDataFrame | None,
    excluded: set[str],
    cfg: V5Config,
) -> set[str]:
    metro = municipal_reference[
        (municipal_reference["POPULACAO_MUNICIPIO"] >= cfg.large_city_threshold)
        & ~municipal_reference["COD_IBGE"].isin(excluded)
    ]
    expected = set(metro["COD_IBGE"].astype(str))
    available = set(district_reference["CD_MUN"].dropna().astype(str))
    missing = sorted(expected - available)
    if missing:
        raise RuntimeError(
            "Municipios >=300 mil sem dados distritais: " + ", ".join(missing[:30])
        )
    if district_geo is None:
        raise RuntimeError("A malha distrital e obrigatoria na V5.")
    geo_codes = set(district_geo["CD_MUN"].dropna().astype(str))
    missing_geo = sorted(expected - geo_codes)
    if missing_geo:
        raise RuntimeError(
            "Municipios >=300 mil sem geometria distrital: "
            + ", ".join(missing_geo[:30])
        )
    return expected


def enrich_relevance(
    demand: pd.DataFrame, stores_by_unit: pd.DataFrame, cfg: V5Config
) -> pd.DataFrame:
    counts = (
        stores_by_unit.groupby("DEMAND_ID")["CHAVE_LOJA"]
        .nunique()
        .rename("QTD_LOJAS")
    )
    out = demand.merge(counts, on="DEMAND_ID", how="left")
    out["QTD_LOJAS"] = out["QTD_LOJAS"].fillna(0).astype(int)
    population = out["POPULACAO_UNIDADE"].clip(lower=0).astype(float)
    stores = out["QTD_LOJAS"].clip(lower=0).astype(float)
    if float(population.sum()) <= 0:
        raise RuntimeError("A populacao total das unidades obrigatorias e zero.")
    out["PARTICIPACAO_POPULACAO"] = population / float(population.sum())
    out["PARTICIPACAO_LOJAS"] = (
        stores / float(stores.sum()) if float(stores.sum()) > 0 else 0.0
    )
    out["RELEVANCIA_TERRITORIAL"] = (
        out["PARTICIPACAO_POPULACAO"]
        + cfg.store_emphasis * out["PARTICIPACAO_LOJAS"]
    )
    out["CARGA_EQUIVALENTE"] = out["RELEVANCIA_TERRITORIAL"]
    out = out.sort_values("DEMAND_IDX").reset_index(drop=True)
    expected_index = np.arange(len(out), dtype=int)
    if not np.array_equal(out["DEMAND_IDX"].to_numpy(int), expected_index):
        raise RuntimeError("DEMAND_IDX deixou de ser um indice denso da demanda V5.")
    return out


def build_candidates(demand: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "DEMAND_ID",
        "DEMAND_IDX",
        "TIPO_UNIDADE",
        "COD_IBGE",
        "CD_DIST",
        "NM_MUN",
        "NM_DIST",
        "COD_UF",
        "UF",
        "LATITUDE",
        "LONGITUDE",
        "POPULACAO_UNIDADE",
        "POPULACAO_MUNICIPIO",
        "QTD_LOJAS",
        "RELEVANCIA_TERRITORIAL",
    ]
    out = demand[columns].copy().rename(
        columns={
            "DEMAND_ID": "DEMAND_ID_ORIGEM_POLO",
            "DEMAND_IDX": "DEMAND_IDX_ORIGEM_POLO",
            "TIPO_UNIDADE": "TIPO_CANDIDATO",
            "POPULACAO_MUNICIPIO": "POPULACAO_SEDE_REFERENCIA",
        }
    )
    out["CANDIDATE_IDX"] = np.arange(len(out), dtype=int)
    out["CANDIDATE_ID"] = "POLO-" + out["DEMAND_ID_ORIGEM_POLO"].astype(str)
    out["DESC_GERENCIA_AREA_PROPOSTA"] = out["UF"].map(v3.DESC_AREA_POR_UF)
    if out["DESC_GERENCIA_AREA_PROPOSTA"].isna().any():
        raise RuntimeError("Existem candidatos sem area derivada da UF.")
    if len(out) < 135:
        raise RuntimeError("A demanda possui menos de 135 candidatos territoriais.")
    return out.reset_index(drop=True)


def same_state_components(
    neighbors: dict[int, set[int]], demand: pd.DataFrame
) -> list[set[int]]:
    uf = demand["UF"].astype(str).to_numpy()
    left = set(range(len(demand)))
    result: list[set[int]] = []
    while left:
        start = min(left)
        left.remove(start)
        queue = deque([start])
        component = {start}
        while queue:
            unit = queue.popleft()
            for other in sorted(neighbors.get(unit, set())):
                if other in left and uf[other] == uf[unit]:
                    left.remove(other)
                    component.add(other)
                    queue.append(other)
        result.append(component)
    return result


def distance_from_unit(demand: pd.DataFrame, unit: int) -> np.ndarray:
    row = demand.iloc[int(unit)]
    return v3.haversine_arrays(
        demand["LATITUDE"].to_numpy(float),
        demand["LONGITUDE"].to_numpy(float),
        np.full(len(demand), float(row["LATITUDE"])),
        np.full(len(demand), float(row["LONGITUDE"])),
    )


def select_135_poles(
    demand: pd.DataFrame,
    neighbors: dict[int, set[int]],
    cfg: V5Config,
) -> list[int]:
    components = same_state_components(neighbors, demand)
    if len(components) > cfg.manager_count:
        detail = [
            {
                "UF": str(demand.iloc[min(component)]["UF"]),
                "QTD_UNIDADES": len(component),
            }
            for component in components
        ]
        raise RuntimeError(
            f"Sao necessarios {len(components)} polos para cobrir os componentes "
            f"territoriais, acima do limite 135: {json.dumps(detail, ensure_ascii=False)}"
        )

    relevance = demand["RELEVANCIA_TERRITORIAL"].to_numpy(float)
    population = demand["POPULACAO_UNIDADE"].to_numpy(float)
    demand_id = demand["DEMAND_ID"].astype(str).to_numpy()
    selected: list[int] = []
    for component in components:
        pool = sorted(
            component,
            key=lambda unit: (-relevance[unit], -population[unit], demand_id[unit]),
        )
        selected.append(int(pool[0]))

    selected = list(dict.fromkeys(selected))
    nearest = np.full(len(demand), np.inf, dtype=float)
    for unit in selected:
        nearest = np.minimum(nearest, distance_from_unit(demand, unit))

    chosen = np.zeros(len(demand), dtype=bool)
    chosen[np.asarray(selected, dtype=int)] = True
    while len(selected) < cfg.manager_count:
        score = relevance * nearest
        score[chosen] = -np.inf
        best_score = float(np.max(score))
        if not np.isfinite(best_score):
            raise RuntimeError("Nao foi possivel completar os 135 polos.")
        tied = np.flatnonzero(np.isclose(score, best_score, rtol=0, atol=1e-15))
        unit = int(
            sorted(
                tied.tolist(),
                key=lambda idx: (-population[idx], demand_id[idx]),
            )[0]
        )
        selected.append(unit)
        chosen[unit] = True
        nearest = np.minimum(nearest, distance_from_unit(demand, unit))
        if len(selected) % 25 == 0 or len(selected) == cfg.manager_count:
            logging.info("Selecao populacional: %s/%s polos", len(selected), 135)

    if len(selected) != 135 or len(set(selected)) != 135:
        raise RuntimeError("A selecao nao produziu exatamente 135 polos unicos.")
    return selected


def edge_distance(demand: pd.DataFrame, left: int, right: int) -> float:
    a = demand.iloc[int(left)]
    b = demand.iloc[int(right)]
    return float(
        v3.haversine_arrays(
            np.array([float(a["LATITUDE"])]),
            np.array([float(a["LONGITUDE"])]),
            np.array([float(b["LATITUDE"])]),
            np.array([float(b["LONGITUDE"])]),
        )[0]
    )


def assign_contiguous_same_uf(
    demand: pd.DataFrame,
    selected_units: list[int],
    neighbors: dict[int, set[int]],
) -> TerritorialState:
    count = len(demand)
    position = np.full(count, -1, dtype=int)
    predecessor = np.full(count, -1, dtype=int)
    path_distance = np.full(count, np.inf, dtype=float)
    uf = demand["UF"].astype(str).to_numpy()
    heap: list[tuple[float, int, int, int]] = []

    for cluster, root in enumerate(selected_units):
        root = int(root)
        if position[root] >= 0:
            raise RuntimeError("Dois polos foram selecionados na mesma unidade.")
        position[root] = cluster
        predecessor[root] = -1
        path_distance[root] = 0.0
    for cluster, root in enumerate(selected_units):
        root = int(root)
        for other in sorted(neighbors.get(root, set())):
            if position[other] >= 0 or uf[other] != uf[root]:
                continue
            heapq.heappush(
                heap,
                (edge_distance(demand, root, other), cluster, int(other), root),
            )

    while heap:
        distance, cluster, unit, parent = heapq.heappop(heap)
        if position[unit] >= 0:
            continue
        pole_unit = int(selected_units[cluster])
        if uf[unit] != uf[pole_unit]:
            continue
        position[unit] = cluster
        predecessor[unit] = parent
        path_distance[unit] = distance
        for other in sorted(neighbors.get(unit, set())):
            if position[other] >= 0 or uf[other] != uf[pole_unit]:
                continue
            heapq.heappush(
                heap,
                (
                    distance + edge_distance(demand, unit, other),
                    cluster,
                    int(other),
                    unit,
                ),
            )

    missing = np.flatnonzero(position < 0)
    if len(missing):
        detail = demand.iloc[missing][
            ["DEMAND_ID", "NM_MUN", "NM_DIST", "UF"]
        ].head(30)
        raise RuntimeError(
            f"{len(missing)} unidades obrigatorias ficaram sem caminho ate um polo: "
            + json.dumps(detail.to_dict("records"), ensure_ascii=False, default=str)
        )

    direct = np.empty(count, dtype=float)
    for cluster, root in enumerate(selected_units):
        members = np.flatnonzero(position == cluster)
        pole = demand.iloc[int(root)]
        direct[members] = v3.haversine_arrays(
            demand.iloc[members]["LATITUDE"].to_numpy(float),
            demand.iloc[members]["LONGITUDE"].to_numpy(float),
            np.full(len(members), float(pole["LATITUDE"])),
            np.full(len(members), float(pole["LONGITUDE"])),
        )
    return TerritorialState(selected_units, position, predecessor, path_distance, direct)


def state_quality(state: TerritorialState, demand: pd.DataFrame) -> tuple[float, ...]:
    relevance = demand["RELEVANCIA_TERRITORIAL"].to_numpy(float)
    population = demand["POPULACAO_UNIDADE"].clip(lower=0).to_numpy(float)
    distance = state.direct_distance
    weighted_cost = float(np.sum(distance * relevance))
    p90 = v3.weighted_percentile(
        pd.Series(distance), pd.Series(population), 0.90
    )
    mean = float(np.average(distance, weights=np.maximum(population, 1e-12)))
    maximum = float(np.max(distance))
    return weighted_cost, p90, mean, maximum


def quality_is_better(new: tuple[float, ...], old: tuple[float, ...]) -> bool:
    tolerance = 1e-8
    if new[0] < old[0] - tolerance:
        return True
    if new[0] > old[0] + tolerance:
        return False
    return new[1:] < old[1:]


def weighted_medoid(members: np.ndarray, demand: pd.DataFrame) -> int:
    if len(members) == 1:
        return int(members[0])
    frame = demand.iloc[members]
    matrix = v3.pairwise_haversine_matrix(
        frame, frame, "LATITUDE", "LONGITUDE", "LATITUDE", "LONGITUDE"
    )
    weights = frame["RELEVANCIA_TERRITORIAL"].to_numpy(float)
    scores = np.sum(matrix * weights[:, None], axis=0)
    population = frame["POPULACAO_UNIDADE"].to_numpy(float)
    best_score = float(np.min(scores))
    tied = np.flatnonzero(np.isclose(scores, best_score, rtol=0, atol=1e-10))
    local = sorted(
        tied.tolist(),
        key=lambda idx: (-population[idx], str(frame.iloc[idx]["DEMAND_ID"])),
    )[0]
    return int(members[local])


def refine_state(
    initial: TerritorialState,
    demand: pd.DataFrame,
    neighbors: dict[int, set[int]],
    cfg: V5Config,
) -> TerritorialState:
    current = initial
    current_quality = state_quality(current, demand)
    for iteration in range(cfg.refine_iterations_v5):
        proposed = []
        for cluster in range(len(current.selected_units)):
            members = np.flatnonzero(current.position == cluster)
            proposed.append(weighted_medoid(members, demand))
        if proposed == current.selected_units:
            break
        trial = assign_contiguous_same_uf(demand, proposed, neighbors)
        trial_quality = state_quality(trial, demand)
        improved = quality_is_better(trial_quality, current_quality)
        logging.info(
            "Melhoria local %s | custo %.6f -> %.6f | P90 %.2f -> %.2f | aceita=%s",
            iteration + 1,
            current_quality[0],
            trial_quality[0],
            current_quality[1],
            trial_quality[1],
            improved,
        )
        if not improved:
            break
        current = trial
        current_quality = trial_quality
    return current


def build_current_baseline(
    demand: pd.DataFrame,
    stores_by_unit: pd.DataFrame,
    current_poles: pd.DataFrame,
) -> pd.DataFrame:
    current = current_poles.reset_index(drop=True).copy()
    current["CHAVE_SUPERVISAO"] = current["CHAVE_SUPERVISAO"].astype(str)
    supervisor_index = {
        str(value): idx for idx, value in enumerate(current["CHAVE_SUPERVISAO"])
    }
    all_distance = v3.pairwise_haversine_matrix(
        demand,
        current,
        "LATITUDE",
        "LONGITUDE",
        "LATITUDE_ATUAL",
        "LONGITUDE_ATUAL",
    )
    counts = (
        stores_by_unit.dropna(subset=["CHAVE_SUPERVISAO"])
        .assign(CHAVE_SUPERVISAO=lambda frame: frame["CHAVE_SUPERVISAO"].astype(str))
        .groupby(["DEMAND_ID", "CHAVE_SUPERVISAO"])["CHAVE_LOJA"]
        .nunique()
        .rename("QTD_LOJAS_SUPERVISOR")
        .reset_index()
    )
    by_unit = {key: group for key, group in counts.groupby("DEMAND_ID")}
    rows: list[dict[str, Any]] = []
    for unit, row in demand.reset_index(drop=True).iterrows():
        group = by_unit.get(str(row["DEMAND_ID"]))
        valid: list[tuple[str, int]] = []
        if group is not None:
            for item in group.itertuples(index=False):
                supervisor = str(item.CHAVE_SUPERVISAO)
                if supervisor in supervisor_index:
                    valid.append((supervisor, int(item.QTD_LOJAS_SUPERVISOR)))
        if valid:
            maximum = max(value for _, value in valid)
            tied = sorted(supervisor for supervisor, value in valid if value == maximum)
            if len(tied) == 1:
                chosen_supervisor = tied[0]
                method = "SUPERVISOR_DOMINANTE"
            else:
                indexed = [(supervisor_index[value], value) for value in tied]
                minimum = min(float(all_distance[unit, idx]) for idx, _ in indexed)
                closest = sorted(
                    value
                    for idx, value in indexed
                    if np.isclose(all_distance[unit, idx], minimum, atol=1e-9)
                )
                chosen_supervisor = closest[0]
                method = (
                    "DESEMPATE_MENOR_DISTANCIA"
                    if len(closest) == 1
                    else "DESEMPATE_ID_ESTAVEL"
                )
            pole_idx = supervisor_index[chosen_supervisor]
        else:
            pole_idx = int(np.argmin(all_distance[unit]))
            chosen_supervisor = str(current.iloc[pole_idx]["CHAVE_SUPERVISAO"])
            method = "FALLBACK_POLO_ATUAL_MAIS_PROXIMO"
        pole = current.iloc[pole_idx]
        rows.append(
            {
                "DEMAND_ID": row["DEMAND_ID"],
                "SUPERVISOR_ATUAL_DOMINANTE": chosen_supervisor,
                "POLO_ATUAL_REFERENCIA_ID": chosen_supervisor,
                "METODO_BASELINE": method,
                "LATITUDE_POLO_ATUAL": float(pole["LATITUDE_ATUAL"]),
                "LONGITUDE_POLO_ATUAL": float(pole["LONGITUDE_ATUAL"]),
                "DISTANCIA_ATUAL_X_KM": float(all_distance[unit, pole_idx]),
            }
        )
    return pd.DataFrame(rows)


def build_assignments(
    state: TerritorialState,
    demand: pd.DataFrame,
    baseline: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> pd.DataFrame:
    out = demand.copy()
    out.insert(0, "RUN_ID", run_id)
    out.insert(1, "CENARIO_ID", scenario_id)
    out["CLUSTER_IDX"] = state.position
    out["GERENCIA_ID"] = out["CLUSTER_IDX"].map(
        lambda value: f"V5-G{int(value) + 1:03d}"
    )
    selected = np.asarray(state.selected_units, dtype=int)
    roots = demand.iloc[selected].reset_index(drop=True)
    out["CANDIDATE_ID"] = out["CLUSTER_IDX"].map(
        lambda value: "POLO-" + str(roots.iloc[int(value)]["DEMAND_ID"])
    )
    for target, source in (
        ("COD_IBGE_POLO", "COD_IBGE"),
        ("CD_DIST_POLO", "CD_DIST"),
        ("NM_MUN_POLO", "NM_MUN"),
        ("NM_DIST_POLO", "NM_DIST"),
        ("UF_POLO", "UF"),
        ("LATITUDE_POLO", "LATITUDE"),
        ("LONGITUDE_POLO", "LONGITUDE"),
    ):
        out[target] = out["CLUSTER_IDX"].map(
            lambda value, column=source: roots.iloc[int(value)][column]
        )
    predecessor_id = demand.set_index("DEMAND_IDX")["DEMAND_ID"].to_dict()
    out["PREDECESSOR_DEMAND_ID"] = [
        predecessor_id.get(int(value), pd.NA) if int(value) >= 0 else pd.NA
        for value in state.predecessor
    ]
    out["DISTANCIA_CAMINHO_KM"] = state.path_distance
    out["DISTANCIA_PROPOSTA_Y_KM"] = state.direct_distance
    out["DISTANCIA_KM"] = state.direct_distance
    out["ATENDIDA"] = True
    out["MOTIVO_NAO_ATENDIMENTO"] = pd.NA
    out["METODO_ATRIBUICAO"] = "EXPANSAO_CONTIGUA_MESMA_UF_V5"
    out["CRUZA_UF"] = out["UF"].astype(str) != out["UF_POLO"].astype(str)
    out = out.merge(baseline, on="DEMAND_ID", how="left", validate="one_to_one")
    out["REDUCAO_DISTANCIA_KM"] = (
        out["DISTANCIA_ATUAL_X_KM"] - out["DISTANCIA_PROPOSTA_Y_KM"]
    )
    out["PERC_REDUCAO_DISTANCIA"] = np.where(
        out["DISTANCIA_ATUAL_X_KM"] > 0,
        out["REDUCAO_DISTANCIA_KM"] / out["DISTANCIA_ATUAL_X_KM"],
        np.nan,
    )
    tolerance = 1e-9
    out["STATUS_DISTANCIA"] = np.select(
        [
            out["REDUCAO_DISTANCIA_KM"] > tolerance,
            out["REDUCAO_DISTANCIA_KM"] < -tolerance,
        ],
        ["MELHOROU", "PIOROU"],
        default="MANTEVE",
    )
    return out


def weighted_metrics(group: pd.DataFrame) -> dict[str, Any]:
    population = group["POPULACAO_UNIDADE"].clip(lower=0).astype(float)
    weights = np.maximum(population.to_numpy(float), 1e-12)
    current = group["DISTANCIA_ATUAL_X_KM"].astype(float)
    proposed = group["DISTANCIA_PROPOSTA_Y_KM"].astype(float)
    current_p90 = v3.weighted_percentile(current, population, 0.90)
    proposed_p90 = v3.weighted_percentile(proposed, population, 0.90)
    current_mean = float(np.average(current, weights=weights))
    proposed_mean = float(np.average(proposed, weights=weights))
    current_max = float(current.max())
    proposed_max = float(proposed.max())
    result = {
        "RAIO_P90_ATUAL_X_KM": current_p90,
        "RAIO_P90_PROPOSTO_Y_KM": proposed_p90,
        "REDUCAO_RAIO_P90_KM": current_p90 - proposed_p90,
        "DISTANCIA_MEDIA_ATUAL_X_KM": current_mean,
        "DISTANCIA_MEDIA_PROPOSTA_Y_KM": proposed_mean,
        "REDUCAO_DISTANCIA_MEDIA_KM": current_mean - proposed_mean,
        "DISTANCIA_MAX_ATUAL_X_KM": current_max,
        "DISTANCIA_MAX_PROPOSTA_Y_KM": proposed_max,
        "REDUCAO_DISTANCIA_MAX_KM": current_max - proposed_max,
        "QTD_UNIDADES_MELHORARAM": int(group["STATUS_DISTANCIA"].eq("MELHOROU").sum()),
        "QTD_UNIDADES_MANTIVERAM": int(group["STATUS_DISTANCIA"].eq("MANTEVE").sum()),
        "QTD_UNIDADES_PIORARAM": int(group["STATUS_DISTANCIA"].eq("PIOROU").sum()),
        "POPULACAO_MELHOROU": float(
            group.loc[group["STATUS_DISTANCIA"].eq("MELHOROU"), "POPULACAO_UNIDADE"].sum()
        ),
        "POPULACAO_MANTEVE": float(
            group.loc[group["STATUS_DISTANCIA"].eq("MANTEVE"), "POPULACAO_UNIDADE"].sum()
        ),
        "POPULACAO_PIOROU": float(
            group.loc[group["STATUS_DISTANCIA"].eq("PIOROU"), "POPULACAO_UNIDADE"].sum()
        ),
    }
    result["PERC_REDUCAO_RAIO_P90"] = (
        result["REDUCAO_RAIO_P90_KM"] / current_p90 if current_p90 > 0 else np.nan
    )
    result["PERC_REDUCAO_DISTANCIA_MEDIA"] = (
        result["REDUCAO_DISTANCIA_MEDIA_KM"] / current_mean
        if current_mean > 0
        else np.nan
    )
    result["PERC_REDUCAO_DISTANCIA_MAX"] = (
        result["REDUCAO_DISTANCIA_MAX_KM"] / current_max
        if current_max > 0
        else np.nan
    )
    return result


def build_portfolio_comparison(assignments: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for manager_id, group in assignments.groupby("GERENCIA_ID", sort=True):
        row = {
            "GERENCIA_ID": manager_id,
            "QTD_UNIDADES": len(group),
            "QTD_MUNICIPIOS": int(group["COD_IBGE"].nunique()),
            "POPULACAO_TOTAL": float(group["POPULACAO_UNIDADE"].sum()),
            "QTD_LOJAS": int(group["QTD_LOJAS"].sum()),
            "PERC_UNIDADES_BASELINE": float(group["DISTANCIA_ATUAL_X_KM"].notna().mean()),
        }
        row.update(weighted_metrics(group))
        rows.append(row)
    return pd.DataFrame(rows)


def build_managers(
    state: TerritorialState,
    assignments: pd.DataFrame,
    demand: pd.DataFrame,
    portfolio_comparison: pd.DataFrame,
    run_id: str,
    scenario_id: str,
) -> pd.DataFrame:
    rows = []
    for cluster, root_idx in enumerate(state.selected_units):
        root = demand.iloc[int(root_idx)]
        members = assignments[assignments["CLUSTER_IDX"].eq(cluster)]
        rows.append(
            {
                "RUN_ID": run_id,
                "CENARIO_ID": scenario_id,
                "GERENCIA_ID": f"V5-G{cluster + 1:03d}",
                "CANDIDATE_ID": "POLO-" + str(root["DEMAND_ID"]),
                "DEMAND_ID_ORIGEM_POLO": root["DEMAND_ID"],
                "COD_IBGE_POLO": root["COD_IBGE"],
                "CD_DIST_POLO": root["CD_DIST"],
                "NM_MUN_POLO": root["NM_MUN"],
                "NM_DIST_POLO": root["NM_DIST"],
                "UF_POLO": root["UF"],
                "LATITUDE": float(root["LATITUDE"]),
                "LONGITUDE": float(root["LONGITUDE"]),
                "DESC_GERENCIA_AREA_PROPOSTA": v3.DESC_AREA_POR_UF.get(str(root["UF"])),
                "POPULACAO_SEDE_REFERENCIA": float(root["POPULACAO_MUNICIPIO"]),
                "QTD_UNIDADES": len(members),
                "QTD_MUNICIPIOS": int(members["COD_IBGE"].nunique()),
                "POPULACAO_ATENDIDA": float(members["POPULACAO_UNIDADE"].sum()),
                "QTD_LOJAS": int(members["QTD_LOJAS"].sum()),
                "RELEVANCIA_TOTAL": float(members["RELEVANCIA_TERRITORIAL"].sum()),
            }
        )
    managers = pd.DataFrame(rows)
    metric_columns = [
        column
        for column in portfolio_comparison.columns
        if column == "GERENCIA_ID" or column not in managers.columns
    ]
    return managers.merge(
        portfolio_comparison[metric_columns], on="GERENCIA_ID", how="left"
    )


def link_regionals(regional: pd.DataFrame, managers: pd.DataFrame) -> pd.DataFrame:
    distance = v3.pairwise_haversine_matrix(
        regional, managers, "LATITUDE", "LONGITUDE", "LATITUDE", "LONGITUDE"
    )
    chosen = np.argmin(distance, axis=1)
    rows = []
    for regional_idx, manager_idx in enumerate(chosen):
        gr = regional.iloc[regional_idx]
        manager = managers.iloc[int(manager_idx)]
        rows.append(
            {
                "RUN_ID": manager["RUN_ID"],
                "CENARIO_ID": manager["CENARIO_ID"],
                "COD_GER_REG": gr["COD_GER_REG"],
                "GER_REGIONAL": gr["GER_REGIONAL"],
                "UF_GR": gr["UF_GR"],
                "LATITUDE_GR": float(gr["LATITUDE"]),
                "LONGITUDE_GR": float(gr["LONGITUDE"]),
                "GERENCIA_ID": manager["GERENCIA_ID"],
                "CANDIDATE_ID": manager["CANDIDATE_ID"],
                "DISTANCIA_GR_POLO_KM": float(distance[regional_idx, manager_idx]),
                "TIPO_VINCULO_GR": "POLO_COMPARTILHAVEL_MAIS_PROXIMO",
            }
        )
    links = pd.DataFrame(rows)
    if links["COD_GER_REG"].nunique() != 81:
        raise RuntimeError("Nem todas as 81 GRs receberam vinculo com polo.")
    return links


def assign_stores(
    stores_by_unit: pd.DataFrame, assignments: pd.DataFrame
) -> pd.DataFrame:
    columns = [
        "DEMAND_ID",
        "GERENCIA_ID",
        "CANDIDATE_ID",
        "COD_IBGE_POLO",
        "CD_DIST_POLO",
        "NM_MUN_POLO",
        "NM_DIST_POLO",
        "UF_POLO",
        "LATITUDE_POLO",
        "LONGITUDE_POLO",
    ]
    out = stores_by_unit.merge(
        assignments[columns].drop_duplicates("DEMAND_ID"),
        on="DEMAND_ID",
        how="left",
        validate="many_to_one",
    )
    out["DISTANCIA_LOJA_POLO_KM"] = v3.haversine_arrays(
        out["LATITUDE"].to_numpy(float),
        out["LONGITUDE"].to_numpy(float),
        out["LATITUDE_POLO"].to_numpy(float),
        out["LONGITUDE_POLO"].to_numpy(float),
    )
    return out


def build_audit(
    assignments: pd.DataFrame,
    managers: pd.DataFrame,
    regional_links: pd.DataFrame,
    excluded: set[str],
) -> pd.DataFrame:
    checks = [
        ("QTD_POLOS", int(managers["GERENCIA_ID"].nunique()), 135),
        ("QTD_CARTEIRAS", int(assignments["GERENCIA_ID"].nunique()), 135),
        ("UNIDADES_DUPLICADAS", int(assignments["DEMAND_ID"].duplicated().sum()), 0),
        ("UNIDADES_SEM_POLO", int(assignments["GERENCIA_ID"].isna().sum()), 0),
        ("UNIDADES_SEM_PREDECESSOR_OU_RAIZ", int((assignments["DISTANCIA_CAMINHO_KM"].isna()).sum()), 0),
        ("CRUZAMENTOS_UF_INVALIDOS", int(assignments["CRUZA_UF"].sum()), 0),
        ("GRS_VINCULADAS", int(regional_links["COD_GER_REG"].nunique()), 81),
        (
            "MUNICIPIOS_EXCLUIDOS_ATENDIDOS",
            int(assignments["COD_IBGE"].astype(str).isin(excluded).sum()),
            0,
        ),
    ]
    return pd.DataFrame(
        [
            {
                "REGRA": name,
                "VALOR": value,
                "ESPERADO": expected,
                "STATUS": "OK" if value == expected else "VIOLACAO",
            }
            for name, value, expected in checks
        ]
    )


def validate_web_contract(
    scenario: pd.DataFrame,
    managers: pd.DataFrame,
    assignments: pd.DataFrame,
    unit_geo: gpd.GeoDataFrame,
) -> None:
    scenario_required = {"MODELO_VERSAO", "DATA_EXECUCAO"}
    manager_required = {
        "GERENCIA_ID",
        "NM_MUN_POLO",
        "LATITUDE",
        "LONGITUDE",
        "DESC_GERENCIA_AREA_PROPOSTA",
        "UF_POLO",
        "COD_IBGE_POLO",
    }
    assignment_required = {
        "DEMAND_ID",
        "GERENCIA_ID",
        "TIPO_UNIDADE",
        "COD_IBGE",
        "NM_MUN",
        "UF",
        "POPULACAO_UNIDADE",
        "QTD_LOJAS",
        "DISTANCIA_KM",
    }
    missing_scenario = sorted(scenario_required - set(scenario.columns))
    missing_managers = sorted(manager_required - set(managers.columns))
    missing_assignments = sorted(assignment_required - set(assignments.columns))
    if missing_scenario or missing_managers or missing_assignments:
        raise RuntimeError(
            "Contrato web V5 incompleto: "
            + json.dumps(
                {
                    "cenario": missing_scenario,
                    "gerencias_propostas": missing_managers,
                    "carteiras_unidades": missing_assignments,
                },
                ensure_ascii=False,
            )
        )
    if scenario.empty or "V5" not in str(scenario.iloc[0]["MODELO_VERSAO"]).upper():
        raise RuntimeError("MODELO_VERSAO nao identifica o cenario como V5.")
    if len(managers) != 135 or managers["GERENCIA_ID"].nunique() != 135:
        raise RuntimeError("O contrato web exige exatamente 135 polos V5 unicos.")
    if managers[list(manager_required)].isna().any().any():
        raise RuntimeError("Existem campos obrigatorios nulos em gerencias_propostas.")
    if assignments[list(assignment_required)].isna().any().any():
        raise RuntimeError("Existem campos obrigatorios nulos em carteiras_unidades.")
    if assignments["DEMAND_ID"].duplicated().any():
        raise RuntimeError("O GeoJSON web teria DEMAND_ID duplicado.")
    if not set(assignments["GERENCIA_ID"]).issubset(set(managers["GERENCIA_ID"])):
        raise RuntimeError("Carteira referencia GERENCIA_ID inexistente no Excel.")
    geometry_ids = set(unit_geo["DEMAND_ID"].astype(str))
    assignment_ids = set(assignments["DEMAND_ID"].astype(str))
    if geometry_ids != assignment_ids:
        raise RuntimeError(
            "As geometrias e as unidades do Excel nao possuem o mesmo universo."
        )
    if unit_geo.geometry.isna().any() or unit_geo.geometry.is_empty.any():
        raise RuntimeError("Existem geometrias vazias no contrato web V5.")


def build_scenario(
    assignments: pd.DataFrame,
    managers: pd.DataFrame,
    regional_links: pd.DataFrame,
    cfg: V5Config,
    run_id: str,
    scenario_id: str,
    elapsed_seconds: float,
) -> pd.DataFrame:
    metrics = weighted_metrics(assignments)
    reduced = metrics["REDUCAO_RAIO_P90_KM"] > 0
    status = "CALCULADO" if reduced else "CALCULADO_COM_RESSALVAS"
    row = {
        "RUN_ID": run_id,
        "CENARIO_ID": scenario_id,
        "MODELO_VERSAO": cfg.model_version,
        "STATUS_CENARIO": status,
        "RESSALVA": pd.NA if reduced else "SEM_REDUCAO_RAIO_P90",
        "QTD_GERENCIAS_SOLICITADA": 135,
        "QTD_GERENCIAS_SELECIONADA": int(managers["GERENCIA_ID"].nunique()),
        "QTD_UNIDADES_ATENDIDAS": len(assignments),
        "QTD_MUNICIPIOS_ATENDIDOS": int(assignments["COD_IBGE"].nunique()),
        "POPULACAO_ATENDIDA": float(assignments["POPULACAO_UNIDADE"].sum()),
        "QTD_LOJAS_REFERENCIA": int(assignments["QTD_LOJAS"].sum()),
        "QTD_GR_VINCULADAS": int(regional_links["COD_GER_REG"].nunique()),
        "QTD_CRUZAMENTOS_UF": int(assignments["CRUZA_UF"].sum()),
        "ENFASE_LOJAS": cfg.store_emphasis,
        "TEMPO_SEGUNDOS": elapsed_seconds,
        "DATA_EXECUCAO": datetime.now(),
    }
    row.update(metrics)
    return pd.DataFrame([row])


def save_checkpoint(
    run_folder: Path,
    assignments: pd.DataFrame,
    managers: pd.DataFrame,
    cfg: V5Config,
) -> None:
    if not cfg.save_checkpoints or not cfg.save_excel:
        return
    path = run_folder / "checkpoint_solucao_inicial_v5.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        managers.to_excel(writer, sheet_name="gerencias_propostas", index=False)
        assignments.to_excel(writer, sheet_name="unidades_atendidas", index=False)
    logging.info("Checkpoint inicial salvo: %s", path)


def save_outputs(
    run_folder: Path,
    scenario: pd.DataFrame,
    managers: pd.DataFrame,
    assignments: pd.DataFrame,
    stores: pd.DataFrame,
    current: pd.DataFrame,
    regional_links: pd.DataFrame,
    portfolio_comparison: pd.DataFrame,
    exclusions: pd.DataFrame,
    audit: pd.DataFrame,
    territorial_audit: pd.DataFrame,
    unit_geo: gpd.GeoDataFrame,
    cfg: V5Config,
) -> None:
    scenario_id = str(scenario.iloc[0]["CENARIO_ID"])
    if cfg.save_excel:
        path = run_folder / f"resultado_{scenario_id}.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            sheets = [
                ("cenario", scenario),
                ("gerencias_propostas", managers),
                ("unidades_atendidas", assignments),
                ("comparacao_raio", portfolio_comparison),
                ("baseline_unidades", assignments[[c for c in assignments.columns if c in {
                    "RUN_ID", "CENARIO_ID", "DEMAND_ID", "TIPO_UNIDADE", "COD_IBGE", "CD_DIST", "NM_MUN", "NM_DIST", "UF", "POPULACAO_UNIDADE", "SUPERVISOR_ATUAL_DOMINANTE", "POLO_ATUAL_REFERENCIA_ID", "METODO_BASELINE", "DISTANCIA_ATUAL_X_KM", "DISTANCIA_PROPOSTA_Y_KM", "REDUCAO_DISTANCIA_KM", "PERC_REDUCAO_DISTANCIA", "STATUS_DISTANCIA"
                }]]),
                ("lojas_propostas", stores),
                ("gerencias_atuais", current),
                ("vinculo_gr_polo", regional_links),
                ("municipios_excluidos", exclusions),
                ("auditoria", audit),
                ("auditoria_territorial", territorial_audit),
            ]
            for name, frame in sheets:
                frame.to_excel(writer, sheet_name=name[:31], index=False)
        logging.info("Resultado Excel salvo: %s", path)

    if cfg.save_geojson:
        manager_geojson = v3.dataframe_to_point_geojson(
            managers,
            "LATITUDE",
            "LONGITUDE",
            [
                "GERENCIA_ID",
                "CANDIDATE_ID",
                "COD_IBGE_POLO",
                "NM_MUN_POLO",
                "UF_POLO",
                "POPULACAO_ATENDIDA",
                "QTD_LOJAS",
                "RAIO_P90_PROPOSTO_Y_KM",
            ],
        )
        (run_folder / "gerencias_propostas.geojson").write_text(
            json.dumps(manager_geojson, ensure_ascii=False), encoding="utf-8"
        )
        properties = [
            "DEMAND_ID",
            "GERENCIA_ID",
            "TIPO_UNIDADE",
            "COD_IBGE",
            "CD_DIST",
            "NM_MUN",
            "NM_DIST",
            "UF",
            "POPULACAO_UNIDADE",
            "QTD_LOJAS",
            "DISTANCIA_KM",
            "DISTANCIA_ATUAL_X_KM",
            "REDUCAO_DISTANCIA_KM",
            "STATUS_DISTANCIA",
            "PREDECESSOR_DEMAND_ID",
        ]
        merge_columns = ["DEMAND_ID"] + [
            column
            for column in properties
            if column != "DEMAND_ID" and column not in unit_geo.columns
        ]
        territory = unit_geo.merge(
            assignments[merge_columns],
            on="DEMAND_ID",
            how="inner",
            validate="one_to_one",
        )
        territory.to_file(run_folder / "carteiras_unidades.geojson", driver="GeoJSON")
        territory[["GERENCIA_ID", "geometry"]].dissolve(
            by="GERENCIA_ID", as_index=False
        ).to_file(run_folder / "carteiras_dissolvidas.geojson", driver="GeoJSON")


def persist_sql(
    engine: Engine,
    sql: v3.SQLConfig,
    cfg: V5Config,
    frames: list[tuple[str, pd.DataFrame]],
) -> None:
    if not cfg.save_sql:
        return
    for table, frame in frames:
        v3.write_sql_table(engine, frame, table, sql, cfg)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = config_from_args(args)
    v3.configure_logging()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex.upper()
    scenario_id = f"V5_135_{run_id[:8]}"
    run_folder = cfg.output_dir / scenario_id
    run_folder.mkdir(parents=True, exist_ok=True)
    sql = v3.SQLConfig()
    engine = v3.create_sql_engine(sql)
    started = datetime.now()
    execution = {
        "RUN_ID": run_id,
        "CENARIO_ID": scenario_id,
        "MODELO_VERSAO": cfg.model_version,
        "CONFIG": json.dumps(asdict(cfg), default=str, ensure_ascii=False),
        "PERIODO_LOJAS": cfg.periodo_lojas,
        "DATA_INICIO": started,
        "STATUS": "EM_EXECUCAO",
    }

    try:
        v3.log_step("GREENFIELD V5 1/8 - GEOGRAFIA E SQL")
        district_ref, district_geo = v3.load_district_data(cfg)
        municipal_geo = v3.load_municipal_geometry(cfg)
        raw = v3.load_raw_data(engine, cfg)
        municipal_ref = v3.prepare_municipal_reference(
            raw["municipalities"], raw["population"], municipal_geo
        )
        excluded, exclusion_audit = load_sql_exclusions(engine, municipal_ref, cfg)
        metro_codes = validate_metropolitan_districts(
            municipal_ref, district_ref, district_geo, excluded, cfg
        )
        district_ref, district_geo, district_audit = v3.reconcile_district_geometries(
            district_ref, district_geo, municipal_geo, metro_codes
        )

        v3.log_step("GREENFIELD V5 2/8 - DEMANDA OBRIGATORIA")
        demand, split = v3.build_hybrid_demand_units(
            municipal_ref, district_ref, excluded, cfg
        )
        v3.validate_demand_exclusivity(demand, split)
        stores = v3.prepare_stores(raw["stores"], municipal_ref)
        stores_by_unit = v3.assign_stores_to_demand_units(
            stores, demand, split, district_geo
        )
        demand = enrich_relevance(demand, stores_by_unit, cfg)
        candidates = build_candidates(demand)
        current = v3.prepare_current_poles(raw["current_poles"], cfg)
        hierarchy = v3.prepare_current_hierarchy(raw["current_hierarchy"])
        current = v3.attach_current_hierarchy(current, hierarchy)
        regional = v3.prepare_regional_points(raw["regional_points"], municipal_geo, cfg)

        v3.log_step("GREENFIELD V5 3/8 - GRAFO TERRITORIAL")
        unit_geo = v3.build_hybrid_unit_geometry(demand, municipal_geo, district_geo)
        geo_units = set(unit_geo["DEMAND_IDX"].astype(int))
        missing_geo = demand[~demand["DEMAND_IDX"].isin(geo_units)]
        if not missing_geo.empty:
            raise RuntimeError(
                f"{len(missing_geo)} unidades obrigatorias sem geometria: "
                + json.dumps(
                    missing_geo[["DEMAND_ID", "NM_MUN", "NM_DIST", "UF"]]
                    .head(30)
                    .to_dict("records"),
                    ensure_ascii=False,
                    default=str,
                )
            )
        territorial_audit = pd.concat(
            [
                district_audit,
                v3.build_territorial_geometry_audit(
                    unit_geo, municipal_geo, demand, cfg
                ),
            ],
            ignore_index=True,
            sort=False,
        )
        neighbors = v3.build_adjacency_graph(unit_geo, len(demand), cfg)

        v3.log_step("GREENFIELD V5 4/8 - SELECAO EXATA DOS 135 POLOS")
        selected = select_135_poles(demand, neighbors, cfg)
        initial_state = assign_contiguous_same_uf(demand, selected, neighbors)
        baseline = build_current_baseline(demand, stores_by_unit, current)
        initial_assignments = build_assignments(
            initial_state, demand, baseline, run_id, scenario_id
        )
        initial_comparison = build_portfolio_comparison(initial_assignments)
        initial_managers = build_managers(
            initial_state,
            initial_assignments,
            demand,
            initial_comparison,
            run_id,
            scenario_id,
        )
        save_checkpoint(run_folder, initial_assignments, initial_managers, cfg)

        v3.log_step("GREENFIELD V5 5/8 - MELHORIA LOCAL CURTA")
        final_state = refine_state(initial_state, demand, neighbors, cfg)
        assignments = build_assignments(
            final_state, demand, baseline, run_id, scenario_id
        )
        comparison = build_portfolio_comparison(assignments)
        managers = build_managers(
            final_state, assignments, demand, comparison, run_id, scenario_id
        )

        v3.log_step("GREENFIELD V5 6/8 - GRS, LOJAS E VALIDACAO")
        regional_links = link_regionals(regional, managers)
        proposed_stores = assign_stores(stores_by_unit, assignments)
        audit = build_audit(assignments, managers, regional_links, excluded)
        if audit["STATUS"].eq("VIOLACAO").any():
            raise RuntimeError(
                "A solucao V5 violou regras obrigatorias: "
                + json.dumps(
                    audit[audit["STATUS"].eq("VIOLACAO")].to_dict("records"),
                    ensure_ascii=False,
                    default=str,
                )
            )
        elapsed = (datetime.now() - started).total_seconds()
        scenario = build_scenario(
            assignments,
            managers,
            regional_links,
            cfg,
            run_id,
            scenario_id,
            elapsed,
        )
        validate_web_contract(scenario, managers, assignments, unit_geo)

        v3.log_step("GREENFIELD V5 7/8 - ARQUIVOS")
        save_outputs(
            run_folder,
            scenario,
            managers,
            assignments,
            proposed_stores,
            current,
            regional_links,
            comparison,
            exclusion_audit,
            audit,
            territorial_audit,
            unit_geo,
            cfg,
        )

        v3.log_step("GREENFIELD V5 8/8 - SQL E CONCLUSAO")
        execution.update(
            {
                "DATA_FIM": datetime.now(),
                "STATUS": "CONCLUIDO",
                "QTD_UNIDADES": len(assignments),
                "QTD_MUNICIPIOS": int(assignments["COD_IBGE"].nunique()),
                "QTD_POLOS": len(managers),
                "QTD_GR": len(regional_links),
                "QTD_EXCLUIDOS": len(exclusion_audit),
                "MENSAGEM": str(scenario.iloc[0]["STATUS_CENARIO"]),
            }
        )
        persist_sql(
            engine,
            sql,
            cfg,
            [
                (T_EXECUCAO, pd.DataFrame([execution])),
                (T_CENARIO, scenario),
                (T_UNIDADE, demand.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_GERENCIA, managers),
                (T_CARTEIRA, assignments),
                (T_LOJA, proposed_stores.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_GR, regional_links),
                (T_BASELINE, baseline.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_COMPARACAO, comparison.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_EXCLUSAO, exclusion_audit.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
                (T_AUDITORIA, audit.assign(RUN_ID=run_id, CENARIO_ID=scenario_id)),
            ],
        )
        logging.info("V5 concluida | RUN_ID=%s | saida=%s", run_id, run_folder)
    except Exception as exc:
        execution.update(
            {
                "DATA_FIM": datetime.now(),
                "STATUS": "ERRO",
                "MENSAGEM": str(exc)[:3000],
            }
        )
        failure = {
            **execution,
            "TRACEBACK": traceback.format_exc(),
        }
        (run_folder / "falha_execucao.json").write_text(
            json.dumps(failure, ensure_ascii=False, default=str, indent=2),
            encoding="utf-8",
        )
        logging.error("Falha V5: %s", exc)
        try:
            if cfg.save_sql:
                v3.write_sql_table(
                    engine, pd.DataFrame([execution]), T_EXECUCAO, sql, cfg
                )
        finally:
            raise


if __name__ == "__main__":
    main()
