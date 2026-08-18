import copy
import json

import pytest

import Readequar_Cenario as readequador


def pole(pole_id: str, area: str, lat: float, lon: float) -> dict:
    return {
        "id": pole_id,
        "name": f"Polo {pole_id}",
        "area": area,
        "latitude": lat,
        "longitude": lon,
        "source": "draft",
    }


def unit(
    code: str,
    uf: str,
    pole_id: str,
    lat: float,
    lon: float,
    population: float,
    stores: float = 1,
    *,
    suffix: str = "",
) -> dict:
    return {
        "id": f"MUN-{code}-{pole_id}{suffix}",
        "type": "MUNICIPIO",
        "municipalityCode": code,
        "municipalityName": f"Municipio {code}",
        "uf": uf,
        "poleId": pole_id,
        "population": population,
        "stores": stores,
        "latitude": lat,
        "longitude": lon,
        "distanceKm": 999,
    }


def config(target_a: int = 1, target_b: int = 3) -> readequador.RebalanceConfig:
    return readequador.RebalanceConfig(
        regions=(
            readequador.RegionConfig("REGIAO A", ("AREA A",), ("AA",), target_a),
            readequador.RegionConfig("REGIAO B", ("AREA B",), ("BB",), target_b),
        ),
        balance_tolerance_pct=20,
        max_p90_increase_pct=10,
        max_assignment_passes=5,
    )


def scenario() -> dict:
    return {
        "id": "draft-1",
        "name": "Teste",
        "data": {
            "summary": {"name": "Teste", "poleCount": 4, "warnings": []},
            "poles": [
                pole("P1", "AREA A", 0, 0),
                pole("P2", "AREA A", 0, 2),
                pole("P3", "AREA A", 0, 4),
                pole("P4", "AREA B", 10, 0),
            ],
            "units": [
                unit("100", "AA", "P1", 0, 0, 100),
                unit("101", "AA", "P1", 0, 0.5, 120),
                unit("102", "AA", "P2", 0, 2, 80),
                unit("103", "AA", "P2", 0, 2.5, 110),
                unit("104", "AA", "P3", 0, 4, 90),
                unit("105", "AA", "P3", 0, 4.5, 100),
                unit("200", "BB", "P4", 10, 0, 100),
                unit("201", "BB", "P4", 10, 1, 100),
                unit("202", "BB", "P4", 10, 2, 100),
                unit("203", "BB", "P4", 10, 3, 100),
            ],
            "territories": {"type": "FeatureCollection", "features": []},
        },
    }


def test_rebalance_meets_regional_targets_and_preserves_totals() -> None:
    original = scenario()
    result = readequador.rebalance_profile(
        original, config(), readequador.PROFILES[1]
    )
    output = result.payload["data"]
    assert len(output["poles"]) == 4
    assert len(output["units"]) == 10
    assert sum(item["population"] for item in output["units"]) == 1_000
    assert output["summary"]["areaCounts"] == {"AREA A": 1, "AREA B": 3}
    area_by_pole = {item["id"]: item["area"] for item in output["poles"]}
    uf_by_area = {"AREA A": "AA", "AREA B": "BB"}
    assert all(uf_by_area[area_by_pole[item["poleId"]]] == item["uf"] for item in output["units"])
    assert all(item["distanceKm"] != 999 for item in output["units"])
    assert original == scenario(), "A entrada nao deve ser alterada."


def test_each_pole_has_a_portfolio_and_its_seat() -> None:
    result = readequador.rebalance_profile(
        scenario(), config(), readequador.PROFILES[2]
    )
    counts = {pole_id: 0 for pole_id in result.facilities}
    for pole_id in result.assignments.values():
        counts[pole_id] += 1
    assert all(value > 0 for value in counts.values())
    assert all(
        result.assignments[facility.seat_key] == facility.pole_id
        for facility in result.facilities.values()
    )


def test_duplicate_municipality_uses_max_population_and_sums_stores() -> None:
    payload = {
        "poles": [
            pole("P1", "AREA A", 0, 0),
            pole("P2", "AREA A", 0, 2),
        ],
        "units": [
            unit("100", "AA", "P1", 0, 1, 100, stores=2, suffix="-1"),
            unit("100", "AA", "P2", 0, 1, 90, stores=5, suffix="-2"),
            unit("101", "AA", "P1", 0, 0, 50),
        ],
    }
    cfg = readequador.RebalanceConfig(
        regions=(readequador.RegionConfig("REGIAO A", ("AREA A",), ("AA",), 2),)
    )
    _, units, audit = readequador.prepare_input(payload, cfg)
    assert len(units) == 2
    assert units["MUN:100"].population == 100
    assert units["MUN:100"].stores == 7
    assert units["MUN:100"].original_pole_id == "P2"
    assert audit[0]["registros_origem"] == 2


def test_results_are_deterministic_except_for_generation_metadata() -> None:
    first = readequador.rebalance_profile(
        copy.deepcopy(scenario()), config(), readequador.PROFILES[0]
    )
    second = readequador.rebalance_profile(
        copy.deepcopy(scenario()), config(), readequador.PROFILES[0]
    )
    assert first.assignments == second.assignments
    assert {
        key: (value.region, value.seat_key)
        for key, value in first.facilities.items()
    } == {
        key: (value.region, value.seat_key)
        for key, value in second.facilities.items()
    }


def test_config_rejects_duplicate_uf_and_wrong_target_total() -> None:
    with pytest.raises(ValueError, match="duas regioes"):
        readequador.validate_config(
            readequador.RebalanceConfig(
                regions=(
                    readequador.RegionConfig("A", ("A",), ("SP",), 1),
                    readequador.RegionConfig("B", ("B",), ("SP",), 1),
                )
            )
        )
    bad = config(target_a=2, target_b=3)
    with pytest.raises(ValueError, match="soma de targetPoles"):
        readequador.prepare_input(scenario(), bad)


def test_weighted_percentile_prioritises_population() -> None:
    assert readequador.weighted_percentile([10, 100], [99, 1], 0.90) == 10


def test_exports_three_jsons_excel_and_manifest(tmp_path) -> None:
    results = readequador.rebalance_all(scenario(), config())
    json_paths, report_path, manifest_path = readequador.export_results(
        results, config(), tmp_path
    )
    assert {path.name for path in json_paths} == {
        "cenario_conservador.json",
        "cenario_equilibrado.json",
        "cenario_geografico.json",
    }
    assert report_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["results"]) == 3
    assert manifest["algorithm"] == "heuristica_readequacao_v1"
