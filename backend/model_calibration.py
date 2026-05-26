"""
Calibration utilities for the weekly road-grid mobility model.

The simulator still contains calibrated behavioral assumptions. This module
turns the validation gates into an explicit objective so candidate parameter
sets can be compared by evidence instead of by inspection.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from mobility_simulator import MobilityConfig, MobilitySimulationEngine
from simulation_validation import (
    PURPOSE_ZONE_ALIGNMENT,
    ValidationOptions,
    compute_soc_balance,
    validate_weekly_simulation,
)


ITINERARY_CONFIG_FIELDS = (
    "ev_probability",
    "ev_efficiency_kwh_per_km",
    "road_circuity",
    "initial_soc_alpha",
    "initial_soc_beta",
    "home_charger_probability",
    "work_charger_probability",
    "worker_weekday_work_probability",
    "student_weekday_school_probability",
    "weekday_nonworker_outing_probability",
    "after_work_stop_probability",
    "worker_weekend_work_probability",
    "weekend_outing_probability",
    "weekend_second_stop_probability",
    "traffic_attraction_exponent",
    "road_graph_source",
    "force_osm_download",
)

_FIT_ITINERARY_CACHE: dict[tuple[object, ...], tuple[pd.DataFrame, pd.DataFrame]] = {}


def clear_fit_itinerary_cache() -> None:
    """Clear process-local generated-itinerary cache used by calibration."""
    _FIT_ITINERARY_CACHE.clear()


@dataclass(frozen=True)
class FitTarget:
    low: float
    high: float
    ideal: float
    weight: float = 1.0
    missing_loss: float = 100.0


FIT_TARGETS: dict[str, FitTarget] = {
    "legs_per_person_week_median": FitTarget(10.0, 16.0, 13.0, 1.0),
    "route_km_p50": FitTarget(5.0, 25.0, 18.0, 1.0),
    "route_km_p90": FitTarget(40.0, 90.0, 55.0, 1.0),
    "weekly_km_person_median": FitTarget(150.0, 350.0, 240.0, 1.0),
    "weekday_am_work_school_share_pct": FitTarget(60.0, 100.0, 95.0, 0.8),
    "weekend_retail_leisure_home_share_pct": FitTarget(85.0, 100.0, 96.0, 0.5),
    "transit_hub_destination_share_pct": FitTarget(0.3, 3.0, 1.0, 0.15),
    "other_destination_share_pct": FitTarget(0.3, 2.5, 0.8, 0.10),
    "arrive_by_delay_gt20_pct": FitTarget(0.0, 2.5, 0.5, 0.6),
    "arrive_by_delay_max_min": FitTarget(0.0, 90.0, 20.0, 0.2),
    "near_reserve_pct": FitTarget(0.0, 12.0, 4.0, 1.0),
    "full_departure_soc_pct": FitTarget(0.0, 45.0, 18.0, 0.8),
    "final_soc_mean_drift_pctpt": FitTarget(-8.0, 8.0, 0.0, 0.8),
    "final_soc_at_full_pct": FitTarget(0.0, 10.0, 2.0, 0.3),
    "charges_per_ev_week": FitTarget(1.5, 4.5, 2.8, 1.2),
    "patches_per_ev_week": FitTarget(0.05, 0.75, 0.25, 1.0),
    "forced_origin_public_per_ev_week": FitTarget(0.0, 0.05, 0.005, 1.0),
    "public_charges_per_ev_week": FitTarget(0.25, 1.25, 0.75, 0.4),
    "median_inconvenience_min": FitTarget(0.0, 35.0, 5.0, 0.8, missing_loss=0.0),
    "public_charge_detour_p95_km": FitTarget(0.0, 20.0, 5.0, 0.4, missing_loss=0.0),
    "public_patch_detour_p95_km": FitTarget(0.0, 15.0, 4.0, 0.4, missing_loss=0.0),
    "top_1pct_edge_flow_share_pct": FitTarget(1.0, 80.0, 12.0, 0.5),
    "home_zone_alignment_pct": FitTarget(65.0, 100.0, 82.0, 0.6),
    "work_zone_alignment_pct": FitTarget(50.0, 100.0, 65.0, 0.7),
    "school_zone_alignment_pct": FitTarget(75.0, 100.0, 92.0, 0.3, missing_loss=0.0),
    "retail_zone_alignment_pct": FitTarget(30.0, 100.0, 45.0, 0.7),
    "leisure_zone_alignment_pct": FitTarget(75.0, 100.0, 92.0, 0.4),
    "transit_hub_zone_alignment_pct": FitTarget(40.0, 100.0, 65.0, 0.25, missing_loss=0.10),
    "public_charger_top_1pct_energy_share_pct": FitTarget(0.0, 25.0, 8.0, 0.5, missing_loss=0.10),
    "public_charger_top_10pct_energy_share_pct": FitTarget(0.0, 50.0, 30.0, 0.4, missing_loss=0.10),
    "active_days_worker_mean": FitTarget(4.0, 6.5, 5.0, 0.8),
    "active_days_retired_mean": FitTarget(1.0, 4.5, 3.0, 0.8),
    "observed_morning_zone_l1": FitTarget(0.0, 0.80, 0.35, 0.7),
    "observed_evening_zone_l1": FitTarget(0.0, 0.80, 0.35, 0.7),
    "observed_morning_fsa_l1": FitTarget(0.0, 1.60, 0.80, 0.4),
    "observed_evening_fsa_l1": FitTarget(0.0, 1.60, 0.80, 0.4),
    "observed_public_charger_zone_l1": FitTarget(0.0, 1.00, 0.35, 0.4),
    "observed_hourly_load_corr": FitTarget(-0.25, 1.0, 0.25, 0.2),
}


def candidate_configs(base: MobilityConfig | None = None) -> list[MobilityConfig]:
    """Small local grid around the current fitted defaults."""
    base = base or MobilityConfig(ev_probability=0.20)
    candidates: list[MobilityConfig] = []
    seen: set[tuple[float, ...]] = set()

    def add(candidate: MobilityConfig) -> None:
        key = (
            candidate.initial_soc_alpha,
            candidate.initial_soc_beta,
            candidate.target_soc,
            candidate.week_end_target_soc,
            candidate.home_charger_probability,
            candidate.work_charger_probability,
            candidate.home_public_charger_access,
            candidate.work_public_charger_access,
            candidate.retail_public_charger_access,
            candidate.patch_softmax_temperature,
            candidate.worker_weekday_work_probability,
            candidate.after_work_stop_probability,
            candidate.weekday_nonworker_outing_probability,
            candidate.weekend_outing_probability,
            candidate.traffic_attraction_exponent,
        )
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    add(base)
    for public_access in [(0.15, 0.25, 0.55), (0.22, 0.25, 0.55), (0.30, 0.25, 0.45)]:
        add(replace(
            base,
            home_public_charger_access=public_access[0],
            work_public_charger_access=public_access[1],
            retail_public_charger_access=public_access[2],
        ))
    for week_end_target in [0.75, 0.80, 0.85]:
        add(replace(base, week_end_target_soc=week_end_target))

    for initial, target_soc, home_work, softmax, traffic_exponent, schedule in product(
        [(5.0, 2.0), (6.0, 2.0), (8.0, 2.0)],
        [0.80, 0.82, 0.83, 0.85],
        [(0.60, 0.25), (0.70, 0.35), (0.80, 0.45)],
        [0.50, 0.90, 2.00],
        [base.traffic_attraction_exponent, 0.25, 0.50],
        [
            (0.84, 0.28, 0.50, 0.66),
            (0.88, 0.34, 0.55, 0.72),
            (0.92, 0.40, 0.60, 0.78),
        ],
    ):
        add(replace(
            base,
            initial_soc_alpha=initial[0],
            initial_soc_beta=initial[1],
            target_soc=target_soc,
            home_charger_probability=home_work[0],
            work_charger_probability=home_work[1],
            patch_softmax_temperature=softmax,
            traffic_attraction_exponent=traffic_exponent,
            worker_weekday_work_probability=schedule[0],
            after_work_stop_probability=schedule[1],
            weekday_nonworker_outing_probability=schedule[2],
            weekend_outing_probability=schedule[3],
        ))
    return candidates


def selected_candidate_indices(
    base: MobilityConfig | None = None,
    *,
    max_candidates: int | None = None,
    candidate_start: int | None = None,
    candidate_stop: int | None = None,
) -> list[int]:
    """Return candidate ids selected by the same range/spread rules as fitting."""
    configs = candidate_configs(base)
    return [
        idx
        for idx, _ in _candidate_items(
            configs,
            max_candidates=max_candidates,
            candidate_start=candidate_start,
            candidate_stop=candidate_stop,
        )
    ]


def evaluate_config(
    config: MobilityConfig,
    *,
    num_people: int = 600,
    seeds: tuple[int, ...] = (101, 202, 303),
    options: ValidationOptions | None = None,
    cache_itinerary: bool = False,
    run_validation: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate one config across seeds and return summary plus per-seed metrics."""
    rows = []
    reports = []
    for seed in seeds:
        if run_validation:
            plan = _cached_weekly_plan(config, num_people, seed) if cache_itinerary else None
            report, artifacts = validate_weekly_simulation(
                num_people=num_people,
                seed=seed,
                config=config,
                options=options,
                precomputed_plan=plan,
            )
        else:
            artifacts = _simulate_fit_artifacts(config, num_people=num_people, seed=seed, options=options, cache_itinerary=cache_itinerary)
            report = pd.DataFrame()
        metrics = extract_fit_metrics(artifacts, config)
        loss = score_fit_metrics(metrics)
        break_count = int((report["status"] != "PASS").sum()) if run_validation else _fit_target_break_count(metrics)
        rows.append({
            "seed": seed,
            "loss": loss,
            "break_count": break_count,
            **_config_fields(config),
            **metrics,
        })
        if not report.empty:
            reports.append(report.assign(seed=seed))
    per_seed = pd.DataFrame(rows)
    summary = _summarize_per_seed(per_seed)
    detail_reports = pd.concat(reports, ignore_index=True) if reports else pd.DataFrame()
    return summary, per_seed.assign(_detail_reports=[detail_reports] * len(per_seed)) if not per_seed.empty else per_seed


