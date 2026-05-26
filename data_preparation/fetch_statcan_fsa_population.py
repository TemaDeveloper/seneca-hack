"""
Fetch Statistics Canada 2021 FSA population/dwelling counts.

Creates:
    backend/data/fsa_population_scaling.csv
"""

from __future__ import annotations

from pathlib import Path
import zipfile

import pandas as pd
import requests


DATA_DIR = Path(__file__).resolve().parents[1] / "backend" / "data"
OUTPUT_CSV = DATA_DIR / "fsa_population_scaling.csv"
STATCAN_FSA_POPULATION_ZIP_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/98100019-eng.zip"


def fetch_statcan_fsa_population(*, force: bool = False) -> pd.DataFrame:
    if OUTPUT_CSV.exists() and not force:
        return pd.read_csv(OUTPUT_CSV)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_zip = DATA_DIR / "_statcan_fsa_population.zip"
    response = requests.get(STATCAN_FSA_POPULATION_ZIP_URL, timeout=60)
    response.raise_for_status()
    tmp_zip.write_bytes(response.content)

    with zipfile.ZipFile(tmp_zip) as zf:
        with zf.open("98100019.csv") as fh:
            raw = pd.read_csv(fh)
    tmp_zip.unlink(missing_ok=True)

    pop_col = "Population and dwelling counts (3):Population, 2021[1]"
    total_dwelling_col = "Population and dwelling counts (3):Total private dwellings, 2021[2]"
    occupied_dwelling_col = "Population and dwelling counts (3):Private dwellings occupied by usual residents, 2021[3]"
    required = {"GEO", pop_col, total_dwelling_col, occupied_dwelling_col}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing expected StatCan columns: {sorted(missing)}")

    df = raw[["GEO", pop_col, total_dwelling_col, occupied_dwelling_col]].copy()
    df = df[df["GEO"].astype(str).str.len() == 3].copy()
    df = df.rename(columns={
        "GEO": "fsa",
        pop_col: "population_2021",
        total_dwelling_col: "total_private_dwellings_2021",
        occupied_dwelling_col: "occupied_private_dwellings_2021",
    })
    for col in ["population_2021", "total_private_dwellings_2021", "occupied_private_dwellings_2021"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["source"] = STATCAN_FSA_POPULATION_ZIP_URL
    df = df.sort_values("fsa").reset_index(drop=True)
    df.to_csv(OUTPUT_CSV, index=False)
    return df


if __name__ == "__main__":
    data = fetch_statcan_fsa_population(force=False)
    print(f"Saved {len(data):,} FSA population rows to {OUTPUT_CSV}")
    print(data.head().to_string(index=False))
