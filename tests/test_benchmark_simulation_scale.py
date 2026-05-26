import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_scale_benchmark_writes_thresholded_evidence_json(tmp_path):
    from data_preparation.benchmark_simulation_scale import build_parser, main

    output_path = tmp_path / "benchmark.json"
    parser = build_parser()
    args = parser.parse_args([
        "--num-people", "80",
        "--batch-size", "40",
        "--edge-flow-detail", "fsa",
        "--max-seconds", "120",
        "--output-json", str(output_path),
    ])

    payload = main(args)
    saved = json.loads(output_path.read_text())

    assert saved == payload
    assert payload["num_people"] == 80
    assert payload["batches"] == 2
    assert payload["passed_max_seconds"] is True
    assert payload["charge_energy_kwh"] == payload["hourly_energy_kwh"]
    assert payload["edge_route_km"] > 0


def test_scale_benchmark_threshold_failure_is_machine_readable():
    from data_preparation.benchmark_simulation_scale import build_parser, main

    parser = build_parser()
    args = parser.parse_args([
        "--num-people", "20",
        "--batch-size", "10",
        "--edge-flow-detail", "fsa",
        "--max-seconds", "0",
    ])

    with pytest.raises(SystemExit, match="Benchmark exceeded --max-seconds"):
        main(args)


def test_committed_300k_benchmark_artifact_proves_speed_gate():
    artifact = json.loads(Path("docs/benchmarks/high_scale_benchmark_300000.json").read_text())

    assert artifact["num_people"] >= 300_000
    assert artifact["road_graph_source"] == "osm"
    assert artifact["edge_flow_detail"] == "fsa"
    assert artifact["max_seconds"] == 600.0
    assert artifact["passed_max_seconds"] is True
    assert artifact["total_s"] <= artifact["max_seconds"]
    assert artifact["people_per_second"] >= 500

    assert artifact["batch_size"] == 25_000
    assert artifact["batches"] == 12
    assert artifact["charger_count"] > 0
    assert artifact["itinerary_rows"] == artifact["leg_rows"]
    assert artifact["leg_rows"] > artifact["num_people"]
    assert artifact["charge_rows"] > 0
    assert artifact["hourly_rows"] > 0
    assert artifact["grid_rows"] == 260 * 168
    assert artifact["edge_flow_rows"] > 0
    assert artifact["edge_vehicle_count"] >= artifact["edge_ev_count"] > 0
    assert artifact["edge_route_km"] > 0
    assert artifact["charge_energy_kwh"] == artifact["hourly_energy_kwh"]
