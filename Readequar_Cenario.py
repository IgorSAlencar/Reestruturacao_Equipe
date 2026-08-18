#!/usr/bin/env python3
"""Readequa polos e carteiras de um ScenarioData exportado pelo Builder.

O algoritmo e deliberadamente heuristico e reproduzivel. Ele preserva a
quantidade nacional de polos, cumpre metas regionais informadas em JSON,
consolida municipios duplicados e produz tres solucoes para comparar o custo
de estabilidade, equilibrio populacional e distancia.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


EARTH_RADIUS_KM = 6_371.0088


@dataclass(frozen=True)
class RegionConfig:
    name: str
    areas: tuple[str, ...]
    ufs: tuple[str, ...]
    target_poles: int


@dataclass(frozen=True)
class RebalanceConfig:
    regions: tuple[RegionConfig, ...]
    balance_tolerance_pct: float = 20.0
    max_p90_increase_pct: float = 10.0
    max_assignment_passes: int = 10
    seed: int = 20260813


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    stability_weight: float
    distance_weight: float
    balance_weight: float
    cross_uf_weight: float = 0.10
    relocation_rounds: int = 0


PROFILES = (
    Profile("conservador", "Conservador", 0.50, 0.30, 0.20, relocation_rounds=0),
    Profile("equilibrado", "Equilibrado", 0.15, 0.45, 0.40, relocation_rounds=1),
    Profile("geografico", "Geografico", 0.05, 0.75, 0.20, relocation_rounds=2),
)


@dataclass
class CanonicalUnit:
    key: str
    unit_type: str
    municipality_code: str
    district_code: str
    name: str
    uf: str
    population: float
    stores: float
    latitude: float
    longitude: float
    original_pole_id: str
    template: dict[str, Any]
    source_ids: list[str] = field(default_factory=list)


@dataclass
class Facility:
    pole_id: str
    region: str
    area: str
    seat_key: str
    latitude: float
    longitude: float
    uf: str
    template: dict[str, Any]
    original_region: str
    original_latitude: float
    original_longitude: float


@dataclass
class ScenarioResult:
    profile: Profile
    payload: dict[str, Any]
    assignments: dict[str, str]
    facilities: dict[str, Facility]
    region_metrics: list[dict[str, Any]]
    portfolio_metrics: list[dict[str, Any]]
    movements: list[dict[str, Any]]
    pole_movements: list[dict[str, Any]]
    violations: list[dict[str, Any]]
    consolidation: list[dict[str, Any]]
    status: str


def _normalise_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\.0$", "", str(value).strip())


def _safe_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} ausente ou invalido: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} precisa ser finito: {value!r}")
    return result


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def weighted_percentile(values: Sequence[float], weights: Sequence[float], percentile: float) -> float:
    pairs = sorted(
        (float(value), max(float(weight), 0.0))
        for value, weight in zip(values, weights, strict=True)
        if math.isfinite(float(value))
    )
    if not pairs:
        return math.nan
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        position = max(0, min(len(pairs) - 1, math.ceil(percentile * len(pairs)) - 1))
        return pairs[position][0]
    threshold = percentile * total
    accumulated = 0.0
    for value, weight in pairs:
        accumulated += weight
        if accumulated >= threshold:
            return value
    return pairs[-1][0]


def load_config(path: str | Path) -> RebalanceConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Configuracao JSON invalida em {source}: {exc}") from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("regions"), list):
        raise ValueError("A configuracao precisa conter uma lista 'regions'.")
    regions: list[RegionConfig] = []
    for index, item in enumerate(raw["regions"]):
        if not isinstance(item, Mapping):
            raise ValueError(f"regions[{index}] precisa ser um objeto.")
        name = str(item.get("name") or "").strip()
        areas = tuple(str(value).strip() for value in item.get("areas", []) if str(value).strip())
        ufs = tuple(str(value).strip().upper() for value in item.get("ufs", []) if str(value).strip())
        target = item.get("targetPoles")
        if not name or not areas or not ufs or not isinstance(target, int) or target <= 0:
            raise ValueError(
                f"regions[{index}] exige name, areas, ufs e targetPoles inteiro positivo."
            )
        regions.append(RegionConfig(name, areas, ufs, target))
    config = RebalanceConfig(
        regions=tuple(regions),
        balance_tolerance_pct=float(raw.get("balanceTolerancePct", 20)),
        max_p90_increase_pct=float(raw.get("maxP90IncreasePct", 10)),
        max_assignment_passes=int(raw.get("maxAssignmentPasses", 10)),
        seed=int(raw.get("seed", 20260813)),
    )
    validate_config(config)
    return config


def validate_config(config: RebalanceConfig) -> None:
    if not 0 <= config.balance_tolerance_pct < 100:
        raise ValueError("balanceTolerancePct deve estar no intervalo [0, 100).")
    if config.max_p90_increase_pct < 0:
        raise ValueError("maxP90IncreasePct nao pode ser negativo.")
    if config.max_assignment_passes <= 0:
        raise ValueError("maxAssignmentPasses precisa ser positivo.")
    names: set[str] = set()
    areas: dict[str, str] = {}
    ufs: dict[str, str] = {}
    for region in config.regions:
        if region.name in names:
            raise ValueError(f"Regiao duplicada: {region.name}")
        names.add(region.name)
        for area in region.areas:
            if area in areas:
                raise ValueError(f"Area {area!r} pertence a duas regioes.")
            areas[area] = region.name
        for uf in region.ufs:
            if uf in ufs:
                raise ValueError(f"UF {uf!r} pertence a duas regioes.")
            ufs[uf] = region.name


def load_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cenario JSON invalido em {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("O JSON do cenario precisa conter um objeto na raiz.")
    return payload


def scenario_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("data")
    if isinstance(nested, Mapping) and ("poles" in nested or "units" in nested):
        return nested
    return payload


def _maps(config: RebalanceConfig) -> tuple[dict[str, RegionConfig], dict[str, str], dict[str, str]]:
    regions = {region.name: region for region in config.regions}
    area_region = {area: region.name for region in config.regions for area in region.areas}
    uf_region = {uf: region.name for region in config.regions for uf in region.ufs}
    return regions, area_region, uf_region


def _coordinate(item: Mapping[str, Any], short: str, long: str, label: str) -> float:
    value = item.get(short)
    if value is None or value == "":
        value = item.get(long)
    return _safe_float(value, label)


def prepare_input(
    payload: Mapping[str, Any], config: RebalanceConfig
) -> tuple[dict[str, dict[str, Any]], dict[str, CanonicalUnit], list[dict[str, Any]]]:
    data = scenario_data(payload)
    raw_poles = data.get("poles")
    raw_units = data.get("units")
    if not isinstance(raw_poles, list) or not isinstance(raw_units, list):
        raise ValueError("O cenario precisa conter listas 'poles' e 'units'.")
    regions, area_region, uf_region = _maps(config)
    poles: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_poles):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Polo na posicao {index} nao e um objeto.")
        pole = copy.deepcopy(dict(raw))
        pole_id = _normalise_id(pole.get("id"))
        if not pole_id or pole_id in poles:
            raise ValueError(f"ID de polo ausente ou duplicado na posicao {index}: {pole_id!r}")
        area = str(pole.get("area") or "").strip()
        if area not in area_region:
            raise ValueError(f"Polo {pole_id}: area {area!r} nao aparece na configuracao.")
        latitude = _coordinate(pole, "lat", "latitude", f"latitude do polo {pole_id}")
        longitude = _coordinate(pole, "lon", "longitude", f"longitude do polo {pole_id}")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"Polo {pole_id}: coordenadas fora do intervalo valido.")
        pole.update(
            {
                "id": pole_id,
                "_area": area,
                "_region": area_region[area],
                "_latitude": latitude,
                "_longitude": longitude,
            }
        )
        poles[pole_id] = pole
    if sum(region.target_poles for region in regions.values()) != len(poles):
        raise ValueError(
            "A soma de targetPoles precisa ser igual ao total de polos do cenario "
            f"({len(poles)})."
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, raw in enumerate(raw_units):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Unidade na posicao {index} nao e um objeto.")
        item = copy.deepcopy(dict(raw))
        unit_type = str(item.get("type") or "MUNICIPIO").strip().upper()
        municipality = _normalise_id(item.get("municipalityCode"))
        district = _normalise_id(item.get("districtCode"))
        is_district = unit_type in {"DISTRICT", "DISTRITO"}
        if is_district and not district:
            raise ValueError(f"Unidade distrital na posicao {index} sem districtCode.")
        if not municipality:
            raise ValueError(f"Unidade na posicao {index} sem municipalityCode.")
        key = f"DIST:{district}" if is_district else f"MUN:{municipality}"
        item["_source_index"] = index
        grouped[key].append(item)

    units: dict[str, CanonicalUnit] = {}
    consolidation: list[dict[str, Any]] = []
    for key in sorted(grouped):
        records = grouped[key]
        first = records[0]
        unit_type = "DISTRITO" if key.startswith("DIST:") else "MUNICIPIO"
        municipality = _normalise_id(first.get("municipalityCode"))
        district = _normalise_id(first.get("districtCode")) if unit_type == "DISTRITO" else ""
        uf_values = {str(item.get("uf") or "").strip().upper() for item in records}
        if len(uf_values) != 1 or "" in uf_values:
            raise ValueError(f"{key}: UF ausente ou divergente entre duplicatas.")
        uf = next(iter(uf_values))
        if uf not in uf_region:
            raise ValueError(f"{key}: UF {uf!r} nao aparece na configuracao.")
        coordinates = [
            (
                _coordinate(item, "lat", "latitude", f"latitude de {key}"),
                _coordinate(item, "lon", "longitude", f"longitude de {key}"),
            )
            for item in records
        ]
        latitude, longitude = coordinates[0]
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"{key}: coordenadas fora do intervalo valido.")
        populations = [max(_safe_float(item.get("population", 0), f"populacao de {key}"), 0) for item in records]
        stores = [max(_safe_float(item.get("stores", 0), f"lojas de {key}"), 0) for item in records]
        by_pole: dict[str, float] = defaultdict(float)
        for item, store_count in zip(records, stores, strict=True):
            pole_id = _normalise_id(item.get("poleId"))
            if pole_id not in poles:
                raise ValueError(f"{key}: poleId {pole_id!r} nao existe na lista de polos.")
            by_pole[pole_id] += store_count
        highest = max(by_pole.values()) if by_pole else 0
        tied = [pole_id for pole_id, value in by_pole.items() if value == highest]
        original_pole = min(
            tied,
            key=lambda pole_id: (
                haversine_km(latitude, longitude, poles[pole_id]["_latitude"], poles[pole_id]["_longitude"]),
                pole_id,
            ),
        )
        name = str(
            first.get("districtName")
            or first.get("municipalityName")
            or first.get("name")
            or key
        ).strip()
        source_ids = [_normalise_id(item.get("id")) or f"linha-{item['_source_index'] + 1}" for item in records]
        units[key] = CanonicalUnit(
            key=key,
            unit_type=unit_type,
            municipality_code=municipality,
            district_code=district,
            name=name,
            uf=uf,
            population=max(populations),
            stores=sum(stores),
            latitude=latitude,
            longitude=longitude,
            original_pole_id=original_pole,
            template={key: value for key, value in first.items() if not key.startswith("_")},
            source_ids=source_ids,
        )
        if len(records) > 1:
            consolidation.append(
                {
                    "unidade_chave": key,
                    "nome": name,
                    "uf": uf,
                    "registros_origem": len(records),
                    "polos_origem": len(by_pole),
                    "populacao_considerada": max(populations),
                    "lojas_somadas": sum(stores),
                    "polo_origem_dominante": original_pole,
                    "ids_origem": ", ".join(source_ids),
                }
            )
    for region in config.regions:
        region_units = sum(1 for unit in units.values() if uf_region[unit.uf] == region.name)
        if region_units < region.target_poles:
            raise ValueError(
                f"{region.name}: targetPoles={region.target_poles}, mas existem somente "
                f"{region_units} unidades candidatas a sede."
            )
    return poles, units, consolidation


def _nearest_unit_key(
    pole: Mapping[str, Any], units: Mapping[str, CanonicalUnit], region: str, uf_region: Mapping[str, str]
) -> str:
    candidates = [unit for unit in units.values() if uf_region[unit.uf] == region]
    return min(
        candidates,
        key=lambda unit: (
            haversine_km(pole["_latitude"], pole["_longitude"], unit.latitude, unit.longitude),
            unit.key,
        ),
    ).key


def _baseline_metrics(
    units: Mapping[str, CanonicalUnit], poles: Mapping[str, Mapping[str, Any]], config: RebalanceConfig
) -> dict[str, dict[str, float]]:
    _, _, uf_region = _maps(config)
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for unit in units.values():
        pole = poles[unit.original_pole_id]
        distance = haversine_km(unit.latitude, unit.longitude, pole["_latitude"], pole["_longitude"])
        grouped[uf_region[unit.uf]].append((distance, unit.population))
    result: dict[str, dict[str, float]] = {}
    for region in config.regions:
        rows = grouped[region.name]
        total_population = sum(weight for _, weight in rows)
        result[region.name] = {
            "p90": weighted_percentile([value for value, _ in rows], [weight for _, weight in rows], 0.90),
            "mean": (
                sum(value * weight for value, weight in rows) / total_population
                if total_population > 0
                else sum(value for value, _ in rows) / max(len(rows), 1)
            ),
        }
    return result


def _choose_new_seats(
    count: int,
    region: str,
    facilities: Sequence[Facility],
    units: Mapping[str, CanonicalUnit],
    uf_region: Mapping[str, str],
    occupied: set[str],
) -> list[str]:
    chosen: list[str] = []
    candidates = [unit for unit in units.values() if uf_region[unit.uf] == region and unit.key not in occupied]
    for _ in range(count):
        if not candidates:
            raise ValueError(f"{region}: nao ha sedes distintas suficientes para novos polos.")
        anchors = [(facility.latitude, facility.longitude) for facility in facilities]
        anchors.extend((units[key].latitude, units[key].longitude) for key in chosen)
        if anchors:
            best = max(
                candidates,
                key=lambda unit: (
                    min(haversine_km(unit.latitude, unit.longitude, lat, lon) for lat, lon in anchors)
                    * max(unit.population, 1.0),
                    unit.population,
                    unit.key,
                ),
            )
        else:
            best = max(candidates, key=lambda unit: (unit.population, unit.key))
        chosen.append(best.key)
        candidates.remove(best)
    return chosen


def initialise_facilities(
    poles: Mapping[str, Mapping[str, Any]],
    units: Mapping[str, CanonicalUnit],
    config: RebalanceConfig,
) -> dict[str, Facility]:
    regions, _, uf_region = _maps(config)
    current_load: dict[str, float] = defaultdict(float)
    for unit in units.values():
        current_load[unit.original_pole_id] += unit.population
    by_region: dict[str, list[str]] = defaultdict(list)
    for pole_id, pole in poles.items():
        by_region[pole["_region"]].append(pole_id)
    donor_ids: list[str] = []
    retained_ids: dict[str, list[str]] = {}
    for region in config.regions:
        ordered = sorted(by_region[region.name], key=lambda pole_id: (-current_load[pole_id], pole_id))
        retained_ids[region.name] = ordered[: region.target_poles]
        donor_ids.extend(ordered[region.target_poles :])
    donor_ids.sort(key=lambda pole_id: (current_load[pole_id], pole_id))

    facilities: dict[str, Facility] = {}
    occupied: set[str] = set()
    for region in config.regions:
        for pole_id in retained_ids[region.name]:
            pole = poles[pole_id]
            seat_key = _nearest_unit_key(pole, units, region.name, uf_region)
            if seat_key in occupied:
                alternatives = sorted(
                    (
                        unit for unit in units.values()
                        if uf_region[unit.uf] == region.name and unit.key not in occupied
                    ),
                    key=lambda unit: (
                        haversine_km(pole["_latitude"], pole["_longitude"], unit.latitude, unit.longitude),
                        unit.key,
                    ),
                )
                seat_key = alternatives[0].key
            occupied.add(seat_key)
            seat = units[seat_key]
            facilities[pole_id] = Facility(
                pole_id=pole_id,
                region=region.name,
                area=pole["_area"],
                seat_key=seat_key,
                latitude=float(pole["_latitude"]),
                longitude=float(pole["_longitude"]),
                uf=seat.uf,
                template=copy.deepcopy({key: value for key, value in pole.items() if not key.startswith("_")}),
                original_region=pole["_region"],
                original_latitude=float(pole["_latitude"]),
                original_longitude=float(pole["_longitude"]),
            )

    donor_cursor = 0
    for region in config.regions:
        deficit = region.target_poles - len(retained_ids[region.name])
        existing = [facility for facility in facilities.values() if facility.region == region.name]
        seats = _choose_new_seats(deficit, region.name, existing, units, uf_region, occupied)
        for seat_key in seats:
            pole_id = donor_ids[donor_cursor]
            donor_cursor += 1
            pole = poles[pole_id]
            seat = units[seat_key]
            occupied.add(seat_key)
            facilities[pole_id] = Facility(
                pole_id=pole_id,
                region=region.name,
                area=regions[region.name].areas[0],
                seat_key=seat_key,
                latitude=seat.latitude,
                longitude=seat.longitude,
                uf=seat.uf,
                template=copy.deepcopy({key: value for key, value in pole.items() if not key.startswith("_")}),
                original_region=pole["_region"],
                original_latitude=float(pole["_latitude"]),
                original_longitude=float(pole["_longitude"]),
            )
    return facilities


def _region_scale(
    region: str, baseline: Mapping[str, Mapping[str, float]], units: Mapping[str, CanonicalUnit], uf_region: Mapping[str, str]
) -> float:
    scale = float(baseline[region]["p90"])
    if math.isfinite(scale) and scale > 0:
        return scale
    coordinates = [unit for unit in units.values() if uf_region[unit.uf] == region]
    if len(coordinates) <= 1:
        return 1.0
    distances = [
        haversine_km(coordinates[0].latitude, coordinates[0].longitude, unit.latitude, unit.longitude)
        for unit in coordinates[1:]
    ]
    return max(sorted(distances)[len(distances) // 2], 1.0)


def assign_units(
    units: Mapping[str, CanonicalUnit],
    facilities: Mapping[str, Facility],
    config: RebalanceConfig,
    profile: Profile,
    baseline: Mapping[str, Mapping[str, float]],
) -> dict[str, str]:
    _, _, uf_region = _maps(config)
    by_region_facilities: dict[str, list[Facility]] = defaultdict(list)
    for facility in facilities.values():
        by_region_facilities[facility.region].append(facility)
    assignments: dict[str, str] = {}
    loads: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    seats = {facility.seat_key: facility.pole_id for facility in facilities.values()}
    for key, pole_id in seats.items():
        assignments[key] = pole_id
        loads[pole_id] += units[key].population
        counts[pole_id] += 1
    region_population = {
        region.name: sum(unit.population for unit in units.values() if uf_region[unit.uf] == region.name)
        for region in config.regions
    }
    target_load = {region.name: region_population[region.name] / region.target_poles for region in config.regions}
    ordered_units = sorted(
        (unit for unit in units.values() if unit.key not in seats),
        key=lambda unit: (-unit.population, unit.key),
    )

    def placement_score(unit: CanonicalUnit, facility: Facility) -> float:
        scale = _region_scale(facility.region, baseline, units, uf_region)
        distance = haversine_km(unit.latitude, unit.longitude, facility.latitude, facility.longitude) / scale
        target = max(target_load[facility.region], 1.0)
        projected = loads[facility.pole_id] + unit.population
        balance = ((projected - target) / target) ** 2
        stability = 0.0 if unit.original_pole_id == facility.pole_id else 1.0
        cross_uf = 0.0 if unit.uf == facility.uf else 1.0
        return (
            profile.distance_weight * distance
            + profile.balance_weight * balance
            + profile.stability_weight * stability
            + profile.cross_uf_weight * cross_uf
        )

    for unit in ordered_units:
        region = uf_region[unit.uf]
        facility = min(
            by_region_facilities[region],
            key=lambda candidate: (placement_score(unit, candidate), candidate.pole_id),
        )
        assignments[unit.key] = facility.pole_id
        loads[facility.pole_id] += unit.population
        counts[facility.pole_id] += 1

    for _ in range(config.max_assignment_passes):
        changed = False
        for unit in sorted(units.values(), key=lambda item: (item.population, item.key)):
            if unit.key in seats:
                continue
            source_id = assignments[unit.key]
            if counts[source_id] <= 1:
                continue
            source = facilities[source_id]
            target_value = max(target_load[source.region], 1.0)
            scale = _region_scale(source.region, baseline, units, uf_region)

            def move_delta(destination: Facility) -> float:
                if destination.pole_id == source_id:
                    return 0.0
                old_distance = haversine_km(unit.latitude, unit.longitude, source.latitude, source.longitude) / scale
                new_distance = haversine_km(unit.latitude, unit.longitude, destination.latitude, destination.longitude) / scale
                old_balance = (
                    ((loads[source_id] - target_value) / target_value) ** 2
                    + ((loads[destination.pole_id] - target_value) / target_value) ** 2
                )
                new_balance = (
                    ((loads[source_id] - unit.population - target_value) / target_value) ** 2
                    + ((loads[destination.pole_id] + unit.population - target_value) / target_value) ** 2
                )
                old_stability = 0.0 if unit.original_pole_id == source_id else 1.0
                new_stability = 0.0 if unit.original_pole_id == destination.pole_id else 1.0
                old_cross = 0.0 if unit.uf == source.uf else 1.0
                new_cross = 0.0 if unit.uf == destination.uf else 1.0
                return (
                    profile.distance_weight * (new_distance - old_distance)
                    + profile.balance_weight * (new_balance - old_balance)
                    + profile.stability_weight * (new_stability - old_stability)
                    + profile.cross_uf_weight * (new_cross - old_cross)
                )

            candidates = by_region_facilities[source.region]
            destination = min(candidates, key=lambda item: (move_delta(item), item.pole_id))
            delta = move_delta(destination)
            if destination.pole_id != source_id and delta < -1e-9:
                assignments[unit.key] = destination.pole_id
                loads[source_id] -= unit.population
                loads[destination.pole_id] += unit.population
                counts[source_id] -= 1
                counts[destination.pole_id] += 1
                changed = True
        if not changed:
            break
    return assignments


def relocate_facilities(
    facilities: dict[str, Facility],
    assignments: Mapping[str, str],
    units: Mapping[str, CanonicalUnit],
) -> bool:
    changed = False
    occupied = {facility.seat_key for facility in facilities.values()}
    for pole_id in sorted(facilities):
        facility = facilities[pole_id]
        members = [unit for unit in units.values() if assignments[unit.key] == pole_id]
        if not members:
            continue
        centroid_lat = sum(unit.latitude * max(unit.population, 1) for unit in members) / sum(max(unit.population, 1) for unit in members)
        centroid_lon = sum(unit.longitude * max(unit.population, 1) for unit in members) / sum(max(unit.population, 1) for unit in members)
        candidates = sorted(
            members,
            key=lambda unit: (
                haversine_km(unit.latitude, unit.longitude, centroid_lat, centroid_lon),
                -unit.population,
                unit.key,
            ),
        )[:20]
        candidates = [unit for unit in candidates if unit.key == facility.seat_key or unit.key not in occupied]
        if not candidates:
            continue

        def cluster_cost(candidate: CanonicalUnit) -> float:
            return sum(
                max(member.population, 1.0)
                * haversine_km(member.latitude, member.longitude, candidate.latitude, candidate.longitude)
                for member in members
            )

        current_seat = units[facility.seat_key]
        best = min(candidates, key=lambda unit: (cluster_cost(unit), unit.key))
        if best.key != current_seat.key and cluster_cost(best) + 1e-6 < cluster_cost(current_seat):
            occupied.remove(facility.seat_key)
            occupied.add(best.key)
            facility.seat_key = best.key
            facility.latitude = best.latitude
            facility.longitude = best.longitude
            facility.uf = best.uf
            changed = True
    return changed


def _metrics(
    units: Mapping[str, CanonicalUnit],
    facilities: Mapping[str, Facility],
    assignments: Mapping[str, str],
    config: RebalanceConfig,
    baseline: Mapping[str, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    _, _, uf_region = _maps(config)
    portfolio_rows: list[dict[str, Any]] = []
    for pole_id in sorted(facilities):
        facility = facilities[pole_id]
        members = [unit for unit in units.values() if assignments[unit.key] == pole_id]
        distances = [haversine_km(unit.latitude, unit.longitude, facility.latitude, facility.longitude) for unit in members]
        population = [unit.population for unit in members]
        total_population = sum(population)
        portfolio_rows.append(
            {
                "polo_id": pole_id,
                "regiao": facility.region,
                "area": facility.area,
                "uf_sede": facility.uf,
                "unidades": len(members),
                "populacao": total_population,
                "lojas": sum(unit.stores for unit in members),
                "distancia_media_ponderada_km": (
                    sum(value * weight for value, weight in zip(distances, population, strict=True)) / total_population
                    if total_population > 0
                    else sum(distances) / max(len(distances), 1)
                ),
                "distancia_p90_ponderada_km": weighted_percentile(distances, population, 0.90),
                "distancia_max_km": max(distances, default=0.0),
            }
        )
    region_rows: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    tolerance = config.balance_tolerance_pct / 100
    p90_tolerance = config.max_p90_increase_pct / 100
    for region in config.regions:
        region_units = [unit for unit in units.values() if uf_region[unit.uf] == region.name]
        distances = [
            haversine_km(
                unit.latitude,
                unit.longitude,
                facilities[assignments[unit.key]].latitude,
                facilities[assignments[unit.key]].longitude,
            )
            for unit in region_units
        ]
        populations = [unit.population for unit in region_units]
        total_population = sum(populations)
        mean_load = total_population / region.target_poles
        lower, upper = mean_load * (1 - tolerance), mean_load * (1 + tolerance)
        portfolios = [row for row in portfolio_rows if row["regiao"] == region.name]
        outside = [row for row in portfolios if not lower <= row["populacao"] <= upper]
        p90 = weighted_percentile(distances, populations, 0.90)
        baseline_p90 = baseline[region.name]["p90"]
        limit = baseline_p90 * (1 + p90_tolerance)
        p90_ok = not math.isfinite(baseline_p90) or p90 <= limit + 1e-9
        region_rows.append(
            {
                "regiao": region.name,
                "meta_polos": region.target_poles,
                "polos_resultado": len(portfolios),
                "populacao_total": total_population,
                "populacao_media_carteira": mean_load,
                "limite_inferior_populacao": lower,
                "limite_superior_populacao": upper,
                "carteiras_fora_faixa": len(outside),
                "p90_base_km": baseline_p90,
                "p90_resultado_km": p90,
                "p90_limite_km": limit,
                "p90_conforme": p90_ok,
            }
        )
        for row in outside:
            violations.append(
                {
                    "tipo": "FAIXA_POPULACIONAL",
                    "regiao": region.name,
                    "polo_id": row["polo_id"],
                    "valor": row["populacao"],
                    "limite": f"{lower:.2f} a {upper:.2f}",
                    "mensagem": "Carteira fora da faixa preferida de equilibrio.",
                }
            )
        if not p90_ok:
            violations.append(
                {
                    "tipo": "P90_DISTANCIA",
                    "regiao": region.name,
                    "polo_id": "",
                    "valor": p90,
                    "limite": limit,
                    "mensagem": "Meta regional tornou o P90 superior ao limite preferido.",
                }
            )
    return region_rows, portfolio_rows, violations


def _build_output(
    original: Mapping[str, Any],
    units: Mapping[str, CanonicalUnit],
    facilities: Mapping[str, Facility],
    assignments: Mapping[str, str],
    profile: Profile,
    region_metrics: Sequence[Mapping[str, Any]],
    violations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(original))
    data = payload["data"] if isinstance(payload.get("data"), dict) and ("poles" in payload["data"] or "units" in payload["data"]) else payload
    new_poles: list[dict[str, Any]] = []
    for pole_id in sorted(facilities):
        facility = facilities[pole_id]
        pole = copy.deepcopy(facility.template)
        pole.update(
            {
                "id": pole_id,
                "area": facility.area,
                "latitude": facility.latitude,
                "longitude": facility.longitude,
                "uf": facility.uf,
                "municipalityCode": units[facility.seat_key].municipality_code,
                "municipalityName": units[facility.seat_key].name,
            }
        )
        pole.pop("lat", None)
        pole.pop("lon", None)
        new_poles.append(pole)
    new_units: list[dict[str, Any]] = []
    for key in sorted(units):
        unit = units[key]
        pole_id = assignments[key]
        facility = facilities[pole_id]
        row = copy.deepcopy(unit.template)
        row.update(
            {
                "type": unit.unit_type,
                "municipalityCode": unit.municipality_code,
                "poleId": pole_id,
                "population": unit.population,
                "stores": unit.stores,
                "latitude": unit.latitude,
                "longitude": unit.longitude,
                "distanceKm": haversine_km(unit.latitude, unit.longitude, facility.latitude, facility.longitude),
                "uf": unit.uf,
            }
        )
        if unit.district_code:
            row["districtCode"] = unit.district_code
        row.pop("lat", None)
        row.pop("lon", None)
        new_units.append(row)
    data["poles"] = new_poles
    data["units"] = new_units
    summary = data.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        data["summary"] = summary
    area_counts: dict[str, int] = defaultdict(int)
    for facility in facilities.values():
        area_counts[facility.area] += 1
    previous_warnings = summary.get("warnings")
    if not isinstance(previous_warnings, list):
        previous_warnings = []
    generated_warnings = list(dict.fromkeys(str(item["mensagem"]) for item in violations))
    summary["poleCount"] = len(new_poles)
    summary["totalPoles"] = len(new_poles)
    summary["totalUnits"] = len(new_units)
    summary["totalStores"] = sum(unit.stores for unit in units.values())
    summary["totalPopulation"] = sum(unit.population for unit in units.values())
    summary["areaCounts"] = dict(sorted(area_counts.items()))
    summary["warnings"] = list(dict.fromkeys([*map(str, previous_warnings), *generated_warnings]))
    original_name = str(summary.get("name") or data.get("name") or payload.get("name") or "Cenario")
    generated_name = f"{original_name} - {profile.label}"
    summary["name"] = generated_name
    if "name" in data:
        data["name"] = generated_name
    if data is not payload and "name" in payload:
        payload["name"] = generated_name
    data["readequation"] = {
        "profile": profile.key,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "CONFORME" if not violations else "COM_RESSALVAS",
        "regionMetrics": list(region_metrics),
    }
    return payload


def rebalance_profile(
    payload: Mapping[str, Any],
    config: RebalanceConfig,
    profile: Profile,
) -> ScenarioResult:
    poles, units, consolidation = prepare_input(payload, config)
    baseline = _baseline_metrics(units, poles, config)
    facilities = initialise_facilities(poles, units, config)
    assignments = assign_units(units, facilities, config, profile, baseline)
    for _ in range(profile.relocation_rounds):
        if not relocate_facilities(facilities, assignments, units):
            break
        assignments = assign_units(units, facilities, config, profile, baseline)
    region_metrics, portfolio_metrics, violations = _metrics(
        units, facilities, assignments, config, baseline
    )
    movements = []
    for unit in sorted(units.values(), key=lambda item: item.key):
        destination = assignments[unit.key]
        if destination != unit.original_pole_id:
            movements.append(
                {
                    "unidade_chave": unit.key,
                    "nome": unit.name,
                    "uf": unit.uf,
                    "populacao": unit.population,
                    "polo_origem": unit.original_pole_id,
                    "polo_destino": destination,
                    "distancia_origem_km": haversine_km(
                        unit.latitude, unit.longitude,
                        poles[unit.original_pole_id]["_latitude"], poles[unit.original_pole_id]["_longitude"],
                    ),
                    "distancia_destino_km": haversine_km(
                        unit.latitude, unit.longitude,
                        facilities[destination].latitude, facilities[destination].longitude,
                    ),
                }
            )
    pole_movements = []
    for pole_id in sorted(facilities):
        facility = facilities[pole_id]
        distance = haversine_km(
            facility.original_latitude,
            facility.original_longitude,
            facility.latitude,
            facility.longitude,
        )
        if facility.region != facility.original_region or distance > 1e-6:
            pole_movements.append(
                {
                    "polo_id": pole_id,
                    "regiao_origem": facility.original_region,
                    "regiao_destino": facility.region,
                    "area_destino": facility.area,
                    "sede_destino": units[facility.seat_key].name,
                    "uf_destino": facility.uf,
                    "deslocamento_sede_km": distance,
                }
            )
    output = _build_output(
        payload, units, facilities, assignments, profile, region_metrics, violations
    )
    return ScenarioResult(
        profile=profile,
        payload=output,
        assignments=assignments,
        facilities=facilities,
        region_metrics=region_metrics,
        portfolio_metrics=portfolio_metrics,
        movements=movements,
        pole_movements=pole_movements,
        violations=violations,
        consolidation=consolidation,
        status="CONFORME" if not violations else "COM_RESSALVAS",
    )


def rebalance_all(payload: Mapping[str, Any], config: RebalanceConfig) -> list[ScenarioResult]:
    return [rebalance_profile(payload, config, profile) for profile in PROFILES]


def export_results(
    results: Sequence[ScenarioResult], config: RebalanceConfig, output_dir: str | Path
) -> tuple[list[Path], Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_paths: list[Path] = []
    for result in results:
        path = destination / f"cenario_{result.profile.key}.json"
        path.write_text(json.dumps(result.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        json_paths.append(path)
    report_path = destination / "relatorio_readequacao.xlsx"
    summary_rows = [
        {
            "perfil": result.profile.key,
            "status": result.status,
            "polos_reposicionados": len(result.pole_movements),
            "unidades_movidas": len(result.movements),
            "violacoes": len(result.violations),
        }
        for result in results
    ]
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Resumo", index=False)
        for result in results:
            prefix = result.profile.key[:8]
            pd.DataFrame(result.region_metrics).to_excel(writer, sheet_name=f"{prefix}_regioes", index=False)
            pd.DataFrame(result.portfolio_metrics).to_excel(writer, sheet_name=f"{prefix}_carteiras", index=False)
            pd.DataFrame(result.movements).to_excel(writer, sheet_name=f"{prefix}_municipios", index=False)
            pd.DataFrame(result.pole_movements).to_excel(writer, sheet_name=f"{prefix}_polos", index=False)
            pd.DataFrame(result.violations).to_excel(writer, sheet_name=f"{prefix}_ressalvas", index=False)
        if results:
            pd.DataFrame(results[0].consolidation).to_excel(writer, sheet_name="Consolidacao", index=False)
    manifest_path = destination / "manifesto_execucao.json"
    manifest = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "algorithm": "heuristica_readequacao_v1",
        "config": {
            "regions": [
                {
                    "name": region.name,
                    "areas": list(region.areas),
                    "ufs": list(region.ufs),
                    "targetPoles": region.target_poles,
                }
                for region in config.regions
            ],
            "balanceTolerancePct": config.balance_tolerance_pct,
            "maxP90IncreasePct": config.max_p90_increase_pct,
            "maxAssignmentPasses": config.max_assignment_passes,
            "seed": config.seed,
        },
        "results": summary_rows,
        "files": [path.name for path in json_paths] + [report_path.name],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_paths, report_path, manifest_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Readequa polos e carteiras conforme metas regionais informadas em JSON."
    )
    parser.add_argument("--input", required=True, help="ScenarioData ou envelope JSON exportado pelo Builder.")
    parser.add_argument("--config", required=True, help="JSON com regioes, UFs e targetPoles.")
    parser.add_argument("--output-dir", default="resultados_readequacao", help="Pasta de saida.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = load_payload(args.input)
        config = load_config(args.config)
        results = rebalance_all(payload, config)
        paths, report, manifest = export_results(results, config, args.output_dir)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"Falha na readequacao: {exc}") from exc
    for path in paths:
        print(f"Cenario: {path.resolve()}")
    print(f"Relatorio: {report.resolve()}")
    print(f"Manifesto: {manifest.resolve()}")
    for result in results:
        print(
            f"{result.profile.label}: {result.status} | "
            f"{len(result.movements)} unidades movidas | {len(result.violations)} ressalvas"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
