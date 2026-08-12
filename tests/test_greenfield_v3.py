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


def test_two_gr_anchors_relax_metropolitan_minimum_when_needed() -> None:
    cfg = replace(v3.ModelConfig(), minimum_metropolitan_units_per_manager=2)
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["DIST-310620001", "DIST-310620002", "DIST-310620003"],
            "DEMAND_IDX": [0, 1, 2],
            "TIPO_UNIDADE": ["DISTRITO"] * 3,
            "COD_IBGE": ["3106200"] * 3,
            "POPULACAO_MUNICIPIO": [2_300_000.0] * 3,
            "CARGA_EQUIVALENTE": [1.0, 1.0, 1.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "CANDIDATE_IDX": [0, 1, 2],
            "COD_IBGE": ["3106200"] * 3,
            "POPULACAO_SEDE_REFERENCIA": [2_300_000.0] * 3,
        }
    )
    requirements = v3.calculate_metropolitan_requirements(
        demand, candidates, 3, cfg
    )

    selected = v3.select_metropolitan_seeds(
        demand,
        candidates,
        np.zeros((3, 3)),
        np.ones(3),
        np.zeros(3),
        requirements,
        3,
        cfg,
        preselected=[0, 1],
    )

    metro = requirements.iloc[0]
    assert selected == [0, 1]
    assert metro["MAX_POLOS_COM_MIN_CONFIGURADO"] == 1
    assert metro["MAX_POLOS_POR_UNIDADES"] == 3
    assert metro["POLOS_ANCORA_GR"] == 2
    assert metro["POLOS_DESEJADOS"] == 2
    assert metro["MIN_UNIDADES_EFETIVO_POR_POLO"] == 1
    assert metro["STATUS_GRANULARIDADE"] == "MINIMO_RELAXADO_POR_ANCORAS_GR"
    assert v3.metropolitan_minimum_units_per_pole(3, 1, cfg) == 2
    assert v3.metropolitan_minimum_units_per_pole(3, 2, cfg) == 1


def test_two_anchor_poles_can_share_three_metropolitan_districts() -> None:
    cfg = replace(v3.ModelConfig(), minimum_metropolitan_units_per_manager=2)
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["D0", "D1", "D2", "M3"],
            "TIPO_UNIDADE": ["DISTRITO", "DISTRITO", "DISTRITO", "MUNICIPIO"],
            "COD_IBGE": ["3106200", "3106200", "3106200", "3100001"],
            "UF": ["MG"] * 4,
            "EH_UNIDADE_ESTRATEGICA": [True] * 4,
            "QTD_LOJAS": [1] * 4,
        }
    )
    candidates = pd.DataFrame(
        {
            "CANDIDATE_IDX": [0, 1, 2, 3],
            "COD_IBGE": ["3106200", "3106200", "3106200", "3100001"],
            "UF": ["MG"] * 4,
            "POPULACAO_SEDE_REFERENCIA": [2_300_000.0] * 3 + [100_000.0],
        }
    )
    selected = [0, 1, 3]
    position = np.array([0, 1, 1, 2], dtype=int)
    neighbors = {0: {1}, 1: {0, 2}, 2: {1, 3}, 3: {2}}

    adjusted, moves = v3.ensure_metropolitan_minimum_units(
        position,
        selected,
        candidates,
        demand,
        np.zeros((4, 4)),
        np.ones(4),
        np.ones(4),
        neighbors,
        {0, 1, 3},
        cfg,
    )

    assert adjusted.tolist() == position.tolist()
    assert moves == []
    v3.validate_solution_constraints(
        demand, candidates, selected, adjusted, neighbors, 3, cfg
    )


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
