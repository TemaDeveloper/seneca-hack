"""
Benchmark high-scale weekly road-grid simulation throughput.

Example:
    PYTHONPATH=backend uv run python data_preparation/benchmark_simulation_scale.py \
      --num-people 200000 --batch-size 25000 --real-grid
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
import subprocess
import time

from mobility_simulator import MobilityConfig, MobilitySimulationEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-people", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--ev-probability", type=float, default=0.20)
    parser.add_argument("--real-grid", action="store_true")
    parser.add_argument("--edge-flow-detail", choices=["fsa", "full"], default="fsa")
    parser.add_argument("--max-seconds", type=float, default=None, help="Fail if total runtime exceeds this threshold.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional path for the benchmark JSON payload.")
    return parser


def _git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _status_path(line: str) -> str:
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path


def _git_dirty(ignore_path: Path | None = None) -> bool | None:
    status = _git_value("status", "--porcelain")
    if status is None:
        return None
    if not status:
        return False
    ignored = None if ignore_path is None else ignore_path.as_posix()
    dirty_lines = [
        line for line in status.splitlines()
        if ignored is None or _status_path(line) != ignored
    ]
    return bool(dirty_lines)


def _command_args_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "num_people": int(args.num_people),
        "batch_size": int(args.batch_size),
        "seed": int(args.seed),
        "ev_probability": float(args.ev_probability),
        "real_grid": bool(args.real_grid),
        "edge_flow_detail": str(args.edge_flow_detail),
        "max_seconds": None if args.max_seconds is None else float(args.max_seconds),
        "output_json": None if args.output_json is None else str(args.output_json),
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, object]:
    cfg = MobilityConfig(
        ev_probability=args.ev_probability,
        road_graph_source="osm" if args.real_grid else "auto",
        charger_source="afdc" if args.real_grid else "auto",
    )
    engine = MobilitySimulationEngine(cfg)
    started = time.perf_counter()
    result = engine.run_weekly_batched_aggregation(
        args.num_people,
        seed=args.seed,
        batch_size=args.batch_size,
        edge_flow_detail=args.edge_flow_detail,
    )
    elapsed = time.perf_counter() - started
    batches = result["batches"]
    max_seconds = None if args.max_seconds is None else float(args.max_seconds)
    payload = {
        "benchmark_schema_version": 2,
        "command_args": _command_args_payload(args),
        "git_commit": _git_value("rev-parse", "--short", "HEAD"),
        "git_dirty": _git_dirty(args.output_json),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "num_people": int(args.num_people),
        "batch_size": int(args.batch_size),
        "seed": int(args.seed),
        "ev_probability": float(args.ev_probability),
        "edge_flow_detail": args.edge_flow_detail,
        "road_graph_source": engine.road_network.summary().source,
        "charger_count": int(len(engine.charger_catalog.public)),
        "batches": int(len(batches)),
        "itinerary_rows": int(batches["itinerary_rows"].sum()) if not batches.empty else 0,
        "leg_rows": int(batches["leg_rows"].sum()) if not batches.empty else 0,
        "charge_rows": int(batches["charge_rows"].sum()) if not batches.empty else 0,
        "hourly_rows": int(len(result["hourly"])),
        "grid_rows": int(len(result["grid_load"])),
        "edge_flow_rows": int(len(result["edge_flows"])),
        "charge_energy_kwh": round(float(batches["charge_energy_kwh"].sum()), 3) if not batches.empty else 0.0,
        "hourly_energy_kwh": round(float(result["hourly"]["energy_kwh"].sum()), 3) if not result["hourly"].empty else 0.0,
        "edge_vehicle_count": round(float(result["edge_flows"]["vehicle_count"].sum()), 3) if not result["edge_flows"].empty else 0.0,
        "edge_ev_count": round(float(result["edge_flows"]["ev_count"].sum()), 3) if not result["edge_flows"].empty else 0.0,
        "edge_route_km": round(float(result["edge_flows"]["route_km"].sum()), 3) if not result["edge_flows"].empty else 0.0,
        "total_s": round(elapsed, 3),
        "people_per_second": round(args.num_people / elapsed, 1) if elapsed > 0 else None,
        "max_seconds": max_seconds,
        "passed_max_seconds": None if max_seconds is None else elapsed <= max_seconds,
    }
    return payload


def main(args: argparse.Namespace | None = None) -> dict[str, object]:
    if args is None:
        parser = build_parser()
        args = parser.parse_args()
    payload = run_benchmark(args)
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(text + "\n", encoding="utf-8")
    if payload["passed_max_seconds"] is False:
        raise SystemExit(
            f"Benchmark exceeded --max-seconds: {payload['total_s']}s > {payload['max_seconds']}s"
        )
    return payload


if __name__ == "__main__":
    main()
