import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_mobility_simulator_returns_agent_rows():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.20))
    agents = engine.run_agent_day(num_people=1000, day_type="weekday", hour=17, seed=7)

    required = {
        "person_id", "is_ev", "home_fsa", "dest_type", "dest_fsa",
        "route_km", "initial_soc", "arrival_soc", "charge_probability",
        "will_charge", "energy_delivered_kwh",
    }
    assert len(agents) == 1000
    assert required.issubset(set(agents.columns))
    assert agents["initial_soc"].between(0, 1).all()
    assert agents["arrival_soc"].between(0, 1).all()


def test_mobility_simulator_ev_share_is_configurable():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.30))
    agents = engine.run_agent_day(num_people=3000, day_type="weekday", hour=8, seed=11)

    observed = agents["is_ev"].mean()
    assert 0.27 <= observed <= 0.33


def test_engine_reuses_static_grid_context_across_behavior_configs():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine, clear_static_context_cache

    clear_static_context_cache()
    first = MobilitySimulationEngine(MobilityConfig(
        ev_probability=0.20,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    ))
    second = MobilitySimulationEngine(MobilityConfig(
        ev_probability=0.80,
        initial_soc_alpha=8.0,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    ))

    assert first.road_network is second.road_network
    assert first.charger_catalog is second.charger_catalog
    assert first.route_km is second.route_km
    assert first.config.ev_probability != second.config.ev_probability


def test_low_soc_agents_charge_more_often():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0))
    agents = engine.run_agent_day(num_people=4000, day_type="weekday", hour=17, seed=13)

    low_soc = agents[agents["arrival_soc"] <= agents["arrival_soc"].quantile(0.25)]
    high_soc = agents[agents["arrival_soc"] >= agents["arrival_soc"].quantile(0.75)]

    assert low_soc["will_charge"].mean() > high_soc["will_charge"].mean()


def test_weekend_midday_has_more_leisure_than_weekday_commute():
    from mobility_simulator import MobilitySimulationEngine

    engine = MobilitySimulationEngine()
    weekday = engine.run_agent_day(num_people=2000, day_type="weekday", hour=8, seed=17)
    weekend = engine.run_agent_day(num_people=2000, day_type="weekend", hour=13, seed=17)

    assert (weekend["dest_type"] == "leisure").mean() > (weekday["dest_type"] == "leisure").mean()
    assert (weekday["dest_type"] == "work").mean() > (weekend["dest_type"] == "work").mean()


def test_aggregate_charging_load_returns_grid_columns():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.50))
    agents = engine.run_agent_day(num_people=3000, day_type="weekday", hour=17, seed=19)
    grid = engine.aggregate_charging_load(agents)

    required = {
        "fsa", "zone_type", "proxy_capacity_kw", "peak_hour",
        "peak_ev_load_kw", "baseline_load_kw", "total_load_kw",
        "overloaded", "deficit_kw", "centroid_lat", "centroid_lon",
    }
    assert isinstance(grid, pd.DataFrame)
    assert required.issubset(set(grid.columns))
    assert (grid["deficit_kw"] >= 0).all()


def test_weekly_itinerary_starts_and_returns_home_for_active_days():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.40))
    people, itinerary = engine.generate_weekly_itinerary(num_people=600, seed=23)

    assert len(people) == 600
    assert not itinerary.empty

    for (_, day), day_legs in itinerary.groupby(["person_id", "day"]):
        person = people.set_index("person_id").loc[day_legs.iloc[0]["person_id"]]
        assert day_legs.iloc[0]["origin_fsa"] == person["home_fsa"]
        assert day_legs.iloc[-1]["dest_fsa"] == person["home_fsa"]


def test_weekly_charging_keeps_ev_trips_feasible():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(ev_probability=1.0, initial_soc_alpha=2.0, initial_soc_beta=5.0)
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=500, seed=29)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=31)

    ev_legs = legs[legs["is_ev"]]
    assert not ev_legs.empty
    assert (ev_legs["soc_before"] >= cfg.reserve_soc - 0.001).all()
    assert not charges.empty
    assert {"normal", "patch"}.issubset(set(charges["event_type"]))


def test_inaccessible_home_and_work_chargers_use_public_catalog():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(road_graph_source="fsa_adjacency", charger_source="zone_proxy"))
    leg = pd.Series({"origin_idx": 0})

    no_access = pd.Series({"has_home_charger": False, "has_work_charger": False})
    assert engine._charger_for_activity(leg, "home", no_access).source != "private"
    assert engine._charger_for_activity(leg, "work", no_access).source != "private"

    with_access = pd.Series({"has_home_charger": True, "has_work_charger": True})
    assert engine._charger_for_activity(leg, "home", with_access).source == "private"
    assert engine._charger_for_activity(leg, "work", with_access).source == "private"


def test_weekly_patch_charging_is_a_stress_signal_not_default():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0))
    people, itinerary = engine.generate_weekly_itinerary(num_people=800, seed=37)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=41)

    patch_rate = (charges["event_type"] == "patch").mean()
    assert 0 < patch_rate < 0.35


def test_weekly_charge_events_aggregate_hourly_load():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.60))
    people, itinerary = engine.generate_weekly_itinerary(num_people=500, seed=43)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=47)
    hourly = engine.aggregate_charge_events(charges)

    assert not hourly.empty
    assert {"fsa", "day", "hour", "ev_load_kw", "energy_kwh", "event_type", "patch_type"}.issubset(hourly.columns)
    assert hourly["day"].between(0, 6).all()
    assert hourly["hour"].between(0, 23).all()


def test_weekly_empty_inputs_have_stable_schemas():
    from mobility_simulator import (
        CHARGE_EVENT_COLUMNS,
        HOURLY_CHARGE_COLUMNS,
        ITINERARY_COLUMNS,
        PEOPLE_COLUMNS,
        WEEKLY_LEG_COLUMNS,
        MobilitySimulationEngine,
    )

    engine = MobilitySimulationEngine()
    people, itinerary = engine.generate_weekly_itinerary(num_people=0, seed=1)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=2)
    hourly = engine.aggregate_charge_events(charges)

    assert list(people.columns) == PEOPLE_COLUMNS
    assert list(itinerary.columns) == ITINERARY_COLUMNS
    assert list(legs.columns) == WEEKLY_LEG_COLUMNS
    assert list(charges.columns) == CHARGE_EVENT_COLUMNS
    assert list(hourly.columns) == HOURLY_CHARGE_COLUMNS