def fit_config(
    base: MobilityConfig | None = None,
    *,
    num_people: int = 500,
    seeds: tuple[int, ...] = (101, 202),
    options: ValidationOptions | None = None,
    max_candidates: int | None = None,
    candidate_start: int | None = None,
    candidate_stop: int | None = None,
    candidate_indices: list[int] | tuple[int, ...] | None = None,
    jobs: int = 1,
    progress: Callable[[dict[str, object], int, int], None] | None = None,
    checkpoint_path: str | Path | None = None,
    resume: bool = False,
    cache_itineraries: bool = True,
    run_validation: bool = True,
) -> pd.DataFrame:
    """
    Score candidate configs and return them ordered by objective loss.

    This is deliberately brute force and transparent. For production-scale
    fitting, replace the candidate grid with Bayesian optimization or direct
    likelihood fitting once observed trip/charging data is available.
    """
    configs = candidate_configs(base)
    if candidate_indices is None:
        items = _candidate_items(
            configs,
            max_candidates=max_candidates,
            candidate_start=candidate_start,
            candidate_stop=candidate_stop,
        )
    else:
        seen_indices: set[int] = set()
        items = []
        for raw_idx in candidate_indices:
            idx = int(raw_idx)
            if idx in seen_indices:
                continue
            if idx < 0 or idx >= len(configs):
                raise ValueError(f"candidate index out of range: {idx}")
            seen_indices.add(idx)
            items.append((idx, configs[idx]))

    checkpoint = Path(checkpoint_path) if checkpoint_path is not None else None
    rows = _load_fit_checkpoint(checkpoint) if resume else []
    allowed_candidates = {idx for idx, _ in items}
    rows = [row for row in rows if int(row.get("candidate", -1)) in allowed_candidates]
    completed_candidates = {int(row["candidate"]) for row in rows if "candidate" in row}
    work = [(idx, cfg, num_people, seeds, options, cache_itineraries, run_validation) for idx, cfg in items if idx not in completed_candidates]
    total = len(items)

    if jobs <= 1 or len(work) <= 1 or not _can_spawn_process_pool():
        for item in work:
            row = _evaluate_candidate_for_fit(item)
            rows.append(row)
            _record_fit_progress(rows, row, len(rows), total, progress, checkpoint)
    else:
        parallel_work = _group_fit_work_by_itinerary(work) if cache_itineraries else [[item] for item in work]
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(_evaluate_candidate_group_for_fit, group) for group in parallel_work]
            for future in as_completed(futures):
                for row in future.result():
                    rows.append(row)
                    _record_fit_progress(rows, row, len(rows), total, progress, checkpoint)
    return _rank_fit_rows(rows)


