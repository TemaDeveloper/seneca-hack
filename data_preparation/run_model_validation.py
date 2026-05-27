"""
Run road-grid model validation and optional calibration scoring.

Example:
    PYTHONPATH=backend uv run python data_preparation/run_model_validation.py \
      --real-grid --num-people 1000 --seeds 101 202 303 --out-dir backend/data/validation
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import platform
import sys
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from charger_catalog import AFDC_CHARGERS_CSV, OSM_CHARGERS_CSV
from activity_poi_catalog import ACTIVITY_FSA_ATTRACTIONS_CSV, ACTIVITY_NODE_ATTRACTIONS_CSV, ACTIVITY_POI_METADATA_JSON, ACTIVITY_POIS_CSV
from mobility_simulator import MobilityConfig
from spatial_assembler import load_enriched_geodataframe
from model_calibration import evaluate_config, fit_config, selected_candidate_indices
from road_network import FSA_EDGE_TEMPLATE_CACHE, FSA_ROUTE_CACHE, OSM_EDGE_TEMPLATE_CACHE, OSM_GRAPH_PICKLE, OSM_GRAPHML, OSM_ROUTE_CACHE
from simulation_validation import ValidationOptions, validate_multi_seed, validate_sensitivity_scenarios_multi_seed, validate_weekly_simulation


def _top_candidate_ids(ranking: pd.DataFrame, count: int, *, preferred: int | None = None) -> list[int]:
    if ranking.empty:
        return [] if preferred is None else [preferred]
    ordered = ranking.sort_values(["max_break_count", "mean_loss", "std_loss", "candidate"])
    ids = [int(candidate) for candidate in ordered["candidate"].head(max(count, 0)).tolist()]
    if preferred is not None and preferred in set(int(candidate) for candidate in ranking["candidate"]):
        ids = [preferred, *[candidate for candidate in ids if candidate != preferred]]
    return ids[:max(count, 1)]


def _adaptive_checkpoint_path(
    out_dir: Path | None,
    stage: str,
    *,
    people: int,
    seeds: tuple[int, ...],
    detail: str,
    all_candidates: bool,
    max_candidates: int | None,
    top: int | None,
) -> Path | None:
    if out_dir is None:
        return None
    seed_part = "-".join(str(seed) for seed in seeds) or "none"
    candidate_part = "all" if all_candidates else f"n{max_candidates}" if max_candidates is not None else "selected"
    top_part = "" if top is None else f"_top{top}"
    return out_dir / f"fit_{stage}_p{people}_s{seed_part}_{detail}_{candidate_part}{top_part}_checkpoint.csv"


def _json_safe(value: object) -> object:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _dataframe_summary(frame: pd.DataFrame | None) -> dict[str, object] | None:
    if frame is None:
        return None
    return {
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
    }


def _records(frame: pd.DataFrame | None, *, limit: int | None = None) -> list[dict[str, object]]:
    if frame is None:
        return []
    sample = frame if limit is None else frame.head(limit)
    return _json_safe(sample.to_dict(orient="records"))  # type: ignore[return-value]


def _validation_summary(seed_reports: pd.DataFrame) -> dict[str, object]:
    broken = seed_reports[seed_reports["status"] != "PASS"] if "status" in seed_reports else pd.DataFrame()
    if "seed" in seed_reports and "status" in seed_reports and not seed_reports.empty:
        pass_by_seed = seed_reports.groupby("seed")["status"].apply(lambda values: bool((values == "PASS").all()))
        seed_count = int(len(pass_by_seed))
        seed_pass_count = int(pass_by_seed.sum())
        seed_pass_rate = seed_pass_count / seed_count if seed_count else None
    else:
        seed_count = 0
        seed_pass_count = 0
        seed_pass_rate = None
    broken_by_metric = broken["metric"].value_counts().to_dict() if "metric" in broken else {}
    return {
        "seed_count": seed_count,
        "seed_pass_count": seed_pass_count,
        "seed_pass_rate": seed_pass_rate,
        "gate_row_count": int(len(seed_reports)),
        "broken_gate_count": int(len(broken)),
        "broken_gate_counts_by_metric": _json_safe(broken_by_metric),
        "broken_gates_sample": _records(broken, limit=25),
    }


def _sensitivity_summary(report: pd.DataFrame | None, metrics: pd.DataFrame | None) -> dict[str, object] | None:
    if report is None and metrics is None:
        return None
    broken = report[report["status"] != "PASS"] if report is not None and "status" in report else pd.DataFrame()
    return {
        "report": _dataframe_summary(report),
        "metrics": _dataframe_summary(metrics),
        "broken_gate_count": int(len(broken)),
        "broken_gates_sample": _records(broken, limit=25),
        "report_rows": _records(report, limit=None),
        "metrics_rows": _records(metrics, limit=None),
    }


def _fit_summary_payload(
    fit_summary: pd.DataFrame | None,
    fit_results: pd.DataFrame | None,
    adaptive_results: dict[str, pd.DataFrame] | None,
) -> dict[str, object] | None:
    if fit_summary is None and fit_results is None and not adaptive_results:
        return None
    payload: dict[str, object] = {
        "current_summary": _dataframe_summary(fit_summary),
        "current_summary_rows": _records(fit_summary, limit=None),
        "candidate_ranking": _dataframe_summary(fit_results),
        "top_candidates": _records(fit_results, limit=10),
    }
    if fit_results is not None and "candidate" in fit_results:
        payload["candidate_count"] = int(fit_results["candidate"].nunique())
    if adaptive_results:
        payload["adaptive_stages"] = {
            name: _dataframe_summary(result)
            for name, result in adaptive_results.items()
        }
    return payload


def _cache_file_metadata() -> dict[str, dict[str, object]]:
    paths = {
        "osm_graphml": OSM_GRAPHML,
        "osm_graph_pickle": OSM_GRAPH_PICKLE,
        "osm_route_cache": OSM_ROUTE_CACHE,
        "fsa_route_cache": FSA_ROUTE_CACHE,
        "osm_edge_template_cache": OSM_EDGE_TEMPLATE_CACHE,
        "fsa_edge_template_cache": FSA_EDGE_TEMPLATE_CACHE,
        "afdc_chargers": AFDC_CHARGERS_CSV,
        "osm_chargers": OSM_CHARGERS_CSV,
        "activity_pois": ACTIVITY_POIS_CSV,
        "activity_fsa_attractions": ACTIVITY_FSA_ATTRACTIONS_CSV,
        "activity_node_attractions": ACTIVITY_NODE_ATTRACTIONS_CSV,
        "activity_poi_metadata": ACTIVITY_POI_METADATA_JSON,
    }
    pickle_headers = {"osm_route_cache", "fsa_route_cache", "osm_edge_template_cache", "fsa_edge_template_cache"}
    metadata: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        entry: dict[str, object] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            stat = path.stat()
            entry["size_bytes"] = int(stat.st_size)
            entry["modified_utc"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            if name in pickle_headers:
                entry.update(_pickle_cache_header(path))
        metadata[name] = entry
    return metadata


def _pickle_cache_header(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except Exception as exc:
        return {"payload_read_error": f"{type(exc).__name__}: {exc}"}

    header: dict[str, object] = {"payload_type": type(payload).__name__}
    if isinstance(payload, dict):
        header["version"] = _json_safe(payload.get("version"))
        header["fingerprint_present"] = bool(payload.get("fingerprint"))
        for key in ("routes", "templates"):
            value = payload.get(key)
            if hasattr(value, "__len__"):
                header[f"{key}_count"] = int(len(value))
    return header


def _build_run_metadata(
    *,
    args: argparse.Namespace,
    config: MobilityConfig,
    options: ValidationOptions,
    seeds: tuple[int, ...],
    seed_reports: pd.DataFrame,
    artifacts: dict[str, pd.DataFrame],
    fit_summary: pd.DataFrame | None = None,
    fit_results: pd.DataFrame | None = None,
    adaptive_results: dict[str, pd.DataFrame] | None = None,
    sensitivity_report: pd.DataFrame | None = None,
    sensitivity_metrics: pd.DataFrame | None = None,
    cache_metadata: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": {
            "argv": sys.argv,
            "args": vars(args),
        },
        "runtime": {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
        },
        "num_people": int(args.num_people),
        "seeds": seeds,
        "config": config,
        "validation_options": options,
        "artifacts": {
            name: _dataframe_summary(frame)
            for name, frame in artifacts.items()
        },
        "validation": _validation_summary(seed_reports),
        "fit": _fit_summary_payload(fit_summary, fit_results, adaptive_results),
        "sensitivity": _sensitivity_summary(sensitivity_report, sensitivity_metrics),
        "cache_files": cache_metadata if cache_metadata is not None else _cache_file_metadata(),
    }
    return _json_safe(metadata)  # type: ignore[return-value]


def _write_run_metadata(out_dir: Path, metadata: dict[str, object]) -> Path:
    path = out_dir / "run_metadata.json"
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    default_cfg = MobilityConfig()
    parser.add_argument("--num-people", type=int, default=1_000)
    parser.add_argument("--seeds", nargs="+", type=int, default=[101, 202, 303])
    parser.add_argument("--ev-probability", type=float, default=0.20)
    parser.add_argument("--grid-ev-load-scale", type=float, default=1.0, help="Multiplier for EV charging load in grid-capacity aggregation.")
    parser.add_argument("--population-scale-grid", action="store_true", help="Scale EV grid load by observed FSA population divided by num_people.")
    parser.add_argument(
        "--population-share",
        type=float,
        default=None,
        help=f"Share of observed FSA population represented by simulated people when --population-scale-grid is used. Default: vehicle population proxy {default_cfg.vehicle_population_share:.2f}.",
    )
    parser.add_argument("--real-grid", action="store_true", help="Require cached OSM road graph and real AFDC/OSM chargers.")
    parser.add_argument("--itinerary-model", choices=["template", "intraday"], default="template", help="Weekly route planner to validate.")
    parser.add_argument("--activity-poi-source", choices=["auto", "cache", "osm", "pbf", "none"], default="auto", help="Activity POI attraction source for intraday routes.")
    parser.add_argument("--force-activity-pois", action="store_true", help="Refresh activity POIs when an explicit fetch/parse source is used.")
    parser.add_argument("--observed-targets", action="store_true", help="Validate against available observed/proxy data artifacts.")
    parser.add_argument("--repeat-week", action="store_true", help="Replay the sampled week from final SoC to validate repeated-week SoC stability.")
    parser.add_argument("--require-sample-evidence", action="store_true", help="Break strict validation when rare-event gates do not have enough sampled events.")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--validation-jobs", type=int, default=1, help="Parallel worker processes for multi-seed validation.")
    parser.add_argument("--sensitivity-jobs", type=int, default=None, help="Parallel worker processes for sensitivity seeds. Default: --validation-jobs.")
    parser.add_argument("--fit", action="store_true", help="Run candidate-grid calibration scoring after validation.")
    parser.add_argument("--sensitivity", action="store_true", help="Run directional stress checks.")
    parser.add_argument("--fit-strategy", choices=["grid", "adaptive"], default="grid", help="Use brute-force grid fitting or staged adaptive fitting.")
    parser.add_argument("--fit-edge-flow-detail", choices=["full", "fsa"], default="fsa", help="Edge-flow aggregation used during fitting. Full validation always uses the main validation options.")
    parser.add_argument("--sensitivity-edge-flow-detail", choices=["full", "fsa"], default="fsa", help="Edge-flow aggregation used during directional stress checks.")
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--all-candidates", action="store_true", help="Fit every candidate in the selected candidate range instead of a representative spread.")
    parser.add_argument("--candidate-start", type=int, default=None, help="Zero-based inclusive candidate-grid start index for chunked exhaustive fitting.")
    parser.add_argument("--candidate-stop", type=int, default=None, help="Zero-based exclusive candidate-grid stop index for chunked exhaustive fitting.")
    parser.add_argument("--fit-num-people", type=int, default=None, help="Population size for candidate fitting. Default: max(250, num_people // 2).")
    parser.add_argument("--fit-seeds", nargs="+", type=int, default=None, help="Seeds for candidate fitting. Default: first two validation seeds.")
    parser.add_argument("--fit-jobs", type=int, default=1, help="Parallel worker processes for candidate-grid fitting.")
    parser.add_argument("--fit-checkpoint", type=Path, default=None, help="CSV checkpoint for completed fit candidates. Defaults to out_dir/fit_candidate_checkpoint.csv when out_dir is set.")
    parser.add_argument("--resume-fit", action="store_true", help="Reuse completed rows from the fit checkpoint.")
    parser.add_argument("--adaptive-stage1-candidates", type=int, default=128, help="Representative candidate count for the cheap adaptive screen.")
    parser.add_argument("--adaptive-stage1-people", type=int, default=120, help="Population size for the cheap adaptive screen.")
    parser.add_argument("--adaptive-stage2-top", type=int, default=24, help="Number of stage-1 candidates to re-evaluate at fit scale.")
    parser.add_argument("--adaptive-final-top", type=int, default=6, help="Number of stage-2 candidates to confirm at validation scale.")
    args = parser.parse_args()

    grid_ev_load_scale = args.grid_ev_load_scale
    population_share = default_cfg.vehicle_population_share if args.population_share is None else args.population_share
    if population_share < 0:
        raise SystemExit("--population-share must be non-negative.")
    if args.population_scale_grid:
        gdf = load_enriched_geodataframe()
        population = pd.to_numeric(gdf.get("population_2021"), errors="coerce").fillna(0.0)
        total_population = float(population.sum())
        if total_population <= 0:
            raise SystemExit("Population scaling requested, but no positive population_2021 values are available. Run data_preparation/fetch_statcan_fsa_population.py.")
        grid_ev_load_scale = total_population * population_share / max(args.num_people, 1)
        print(f"Population grid scale: {grid_ev_load_scale:.3f} ({total_population:,.0f} population * {population_share} / {args.num_people} simulated people)")

    cfg = MobilityConfig(
        ev_probability=args.ev_probability,
        grid_ev_load_scale=grid_ev_load_scale,
        vehicle_population_share=population_share,
        road_graph_source="osm" if args.real_grid else "auto",
        charger_source="afdc" if args.real_grid else "auto",
        itinerary_model=args.itinerary_model,
        activity_poi_source=args.activity_poi_source,
        force_activity_poi_download=args.force_activity_pois,
    )
    opts = ValidationOptions(
        require_real_grid=args.real_grid,
        require_real_chargers=args.real_grid,
        include_observed_targets=args.observed_targets,
        include_repeat_week=args.repeat_week,
        require_sample_evidence=args.require_sample_evidence,
    )
    seeds = tuple(args.seeds)

    if len(seeds) == 1:
        report, artifacts = validate_weekly_simulation(args.num_people, seeds[0], cfg, opts)
        print(report.to_string(index=False))
        seed_reports = report.assign(seed=seeds[0])
    else:
        suite_report, artifacts = validate_multi_seed(args.num_people, seeds, cfg, opts, jobs=max(1, args.validation_jobs))
        print(suite_report.to_string(index=False))
        seed_reports = artifacts["seed_reports"]
        broken = seed_reports[seed_reports["status"] != "PASS"]
        if not broken.empty:
            print("\nBroken gates:")
            print(broken.to_string(index=False))

    fit_summary = None
    fit_results = None
    adaptive_results: dict[str, pd.DataFrame] = {}
    if args.fit:
        fit_num_people = args.fit_num_people if args.fit_num_people is not None else max(250, args.num_people // 2)
        fit_seeds = tuple(args.fit_seeds) if args.fit_seeds is not None else seeds[:2]
        if fit_num_people <= 0:
            raise SystemExit("--fit-num-people must be positive.")
        if not fit_seeds:
            raise SystemExit("--fit-seeds must include at least one seed.")
        if args.candidate_start is not None and args.candidate_start < 0:
            raise SystemExit("--candidate-start must be non-negative.")
        if args.candidate_stop is not None and args.candidate_start is not None and args.candidate_stop < args.candidate_start:
            raise SystemExit("--candidate-stop must be greater than or equal to --candidate-start.")
        fit_checkpoint = args.fit_checkpoint
        if fit_checkpoint is None and args.out_dir is not None:
            fit_checkpoint = args.out_dir / "fit_candidate_checkpoint.csv"
        fit_opts = replace(opts, edge_flow_detail=args.fit_edge_flow_detail)

        def _print_fit_progress(row: dict[str, object], completed: int, total: int) -> None:
            print(
                "Fit candidate "
                f"{completed}/{total}: id={int(row['candidate'])} "
                f"loss={float(row['mean_loss']):.4f} "
                f"breaks={int(row['max_break_count'])}",
                flush=True,
            )

        if args.fit_strategy == "adaptive":
            stage1_max = None if args.all_candidates else args.adaptive_stage1_candidates
            stage1_indices = selected_candidate_indices(
                cfg,
                max_candidates=stage1_max,
                candidate_start=args.candidate_start,
                candidate_stop=args.candidate_stop,
            )
            stage1_checkpoint = _adaptive_checkpoint_path(
                args.out_dir,
                "stage1",
                people=args.adaptive_stage1_people,
                seeds=fit_seeds[:1],
                detail=args.fit_edge_flow_detail,
                all_candidates=args.all_candidates,
                max_candidates=stage1_max,
                top=None,
            )
            stage2_checkpoint = _adaptive_checkpoint_path(
                args.out_dir,
                "stage2",
                people=fit_num_people,
                seeds=fit_seeds,
                detail=args.fit_edge_flow_detail,
                all_candidates=False,
                max_candidates=None,
                top=args.adaptive_stage2_top,
            )
            final_checkpoint = fit_checkpoint

            print(f"\nAdaptive fit stage 1: {len(stage1_indices)} candidates x {args.adaptive_stage1_people} people x seed {fit_seeds[:1]}")
            stage1 = fit_config(
                cfg,
                num_people=args.adaptive_stage1_people,
                seeds=fit_seeds[:1],
                options=fit_opts,
                candidate_indices=stage1_indices,
                jobs=max(1, args.fit_jobs),
                progress=_print_fit_progress,
                checkpoint_path=stage1_checkpoint,
                resume=args.resume_fit,
                run_validation=False,
            )
            stage2_indices = _top_candidate_ids(stage1, args.adaptive_stage2_top, preferred=0)
            print(f"\nAdaptive fit stage 2: {len(stage2_indices)} candidates x {fit_num_people} people x seeds {fit_seeds}")
            stage2 = fit_config(
                cfg,
                num_people=fit_num_people,
                seeds=fit_seeds,
                options=fit_opts,
                candidate_indices=stage2_indices,
                jobs=max(1, args.fit_jobs),
                progress=_print_fit_progress,
                checkpoint_path=stage2_checkpoint,
                resume=args.resume_fit,
                run_validation=False,
            )
            final_indices = _top_candidate_ids(stage2, args.adaptive_final_top, preferred=0)
            print(f"\nAdaptive fit final: {len(final_indices)} candidates x {args.num_people} people x seeds {seeds}")
            fit_results = fit_config(
                cfg,
                num_people=args.num_people,
                seeds=seeds,
                options=opts,
                candidate_indices=final_indices,
                jobs=max(1, args.fit_jobs),
                progress=_print_fit_progress,
                checkpoint_path=final_checkpoint,
                resume=args.resume_fit,
            )
            default_row = fit_results[fit_results["candidate"] == 0].drop(columns=["candidate"], errors="ignore")
            if default_row.empty:
                fit_summary, _ = evaluate_config(cfg, num_people=args.num_people, seeds=seeds, options=opts)
            else:
                fit_summary = default_row.reset_index(drop=True)
            adaptive_results = {
                "fit_stage1_ranking": stage1,
                "fit_stage2_ranking": stage2,
                "fit_candidate_ranking": fit_results,
            }
        else:
            fit_summary, _ = evaluate_config(cfg, num_people=args.num_people, seeds=seeds, options=fit_opts)
            fit_results = fit_config(
                cfg,
                num_people=fit_num_people,
                seeds=fit_seeds,
                options=fit_opts,
                max_candidates=None if args.all_candidates else args.max_candidates,
                candidate_start=args.candidate_start,
                candidate_stop=args.candidate_stop,
                jobs=max(1, args.fit_jobs),
                progress=_print_fit_progress,
                checkpoint_path=fit_checkpoint,
                resume=args.resume_fit,
            )
        print("\nCurrent fit summary:")
        print(fit_summary.to_string(index=False))
        print("\nCandidate fit ranking:")
        print(fit_results.head(10).to_string(index=False))

    sensitivity_report = None
    sensitivity_metrics = None
    if args.sensitivity:
        sensitivity_opts = replace(opts, edge_flow_detail=args.sensitivity_edge_flow_detail)
        sensitivity_report, sensitivity_metrics = validate_sensitivity_scenarios_multi_seed(
            num_people=args.num_people,
            seeds=seeds,
            config=cfg,
            options=sensitivity_opts,
            jobs=max(1, args.sensitivity_jobs if args.sensitivity_jobs is not None else args.validation_jobs),
        )
        print("\nSensitivity report:")
        print(sensitivity_report.to_string(index=False))
        print("\nSensitivity metrics:")
        print(sensitivity_metrics.to_string(index=False))

    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        seed_reports.to_csv(args.out_dir / "validation_seed_reports.csv", index=False)
        for name in ["people", "itinerary", "legs", "charges", "hourly", "grid_load", "edge_flows"]:
            if name in artifacts:
                artifacts[name].to_csv(args.out_dir / f"{name}.csv", index=False)
        if fit_summary is not None:
            fit_summary.to_csv(args.out_dir / "fit_current_summary.csv", index=False)
        if fit_results is not None:
            fit_results.to_csv(args.out_dir / "fit_candidate_ranking.csv", index=False)
        for name, result in adaptive_results.items():
            result.to_csv(args.out_dir / f"{name}.csv", index=False)
        if sensitivity_report is not None:
            sensitivity_report.to_csv(args.out_dir / "sensitivity_report.csv", index=False)
        if sensitivity_metrics is not None:
            sensitivity_metrics.to_csv(args.out_dir / "sensitivity_metrics.csv", index=False)
        metadata = _build_run_metadata(
            args=args,
            config=cfg,
            options=opts,
            seeds=seeds,
            seed_reports=seed_reports,
            artifacts=artifacts,
            fit_summary=fit_summary,
            fit_results=fit_results,
            adaptive_results=adaptive_results,
            sensitivity_report=sensitivity_report,
            sensitivity_metrics=sensitivity_metrics,
        )
        _write_run_metadata(args.out_dir, metadata)

    broken = seed_reports[seed_reports["status"] != "PASS"]
    if sensitivity_report is not None:
        broken = pd.concat([broken, sensitivity_report[sensitivity_report["status"] != "PASS"]], ignore_index=True)
    if not broken.empty:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