def test_hackathon_fsa_data_is_mapped_to_road_graph():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(road_graph_source="fsa_adjacency"))
    summary = engine.road_network.summary()

    assert summary.node_count == len(engine.base_gdf)
    assert summary.edge_count >= len(engine.base_gdf)
    assert summary.unreachable_od_pairs == 0
    assert summary.median_circuity >= 1.0
    assert {"fsa", "zone_type", "proxy_capacity_kw", "centroid_lat", "centroid_lon"}.issubset(engine.base_gdf.columns)


def test_weekly_itinerary_uses_network_routes_and_arrive_by_timing():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.40, road_graph_source="fsa_adjacency"))
    _, itinerary = engine.generate_weekly_itinerary(num_people=500, seed=81)

    assert {"origin_idx", "dest_idx", "route_path", "freeflow_time_h", "travel_time_h", "planned_arrival_hour_abs"}.issubset(itinerary.columns)
    assert itinerary["reachable_route"].all()
    assert (itinerary["route_km"] > 0).all()
    assert (itinerary["travel_time_h"] > 0).all()

    commute = itinerary[itinerary["dest_type"].isin(["work", "school"]) & itinerary["planned_arrival_hour_abs"].notna()]
    assert not commute.empty
    assert commute["schedule_delay_min"].quantile(0.95) < 20.0


def test_weekly_charging_has_no_charge_drive_overlap_or_destination_prepay():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import _temporal_consistency_checks

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0, initial_soc_alpha=2.0, initial_soc_beta=5.0))
    people, itinerary = engine.generate_weekly_itinerary(num_people=700, seed=53)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=59)

    assert (legs["depart_hour_abs"] <= 168).all()
    assert (legs["arrival_hour_abs"] <= 168).all()
    assert (charges["end_hour_abs"] <= 168).all()
    assert "destination_public" not in set(charges["patch_type"])
    assert {"charger_id", "charger_lat", "charger_lon", "charger_source", "road_node_id", "road_snap_distance_m"}.issubset(charges.columns)
    assert {"origin_fsa", "origin_zone_type", "origin_activity", "origin_idx"}.issubset(charges.columns)
    assert charges["charger_lat"].between(40, 46).all()
    assert charges["charger_lon"].between(-82, -76).all()
    assert charges["road_node_id"].notna().all()
    assert charges["road_snap_distance_m"].notna().all()
    assert charges["origin_fsa"].astype(str).str.len().gt(0).all()
    assert (charges["origin_idx"].astype(int) >= 0).all()
    assert (charges["detour_km"] >= 0).all()

    ev_legs = legs[legs["is_ev"]]
    for person_id, person_charges in charges.groupby("person_id"):
        person_legs = ev_legs[ev_legs["person_id"] == person_id]
        for _, charge in person_charges.iterrows():
            overlaps = (
                (person_legs["depart_hour_abs"] < charge["end_hour_abs"])
                & (person_legs["arrival_hour_abs"] > charge["start_hour_abs"])
            )
            assert not overlaps.any(), f"charge overlaps drive leg for {person_id}"

        ordered = person_charges.sort_values("start_hour_abs")
        assert (ordered["start_hour_abs"].to_numpy()[1:] >= ordered["end_hour_abs"].to_numpy()[:-1] - 1e-9).all()

    rows = []
    _temporal_consistency_checks(rows, legs, charges)
    temporal = pd.DataFrame(rows)
    assert {
        "leg_time_bounds",
        "leg_duration_consistency_max_min",
        "leg_leg_overlap_count",
        "charge_time_bounds",
        "charge_duration_consistency_max_min",
        "charge_drive_overlap_count",
        "charge_charge_overlap_count",
        "destination_prepay_patch_count",
    }.issubset(set(temporal["metric"]))
    assert (temporal["status"] == "PASS").all()

    long_leg = legs[(legs["is_ev"]) & (legs["arrival_hour_abs"] - legs["depart_hour_abs"] > 0.3)].iloc[0]
    bad_charge = charges.iloc[[0]].copy()
    bad_charge["person_id"] = long_leg["person_id"]
    bad_charge["start_hour_abs"] = float(long_leg["depart_hour_abs"]) + 0.05
    bad_charge["duration_h"] = 0.1
    bad_charge["end_hour_abs"] = bad_charge["start_hour_abs"] + bad_charge["duration_h"]
    rows = []
    _temporal_consistency_checks(rows, legs, bad_charge)
    bad_temporal = pd.DataFrame(rows).set_index("metric")
    assert bad_temporal.loc["charge_drive_overlap_count", "status"] == "BREAK"


def test_weekly_charging_recomputes_arrive_by_delay_after_charging():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(
        ev_probability=1.0,
        initial_soc_alpha=2.0,
        initial_soc_beta=5.0,
        home_charger_probability=0.0,
        work_charger_probability=0.0,
        home_public_charger_access=1.0,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    )
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=450, seed=247)
    legs, _ = engine.simulate_weekly_charging(people, itinerary, seed=257)
    arrive_by = legs[legs["planned_arrival_hour_abs"].notna()].copy()

    assert not arrive_by.empty
    expected_delay = (
        (arrive_by["arrival_hour_abs"] + arrive_by["week_overflow_h"] - arrive_by["planned_arrival_hour_abs"])
        .clip(lower=0.0)
        * 60.0
    )
    assert np.allclose(arrive_by["schedule_delay_min"], expected_delay, atol=0.061)


def test_weekly_normal_charge_target_soc_uses_config_anchor():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    low = MobilitySimulationEngine(MobilityConfig(target_soc=0.75))
    high = MobilitySimulationEngine(MobilityConfig(target_soc=0.85))

    for activity in ["home", "work", "retail"]:
        low_rng = np.random.default_rng(42)
        high_rng = np.random.default_rng(42)
        low_target = low._preferred_target_soc(low_rng, activity)
        high_target = high._preferred_target_soc(high_rng, activity)

        assert high_target > low_target
        assert high_target - low_target <= 0.11