def _evaluate_candidate_for_fit(
    item: tuple[int, MobilityConfig, int, tuple[int, ...], ValidationOptions | None, bool, bool],
) -> dict[str, object]:
    idx, cfg, num_people, seeds, options, cache_itinerary, run_validation = item
    summary, per_seed = evaluate_config(
        cfg,
        num_people=num_people,
        seeds=seeds,
        options=options,
        cache_itinerary=cache_itinerary,
        run_validation=run_validation,
    )
    row = summary.iloc[0].to_dict()
    row["break_metrics"] = _candidate_break_summary(per_seed, row, run_validation=run_validation)
    row["candidate"] = idx
    return row


def _evaluate_candidate_group_for_fit(
    group: list[tuple[int, MobilityConfig, int, tuple[int, ...], ValidationOptions | None, bool, bool]],
) -> list[dict[str, object]]:
    return [_evaluate_candidate_for_fit(item) for item in group]


def _cached_weekly_plan(config: MobilityConfig, num_people: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    key = _itinerary_cache_key(config, num_people, seed)
    if key not in _FIT_ITINERARY_CACHE:
        engine = MobilitySimulationEngine(config)
        _FIT_ITINERARY_CACHE[key] = engine.generate_weekly_itinerary(num_people=num_people, seed=seed)
    return _FIT_ITINERARY_CACHE[key]


def _simulate_fit_artifacts(
    config: MobilityConfig,
    *,
    num_people: int,
    seed: int,
    options: ValidationOptions | None,
    cache_itinerary: bool,
) -> dict[str, pd.DataFrame]:
    opts = options or ValidationOptions()
    engine = MobilitySimulationEngine(config)
    if cache_itinerary:
        people, itinerary = _cached_weekly_plan(config, num_people, seed)
    else:
        people, itinerary = engine.generate_weekly_itinerary(num_people=num_people, seed=seed)
    legs, charges = engine.simulate_weekly_charging(people, itinerary, seed=seed + 1)
    hourly = engine.aggregate_charge_events(charges)
    grid_load = engine.aggregate_weekly_grid_load(hourly)
    if opts.edge_flow_detail == "full":
        edge_flows = engine.aggregate_edge_flows(legs)
    elif opts.edge_flow_detail == "fsa":
        edge_flows = engine.aggregate_fsa_corridor_flows(legs)
    else:
        raise ValueError("edge_flow_detail must be 'full' or 'fsa'.")
    return {
        "people": people,
        "itinerary": itinerary,
        "legs": legs,
        "charges": charges,
        "hourly": hourly,
        "grid_load": grid_load,
        "edge_flows": edge_flows,
    }


def _fit_target_break_count(metrics: dict[str, float]) -> int:
    count = 0
    for name, target in FIT_TARGETS.items():
        value = metrics.get(name, np.nan)
        if np.isfinite(value) and not (target.low <= value <= target.high):
            count += 1
    return count


def _candidate_break_summary(per_seed: pd.DataFrame, summary_row: dict[str, object], *, run_validation: bool) -> str:
    if run_validation and not per_seed.empty and "_detail_reports" in per_seed:
        detail_reports = per_seed["_detail_reports"].iloc[0]
        if isinstance(detail_reports, pd.DataFrame) and not detail_reports.empty:
            broken = detail_reports[detail_reports["status"] != "PASS"]
            return _validation_break_summary(broken)
    return _fit_target_break_summary(summary_row)


def _validation_break_summary(broken: pd.DataFrame) -> str:
    if broken.empty or not {"seed", "metric"}.issubset(broken.columns):
        return ""
    parts: list[str] = []
    for seed, group in broken.groupby("seed", sort=True):
        counts = group["metric"].value_counts().sort_index()
        metrics = "; ".join(f"{metric}={int(count)}" for metric, count in counts.items())
        parts.append(f"seed {int(seed)}: {metrics}")
    return " | ".join(parts)


def _fit_target_break_summary(summary_row: dict[str, object]) -> str:
    parts: list[str] = []
    for name, target in FIT_TARGETS.items():
        value = summary_row.get(f"{name}_mean", summary_row.get(name, np.nan))
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric) and not (target.low <= numeric <= target.high):
            parts.append(name)
    return "; ".join(parts)


