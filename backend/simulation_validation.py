"""
Validation gates for the weekly road-grid mobility simulation.

The checks are intentionally numeric and report-oriented: they do not decide
policy, they expose whether the current run behaves like a plausible mobility,
charging, grid, and road-network model.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
import os
import sys

import numpy as np
import pandas as pd

from mobility_simulator import MobilityConfig, MobilitySimulationEngine


VALIDATION_COLUMNS = ["gate", "metric", "value", "status", "detail"]


@dataclass(frozen=True)
class ValidationOptions:
    require_real_grid: bool = False
    require_real_chargers: bool = False
    min_real_chargers: int = 1_000
    max_snap_p95_m: float = 2_000.0
    max_circuity_p90: float = 2.5
    min_seed_pass_rate: float = 1.0
    include_observed_targets: bool = False
    include_repeat_week: bool = False
    edge_flow_detail: str = "full"
    require_sample_evidence: bool = False


def validate_weekly_simulation(
    num_people: int = 2_500,
    seed: int = 42,
    config: MobilityConfig | None = None,
    options: ValidationOptions | None = None,
    precomputed_plan: tuple[pd.DataFrame, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    cfg = config or MobilityConfig(ev_probability=0.20)
    opts = options or ValidationOptions()
    engine = MobilitySimulationEngine(cfg)
    if precomputed_plan is None:
        people, itinerary = engine.generate_weekly_itinerary(num_people=num_people, seed=seed)
    else:
        people, itinerary = precomputed_plan
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=seed + 1)
    hourly = engine.aggregate_charge_events(charges)
    grid_load = engine.aggregate_weekly_grid_load(hourly)
    if opts.edge_flow_detail == "full":
        edge_flows = engine.aggregate_edge_flows(legs)
    elif opts.edge_flow_detail == "fsa":
        edge_flows = engine.aggregate_fsa_corridor_flows(legs)
    else:
        raise ValueError("edge_flow_detail must be 'full' or 'fsa'.")

    rows: list[dict[str, object]] = []
    _hackathon_data_mapping_checks(rows, engine)
    _road_graph_checks(rows, engine, itinerary, edge_flows, opts)
    if getattr(cfg, "itinerary_model", "template") == "intraday":
        _intraday_route_plan_checks(rows, people, itinerary)
    _mobility_checks(rows, people, legs)
    _purpose_zone_alignment_checks(rows, legs, opts)
    _charging_checks(rows, cfg, people, legs, charges, opts)
    _patch_checks(rows, people, charges, opts)
    _charger_catalog_mapping_checks(rows, engine, charges, opts)
    _private_charger_mapping_checks(rows, engine, charges, opts)
    _charger_concentration_checks(rows, engine, charges, opts)
    _temporal_consistency_checks(rows, legs, charges)
    if opts.include_repeat_week:
        _repeat_week_stability_checks(rows, engine, cfg, people, itinerary, legs, charges, seed)
    _load_checks(rows, hourly, charges)
    _grid_load_checks(rows, grid_load, hourly, cfg)
    if opts.include_observed_targets:
        _observed_target_checks(rows, artifacts={
            "people": people,
            "itinerary": itinerary,
            "legs": legs,
            "charges": charges,
            "hourly": hourly,
            "grid_load": grid_load,
            "edge_flows": edge_flows,
        })

    report = pd.DataFrame(rows, columns=VALIDATION_COLUMNS)
    artifacts = {
        "people": people,
        "itinerary": itinerary,
        "legs": legs,
        "charges": charges,
        "hourly": hourly,
        "grid_load": grid_load,
        "edge_flows": edge_flows,
    }
    return report, artifacts


def validate_multi_seed(
    num_people: int = 1_000,
    seeds: tuple[int, ...] = (101, 202, 303),
    config: MobilityConfig | None = None,
    options: ValidationOptions | None = None,
    jobs: int = 1,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Run the full validation report across seeds and summarize stability."""
    opts = options or ValidationOptions()
    reports = []
    last_artifacts: dict[str, pd.DataFrame] = {}
    if _should_parallelize(jobs, seeds):
        last_seed = int(seeds[-1])
        worker_args = [(num_people, int(seed), config, opts, int(seed) == last_seed) for seed in seeds]
        with _validation_process_pool(min(jobs, len(seeds))) as pool:
            results = list(pool.map(_validate_weekly_seed_worker, worker_args))
    else:
        results = [
            (int(seed), *validate_weekly_simulation(num_people=num_people, seed=int(seed), config=config, options=opts))
            for seed in seeds
        ]
    for seed, report, artifacts in results:
        seeded = report.copy()
        seeded.insert(0, "seed", seed)
        reports.append(seeded)
        last_artifacts = artifacts

    all_reports = pd.concat(reports, ignore_index=True) if reports else pd.DataFrame(columns=["seed", *VALIDATION_COLUMNS])
    rows: list[dict[str, object]] = []
    if all_reports.empty:
        _add(rows, "suite", "seeds_nonempty", 0, False, "")
    else:
        seed_pass = all_reports.groupby("seed")["status"].apply(lambda values: bool((values == "PASS").all()))
        pass_rate = float(seed_pass.mean())
        _add(rows, "suite", "seed_pass_rate_pct", round(pass_rate * 100, 2), pass_rate >= opts.min_seed_pass_rate, str(seed_pass.to_dict()))
        broken = all_reports[all_reports["status"] != "PASS"]
        _add(rows, "suite", "broken_gate_count", int(len(broken)), broken.empty, broken[["seed", "gate", "metric", "value"]].to_dict("records")[:10] if not broken.empty else "")
    suite_report = pd.DataFrame(rows, columns=VALIDATION_COLUMNS)
    return suite_report, {"seed_reports": all_reports, **last_artifacts}


def validate_sensitivity_scenarios(
    num_people: int = 500,
    seed: int = 515,
    config: MobilityConfig | None = None,
    options: ValidationOptions | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Validate directional behavior under stress scenarios.

    Stress scenarios may intentionally break baseline plausibility bands; this
    function checks monotonic response instead of requiring every standard gate
    to pass.
    """
    from model_calibration import extract_fit_metrics

    base = config or MobilityConfig(ev_probability=0.20)
    scenarios = {
        "base": base,
        "low_initial_soc": replace(base, initial_soc_alpha=2.0, initial_soc_beta=5.0),
        "low_home_work_access": replace(base, home_charger_probability=min(base.home_charger_probability, 0.35), work_charger_probability=min(base.work_charger_probability, 0.10)),
        "high_home_work_access": replace(
            base,
            home_charger_probability=max(base.home_charger_probability, 0.95),
            work_charger_probability=max(base.work_charger_probability, 0.80),
            home_public_charger_access=max(base.home_public_charger_access, 0.60),
            work_public_charger_access=max(base.work_public_charger_access, 0.60),
        ),
        "higher_reserve": replace(base, reserve_soc=max(base.reserve_soc, 0.22)),
        "cold_inefficient": replace(base, ev_efficiency_kwh_per_km=max(base.ev_efficiency_kwh_per_km, 0.24)),
    }
    metric_rows = []
    for name, cfg in scenarios.items():
        report, artifacts = validate_weekly_simulation(num_people=num_people, seed=seed, config=cfg, options=options)
        metrics = extract_fit_metrics(artifacts, cfg)
        metrics["weekday_patches_per_ev_week"] = _weekday_patches_per_ev(artifacts)
        broken = report[report["status"] != "PASS"]
        reserve_rows = report[report["metric"].isin(["reserve_violations", "pre_departure_reserve_violations"])]
        metric_rows.append({
            "scenario": name,
            "break_count": int(len(broken)),
            "max_break_count": int(len(broken)),
            "break_metrics": _broken_gate_summary(broken),
            "reserve_ok": bool((reserve_rows["status"] == "PASS").all()),
            **metrics,
        })
    metrics_df = pd.DataFrame(metric_rows).set_index("scenario")
    return _sensitivity_report_from_metrics(metrics_df), metrics_df.reset_index()


def validate_sensitivity_scenarios_multi_seed(
    num_people: int = 500,
    seeds: tuple[int, ...] = (515, 616, 717),
    config: MobilityConfig | None = None,
    options: ValidationOptions | None = None,
    jobs: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Validate stress-response direction across multiple random seeds.

    Directional sensitivity is noisy at small sample sizes. This aggregates the
    scenario metrics first, then applies the monotonic checks to the mean
    response instead of one arbitrary seed.
    """
    if not seeds:
        raise ValueError("At least one seed is required for sensitivity validation.")
    if _should_parallelize(jobs, seeds):
        worker_args = [(num_people, int(seed), config, options) for seed in seeds]
        with _validation_process_pool(min(jobs, len(seeds))) as pool:
            per_seed = [metrics.assign(seed=seed) for seed, metrics in pool.map(_validate_sensitivity_seed_worker, worker_args)]
    else:
        per_seed = []
        for seed in seeds:
            _, metrics = validate_sensitivity_scenarios(num_people=num_people, seed=int(seed), config=config, options=options)
            per_seed.append(metrics.assign(seed=int(seed)))
    per_seed_df = pd.concat(per_seed, ignore_index=True)
    numeric_cols = [
        col
        for col in per_seed_df.select_dtypes(include=[np.number]).columns
        if col != "seed"
    ]
    aggregate = per_seed_df.groupby("scenario", sort=False)[numeric_cols].mean()
    aggregate["max_break_count"] = per_seed_df.groupby("scenario", sort=False)["break_count"].max()
    aggregate["break_metrics"] = per_seed_df.groupby("scenario", sort=False).apply(_aggregate_break_metrics).to_numpy()
    aggregate["reserve_ok"] = per_seed_df.groupby("scenario", sort=False)["reserve_ok"].all()
    aggregate["seed_count"] = len(seeds)
    return _sensitivity_report_from_metrics(aggregate), aggregate.reset_index()


def _validate_weekly_seed_worker(args: tuple[int, int, MobilityConfig | None, ValidationOptions, bool]) -> tuple[int, pd.DataFrame, dict[str, pd.DataFrame]]:
    num_people, seed, config, options, keep_artifacts = args
    report, artifacts = validate_weekly_simulation(num_people=num_people, seed=seed, config=config, options=options)
    return seed, report, artifacts if keep_artifacts else {}


def _validate_sensitivity_seed_worker(args: tuple[int, int, MobilityConfig | None, ValidationOptions | None]) -> tuple[int, pd.DataFrame]:
    num_people, seed, config, options = args
    _, metrics = validate_sensitivity_scenarios(num_people=num_people, seed=seed, config=config, options=options)
    return seed, metrics


def _validation_process_pool(max_workers: int) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=max_workers)