def test_validation_reports_arrive_by_delay_tail():
    from mobility_simulator import MobilityConfig
    from simulation_validation import _mobility_checks, validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=0.60, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(num_people=250, seed=267, config=cfg)
    metrics = set(report["metric"])

    assert {"arrive_by_delay_p95_min", "arrive_by_delay_gt20_pct", "arrive_by_delay_max_min"}.issubset(metrics)

    people = pd.DataFrame({
        "person_id": [f"P{i}" for i in range(100)],
        "person_type": ["worker"] * 80 + ["retired"] * 20,
    })
    itinerary = pd.DataFrame({
        "person_id": [f"P{i}" for i in range(100)],
        "person_type": ["worker"] * 80 + ["retired"] * 20,
        "day": [0] * 100,
        "depart_hour_abs": [8.0] * 100,
        "dest_type": ["work"] * 100,
        "dest_fsa": ["M5V"] * 100,
        "planned_arrival_hour_abs": [9.0] * 100,
        "schedule_delay_min": [30.0] * 100,
        "route_km": [12.0] * 100,
    })
    rows = []
    _mobility_checks(rows, people, itinerary)
    bad_report = pd.DataFrame(rows).set_index("metric")

    assert bad_report.loc["arrive_by_delay_gt20_pct", "status"] == "BREAK"
    assert bad_report.loc["arrive_by_delay_max_min", "status"] == "PASS"


def test_public_patch_charging_maps_to_charger_catalog_not_destination_prepay():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(ev_probability=1.0, initial_soc_alpha=2.0, initial_soc_beta=5.0, road_graph_source="fsa_adjacency")
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=900, seed=83)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=89)

    public_patches = charges[charges["patch_type"].isin(["near_route_public", "forced_origin_public"])]
    assert not public_patches.empty
    assert set(public_patches["charger_source"]).issubset({"zone_proxy", "osm", "afdc"})
    assert public_patches["charger_id"].str.len().gt(0).all()


def test_patch_softmax_temperature_changes_patch_choice_mix():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    def public_patch_share(temperature: float) -> float:
        cfg = MobilityConfig(
            road_graph_source="fsa_adjacency",
            charger_source="zone_proxy",
            retail_public_charger_access=1.0,
            patch_softmax_temperature=temperature,
        )
        engine = MobilitySimulationEngine(cfg)
        leg = pd.Series({
            "person_id": "P",
            "origin_activity": "retail",
            "origin_idx": 0,
            "dest_idx": 25,
            "origin_fsa": engine.fsas[0],
            "origin_zone_type": engine.zone_types[0],
            "dest_fsa": engine.fsas[25],
            "dest_zone_type": engine.zone_types[25],
            "dest_type": "home",
            "dwell_start_abs": 0.0,
            "depart_hour_abs": 0.75,
        })
        person = pd.Series({"has_home_charger": False, "has_work_charger": False})
        rng = np.random.default_rng(123)
        choices = [
            engine._choose_patch_charge(rng, leg, person, soc_kwh=1.0, required_kwh=28.0, capacity=70.0)["patch_type"]
            for _ in range(400)
        ]
        return float(pd.Series(choices).isin(["near_route_public", "forced_origin_public"]).mean())

    assert public_patch_share(2.0) > public_patch_share(0.2) + 0.05


def test_public_charge_events_keep_origin_context_and_plausible_detours():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(
        ev_probability=1.0,
        home_charger_probability=0.0,
        work_charger_probability=0.0,
        home_public_charger_access=1.0,
        work_public_charger_access=1.0,
        retail_public_charger_access=1.0,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    )
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=300, seed=241)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=251)
    public = charges[charges["charger_source"].isin(["zone_proxy", "osm", "afdc"])]

    assert len(public) >= 30
    assert public["origin_fsa"].astype(str).str.len().gt(0).all()
    assert (public["origin_idx"].astype(int) >= 0).all()
    assert public["detour_km"].quantile(0.95) <= 20.0


def test_validation_catches_public_charge_not_in_catalog():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, _charger_catalog_mapping_checks

    cfg = MobilityConfig(
        ev_probability=1.0,
        home_charger_probability=0.0,
        work_charger_probability=0.0,
        home_public_charger_access=1.0,
        work_public_charger_access=1.0,
        retail_public_charger_access=1.0,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    )
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=180, seed=241)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=251)
    public_index = charges.index[charges["charger_source"].isin(["zone_proxy", "osm", "afdc"])][0]

    rows = []
    _charger_catalog_mapping_checks(rows, engine, charges, ValidationOptions())
    good_report = pd.DataFrame(rows).set_index("metric")

    broken = charges.copy()
    broken.loc[public_index, "charger_id"] = "missing_public_charger"
    rows = []
    _charger_catalog_mapping_checks(rows, engine, broken, ValidationOptions())
    bad_report = pd.DataFrame(rows).set_index("metric")

    assert good_report.loc["public_charge_ids_in_catalog_pct", "status"] == "PASS"
    assert good_report.loc["public_charge_catalog_attributes_match_pct", "status"] == "PASS"
    assert bad_report.loc["public_charge_ids_in_catalog_pct", "status"] == "BREAK"
    assert bad_report.loc["public_charge_catalog_attributes_match_pct", "status"] == "BREAK"


def test_validation_catches_private_charge_mapped_to_wrong_origin():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, _private_charger_mapping_checks

    cfg = MobilityConfig(
        ev_probability=1.0,
        initial_soc_alpha=2.0,
        initial_soc_beta=5.0,
        home_charger_probability=1.0,
        work_charger_probability=1.0,
        road_graph_source="fsa_adjacency",
        charger_source="zone_proxy",
    )
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=220, seed=341)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=351)
    private_index = charges.index[charges["charger_source"] == "private"][0]

    rows = []
    _private_charger_mapping_checks(rows, engine, charges, ValidationOptions())
    good_report = pd.DataFrame(rows).set_index("metric")

    broken = charges.copy()
    broken.loc[private_index, "fsa"] = "ZZZ"
    broken.loc[private_index, "charger_id"] = "private_home_ZZZ"
    broken.loc[private_index, "detour_km"] = 5.0
    rows = []
    _private_charger_mapping_checks(rows, engine, broken, ValidationOptions())
    bad_report = pd.DataFrame(rows).set_index("metric")

    assert good_report.loc["private_charge_location_matches_origin_pct", "status"] == "PASS"
    assert good_report.loc["private_charge_power_detour_valid_pct", "status"] == "PASS"
    assert good_report.loc["private_charge_road_nodes_valid", "status"] == "PASS"
    assert bad_report.loc["private_charge_location_matches_origin_pct", "status"] == "BREAK"
    assert bad_report.loc["private_charge_power_detour_valid_pct", "status"] == "BREAK"


