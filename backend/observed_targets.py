"""
Observed-data target extraction for model calibration.

These targets use the public/data artifacts currently present in the repo:
- Toronto traffic-count-derived zone weights (`zone_weights.json`)
- Toronto traffic-count-derived FSA weights (`toronto_traffic_fsa_counts.csv`)
- AFDC/NREL charger catalog mapped to FSA zones
- IESO-style hourly baseline shape (`ieso_load_profile.csv`)

They are not a replacement for TTS trip records or charger-session logs. They
are the strongest observed/proxy targets available in the current worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd


DATA_DIR = Path(__file__).resolve().parent / "data"
ZONE_WEIGHTS_JSON = DATA_DIR / "zone_weights.json"
TRAFFIC_FSA_COUNTS_CSV = DATA_DIR / "toronto_traffic_fsa_counts.csv"
IESO_LOAD_PROFILE_CSV = DATA_DIR / "ieso_load_profile.csv"
AFDC_CHARGERS_CSV = DATA_DIR / "cache" / "afdc_on_ev_chargers.csv"

ZONE_TYPES = ["residential", "leisure", "office_park", "retail_hub", "transit_hub"]
MIN_PUBLIC_CHARGE_EVENTS_FOR_DISTRIBUTION = 30


@dataclass(frozen=True)
class ObservedTargets:
    morning_zone_weights: dict[str, float]
    evening_zone_weights: dict[str, float]
    morning_fsa_weights: dict[str, float]
    evening_fsa_weights: dict[str, float]
    charger_zone_weights: dict[str, float]
    ieso_hourly_profile: dict[int, float]


def load_observed_targets() -> ObservedTargets:
    zone_weights = _load_zone_weights()
    return ObservedTargets(
        morning_zone_weights=zone_weights["Morning"],
        evening_zone_weights=zone_weights["Evening"],
        morning_fsa_weights=_load_traffic_fsa_weights("am_peak_vehicle"),
        evening_fsa_weights=_load_traffic_fsa_weights("pm_peak_vehicle"),
        charger_zone_weights=_load_charger_zone_weights(),
        ieso_hourly_profile=_load_ieso_profile(),
    )


def compute_observed_target_metrics(artifacts: dict[str, pd.DataFrame], targets: ObservedTargets | None = None) -> dict[str, float]:
    targets = targets or load_observed_targets()
    itinerary = artifacts.get("itinerary", pd.DataFrame())
    charges = artifacts.get("charges", pd.DataFrame())
    hourly = artifacts.get("hourly", pd.DataFrame())

    morning = itinerary[
        (itinerary.get("day", pd.Series(dtype=float)) < 5)
        & ((itinerary.get("depart_hour_abs", pd.Series(dtype=float)) % 24).between(6, 10.5))
    ]
    evening = itinerary[
        (itinerary.get("day", pd.Series(dtype=float)) < 5)
        & ((itinerary.get("depart_hour_abs", pd.Series(dtype=float)) % 24).between(15, 19.5))
    ]
    public_charges = charges[charges.get("charger_source", pd.Series(dtype=object)).isin(["afdc", "osm"])] if not charges.empty else charges
    edge_flows = artifacts.get("edge_flows", pd.DataFrame())

    metrics = {
        "observed_morning_zone_l1": _distribution_l1(_endpoint_zone_distribution(morning), targets.morning_zone_weights),
        "observed_evening_zone_l1": _distribution_l1(_endpoint_zone_distribution(evening), targets.evening_zone_weights),
        "observed_morning_fsa_l1": _fsa_distribution_l1(_edge_fsa_distribution(edge_flows, 6, 10), targets.morning_fsa_weights),
        "observed_evening_fsa_l1": _fsa_distribution_l1(_edge_fsa_distribution(edge_flows, 15, 19), targets.evening_fsa_weights),
        "observed_public_charger_zone_l1": _distribution_l1(_zone_distribution(public_charges, "zone_type", weight_col="energy_delivered_kwh"), targets.charger_zone_weights),
        "observed_hourly_load_corr": _hourly_profile_corr(hourly, targets.ieso_hourly_profile),
    }
    return metrics


def observed_target_report(artifacts: dict[str, pd.DataFrame], targets: ObservedTargets | None = None) -> pd.DataFrame:
    metrics = compute_observed_target_metrics(artifacts, targets)
    charges = artifacts.get("charges", pd.DataFrame())
    public_charges = charges[charges.get("charger_source", pd.Series(dtype=object)).isin(["afdc", "osm"])] if not charges.empty else charges
    public_event_count = int((public_charges.get("energy_delivered_kwh", pd.Series(dtype=float)).fillna(0.0) > 0).sum()) if not public_charges.empty else 0
    public_distribution_sufficient = public_event_count >= MIN_PUBLIC_CHARGE_EVENTS_FOR_DISTRIBUTION
    public_charger_status = metrics["observed_public_charger_zone_l1"] <= 1.00 or not public_distribution_sufficient
    public_charger_detail = "L1 distance between public charge-event energy zone mix and AFDC charger zone distribution."
    if not public_distribution_sufficient:
        public_charger_detail += f" Strict gate deferred because only {public_event_count} public charge events were sampled."
    rows = [
        {
            "target": "traffic_morning_zone_l1",
            "value": round(metrics["observed_morning_zone_l1"], 4),
            "status": "PASS" if metrics["observed_morning_zone_l1"] <= 0.80 else "BREAK",
            "detail": "L1 distance between simulated weekday AM trip endpoint-zone exposure and traffic-count-derived Morning zone weights.",
        },
        {
            "target": "traffic_evening_zone_l1",
            "value": round(metrics["observed_evening_zone_l1"], 4),
            "status": "PASS" if metrics["observed_evening_zone_l1"] <= 0.80 else "BREAK",
            "detail": "L1 distance between simulated weekday PM trip endpoint-zone exposure and traffic-count-derived Evening zone weights.",
        },
        {
            "target": "traffic_morning_fsa_l1",
            "value": round(metrics["observed_morning_fsa_l1"], 4) if np.isfinite(metrics["observed_morning_fsa_l1"]) else "nan",
            "status": "PASS" if (not np.isfinite(metrics["observed_morning_fsa_l1"]) or metrics["observed_morning_fsa_l1"] <= 1.60) else "BREAK",
            "detail": "L1 distance between simulated weekday AM route-edge exposure by FSA and Toronto traffic-count FSA weights. Loose because counts are Toronto-only intersections.",
        },
        {
            "target": "traffic_evening_fsa_l1",
            "value": round(metrics["observed_evening_fsa_l1"], 4) if np.isfinite(metrics["observed_evening_fsa_l1"]) else "nan",
            "status": "PASS" if (not np.isfinite(metrics["observed_evening_fsa_l1"]) or metrics["observed_evening_fsa_l1"] <= 1.60) else "BREAK",
            "detail": "L1 distance between simulated weekday PM route-edge exposure by FSA and Toronto traffic-count FSA weights. Loose because counts are Toronto-only intersections.",
        },
        {
            "target": "public_charger_zone_l1",
            "value": round(metrics["observed_public_charger_zone_l1"], 4),
            "status": "PASS" if public_charger_status else "BREAK",
            "detail": public_charger_detail,
        },
        {
            "target": "hourly_load_profile_corr",
            "value": round(metrics["observed_hourly_load_corr"], 4),
            "status": "PASS" if metrics["observed_hourly_load_corr"] >= -0.25 else "BREAK",
            "detail": "Correlation between EV hourly charging energy and IESO-style baseline shape. Loose gate: EV load need not match baseline.",
        },
    ]
    return pd.DataFrame(rows)


def _load_zone_weights() -> dict[str, dict[str, float]]:
    if not ZONE_WEIGHTS_JSON.exists():
        return {
            "Morning": _uniform_distribution(),
            "Evening": _uniform_distribution(),
        }
    with ZONE_WEIGHTS_JSON.open() as fh:
        raw = json.load(fh)
    return {
        "Morning": _complete_distribution(raw.get("Morning", {})),
        "Evening": _complete_distribution(raw.get("Evening", {})),
    }


def _load_charger_zone_weights() -> dict[str, float]:
    if not AFDC_CHARGERS_CSV.exists():
        return _uniform_distribution()
    chargers = pd.read_csv(AFDC_CHARGERS_CSV)
    return _zone_distribution(chargers, "zone_type")


def _load_traffic_fsa_weights(weight_col: str) -> dict[str, float]:
    if not TRAFFIC_FSA_COUNTS_CSV.exists():
        return {}
    counts = pd.read_csv(TRAFFIC_FSA_COUNTS_CSV)
    if counts.empty or "fsa" not in counts or weight_col not in counts:
        return {}
    weights = counts.groupby("fsa")[weight_col].sum().astype(float)
    weights = weights[weights > 0]
    total = float(weights.sum())
    if total <= 0:
        return {}
    return {str(fsa): float(value / total) for fsa, value in weights.items()}


def _load_ieso_profile() -> dict[int, float]:
    if not IESO_LOAD_PROFILE_CSV.exists():
        return {hour: 1.0 / 24 for hour in range(24)}
    profile = pd.read_csv(IESO_LOAD_PROFILE_CSV)
    weights = profile.set_index("hour")["load_fraction"].astype(float)
    total = float(weights.sum())
    if total <= 0:
        return {hour: 1.0 / 24 for hour in range(24)}
    return {int(hour): float(value / total) for hour, value in weights.items()}


def _zone_distribution(df: pd.DataFrame, zone_col: str, weight_col: str | None = None) -> dict[str, float]:
    if df.empty or zone_col not in df:
        return _uniform_distribution()
    if weight_col and weight_col in df:
        weights = df.groupby(zone_col)[weight_col].sum()
    else:
        weights = df[zone_col].value_counts()
    values = {zone: float(weights.get(zone, 0.0)) for zone in ZONE_TYPES}
    return _complete_distribution(values)


def _endpoint_zone_distribution(itinerary: pd.DataFrame) -> dict[str, float]:
    if itinerary.empty:
        return _uniform_distribution()
    parts = []
    for column in ["origin_zone_type", "dest_zone_type"]:
        if column in itinerary:
            parts.append(itinerary[column].rename("zone_type"))
    if not parts:
        return _uniform_distribution()
    endpoints = pd.concat(parts, ignore_index=True).to_frame()
    return _zone_distribution(endpoints, "zone_type")


def _complete_distribution(values: dict[str, float]) -> dict[str, float]:
    complete = {zone: max(float(values.get(zone, 0.0)), 0.0) for zone in ZONE_TYPES}
    total = sum(complete.values())
    if total <= 0:
        return _uniform_distribution()
    return {zone: value / total for zone, value in complete.items()}


def _uniform_distribution() -> dict[str, float]:
    return {zone: 1.0 / len(ZONE_TYPES) for zone in ZONE_TYPES}


def _distribution_l1(actual: dict[str, float], expected: dict[str, float]) -> float:
    actual = _complete_distribution(actual)
    expected = _complete_distribution(expected)
    return float(sum(abs(actual[zone] - expected[zone]) for zone in ZONE_TYPES))


def _edge_fsa_distribution(edge_flows: pd.DataFrame, start_hour: int, end_hour: int) -> dict[str, float]:
    if edge_flows.empty or not {"fsa", "vehicle_count", "day", "hour"}.issubset(edge_flows.columns):
        return {}
    window = edge_flows[
        (edge_flows["day"] < 5)
        & (edge_flows["hour"].between(start_hour, end_hour))
    ]
    if window.empty:
        return {}
    weights = window.groupby("fsa")["vehicle_count"].sum().astype(float)
    total = float(weights.sum())
    if total <= 0:
        return {}
    return {str(fsa): float(value / total) for fsa, value in weights.items()}


def _fsa_distribution_l1(actual: dict[str, float], expected: dict[str, float]) -> float:
    if not expected:
        return np.nan
    fsas = sorted(expected)
    actual_total = sum(max(float(actual.get(fsa, 0.0)), 0.0) for fsa in fsas)
    expected_total = sum(max(float(expected.get(fsa, 0.0)), 0.0) for fsa in fsas)
    if actual_total <= 0 or expected_total <= 0:
        return np.nan
    return float(sum(abs(
        max(float(actual.get(fsa, 0.0)), 0.0) / actual_total
        - max(float(expected.get(fsa, 0.0)), 0.0) / expected_total
    ) for fsa in fsas))


def _hourly_profile_corr(hourly: pd.DataFrame, expected: dict[int, float]) -> float:
    if hourly.empty or "hour" not in hourly or "energy_kwh" not in hourly:
        return np.nan
    actual = hourly.groupby("hour")["energy_kwh"].sum().reindex(range(24), fill_value=0.0).astype(float)
    if actual.sum() <= 0:
        return np.nan
    actual = actual / actual.sum()
    expected_series = pd.Series(expected).reindex(range(24), fill_value=0.0).astype(float)
    if expected_series.sum() <= 0 or actual.std() == 0 or expected_series.std() == 0:
        return np.nan
    return float(actual.corr(expected_series))