def _itinerary_cache_key(config: MobilityConfig, num_people: int, seed: int) -> tuple[object, ...]:
    return (
        int(num_people),
        int(seed),
        tuple((field, getattr(config, field)) for field in ITINERARY_CONFIG_FIELDS),
    )


def _group_fit_work_by_itinerary(
    work: list[tuple[int, MobilityConfig, int, tuple[int, ...], ValidationOptions | None, bool, bool]],
) -> list[list[tuple[int, MobilityConfig, int, tuple[int, ...], ValidationOptions | None, bool, bool]]]:
    groups: dict[tuple[object, ...], list[tuple[int, MobilityConfig, int, tuple[int, ...], ValidationOptions | None, bool, bool]]] = {}
    for item in work:
        _, cfg, num_people, seeds, _, _, run_validation = item
        key = (
            int(num_people),
            tuple(int(seed) for seed in seeds),
            bool(run_validation),
            tuple((field, getattr(cfg, field)) for field in ITINERARY_CONFIG_FIELDS),
        )
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def _rank_fit_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["max_break_count", "mean_loss", "std_loss", "candidate"],
        ignore_index=True,
    )


def _load_fit_checkpoint(checkpoint: Path | None) -> list[dict[str, object]]:
    if checkpoint is None or not checkpoint.exists():
        return []
    df = pd.read_csv(checkpoint)
    if "candidate" not in df.columns:
        return []
    df = df.drop_duplicates(subset=["candidate"], keep="last")
    return df.to_dict("records")