def test_weekly_soc_covers_trip_plus_reserve_before_departure():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(ev_probability=1.0, initial_soc_alpha=2.0, initial_soc_beta=5.0)
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=650, seed=61)
    legs, _ = engine.simulate_weekly_charging(people, itinerary, seed=67)

    ev_legs = legs[legs["is_ev"]]
    required_soc = ev_legs["trip_kwh"] / cfg.battery_capacity_kwh + cfg.reserve_soc
    assert (ev_legs["soc_before"] + 0.002 >= required_soc).all()


def test_weekly_final_soc_balance_is_bounded():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import compute_soc_balance, replay_week_from_final_soc

    cfg = MobilityConfig(ev_probability=1.0, initial_soc_alpha=6.0, initial_soc_beta=2.0)
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=350, seed=269)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=271)
    balance = compute_soc_balance(people, legs, charges, cfg)

    assert not balance.empty
    assert (balance["final_soc"] >= cfg.reserve_soc - 0.01).all()
    assert abs(balance["soc_delta"].mean() * 100.0) <= 8.0
    assert (balance["final_soc"] >= 0.995).mean() < 0.10

    _, replay_legs, replay_charges, replay_balance = replay_week_from_final_soc(
        engine,
        people,
        itinerary,
        legs,
        charges,
        seed=273,
    )
    assert not replay_legs.empty
    assert not replay_charges.empty
    assert (replay_balance["final_soc"] >= cfg.reserve_soc - 0.01).all()
    assert abs(replay_balance["soc_delta"].mean() * 100.0) <= 5.0


def test_validation_can_check_repeated_week_stability():
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(
        num_people=350,
        seed=901,
        config=cfg,
        options=ValidationOptions(include_repeat_week=True),
    )
    repeat = report[report["gate"] == "repeat_week"]

    assert {
        "repeat_week_final_soc_mean_drift_pctpt",
        "repeat_week_final_soc_below_reserve",
        "repeat_week_charges_per_ev",
        "repeat_week_reserve_ok",
        "repeat_week_temporal_ok",
    }.issubset(set(repeat["metric"]))
    assert (repeat["status"] == "PASS").all()


def test_weekly_hourly_aggregation_is_unique_and_energy_conserving():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.80))
    people, itinerary = engine.generate_weekly_itinerary(num_people=800, seed=71)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=73)
    hourly = engine.aggregate_charge_events(charges)

    keys = ["fsa", "day", "hour", "event_type", "patch_type"]
    assert not hourly.duplicated(keys).any()
    assert np.isclose(hourly["energy_kwh"].sum(), charges["energy_delivered_kwh"].sum(), rtol=0, atol=1e-6)
    assert np.isclose(hourly["ev_load_kw"].sum(), hourly["energy_kwh"].sum(), rtol=0, atol=1e-6)


def test_validation_catches_charge_event_accounting_breaks():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, _charging_checks

    cfg = MobilityConfig(ev_probability=1.0, initial_soc_alpha=2.0, initial_soc_beta=5.0)
    engine = MobilitySimulationEngine(cfg)
    people, itinerary = engine.generate_weekly_itinerary(num_people=500, seed=701)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=703)
    assert not charges.empty

    rows = []
    _charging_checks(rows, cfg, people, legs, charges, ValidationOptions())
    report = pd.DataFrame(rows).set_index("metric")
    assert report.loc["charge_event_energy_duration_error_max_kwh", "status"] == "PASS"
    assert report.loc["charge_chronology_soc_error_max", "status"] == "PASS"

    bad_energy = charges.copy()
    bad_energy.loc[bad_energy.index[0], "energy_delivered_kwh"] += 1.0
    rows = []
    _charging_checks(rows, cfg, people, legs, bad_energy, ValidationOptions())
    bad_energy_report = pd.DataFrame(rows).set_index("metric")
    assert bad_energy_report.loc["charge_event_energy_duration_error_max_kwh", "status"] == "BREAK"

    bad_soc = charges.copy()
    bad_soc.loc[bad_soc.index[0], "soc_after_charge"] = 0.0
    rows = []
    _charging_checks(rows, cfg, people, legs, bad_soc, ValidationOptions())
    bad_soc_report = pd.DataFrame(rows).set_index("metric")
    assert bad_soc_report.loc["charge_chronology_soc_error_max", "status"] == "BREAK"


def test_weekly_grid_load_has_headroom_and_conserves_ev_load():
    from mobility_simulator import GRID_LOAD_COLUMNS, MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.60, road_graph_source="fsa_adjacency"))
    people, itinerary = engine.generate_weekly_itinerary(num_people=500, seed=75)
    _, charges = engine.simulate_weekly_charging(people, itinerary, seed=77)
    hourly = engine.aggregate_charge_events(charges)
    grid = engine.aggregate_weekly_grid_load(hourly)

    assert list(grid.columns) == GRID_LOAD_COLUMNS
    assert not grid.empty
    assert (grid["baseline_load_kw"] <= grid["proxy_capacity_kw"]).all()
    assert (grid["headroom_kw"] > 0).all()
    assert np.isclose(grid["ev_load_kw"].sum(), hourly["ev_load_kw"].sum(), rtol=0, atol=1e-6)


def test_weekly_grid_load_scale_can_stress_capacity():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import validate_weekly_simulation

    base = MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", grid_ev_load_scale=1.0)
    stressed = MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", grid_ev_load_scale=20.0)
    base_engine = MobilitySimulationEngine(base)
    stressed_engine = MobilitySimulationEngine(stressed)

    people, itinerary = base_engine.generate_weekly_itinerary(num_people=500, seed=91)
    _, charges = base_engine.simulate_weekly_charging(people, itinerary, seed=93)
    hourly = base_engine.aggregate_charge_events(charges)
    base_grid = base_engine.aggregate_weekly_grid_load(hourly)
    stressed_grid = stressed_engine.aggregate_weekly_grid_load(hourly)

    assert stressed_grid["ev_load_kw"].sum() > base_grid["ev_load_kw"].sum()
    assert stressed_grid["deficit_kw"].max() >= base_grid["deficit_kw"].max()
    assert stressed_grid["overloaded"].sum() >= base_grid["overloaded"].sum()

    report, _ = validate_weekly_simulation(
        num_people=500,
        seed=91,
        config=MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", grid_ev_load_scale=20.0),
    )
    scale_row = report[report["metric"] == "ev_load_scale_matches_config"].iloc[0]
    assert scale_row["status"] == "PASS"
    assert float(scale_row["value"]) == 20.0