def _should_parallelize(jobs: int, seeds: tuple[int, ...]) -> bool:
    if jobs <= 1 or len(seeds) <= 1:
        return False
    main_file = getattr(sys.modules.get("__main__"), "__file__", "")
    if not (bool(main_file) and not str(main_file).startswith("<")):
        return False
    try:
        os.sysconf("SC_SEM_NSEMS_MAX")
    except (OSError, ValueError, PermissionError):
        return False
    return True


def _sensitivity_report_from_metrics(metrics_df: pd.DataFrame) -> pd.DataFrame:
    base_patch = float(metrics_df.loc["base", "patches_per_ev_week"])
    base_charges = float(metrics_df.loc["base", "charges_per_ev_week"])
    rows: list[dict[str, object]] = []
    _add(rows, "sensitivity", "low_initial_soc_patch_rises", round(float(metrics_df.loc["low_initial_soc", "patches_per_ev_week"] - base_patch), 3), bool(metrics_df.loc["low_initial_soc", "patches_per_ev_week"] > base_patch), "")
    _add(rows, "sensitivity", "low_access_patch_rises", round(float(metrics_df.loc["low_home_work_access", "patches_per_ev_week"] - base_patch), 3), bool(metrics_df.loc["low_home_work_access", "patches_per_ev_week"] > base_patch), "")
    _add(rows, "sensitivity", "high_access_patch_falls", round(float(base_patch - metrics_df.loc["high_home_work_access", "patches_per_ev_week"]), 3), bool(metrics_df.loc["high_home_work_access", "patches_per_ev_week"] < base_patch), "")
    _add(rows, "sensitivity", "high_access_weekday_patch_falls", round(float(metrics_df.loc["base", "weekday_patches_per_ev_week"] - metrics_df.loc["high_home_work_access", "weekday_patches_per_ev_week"]), 3), bool(metrics_df.loc["high_home_work_access", "weekday_patches_per_ev_week"] < metrics_df.loc["base", "weekday_patches_per_ev_week"]), "")
    _add(rows, "sensitivity", "higher_reserve_patch_rises", round(float(metrics_df.loc["higher_reserve", "patches_per_ev_week"] - base_patch), 3), bool(metrics_df.loc["higher_reserve", "patches_per_ev_week"] > base_patch), "")
    _add(rows, "sensitivity", "cold_efficiency_patch_rises", round(float(metrics_df.loc["cold_inefficient", "patches_per_ev_week"] - base_patch), 3), bool(metrics_df.loc["cold_inefficient", "patches_per_ev_week"] > base_patch), "")
    _add(rows, "sensitivity", "low_initial_soc_charges_rise", round(float(metrics_df.loc["low_initial_soc", "charges_per_ev_week"] - base_charges), 3), bool(metrics_df.loc["low_initial_soc", "charges_per_ev_week"] > base_charges), "")
    _add(rows, "sensitivity", "stress_reserve_violations_absent", bool(metrics_df["reserve_ok"].all()), bool(metrics_df["reserve_ok"].all()), "")
    if "break_count" in metrics_df:
        base_breaks = float(metrics_df.loc["base", "break_count"])
        _add(rows, "sensitivity", "base_scenario_internal_break_count", round(base_breaks, 3), base_breaks == 0.0, str(metrics_df.loc["base"].get("break_metrics", "")))
    return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)


def _broken_gate_summary(broken: pd.DataFrame) -> str:
    if broken.empty or "metric" not in broken:
        return ""
    counts = broken["metric"].value_counts().sort_index()
    return "; ".join(f"{metric}={int(count)}" for metric, count in counts.items())


def _aggregate_break_metrics(group: pd.DataFrame) -> str:
    parts: list[str] = []
    for row in group.to_dict("records"):
        summary = str(row.get("break_metrics", "") or "")
        if summary:
            parts.append(f"seed {int(row['seed'])}: {summary}")
    return " | ".join(parts)


def _weekday_patches_per_ev(artifacts: dict[str, pd.DataFrame]) -> float:
    people = artifacts.get("people", pd.DataFrame())
    charges = artifacts.get("charges", pd.DataFrame())
    ev_count = max(1, int(people["is_ev"].sum())) if not people.empty and "is_ev" in people else 1
    if charges.empty or not {"event_type", "start_hour_abs"}.issubset(charges.columns):
        return 0.0
    patch = charges[charges["event_type"] == "patch"]
    start = pd.to_numeric(patch["start_hour_abs"], errors="coerce")
    return float((start < 5 * 24).sum() / ev_count)


def _add(rows: list[dict[str, object]], gate: str, metric: str, value: object, status: bool, detail: str = "") -> None:
    rows.append({
        "gate": gate,
        "metric": metric,
        "value": value,
        "status": "PASS" if status else "BREAK",
        "detail": str(detail),
    })


def compute_soc_balance(people: pd.DataFrame, legs: pd.DataFrame, charges: pd.DataFrame, config: MobilityConfig) -> pd.DataFrame:
    """Compute EV initial/final SoC from weekly trip energy and delivered charge energy."""
    ev_people = people[people["is_ev"]].copy()
    if ev_people.empty:
        return pd.DataFrame(columns=["person_id", "initial_soc", "final_soc", "soc_delta"])
    capacity = float(config.battery_capacity_kwh)
    initial = ev_people.set_index("person_id")["initial_soc"].astype(float)
    trip_kwh = legs[legs["is_ev"]].groupby("person_id")["trip_kwh"].sum() if not legs.empty else pd.Series(dtype=float)
    delivered_kwh = charges.groupby("person_id")["energy_delivered_kwh"].sum() if not charges.empty else pd.Series(dtype=float)
    final_soc = (
        initial * capacity
        - trip_kwh.reindex(initial.index, fill_value=0.0)
        + delivered_kwh.reindex(initial.index, fill_value=0.0)
    ).clip(lower=0.0, upper=capacity) / capacity
    return pd.DataFrame({
        "person_id": initial.index,
        "initial_soc": initial.to_numpy(dtype=float),
        "final_soc": final_soc.to_numpy(dtype=float),
        "soc_delta": final_soc.to_numpy(dtype=float) - initial.to_numpy(dtype=float),
    })


