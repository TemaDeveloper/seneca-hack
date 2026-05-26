import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_temperature_efficiency_penalty_is_monotone():
    from road_grid_dashboard import efficiency_for_temperature

    warm = efficiency_for_temperature(20.0)
    cold = efficiency_for_temperature(-10.0)

    assert np.isclose(warm, 0.18)
    assert cold > warm


def test_peak_grid_summary_preserves_one_row_per_fsa():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from road_grid_dashboard import summarize_peak_grid_by_fsa

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.8, road_graph_source="fsa_adjacency"))
    people, itinerary = engine.generate_weekly_itinerary(num_people=300, seed=811)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=812)
    hourly = engine.aggregate_charge_events(charges)
    grid = engine.aggregate_weekly_grid_load(hourly)
    peak = summarize_peak_grid_by_fsa(grid, time_window="Morning")

    assert len(peak) == engine.base_gdf["fsa"].nunique()
    assert peak["fsa"].is_unique
    assert peak["peak_day"].between(0, 4).all()
    assert peak["peak_hour"].between(6, 10).all()
    assert {"peak_ev_load_kw", "baseline_load_kw", "total_load_kw", "deficit_kw"}.issubset(peak.columns)


def test_weekly_dashboard_adapter_uses_offline_grid_end_to_end():
    from road_grid_dashboard import run_weekly_road_grid_simulation

    result = run_weekly_road_grid_simulation(
        num_people=250,
        ev_probability=0.7,
        temperature_celsius=0.0,
        grid_ev_load_scale=2.0,
        time_window="Evening",
        seed=821,
        require_real_grid=False,
    )

    assert result.engine.road_network.source in {"osm", "fsa_adjacency"}
    assert not result.itinerary.empty
    assert not result.legs.empty
    assert not result.charges.empty
    assert not result.edge_flows.empty
    assert len(result.peak_grid) == result.engine.base_gdf["fsa"].nunique()
    assert result.peak_grid["peak_hour"].between(15, 21).all()


def test_weekly_dashboard_adapter_scales_ev_load_not_baseline():
    from road_grid_dashboard import run_weekly_road_grid_simulation

    base = run_weekly_road_grid_simulation(
        num_people=220,
        ev_probability=0.8,
        temperature_celsius=20.0,
        grid_ev_load_scale=1.0,
        time_window="Full Week",
        seed=825,
        require_real_grid=False,
    )
    scaled = run_weekly_road_grid_simulation(
        num_people=220,
        ev_probability=0.8,
        temperature_celsius=20.0,
        grid_ev_load_scale=3.5,
        time_window="Full Week",
        seed=825,
        require_real_grid=False,
    )

    assert np.isclose(scaled.grid_load["ev_load_kw"].sum(), base.grid_load["ev_load_kw"].sum() * 3.5)
    assert np.isclose(scaled.grid_load["baseline_load_kw"].sum(), base.grid_load["baseline_load_kw"].sum())
    assert scaled.grid_load["deficit_kw"].sum() >= base.grid_load["deficit_kw"].sum()


def test_weekly_dashboard_adapter_maps_hackathon_data_to_real_osm_grid():
    graph_path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml")
    charger_path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "afdc_on_ev_chargers.csv")
    if not os.path.exists(graph_path) or not os.path.exists(charger_path):
        import pytest

        pytest.skip("real OSM graph or AFDC charger cache missing")

    from road_grid_dashboard import run_weekly_road_grid_simulation

    result = run_weekly_road_grid_simulation(
        num_people=180,
        ev_probability=1.0,
        temperature_celsius=-5.0,
        grid_ev_load_scale=1.0,
        time_window="Full Week",
        seed=831,
        require_real_grid=True,
    )
    graph_nodes = set(result.engine.road_network.graph.nodes)
    sampled_edges = set(result.edge_flows["edge_u"].head(300).astype(int)) | set(result.edge_flows["edge_v"].head(300).astype(int))

    assert result.engine.road_network.source == "osm"
    assert result.engine.road_network.summary().unreachable_od_pairs == 0
    assert set(result.peak_grid["fsa"]) == set(result.engine.base_gdf["fsa"])
    assert sampled_edges.issubset(graph_nodes)
    assert len(result.engine.charger_catalog.public) >= 1_000
    assert result.engine.charger_catalog.public["road_node_id"].notna().all()
    assert result.charges["road_node_id"].notna().all()
    assert result.grid_load[["fsa", "day", "hour"]].drop_duplicates().shape[0] == len(result.engine.base_gdf) * 168


def test_weekly_dashboard_adapter_uses_batched_fsa_flow_for_large_runs():
    from road_grid_dashboard import run_weekly_road_grid_simulation

    result = run_weekly_road_grid_simulation(
        num_people=700,
        ev_probability=0.6,
        temperature_celsius=10.0,
        grid_ev_load_scale=2.0,
        time_window="Full Week",
        seed=841,
        require_real_grid=False,
        batch_size=250,
        edge_flow_detail="fsa",
    )

    assert result.is_batched
    assert result.edge_flow_detail == "fsa"
    assert result.people.empty
    assert result.legs.empty
    assert len(result.batch_summary) == 3
    assert int(result.batch_summary["people"].sum()) == 700
    assert result.trip_leg_count == int(result.batch_summary["leg_rows"].sum())
    assert result.charge_event_count == int(result.batch_summary["charge_rows"].sum())
    assert not result.grid_load.empty
    assert not result.edge_flows.empty
    assert len(result.peak_grid) == result.engine.base_gdf["fsa"].nunique()
