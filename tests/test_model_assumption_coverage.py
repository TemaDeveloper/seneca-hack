import os
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_mobility_config_fields_are_named_in_assumptions_doc():
    from mobility_simulator import MobilityConfig

    assumptions = Path("docs/model_assumptions.md").read_text(encoding="utf-8")
    missing = [field.name for field in fields(MobilityConfig) if field.name not in assumptions]

    assert missing == []


def test_dashboard_runtime_constants_are_named_in_assumptions_doc():
    assumptions = Path("docs/model_assumptions.md").read_text(encoding="utf-8")
    required_names = [
        "BASE_EFFICIENCY_KWH_PER_KM",
        "OPTIMAL_TEMP_C",
        "TEMP_EFFICIENCY_LOSS_PER_DEGREE",
        "MAX_EFFICIENCY_LOSS",
        "GTA_BASE_FLEET",
        "DEFAULT_SAMPLED_DRIVERS",
        "MAX_SAMPLED_DRIVERS",
        "HIGH_SCALE_BATCH_SIZE",
    ]

    missing = [name for name in required_names if name not in assumptions]

    assert missing == []
