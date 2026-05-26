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
    batch_summary: pd.DataFrame
    peak_grid: pd.DataFrame
    edge_flow_detail: str
    is_batched: bool

    @property
    def trip_leg_count(self) -> int:
        return _count_rows(self.legs, self.batch_summary, "leg_rows")

    @property
    def itinerary_row_count(self) -> int:
        return _count_rows(self.itinerary, self.batch_summary, "itinerary_rows")

    @property
    def charge_event_count(self) -> int:
        return _count_rows(self.charges, self.batch_summary, "charge_rows")


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
    batch_size: int | None = None,
    edge_flow_detail: Literal["full", "fsa"] = "full",
) -> RoadGridSimulationResult:
    """
    Run the end-to-end weekly road-grid model and return dashboard artifacts.

    `num_people` is the sampled driver/person count. `grid_ev_load_scale`
    expands sampled hourly charging load to the represented population.
    Supplying `batch_size` enables bounded-memory aggregation for large runs.
    """
    cfg = MobilityConfig(
        ev_probability=float(ev_probability),
        ev_efficiency_kwh_per_km=efficiency_for_temperature(temperature_celsius),
        grid_ev_load_scale=float(grid_ev_load_scale),
        road_graph_source="osm" if require_real_grid else "auto",
        charger_source="afdc" if require_real_grid else "auto",
    )
    engine = MobilitySimulationEngine(cfg)
    use_batched = batch_size is not None and int(num_people) > int(batch_size)
    if use_batched:
        aggregated = engine.run_weekly_batched_aggregation(
            int(num_people),
            seed=seed,
            batch_size=int(batch_size),
            edge_flow_detail=edge_flow_detail,
        )
        people = pd.DataFrame()
        itinerary = pd.DataFrame()
        legs = pd.DataFrame()
        charges = pd.DataFrame()
        hourly = aggregated["hourly"]
        grid_load = aggregated["grid_load"]
        edge_flows = aggregated["edge_flows"]
        batch_summary = aggregated["batches"]
    else:
        people, itinerary = engine.generate_weekly_itinerary(num_people=int(num_people), seed=seed)
        legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=seed + 1)
        hourly = engine.aggregate_charge_events(charges)
        grid_load = engine.aggregate_weekly_grid_load(hourly)
        if edge_flow_detail == "full":
            edge_flows = engine.aggregate_edge_flows(legs)
        elif edge_flow_detail == "fsa":
            edge_flows = engine.aggregate_fsa_corridor_flows(legs)
        else:
            raise ValueError("edge_flow_detail must be 'full' or 'fsa'.")
        batch_summary = pd.DataFrame([{
            "batch": 0,
            "seed": int(seed),
            "people": int(num_people),
            "itinerary_rows": len(itinerary),
            "leg_rows": len(legs),
            "charge_rows": len(charges),
            "hourly_rows": len(hourly),
            "edge_flow_rows": len(edge_flows),
        }])
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
        batch_summary=batch_summary,
        peak_grid=peak_grid,
        edge_flow_detail=edge_flow_detail,
        is_batched=use_batched,
    )


def _count_rows(frame: pd.DataFrame, batch_summary: pd.DataFrame, column: str) -> int:
    if not frame.empty:
        return int(len(frame))
    if not batch_summary.empty and column in batch_summary:
        return int(batch_summary[column].sum())
    return 0


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
