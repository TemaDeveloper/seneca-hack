"""
Benchmark high-scale weekly road-grid simulation throughput.

Example:
    PYTHONPATH=backend uv run python data_preparation/benchmark_simulation_scale.py \
      --num-people 200000 --batch-size 25000 --real-grid
"""

from __future__ import annotations

import argparse
import json
import time

from mobility_simulator import MobilityConfig, MobilitySimulationEngine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--num-people", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=25_000)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--ev-probability", type=float, default=0.20)
    parser.add_argument("--real-grid", action="store_true")
    parser.add_argument("--edge-flow-detail", choices=["fsa", "full"], default="fsa")
    args = parser.parse_args()

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
    payload = {
        "num_people": int(args.num_people),
        "batch_size": int(args.batch_size),
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
        "total_s": round(elapsed, 3),
        "people_per_second": round(args.num_people / elapsed, 1) if elapsed > 0 else None,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
