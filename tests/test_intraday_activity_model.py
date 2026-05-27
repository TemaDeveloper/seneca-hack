import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _intraday_engine(ev_probability=0.4):
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    return MobilitySimulationEngine(MobilityConfig(
        ev_probability=ev_probability,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
        itinerary_model="intraday",
        activity_poi_source="none",
    ))


def test_intraday_itinerary_schema_timing_continuity_and_home_closure():
    from mobility_simulator import ITINERARY_COLUMNS

    engine = _intraday_engine()
    people, itinerary = engine.generate_weekly_itinerary(num_people=250, seed=1101)

    assert len(people) == 250
    assert list(itinerary.columns) == ITINERARY_COLUMNS
    assert not itinerary.empty
    assert itinerary["depart_hour_abs"].between(0, 168).all()
    assert itinerary["arrival_hour_abs"].between(0, 168).all()
    assert (itinerary["arrival_hour_abs"] >= itinerary["depart_hour_abs"]).all()
    assert (itinerary["dwell_before_h"] >= 0).all()
    assert itinerary["reachable_route"].all()
    assert (itinerary["route_km"] > 0).all()

    ordered = itinerary.sort_values(["person_id", "depart_hour_abs"])
    for _, trips in ordered.groupby("person_id"):
        assert (trips["origin_idx"].to_numpy()[1:] == trips["dest_idx"].to_numpy()[:-1]).all()
        assert (trips["depart_hour_abs"].to_numpy()[1:] >= trips["arrival_hour_abs"].to_numpy()[:-1] - 1e-9).all()

    last_by_day = ordered.groupby(["person_id", "day"]).tail(1)
    assert (last_by_day["dest_type"] == "home").mean() >= 0.995


def test_intraday_planner_has_dynamic_stop_count_and_time_windows():
    engine = _intraday_engine()
    people, itinerary = engine.generate_weekly_itinerary(num_people=700, seed=1103)

    stop_counts = itinerary.groupby(["person_id", "day"]).size()
    assert stop_counts.nunique() >= 3

    leg_counts = itinerary.groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    assert 9 <= leg_counts.median() <= 18

    dest_types = set(itinerary["dest_type"])
    assert {"restaurant", "errand"}.issubset(dest_types)

    work_arrivals = itinerary[itinerary["dest_type"] == "work"]["arrival_hour_abs"] % 24
    school_arrivals = itinerary[itinerary["dest_type"] == "school"]["arrival_hour_abs"] % 24
    assert not work_arrivals.empty
    assert (work_arrivals > 19).mean() <= 0.01
    if not school_arrivals.empty:
        assert (school_arrivals > 12).mean() <= 0.01

    retail_arrivals = itinerary[itinerary["dest_type"] == "retail"]["arrival_hour_abs"] % 24
    assert (retail_arrivals > 21).mean() <= 0.10


def test_intraday_planner_keeps_work_and_school_anchors_sticky():
    engine = _intraday_engine()
    _, itinerary = engine.generate_weekly_itinerary(num_people=900, seed=1105)

    for activity in ["work", "school"]:
        trips = itinerary[itinerary["dest_type"] == activity]
        assert not trips.empty
        per_person_unique = trips.groupby("person_id")["dest_fsa"].nunique()
        assert (per_person_unique <= 1).mean() >= 0.90


def test_intraday_validation_gates_pass_before_charging():
    from simulation_validation import _intraday_route_plan_checks

    engine = _intraday_engine()
    people, itinerary = engine.generate_weekly_itinerary(num_people=450, seed=1107)
    rows = []
    _intraday_route_plan_checks(rows, people, itinerary)
    report = pd.DataFrame(rows)

    assert not report.empty
    assert (report["status"] == "PASS").all()


def test_intraday_itinerary_runs_through_charging_and_hourly_grid_aggregation():
    engine = _intraday_engine(ev_probability=0.8)
    people, itinerary = engine.generate_weekly_itinerary(num_people=300, seed=1109)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=1110)
    hourly = engine.aggregate_charge_events(charges)
    grid = engine.aggregate_weekly_grid_load(hourly)

    ev_legs = legs[legs["is_ev"]]
    assert not ev_legs.empty
    assert not charges.empty
    assert not hourly.empty
    assert len(grid) > 0
    assert (ev_legs["soc_before"] >= engine.config.reserve_soc - 0.002).all()
    assert np.isclose(hourly["energy_kwh"].sum(), charges["energy_delivered_kwh"].sum(), atol=1e-6)
