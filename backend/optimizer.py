"""
Phase 3: Prescriptive Optimization Solver

Uses PuLP to solve a Facility Location Problem (FLP) — given overloaded
grid zones from Phase 2, selects optimal locations for new EV charging
infrastructure within a station budget.

Public API:
    optimize_placement(grid_df, max_stations) -> pd.DataFrame
"""

import math
import pandas as pd
import pulp


# ---------------------------------------------------------------------------
# Asset Prescription Rules
# ---------------------------------------------------------------------------
CHARGER_TYPES = {
    "residential": {"type": "Level 2 Smart-Charging Hub", "kw_per_unit": 7},
    "leisure":     {"type": "Level 2 Smart-Charging Hub", "kw_per_unit": 7},
    "office_park": {"type": "DC Fast Charging Array",     "kw_per_unit": 50},
    "retail_hub":  {"type": "DC Fast Charging Array",     "kw_per_unit": 50},
    "transit_hub": {"type": "Ultra-Fast Charging Array",  "kw_per_unit": 150},
}

BESS_HOURS = 2  # Battery buffer sized for 2 hours of peak deficit


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute haversine distance in km between two GPS coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def optimize_placement(grid_df: pd.DataFrame, max_stations: int = 10) -> pd.DataFrame:
    """
    Solve the Facility Location Problem to find optimal charging station sites.

    Args:
        grid_df: Output from SimulationEngine.aggregate_grid_load() — must contain
                 columns: fsa, zone_type, deficit_kw, overloaded, centroid_lat, centroid_lon
        max_stations: Maximum number of charging stations to deploy (budget constraint)

    Returns:
        DataFrame with columns: fsa, zone_type, deficit_kw, centroid_lat, centroid_lon,
        charger_type, charger_units, charger_kw_per_unit, bess_kwh
        Sorted by deficit_kw descending. Empty DataFrame if no overloaded zones.
    """
    # 1. Filter to overloaded zones only
    candidates = grid_df[grid_df["overloaded"] == True].copy().reset_index(drop=True)

    if candidates.empty:
        return pd.DataFrame(columns=[
            "fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon",
            "charger_type", "charger_units", "charger_kw_per_unit",
            "total_charger_kw", "bess_kwh"
        ])

    # 2. Infeasibility shield — cap budget at candidate count
    effective_budget = min(max_stations, len(candidates))

    n = len(candidates)
    demand = candidates["deficit_kw"].values
    lats = candidates["centroid_lat"].values
    lons = candidates["centroid_lon"].values

    # 3. Build coverage weight matrix (inverse distance)
    # w[i][j] = how well station at j covers demand at i
    coverage = []
    for i in range(n):
        row = []
        for j in range(n):
            if i == j:
                row.append(1.0)  # Perfect self-coverage
            else:
                dist = _haversine_km(lats[i], lons[i], lats[j], lons[j])
                row.append(1.0 / (1.0 + dist))  # Decay with distance
        coverage.append(row)

    # 4. Define PuLP problem
    prob = pulp.LpProblem("EV_Charger_Placement", pulp.LpMaximize)

    # Decision variables
    y = [pulp.LpVariable(f"y_{j}", cat="Binary") for j in range(n)]  # Open station at j?
    x = [[pulp.LpVariable(f"x_{i}_{j}", lowBound=0, upBound=1) for j in range(n)] for i in range(n)]

    # Objective: maximize deficit-weighted coverage
    prob += pulp.lpSum(
        demand[i] * coverage[i][j] * x[i][j]
        for i in range(n) for j in range(n)
    )

    # Constraint 1: Budget
    prob += pulp.lpSum(y[j] for j in range(n)) <= effective_budget

    # Constraint 2: Linking — can only serve from open stations
    for i in range(n):
        for j in range(n):
            prob += x[i][j] <= y[j]

    # Constraint 3: Demand cap — each zone's demand counted at most once
    for i in range(n):
        prob += pulp.lpSum(x[i][j] for j in range(n)) <= 1

    # 5. Solve. Some local Apple Silicon environments install an x86 CBC
    # binary with PuLP; fall back to deterministic greedy selection if CBC
    # cannot execute.
    try:
        prob.solve(pulp.PULP_CBC_CMD(msg=0))
        solved = prob.status == pulp.constants.LpStatusOptimal
    except OSError:
        solved = False

    if not solved:
        # Fallback: greedy — pick top N by deficit
        selected_indices = candidates.nlargest(effective_budget, "deficit_kw").index.tolist()
    else:
        selected_indices = [j for j in range(n) if y[j].varValue and y[j].varValue > 0.5]

    # 6. Build result with deficit-scaled prescriptions
    selected = candidates.iloc[selected_indices].copy()

    charger_info = selected["zone_type"].map(CHARGER_TYPES)
    selected["charger_type"] = charger_info.apply(lambda c: c["type"])
    selected["charger_kw_per_unit"] = charger_info.apply(lambda c: c["kw_per_unit"])
    # Scale units to cover the deficit: ceil(deficit / kw_per_unit)
    selected["charger_units"] = (
        (selected["deficit_kw"] / selected["charger_kw_per_unit"]).apply(math.ceil).clip(lower=1).astype(int)
    )
    selected["total_charger_kw"] = selected["charger_units"] * selected["charger_kw_per_unit"]
    selected["bess_kwh"] = (selected["deficit_kw"] * BESS_HOURS).astype(int)

    result = selected[["fsa", "zone_type", "deficit_kw", "centroid_lat", "centroid_lon",
                        "charger_type", "charger_units", "charger_kw_per_unit",
                        "total_charger_kw", "bess_kwh"]]

    return result.sort_values("deficit_kw", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    from monte_carlo import SimulationEngine

    engine = SimulationEngine()
    ev_df = engine.run_simulation(num_evs=15000, time_of_day="Full Day", temperature_celsius=-15.0)
    grid_df = engine.aggregate_grid_load(ev_df)

    print(f"\nOverloaded FSAs: {grid_df['overloaded'].sum()}/{len(grid_df)}")
    print(f"Running optimization for 10 stations...\n")

    result = optimize_placement(grid_df, max_stations=10)

    print("=" * 80)
    print("OPTIMAL CHARGING STATION PLACEMENTS")
    print("=" * 80)
    for _, site in result.iterrows():
        print(f"\n  Optimal Site: FSA {site['fsa']} ({site['zone_type']})")
        print(f"  Coordinates: ({site['centroid_lat']:.4f}, {site['centroid_lon']:.4f})")
        print(f"  Peak Deficit: +{site['deficit_kw']:.0f} kW")
        print(f"  Prescribed: {site['charger_units']}x {site['charger_type']} ({site['total_charger_kw']} kW total)")
        print(f"  BESS Buffer: {site['bess_kwh']} kWh")
    print("\n" + "=" * 80)