def _record_fit_progress(
    rows: list[dict[str, object]],
    row: dict[str, object],
    completed: int,
    total: int,
    progress: Callable[[dict[str, object], int, int], None] | None,
    checkpoint: Path | None,
) -> None:
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).sort_values("candidate").to_csv(checkpoint, index=False)
    if progress is not None:
        progress(row, completed, total)


def _can_spawn_process_pool() -> bool:
    main_path = Path(sys.argv[0])
    return bool(sys.argv[0]) and sys.argv[0] not in {"-", "-c", "<stdin>"} and main_path.exists()


def extract_fit_metrics(artifacts: dict[str, pd.DataFrame], config: MobilityConfig) -> dict[str, float]:
    people = artifacts["people"]
    itinerary = artifacts["itinerary"]
    legs = artifacts["legs"]
    charges = artifacts["charges"]
    edge_flows = artifacts["edge_flows"]
    ev_people = people[people["is_ev"]]
    ev_count = max(1, len(ev_people))
    ev_legs = legs[legs["is_ev"]]

    leg_counts = itinerary.groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    weekly_km = itinerary.groupby("person_id")["route_km"].sum().reindex(people["person_id"], fill_value=0)
    weekday_am = itinerary[(itinerary["day"] < 5) & (itinerary["depart_hour_abs"] % 24 <= 10.5)]
    weekend = itinerary[itinerary["day"] >= 5]
    commute = legs[legs["planned_arrival_hour_abs"].notna()] if not legs.empty else legs
    patch = charges[charges["event_type"] == "patch"]
    public_charges = charges[charges["charger_source"].isin(["afdc", "osm", "zone_proxy"])] if not charges.empty else charges
    public_patch = patch[patch["charger_source"].isin(["afdc", "osm", "zone_proxy"])] if not patch.empty else patch
    soc_balance = compute_soc_balance(people, legs, charges, config)
    active_days = itinerary.groupby(["person_id", "day"]).size().reset_index().groupby("person_id").size().reindex(people["person_id"], fill_value=0)
    active_by_type = people.assign(active_days=people["person_id"].map(active_days)).groupby("person_type")["active_days"].mean()
    try:
        from observed_targets import compute_observed_target_metrics
        observed_metrics = compute_observed_target_metrics(artifacts)
    except Exception:
        observed_metrics = {}

    metrics = {
        "legs_per_person_week_median": float(leg_counts.median()) if len(leg_counts) else np.nan,
        "route_km_p50": float(itinerary["route_km"].median()) if len(itinerary) else np.nan,
        "route_km_p90": float(itinerary["route_km"].quantile(0.90)) if len(itinerary) else np.nan,
        "weekly_km_person_median": float(weekly_km.median()) if len(weekly_km) else np.nan,
        "weekday_am_work_school_share_pct": float(weekday_am["dest_type"].isin(["work", "school"]).mean() * 100) if len(weekday_am) else np.nan,
        "weekend_retail_leisure_home_share_pct": float(weekend["dest_type"].isin(["retail", "leisure", "home"]).mean() * 100) if len(weekend) else np.nan,
        "transit_hub_destination_share_pct": float((itinerary["dest_type"] == "transit_hub").mean() * 100.0) if len(itinerary) else np.nan,
        "other_destination_share_pct": float((itinerary["dest_type"] == "other").mean() * 100.0) if len(itinerary) else np.nan,
        "arrive_by_delay_gt20_pct": float((commute["schedule_delay_min"] > 20.0).mean() * 100.0) if len(commute) else np.nan,
        "arrive_by_delay_max_min": float(commute["schedule_delay_min"].max()) if len(commute) else np.nan,
        "near_reserve_pct": float((ev_legs["soc_after"] < config.reserve_soc + 0.05).mean() * 100) if len(ev_legs) else np.nan,
        "full_departure_soc_pct": float((ev_legs["soc_before"] >= 0.995).mean() * 100) if len(ev_legs) else np.nan,
        "final_soc_mean_drift_pctpt": float(soc_balance["soc_delta"].mean() * 100.0) if not soc_balance.empty else np.nan,
        "final_soc_at_full_pct": float((soc_balance["final_soc"] >= 0.995).mean() * 100.0) if not soc_balance.empty else np.nan,
        "charges_per_ev_week": float(len(charges) / ev_count),
        "patches_per_ev_week": float(len(patch) / ev_count),
        "forced_origin_public_per_ev_week": float((charges["patch_type"] == "forced_origin_public").sum() / ev_count) if not charges.empty else 0.0,
        "public_charges_per_ev_week": float(len(public_charges) / ev_count),
        "median_inconvenience_min": float(patch["inconvenience_minutes"].median()) if len(patch) else np.nan,
        "public_charge_detour_p95_km": float(public_charges["detour_km"].quantile(0.95)) if len(public_charges) >= 30 else np.nan,
        "public_patch_detour_p95_km": float(public_patch["detour_km"].quantile(0.95)) if len(public_patch) >= 10 else np.nan,
        "top_1pct_edge_flow_share_pct": _top_edge_flow_share(edge_flows),
        **_purpose_zone_alignment_metrics(itinerary),
        **_public_charger_concentration_metrics(public_charges),
        "active_days_worker_mean": float(active_by_type.get("worker", np.nan)),
        "active_days_retired_mean": float(active_by_type.get("retired", np.nan)),
        **observed_metrics,
    }
    return metrics


