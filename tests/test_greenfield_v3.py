from dataclasses import replace

import numpy as np
import pandas as pd

import Estudo_GreenField_V3_COMPLETO as v3


def test_single_official_district_limits_metropolis_to_one_pole() -> None:
    cfg = replace(v3.ModelConfig(), minimum_metropolitan_units_per_manager=2)
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["DIST-120040101", "MUN-1200002"],
            "DEMAND_IDX": [0, 1],
            "TIPO_UNIDADE": ["DISTRITO", "MUNICIPIO"],
            "COD_IBGE": ["1200401", "1200002"],
            "POPULACAO_MUNICIPIO": [400_000.0, 100_000.0],
            "CARGA_EQUIVALENTE": [4.0, 1.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "CANDIDATE_IDX": [0, 1],
            "COD_IBGE": ["1200401", "1200002"],
            "POPULACAO_SEDE_REFERENCIA": [400_000.0, 100_000.0],
        }
    )

    requirements = v3.calculate_metropolitan_requirements(
        demand, candidates, 2, cfg
    )

    metro = requirements.iloc[0]
    assert metro["QTD_UNIDADES_CIDADE"] == 1
    assert metro["MIN_UNIDADES_POR_POLO"] == 1
    assert metro["MAX_POLOS_POR_UNIDADES"] == 1
    assert metro["POLOS_DESEJADOS"] == 1
    assert metro["STATUS_GRANULARIDADE"] == (
        "LIMITADO_A_1_POLO_DISTRITO_OFICIAL_UNICO"
    )
    selected = v3.select_metropolitan_seeds(
        demand,
        candidates,
        np.zeros((2, 2)),
        np.ones(2),
        np.zeros(2),
        requirements,
        2,
        cfg,
        preselected=[0],
    )
    assert selected == [0]


def test_single_district_metropolitan_constraints_remain_feasible() -> None:
    cfg = replace(v3.ModelConfig(), minimum_metropolitan_units_per_manager=2)
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["DIST-120040101", "MUN-1200002"],
            "TIPO_UNIDADE": ["DISTRITO", "MUNICIPIO"],
            "COD_IBGE": ["1200401", "1200002"],
            "UF": ["AC", "AC"],
            "POPULACAO_MUNICIPIO": [400_000.0, 100_000.0],
            "EH_UNIDADE_ESTRATEGICA": [True, True],
            "QTD_LOJAS": [1, 1],
        }
    )
    candidates = pd.DataFrame(
        {
            "CANDIDATE_IDX": [0, 1],
            "COD_IBGE": ["1200401", "1200002"],
            "UF": ["AC", "AC"],
            "POPULACAO_SEDE_REFERENCIA": [400_000.0, 100_000.0],
        }
    )
    position = np.array([0, 1], dtype=int)
    selected = [0, 1]
    neighbors = {0: {1}, 1: {0}}

    adjusted, moves = v3.ensure_metropolitan_minimum_units(
        position,
        selected,
        candidates,
        demand,
        np.zeros((2, 2)),
        np.ones(2),
        np.ones(2),
        neighbors,
        {0, 1},
        cfg,
    )

    assert adjusted.tolist() == [0, 1]
    assert moves == []
    v3.validate_solution_constraints(
        demand, candidates, selected, adjusted, neighbors, 2, cfg
    )