def test_grid_validation_catches_scaled_ev_load_mismatch():
    from mobility_simulator import MobilityConfig
    from simulation_validation import _grid_load_checks

    hourly = pd.DataFrame({
        "fsa": ["M5V"],
        "day": [0],
        "hour": [18],
        "ev_load_kw": [10.0],
    })
    grid = pd.DataFrame({
        "fsa": ["M5V"],
        "zone_type": ["residential"],
        "day": [0],
        "hour": [18],
        "proxy_capacity_kw": [100.0],
        "baseline_load_kw": [80.0],
        "ev_load_kw": [25.0],
        "total_load_kw": [105.0],
        "headroom_kw": [20.0],
        "overloaded": [True],
        "deficit_kw": [5.0],
        "centroid_lat": [43.64],
        "centroid_lon": [-79.39],
    })
    rows = []
    _grid_load_checks(rows, grid, hourly, MobilityConfig(grid_ev_load_scale=2.0))
    report = pd.DataFrame(rows).set_index("metric")

    assert report.loc["ev_load_scale_matches_config", "status"] == "BREAK"
    assert report.loc["ev_load_energy_conservation_kw", "status"] == "BREAK"


def test_population_expansion_scale_uses_statcan_counts_when_available():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    cfg = MobilityConfig(vehicle_population_share=0.46)
    engine = MobilitySimulationEngine(cfg)
    scale = engine.population_expansion_scale(1000)
    if engine.base_gdf["population_2021"].notna().any():
        expected = engine.base_gdf["population_2021"].fillna(0).sum() * cfg.vehicle_population_share / 1000
        assert np.isclose(scale, expected)
        assert np.isclose(engine.population_expansion_scale(1000, population_share=0.05), expected * (0.05 / cfg.vehicle_population_share))
    else:
        assert scale == 1.0


def test_daily_grid_load_baseline_does_not_overload_without_evs():
    from mobility_simulator import MobilitySimulationEngine

    engine = MobilitySimulationEngine()
    agents = engine.run_agent_day(num_people=1000, day_type="weekday", hour=18, seed=79)
    agents["will_charge"] = False
    agents["charge_duration_h"] = 0.0
    grid = engine.aggregate_charging_load(agents)

    assert grid.empty

    active = engine.run_agent_day(num_people=2000, day_type="weekday", hour=18, seed=80)
    load = engine.aggregate_charging_load(active)
    assert load.empty or (load["baseline_load_kw"] < load["proxy_capacity_kw"]).all()


def test_weekly_trips_aggregate_to_road_edge_flows():
    from mobility_simulator import EDGE_FLOW_COLUMNS, MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.50, road_graph_source="fsa_adjacency"))
    _, itinerary = engine.generate_weekly_itinerary(num_people=300, seed=97)
    flows = engine.aggregate_edge_flows(itinerary)
    routed = itinerary[itinerary["route_path"].str.contains("|", regex=False)]

    assert list(flows.columns) == EDGE_FLOW_COLUMNS
    assert not flows.empty
    assert (flows["vehicle_count"] >= flows["ev_count"]).all()
    assert flows["day"].between(0, 6).all()
    assert flows["hour"].between(0, 23).all()
    assert flows["fsa"].isin(set(engine.fsas)).all()
    assert np.isclose(flows["route_km"].sum(), routed["route_km"].sum(), rtol=1e-6, atol=0.1)


def test_validation_catches_corrupted_edge_flow_artifact():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, _edge_flow_integrity_checks

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.50, road_graph_source="fsa_adjacency"))
    _, itinerary = engine.generate_weekly_itinerary(num_people=120, seed=97)
    flows = engine.aggregate_edge_flows(itinerary)

    rows = []
    _edge_flow_integrity_checks(rows, engine, flows, ValidationOptions())
    good_report = pd.DataFrame(rows).set_index("metric")

    broken = flows.copy()
    broken.loc[broken.index[0], "ev_count"] = broken.loc[broken.index[0], "vehicle_count"] + 1.0
    broken.loc[broken.index[0], "route_km"] = -1.0
    broken.loc[broken.index[0], "hour"] = 25
    rows = []
    _edge_flow_integrity_checks(rows, engine, broken, ValidationOptions())
    bad_report = pd.DataFrame(rows).set_index("metric")

    assert good_report.loc["edge_flow_ev_count_le_vehicle_count", "status"] == "PASS"
    assert good_report.loc["edge_flow_counts_nonnegative", "status"] == "PASS"
    assert good_report.loc["edge_flow_time_bounds", "status"] == "PASS"
    assert bad_report.loc["edge_flow_ev_count_le_vehicle_count", "status"] == "BREAK"
    assert bad_report.loc["edge_flow_counts_nonnegative", "status"] == "BREAK"
    assert bad_report.loc["edge_flow_time_bounds", "status"] == "BREAK"


def test_fsa_corridor_flows_are_fast_calibration_proxy():
    from mobility_simulator import EDGE_FLOW_COLUMNS, MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=0.50, road_graph_source="fsa_adjacency")
    engine = MobilitySimulationEngine(cfg)
    _, itinerary = engine.generate_weekly_itinerary(num_people=150, seed=98)
    flows = engine.aggregate_fsa_corridor_flows(itinerary)
    routed = itinerary[itinerary["route_path"].str.contains("|", regex=False)]

    assert list(flows.columns) == EDGE_FLOW_COLUMNS
    assert not flows.empty
    assert flows["edge_u"].max() < len(engine.fsas)
    assert flows["fsa"].isin(set(engine.fsas)).all()
    assert np.isclose(flows["route_km"].sum(), routed["route_km"].sum(), rtol=1e-6, atol=0.1)

    report, artifacts = validate_weekly_simulation(
        num_people=150,
        seed=99,
        config=cfg,
        options=ValidationOptions(edge_flow_detail="fsa", include_observed_targets=True),
    )
    assert "traffic_morning_fsa_l1" in set(report["metric"])
    assert not artifacts["edge_flows"].empty


