from dataclasses import replace

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

import Estudo_GreenField_V4_OTIMO as v4


def test_bottleneck_assignment_minimizes_maximum_before_sum() -> None:
    distance = np.array(
        [
            [1.0, 100.0, 100.0],
            [2.0, 3.0, 100.0],
            [100.0, 4.0, 5.0],
        ]
    )
    rows, cols, threshold = v4.bottleneck_assignment(distance)
    chosen = distance[rows, cols]
    assert threshold == pytest.approx(5.0)
    assert chosen.max() == pytest.approx(5.0)
    assert chosen.sum() == pytest.approx(9.0)


def test_district_population_reconciliation() -> None:
    demand = pd.DataFrame(
        {
            "COD_IBGE": ["3550308", "3550308"],
            "TIPO_UNIDADE": ["DISTRITO", "DISTRITO"],
            "POPULACAO_UNIDADE": [60.0, 40.0],
        }
    )
    reference = pd.DataFrame({"COD_IBGE": ["3550308"], "POPULACAO_MUNICIPIO": [100.0]})
    audit = v4.build_district_reconciliation_audit(demand, reference, {"3550308"})
    assert audit.iloc[0]["STATUS"] == "OK"
    assert audit.iloc[0]["DIFERENCA"] == pytest.approx(0.0)


def test_300_thousand_is_split_and_299999_is_not() -> None:
    cfg = v4.V4Config()
    municipal = pd.DataFrame(
        {
            "COD_IBGE": ["3500001", "3500002"],
            "NM_MUN": ["Cidade menor", "Cidade metropolitana"],
            "COD_UF": ["35", "35"],
            "UF": ["SP", "SP"],
            "POPULACAO_MUNICIPIO": [299_999.0, 300_000.0],
            "LATITUDE_MUNICIPIO": [-23.0, -23.5],
            "LONGITUDE_MUNICIPIO": [-46.0, -46.5],
            "AREA_KM2_MUNICIPIO": [100.0, 200.0],
        }
    )
    districts = pd.DataFrame(
        {
            "CD_MUN": ["3500002", "3500002"],
            "CD_DIST": ["350000201", "350000202"],
            "NM_MUN": ["Cidade metropolitana", "Cidade metropolitana"],
            "NM_DIST": ["Distrito A", "Distrito B"],
            "POP_DISTRITO_2022": [2.0, 1.0],
            "LATITUDE_DISTRITO": [-23.4, -23.6],
            "LONGITUDE_DISTRITO": [-46.4, -46.6],
            "AREA_KM2_DISTRITO": [120.0, 80.0],
        }
    )
    demand, split = v4.v3.build_hybrid_demand_units(municipal, districts, set(), cfg)
    smaller = demand[demand["COD_IBGE"] == "3500001"]
    metropolis = demand[demand["COD_IBGE"] == "3500002"]
    assert split == {"3500002"}
    assert smaller["TIPO_UNIDADE"].tolist() == ["MUNICIPIO"]
    assert metropolis["TIPO_UNIDADE"].eq("DISTRITO").all()
    assert len(metropolis) == 2
    assert metropolis["POPULACAO_UNIDADE"].sum() == pytest.approx(300_000.0)


@pytest.mark.parametrize(
    "reconciler",
    [v4.reconcile_district_geometries, v4.v3.reconcile_district_geometries],
    ids=["v4", "v3"],
)
def test_district_geometry_is_clipped_to_parent_municipality(reconciler) -> None:
    municipal_geo = gpd.GeoDataFrame(
        {"CD_MUN": ["3500001", "3500002"]},
        geometry=[box(0, 0, 1_000, 1_000), box(1_000, 0, 2_000, 1_000)],
        crs="EPSG:5880",
    )
    district_geo = gpd.GeoDataFrame(
        {
            "CD_DIST": ["350000101"],
            "CD_MUN": ["3500001"],
            "AREA_KM2_GEOMETRIA": [1.1],
            "LAT_GEOMETRIA": [0.0],
            "LON_GEOMETRIA": [0.0],
        },
        geometry=[box(0, 0, 1_100, 1_000)],
        crs="EPSG:5880",
    )
    district_reference = pd.DataFrame(
        {
            "CD_DIST": ["350000101"],
            "CD_MUN": ["3500001"],
            "AREA_KM2_DISTRITO": [1.1],
            "LATITUDE_DISTRITO": [0.0],
            "LONGITUDE_DISTRITO": [0.0],
        }
    )

    corrected_reference, corrected_geo, audit = reconciler(
        district_reference, district_geo, municipal_geo, {"3500001"}
    )

    clipped = corrected_geo.iloc[0].geometry
    neighbor = municipal_geo.iloc[1].geometry
    assert clipped.area / 1_000_000 == pytest.approx(1.0)
    assert clipped.intersection(neighbor).area == pytest.approx(0.0)
    assert corrected_reference.iloc[0]["AREA_KM2_DISTRITO"] == pytest.approx(1.0)
    district_audit = audit[audit["METRICA"] == "RECORTE_DISTRITO_AO_MUNICIPIO_PAI"]
    assert district_audit.iloc[0]["AREA_REMOVIDA_KM2"] == pytest.approx(0.1)
    assert audit["STATUS"].eq("OK").all()


