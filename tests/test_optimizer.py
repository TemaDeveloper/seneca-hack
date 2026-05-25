import pytest
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _get_grid_df():
    """Helper: run a small simulation and return grid results."""
    from monte_carlo import SimulationEngine
    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=5000, time_of_day="Full Day")
    return engine.aggregate_grid_load(ev_df)


def test_optimize_placement_returns_expected_columns():
    """Optimizer output must have all prescription columns."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    result = optimize_placement(grid_df, max_stations=5)
    required = {"fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon",
                "charger_type", "charger_units", "charger_kw_per_unit", "total_charger_kw", "bess_kwh"}
    assert required.issubset(set(result.columns)), f"Missing: {required - set(result.columns)}"


def test_optimize_placement_respects_budget():
    """Number of selected stations must not exceed max_stations."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    result = optimize_placement(grid_df, max_stations=5)
    assert len(result) <= 5


def test_optimize_placement_selects_only_overloaded():
    """All selected sites must come from overloaded FSAs."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    overloaded_fsas = set(grid_df[grid_df["overloaded"]]["fsa"])
    result = optimize_placement(grid_df, max_stations=10)
    for fsa in result["fsa"]:
        assert fsa in overloaded_fsas, f"Selected non-overloaded FSA: {fsa}"


def test_optimize_placement_bess_sizing():
    """BESS must be deficit_kw * 2 hours."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    result = optimize_placement(grid_df, max_stations=5)
    for _, row in result.iterrows():
        expected_bess = int(row["deficit_kw"] * 2)
        assert row["bess_kwh"] == expected_bess, f"FSA {row['fsa']}: expected BESS {expected_bess}, got {row['bess_kwh']}"


def test_optimize_placement_empty_when_no_overload():
    """If no FSAs are overloaded, return empty DataFrame."""
    from optimizer import optimize_placement
    # Create a fake grid_df with no overloaded zones
    fake_df = pd.DataFrame({
        "fsa": ["X1A", "X1B"],
        "zone_type": ["residential", "residential"],
        "proxy_capacity_kw": [300, 300],
        "peak_hour": [18, 18],
        "peak_ev_load_kw": [10.0, 10.0],
        "baseline_load_kw": [100.0, 100.0],
        "total_load_kw": [110.0, 110.0],
        "overloaded": [False, False],
        "deficit_kw": [0.0, 0.0],
        "centroid_lat": [43.65, 43.70],
        "centroid_lon": [-79.38, -79.40],
    })
    result = optimize_placement(fake_df, max_stations=5)
    assert len(result) == 0


def test_optimize_placement_chargers_cover_deficit():
    """Total charger kW must be >= deficit_kw for each site."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    result = optimize_placement(grid_df, max_stations=10)
    for _, row in result.iterrows():
        assert row["total_charger_kw"] >= row["deficit_kw"], (
            f"FSA {row['fsa']}: {row['total_charger_kw']} kW chargers < {row['deficit_kw']} kW deficit"
        )


def test_optimize_placement_caps_budget_at_candidates():
    """If max_stations > overloaded count, should not crash."""
    from optimizer import optimize_placement
    grid_df = _get_grid_df()
    overloaded_count = grid_df["overloaded"].sum()
    result = optimize_placement(grid_df, max_stations=9999)
    assert len(result) <= overloaded_count