def test_edge_flows_are_bucketed_by_traversal_time_not_departure_only():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency"))
    route = next(
        candidate
        for row in engine.road_network.routes
        for candidate in row
        if len(candidate.path) >= 3
    )
    leg = pd.DataFrame([{
        "is_ev": True,
        "depart_hour_abs": 8.75,
        "travel_time_h": 2.6,
        "route_km": round(route.distance_km, 2),
        "route_path": "|".join(map(str, route.path)),
    }])

    flows = engine.aggregate_edge_flows(leg)

    assert flows["hour"].min() == 8
    assert flows["hour"].max() >= 10
    assert flows[["day", "hour"]].drop_duplicates().shape[0] >= 3
    assert np.isclose(flows["route_km"].sum(), leg["route_km"].sum(), rtol=0, atol=1e-6)


def test_road_network_reuses_edge_segment_expansion_cache():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency"))
    route = next(
        candidate
        for row in engine.road_network.routes
        for candidate in row
        if len(candidate.path) >= 3
    )

    engine.road_network._edge_segment_cache.clear()
    engine.road_network._edge_template_cache.clear()
    first = engine.road_network.route_edge_segments(route.path)
    cache_size = len(engine.road_network._edge_segment_cache)
    second = engine.road_network.route_edge_segments(route.path)

    assert cache_size == 1
    assert second == first
    assert len(engine.road_network._edge_segment_cache) == cache_size


def test_road_network_reuses_od_edge_template_cache():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency"))
    route = next(
        candidate
        for row in engine.road_network.routes
        for candidate in row
        if len(candidate.path) >= 3
    )

    engine.road_network._edge_template_cache.clear()
    first = engine.road_network.route_edge_template(route.origin_idx, route.dest_idx)
    cache_size = len(engine.road_network._edge_template_cache)
    second = engine.road_network.route_edge_template(route.origin_idx, route.dest_idx)

    assert cache_size == 1
    assert second == first
    assert len(first.segments) == len(first.fsa_indices)
    assert first.distance_km > 0
    assert first.freeflow_time_h > 0
    assert len(engine.road_network._edge_template_cache) == cache_size


def test_road_network_persists_edge_template_cache(tmp_path, monkeypatch):
    import pickle

    import road_network
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine, clear_static_context_cache

    monkeypatch.setattr(road_network, "FSA_EDGE_TEMPLATE_CACHE", tmp_path / "edge_templates.pkl")
    cfg = MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    clear_static_context_cache()
    engine = MobilitySimulationEngine(cfg)
    route = next(
        candidate
        for row in engine.road_network.routes
        for candidate in row
        if len(candidate.path) >= 3
    )

    first = engine.road_network.route_edge_template(route.origin_idx, route.dest_idx)
    engine.road_network.persist_edge_template_cache()

    assert road_network.FSA_EDGE_TEMPLATE_CACHE.exists()

    clear_static_context_cache()
    second_engine = MobilitySimulationEngine(cfg)
    key = (route.origin_idx, route.dest_idx)

    assert key in second_engine.road_network._edge_template_cache
    assert second_engine.road_network.route_edge_template(*key) == first

    with road_network.FSA_EDGE_TEMPLATE_CACHE.open("rb") as fh:
        stale_payload = pickle.load(fh)
    stale_payload["fingerprint"] = dict(stale_payload["fingerprint"])
    stale_payload["fingerprint"]["fsas"] = ("stale",)
    with road_network.FSA_EDGE_TEMPLATE_CACHE.open("wb") as fh:
        pickle.dump(stale_payload, fh)

    clear_static_context_cache()
    third_engine = MobilitySimulationEngine(cfg)
    assert key not in third_engine.road_network._edge_template_cache
    assert third_engine.road_network.route_edge_template(*key) == first


def test_road_network_migrates_compatible_route_cache_with_fingerprint(tmp_path, monkeypatch):
    import pickle

    import road_network
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine, clear_static_context_cache

    monkeypatch.setattr(road_network, "FSA_ROUTE_CACHE", tmp_path / "routes.pkl")
    monkeypatch.setattr(road_network, "FSA_EDGE_TEMPLATE_CACHE", tmp_path / "edge_templates.pkl")
    cfg = MobilityConfig(ev_probability=1.0, road_graph_source="fsa_adjacency", charger_source="zone_proxy")

    clear_static_context_cache()
    engine = MobilitySimulationEngine(cfg)
    with road_network.FSA_ROUTE_CACHE.open("wb") as fh:
        pickle.dump({"version": 2, "routes": engine.road_network.routes}, fh)

    clear_static_context_cache()
    migrated = MobilitySimulationEngine(cfg)
    with road_network.FSA_ROUTE_CACHE.open("rb") as fh:
        payload = pickle.load(fh)

    assert payload["version"] == road_network.ROUTE_CACHE_VERSION
    assert payload["fingerprint"] == migrated.road_network._cache_fingerprint()
    assert migrated.road_network._cached_routes_compatible(payload["routes"])


def test_validation_options_can_require_real_grid_and_chargers():
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=0.30, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(
        num_people=300,
        seed=107,
        config=cfg,
        options=ValidationOptions(require_real_grid=True, require_real_chargers=True),
    )

    broken = report[report["status"] != "PASS"]
    assert {"source", "charger_count", "real_charger_catalog_only"}.issubset(set(broken["metric"]))