def score_fit_metrics(metrics: dict[str, float], targets: dict[str, FitTarget] | None = None) -> float:
    targets = targets or FIT_TARGETS
    loss = 0.0
    for name, target in targets.items():
        value = metrics.get(name, np.nan)
        loss += target.weight * _target_loss(value, target)
    return float(loss)


def _target_loss(value: float, target: FitTarget) -> float:
    if not np.isfinite(value):
        return target.missing_loss
    width = max(target.high - target.low, 1e-9)
    if target.low <= value <= target.high:
        return ((value - target.ideal) / width) ** 2
    distance = target.low - value if value < target.low else value - target.high
    return 1.0 + (distance / width) ** 2


def _top_edge_flow_share(edge_flows: pd.DataFrame) -> float:
    if edge_flows.empty:
        return np.nan
    total = float(edge_flows["vehicle_count"].sum())
    if total <= 0:
        return np.nan
    top_n = max(1, int(np.ceil(len(edge_flows) * 0.01)))
    return float(edge_flows.nlargest(top_n, "vehicle_count")["vehicle_count"].sum() / total * 100)


def _purpose_zone_alignment_metrics(itinerary: pd.DataFrame) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if itinerary.empty or not {"dest_type", "dest_zone_type"}.issubset(itinerary.columns):
        return {f"{purpose}_zone_alignment_pct": np.nan for purpose in PURPOSE_ZONE_ALIGNMENT}
    for purpose, (allowed_zones, _, min_sample) in PURPOSE_ZONE_ALIGNMENT.items():
        subset = itinerary[itinerary["dest_type"] == purpose]
        if len(subset) < min_sample:
            metrics[f"{purpose}_zone_alignment_pct"] = np.nan
        else:
            metrics[f"{purpose}_zone_alignment_pct"] = float(subset["dest_zone_type"].isin(allowed_zones).mean() * 100.0)
    return metrics


