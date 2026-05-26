"""
Fetch/cache real-world OSM road graph and public EV chargers for the GTA FSA set.

Run:
    PYTHONPATH=backend uv run python data_preparation/fetch_real_world_grid.py

The simulator will use these cached files automatically with
`MobilityConfig(road_graph_source="auto", charger_source="auto")`.
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from charger_catalog import AFDC_CHARGERS_CSV, OSM_CHARGERS_CSV, load_or_fetch_afdc_chargers, load_or_fetch_osm_chargers
from road_network import OSM_GRAPHML, RoadNetwork, load_or_fetch_osm_drive_graph
from spatial_assembler import load_enriched_geodataframe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-download road graph and charger catalogs even if cache exists.")
    parser.add_argument("--include-osm-chargers", action="store_true", help="Also try Overpass OSM charger points; AFDC is the default real charger source.")
    args = parser.parse_args()

    gdf = load_enriched_geodataframe()
    print(f"FSAs: {len(gdf)}")

    graph = load_or_fetch_osm_drive_graph(gdf, force_download=args.force)
    print(f"OSM road graph cached: {OSM_GRAPHML}")
    print(f"OSM graph nodes={graph.number_of_nodes():,} edges={graph.number_of_edges():,}")

    chargers = load_or_fetch_afdc_chargers(gdf, force_download=args.force)
    print(f"AFDC chargers cached: {AFDC_CHARGERS_CSV}")
    print(f"AFDC public chargers mapped to FSAs={len(chargers):,}")

    if args.include_osm_chargers:
        try:
            osm_chargers = load_or_fetch_osm_chargers(gdf, force_download=args.force)
            print(f"OSM chargers cached: {OSM_CHARGERS_CSV}")
            print(f"OSM chargers mapped to FSAs={len(osm_chargers):,}")
        except Exception as exc:
            print(f"OSM charger fetch skipped/failed: {exc}")

    network = RoadNetwork(gdf, source="osm", force_osm_download=False)
    summary = network.summary()
    print(summary)


if __name__ == "__main__":
    main()