def test_parallel_multi_seed_validation_matches_serial():
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions, validate_multi_seed

    cfg = MobilityConfig(ev_probability=0.30, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    opts = ValidationOptions(edge_flow_detail="fsa")

    serial_report, serial_artifacts = validate_multi_seed(160, (101, 202), cfg, opts, jobs=1)
    parallel_report, parallel_artifacts = validate_multi_seed(160, (101, 202), cfg, opts, jobs=2)

    pd.testing.assert_frame_equal(serial_report, parallel_report)
    pd.testing.assert_frame_equal(serial_artifacts["seed_reports"], parallel_artifacts["seed_reports"])


def test_route_path_validation_checks_all_unique_paths_not_prefix():
    import networkx as nx

    from simulation_validation import _osm_route_path_validity

    graph = nx.Graph()
    graph.add_edges_from((idx, idx + 1) for idx in range(250))
    paths = pd.Series([f"{idx}|{idx + 1}" for idx in range(201)] + ["0|250"])

    unique_count, nodes_valid, edges_valid = _osm_route_path_validity(graph, paths)

    assert unique_count == 202
    assert nodes_valid
    assert not edges_valid


def test_validation_reports_hackathon_mapping_and_purpose_alignment():
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=0.50, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(
        num_people=350,
        seed=137,
        config=cfg,
        options=ValidationOptions(include_observed_targets=True),
    )

    metrics = set(report["metric"])
    assert {
        "fsa_rows",
        "required_columns_present",
        "population_positive_pct",
        "traffic_count_join_pct",
        "road_anchor_per_fsa_count",
        "route_matrix_shape",
        "home_zone_alignment_pct",
        "work_zone_alignment_pct",
        "retail_zone_alignment_pct",
    }.issubset(metrics)
    assert (report[report["gate"].isin(["hackathon_data", "purpose_zone"])]["status"] == "PASS").all()


def test_purpose_zone_alignment_gate_catches_bad_destination_mapping():
    from simulation_validation import _purpose_zone_alignment_checks

    itinerary = pd.DataFrame({
        "dest_type": ["work"] * 80,
        "dest_zone_type": ["residential"] * 80,
    })
    rows = []
    _purpose_zone_alignment_checks(rows, itinerary)
    report = pd.DataFrame(rows).set_index("metric")

    assert report.loc["work_zone_alignment_pct", "status"] == "BREAK"


def test_strict_sample_evidence_breaks_insufficient_purpose_samples():
    from simulation_validation import ValidationOptions, _purpose_zone_alignment_checks

    itinerary = pd.DataFrame({
        "dest_type": ["home"] * 80,
        "dest_zone_type": ["residential"] * 80,
    })
    rows = []
    _purpose_zone_alignment_checks(rows, itinerary, ValidationOptions(require_sample_evidence=True))
    report = pd.DataFrame(rows).set_index("metric")

    assert report.loc["transit_hub_zone_alignment_pct", "status"] == "BREAK"


def test_weekly_itinerary_samples_misc_outing_purposes():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import _mobility_checks

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.0, road_graph_source="fsa_adjacency", charger_source="zone_proxy"))
    people, itinerary = engine.generate_weekly_itinerary(num_people=1500, seed=2468)
    counts = itinerary["dest_type"].value_counts()

    assert counts.get("transit_hub", 0) >= 30
    assert counts.get("other", 0) >= 30

    rows = []
    _mobility_checks(rows, people, itinerary)
    report = pd.DataFrame(rows).set_index("metric")
    assert report.loc["transit_hub_destination_share_pct", "status"] == "PASS"
    assert report.loc["other_destination_share_pct", "status"] == "PASS"


def test_charger_concentration_gate_catches_single_station_collapse():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from simulation_validation import ValidationOptions, _charger_concentration_checks

    engine = MobilitySimulationEngine(MobilityConfig(road_graph_source="fsa_adjacency", charger_source="zone_proxy"))
    charges = pd.DataFrame({
        "charger_id": ["single"] * 40,
        "charger_source": ["zone_proxy"] * 40,
        "energy_delivered_kwh": [12.0] * 40,
    })
    rows = []
    _charger_concentration_checks(rows, engine, charges, ValidationOptions())
    report = pd.DataFrame(rows).set_index("metric")

    assert report.loc["public_chargers_used", "status"] == "BREAK"
    assert report.loc["top_1pct_used_public_charger_energy_share_pct", "status"] == "BREAK"


