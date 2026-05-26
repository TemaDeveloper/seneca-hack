"""
Road-grid mobility and EV charging simulation.

This is a research prototype for the next model generation. It maps the
hackathon FSA/zone/capacity data onto a cached OSM road graph when available,
with an offline FSA-adjacency graph kept only as a deterministic fallback.

Public API:
    MobilitySimulationEngine.run_agent_day(...) -> pd.DataFrame
    MobilitySimulationEngine.aggregate_charging_load(...) -> pd.DataFrame
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from charger_catalog import AFDC_CHARGERS_CSV, OSM_CHARGERS_CSV, ChargerCatalog, ChargerChoice
from spatial_assembler import FSA_GEOJSON, FSA_POPULATION_CSV, ZONE_CSV, load_enriched_geodataframe
from monte_carlo import CHARGER_POWER_KW, float_to_time
from road_network import FSA_ROUTE_CACHE, OSM_GRAPHML, OSM_GRAPH_PICKLE, OSM_ROUTE_CACHE, RoadNetwork


DayType = Literal["weekday", "weekend"]
TimingMode = Literal["arrive_by", "depart_at"]
DATA_DIR = Path(__file__).resolve().parent / "data"
TRAFFIC_FSA_COUNTS_CSV = DATA_DIR / "toronto_traffic_fsa_counts.csv"


@dataclass(frozen=True)
class PlannedStop:
    dest_type: str
    dest_idx: int
    hour: float
    timing: TimingMode


@dataclass(frozen=True)
class MobilityConfig:
    ev_probability: float = 0.03
    battery_capacity_kwh: float = 70.0
    ev_efficiency_kwh_per_km: float = 0.18
    road_circuity: float = 1.25
    initial_soc_alpha: float = 6.0
    initial_soc_beta: float = 2.0
    target_soc: float = 0.82
    week_end_target_soc: float = 0.75
    reserve_soc: float = 0.15
    home_charger_probability: float = 0.70
    work_charger_probability: float = 0.35
    home_public_charger_access: float = 0.30
    work_public_charger_access: float = 0.25
    retail_public_charger_access: float = 0.45
    patch_softmax_temperature: float = 0.90
    worker_weekday_work_probability: float = 0.84
    student_weekday_school_probability: float = 0.86
    weekday_nonworker_outing_probability: float = 0.50
    after_work_stop_probability: float = 0.28
    worker_weekend_work_probability: float = 0.12
    weekend_outing_probability: float = 0.66
    weekend_second_stop_probability: float = 0.34
    baseline_peak_utilization: float = 0.82
    grid_ev_load_scale: float = 1.0
    vehicle_population_share: float = 0.46
    traffic_attraction_exponent: float = 0.25
    road_graph_source: str = "auto"
    force_osm_download: bool = False
    charger_source: str = "auto"
    force_charger_download: bool = False


@dataclass(frozen=True)
class _StaticMobilityContext:
    base_gdf: pd.DataFrame
    fsas: np.ndarray
    zone_types: np.ndarray
    traffic_am_attraction: np.ndarray
    traffic_pm_attraction: np.ndarray
    traffic_total_attraction: np.ndarray
    distance_km: np.ndarray
    road_network: RoadNetwork
    charger_catalog: ChargerCatalog
    route_km: np.ndarray
    freeflow_time_h: np.ndarray


_STATIC_CONTEXT_CACHE: dict[tuple[object, ...], _StaticMobilityContext] = {}


def clear_static_context_cache() -> None:
    """Clear process-local immutable GIS/charger context cache."""
    _STATIC_CONTEXT_CACHE.clear()


def _file_signature(paths: list[Path]) -> tuple[tuple[str, int | None], ...]:
    signature = []
    for path in paths:
        signature.append((str(path), path.stat().st_mtime_ns if path.exists() else None))
    return tuple(signature)


DEST_TYPES = ["work", "school", "retail", "leisure", "home", "transit_hub", "other"]

DEST_ATTRACTION_BY_ZONE = {
    "work": {"residential": 0.25, "leisure": 0.40, "office_park": 4.50, "retail_hub": 1.40, "transit_hub": 1.00},
    "school": {"residential": 1.20, "leisure": 0.70, "office_park": 2.00, "retail_hub": 0.30, "transit_hub": 0.20},
    "retail": {"residential": 0.70, "leisure": 0.50, "office_park": 0.80, "retail_hub": 4.50, "transit_hub": 1.20},
    "leisure": {"residential": 0.40, "leisure": 4.20, "office_park": 0.25, "retail_hub": 1.30, "transit_hub": 0.50},
    "home": {"residential": 4.50, "leisure": 0.20, "office_park": 0.20, "retail_hub": 0.35, "transit_hub": 0.10},
    "transit_hub": {"residential": 0.08, "leisure": 0.12, "office_park": 0.25, "retail_hub": 0.45, "transit_hub": 18.00},
    "other": {"residential": 1.00, "leisure": 1.00, "office_park": 1.00, "retail_hub": 1.00, "transit_hub": 1.00},
}

DEST_TAU_KM = {
    "work": 24.0,
    "school": 7.0,
    "retail": 10.0,
    "leisure": 16.0,
    "home": 20.0,
    "transit_hub": 28.0,
    "other": 13.0,
}

DEST_SOFT_MAX_KM = {
    "work": 80.0,
    "school": 35.0,
    "retail": 45.0,
    "leisure": 90.0,
    "home": 80.0,
    "transit_hub": 120.0,
    "other": 60.0,
}

HOME_ZONE_MULTIPLIER = {
    "residential": 1.00,
    "leisure": 0.15,
    "office_park": 0.28,
    "retail_hub": 0.45,
    "transit_hub": 0.10,
}

CHARGER_DISTANCE_KM_BY_ZONE = {
    "residential": 1.40,
    "leisure": 0.90,
    "office_park": 0.75,
    "retail_hub": 0.45,
    "transit_hub": 0.30,
}

AVAILABILITY_BY_ZONE = {
    "residential": 0.95,
    "leisure": 0.85,
    "office_park": 0.95,
    "retail_hub": 0.85,
    "transit_hub": 0.75,
}

PERSON_TYPE_PROBS = {
    "worker": 0.62,
    "student": 0.08,
    "retired": 0.12,
    "other": 0.18,
}

WEEKDAY_OUTING_TYPE_PROBS = {
    "retail": 0.59,
    "leisure": 0.30,
    "transit_hub": 0.06,
    "other": 0.05,
}

WEEKEND_OUTING_TYPE_PROBS = {
    "retail": 0.48,
    "leisure": 0.42,
    "transit_hub": 0.06,
    "other": 0.04,
}

NORMAL_CHARGE_BASE_BY_LOCATION = {
    "home": -1.2,
    "work": -1.4,
    "school": -1.6,
    "retail": -1.8,
    "leisure": -2.0,
    "transit_hub": -1.8,
    "other": -1.8,
}

PATCH_BASE_UTILITY = {
    "previous_home": 2.0,
    "previous_work": 0.8,
    "current_origin": 0.2,
    "near_route_public": 0.4,
    "forced_origin_public": -2.0,
}

DEST_CHARGER_TYPE = {
    "home": "residential",
    "work": "office_park",
    "school": "office_park",
    "retail": "retail_hub",
    "leisure": "leisure",
    "transit_hub": "transit_hub",
    "other": "retail_hub",
}

PEOPLE_COLUMNS = [
    "person_id", "person_type", "is_ev", "home_idx", "home_fsa", "home_zone_type",
    "initial_soc", "has_home_charger", "has_work_charger", "work_idx", "school_idx",
]

ITINERARY_COLUMNS = [
    "person_id", "person_type", "is_ev", "day", "day_type",
    "origin_fsa", "origin_zone_type", "origin_activity",
    "dest_fsa", "dest_zone_type", "dest_type",
    "origin_idx", "dest_idx", "depart_hour_abs", "arrival_hour_abs",
    "planned_arrival_hour_abs", "schedule_delay_min", "dwell_before_h",
    "route_km", "freeflow_time_h", "travel_time_h", "trip_kwh",
    "route_path", "reachable_route",
]

WEEKLY_LEG_COLUMNS = ITINERARY_COLUMNS + [
    "dwell_start_abs", "soc_before", "soc_after", "patch_inserted", "week_overflow_h",
]

CHARGE_EVENT_COLUMNS = [
    "person_id",
    "origin_fsa", "origin_zone_type", "origin_activity", "origin_idx",
    "dest_fsa", "dest_zone_type", "dest_type", "dest_idx",
    "charger_id", "fsa", "zone_type", "charger_lat", "charger_lon",
    "charger_source", "road_node_id", "road_snap_distance_m", "start_hour_abs", "max_duration_h",
    "charger_kw", "target_soc", "event_type", "patch_type",
    "inconvenience_minutes", "detour_km", "duration_h", "end_hour_abs",
    "energy_delivered_kwh", "soc_after_charge",
]

HOURLY_CHARGE_COLUMNS = [
    "fsa", "day", "hour", "event_type", "patch_type", "ev_load_kw", "energy_kwh",
]

EDGE_FLOW_COLUMNS = [
    "day", "hour", "edge_u", "edge_v", "fsa", "zone_type", "vehicle_count", "ev_count", "route_km",
]

BATCH_SUMMARY_COLUMNS = [
    "batch", "seed", "people", "itinerary_rows", "leg_rows", "charge_rows",
    "hourly_rows", "edge_flow_rows", "charge_energy_kwh", "hourly_energy_kwh",
    "edge_vehicle_count", "edge_ev_count", "edge_route_km",
]

GRID_LOAD_COLUMNS = [
    "fsa", "zone_type", "day", "hour", "proxy_capacity_kw",
    "baseline_load_kw", "ev_load_kw", "total_load_kw", "headroom_kw",
    "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
]


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _normalize(values: np.ndarray) -> np.ndarray:
    total = values.sum()
    if total <= 0:
        return np.full(len(values), 1.0 / len(values))
    return values / total


def _weekday_dest_probs(hour: int) -> dict[str, float]:
    if 6 <= hour <= 9:
        return {"work": 0.48, "school": 0.12, "retail": 0.07, "leisure": 0.03, "home": 0.20, "transit_hub": 0.03, "other": 0.07}
    if 10 <= hour <= 15:
        return {"work": 0.38, "school": 0.08, "retail": 0.20, "leisure": 0.08, "home": 0.12, "transit_hub": 0.03, "other": 0.11}
    if 16 <= hour <= 19:
        return {"work": 0.06, "school": 0.02, "retail": 0.17, "leisure": 0.09, "home": 0.56, "transit_hub": 0.03, "other": 0.07}
    if 20 <= hour <= 23:
        return {"work": 0.03, "school": 0.01, "retail": 0.09, "leisure": 0.16, "home": 0.62, "transit_hub": 0.02, "other": 0.07}
    return {"work": 0.02, "school": 0.00, "retail": 0.03, "leisure": 0.03, "home": 0.84, "transit_hub": 0.01, "other": 0.07}


def _weekend_dest_probs(hour: int) -> dict[str, float]:
    if 8 <= hour <= 11:
        return {"work": 0.06, "school": 0.02, "retail": 0.22, "leisure": 0.22, "home": 0.36, "transit_hub": 0.03, "other": 0.09}
    if 12 <= hour <= 17:
        return {"work": 0.04, "school": 0.01, "retail": 0.31, "leisure": 0.29, "home": 0.20, "transit_hub": 0.04, "other": 0.11}
    if 18 <= hour <= 22:
        return {"work": 0.03, "school": 0.00, "retail": 0.18, "leisure": 0.27, "home": 0.39, "transit_hub": 0.03, "other": 0.10}
    return {"work": 0.02, "school": 0.00, "retail": 0.05, "leisure": 0.05, "home": 0.78, "transit_hub": 0.02, "other": 0.08}


def destination_type_probabilities(day_type: DayType, hour: int) -> np.ndarray:
    """Return P(dest_type | day_type, hour) in DEST_TYPES order."""
    hour = int(hour) % 24
    probs = _weekend_dest_probs(hour) if day_type == "weekend" else _weekday_dest_probs(hour)
    return np.array([probs[t] for t in DEST_TYPES], dtype=float)


def _sample_outing_type(rng: np.random.Generator, day_type: DayType) -> str:
    probs = WEEKEND_OUTING_TYPE_PROBS if day_type == "weekend" else WEEKDAY_OUTING_TYPE_PROBS
    labels = np.array(list(probs), dtype=object)
    weights = np.array(list(probs.values()), dtype=float)
    return str(rng.choice(labels, p=weights / weights.sum()))


def _haversine_matrix(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat_rad = np.radians(lat)
    lon_rad = np.radians(lon)
    dlat = lat_rad[:, None] - lat_rad[None, :]
    dlon = lon_rad[:, None] - lon_rad[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat_rad[:, None]) * np.cos(lat_rad[None, :]) * np.sin(dlon / 2) ** 2
    return 6371.0 * 2.0 * np.arcsin(np.sqrt(a))


def _choice_from_probs(rng: np.random.Generator, items: np.ndarray, probs: np.ndarray, size: int) -> np.ndarray:
    return rng.choice(items, size=size, replace=True, p=_normalize(probs))


class MobilitySimulationEngine:
    """
    Agent mobility model over a road-network-backed FSA route matrix.

    `road_graph_source="auto"` uses cached OSMnx road data when available and
    otherwise falls back to a deterministic FSA adjacency graph built from the
    supplied GIS polygons. Either way, trip distance and trip time come from a
    network path rather than a complete centroid-to-centroid shortcut.
    """

    def __init__(self, config: MobilityConfig | None = None):
        self.config = config or MobilityConfig()
        cache_key = self._static_context_cache_key(self.config)
        if cache_key is not None and cache_key in _STATIC_CONTEXT_CACHE:
            self._apply_static_context(_STATIC_CONTEXT_CACHE[cache_key])
            self._prepare_behavior_caches()
            return

        self.base_gdf = load_enriched_geodataframe()
        self._prepare_spatial_tables()
        self.road_network = RoadNetwork(
            self.base_gdf,
            source=self.config.road_graph_source,
            force_osm_download=self.config.force_osm_download,
        )
        self.charger_catalog = ChargerCatalog(
            self.base_gdf,
            source=self.config.charger_source,
            force_osm_download=self.config.force_charger_download,
        )
        self.charger_catalog.snap_to_road_network(self.road_network)
        self.route_km = self.road_network.route_km
        self.freeflow_time_h = self.road_network.freeflow_time_h
        self._prepare_behavior_caches()
        if cache_key is not None:
            _STATIC_CONTEXT_CACHE[cache_key] = self._static_context()

    @staticmethod
    def _static_context_cache_key(config: MobilityConfig) -> tuple[object, ...] | None:
        if config.force_osm_download or config.force_charger_download:
            return None
        return (
            str(config.road_graph_source),
            str(config.charger_source),
            _file_signature([
                Path(FSA_GEOJSON),
                Path(ZONE_CSV),
                Path(FSA_POPULATION_CSV),
                TRAFFIC_FSA_COUNTS_CSV,
                OSM_GRAPHML,
                OSM_GRAPH_PICKLE,
                OSM_ROUTE_CACHE,
                FSA_ROUTE_CACHE,
                AFDC_CHARGERS_CSV,
                OSM_CHARGERS_CSV,
            ]),
        )

    def _static_context(self) -> _StaticMobilityContext:
        return _StaticMobilityContext(
            base_gdf=self.base_gdf,
            fsas=self.fsas,
            zone_types=self.zone_types,
            traffic_am_attraction=self.traffic_am_attraction,
            traffic_pm_attraction=self.traffic_pm_attraction,
            traffic_total_attraction=self.traffic_total_attraction,
            distance_km=self.distance_km,
            road_network=self.road_network,
            charger_catalog=self.charger_catalog,
            route_km=self.route_km,
            freeflow_time_h=self.freeflow_time_h,
        )

    def _apply_static_context(self, context: _StaticMobilityContext) -> None:
        self.base_gdf = context.base_gdf
        self.fsas = context.fsas
        self.zone_types = context.zone_types
        self.traffic_am_attraction = context.traffic_am_attraction
        self.traffic_pm_attraction = context.traffic_pm_attraction
        self.traffic_total_attraction = context.traffic_total_attraction
        self.distance_km = context.distance_km
        self.road_network = context.road_network
        self.charger_catalog = context.charger_catalog
        self.route_km = context.route_km
        self.freeflow_time_h = context.freeflow_time_h

    def _prepare_behavior_caches(self) -> None:
        self._fsa_indices = np.arange(len(self.fsas), dtype=int)
        self._zone_attraction_cache = {
            dest_type: pd.Series(self.zone_types).map(DEST_ATTRACTION_BY_ZONE[dest_type]).fillna(1.0).to_numpy(dtype=float)
            for dest_type in DEST_TYPES
        }
        self._destination_prob_cache: dict[tuple[str, int], np.ndarray] = {}

    def _prepare_spatial_tables(self) -> None:
        gdf = self.base_gdf.copy()
        area_km2 = gdf.to_crs(epsg=32617).geometry.area.to_numpy() / 1_000_000
        gdf["area_km2"] = np.maximum(area_km2, 0.05)
        capped_area = np.minimum(gdf["area_km2"].to_numpy(), 12.0)
        population = pd.to_numeric(gdf.get("population_2021"), errors="coerce").fillna(0.0)
        if float(population.sum()) > 0:
            gdf["home_weight"] = np.maximum(population.to_numpy(dtype=float), 1.0)
        else:
            gdf["home_weight"] = np.sqrt(capped_area) * gdf["zone_type"].map(HOME_ZONE_MULTIPLIER).fillna(0.2)
        gdf = self._attach_traffic_attraction(gdf)

        self.base_gdf = gdf
        self.fsas = gdf["fsa"].to_numpy()
        self.zone_types = gdf["zone_type"].to_numpy()
        self.traffic_am_attraction = gdf["traffic_am_attraction"].to_numpy(dtype=float)
        self.traffic_pm_attraction = gdf["traffic_pm_attraction"].to_numpy(dtype=float)
        self.traffic_total_attraction = gdf["traffic_total_attraction"].to_numpy(dtype=float)
        self.distance_km = _haversine_matrix(gdf["centroid_lat"].to_numpy(), gdf["centroid_lon"].to_numpy())

    @staticmethod
    def _attach_traffic_attraction(gdf: pd.DataFrame) -> pd.DataFrame:
        gdf = gdf.copy()
        defaults = {
            "traffic_am_attraction": 1.0,
            "traffic_pm_attraction": 1.0,
            "traffic_total_attraction": 1.0,
        }
        if not TRAFFIC_FSA_COUNTS_CSV.exists():
            return gdf.assign(**defaults)

        counts = pd.read_csv(TRAFFIC_FSA_COUNTS_CSV)
        grouped = counts.groupby("fsa")[["am_peak_vehicle", "pm_peak_vehicle", "total_vehicle"]].sum()
        for source_col, target_col in [
            ("am_peak_vehicle", "traffic_am_attraction"),
            ("pm_peak_vehicle", "traffic_pm_attraction"),
            ("total_vehicle", "traffic_total_attraction"),
        ]:
            values = gdf["fsa"].astype(str).map(grouped[source_col]).fillna(0.0).astype(float)
            positive = values[values > 0]
            if positive.empty or float(positive.mean()) <= 0:
                gdf[target_col] = 1.0
                continue
            relative = np.ones(len(gdf), dtype=float)
            observed = values > 0
            relative[observed.to_numpy()] = np.clip(values[observed].to_numpy() / float(positive.mean()), 0.15, 6.0)
            gdf[target_col] = relative
        return gdf

    def population_expansion_scale(self, num_people: int, *, population_share: float | None = None) -> float:
        """Return population/sample expansion factor from observed FSA counts."""
        if num_people <= 0:
            return 1.0
        population_share = self.config.vehicle_population_share if population_share is None else population_share
        population = pd.to_numeric(self.base_gdf.get("population_2021"), errors="coerce").fillna(0.0)
        total_population = float(population.sum())
        if total_population <= 0:
            return 1.0
        return max(total_population * population_share / float(num_people), 0.0)

    def _destination_probs_for_type(self, home_idx: int, dest_type: str, hour: int) -> np.ndarray:
        hour = int(hour) % 24
        matrix = self._destination_prob_matrix(dest_type, hour)
        return matrix[int(home_idx)]

    def _destination_prob_matrix(self, dest_type: str, hour: int) -> np.ndarray:
        key = (str(dest_type), int(hour) % 24)
        cached = self._destination_prob_cache.get(key)
        if cached is not None:
            return cached

        dest_type = str(dest_type)
        hour = int(hour) % 24
        zone_attraction = self._zone_attraction_cache[dest_type]
        route_km = self.route_km
        distance_decay = np.exp(-route_km / DEST_TAU_KM[dest_type])
        long_trip_penalty = np.exp(-np.maximum(route_km - DEST_SOFT_MAX_KM[dest_type], 0.0) / 8.0)

        peak_factor = np.ones(len(self.fsas), dtype=float)
        if 7 <= hour <= 9 and dest_type in {"work", "school", "transit_hub"}:
            peak_factor *= pd.Series(self.zone_types).map({"office_park": 1.4, "retail_hub": 1.1, "transit_hub": 1.5}).fillna(1.0).to_numpy(dtype=float)
        elif 16 <= hour <= 19 and dest_type == "home":
            peak_factor *= pd.Series(self.zone_types).map({"residential": 1.5, "retail_hub": 0.8, "office_park": 0.5}).fillna(1.0).to_numpy(dtype=float)

        destination_weight = zone_attraction * peak_factor * self._traffic_attraction_for(dest_type, hour)
        probs = distance_decay * long_trip_penalty * destination_weight[None, :]
        probs = probs.astype(np.float64, copy=True)
        diagonal_multiplier = 8.0 if dest_type == "home" else 0.25
        np.fill_diagonal(probs, np.diag(probs) * diagonal_multiplier)
        row_sums = probs.sum(axis=1)
        bad = row_sums <= 0
        if bad.any():
            probs[bad, :] = 1.0 / len(self.fsas)
            row_sums[bad] = 1.0
        probs = probs / row_sums[:, None]
        self._destination_prob_cache[key] = probs
        return probs

    def _sample_destination_indices(
        self,
        rng: np.random.Generator,
        origin_idx: np.ndarray,
        dest_type: str,
        hour: int,
    ) -> np.ndarray:
        origin_idx = np.asarray(origin_idx, dtype=int)
        dest = np.empty(len(origin_idx), dtype=int)
        if len(origin_idx) == 0:
            return dest
        matrix = self._destination_prob_matrix(dest_type, hour)
        for origin in np.unique(origin_idx):
            mask = origin_idx == origin
            dest[mask] = rng.choice(self._fsa_indices, size=int(mask.sum()), p=matrix[int(origin)])
        return dest

    def _traffic_attraction_for(self, dest_type: str, hour: int) -> np.ndarray:
        exponent = max(float(self.config.traffic_attraction_exponent), 0.0)
        if exponent <= 0 or dest_type == "home":
            return np.ones(len(self.fsas), dtype=float)
        hour = int(hour) % 24
        if 6 <= hour <= 10:
            base = self.traffic_am_attraction
        elif 15 <= hour <= 20:
            base = self.traffic_pm_attraction
        else:
            base = self.traffic_total_attraction
        return np.power(np.clip(base, 0.25, 3.0), exponent)

    def run_agent_day(
        self,
        num_people: int = 10_000,
        day_type: DayType = "weekday",
        hour: int = 17,
        seed: int | None = None,
    ) -> pd.DataFrame:
        """
        Simulate one representative trip/charging opportunity per person.

        Returns one row per sampled person, including non-EVs. Downstream load
        aggregation filters to EVs that decided to charge.
        """
        rng = np.random.default_rng(seed)
        cfg = self.config

        if num_people <= 0:
            return pd.DataFrame(columns=PEOPLE_COLUMNS), pd.DataFrame(columns=ITINERARY_COLUMNS)

        home_probs = _normalize(self.base_gdf["home_weight"].to_numpy())
        home_idx = _choice_from_probs(rng, np.arange(len(self.fsas)), home_probs, num_people)

        dest_type_probs = destination_type_probabilities(day_type, hour)
        dest_type = rng.choice(np.array(DEST_TYPES), size=num_people, p=dest_type_probs)

        dest_idx = np.empty(num_people, dtype=int)
        for value in np.unique(dest_type):
            mask = dest_type == value
            dest_idx[mask] = self._sample_destination_indices(rng, home_idx[mask], str(value), hour)

        direct_km = self.distance_km[home_idx, dest_idx]
        local_trip_km = rng.gamma(shape=2.0, scale=1.0, size=num_people)
        route_km = np.where(home_idx == dest_idx, local_trip_km, self.route_km[home_idx, dest_idx])
        trip_kwh = route_km * cfg.ev_efficiency_kwh_per_km

        is_ev = rng.random(num_people) < cfg.ev_probability
        initial_soc = rng.beta(cfg.initial_soc_alpha, cfg.initial_soc_beta, size=num_people)
        projected_arrival_soc = np.clip(initial_soc - (trip_kwh / cfg.battery_capacity_kwh), 0.0, 1.0)

        dest_zone = self.zone_types[dest_idx]
        charger_distance_km = pd.Series(dest_zone).map(CHARGER_DISTANCE_KM_BY_ZONE).fillna(1.0).to_numpy()
        walk_m = charger_distance_km * 1000.0
        dwell_hours = self._sample_dwell_hours(rng, dest_type, num_people)
        availability = pd.Series(dest_zone).map(AVAILABILITY_BY_ZONE).fillna(0.85).to_numpy()

        soc_need = np.clip((0.50 - projected_arrival_soc) / 0.30, 0.0, 1.0)
        range_anxiety = np.clip((0.25 - projected_arrival_soc) / 0.25, 0.0, 1.0)
        proximity = np.exp(-walk_m / 500.0)
        dwell = 1.0 - np.exp(-dwell_hours / 2.0)
        detour_km = charger_distance_km * 1.6

        charge_logits = -3.0 + 3.0 * soc_need + 3.0 * range_anxiety + 1.2 * proximity + 1.0 * dwell - 0.6 * detour_km
        charge_probability = np.clip(_sigmoid(charge_logits) * availability, 0.0, 1.0)
        will_charge = is_ev & (rng.random(num_people) < charge_probability)

        charger_kw = pd.Series(dest_zone).map(CHARGER_POWER_KW).fillna(7.0).to_numpy()
        energy_needed_kwh = np.maximum((cfg.target_soc - projected_arrival_soc) * cfg.battery_capacity_kwh, 0.0)
        charge_duration_h = np.where(will_charge, np.minimum(dwell_hours, energy_needed_kwh / charger_kw), 0.0)
        energy_delivered_kwh = charge_duration_h * charger_kw

        arrival_hour = np.clip(rng.normal(loc=hour + 0.5, scale=0.35, size=num_people), hour, hour + 0.99)

        return pd.DataFrame({
            "person_id": [f"P_{i:06d}" for i in range(1, num_people + 1)],
            "is_ev": is_ev,
            "home_fsa": self.fsas[home_idx],
            "home_zone_type": self.zone_types[home_idx],
            "dest_type": dest_type,
            "dest_fsa": self.fsas[dest_idx],
            "dest_zone_type": dest_zone,
            "arrival_hour_float": arrival_hour,
            "arrival_time": [float_to_time(t) for t in arrival_hour],
            "route_km": np.round(route_km, 2),
            "trip_kwh": np.round(trip_kwh, 2),
            "initial_soc": np.round(initial_soc, 3),
            "arrival_soc": np.round(projected_arrival_soc, 3),
            "dwell_hours": np.round(dwell_hours, 2),
            "charger_distance_km": np.round(charger_distance_km, 2),
            "charge_probability": np.round(charge_probability, 3),
            "will_charge": will_charge,
            "charger_kw": charger_kw,
            "charge_duration_h": np.round(charge_duration_h, 2),
            "energy_delivered_kwh": np.round(energy_delivered_kwh, 2),
        })

    def generate_weekly_itinerary(
        self,
        num_people: int = 5_000,
        seed: int | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Generate a full Monday-Sunday trip plan before any charging is patched.

        The week starts Monday 00:00 with every person at home. Most day
        templates start and end at home; low-travel days simply produce no legs.
        """
        rng = np.random.default_rng(seed)
        cfg = self.config

        home_probs = _normalize(self.base_gdf["home_weight"].to_numpy())
        home_idx = _choice_from_probs(rng, np.arange(len(self.fsas)), home_probs, num_people)
        person_types = rng.choice(
            np.array(list(PERSON_TYPE_PROBS)),
            size=num_people,
            p=np.array(list(PERSON_TYPE_PROBS.values())),
        )
        is_ev = rng.random(num_people) < cfg.ev_probability
        initial_soc = rng.beta(cfg.initial_soc_alpha, cfg.initial_soc_beta, size=num_people)
        has_home_charger = rng.random(num_people) < cfg.home_charger_probability
        has_work_charger = rng.random(num_people) < cfg.work_charger_probability

        work_idx = np.full(num_people, -1, dtype=int)
        school_idx = np.full(num_people, -1, dtype=int)
        worker_mask = person_types == "worker"
        school_mask = np.isin(person_types, ["student", "worker", "other"])
        work_idx[worker_mask] = self._sample_destination_indices(rng, home_idx[worker_mask], "work", 8)
        school_idx[school_mask] = self._sample_destination_indices(rng, home_idx[school_mask], "school", 8)

        person_ids = np.array([f"P_{i:06d}" for i in range(1, num_people + 1)], dtype=object)
        home_fsa = self.fsas[home_idx]
        home_zone_type = self.zone_types[home_idx]

        people = pd.DataFrame({
            "person_id": person_ids,
            "person_type": person_types,
            "is_ev": is_ev,
            "home_idx": home_idx,
            "home_fsa": home_fsa,
            "home_zone_type": home_zone_type,
            "initial_soc": np.round(initial_soc, 3),
            "has_home_charger": has_home_charger,
            "has_work_charger": has_work_charger,
            "work_idx": work_idx,
            "school_idx": school_idx,
        })

        legs: list[dict] = []
        for person_i in range(num_people):
            current_idx = int(home_idx[person_i])
            current_activity = "home"
            last_arrival_abs = 0.0
            person_type = str(person_types[person_i])
            person_home_idx = int(home_idx[person_i])
            person_work_idx = int(work_idx[person_i])
            person_school_idx = int(school_idx[person_i])
            person_id = str(person_ids[person_i])
            person_is_ev = bool(is_ev[person_i])
            person_home_fsa = str(home_fsa[person_i])
            person_home_zone_type = str(home_zone_type[person_i])
            for day in range(7):
                day_type: DayType = "weekday" if day < 5 else "weekend"
                day_plan = self._sample_day_plan(rng, person_type, day_type, person_home_idx, person_work_idx, person_school_idx)
                for stop in day_plan:
                    route = self.road_network.route(current_idx, stop.dest_idx)
                    planned_abs = day * 24.0 + stop.hour
                    if stop.timing == "arrive_by":
                        duration_h = self.road_network.travel_time_h(current_idx, stop.dest_idx, planned_abs, route=route)
                        depart_abs = max(last_arrival_abs + 0.25, planned_abs - duration_h)
                        duration_h = self.road_network.travel_time_h(current_idx, stop.dest_idx, depart_abs, route=route)
                    else:
                        depart_abs = max(last_arrival_abs + 0.25, planned_abs)
                        duration_h = self.road_network.travel_time_h(current_idx, stop.dest_idx, depart_abs, route=route)
                    arrival_abs = depart_abs + duration_h
                    if arrival_abs > 168.0:
                        depart_abs = max(last_arrival_abs + 0.25, 168.0 - duration_h)
                        arrival_abs = depart_abs + duration_h
                        if depart_abs >= 168.0 or arrival_abs > 168.0:
                            break
                    route_km = route.distance_km
                    legs.append({
                        "person_id": person_id,
                        "person_type": person_type,
                        "is_ev": person_is_ev,
                        "day": day,
                        "day_type": day_type,
                        "origin_fsa": self.fsas[current_idx],
                        "origin_zone_type": self.zone_types[current_idx],
                        "origin_activity": current_activity,
                        "dest_fsa": self.fsas[stop.dest_idx],
                        "dest_zone_type": self.zone_types[stop.dest_idx],
                        "dest_type": stop.dest_type,
                        "origin_idx": current_idx,
                        "dest_idx": int(stop.dest_idx),
                        "depart_hour_abs": depart_abs,
                        "arrival_hour_abs": arrival_abs,
                        "planned_arrival_hour_abs": planned_abs if stop.timing == "arrive_by" else np.nan,
                        "schedule_delay_min": max(0.0, arrival_abs - planned_abs) * 60.0 if stop.timing == "arrive_by" else 0.0,
                        "dwell_before_h": max(0.0, depart_abs - last_arrival_abs),
                        "route_km": round(route_km, 2),
                        "freeflow_time_h": round(route.freeflow_time_h, 3),
                        "travel_time_h": round(duration_h, 3),
                        "trip_kwh": round(route_km * cfg.ev_efficiency_kwh_per_km, 2),
                        "route_path": "|".join(map(str, route.path)),
                        "reachable_route": bool(route.reachable),
                    })
                    current_idx = int(stop.dest_idx)
                    current_activity = stop.dest_type
                    last_arrival_abs = arrival_abs

                # If the sampled day did not return home, force a home leg.
                if current_idx != person_home_idx:
                    depart_abs = max(last_arrival_abs + 0.25, day * 24.0 + 20.5)
                    route = self.road_network.route(current_idx, person_home_idx)
                    duration_h = self.road_network.travel_time_h(current_idx, person_home_idx, depart_abs, route=route)
                    route_km = route.distance_km
                    arrival_abs = depart_abs + duration_h
                    if arrival_abs > 168.0:
                        depart_abs = max(last_arrival_abs + 0.25, 168.0 - duration_h)
                        arrival_abs = depart_abs + duration_h
                        if depart_abs >= 168.0 or arrival_abs > 168.0:
                            continue
                    legs.append({
                        "person_id": person_id,
                        "person_type": person_type,
                        "is_ev": person_is_ev,
                        "day": day,
                        "day_type": day_type,
                        "origin_fsa": self.fsas[current_idx],
                        "origin_zone_type": self.zone_types[current_idx],
                        "origin_activity": current_activity,
                        "dest_fsa": person_home_fsa,
                        "dest_zone_type": person_home_zone_type,
                        "dest_type": "home",
                        "origin_idx": current_idx,
                        "dest_idx": person_home_idx,
                        "depart_hour_abs": depart_abs,
                        "arrival_hour_abs": arrival_abs,
                        "planned_arrival_hour_abs": np.nan,
                        "schedule_delay_min": 0.0,
                        "dwell_before_h": max(0.0, depart_abs - last_arrival_abs),
                        "route_km": round(route_km, 2),
                        "freeflow_time_h": round(route.freeflow_time_h, 3),
                        "travel_time_h": round(duration_h, 3),
                        "trip_kwh": round(route_km * cfg.ev_efficiency_kwh_per_km, 2),
                        "route_path": "|".join(map(str, route.path)),
                        "reachable_route": bool(route.reachable),
                    })
                    current_idx = person_home_idx
                    current_activity = "home"
                    last_arrival_abs = arrival_abs

        itinerary = pd.DataFrame(legs)
        if not itinerary.empty:
            itinerary = itinerary.sort_values(["person_id", "depart_hour_abs"]).reset_index(drop=True)
        else:
            itinerary = pd.DataFrame(columns=ITINERARY_COLUMNS)
        return people, itinerary

    def _sample_day_plan(
        self,
        rng: np.random.Generator,
        person_type: str,
        day_type: DayType,
        home_idx: int,
        work_idx: int,
        school_idx: int,
    ) -> list[PlannedStop]:
        plan: list[PlannedStop] = []
        cfg = self.config
        if day_type == "weekday":
            if person_type == "worker" and work_idx >= 0 and rng.random() < cfg.worker_weekday_work_probability:
                plan.append(PlannedStop("work", work_idx, float(np.clip(rng.normal(8.95, 0.35), 7.75, 10.0)), "arrive_by"))
                if rng.random() < cfg.after_work_stop_probability:
                    retail_idx = rng.choice(self._fsa_indices, p=self._destination_probs_for_type(work_idx, "retail", 17))
                    plan.append(PlannedStop("retail", int(retail_idx), float(np.clip(rng.normal(17.25, 0.55), 16.0, 19.25)), "depart_at"))
                    plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(19.2, 0.7), 17.5, 22.0)), "depart_at"))
                else:
                    plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(17.2, 0.8), 15.5, 20.5)), "depart_at"))
            elif person_type == "student" and school_idx >= 0 and rng.random() < cfg.student_weekday_school_probability:
                plan.append(PlannedStop("school", school_idx, float(np.clip(rng.normal(8.35, 0.25), 7.5, 9.0)), "arrive_by"))
                plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(15.5, 0.8), 14.0, 18.5)), "depart_at"))
            elif rng.random() < cfg.weekday_nonworker_outing_probability:
                dest_type = _sample_outing_type(rng, day_type)
                dest_idx = rng.choice(self._fsa_indices, p=self._destination_probs_for_type(home_idx, dest_type, 13))
                plan.append(PlannedStop(dest_type, int(dest_idx), float(np.clip(rng.normal(12.8, 1.5), 9.0, 17.0)), "depart_at"))
                plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(15.5, 1.8), 11.0, 21.0)), "depart_at"))
        else:
            if person_type == "worker" and work_idx >= 0 and rng.random() < cfg.worker_weekend_work_probability:
                plan.append(PlannedStop("work", work_idx, float(np.clip(rng.normal(10.0, 1.3), 7.0, 14.0)), "arrive_by"))
                plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(17.0, 1.5), 13.0, 22.0)), "depart_at"))
            elif rng.random() < cfg.weekend_outing_probability:
                first_type = _sample_outing_type(rng, day_type)
                first_idx = rng.choice(self._fsa_indices, p=self._destination_probs_for_type(home_idx, first_type, 13))
                plan.append(PlannedStop(first_type, int(first_idx), float(np.clip(rng.normal(12.2, 1.8), 8.0, 17.0)), "depart_at"))
                if rng.random() < cfg.weekend_second_stop_probability:
                    second_type = "leisure" if first_type == "retail" else "retail"
                    second_idx = rng.choice(self._fsa_indices, p=self._destination_probs_for_type(int(first_idx), second_type, 16))
                    plan.append(PlannedStop(second_type, int(second_idx), float(np.clip(rng.normal(16.2, 1.4), 12.0, 20.0)), "depart_at"))
                    plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(19.0, 1.5), 15.0, 23.0)), "depart_at"))
                else:
                    plan.append(PlannedStop("home", home_idx, float(np.clip(rng.normal(16.0, 1.8), 11.0, 22.0)), "depart_at"))
        return plan

    def _leg_travel_time(self, leg: pd.Series, depart_abs: float) -> float:
        origin_idx = int(leg["origin_idx"])
        dest_idx = int(leg["dest_idx"])
        route = self.road_network.route(origin_idx, dest_idx)
        return self.road_network.travel_time_h(origin_idx, dest_idx, depart_abs, route=route)

    def simulate_weekly_charging(
        self,
        people: pd.DataFrame,
        itinerary: pd.DataFrame,
        seed: int | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Run SoC forward through the weekly itinerary and insert charging.

        Normal charging is evaluated at every dwell. If a leg would violate
        reserve, a probabilistic patch charge is inserted. Trips are never
        marked failed; the fallback is forced origin-side public charging.
        """
        if people.empty or itinerary.empty:
            return pd.DataFrame(columns=WEEKLY_LEG_COLUMNS), pd.DataFrame(columns=CHARGE_EVENT_COLUMNS)

        rng = np.random.default_rng(seed)
        cfg = self.config
        people_lookup = {str(person["person_id"]): person for person in people.to_dict("records")}
        leg_rows: list[dict] = []
        charge_rows: list[dict] = []

        for person_id, person_legs in itinerary.groupby("person_id", sort=False):
            person = people_lookup[str(person_id)]
            capacity = cfg.battery_capacity_kwh
            soc_kwh = float(person["initial_soc"]) * capacity
            legs = person_legs.sort_values("depart_hour_abs").to_dict("records")
            future_needs = self._future_needs_until_strong_charger(legs, person, capacity)
            current_time = 0.0
            current_idx = int(person["home_idx"])
            current_fsa = str(person["home_fsa"])
            current_zone_type = str(person["home_zone_type"])
            current_activity = "home"

            for leg_pos, leg in enumerate(legs):
                depart_abs = max(float(leg["depart_hour_abs"]), current_time)
                if depart_abs >= 168.0:
                    break

                dwell_before_h = max(0.0, depart_abs - current_time)
                adjusted_leg = dict(leg)
                adjusted_leg["origin_fsa"] = current_fsa
                adjusted_leg["origin_zone_type"] = current_zone_type
                adjusted_leg["origin_activity"] = current_activity
                adjusted_leg["origin_idx"] = current_idx
                adjusted_leg["dwell_start_abs"] = current_time
                adjusted_leg["dwell_before_h"] = dwell_before_h
                adjusted_leg["depart_hour_abs"] = depart_abs
                adjusted_leg["charge_blocked_until_abs"] = current_time

                if not bool(person["is_ev"]):
                    duration_h = self._leg_travel_time(adjusted_leg, depart_abs)
                    adjusted_leg["travel_time_h"] = round(duration_h, 3)
                    arrival_abs = min(168.0, depart_abs + duration_h)
                    planned_arrival = adjusted_leg.get("planned_arrival_hour_abs", np.nan)
                    schedule_delay_min = (
                        max(0.0, depart_abs + duration_h - float(planned_arrival)) * 60.0
                        if pd.notna(planned_arrival)
                        else 0.0
                    )
                    row = dict(adjusted_leg)
                    row.update({
                        "day": int(min(6, depart_abs // 24)),
                        "day_type": "weekday" if int(min(6, depart_abs // 24)) < 5 else "weekend",
                        "arrival_hour_abs": arrival_abs,
                        "schedule_delay_min": round(schedule_delay_min, 3),
                        "soc_before": np.nan,
                        "soc_after": np.nan,
                        "patch_inserted": False,
                        "week_overflow_h": max(0.0, depart_abs + duration_h - 168.0),
                    })
                    leg_rows.append(row)
                    current_time = arrival_abs
                    current_idx = int(leg["dest_idx"])
                    current_fsa = str(leg["dest_fsa"])
                    current_zone_type = str(leg["dest_zone_type"])
                    current_activity = str(leg["dest_type"])
                    continue

                future_need = future_needs[leg_pos]
                normal_event = self._maybe_normal_charge(rng, adjusted_leg, person, soc_kwh, future_need, capacity)
                if normal_event is not None:
                    soc_kwh, event = self._apply_charge_event(soc_kwh, normal_event, capacity)
                    if event["energy_delivered_kwh"] > 1e-9:
                        charge_rows.append(event)
                    depart_abs = max(depart_abs, float(event["end_hour_abs"]))
                    adjusted_leg["depart_hour_abs"] = depart_abs
                    adjusted_leg["charge_blocked_until_abs"] = float(event["end_hour_abs"])
                    adjusted_leg["dwell_before_h"] = max(0.0, depart_abs - current_time)

                required_for_leg = float(adjusted_leg["trip_kwh"]) + cfg.reserve_soc * capacity
                patch_inserted = False
                if soc_kwh < required_for_leg:
                    patch_event = self._choose_patch_charge(rng, adjusted_leg, person, soc_kwh, required_for_leg, capacity)
                    soc_kwh, event = self._apply_charge_event(soc_kwh, patch_event, capacity)
                    if event["energy_delivered_kwh"] > 1e-9:
                        charge_rows.append(event)
                    detour_h = float(event["detour_km"]) / 30.0
                    depart_abs = max(depart_abs, float(event["end_hour_abs"]) + detour_h)
                    adjusted_leg["depart_hour_abs"] = depart_abs
                    adjusted_leg["dwell_before_h"] = max(0.0, depart_abs - current_time)
                    patch_inserted = True

                soc_before = soc_kwh / capacity
                soc_kwh = max(0.0, soc_kwh - float(adjusted_leg["trip_kwh"]))
                duration_h = self._leg_travel_time(adjusted_leg, depart_abs)
                adjusted_leg["travel_time_h"] = round(duration_h, 3)
                raw_arrival_abs = depart_abs + duration_h
                arrival_abs = min(168.0, raw_arrival_abs)
                planned_arrival = adjusted_leg.get("planned_arrival_hour_abs", np.nan)
                schedule_delay_min = (
                    max(0.0, raw_arrival_abs - float(planned_arrival)) * 60.0
                    if pd.notna(planned_arrival)
                    else 0.0
                )
                row = dict(adjusted_leg)
                row.update({
                    "day": int(min(6, depart_abs // 24)),
                    "day_type": "weekday" if int(min(6, depart_abs // 24)) < 5 else "weekend",
                    "arrival_hour_abs": arrival_abs,
                    "schedule_delay_min": round(schedule_delay_min, 3),
                    "soc_before": round(soc_before, 3),
                    "soc_after": round(soc_kwh / capacity, 3),
                    "patch_inserted": patch_inserted,
                    "week_overflow_h": max(0.0, raw_arrival_abs - 168.0),
                })
                leg_rows.append(row)
                current_time = arrival_abs
                current_idx = int(adjusted_leg["dest_idx"])
                current_fsa = str(adjusted_leg["dest_fsa"])
                current_zone_type = str(adjusted_leg["dest_zone_type"])
                current_activity = str(adjusted_leg["dest_type"])

            if bool(person["is_ev"]) and current_time < 168.0:
                terminal_leg = {
                    "person_id": person_id,
                    "origin_fsa": current_fsa,
                    "origin_zone_type": current_zone_type,
                    "origin_activity": current_activity,
                    "origin_idx": current_idx,
                    "depart_hour_abs": 168.0,
                    "dwell_start_abs": current_time,
                    "dwell_before_h": 168.0 - current_time,
                    "charge_blocked_until_abs": current_time,
                }
                terminal_need = max(cfg.reserve_soc * capacity, cfg.week_end_target_soc * capacity)
                terminal_event = self._maybe_normal_charge(rng, terminal_leg, person, soc_kwh, terminal_need, capacity)
                terminal_blocked_until = current_time
                if terminal_event is not None:
                    soc_kwh, event = self._apply_charge_event(soc_kwh, terminal_event, capacity)
                    terminal_blocked_until = float(event["end_hour_abs"])
                    if event["energy_delivered_kwh"] > 1e-9:
                        charge_rows.append(event)
                if soc_kwh < cfg.reserve_soc * capacity and terminal_blocked_until < 168.0:
                    reserve_charger = self._charger_for_activity(terminal_leg, current_activity, person)
                    reserve_start = max(current_time, terminal_blocked_until)
                    reserve_duration = max(0.0, 168.0 - reserve_start)
                    reserve_event = self._make_charge_event(
                        person_id=str(person_id),
                        leg=terminal_leg,
                        charger=reserve_charger,
                        start_hour_abs=reserve_start,
                        max_duration_h=reserve_duration,
                        target_soc=max(cfg.reserve_soc, cfg.week_end_target_soc),
                        event_type="patch",
                        patch_type="terminal_reserve",
                        inconvenience_minutes=0.0,
                        detour_km=reserve_charger.detour_km,
                    )
                    soc_kwh, event = self._apply_charge_event(soc_kwh, reserve_event, capacity)
                    if event["energy_delivered_kwh"] > 1e-9:
                        charge_rows.append(event)

        return pd.DataFrame(leg_rows, columns=WEEKLY_LEG_COLUMNS), pd.DataFrame(charge_rows, columns=CHARGE_EVENT_COLUMNS)

    def run_weekly_batched_aggregation(
        self,
        num_people: int,
        *,
        seed: int | None = None,
        batch_size: int = 25_000,
        edge_flow_detail: Literal["full", "fsa"] = "fsa",
    ) -> dict[str, pd.DataFrame]:
        """
        Run exact weekly simulation in bounded-memory batches.

        This keeps the same per-person itinerary and SoC/charging rules as
        `generate_weekly_itinerary` + `simulate_weekly_charging`, but only keeps
        aggregated hourly charging and road-flow tables across batches. It is
        the intended path for 100k+ population runs.
        """
        num_people = int(num_people)
        batch_size = int(batch_size)
        if num_people <= 0:
            empty_hourly = pd.DataFrame(columns=HOURLY_CHARGE_COLUMNS)
            return {
                "hourly": empty_hourly,
                "grid_load": self.aggregate_weekly_grid_load(empty_hourly),
                "edge_flows": pd.DataFrame(columns=EDGE_FLOW_COLUMNS),
                "batches": pd.DataFrame(columns=BATCH_SUMMARY_COLUMNS),
            }
        if batch_size <= 0:
            raise ValueError("batch_size must be positive.")
        if edge_flow_detail not in {"full", "fsa"}:
            raise ValueError("edge_flow_detail must be 'full' or 'fsa'.")

        base_seed = 0 if seed is None else int(seed)
        hourly_parts: list[pd.DataFrame] = []
        edge_parts: list[pd.DataFrame] = []
        batch_rows: list[dict[str, int]] = []
        for batch_idx, start in enumerate(range(0, num_people, batch_size)):
            size = min(batch_size, num_people - start)
            batch_seed = base_seed + batch_idx * 10_007
            people, itinerary = self.generate_weekly_itinerary(size, seed=batch_seed)
            legs, charges = self.simulate_weekly_charging(people, itinerary, seed=batch_seed + 1)
            hourly = self.aggregate_charge_events(charges)
            if edge_flow_detail == "full":
                edge_flows = self.aggregate_edge_flows(legs)
            else:
                edge_flows = self.aggregate_fsa_corridor_flows(legs)
            if not hourly.empty:
                hourly_parts.append(hourly)
            if not edge_flows.empty:
                edge_parts.append(edge_flows)
            batch_rows.append({
                "batch": batch_idx,
                "seed": batch_seed,
                "people": size,
                "itinerary_rows": len(itinerary),
                "leg_rows": len(legs),
                "charge_rows": len(charges),
                "hourly_rows": len(hourly),
                "edge_flow_rows": len(edge_flows),
                "charge_energy_kwh": round(float(charges["energy_delivered_kwh"].sum()), 6) if not charges.empty else 0.0,
                "hourly_energy_kwh": round(float(hourly["energy_kwh"].sum()), 6) if not hourly.empty else 0.0,
                "edge_vehicle_count": round(float(edge_flows["vehicle_count"].sum()), 6) if not edge_flows.empty else 0.0,
                "edge_ev_count": round(float(edge_flows["ev_count"].sum()), 6) if not edge_flows.empty else 0.0,
                "edge_route_km": round(float(edge_flows["route_km"].sum()), 6) if not edge_flows.empty else 0.0,
            })

        hourly_all = self._sum_hourly_parts(hourly_parts)
        edge_all = self._sum_edge_flow_parts(edge_parts)
        return {
            "hourly": hourly_all,
            "grid_load": self.aggregate_weekly_grid_load(hourly_all),
            "edge_flows": edge_all,
            "batches": pd.DataFrame(batch_rows, columns=BATCH_SUMMARY_COLUMNS),
        }

    @staticmethod
    def _sum_hourly_parts(parts: list[pd.DataFrame]) -> pd.DataFrame:
        if not parts:
            return pd.DataFrame(columns=HOURLY_CHARGE_COLUMNS)
        return (
            pd.concat(parts, ignore_index=True)
            .groupby(["fsa", "day", "hour", "event_type", "patch_type"], as_index=False)[["ev_load_kw", "energy_kwh"]]
            .sum()
        )[HOURLY_CHARGE_COLUMNS]

    @staticmethod
    def _sum_edge_flow_parts(parts: list[pd.DataFrame]) -> pd.DataFrame:
        if not parts:
            return pd.DataFrame(columns=EDGE_FLOW_COLUMNS)
        return (
            pd.concat(parts, ignore_index=True)
            .groupby(["day", "hour", "edge_u", "edge_v", "fsa", "zone_type"], as_index=False)
            .agg(vehicle_count=("vehicle_count", "sum"), ev_count=("ev_count", "sum"), route_km=("route_km", "sum"))
        )[EDGE_FLOW_COLUMNS]

    def _future_needs_until_strong_charger(self, legs: list[dict], person: dict | pd.Series, capacity: float) -> list[float]:
        needs: list[float] = []
        for leg_pos in range(len(legs)):
            need = 0.0
            for j in range(leg_pos, len(legs)):
                leg = legs[j]
                need += float(leg["trip_kwh"])
                if j > leg_pos and self._strong_charger_access(str(leg["origin_activity"]), person):
                    break
            needs.append(min(need + self.config.reserve_soc * capacity, capacity))
        return needs

    def _future_need_until_strong_charger(self, legs: pd.DataFrame, leg_pos: int, person: pd.Series, capacity: float) -> float:
        need = 0.0
        for j in range(leg_pos, len(legs)):
            leg = legs.iloc[j]
            need += float(leg["trip_kwh"])
            if j > leg_pos and self._strong_charger_access(str(leg["origin_activity"]), person):
                break
        return min(need + self.config.reserve_soc * capacity, capacity)

    def _strong_charger_access(self, activity: str, person: pd.Series) -> bool:
        if activity == "home":
            return bool(person["has_home_charger"])
        if activity == "work":
            return bool(person["has_work_charger"])
        return False

    def _location_charge_access(self, activity: str, person: pd.Series) -> float:
        if activity == "home":
            return 1.0 if bool(person["has_home_charger"]) else self.config.home_public_charger_access
        if activity == "work":
            return 1.0 if bool(person["has_work_charger"]) else self.config.work_public_charger_access
        return {
            "school": 0.35,
            "retail": self.config.retail_public_charger_access,
            "leisure": 0.55,
            "transit_hub": 0.80,
            "other": 0.45,
        }.get(activity, 0.45)

    def _charger_for_activity(self, leg: pd.Series, activity: str, person: pd.Series) -> ChargerChoice:
        if activity == "home" and not bool(person["has_home_charger"]):
            return self.charger_catalog.nearest_public_to_fsa(int(leg["origin_idx"]))
        if activity == "work" and not bool(person["has_work_charger"]):
            return self.charger_catalog.nearest_public_to_fsa(int(leg["origin_idx"]))
        return self.charger_catalog.choose_activity_charger(int(leg["origin_idx"]), activity)

    def _public_charger_for_leg(self, leg: pd.Series) -> ChargerChoice:
        origin_idx = int(leg["origin_idx"])
        dest_idx = int(leg["dest_idx"])
        route = self.road_network.route(origin_idx, dest_idx)
        route_fsas = self.road_network.route_fsa_indices(route)
        return self.charger_catalog.nearest_public_to_route(route_fsas, origin_idx, dest_idx)

    def _preferred_target_soc(self, rng: np.random.Generator, activity: str) -> float:
        target_shift = float(self.config.target_soc) - 0.80
        if activity == "home":
            return float(np.clip(rng.beta(10.0, 2.0) + target_shift, 0.60, 1.00))
        if activity in {"work", "school"}:
            return float(np.clip(rng.beta(8.0, 2.5) + target_shift, 0.55, 0.95))
        return float(np.clip(rng.beta(6.0, 2.5) + target_shift, 0.45, 0.85))

    def _maybe_normal_charge(
        self,
        rng: np.random.Generator,
        leg: pd.Series,
        person: pd.Series,
        soc_kwh: float,
        future_need_kwh: float,
        capacity: float,
    ) -> dict | None:
        activity = str(leg["origin_activity"])
        dwell_h = float(leg["dwell_before_h"])
        if dwell_h < 0.20:
            return None
        soc = soc_kwh / capacity
        access = self._location_charge_access(activity, person)
        soc_gap = max(future_need_kwh - soc_kwh, 0.0) / capacity
        low_soc_pressure = np.clip((0.35 - soc) / 0.35, 0.0, 1.0)
        dwell_sufficiency = np.clip(dwell_h / 4.0, 0.0, 1.0)
        habit_bias = 0.15 if activity == "home" and bool(person["has_home_charger"]) else 0.0
        logit = (
            NORMAL_CHARGE_BASE_BY_LOCATION.get(activity, -1.0)
            + 3.0 * soc_gap
            + 2.0 * low_soc_pressure
            + 1.0 * dwell_sufficiency
            + habit_bias
        )
        probability = float(np.clip(_sigmoid(np.array([logit]))[0] * access, 0.0, 1.0))
        if rng.random() >= probability:
            return None

        charger = self._charger_for_activity(leg, activity, person)
        required_soc = np.clip(future_need_kwh / capacity, 0.0, 1.0)
        target_soc = min(1.0, max(required_soc, self._preferred_target_soc(rng, activity)))
        return self._make_charge_event(
            person_id=str(leg["person_id"]),
            leg=leg,
            charger=charger,
            start_hour_abs=max(0.0, float(leg.get("dwell_start_abs", float(leg["depart_hour_abs"]) - dwell_h))),
            max_duration_h=dwell_h,
            target_soc=target_soc,
            event_type="normal",
            patch_type="none",
            inconvenience_minutes=0.0,
            detour_km=charger.detour_km,
        )

    def _choose_patch_charge(
        self,
        rng: np.random.Generator,
        leg: pd.Series,
        person: pd.Series,
        soc_kwh: float,
        required_kwh: float,
        capacity: float,
    ) -> dict:
        activity = str(leg["origin_activity"])
        gap_kwh = max(required_kwh - soc_kwh, 0.0)
        candidates = []
        origin_start_abs = max(float(leg["dwell_start_abs"]), float(leg.get("charge_blocked_until_abs", leg["dwell_start_abs"])))
        origin_dwell_h = max(0.0, float(leg["depart_hour_abs"]) - origin_start_abs)

        origin_access = self._location_charge_access(activity, person)
        if origin_access > 0:
            label = "previous_home" if activity == "home" else "previous_work" if activity == "work" else "current_origin"
            charger = self._charger_for_activity(leg, activity, person)
            candidates.append(self._patch_candidate(label, leg, gap_kwh, origin_dwell_h, charger, charger.detour_km, 0.0, origin_access))

        route_charger = self._public_charger_for_leg(leg)
        origin_public = self.charger_catalog.nearest_public_to_fsa(int(leg["origin_idx"]))
        candidates.append(self._patch_candidate("near_route_public", leg, gap_kwh, 0.0, route_charger, max(1.0, route_charger.detour_km), 8.0, 0.75))
        candidates.append(self._patch_candidate("forced_origin_public", leg, gap_kwh, 0.0, origin_public, max(1.0, origin_public.detour_km), 10.0, 1.0))

        utilities = np.array([c["utility"] for c in candidates], dtype=float)
        probs = np.exp((utilities - utilities.max()) / self.config.patch_softmax_temperature)
        probs = probs / probs.sum()
        chosen = candidates[int(rng.choice(np.arange(len(candidates)), p=probs))]

        target_soc = min(1.0, required_kwh / capacity)
        start_hour_abs = origin_start_abs if chosen["patch_type"] in {"previous_home", "previous_work", "current_origin"} else float(leg["depart_hour_abs"])
        max_duration_h = min(chosen["max_duration_h"], max(0.01, 168.0 - start_hour_abs))
        return self._make_charge_event(
            person_id=str(leg["person_id"]),
            leg=leg,
            charger=chosen["charger"],
            start_hour_abs=start_hour_abs,
            max_duration_h=max_duration_h,
            target_soc=target_soc,
            event_type="patch",
            patch_type=chosen["patch_type"],
            inconvenience_minutes=chosen["inconvenience_minutes"],
            detour_km=chosen["detour_km"],
        )

    @staticmethod
    def _patch_candidate(
        patch_type: str,
        leg: pd.Series,
        gap_kwh: float,
        dwell_h: float,
        charger: ChargerChoice,
        detour_km: float,
        wait_minutes: float,
        access: float,
    ) -> dict:
        charger_kw = charger.charger_kw
        available_kwh = max(0.0, dwell_h * charger_kw)
        dwell_fit = min(available_kwh / max(gap_kwh, 0.1), 1.0) * 2.0
        charger_power_score = np.log1p(charger_kw) / np.log1p(150.0)
        charge_h = max(gap_kwh / max(charger_kw, 1.0), 0.0)
        delay_h = max(charge_h - max(dwell_h, 0.0), 0.0)
        extra_minutes = wait_minutes + delay_h * 60.0
        utility = (
            PATCH_BASE_UTILITY[patch_type]
            + charger_power_score
            + dwell_fit
            - 0.25 * detour_km
            - 0.03 * wait_minutes
            - 0.05 * extra_minutes
            + np.log(max(access, 0.01))
        )
        return {
            "patch_type": patch_type,
            "utility": float(utility),
            "max_duration_h": max(0.01, gap_kwh / max(charger_kw, 1.0)),
            "charger": charger,
            "detour_km": detour_km,
            "inconvenience_minutes": extra_minutes + detour_km * 2.0,
        }

    @staticmethod
    def _make_charge_event(
        person_id: str,
        leg: pd.Series,
        charger: ChargerChoice,
        start_hour_abs: float,
        max_duration_h: float,
        target_soc: float,
        event_type: str,
        patch_type: str,
        inconvenience_minutes: float,
        detour_km: float,
    ) -> dict:
        origin_idx = leg.get("origin_idx", -1)
        dest_idx = leg.get("dest_idx", -1)
        return {
            "person_id": person_id,
            "origin_fsa": str(leg.get("origin_fsa", "")),
            "origin_zone_type": str(leg.get("origin_zone_type", "")),
            "origin_activity": str(leg.get("origin_activity", "")),
            "origin_idx": -1 if pd.isna(origin_idx) else int(origin_idx),
            "dest_fsa": str(leg.get("dest_fsa", "")),
            "dest_zone_type": str(leg.get("dest_zone_type", "")),
            "dest_type": str(leg.get("dest_type", "")),
            "dest_idx": -1 if pd.isna(dest_idx) else int(dest_idx),
            "charger_id": charger.charger_id,
            "fsa": charger.fsa,
            "zone_type": charger.zone_type,
            "charger_lat": charger.lat,
            "charger_lon": charger.lon,
            "charger_source": charger.source,
            "road_node_id": charger.road_node_id,
            "road_snap_distance_m": charger.road_snap_distance_m,
            "start_hour_abs": start_hour_abs,
            "max_duration_h": max_duration_h,
            "charger_kw": charger.charger_kw,
            "target_soc": target_soc,
            "event_type": event_type,
            "patch_type": patch_type,
            "inconvenience_minutes": inconvenience_minutes,
            "detour_km": detour_km,
        }

    @staticmethod
    def _apply_charge_event(soc_kwh: float, event: dict, capacity: float) -> tuple[float, dict]:
        target_kwh = min(capacity, event["target_soc"] * capacity)
        needed_kwh = max(target_kwh - soc_kwh, 0.0)
        delivered_kwh = min(needed_kwh, event["max_duration_h"] * event["charger_kw"])
        duration_h = delivered_kwh / event["charger_kw"] if event["charger_kw"] > 0 else 0.0
        soc_after = min(capacity, soc_kwh + delivered_kwh)
        event = dict(event)
        event.update({
            "duration_h": duration_h,
            "end_hour_abs": event["start_hour_abs"] + duration_h,
            "energy_delivered_kwh": delivered_kwh,
            "soc_after_charge": soc_after / capacity,
        })
        return soc_after, event

    def aggregate_charge_events(self, charge_events: pd.DataFrame) -> pd.DataFrame:
        """Aggregate normal and patch charge events into hourly FSA load."""
        if charge_events.empty:
            return pd.DataFrame(columns=HOURLY_CHARGE_COLUMNS)

        records = []
        for _, event in charge_events[charge_events["duration_h"] > 0].iterrows():
            start = float(event["start_hour_abs"])
            end = float(event["end_hour_abs"])
            for h in range(max(0, int(start)), min(167, int(np.ceil(end))) + 1):
                active_fraction = max(0.0, min(end, h + 1.0) - max(start, h))
                if active_fraction <= 0:
                    continue
                energy_kwh = event["charger_kw"] * active_fraction
                records.append({
                    "fsa": event["fsa"],
                    "day": h // 24,
                    "hour": h % 24,
                    "ev_load_kw": energy_kwh,
                    "energy_kwh": energy_kwh,
                    "event_type": event["event_type"],
                    "patch_type": event["patch_type"],
                })
        if not records:
            return pd.DataFrame(columns=HOURLY_CHARGE_COLUMNS)
        return (
            pd.DataFrame(records)
            .groupby(["fsa", "day", "hour", "event_type", "patch_type"], as_index=False)[["ev_load_kw", "energy_kwh"]]
            .sum()
        )[HOURLY_CHARGE_COLUMNS]

    def aggregate_weekly_grid_load(self, hourly_charge_events: pd.DataFrame) -> pd.DataFrame:
        """
        Combine baseline grid load and simulated EV charging by FSA/day/hour.

        `proxy_capacity_kw` is treated as available service capacity, not as
        pre-EV baseline load. Baseline load peaks at
        `config.baseline_peak_utilization * proxy_capacity_kw`, so the no-EV
        baseline does not overload by construction.
        """
        base = self.base_gdf[["fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"]].copy()
        day_hour = pd.MultiIndex.from_product([range(7), range(24)], names=["day", "hour"]).to_frame(index=False)
        grid = base.merge(day_hour, how="cross")
        baseline_fraction = _baseline_load_fraction_by_hour()
        grid["baseline_load_kw"] = (
            grid["hour"].map(baseline_fraction).fillna(0.85)
            * self.config.baseline_peak_utilization
            * grid["proxy_capacity_kw"]
        )

        if hourly_charge_events.empty:
            ev = pd.DataFrame(columns=["fsa", "day", "hour", "ev_load_kw"])
        else:
            ev = hourly_charge_events.groupby(["fsa", "day", "hour"], as_index=False)["ev_load_kw"].sum()
        grid = grid.merge(ev, on=["fsa", "day", "hour"], how="left")
        grid["ev_load_kw"] = grid["ev_load_kw"].fillna(0.0) * self.config.grid_ev_load_scale
        grid["total_load_kw"] = grid["baseline_load_kw"] + grid["ev_load_kw"]
        grid["headroom_kw"] = grid["proxy_capacity_kw"] - grid["baseline_load_kw"]
        grid["overloaded"] = grid["total_load_kw"] > grid["proxy_capacity_kw"]
        grid["deficit_kw"] = (grid["total_load_kw"] - grid["proxy_capacity_kw"]).clip(lower=0.0)
        return grid[GRID_LOAD_COLUMNS]

    def aggregate_edge_flows(self, legs: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate simulated trips onto graph edges by day/hour.

        For the real OSM graph, `edge_u`/`edge_v` are OSM node IDs from the
        routed path. For the offline FSA graph, they are FSA node indices. This
        gives the road-grid flow layer needed to inspect where simulated traffic
        concentrates.
        """
        if legs.empty:
            return pd.DataFrame(columns=EDGE_FLOW_COLUMNS)

        days: list[int] = []
        hours: list[int] = []
        edge_u: list[int] = []
        edge_v: list[int] = []
        fsa_values: list[str] = []
        zone_values: list[str] = []
        vehicle_counts: list[float] = []
        ev_counts: list[float] = []
        route_km_values: list[float] = []
        fsas = self.fsas
        zone_types = self.zone_types
        has_od = {"origin_idx", "dest_idx"}.issubset(legs.columns)

        for leg in legs.itertuples(index=False):
            origin_idx = int(getattr(leg, "origin_idx", -1)) if has_od else -1
            dest_idx = int(getattr(leg, "dest_idx", -1)) if has_od else -1
            if 0 <= origin_idx < len(self.fsas) and 0 <= dest_idx < len(self.fsas):
                template = self.road_network.route_edge_template(origin_idx, dest_idx)
                segments = template.segments
                fsa_indices = template.fsa_indices
                segment_time_h = template.freeflow_time_h
                segment_distance_km = template.distance_km
            else:
                path_raw = str(getattr(leg, "route_path", ""))
                if not path_raw:
                    continue
                path = [int(node) for node in path_raw.split("|") if node != ""]
                if len(path) < 2:
                    continue
                segments = tuple(self.road_network.route_edge_segments(path))
                fsa_indices = tuple(self.road_network.edge_fsa_idx(segment.u, segment.v) for segment in segments)
                segment_time_h = sum(segment.freeflow_time_h for segment in segments)
                segment_distance_km = sum(segment.distance_km for segment in segments)
            if not segments:
                continue

            depart_abs = float(getattr(leg, "depart_hour_abs"))
            travel_time_h = max(float(getattr(leg, "travel_time_h", 0.0)), 0.001)
            route_km = max(float(getattr(leg, "route_km", 0.0)), 0.0)
            is_ev = bool(getattr(leg, "is_ev", False))
            time_scale = travel_time_h / max(segment_time_h, 0.001)
            distance_scale = route_km / max(segment_distance_km, 0.001)
            cursor_abs = depart_abs

            for segment, fsa_idx in zip(segments, fsa_indices):
                duration_h = max(segment.freeflow_time_h * time_scale, 1e-6)
                distance_km = max(segment.distance_km * distance_scale, 0.0)
                start_abs = cursor_abs
                end_abs = min(168.0, cursor_abs + duration_h)
                cursor_abs += duration_h
                if end_abs <= start_abs:
                    continue

                first_hour = max(0, int(np.floor(start_abs)))
                last_hour = min(167, int(np.ceil(end_abs)))
                for hour_abs in range(first_hour, last_hour + 1):
                    overlap_h = max(0.0, min(end_abs, hour_abs + 1.0) - max(start_abs, float(hour_abs)))
                    if overlap_h <= 0:
                        continue
                    fraction = overlap_h / duration_h
                    days.append(int(hour_abs // 24))
                    hours.append(int(hour_abs % 24))
                    edge_u.append(segment.u)
                    edge_v.append(segment.v)
                    fsa_values.append(fsas[fsa_idx])
                    zone_values.append(zone_types[fsa_idx])
                    vehicle_counts.append(fraction)
                    ev_counts.append(fraction if is_ev else 0.0)
                    route_km_values.append(distance_km * fraction)
        self.road_network.persist_edge_template_cache()
        if not days:
            return pd.DataFrame(columns=EDGE_FLOW_COLUMNS)
        return (
            pd.DataFrame({
                "day": days,
                "hour": hours,
                "edge_u": edge_u,
                "edge_v": edge_v,
                "fsa": fsa_values,
                "zone_type": zone_values,
                "vehicle_count": vehicle_counts,
                "ev_count": ev_counts,
                "route_km": route_km_values,
            })
            .groupby(["day", "hour", "edge_u", "edge_v", "fsa", "zone_type"], as_index=False)
            .agg(vehicle_count=("vehicle_count", "sum"), ev_count=("ev_count", "sum"), route_km=("route_km", "sum"))
        )[EDGE_FLOW_COLUMNS]

    def aggregate_fsa_corridor_flows(self, legs: pd.DataFrame) -> pd.DataFrame:
        """
        Fast calibration proxy for road-flow geography.

        This keeps real OSM route distances/times from the cached route matrix,
        but aggregates each trip onto the FSA corridor touched by the route
        instead of expanding through every OSM edge. Use full edge expansion for
        final validation and visualization.
        """
        if legs.empty:
            return pd.DataFrame(columns=EDGE_FLOW_COLUMNS)

        days: list[int] = []
        hours: list[int] = []
        edge_u: list[int] = []
        edge_v: list[int] = []
        fsa_values: list[str] = []
        zone_values: list[str] = []
        vehicle_counts: list[float] = []
        ev_counts: list[float] = []
        route_km_values: list[float] = []
        fsas = self.fsas
        zone_types = self.zone_types

        for leg in legs.itertuples(index=False):
            path_raw = str(getattr(leg, "route_path", ""))
            if "|" not in path_raw:
                continue
            origin_idx = int(getattr(leg, "origin_idx", -1))
            dest_idx = int(getattr(leg, "dest_idx", -1))
            if origin_idx < 0 or dest_idx < 0:
                continue
            route = self.road_network.route(origin_idx, dest_idx)
            route_fsas = self.road_network.route_fsa_indices(route)
            if not route_fsas:
                continue

            depart_abs = float(getattr(leg, "depart_hour_abs"))
            travel_time_h = max(float(getattr(leg, "travel_time_h", route.freeflow_time_h)), 0.001)
            route_km = max(float(getattr(leg, "route_km", route.distance_km)), 0.0)
            is_ev = bool(getattr(leg, "is_ev", False))
            end_abs = min(168.0, depart_abs + travel_time_h)
            if depart_abs >= 168.0 or end_abs <= depart_abs:
                continue
            effective_travel_time_h = max(end_abs - depart_abs, 0.001)
            first_hour = max(0, int(np.floor(depart_abs)))
            last_hour = min(167, int(np.ceil(end_abs)))
            path_share = 1.0 / max(len(route_fsas), 1)

            for hour_abs in range(first_hour, last_hour + 1):
                overlap_h = max(0.0, min(end_abs, hour_abs + 1.0) - max(depart_abs, float(hour_abs)))
                if overlap_h <= 0:
                    continue
                time_share = overlap_h / effective_travel_time_h
                for pos, fsa_idx in enumerate(route_fsas):
                    fraction = time_share * path_share
                    days.append(int(hour_abs // 24))
                    hours.append(int(hour_abs % 24))
                    edge_u.append(int(fsa_idx))
                    edge_v.append(int(route_fsas[min(pos + 1, len(route_fsas) - 1)]))
                    fsa_values.append(fsas[int(fsa_idx)])
                    zone_values.append(zone_types[int(fsa_idx)])
                    vehicle_counts.append(fraction)
                    ev_counts.append(fraction if is_ev else 0.0)
                    route_km_values.append(route_km * fraction)
        if not days:
            return pd.DataFrame(columns=EDGE_FLOW_COLUMNS)
        return (
            pd.DataFrame({
                "day": days,
                "hour": hours,
                "edge_u": edge_u,
                "edge_v": edge_v,
                "fsa": fsa_values,
                "zone_type": zone_values,
                "vehicle_count": vehicle_counts,
                "ev_count": ev_counts,
                "route_km": route_km_values,
            })
            .groupby(["day", "hour", "edge_u", "edge_v", "fsa", "zone_type"], as_index=False)
            .agg(vehicle_count=("vehicle_count", "sum"), ev_count=("ev_count", "sum"), route_km=("route_km", "sum"))
        )[EDGE_FLOW_COLUMNS]

    @staticmethod
    def _sample_dwell_hours(rng: np.random.Generator, dest_type: np.ndarray, size: int) -> np.ndarray:
        means = {
            "work": 7.5,
            "school": 6.0,
            "retail": 1.3,
            "leisure": 2.7,
            "home": 10.0,
            "transit_hub": 0.5,
            "other": 1.6,
        }
        dwell = np.empty(size)
        for value in np.unique(dest_type):
            mask = dest_type == value
            mean = means.get(str(value), 1.6)
            dwell[mask] = rng.gamma(shape=2.0, scale=mean / 2.0, size=mask.sum())
        return np.clip(dwell, 0.15, 14.0)

    def aggregate_charging_load(self, agents_df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate charging EVs into per-FSA load and grid stress results."""
        charging = agents_df[(agents_df["is_ev"]) & (agents_df["will_charge"]) & (agents_df["charge_duration_h"] > 0)].copy()
        if charging.empty:
            return pd.DataFrame(columns=[
                "fsa", "zone_type", "proxy_capacity_kw", "peak_hour",
                "peak_ev_load_kw", "baseline_load_kw", "total_load_kw",
                "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
            ])

        arrivals = charging["arrival_hour_float"].to_numpy()
        departures = arrivals + charging["charge_duration_h"].to_numpy()
        fsas = charging["dest_fsa"].to_numpy()
        charger_kws = charging["charger_kw"].to_numpy()

        records = []
        for h in range(max(0, int(arrivals.min())), min(23, int(np.ceil(departures.max()))) + 1):
            active = (arrivals < h + 1) & (departures > h)
            if not active.any():
                continue
            active_fsas = fsas[active]
            active_kws = charger_kws[active]
            for fsa in np.unique(active_fsas):
                records.append({
                    "fsa": fsa,
                    "hour": h,
                    "ev_load_kw": active_kws[active_fsas == fsa].sum(),
                })

        hourly = pd.DataFrame(records)
        idx_peak = hourly.groupby("fsa")["ev_load_kw"].idxmax()
        peak = hourly.loc[idx_peak].rename(columns={"hour": "peak_hour", "ev_load_kw": "peak_ev_load_kw"})

        result = peak.merge(
            self.base_gdf[["fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"]],
            on="fsa",
            how="left",
        )

        # Reuse the same synthetic IESO shape as the current engine without importing private helpers.
        baseline_fraction = _baseline_load_fraction_by_hour()
        result["baseline_load_kw"] = (
            result["peak_hour"].map(baseline_fraction).fillna(0.85)
            * self.config.baseline_peak_utilization
            * result["proxy_capacity_kw"]
        )
        result["baseline_load_kw"] = result["baseline_load_kw"].round(1)
        result["peak_ev_load_kw"] = result["peak_ev_load_kw"].round(1)
        result["total_load_kw"] = (result["peak_ev_load_kw"] + result["baseline_load_kw"]).round(1)
        result["overloaded"] = result["total_load_kw"] > result["proxy_capacity_kw"]
        result["deficit_kw"] = (result["total_load_kw"] - result["proxy_capacity_kw"]).clip(lower=0).round(1)

        return result[[
            "fsa", "zone_type", "proxy_capacity_kw", "peak_hour",
            "peak_ev_load_kw", "baseline_load_kw", "total_load_kw",
            "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
        ]].sort_values("deficit_kw", ascending=False).reset_index(drop=True)


def _baseline_load_fraction_by_hour() -> dict[int, float]:
    profile_path = Path(__file__).resolve().parent / "data" / "ieso_load_profile.csv"
    if profile_path.exists():
        profile = pd.read_csv(profile_path)
        if {"hour", "load_fraction"}.issubset(profile.columns):
            return {int(row.hour): float(row.load_fraction) for row in profile.itertuples(index=False)}
    return {
        0: 0.55, 1: 0.50, 2: 0.47, 3: 0.45, 4: 0.45, 5: 0.48,
        6: 0.55, 7: 0.65, 8: 0.75, 9: 0.82, 10: 0.85, 11: 0.87,
        12: 0.88, 13: 0.87, 14: 0.86, 15: 0.85, 16: 0.88, 17: 0.95,
        18: 1.00, 19: 0.97, 20: 0.90, 21: 0.82, 22: 0.72, 23: 0.62,
    }


if __name__ == "__main__":
    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.20))
    people_df, itinerary_df = engine.generate_weekly_itinerary(num_people=2_500, seed=42)
    legs_df, charges_df = engine.simulate_weekly_charging(people_df, itinerary_df, seed=43)
    hourly_df = engine.aggregate_charge_events(charges_df)

    ev_people = people_df[people_df["is_ev"]]
    ev_legs = legs_df[legs_df["is_ev"]]
    patch_events = charges_df[charges_df["event_type"] == "patch"]

    print("WEEKLY MOBILITY / CHARGING SIMULATION")
    print(f"people: {len(people_df):,}")
    print(f"evs: {len(ev_people):,}")
    print(f"trip legs: {len(itinerary_df):,}")
    print(f"charging events: {len(charges_df):,}")
    print(f"patch events: {len(patch_events):,}")
    print("\ndestination mix:")
    print((itinerary_df["dest_type"].value_counts(normalize=True) * 100).round(1).to_string())
    print("\nweekly km per EV:")
    print(ev_legs.groupby("person_id")["route_km"].sum().describe(percentiles=[0.25, 0.5, 0.75, 0.9]).round(2).to_string())
    print("\ncharge event types:")
    print(charges_df["event_type"].value_counts().to_string())
    print("\npatch types:")
    print(patch_events["patch_type"].value_counts().to_string())
    print("\npeak hourly average load:")
    print(round(hourly_df.groupby(["day", "hour"])["ev_load_kw"].sum().max(), 1) if not hourly_df.empty else 0.0)