def _public_charger_concentration_metrics(public_charges: pd.DataFrame) -> dict[str, float]:
    empty = {
        "public_charger_top_1pct_energy_share_pct": np.nan,
        "public_charger_top_10pct_energy_share_pct": np.nan,
    }
    if public_charges.empty or not {"charger_id", "energy_delivered_kwh"}.issubset(public_charges.columns):
        return empty
    public = public_charges[pd.to_numeric(public_charges["energy_delivered_kwh"], errors="coerce").fillna(0.0) > 0]
    if len(public) < 30:
        return empty
    energy = public.groupby("charger_id")["energy_delivered_kwh"].sum().sort_values(ascending=False)
    total = float(energy.sum())
    if total <= 0 or energy.empty:
        return empty
    top_1_count = max(1, int(np.ceil(len(energy) * 0.01)))
    top_10_count = max(1, int(np.ceil(len(energy) * 0.10)))
    return {
        "public_charger_top_1pct_energy_share_pct": float(energy.head(top_1_count).sum() / total * 100.0),
        "public_charger_top_10pct_energy_share_pct": float(energy.head(top_10_count).sum() / total * 100.0),
    }


def _config_fields(config: MobilityConfig) -> dict[str, float | str | bool]:
    return {
        "ev_probability": config.ev_probability,
        "initial_soc_alpha": config.initial_soc_alpha,
        "initial_soc_beta": config.initial_soc_beta,
        "target_soc": config.target_soc,
        "week_end_target_soc": config.week_end_target_soc,
        "reserve_soc": config.reserve_soc,
        "home_charger_probability": config.home_charger_probability,
        "work_charger_probability": config.work_charger_probability,
        "home_public_charger_access": config.home_public_charger_access,
        "work_public_charger_access": config.work_public_charger_access,
        "retail_public_charger_access": config.retail_public_charger_access,
        "patch_softmax_temperature": config.patch_softmax_temperature,
        "worker_weekday_work_probability": config.worker_weekday_work_probability,
        "student_weekday_school_probability": config.student_weekday_school_probability,
        "weekday_nonworker_outing_probability": config.weekday_nonworker_outing_probability,
        "after_work_stop_probability": config.after_work_stop_probability,
        "worker_weekend_work_probability": config.worker_weekend_work_probability,
        "weekend_outing_probability": config.weekend_outing_probability,
        "weekend_second_stop_probability": config.weekend_second_stop_probability,
        "baseline_peak_utilization": config.baseline_peak_utilization,
        "grid_ev_load_scale": config.grid_ev_load_scale,
        "vehicle_population_share": config.vehicle_population_share,
        "traffic_attraction_exponent": config.traffic_attraction_exponent,
        "road_graph_source": config.road_graph_source,
        "charger_source": config.charger_source,
    }


def _summarize_per_seed(per_seed: pd.DataFrame) -> pd.DataFrame:
    metric_cols = [name for name in FIT_TARGETS if name in per_seed.columns]
    row = {
        "mean_loss": float(per_seed["loss"].mean()),
        "std_loss": float(per_seed["loss"].std(ddof=0)),
        "max_break_count": int(per_seed["break_count"].max()),
        "mean_break_count": float(per_seed["break_count"].mean()),
    }
    for key, value in _config_fields(_row_to_config(per_seed.iloc[0])).items():
        row[key] = value
    for metric in metric_cols:
        row[f"{metric}_mean"] = float(per_seed[metric].mean())
        row[f"{metric}_std"] = float(per_seed[metric].std(ddof=0))
    return pd.DataFrame([row])