def test_patch_event_gate_is_sample_size_aware():
    from mobility_simulator import MobilityConfig
    from simulation_validation import validate_weekly_simulation

    cfg = MobilityConfig(ev_probability=0.03, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(num_people=120, seed=607, config=cfg)

    row = report[report["metric"] == "patch_events_nonempty"].iloc[0]
    assert row["status"] == "PASS"
    assert "Sample-insufficient" in row["detail"]


def test_calibration_extracts_and_scores_fit_metrics(monkeypatch, tmp_path):
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine
    from model_calibration import FIT_TARGETS, candidate_configs, clear_fit_itinerary_cache, evaluate_config, fit_config, score_fit_metrics, selected_candidate_indices

    cfg = MobilityConfig(ev_probability=0.40, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    summary, per_seed = evaluate_config(cfg, num_people=250, seeds=(111,))

    assert not summary.empty
    assert not per_seed.empty
    assert np.isfinite(summary["mean_loss"].iloc[0])
    assert "charges_per_ev_week_mean" in summary.columns
    assert "public_charges_per_ev_week_mean" in summary.columns
    assert "arrive_by_delay_gt20_pct_mean" in summary.columns
    assert "work_zone_alignment_pct_mean" in summary.columns
    assert "retail_zone_alignment_pct_mean" in summary.columns
    assert "transit_hub_destination_share_pct_mean" in summary.columns
    assert "other_destination_share_pct_mean" in summary.columns

    candidates = candidate_configs(cfg)
    assert candidates[0] == cfg

    ranking = fit_config(cfg, num_people=120, seeds=(111,), max_candidates=3)
    assert len(ranking) == 3
    assert "break_metrics" in ranking.columns
    assert ranking[["max_break_count", "mean_loss"]].sort_values(["max_break_count", "mean_loss"]).index.tolist() == ranking.index.tolist()

    serial = fit_config(cfg, num_people=80, seeds=(111,), max_candidates=2, jobs=1)
    parallel = fit_config(cfg, num_people=80, seeds=(111,), max_candidates=2, jobs=2)
    pd.testing.assert_frame_equal(serial, parallel)

    chunk = fit_config(cfg, num_people=60, seeds=(111,), max_candidates=None, candidate_start=2, candidate_stop=4)
    assert sorted(chunk["candidate"].tolist()) == [2, 3]
    assert selected_candidate_indices(cfg, candidate_start=2, candidate_stop=4) == [2, 3]

    subset = fit_config(cfg, num_people=60, seeds=(111,), candidate_indices=[3, 1, 3])
    assert sorted(subset["candidate"].tolist()) == [1, 3]

    clear_fit_itinerary_cache()
    generate_calls = 0
    original_generate = MobilitySimulationEngine.generate_weekly_itinerary

    def counted_generate(self, *args, **kwargs):
        nonlocal generate_calls
        generate_calls += 1
        return original_generate(self, *args, **kwargs)

    monkeypatch.setattr(MobilitySimulationEngine, "generate_weekly_itinerary", counted_generate)
    shared_plan = fit_config(cfg, num_people=60, seeds=(111,), candidate_indices=[0, 1, 2], jobs=1)
    assert len(shared_plan) == 3
    assert generate_calls == 1
    monkeypatch.setattr(MobilitySimulationEngine, "generate_weekly_itinerary", original_generate)
    clear_fit_itinerary_cache()

    sparse_metrics = {
        "charges_per_ev_week": 2.8,
        "patches_per_ev_week": 0.25,
        "public_charges_per_ev_week": 0.75,
        "arrive_by_delay_gt20_pct": 0.5,
        "arrive_by_delay_max_min": 20.0,
        "median_inconvenience_min": np.nan,
        "public_charge_detour_p95_km": np.nan,
        "public_patch_detour_p95_km": np.nan,
    }
    sparse_targets = {name: FIT_TARGETS[name] for name in sparse_metrics}
    assert score_fit_metrics(sparse_metrics, targets=sparse_targets) < 1.0

    import model_calibration
    monkeypatch.setattr(model_calibration.sys, "argv", ["<stdin>"])
    fallback = fit_config(cfg, num_people=60, seeds=(111,), max_candidates=2, jobs=2)
    assert len(fallback) == 2

    progress_events = []
    checkpoint = tmp_path / "fit_checkpoint.csv"
    checkpointed = fit_config(
        cfg,
        num_people=60,
        seeds=(111,),
        max_candidates=1,
        checkpoint_path=checkpoint,
        progress=lambda row, completed, total: progress_events.append((int(row["candidate"]), completed, total)),
    )
    assert progress_events == [(0, 1, 1)]
    assert checkpoint.exists()
    assert pd.read_csv(checkpoint)["candidate"].tolist() == [0]

    resumed_events = []
    resumed = fit_config(
        cfg,
        num_people=60,
        seeds=(111,),
        max_candidates=1,
        checkpoint_path=checkpoint,
        resume=True,
        progress=lambda row, completed, total: resumed_events.append((int(row["candidate"]), completed, total)),
    )
    assert resumed_events == []
    pd.testing.assert_frame_equal(checkpointed, resumed)

    filtered_resume = fit_config(
        cfg,
        num_people=60,
        seeds=(111,),
        candidate_indices=[1],
        checkpoint_path=checkpoint,
        resume=True,
    )
    assert filtered_resume["candidate"].tolist() == [1]

    aligned_metrics = {
        "transit_hub_destination_share_pct": 1.0,
        "other_destination_share_pct": 0.8,
        "work_zone_alignment_pct": 65.0,
        "retail_zone_alignment_pct": 45.0,
        "transit_hub_zone_alignment_pct": 65.0,
        "public_charger_top_1pct_energy_share_pct": 8.0,
        "public_charger_top_10pct_energy_share_pct": 30.0,
    }
    collapsed_metrics = {
        "transit_hub_destination_share_pct": 0.0,
        "other_destination_share_pct": 0.0,
        "work_zone_alignment_pct": 20.0,
        "retail_zone_alignment_pct": 10.0,
        "transit_hub_zone_alignment_pct": 15.0,
        "public_charger_top_1pct_energy_share_pct": 95.0,
        "public_charger_top_10pct_energy_share_pct": 100.0,
    }
    concentration_targets = {name: FIT_TARGETS[name] for name in aligned_metrics}
    assert score_fit_metrics(collapsed_metrics, targets=concentration_targets) > score_fit_metrics(aligned_metrics, targets=concentration_targets)


def test_sensitivity_validation_reports_directional_checks():
    from mobility_simulator import MobilityConfig
    from simulation_validation import validate_sensitivity_scenarios, validate_sensitivity_scenarios_multi_seed

    cfg = MobilityConfig(ev_probability=0.50, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, metrics = validate_sensitivity_scenarios(num_people=250, seed=515, config=cfg)

    assert not report.empty
    assert not metrics.empty
    status_by_metric = report.set_index("metric")["status"]
    assert "low_initial_soc_patch_rises" in set(report["metric"])
    assert status_by_metric.loc["high_access_patch_falls"] == "PASS"
    assert status_by_metric.loc["high_access_weekday_patch_falls"] == "PASS"
    assert status_by_metric.loc["base_scenario_internal_break_count"] == "PASS"
    assert "patches_per_ev_week" in metrics.columns
    assert "weekday_patches_per_ev_week" in metrics.columns
    assert "break_metrics" in metrics.columns
    assert "max_break_count" in metrics.columns

    multi_report, multi_metrics = validate_sensitivity_scenarios_multi_seed(num_people=250, seeds=(515, 616), config=cfg, jobs=2)
    assert not multi_report.empty
    assert set(multi_metrics["scenario"]) == set(metrics["scenario"])
    assert (multi_metrics["seed_count"] == 2).all()
    assert "cold_efficiency_patch_rises" in set(multi_report["metric"])
    assert "break_metrics" in multi_metrics.columns
    assert "max_break_count" in multi_metrics.columns


def test_observed_targets_feed_validation_and_calibration_metrics():
    from mobility_simulator import MobilityConfig
    from model_calibration import evaluate_config
    from observed_targets import load_observed_targets
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    targets = load_observed_targets()
    assert abs(sum(targets.morning_zone_weights.values()) - 1.0) < 1e-9
    assert abs(sum(targets.charger_zone_weights.values()) - 1.0) < 1e-9

    cfg = MobilityConfig(ev_probability=0.40, road_graph_source="fsa_adjacency", charger_source="zone_proxy")
    report, _ = validate_weekly_simulation(
        num_people=250,
        seed=121,
        config=cfg,
        options=ValidationOptions(include_observed_targets=True),
    )
    observed = report[report["gate"] == "observed"]
    assert {
        "traffic_morning_zone_l1",
        "traffic_evening_zone_l1",
        "traffic_morning_fsa_l1",
        "traffic_evening_fsa_l1",
        "hourly_load_profile_corr",
    }.issubset(set(observed["metric"]))

    summary, _ = evaluate_config(cfg, num_people=250, seeds=(121,))
    assert "observed_morning_zone_l1_mean" in summary.columns
    assert "observed_morning_fsa_l1_mean" in summary.columns
