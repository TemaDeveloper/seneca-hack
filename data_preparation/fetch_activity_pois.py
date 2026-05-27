"""
Fetch/cache activity POIs used by the intraday route planner.

This script explicitly hits OSM/Overpass. Normal simulator runs use the cache
when present and otherwise fall back to deterministic FSA-level proxy weights.

Run:
    PYTHONPATH=backend uv run python data_preparation/fetch_activity_pois.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from activity_poi_catalog import (  # noqa: E402
    ACTIVITY_FSA_ATTRACTIONS_CSV,
    ACTIVITY_NODE_ATTRACTIONS_CSV,
    ACTIVITY_POI_METADATA_JSON,
    ACTIVITY_POIS_CSV,
    describe_required_external_data,
    load_or_fetch_activity_pois,
    read_activity_poi_cache_metadata,
)
from road_network import RoadNetwork  # noqa: E402
from spatial_assembler import load_enriched_geodataframe  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-download OSM POIs even if cache exists.")
    parser.add_argument("--source", choices=["osm", "auto", "none", "cache"], default="osm")
    parser.add_argument("--road-graph-source", choices=["auto", "osm", "fsa_adjacency"], default="auto")
    parser.add_argument("--chunk-size", type=int, default=10, help="Number of FSAs per resumable OSM fetch chunk.")
    parser.add_argument("--limit-fsas", type=int, default=None, help="Fetch only the first N FSAs for smoke tests.")
    parser.add_argument("--list-required-data", action="store_true", help="Print non-OSM datasets that remain assumptions/gaps.")
    args = parser.parse_args()

    if args.list_required_data:
        print(describe_required_external_data().to_string(index=False))

    gdf = load_enriched_geodataframe()
    network = RoadNetwork(gdf, source=args.road_graph_source)
    catalog = load_or_fetch_activity_pois(
        gdf,
        road_network=network,
        source=args.source,
        force_download=args.force,
        chunk_size=args.chunk_size,
        limit_fsas=args.limit_fsas,
    )

    print(f"Activity POI source: {catalog.source}")
    print(f"POIs cached: {ACTIVITY_POIS_CSV}")
    print(f"FSA attractions cached: {ACTIVITY_FSA_ATTRACTIONS_CSV}")
    print(f"Node attractions cached: {ACTIVITY_NODE_ATTRACTIONS_CSV}")
    print(f"Activity POI metadata: {ACTIVITY_POI_METADATA_JSON}")
    print(catalog.summary())
    metadata = read_activity_poi_cache_metadata()
    if metadata:
        print("\nCache metadata:")
        print(metadata)
    if not catalog.fsa_attractions.empty:
        print("\nAttraction rows by activity:")
        print(catalog.fsa_attractions.groupby("activity_type")["attraction_weight"].agg(["count", "sum"]).to_string())


if __name__ == "__main__":
    main()
