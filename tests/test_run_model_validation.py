import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_run_metadata_summarizes_validation_fit_and_sensitivity(tmp_path):
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions
    from data_preparation.run_model_validation import _build_run_metadata, _write_run_metadata

    args = argparse.Namespace(
        num_people=120,
        seeds=[101, 202],
        out_dir=tmp_path,
        real_grid=True,
        observed_targets=True,
    )
    seed_reports = pd.DataFrame(
        [
            {"seed": 101, "gate": "mobility", "metric": "legs_per_person_week", "value": 12.0, "status": "PASS", "detail": ""},
            {"seed": 202, "gate": "charging", "metric": "reserve_violations", "value": 0, "status": "FAIL", "detail": "synthetic"},
        ]
    )
    artifacts = {
        "people": pd.DataFrame({"person_id": [1, 2], "is_ev": [True, False]}),
        "charges": pd.DataFrame({"person_id": [1], "energy_delivered_kwh": [12.5]}),
    }
    fit_summary = pd.DataFrame({"mean_loss": [0.7], "max_break_count": [0]})
    fit_results = pd.DataFrame(
        {
            "candidate": [0, 1],
            "mean_loss": [0.7, 0.9],
            "max_break_count": [0, 1],
        }
    )
    sensitivity_report = pd.DataFrame(
        [
            {"gate": "sensitivity", "metric": "low_initial_soc_patch_rises", "value": 0.2, "status": "PASS", "detail": ""},
        ]
    )
    sensitivity_metrics = pd.DataFrame({"scenario": ["base"], "patches_per_ev_week": [0.3]})

    metadata = _build_run_metadata(
        args=args,
        config=MobilityConfig(ev_probability=0.25, road_graph_source="osm"),
        options=ValidationOptions(require_real_grid=True),
        seeds=(101, 202),
        seed_reports=seed_reports,
        artifacts=artifacts,
        fit_summary=fit_summary,
        fit_results=fit_results,
        adaptive_results={"fit_stage1_ranking": fit_results.head(1)},
        sensitivity_report=sensitivity_report,
        sensitivity_metrics=sensitivity_metrics,
        cache_metadata={"osm_route_cache": {"exists": True, "version": 3, "fingerprint_present": True}},
    )

    assert metadata["num_people"] == 120
    assert metadata["seeds"] == [101, 202]
    assert metadata["config"]["ev_probability"] == 0.25
    assert metadata["validation_options"]["require_real_grid"] is True
    assert metadata["artifacts"]["people"]["rows"] == 2
    assert metadata["validation"]["seed_pass_rate"] == 0.5
    assert metadata["validation"]["broken_gate_count"] == 1
    assert metadata["fit"]["candidate_count"] == 2
    assert metadata["fit"]["adaptive_stages"]["fit_stage1_ranking"]["rows"] == 1
    assert metadata["sensitivity"]["broken_gate_count"] == 0
    assert metadata["cache_files"]["osm_route_cache"]["fingerprint_present"] is True

    path = _write_run_metadata(tmp_path, metadata)
    loaded = json.loads(path.read_text())
    assert loaded["validation"]["broken_gates_sample"][0]["metric"] == "reserve_violations"