def replay_week_from_final_soc(
    engine: MobilitySimulationEngine,
    people: pd.DataFrame,
    itinerary: pd.DataFrame,
    first_week_legs: pd.DataFrame,
    first_week_charges: pd.DataFrame,
    *,
    seed: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replay the same weekly plan using week-one final SoC as week-two initial SoC."""
    balance = compute_soc_balance(people, first_week_legs, first_week_charges, engine.config)
    warm_people = people.copy()
    if not balance.empty:
        final_soc = balance.set_index("person_id")["final_soc"]
        ev_mask = warm_people["is_ev"].astype(bool)
        warm_people.loc[ev_mask, "initial_soc"] = warm_people.loc[ev_mask, "person_id"].map(final_soc).fillna(warm_people.loc[ev_mask, "initial_soc"]).to_numpy()
    replay_legs, replay_charges = engine.simulate_weekly_charging(warm_people, itinerary, seed=seed)
    replay_balance = compute_soc_balance(warm_people, replay_legs, replay_charges, engine.config)
    return warm_people, replay_legs, replay_charges, replay_balance


def _repeat_week_stability_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    cfg: MobilityConfig,
    people: pd.DataFrame,
    itinerary: pd.DataFrame,
    first_week_legs: pd.DataFrame,
    first_week_charges: pd.DataFrame,
    seed: int,
) -> None:
    if people.empty or itinerary.empty:
        _add(rows, "repeat_week", "repeat_week_inputs_nonempty", 0, False, "No people or itinerary rows to replay.")
        return
    warm_people, replay_legs, replay_charges, replay_balance = replay_week_from_final_soc(
        engine,
        people,
        itinerary,
        first_week_legs,
        first_week_charges,
        seed=seed + 2,
    )
    ev_count = int(warm_people["is_ev"].sum())
    if ev_count == 0 or replay_balance.empty:
        _add(rows, "repeat_week", "repeat_week_ev_sample", ev_count, False, "No EVs available for repeated-week stability check.")
        return

    drift_pct = float(replay_balance["soc_delta"].mean() * 100.0)
    below_reserve = int((replay_balance["final_soc"] < cfg.reserve_soc - 0.01).sum())
    full_pct = float((replay_balance["final_soc"] >= 0.995).mean() * 100.0)
    charges_per_ev = float(len(replay_charges) / max(ev_count, 1))
    patch_per_ev = float((replay_charges["event_type"].eq("patch").sum() if not replay_charges.empty else 0) / max(ev_count, 1))
    reserve_rows: list[dict[str, object]] = []
    _charging_checks(reserve_rows, cfg, warm_people, replay_legs, replay_charges, ValidationOptions())
    reserve_report = pd.DataFrame(reserve_rows)
    reserve_ok = bool(reserve_report[reserve_report["metric"].isin(["reserve_violations", "pre_departure_reserve_violations"])]["status"].eq("PASS").all())
    temporal_rows: list[dict[str, object]] = []
    _temporal_consistency_checks(temporal_rows, replay_legs, replay_charges)
    temporal_ok = bool(pd.DataFrame(temporal_rows)["status"].eq("PASS").all())

    _add(rows, "repeat_week", "repeat_week_ev_sample", ev_count, ev_count >= 30, "Below 30 EVs is too noisy for this stability gate.")
    _add(rows, "repeat_week", "repeat_week_final_soc_mean_drift_pctpt", round(drift_pct, 2), abs(drift_pct) <= 5.0, "")
    _add(rows, "repeat_week", "repeat_week_final_soc_below_reserve", below_reserve, below_reserve == 0, "")
    _add(rows, "repeat_week", "repeat_week_final_soc_at_full_pct", round(full_pct, 2), full_pct < 10.0, "")
    _add(rows, "repeat_week", "repeat_week_charges_per_ev", round(charges_per_ev, 2), 1.5 <= charges_per_ev <= 4.5, "")
    _add(rows, "repeat_week", "repeat_week_patches_per_ev", round(patch_per_ev, 2), patch_per_ev < 0.75, "")
    _add(rows, "repeat_week", "repeat_week_reserve_ok", reserve_ok, reserve_ok, "")
    _add(rows, "repeat_week", "repeat_week_temporal_ok", temporal_ok, temporal_ok, "")


def _road_graph_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    itinerary: pd.DataFrame,
    edge_flows: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    summary = engine.road_network.summary()
    charger_summary = engine.charger_catalog.summary()
    source_ok = summary.source == "osm" if options.require_real_grid else summary.source in {"osm", "fsa_adjacency"}
    _add(rows, "road_graph", "source", summary.source, source_ok, "Use osm for real-road production runs; fsa_adjacency is offline fallback.")
    _add(rows, "road_graph", "nodes", summary.node_count, summary.node_count >= len(engine.base_gdf), "")
    _add(rows, "road_graph", "edges", summary.edge_count, summary.edge_count >= len(engine.base_gdf), "")
    if options.require_real_grid:
        _add(rows, "road_graph", "connected_components", summary.component_count, summary.component_count == 1, "")
        _add(rows, "road_graph", "isolated_nodes", summary.isolated_nodes, summary.isolated_nodes == 0, "")
        _add(rows, "road_graph", "p95_snap_distance_m", round(summary.p95_snap_distance_m, 2), summary.p95_snap_distance_m <= options.max_snap_p95_m, "")
    _add(rows, "road_graph", "unreachable_od_pairs", summary.unreachable_od_pairs, summary.unreachable_od_pairs == 0, "")
    _add(rows, "road_graph", "median_circuity", round(summary.median_circuity, 3), summary.median_circuity >= 1.0, "")
    _add(rows, "road_graph", "p90_circuity", round(summary.p90_circuity, 3), 1.0 <= summary.p90_circuity <= options.max_circuity_p90, "")
    if not itinerary.empty:
        _add(rows, "road_graph", "routed_legs_reachable_pct", round(float(itinerary["reachable_route"].mean()) * 100, 3), bool(itinerary["reachable_route"].all()), "")
        if options.require_real_grid and summary.source == "osm":
            unique_count, nodes_valid, edges_valid = _osm_route_path_validity(
                engine.road_network.graph,
                itinerary["route_path"].astype(str),
            )
            _add(rows, "road_graph", "route_path_unique_count", unique_count, unique_count > 0, "Unique full route paths validated against the OSM graph.")
            _add(rows, "road_graph", "route_path_uses_osm_nodes", nodes_valid, nodes_valid, "All unique full route paths, not just centroids.")
            _add(rows, "road_graph", "route_path_edges_exist", edges_valid, edges_valid, "All unique route edges; allows reverse edge because some FSA centroid snaps use undirected fallback.")
    if edge_flows.empty:
        _add(rows, "road_graph", "edge_flows_nonempty", 0, False, "")
    else:
        _edge_flow_integrity_checks(rows, engine, edge_flows, options)
        total_flow = float(edge_flows["vehicle_count"].sum())
        top_1pct_edges = max(1, int(np.ceil(len(edge_flows) * 0.01)))
        top_share = float(edge_flows.nlargest(top_1pct_edges, "vehicle_count")["vehicle_count"].sum() / total_flow)
        _add(rows, "road_graph", "top_1pct_edge_flow_share_pct", round(top_share * 100, 2), 1.0 <= top_share * 100 <= 80.0, "")
        routed = itinerary[itinerary["route_path"].astype(str).str.contains("|", regex=False)]
        route_km_diff = float(edge_flows["route_km"].sum() - routed["route_km"].sum()) if not routed.empty else 0.0
        route_km_tolerance = max(0.1, float(routed["route_km"].sum()) * 1e-6) if not routed.empty else 0.1
        _add(rows, "road_graph", "edge_flow_route_km_conservation", round(route_km_diff, 6), abs(route_km_diff) <= route_km_tolerance, "")
        _add(rows, "road_graph", "edge_flow_hour_buckets", int(edge_flows[["day", "hour"]].drop_duplicates().shape[0]), edge_flows[["day", "hour"]].drop_duplicates().shape[0] >= 24, "")

    source_mix = dict(charger_summary["source_mix"])
    if options.require_real_chargers:
        charger_ok = int(charger_summary["charger_count"]) >= options.min_real_chargers and any(source in source_mix for source in ("afdc", "osm"))
    else:
        charger_ok = int(charger_summary["charger_count"]) > 0
    _add(rows, "chargers", "charger_count", charger_summary["charger_count"], charger_ok, str(charger_summary["source_mix"]))
    if options.require_real_chargers:
        real_catalog_only = bool(source_mix) and set(source_mix).issubset({"afdc", "osm"})
        _add(rows, "chargers", "real_charger_catalog_only", source_mix, real_catalog_only, "Real-grid validation must not silently use zone-proxy public chargers.")
    if options.require_real_grid:
        public = engine.charger_catalog.public
        snap_cols = {"road_node_id", "road_snap_distance_m"}
        snap_complete = snap_cols.issubset(public.columns) and not public.empty and public[list(snap_cols)].notna().all().all()
        if snap_complete and summary.source == "osm":
            graph_nodes = set(engine.road_network.graph.nodes)
            node_ids = public["road_node_id"].dropna().astype(int)
            nodes_valid = bool(set(node_ids).issubset(graph_nodes))
        else:
            nodes_valid = bool(snap_complete)
        snap_p95 = float(public["road_snap_distance_m"].quantile(0.95)) if snap_complete else float("nan")
        _add(rows, "chargers", "charger_road_node_snap_complete", bool(snap_complete and nodes_valid), bool(snap_complete and nodes_valid), "")
        _add(rows, "chargers", "charger_road_snap_p95_m", round(snap_p95, 2) if np.isfinite(snap_p95) else "nan", bool(snap_complete and snap_p95 <= options.max_snap_p95_m), "")


def _edge_flow_integrity_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    edge_flows: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    required = {"day", "hour", "edge_u", "edge_v", "vehicle_count", "ev_count", "route_km", "fsa", "zone_type"}
    missing = sorted(required - set(edge_flows.columns))
    if missing:
        _add(rows, "road_graph", "edge_flow_required_columns", False, False, missing)
        return
    _add(rows, "road_graph", "edge_flow_required_columns", True, True, "")

    numeric = edge_flows[["vehicle_count", "ev_count", "route_km"]].apply(pd.to_numeric, errors="coerce")
    finite = bool(np.isfinite(numeric.to_numpy(dtype=float)).all())
    nonnegative = bool((numeric >= -1e-9).all().all()) if finite else False
    ev_le_vehicle = bool((numeric["ev_count"] <= numeric["vehicle_count"] + 1e-9).all()) if finite else False
    time_bounds = bool(
        pd.to_numeric(edge_flows["day"], errors="coerce").between(0, 6).all()
        and pd.to_numeric(edge_flows["hour"], errors="coerce").between(0, 23).all()
    )
    fsa_valid = bool(edge_flows["fsa"].astype(str).isin(set(str(fsa) for fsa in engine.fsas)).all())
    zone_valid = bool(edge_flows["zone_type"].astype(str).isin(set(str(zone) for zone in engine.zone_types)).all())

    _add(rows, "road_graph", "edge_flow_numeric_finite", finite, finite, "")
    _add(rows, "road_graph", "edge_flow_counts_nonnegative", nonnegative, nonnegative, "")
    _add(rows, "road_graph", "edge_flow_ev_count_le_vehicle_count", ev_le_vehicle, ev_le_vehicle, "")
    _add(rows, "road_graph", "edge_flow_time_bounds", time_bounds, time_bounds, "")
    _add(rows, "road_graph", "edge_flow_fsa_zone_valid", bool(fsa_valid and zone_valid), bool(fsa_valid and zone_valid), "")

    if options.edge_flow_detail == "full" and engine.road_network.source == "osm":
        graph = engine.road_network.graph
        graph_nodes = set(graph.nodes)
        unique_edges = edge_flows[["edge_u", "edge_v"]].drop_duplicates()
        try:
            edge_pairs = [(int(row.edge_u), int(row.edge_v)) for row in unique_edges.itertuples(index=False)]
        except ValueError:
            edge_pairs = []
        nodes_valid = bool(edge_pairs) and all(u in graph_nodes and v in graph_nodes for u, v in edge_pairs)
        edges_valid = bool(edge_pairs) and all(graph.has_edge(u, v) or graph.has_edge(v, u) for u, v in edge_pairs)
        _add(rows, "road_graph", "edge_flow_uses_osm_nodes", nodes_valid, nodes_valid, "")
        _add(rows, "road_graph", "edge_flow_edges_exist", edges_valid, edges_valid, "Allows reverse edge for undirected fallback routes.")


def _osm_route_path_validity(graph: object, route_paths: pd.Series) -> tuple[int, bool, bool]:
    unique_paths = list(dict.fromkeys(str(path) for path in route_paths if "|" in str(path)))
    if not unique_paths:
        return 0, False, False
    graph_nodes = set(graph.nodes)
    nodes_valid = True
    edges_valid = True
    for path in unique_paths:
        try:
            nodes = [int(node) for node in path.split("|") if node]
        except ValueError:
            nodes_valid = False
            edges_valid = False
            continue
        if len(nodes) < 2:
            nodes_valid = False
            edges_valid = False
            continue
        if not set(nodes).issubset(graph_nodes):
            nodes_valid = False
            edges_valid = False
            continue
        if not all(graph.has_edge(u, v) or graph.has_edge(v, u) for u, v in zip(nodes, nodes[1:])):
            edges_valid = False
    return len(unique_paths), nodes_valid, edges_valid


def _hackathon_data_mapping_checks(rows: list[dict[str, object]], engine: MobilitySimulationEngine) -> None:
    """Validate that hackathon spatial/data artifacts are present on the road-grid model."""
    gdf = engine.base_gdf
    fsa_count = int(len(gdf))
    required_cols = {
        "fsa",
        "geometry",
        "zone_type",
        "proxy_capacity_kw",
        "centroid_lat",
        "centroid_lon",
        "home_weight",
        "population_2021",
        "traffic_am_attraction",
        "traffic_pm_attraction",
        "traffic_total_attraction",
    }
    missing = sorted(required_cols - set(gdf.columns))
    known_zones = {"residential", "leisure", "office_park", "retail_hub", "transit_hub"}
    zone_ok = "zone_type" in gdf and gdf["zone_type"].isin(known_zones).all()
    capacity_ok = "proxy_capacity_kw" in gdf and pd.to_numeric(gdf["proxy_capacity_kw"], errors="coerce").gt(0).all()
    centroid_ok = {"centroid_lat", "centroid_lon"}.issubset(gdf.columns) and bool(
        pd.to_numeric(gdf["centroid_lat"], errors="coerce").between(40.0, 46.0).all()
        and pd.to_numeric(gdf["centroid_lon"], errors="coerce").between(-82.0, -76.0).all()
    )
    population_positive = (
        float(pd.to_numeric(gdf.get("population_2021", pd.Series(dtype=float)), errors="coerce").fillna(0.0).gt(0).mean())
        if "population_2021" in gdf
        else 0.0
    )
    traffic_values = pd.to_numeric(gdf.get("traffic_total_attraction", pd.Series(dtype=float)), errors="coerce").fillna(1.0)
    traffic_nondefault = float((traffic_values != 1.0).mean()) if len(traffic_values) else 0.0
    route_shape = getattr(engine.road_network, "route_km", np.empty((0, 0))).shape
    anchors = getattr(engine.road_network, "fsa_node_ids", [])

    _add(rows, "hackathon_data", "fsa_rows", fsa_count, fsa_count >= 200, "Expected full GTA M/L FSA coverage.")
    _add(rows, "hackathon_data", "fsa_codes_unique", bool(gdf["fsa"].is_unique) if "fsa" in gdf else False, "fsa" in gdf and bool(gdf["fsa"].is_unique), "")
    _add(rows, "hackathon_data", "required_columns_present", len(missing) == 0, len(missing) == 0, missing)
    _add(rows, "hackathon_data", "zone_types_valid", bool(zone_ok), bool(zone_ok), "")
    _add(rows, "hackathon_data", "proxy_capacity_positive", bool(capacity_ok), bool(capacity_ok), "")
    _add(rows, "hackathon_data", "centroids_in_gta_bounds", bool(centroid_ok), bool(centroid_ok), "")
    _add(rows, "hackathon_data", "population_positive_pct", round(population_positive * 100.0, 2), population_positive >= 0.95, "")
    _add(rows, "hackathon_data", "traffic_count_join_pct", round(traffic_nondefault * 100.0, 2), traffic_nondefault >= 0.05, "Toronto traffic counts are partial GTA coverage, but should affect a non-trivial FSA subset.")
    _add(rows, "hackathon_data", "road_anchor_per_fsa_count", len(anchors), len(anchors) == fsa_count, "")
    _add(rows, "hackathon_data", "road_anchor_unique_count", len(set(anchors)), len(set(anchors)) == fsa_count, "")
    _add(rows, "hackathon_data", "route_matrix_shape", f"{route_shape[0]}x{route_shape[1]}", route_shape == (fsa_count, fsa_count), "")


PURPOSE_ZONE_ALIGNMENT: dict[str, tuple[set[str], float, int]] = {
    "home": ({"residential"}, 0.65, 50),
    "work": ({"office_park", "retail_hub", "transit_hub"}, 0.50, 50),
    "school": ({"residential", "office_park", "leisure"}, 0.75, 30),
    "retail": ({"retail_hub", "transit_hub", "office_park"}, 0.30, 50),
    "restaurant": ({"retail_hub", "leisure", "office_park", "transit_hub"}, 0.35, 30),
    "bar_nightlife": ({"retail_hub", "leisure", "transit_hub"}, 0.35, 15),
    "leisure": ({"leisure", "retail_hub", "residential"}, 0.75, 50),
    "errand": ({"retail_hub", "residential", "office_park"}, 0.45, 30),
    "transit_hub": ({"transit_hub", "retail_hub", "office_park"}, 0.40, 30),
}


def _purpose_zone_alignment_checks(rows: list[dict[str, object]], itinerary: pd.DataFrame, options: ValidationOptions | None = None) -> None:
    options = options or ValidationOptions()
    if itinerary.empty or not {"dest_type", "dest_zone_type"}.issubset(itinerary.columns):
        _add(rows, "purpose_zone", "purpose_zone_inputs_nonempty", 0, False, "")
        return
    for purpose, (allowed_zones, threshold, min_sample) in PURPOSE_ZONE_ALIGNMENT.items():
        subset = itinerary[itinerary["dest_type"] == purpose]
        sample = int(len(subset))
        if sample < min_sample:
            _add(
                rows,
                "purpose_zone",
                f"{purpose}_zone_alignment_pct",
                "sample_insufficient",
                not options.require_sample_evidence,
                f"sample={sample}, min_sample={min_sample}, allowed={sorted(allowed_zones)}",
            )
            continue
        aligned = float(subset["dest_zone_type"].isin(allowed_zones).mean())
        _add(
            rows,
            "purpose_zone",
            f"{purpose}_zone_alignment_pct",
            round(aligned * 100.0, 2),
            aligned >= threshold,
            f"sample={sample}, threshold={threshold:.2f}, allowed={sorted(allowed_zones)}",
        )


def _intraday_route_plan_checks(rows: list[dict[str, object]], people: pd.DataFrame, itinerary: pd.DataFrame) -> None:
    required = {
        "person_id", "day", "origin_idx", "dest_idx", "origin_activity", "dest_type",
        "depart_hour_abs", "arrival_hour_abs", "planned_arrival_hour_abs",
        "schedule_delay_min", "dwell_before_h", "route_km", "travel_time_h",
        "reachable_route",
    }
    if itinerary.empty:
        _add(rows, "intraday_plan", "intraday_itinerary_nonempty", 0, False, "")
        return
    missing = sorted(required - set(itinerary.columns))
    _add(rows, "intraday_plan", "intraday_required_columns", len(missing) == 0, len(missing) == 0, missing)
    if missing:
        return

    ordered = itinerary.sort_values(["person_id", "depart_hour_abs"]).copy()
    depart = pd.to_numeric(ordered["depart_hour_abs"], errors="coerce")
    arrival = pd.to_numeric(ordered["arrival_hour_abs"], errors="coerce")
    travel = pd.to_numeric(ordered["travel_time_h"], errors="coerce")
    dwell = pd.to_numeric(ordered["dwell_before_h"], errors="coerce")
    route_km = pd.to_numeric(ordered["route_km"], errors="coerce")
    time_bounds = bool((depart >= 0).all() and (arrival >= depart).all() and (arrival <= 168.0).all())
    duration_error_min = float(((arrival - depart - travel).abs() * 60.0).max()) if len(ordered) else 0.0
    home_closed = ordered.groupby(["person_id", "day"]).tail(1)
    home_closure = float((home_closed["dest_type"] == "home").mean()) if len(home_closed) else 0.0
    first_by_day = ordered.groupby(["person_id", "day"]).head(1)
    person_home = people.set_index("person_id")["home_idx"] if "home_idx" in people else pd.Series(dtype=float)
    first_home_match = first_by_day.apply(lambda row: int(row["origin_idx"]) == int(person_home.get(row["person_id"], -999)), axis=1)
    first_home_pct = float(first_home_match.mean()) if len(first_home_match) else 0.0
    continuity_ok = _intraday_continuity_ok(ordered)
    stop_counts = ordered.groupby(["person_id", "day"]).size()
    stop_count_unique = int(stop_counts.nunique()) if len(stop_counts) else 0
    work_arrivals = ordered[ordered["dest_type"] == "work"]["arrival_hour_abs"] % 24
    school_arrivals = ordered[ordered["dest_type"] == "school"]["arrival_hour_abs"] % 24
    work_after_19_pct = float((work_arrivals > 19.0).mean() * 100.0) if len(work_arrivals) else 0.0
    school_after_12_pct = float((school_arrivals > 12.0).mean() * 100.0) if len(school_arrivals) else 0.0
    retail_after_21_pct = float(((ordered[ordered["dest_type"] == "retail"]["arrival_hour_abs"] % 24) > 21.0).mean() * 100.0) if (ordered["dest_type"] == "retail").any() else 0.0
    bar_rows = ordered[ordered["dest_type"] == "bar_nightlife"]
    bar_late_valid = _bar_returns_before_activity_cutoff(ordered) if len(bar_rows) else True
    route_p50 = float(route_km.median())
    route_p90 = float(route_km.quantile(0.90))
    leg_counts = ordered.groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    weekly_km = ordered.groupby("person_id")["route_km"].sum().reindex(people["person_id"], fill_value=0)
    work_repeat = _repeat_destination_share(ordered, "work")
    school_repeat = _repeat_destination_share(ordered, "school")

    _add(rows, "intraday_plan", "intraday_time_bounds", bool(time_bounds), bool(time_bounds), "")
    _add(rows, "intraday_plan", "intraday_duration_consistency_max_min", round(duration_error_min, 4), duration_error_min <= 0.08, "")
    _add(rows, "intraday_plan", "intraday_nonnegative_dwell", bool((dwell >= -1e-9).all()), bool((dwell >= -1e-9).all()), "")
    _add(rows, "intraday_plan", "intraday_active_day_home_closure_pct", round(home_closure * 100.0, 3), home_closure >= 0.995, "")
    _add(rows, "intraday_plan", "intraday_first_leg_starts_home_pct", round(first_home_pct * 100.0, 3), first_home_pct >= 0.995, "")
    _add(rows, "intraday_plan", "intraday_leg_continuity", bool(continuity_ok), bool(continuity_ok), "")
    _add(rows, "intraday_plan", "intraday_stop_count_variety", stop_count_unique, stop_count_unique >= 3, "")
    _add(rows, "intraday_plan", "intraday_legs_per_person_week_median", round(float(leg_counts.median()), 2), 9 <= leg_counts.median() <= 18, "")
    _add(rows, "intraday_plan", "intraday_weekly_km_person_median", round(float(weekly_km.median()), 2), 120 <= weekly_km.median() <= 380, "")
    _add(rows, "intraday_plan", "intraday_route_km_p50_p90", f"{route_p50:.2f}/{route_p90:.2f}", 4 <= route_p50 <= 28 and 25 <= route_p90 <= 100, "")
    _add(rows, "intraday_plan", "intraday_work_after_19_pct", round(work_after_19_pct, 3), work_after_19_pct <= 1.0, "")
    _add(rows, "intraday_plan", "intraday_school_after_12_pct", round(school_after_12_pct, 3), school_after_12_pct <= 1.0, "")
    _add(rows, "intraday_plan", "intraday_retail_after_21_pct", round(retail_after_21_pct, 3), retail_after_21_pct <= 10.0, "")
    _add(rows, "intraday_plan", "intraday_bar_returns_before_4am", bool(bar_late_valid), bool(bar_late_valid), "")
    _add(rows, "intraday_plan", "intraday_work_destination_repeatability_pct", round(work_repeat * 100, 2), work_repeat >= 0.90, "")
    _add(rows, "intraday_plan", "intraday_school_destination_repeatability_pct", round(school_repeat * 100, 2), school_repeat >= 0.90, "")


def _intraday_continuity_ok(ordered: pd.DataFrame) -> bool:
    for _, trips in ordered.groupby("person_id", sort=False):
        dest = trips["dest_idx"].to_numpy(dtype=int)
        origin = trips["origin_idx"].to_numpy(dtype=int)
        if len(trips) > 1 and not bool((origin[1:] == dest[:-1]).all()):
            return False
        if bool((trips["depart_hour_abs"].to_numpy()[1:] + 1e-9 < trips["arrival_hour_abs"].to_numpy()[:-1]).any()):
            return False
    return True


def _bar_returns_before_activity_cutoff(ordered: pd.DataFrame) -> bool:
    for _, trips in ordered.groupby("person_id", sort=False):
        trips = trips.sort_values("depart_hour_abs").reset_index(drop=True)
        for idx, row in trips[trips["dest_type"] == "bar_nightlife"].iterrows():
            later = trips.iloc[idx + 1:]
            home = later[later["dest_type"] == "home"]
            if home.empty:
                return False
            arrival = float(home.iloc[0]["arrival_hour_abs"])
            service_start = np.floor((float(row["depart_hour_abs"]) - 4.0) / 24.0) * 24.0 + 4.0
            cutoff = min(service_start + 24.0, 168.0)
            if arrival > cutoff + 1e-9:
                return False
    return True


def _charger_concentration_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    charges: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    public_sources = ["afdc", "osm"] if options.require_real_chargers else ["afdc", "osm", "zone_proxy"]
    if charges.empty or "charger_source" not in charges:
        _add(rows, "charger_concentration", "public_charge_events", 0, not options.require_sample_evidence, "No sampled public charge events.")
        return

    public = charges[
        charges["charger_source"].isin(public_sources)
        & (pd.to_numeric(charges.get("energy_delivered_kwh", pd.Series(dtype=float)), errors="coerce").fillna(0.0) > 0)
    ]
    public_event_count = int(len(public))
    if public_event_count < 30:
        _add(rows, "charger_concentration", "public_charge_events", public_event_count, not options.require_sample_evidence, "Sample-insufficient for concentration gates.")
        return

    energy_by_charger = public.groupby("charger_id")["energy_delivered_kwh"].sum().sort_values(ascending=False)
    total_energy = float(energy_by_charger.sum())
    used_count = int(len(energy_by_charger))
    catalog_count = int(len(engine.charger_catalog.public)) if hasattr(engine, "charger_catalog") else 0
    top_1_count = max(1, int(np.ceil(used_count * 0.01)))
    top_10_count = max(1, int(np.ceil(used_count * 0.10)))
    top_1_share = float(energy_by_charger.head(top_1_count).sum() / total_energy) if total_energy > 0 else 1.0
    top_10_share = float(energy_by_charger.head(top_10_count).sum() / total_energy) if total_energy > 0 else 1.0
    min_used = min(20, max(8, int(np.ceil(public_event_count * 0.25))))
    catalog_used_pct = float(used_count / catalog_count * 100.0) if catalog_count > 0 else 0.0

    _add(rows, "charger_concentration", "public_charge_events", public_event_count, public_event_count >= 30, "")
    _add(rows, "charger_concentration", "public_chargers_used", used_count, used_count >= min_used, f"min_used={min_used}")
    _add(rows, "charger_concentration", "public_catalog_used_pct", round(catalog_used_pct, 2), catalog_used_pct > 0.0, f"catalog_count={catalog_count}")
    _add(rows, "charger_concentration", "top_1pct_used_public_charger_energy_share_pct", round(top_1_share * 100.0, 2), top_1_share <= 0.25, "")
    _add(rows, "charger_concentration", "top_10pct_used_public_charger_energy_share_pct", round(top_10_share * 100.0, 2), top_10_share <= 0.50, "")


def _charger_catalog_mapping_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    charges: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    public_sources = {"afdc", "osm", "zone_proxy"}
    required_charge_cols = {"charger_id", "charger_source", "fsa", "charger_lat", "charger_lon", "charger_kw"}
    if charges.empty:
        _add(rows, "chargers", "public_charge_ids_in_catalog_pct", "no_charge_events", not options.require_sample_evidence, "")
        return
    if not required_charge_cols.issubset(charges.columns):
        missing = sorted(required_charge_cols - set(charges.columns))
        _add(rows, "chargers", "public_charge_ids_in_catalog_pct", "missing_columns", False, f"missing={missing}")
        return

    public = charges[charges["charger_source"].isin(public_sources)].copy()
    if public.empty:
        _add(rows, "chargers", "public_charge_ids_in_catalog_pct", "no_public_events", not options.require_sample_evidence, "")
        return

    catalog = engine.charger_catalog.public.copy()
    required_catalog_cols = {"charger_id", "source", "fsa", "lat", "lon", "charger_kw"}
    if catalog.empty or not required_catalog_cols.issubset(catalog.columns):
        missing = sorted(required_catalog_cols - set(catalog.columns))
        _add(rows, "chargers", "public_charge_ids_in_catalog_pct", "catalog_unavailable", False, f"missing={missing}")
        return

    public = public.reset_index(drop=True)
    catalog = catalog.drop_duplicates(subset=["charger_id"]).reset_index(drop=True)
    joined = public.merge(
        catalog[["charger_id", "source", "fsa", "lat", "lon", "charger_kw", *([ "road_node_id" ] if "road_node_id" in catalog.columns else [])]],
        on="charger_id",
        how="left",
        suffixes=("", "_catalog"),
    )
    membership = joined["source"].notna()
    membership_pct = float(membership.mean() * 100.0)
    source_match = joined["charger_source"].astype(str).eq(joined["source"].astype(str))
    fsa_match = joined["fsa"].astype(str).eq(joined["fsa_catalog"].astype(str))
    lat_match = (pd.to_numeric(joined["charger_lat"], errors="coerce") - pd.to_numeric(joined["lat"], errors="coerce")).abs() <= 1e-6
    lon_match = (pd.to_numeric(joined["charger_lon"], errors="coerce") - pd.to_numeric(joined["lon"], errors="coerce")).abs() <= 1e-6
    power_match = (pd.to_numeric(joined["charger_kw"], errors="coerce") - pd.to_numeric(joined["charger_kw_catalog"], errors="coerce")).abs() <= 1e-6
    attribute_match = membership & source_match & fsa_match & lat_match & lon_match & power_match
    if "road_node_id" in joined.columns and "road_node_id_catalog" in joined.columns:
        event_node = pd.to_numeric(joined["road_node_id"], errors="coerce")
        catalog_node = pd.to_numeric(joined["road_node_id_catalog"], errors="coerce")
        node_match = event_node.isna() | catalog_node.isna() | event_node.eq(catalog_node)
        attribute_match &= node_match
    attribute_pct = float(attribute_match.mean() * 100.0)

    _add(rows, "chargers", "public_charge_ids_in_catalog_pct", round(membership_pct, 2), bool(membership.all()), "")
    _add(rows, "chargers", "public_charge_catalog_attributes_match_pct", round(attribute_pct, 2), bool(attribute_match.all()), "")


def _private_charger_mapping_checks(
    rows: list[dict[str, object]],
    engine: MobilitySimulationEngine,
    charges: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    required = {
        "charger_id", "charger_source", "fsa", "zone_type", "charger_lat", "charger_lon", "charger_kw",
        "detour_km", "origin_fsa", "origin_zone_type", "origin_activity", "origin_idx", "road_node_id",
    }
    if charges.empty:
        _add(rows, "chargers", "private_charge_mapping_events", "no_charge_events", not options.require_sample_evidence, "")
        return
    if not required.issubset(charges.columns):
        missing = sorted(required - set(charges.columns))
        _add(rows, "chargers", "private_charge_mapping_events", "missing_columns", False, f"missing={missing}")
        return

    private = charges[charges["charger_source"] == "private"].copy()
    if private.empty:
        _add(rows, "chargers", "private_charge_mapping_events", 0, not options.require_sample_evidence, "No sampled private charge events.")
        return

    base = engine.base_gdf[["fsa", "zone_type", "centroid_lat", "centroid_lon"]].copy()
    base["fsa"] = base["fsa"].astype(str)
    private = private.reset_index(drop=True)
    joined = private.merge(base, left_on="origin_fsa", right_on="fsa", how="left", suffixes=("", "_origin"))
    valid_activities = joined["origin_activity"].astype(str).isin(["home", "work", "school"])
    expected_id = "private_" + joined["origin_activity"].astype(str) + "_" + joined["origin_fsa"].astype(str)
    id_match = joined["charger_id"].astype(str).eq(expected_id)
    fsa_match = joined["fsa"].astype(str).eq(joined["origin_fsa"].astype(str))
    zone_match = joined["zone_type"].astype(str).eq(joined["origin_zone_type"].astype(str))
    origin_zone_match = joined["zone_type_origin"].astype(str).eq(joined["origin_zone_type"].astype(str))
    lat_match = (pd.to_numeric(joined["charger_lat"], errors="coerce") - pd.to_numeric(joined["centroid_lat"], errors="coerce")).abs() <= 1e-6
    lon_match = (pd.to_numeric(joined["charger_lon"], errors="coerce") - pd.to_numeric(joined["centroid_lon"], errors="coerce")).abs() <= 1e-6
    power_match = (pd.to_numeric(joined["charger_kw"], errors="coerce") - 7.0).abs() <= 1e-9
    detour_match = pd.to_numeric(joined["detour_km"], errors="coerce").abs() <= 1e-9
    graph_nodes = set(engine.road_network.graph.nodes)
    road_node_valid = pd.to_numeric(joined["road_node_id"], errors="coerce").dropna().astype(int).isin(graph_nodes)
    all_road_nodes_valid = len(road_node_valid) == len(joined) and bool(road_node_valid.all())

    location_match = valid_activities & id_match & fsa_match & zone_match & origin_zone_match & lat_match & lon_match
    charging_match = power_match & detour_match
    _add(rows, "chargers", "private_charge_mapping_events", int(len(private)), len(private) > 0, "")
    _add(rows, "chargers", "private_charge_location_matches_origin_pct", round(float(location_match.mean() * 100.0), 2), bool(location_match.all()), "")
    _add(rows, "chargers", "private_charge_power_detour_valid_pct", round(float(charging_match.mean() * 100.0), 2), bool(charging_match.all()), "")
    _add(rows, "chargers", "private_charge_road_nodes_valid", bool(all_road_nodes_valid), bool(all_road_nodes_valid), "")


def _mobility_checks(rows: list[dict[str, object]], people: pd.DataFrame, itinerary: pd.DataFrame) -> None:
    if itinerary.empty:
        _add(rows, "mobility", "itinerary_nonempty", 0, False, "No trips generated.")
        return

    leg_counts = itinerary.groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    active_days = itinerary.groupby(["person_id", "day"]).size().reset_index().groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    active_by_type = people.assign(active_days=people["person_id"].map(active_days)).groupby("person_type")["active_days"].mean()
    last_by_day = itinerary.sort_values("depart_hour_abs").groupby(["person_id", "day"]).tail(1)
    home_closure = float((last_by_day["dest_type"] == "home").mean()) if len(last_by_day) else 0.0
    route_p50 = float(itinerary["route_km"].median())
    route_p90 = float(itinerary["route_km"].quantile(0.90))
    weekly_km = itinerary.groupby("person_id")["route_km"].sum().reindex(people["person_id"], fill_value=0)
    commute = itinerary[itinerary["planned_arrival_hour_abs"].notna()]
    delay_p95 = float(commute["schedule_delay_min"].quantile(0.95)) if len(commute) else 0.0
    delay_gt20_pct = float((commute["schedule_delay_min"] > 20.0).mean() * 100.0) if len(commute) else 0.0
    delay_max = float(commute["schedule_delay_min"].max()) if len(commute) else 0.0
    weekday_am = itinerary[(itinerary["day"] < 5) & (itinerary["depart_hour_abs"] % 24 <= 10.5)]
    weekday_am_work_school = float(weekday_am["dest_type"].isin(["work", "school"]).mean()) if len(weekday_am) else 0.0
    weekend = itinerary[itinerary["day"] >= 5]
    weekend_retail_leisure = float(weekend["dest_type"].isin(["retail", "restaurant", "bar_nightlife", "leisure", "errand", "home"]).mean()) if len(weekend) else 0.0
    transit_share_pct = float((itinerary["dest_type"] == "transit_hub").mean() * 100.0)
    other_share_pct = float((itinerary["dest_type"] == "other").mean() * 100.0)
    work_repeat = _repeat_destination_share(itinerary, "work")
    school_repeat = _repeat_destination_share(itinerary, "school")

    _add(rows, "mobility", "legs_per_person_week_median", round(float(leg_counts.median()), 2), 10 <= leg_counts.median() <= 16, "")
    _add(rows, "mobility", "workers_vs_retired_active_days", round(float(active_by_type.get("worker", 0) - active_by_type.get("retired", 0)), 2), active_by_type.get("worker", 0) > active_by_type.get("retired", 0), "")
    _add(rows, "mobility", "active_day_home_closure_pct", round(home_closure * 100, 3), home_closure >= 0.995, "")
    _add(rows, "mobility", "route_km_p50_p90", f"{route_p50:.2f}/{route_p90:.2f}", 5 <= route_p50 <= 25 and 40 <= route_p90 <= 90, "")
    _add(rows, "mobility", "weekly_km_person_median", round(float(weekly_km.median()), 2), 150 <= weekly_km.median() <= 350, "")
    _add(rows, "mobility", "arrive_by_delay_p95_min", round(delay_p95, 2), delay_p95 < 20.0, "")
    _add(rows, "mobility", "arrive_by_delay_gt20_pct", round(delay_gt20_pct, 2), delay_gt20_pct < 2.5, "")
    _add(rows, "mobility", "arrive_by_delay_max_min", round(delay_max, 2), delay_max < 90.0, "Tail metric; patch charging may delay rare low-SoC trips.")
    _add(rows, "mobility", "weekday_am_work_school_share_pct", round(weekday_am_work_school * 100, 2), weekday_am_work_school >= 0.45, "")
    _add(rows, "mobility", "weekend_retail_leisure_home_share_pct", round(weekend_retail_leisure * 100, 2), weekend_retail_leisure >= 0.85, "")
    _add(rows, "mobility", "transit_hub_destination_share_pct", round(transit_share_pct, 2), 0.3 <= transit_share_pct <= 3.0, "")
    _add(rows, "mobility", "other_destination_share_pct", round(other_share_pct, 2), 0.3 <= other_share_pct <= 2.5, "")
    _add(rows, "mobility", "work_destination_repeatability_pct", round(work_repeat * 100, 2), work_repeat >= 0.90, "")
    _add(rows, "mobility", "school_destination_repeatability_pct", round(school_repeat * 100, 2), school_repeat >= 0.90, "")


def _charging_checks(
    rows: list[dict[str, object]],
    cfg: MobilityConfig,
    people: pd.DataFrame,
    legs: pd.DataFrame,
    charges: pd.DataFrame,
    options: ValidationOptions,
) -> None:
    ev_people = people[people["is_ev"]]
    ev_count = max(1, len(ev_people))
    ev_legs = legs[legs["is_ev"]]
    if ev_legs.empty:
        _add(rows, "charging", "ev_legs_nonempty", 0, False, "")
        return

    reserve_violations = int((ev_legs["soc_after"] < cfg.reserve_soc - 0.002).sum())
    required_soc = ev_legs["trip_kwh"] / cfg.battery_capacity_kwh + cfg.reserve_soc
    pre_depart_violations = int((ev_legs["soc_before"] + 0.002 < required_soc).sum())
    near_reserve = float((ev_legs["soc_after"] < cfg.reserve_soc + 0.05).mean())
    full_departures = float((ev_legs["soc_before"] >= 0.995).mean())
    leg_soc_error = float((ev_legs["soc_before"] - ev_legs["soc_after"] - ev_legs["trip_kwh"] / cfg.battery_capacity_kwh).abs().max())
    soc_balance = compute_soc_balance(people, legs, charges, cfg)
    final_soc_drift_pct = float(soc_balance["soc_delta"].mean() * 100.0) if not soc_balance.empty else np.nan
    final_soc_below_reserve = int((soc_balance["final_soc"] < cfg.reserve_soc - 0.01).sum()) if not soc_balance.empty else 0
    final_soc_at_full_pct = float((soc_balance["final_soc"] >= 0.995).mean() * 100.0) if not soc_balance.empty else 0.0
    charges_per_ev = len(charges) / ev_count
    patch_events = charges[charges["event_type"] == "patch"]
    patch_per_ev = len(patch_events) / ev_count
    forced_per_ev = int((charges["patch_type"] == "forced_origin_public").sum()) / ev_count
    geocode_cols = ["charger_id", "charger_lat", "charger_lon", "charger_source", "road_node_id", "road_snap_distance_m"]
    context_cols = ["origin_fsa", "origin_zone_type", "origin_activity", "origin_idx"]
    source_ok = charges.empty or charges[geocode_cols].notna().all().all()
    context_ok = charges.empty or (
        charges[context_cols].notna().all().all()
        and charges["origin_fsa"].astype(str).str.len().gt(0).all()
        and (charges["origin_idx"].astype(int) >= 0).all()
    )
    detour_ok = charges.empty or (pd.to_numeric(charges["detour_km"], errors="coerce").fillna(-1.0) >= 0).all()
    charge_event_energy_error, charge_max_duration_violations, charge_soc_bounds_ok, charge_target_soc_ok = _charge_event_accounting_metrics(charges)
    charge_chronology_soc_error = _charge_chronology_soc_error(people, legs, charges, cfg)
    public_charges = charges[charges["charger_source"].isin(["afdc", "osm", "zone_proxy"])]
    public_detour_p95 = float(public_charges["detour_km"].quantile(0.95)) if len(public_charges) else float("nan")
    public_detour_sufficient = len(public_charges) >= 30
    if options.require_real_chargers:
        public_sources_real = public_charges.empty or public_charges["charger_source"].isin(["afdc", "osm"]).all()
        _add(rows, "charging", "public_charge_sources_real", bool(public_sources_real), bool(public_sources_real), "")

    _add(rows, "charging", "reserve_violations", reserve_violations, reserve_violations == 0, "")
    _add(rows, "charging", "pre_departure_reserve_violations", pre_depart_violations, pre_depart_violations == 0, "")
    _add(rows, "charging", "near_reserve_pct", round(near_reserve * 100, 2), near_reserve < 0.12, "")
    _add(rows, "charging", "full_departure_soc_pct", round(full_departures * 100, 2), full_departures < 0.45, "")
    _add(rows, "charging", "leg_soc_energy_error_max", round(leg_soc_error, 4), leg_soc_error < 0.006, "")
    _add(rows, "charging", "final_soc_mean_drift_pctpt", round(final_soc_drift_pct, 2), abs(final_soc_drift_pct) <= 8.0, "")
    _add(rows, "charging", "final_soc_below_reserve", final_soc_below_reserve, final_soc_below_reserve == 0, "")
    _add(rows, "charging", "final_soc_at_full_pct", round(final_soc_at_full_pct, 2), final_soc_at_full_pct < 10.0, "")
    _add(rows, "charging", "charges_per_ev_week", round(charges_per_ev, 2), 1.5 <= charges_per_ev <= 4.5, "")
    _add(rows, "charging", "patches_per_ev_week", round(patch_per_ev, 2), patch_per_ev < 0.75, "")
    _add(rows, "charging", "forced_origin_public_per_ev_week", round(forced_per_ev, 3), forced_per_ev < 0.05, "")
    _add(rows, "charging", "charger_geocoding_complete", bool(source_ok), bool(source_ok), "")
    _add(rows, "charging", "charge_origin_context_complete", bool(context_ok), bool(context_ok), "")
    _add(rows, "charging", "charge_detour_nonnegative", bool(detour_ok), bool(detour_ok), "")
    _add(rows, "charging", "charge_event_energy_duration_error_max_kwh", round(charge_event_energy_error, 6), charge_event_energy_error <= 1e-6, "")
    _add(rows, "charging", "charge_event_max_duration_violations", charge_max_duration_violations, charge_max_duration_violations == 0, "")
    _add(rows, "charging", "charge_event_soc_bounds", bool(charge_soc_bounds_ok), bool(charge_soc_bounds_ok), "")
    _add(rows, "charging", "charge_event_soc_target_consistency", bool(charge_target_soc_ok), bool(charge_target_soc_ok), "")
    _add(rows, "charging", "charge_chronology_soc_error_max", round(charge_chronology_soc_error, 6), charge_chronology_soc_error <= 0.006, "Replays visible charge events and legs by person; tolerance allows rounded leg SoC columns.")
    _add(
        rows,
        "charging",
        "public_charge_detour_p95_km",
        round(public_detour_p95, 2) if np.isfinite(public_detour_p95) else "nan",
        (public_detour_sufficient and public_detour_p95 <= 20.0) or (not public_detour_sufficient and not options.require_sample_evidence),
        "" if public_detour_sufficient else f"Strict gate deferred because only {len(public_charges)} public charge events were sampled.",
    )


def _charge_event_accounting_metrics(charges: pd.DataFrame) -> tuple[float, int, bool, bool]:
    if charges.empty:
        return 0.0, 0, True, True
    duration = pd.to_numeric(charges["duration_h"], errors="coerce")
    charger_kw = pd.to_numeric(charges["charger_kw"], errors="coerce")
    delivered = pd.to_numeric(charges["energy_delivered_kwh"], errors="coerce")
    max_duration = pd.to_numeric(charges["max_duration_h"], errors="coerce")
    soc_after = pd.to_numeric(charges["soc_after_charge"], errors="coerce")
    target_soc = pd.to_numeric(charges["target_soc"], errors="coerce")

    energy_error = float((duration * charger_kw - delivered).abs().max())
    max_duration_violations = int((duration > max_duration + 1e-6).sum())
    soc_bounds_ok = bool(
        soc_after.notna().all()
        and delivered.notna().all()
        and duration.notna().all()
        and charger_kw.notna().all()
        and max_duration.notna().all()
        and (soc_after >= -1e-9).all()
        and (soc_after <= 1.0 + 1e-9).all()
        and (delivered >= -1e-9).all()
        and (duration >= -1e-9).all()
        and (charger_kw > 0).all()
        and (max_duration >= -1e-9).all()
    )
    target_soc_ok = bool(
        target_soc.notna().all()
        and (target_soc >= -1e-9).all()
        and (target_soc <= 1.0 + 1e-9).all()
        and (soc_after <= target_soc.clip(upper=1.0) + 1e-6).all()
    )
    return energy_error, max_duration_violations, soc_bounds_ok, target_soc_ok


def _charge_chronology_soc_error(people: pd.DataFrame, legs: pd.DataFrame, charges: pd.DataFrame, cfg: MobilityConfig) -> float:
    if people.empty or legs.empty:
        return 0.0
    people_lookup = people[people["is_ev"]].set_index("person_id")
    if people_lookup.empty:
        return 0.0
    legs_by_person = {person_id: group for person_id, group in legs[legs["is_ev"]].groupby("person_id", sort=False)}
    charges_by_person = {person_id: group for person_id, group in charges.groupby("person_id", sort=False)} if not charges.empty else {}
    max_error = 0.0
    capacity = float(cfg.battery_capacity_kwh)

    for person_id, person in people_lookup.iterrows():
        events: list[tuple[float, int, pd.Series]] = []
        for _, charge in charges_by_person.get(person_id, pd.DataFrame()).iterrows():
            events.append((float(charge["start_hour_abs"]), 0, charge))
        for _, leg in legs_by_person.get(person_id, pd.DataFrame()).iterrows():
            events.append((float(leg["depart_hour_abs"]), 1, leg))
        current_soc = float(person["initial_soc"])
        for _, kind, row in sorted(events, key=lambda item: (item[0], item[1])):
            if kind == 0:
                current_soc = min(1.0, current_soc + float(row["energy_delivered_kwh"]) / capacity)
                max_error = max(max_error, abs(current_soc - float(row["soc_after_charge"])))
            else:
                max_error = max(max_error, abs(current_soc - float(row["soc_before"])))
                current_soc = max(0.0, current_soc - float(row["trip_kwh"]) / capacity)
                max_error = max(max_error, abs(current_soc - float(row["soc_after"])))
    return float(max_error)


def _patch_checks(rows: list[dict[str, object]], people: pd.DataFrame, charges: pd.DataFrame, options: ValidationOptions) -> None:
    patch = charges[charges["event_type"] == "patch"]
    if patch.empty:
        ev_count = int(people["is_ev"].sum()) if "is_ev" in people else 0
        enough_ev_sample = ev_count >= 50
        _add(
            rows,
            "patch",
            "patch_events_nonempty",
            0,
            not enough_ev_sample,
            "Sample-insufficient for rare patch events." if not enough_ev_sample else "Stress/base validation with enough EVs should create some patch events.",
        )
        return

    forced_share = float((patch["patch_type"] == "forced_origin_public").mean())
    target_full = float((patch["target_soc"] >= 0.95).mean())
    inconvenience_median = float(patch["inconvenience_minutes"].median())
    public_mapped = patch[patch["patch_type"].isin(["near_route_public", "forced_origin_public"])]["charger_source"].isin(["zone_proxy", "osm", "afdc"]).all()
    public_patch = patch[patch["charger_source"].isin(["afdc", "osm", "zone_proxy"])]
    public_patch_detour_p95 = float(public_patch["detour_km"].quantile(0.95)) if len(public_patch) else float("nan")
    public_patch_detour_sufficient = len(public_patch) >= 10
    public_patch_detour_ok = (
        public_patch_detour_p95 <= 15.0
        if public_patch_detour_sufficient
        else not options.require_sample_evidence
    )
    if options.require_real_chargers:
        public_patch_real = public_patch.empty or public_patch["charger_source"].isin(["afdc", "osm"]).all()
        _add(rows, "patch", "public_patch_sources_real", bool(public_patch_real), bool(public_patch_real), "")

    _add(rows, "patch", "forced_patch_share_pct", round(forced_share * 100, 2), forced_share < 0.15, "")
    _add(rows, "patch", "full_battery_patch_target_pct", round(target_full * 100, 2), target_full == 0.0, "")
    _add(rows, "patch", "median_inconvenience_min", round(inconvenience_median, 2), inconvenience_median < 35.0, "")
    _add(rows, "patch", "public_patches_mapped_to_public_catalog", bool(public_mapped), bool(public_mapped), "")
    _add(
        rows,
        "patch",
        "public_patch_detour_p95_km",
        round(public_patch_detour_p95, 2) if np.isfinite(public_patch_detour_p95) else "nan",
        bool(public_patch_detour_ok),
        "" if public_patch_detour_sufficient else f"Strict gate deferred because only {len(public_patch)} public patches were sampled.",
    )


def _temporal_consistency_checks(rows: list[dict[str, object]], legs: pd.DataFrame, charges: pd.DataFrame) -> None:
    eps = 1e-6
    if legs.empty:
        _add(rows, "temporal", "leg_time_bounds", False, False, "No leg rows to validate.")
        return

    depart = pd.to_numeric(legs["depart_hour_abs"], errors="coerce")
    arrival = pd.to_numeric(legs["arrival_hour_abs"], errors="coerce")
    travel = pd.to_numeric(legs["travel_time_h"], errors="coerce")
    overflow_source = legs["week_overflow_h"] if "week_overflow_h" in legs else pd.Series(0.0, index=legs.index)
    overflow = pd.to_numeric(overflow_source, errors="coerce").fillna(0.0)
    raw_arrival = arrival + overflow
    leg_bounds_ok = bool(
        depart.notna().all()
        and arrival.notna().all()
        and travel.notna().all()
        and (depart >= -eps).all()
        and (depart <= 168.0 + eps).all()
        and (arrival >= depart - eps).all()
        and (arrival <= 168.0 + eps).all()
        and (travel >= -eps).all()
        and (overflow >= -eps).all()
    )
    leg_duration_error_h = float((raw_arrival - depart - travel).abs().max()) if len(legs) else 0.0
    leg_overlap_count = _interval_overlap_count(legs, "depart_hour_abs", "arrival_hour_abs")

    _add(rows, "temporal", "leg_time_bounds", leg_bounds_ok, leg_bounds_ok, "")
    _add(rows, "temporal", "leg_duration_consistency_max_min", round(leg_duration_error_h * 60.0, 4), leg_duration_error_h <= 0.02, "Compares raw arrival including week overflow against travel time.")
    _add(rows, "temporal", "leg_leg_overlap_count", leg_overlap_count, leg_overlap_count == 0, "")

    if charges.empty:
        _add(rows, "temporal", "charge_time_bounds", True, True, "No charge events sampled.")
        _add(rows, "temporal", "charge_drive_overlap_count", 0, True, "")
        _add(rows, "temporal", "charge_charge_overlap_count", 0, True, "")
        _add(rows, "temporal", "destination_prepay_patch_count", 0, True, "")
        return

    start = pd.to_numeric(charges["start_hour_abs"], errors="coerce")
    end = pd.to_numeric(charges["end_hour_abs"], errors="coerce")
    duration = pd.to_numeric(charges["duration_h"], errors="coerce")
    charge_bounds_ok = bool(
        start.notna().all()
        and end.notna().all()
        and duration.notna().all()
        and (start >= -eps).all()
        and (start <= 168.0 + eps).all()
        and (end >= start - eps).all()
        and (end <= 168.0 + eps).all()
        and (duration >= -eps).all()
    )
    charge_duration_error_h = float((end - start - duration).abs().max()) if len(charges) else 0.0
    charge_drive_overlap_count = _charge_drive_overlap_count(legs, charges)
    charge_charge_overlap_count = _interval_overlap_count(charges, "start_hour_abs", "end_hour_abs")
    destination_prepay_patch_count = int((charges["patch_type"] == "destination_public").sum()) if "patch_type" in charges else 0

    _add(rows, "temporal", "charge_time_bounds", charge_bounds_ok, charge_bounds_ok, "")
    _add(rows, "temporal", "charge_duration_consistency_max_min", round(charge_duration_error_h * 60.0, 6), charge_duration_error_h <= 1e-6, "")
    _add(rows, "temporal", "charge_drive_overlap_count", charge_drive_overlap_count, charge_drive_overlap_count == 0, "")
    _add(rows, "temporal", "charge_charge_overlap_count", charge_charge_overlap_count, charge_charge_overlap_count == 0, "")
    _add(rows, "temporal", "destination_prepay_patch_count", destination_prepay_patch_count, destination_prepay_patch_count == 0, "")


def _interval_overlap_count(df: pd.DataFrame, start_col: str, end_col: str) -> int:
    if df.empty or not {"person_id", start_col, end_col}.issubset(df.columns):
        return 0
    overlaps = 0
    for _, group in df.sort_values(["person_id", start_col]).groupby("person_id", sort=False):
        starts = pd.to_numeric(group[start_col], errors="coerce").to_numpy(dtype=float)
        ends = pd.to_numeric(group[end_col], errors="coerce").to_numpy(dtype=float)
        if len(starts) > 1:
            overlaps += int((starts[1:] < ends[:-1] - 1e-6).sum())
    return overlaps


def _charge_drive_overlap_count(legs: pd.DataFrame, charges: pd.DataFrame) -> int:
    if legs.empty or charges.empty:
        return 0
    overlaps = 0
    legs_by_person = {
        person_id: group
        for person_id, group in legs.groupby("person_id", sort=False)
    }
    for charge in charges.itertuples(index=False):
        person_legs = legs_by_person.get(charge.person_id)
        if person_legs is None:
            continue
        mask = (
            (pd.to_numeric(person_legs["depart_hour_abs"], errors="coerce") < float(charge.end_hour_abs) - 1e-6)
            & (pd.to_numeric(person_legs["arrival_hour_abs"], errors="coerce") > float(charge.start_hour_abs) + 1e-6)
        )
        overlaps += int(mask.sum())
    return overlaps


def _load_checks(rows: list[dict[str, object]], hourly: pd.DataFrame, charges: pd.DataFrame) -> None:
    if hourly.empty and charges.empty:
        _add(rows, "load", "hourly_nonempty", 0, False, "")
        return
    energy_diff = float(hourly["energy_kwh"].sum() - charges["energy_delivered_kwh"].sum())
    unique_total_keys = not hourly.duplicated(["fsa", "day", "hour", "event_type", "patch_type"]).any()
    _add(rows, "load", "hourly_energy_conservation_kwh", round(energy_diff, 6), abs(energy_diff) < 1e-6, "")
    _add(rows, "load", "hourly_event_bucket_uniqueness", bool(unique_total_keys), bool(unique_total_keys), "")


def _grid_load_checks(rows: list[dict[str, object]], grid_load: pd.DataFrame, hourly: pd.DataFrame, cfg: MobilityConfig) -> None:
    if grid_load.empty:
        _add(rows, "grid", "grid_load_nonempty", 0, False, "")
        return
    no_ev_baseline_overloads = bool((grid_load["baseline_load_kw"] <= grid_load["proxy_capacity_kw"] + 1e-9).all())
    baseline_peak_utilization = float((grid_load["baseline_load_kw"] / grid_load["proxy_capacity_kw"]).max())
    raw_hourly_sum = float(hourly["ev_load_kw"].sum()) if not hourly.empty else 0.0
    observed_scale = float(grid_load["ev_load_kw"].sum() / raw_hourly_sum) if raw_hourly_sum > 0 else 1.0
    scale_matches = raw_hourly_sum <= 0 or bool(np.isclose(observed_scale, cfg.grid_ev_load_scale, rtol=1e-9, atol=1e-9))
    ev_energy_diff = float(grid_load["ev_load_kw"].sum() - raw_hourly_sum * cfg.grid_ev_load_scale)
    overloaded_fsa_hours = int(grid_load["overloaded"].sum())
    max_deficit_kw = float(grid_load["deficit_kw"].max())
    _add(rows, "grid", "baseline_no_ev_overloads", no_ev_baseline_overloads, no_ev_baseline_overloads, "")
    _add(rows, "grid", "baseline_peak_utilization_pct", round(baseline_peak_utilization * 100, 2), baseline_peak_utilization < 0.99, "")
    _add(rows, "grid", "ev_load_scale", round(observed_scale, 3), observed_scale > 0, "")
    _add(rows, "grid", "ev_load_scale_matches_config", round(observed_scale, 6), scale_matches, f"expected={cfg.grid_ev_load_scale:.6f}")
    _add(rows, "grid", "ev_load_energy_conservation_kw", round(ev_energy_diff, 6), abs(ev_energy_diff) < 1e-6, "")
    _add(rows, "grid", "overloaded_fsa_hours", overloaded_fsa_hours, overloaded_fsa_hours >= 0, f"max_deficit_kw={max_deficit_kw:.3f}")


def _observed_target_checks(rows: list[dict[str, object]], artifacts: dict[str, pd.DataFrame]) -> None:
    from observed_targets import observed_target_report

    report = observed_target_report(artifacts)
    for _, row in report.iterrows():
        _add(rows, "observed", str(row["target"]), row["value"], row["status"] == "PASS", row["detail"])


def _repeat_destination_share(itinerary: pd.DataFrame, dest_type: str) -> float:
    trips = itinerary[itinerary["dest_type"] == dest_type]
    if trips.empty:
        return 1.0
    counts = trips.groupby("person_id")["dest_fsa"].nunique()
    repeated = counts[counts.index.isin(trips["person_id"].unique())]
    if repeated.empty:
        return 1.0
    return float((repeated <= 1).mean())


if __name__ == "__main__":
    report_df, _ = validate_weekly_simulation()
    print(report_df.to_string(index=False))
    broken = report_df[report_df["status"] != "PASS"]
    if not broken.empty:
        raise SystemExit(1)
