"""
Dashboard/runtime adapter for the weekly road-grid mobility model.

This module keeps Streamlit and ad-hoc scripts on the same path as the
validated model: hackathon FSA polygons/capacity/population, OSM road routes,
AFDC public chargers, weekly itinerary generation, SoC decisions, hourly load,
and edge-flow aggregation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from mobility_simulator import MobilityConfig, MobilitySimulationEngine


TimeWindow = Literal["Full Week", "Full Day", "Morning", "Evening", "Weekend", "Weekday AM", "Weekday PM"]

BASE_EFFICIENCY_KWH_PER_KM = 0.18
OPTIMAL_TEMP_C = 20.0
TEMP_EFFICIENCY_LOSS_PER_DEGREE = 0.0085
MAX_EFFICIENCY_LOSS = 0.40


@dataclass(frozen=True)
class RoadGridSimulationResult:
    engine: MobilitySimulationEngine
    people: pd.DataFrame
    itinerary: pd.DataFrame
    legs: pd.DataFrame
    charges: pd.DataFrame
    hourly: pd.DataFrame
    grid_load: pd.DataFrame
    edge_flows: pd.DataFrame
    peak_grid: pd.DataFrame


def efficiency_for_temperature(temperature_celsius: float, *, base_efficiency: float = BASE_EFFICIENCY_KWH_PER_KM) -> float:
    """Convert ambient temperature into kWh/km using the existing winter penalty."""
    if temperature_celsius >= OPTIMAL_TEMP_C:
        return base_efficiency
    temp_diff = OPTIMAL_TEMP_C - float(temperature_celsius)
    loss = min(MAX_EFFICIENCY_LOSS, temp_diff * TEMP_EFFICIENCY_LOSS_PER_DEGREE)
    return base_efficiency / max(1.0 - loss, 0.01)


def run_weekly_road_grid_simulation(
    *,
    num_people: int,
    ev_probability: float,
    temperature_celsius: float = 20.0,
    grid_ev_load_scale: float = 1.0,
    time_window: TimeWindow | str = "Full Week",
    seed: int = 42,
    require_real_grid: bool = True,
) -> RoadGridSimulationResult:
    """
    Run the end-to-end weekly road-grid model and return dashboard artifacts.

    `num_people` is the sampled driver/person count. `grid_ev_load_scale`
    expands sampled hourly charging load to the represented population.
    """
    cfg = MobilityConfig(
        ev_probability=float(ev_probability),
        ev_efficiency_kwh_per_km=efficiency_for_temperature(temperature_celsius),
        grid_ev_load_scale=float(grid_ev_load_scale),
        road_graph_source="osm" if require_real_grid else "auto",
        charger_source="afdc" if require_real_grid else "auto",
    )
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=int(num_people), seed=seed)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=seed + 1)
    hourly = engine.aggregate_charge_events(charges)
    grid_load = engine.aggregate_weekly_grid_load(hourly)
    edge_flows = engine.aggregate_edge_flows(legs)
    peak_grid = summarize_peak_grid_by_fsa(grid_load, time_window=time_window)
    return RoadGridSimulationResult(
        engine=engine,
        people=people,
        itinerary=itinerary,
        legs=legs,
        charges=charges,
        hourly=hourly,
        grid_load=grid_load,
        edge_flows=edge_flows,
        peak_grid=peak_grid,
    )


def summarize_peak_grid_by_fsa(grid_load: pd.DataFrame, *, time_window: TimeWindow | str = "Full Week") -> pd.DataFrame:
    """Collapse FSA/day/hour load into one peak row per FSA for map/optimizer views."""
    if grid_load.empty:
        return pd.DataFrame(columns=[
            "fsa", "zone_type", "proxy_capacity_kw", "peak_day", "peak_hour",
            "peak_ev_load_kw", "baseline_load_kw", "total_load_kw", "headroom_kw",
            "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
        ])

    filtered = _filter_time_window(grid_load, time_window)
    if filtered.empty:
        filtered = grid_load.copy()

    ordered = filtered.sort_values(
        ["fsa", "total_load_kw", "ev_load_kw", "deficit_kw"],
        ascending=[True, False, False, False],
    )
    peak = ordered.groupby("fsa", as_index=False).head(1).copy()
    peak = peak.rename(columns={
        "day": "peak_day",
        "hour": "peak_hour",
        "ev_load_kw": "peak_ev_load_kw",
    })
    return peak[[
        "fsa", "zone_type", "proxy_capacity_kw", "peak_day", "peak_hour",
        "peak_ev_load_kw", "baseline_load_kw", "total_load_kw", "headroom_kw",
        "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
    ]].sort_values("deficit_kw", ascending=False).reset_index(drop=True)


def _filter_time_window(grid_load: pd.DataFrame, time_window: TimeWindow | str) -> pd.DataFrame:
    label = str(time_window).strip().lower()
    if label in {"full week", "full day", "all"}:
        return grid_load.copy()
    if label in {"morning", "weekday am"}:
        return grid_load[(grid_load["day"] < 5) & (grid_load["hour"].between(6, 10))]
    if label in {"evening", "weekday pm"}:
        return grid_load[(grid_load["day"] < 5) & (grid_load["hour"].between(15, 21))]
    if label == "weekend":
        return grid_load[grid_load["day"] >= 5]
    return grid_load.copy()