def _row_to_config(row: pd.Series) -> MobilityConfig:
    return MobilityConfig(
        ev_probability=float(row["ev_probability"]),
        initial_soc_alpha=float(row["initial_soc_alpha"]),
        initial_soc_beta=float(row["initial_soc_beta"]),
        target_soc=float(row["target_soc"]),
        week_end_target_soc=float(row.get("week_end_target_soc", 0.75)),
        reserve_soc=float(row["reserve_soc"]),
        home_charger_probability=float(row["home_charger_probability"]),
        work_charger_probability=float(row["work_charger_probability"]),
        home_public_charger_access=float(row.get("home_public_charger_access", 0.30)),
        work_public_charger_access=float(row.get("work_public_charger_access", 0.25)),
        retail_public_charger_access=float(row.get("retail_public_charger_access", 0.45)),
        patch_softmax_temperature=float(row["patch_softmax_temperature"]),
        worker_weekday_work_probability=float(row.get("worker_weekday_work_probability", 0.84)),
        student_weekday_school_probability=float(row.get("student_weekday_school_probability", 0.86)),
        weekday_nonworker_outing_probability=float(row.get("weekday_nonworker_outing_probability", 0.50)),
        after_work_stop_probability=float(row.get("after_work_stop_probability", 0.28)),
        worker_weekend_work_probability=float(row.get("worker_weekend_work_probability", 0.12)),
        weekend_outing_probability=float(row.get("weekend_outing_probability", 0.66)),
        weekend_second_stop_probability=float(row.get("weekend_second_stop_probability", 0.34)),
        baseline_peak_utilization=float(row.get("baseline_peak_utilization", 0.82)),
        grid_ev_load_scale=float(row.get("grid_ev_load_scale", 1.0)),
        vehicle_population_share=float(row.get("vehicle_population_share", 0.46)),
        traffic_attraction_exponent=float(row.get("traffic_attraction_exponent", 0.0)),
        road_graph_source=str(row["road_graph_source"]),
        charger_source=str(row["charger_source"]),
    )


def _candidate_items(
    configs: list[MobilityConfig],
    *,
    max_candidates: int | None,
    candidate_start: int | None,
    candidate_stop: int | None,
) -> list[tuple[int, MobilityConfig]]:
    start = 0 if candidate_start is None else int(candidate_start)
    stop = len(configs) if candidate_stop is None else int(candidate_stop)
    if start < 0:
        raise ValueError("candidate_start must be non-negative.")
    if stop < start:
        raise ValueError("candidate_stop must be greater than or equal to candidate_start.")

    bounded = list(enumerate(configs))[start:min(stop, len(configs))]
    if max_candidates is None or max_candidates >= len(bounded):
        return bounded
    if max_candidates <= 1:
        return bounded[:1]
    positions = np.linspace(0, len(bounded) - 1, max_candidates, dtype=int)
    unique_positions = []
    for pos in positions:
        pos = int(pos)
        if pos not in unique_positions:
            unique_positions.append(pos)
    return [bounded[pos] for pos in unique_positions[:max_candidates]]


def _spread_candidates(configs: list[MobilityConfig], max_candidates: int) -> list[MobilityConfig]:
    if max_candidates >= len(configs):
        return configs
    if max_candidates <= 1:
        return configs[:1]
    indices = np.linspace(0, len(configs) - 1, max_candidates, dtype=int)
    unique_indices = []
    for idx in indices:
        if int(idx) not in unique_indices:
            unique_indices.append(int(idx))
    if 0 not in unique_indices:
        unique_indices.insert(0, 0)
    return [configs[idx] for idx in unique_indices[:max_candidates]]


if __name__ == "__main__":
    cfg = MobilityConfig(ev_probability=0.20, road_graph_source="auto", charger_source="auto")
    summary, per_seed = evaluate_config(cfg)
    print(summary.to_string(index=False))
    print(per_seed.drop(columns=["_detail_reports"], errors="ignore").to_string(index=False))