@pytest.mark.parametrize(
    "assigner",
    [v4.assign_stores_to_v4_demand, v4.v3.assign_stores_to_demand_units],
    ids=["v4", "v3"],
)
def test_store_is_never_assigned_to_district_of_another_municipality(
    assigner,
) -> None:
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["DIST-350000101", "DIST-350000202"],
            "TIPO_UNIDADE": ["DISTRITO", "DISTRITO"],
            "COD_IBGE": ["3500001", "3500002"],
            "CD_DIST": ["350000101", "350000202"],
            "LATITUDE": [-23.0, -23.0],
            "LONGITUDE": [-46.0, -46.0],
        }
    )
    district_geo = gpd.GeoDataFrame(
        {
            "CD_MUN": ["3500001", "3500002"],
            "CD_DIST": ["350000101", "350000202"],
        },
        geometry=[
            box(-46.1, -23.1, -45.9, -22.9),
            box(-46.1, -23.1, -45.9, -22.9),
        ],
        crs="EPSG:4326",
    )
    stores = pd.DataFrame(
        {
            "CHAVE_LOJA": ["L1"],
            "COD_IBGE": ["3500001"],
            "LATITUDE": [-23.0],
            "LONGITUDE": [-46.0],
        }
    )

    assigned = assigner(stores, demand, {"3500001", "3500002"}, district_geo)

    assert assigned.iloc[0]["DEMAND_ID"] == "DIST-350000101"
    assert assigned.iloc[0]["METODO_ASSOCIACAO_UNIDADE"] == (
        "PONTO_DENTRO_DISTRITO_RECONCILIADO"
    )


def test_tiny_scip_model_is_feasible_contiguous_and_lexicographic() -> None:
    pytest.importorskip("pyscipopt")
    cfg = replace(
        v4.V4Config(),
        manager_count=2,
        expected_regional_points=1,
        time_limit_seconds=30,
        mip_gap=0.0,
        regional_anchor_radius_km=100.0,
    )
    demand = pd.DataFrame(
        {
            "DEMAND_ID": ["U0", "U1", "U2", "U3"],
            "DEMAND_IDX": [0, 1, 2, 3],
            "ATENDIMENTO_OBRIGATORIO": [True, True, True, True],
            "POPULACAO_UNIDADE": [100.0, 100.0, 100.0, 100.0],
            "QTD_LOJAS": [10, 10, 10, 10],
            "UF": ["SP", "SP", "SP", "SP"],
            "LATITUDE": [-23.0, -23.1, -23.2, -23.3],
            "LONGITUDE": [-46.0, -46.0, -46.0, -46.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "CANDIDATE_IDX": [0, 1],
            "CANDIDATE_ID": ["P0", "P1"],
            "DEMAND_IDX_ORIGEM_POLO": [0, 3],
            "UF": ["SP", "SP"],
            "DESC_GERENCIA_AREA_PROPOSTA": ["SAO PAULO", "SAO PAULO"],
            "LATITUDE": [-23.0, -23.3],
            "LONGITUDE": [-46.0, -46.0],
        }
    )
    regional = pd.DataFrame(
        {
            "COD_GER_REG": ["R1"],
            "DESC_GERENCIA_AREA_GR": ["SAO PAULO"],
            "LATITUDE": [-23.0],
            "LONGITUDE": [-46.0],
        }
    )
    distance = np.array(
        [
            [0.0, 30.0],
            [10.0, 20.0],
            [20.0, 10.0],
            [30.0, 0.0],
        ],
        dtype=np.float32,
    )
    neighbors = {0: {1}, 1: {0, 2}, 2: {1, 3}, 3: {2}}
    bundle = v4.build_optimization_model(
        demand, candidates, regional, distance, neighbors, cfg
    )
    assert v4.add_contiguous_warm_start(
        bundle, demand, candidates, regional, neighbors, cfg
    )
    state, reports = v4.solve_lexicographic(bundle, candidates, neighbors, cfg)
    assert state.selected == [0, 1]
    assert state.assignment.tolist() == [0, 0, 1, 1]
    assert reports["OTIMO_COMPROVADO"].all()
    for candidate in state.selected:
        nodes = set(np.flatnonzero(state.assignment == candidate).tolist())
        assert len(v4.v3.components(nodes, neighbors)) == 1
