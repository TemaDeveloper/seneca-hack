import pytest
import os
import sys
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_monte_carlo_importable():
    """monte_carlo module must import without crashing."""
    from monte_carlo import SimulationEngine
    assert SimulationEngine is not None


def test_simulation_engine_loads_weights_on_init():
    """Weights should be loaded during __init__, not at module level."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    assert engine.time_weights is not None
    assert "Morning" in engine.time_weights
    assert "Evening" in engine.time_weights


def test_run_simulation_returns_ev_dataframe():
    """run_simulation must return a DataFrame with individual EV records."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=100, time_of_day="Morning")
    assert len(ev_df) == 100
    assert "vehicle_id" in ev_df.columns
    assert "soc_needed_kwh" in ev_df.columns
    assert "arrival_hour_float" in ev_df.columns


def test_aggregate_grid_load_returns_per_fsa_results():
    """aggregate_grid_load must return per-FSA results with overload flags."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=500, time_of_day="Evening")
    grid_df = engine.aggregate_grid_load(ev_df)

    required_cols = {"fsa", "zone_type", "proxy_capacity_kw", "peak_ev_load_kw",
                     "baseline_load_kw", "total_load_kw", "overloaded", "deficit_kw"}
    assert required_cols.issubset(set(grid_df.columns)), f"Missing: {required_cols - set(grid_df.columns)}"


def test_aggregate_grid_load_deficit_is_non_negative():
    """deficit_kw must never be negative."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=500, time_of_day="Morning")
    grid_df = engine.aggregate_grid_load(ev_df)
    assert (grid_df["deficit_kw"] >= 0).all()


def test_aggregate_grid_load_overloaded_flag_consistent():
    """overloaded must be True exactly when total_load_kw > proxy_capacity_kw."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=1000, time_of_day="Evening")
    grid_df = engine.aggregate_grid_load(ev_df)

    expected = grid_df["total_load_kw"] > grid_df["proxy_capacity_kw"]
    pd.testing.assert_series_equal(grid_df["overloaded"].reset_index(drop=True), expected.reset_index(drop=True), check_names=False)


def test_full_day_simulation_covers_both_peaks():
    """Full Day mode must produce arrivals in both midday and evening windows."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=1000, time_of_day="Full Day")
    assert len(ev_df) == 1000

    hours = ev_df["arrival_hour_float"]
    midday_count = ((hours >= 9.0) & (hours <= 15.0)).sum()
    evening_count = ((hours >= 14.0) & (hours <= 21.0)).sum()

    # Both peaks should have substantial arrivals
    assert midday_count > 100, f"Too few midday arrivals: {midday_count}"
    assert evening_count > 400, f"Too few evening arrivals: {evening_count}"
