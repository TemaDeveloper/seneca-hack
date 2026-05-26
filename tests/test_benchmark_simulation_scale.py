import json
import os
import sys

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
