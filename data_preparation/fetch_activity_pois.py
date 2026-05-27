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
    activity_poi_cache_status,
    describe_required_external_data,
    load_or_fetch_activity_pois,
    read_activity_poi_cache_metadata,
    rebuild_activity_poi_cache_from_chunks,
)
from road_network import RoadNetwork  # noqa: E402
from spatial_assembler import load_enriched_geodataframe  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-download OSM POIs even if cache exists.")
    parser.add_argument("--source", choices=["osm", "auto", "none", "cache"], default="osm")
    parser.add_argument("--road-graph-source", choices=["auto", "osm", "fsa_adjacency"], default="auto")
    parser.add_argument("--chunk-size", type=int, default=10, help="Number of FSAs per resumable OSM fetch chunk.")
    parser.add_argument("--request-timeout", type=int, default=120, help="OSMnx/Overpass request timeout per chunk in seconds.")
    parser.add_argument("--limit-fsas", type=int, default=None, help="Fetch only the first N FSAs for smoke tests.")
    parser.add_argument("--start-fsa", type=int, default=None, help="Zero-based first FSA index to fetch.")
    parser.add_argument("--stop-fsa", type=int, default=None, help="Zero-based exclusive FSA index to fetch.")
    parser.add_argument("--max-fsas", type=int, default=None, help="When fetching OSM without explicit start/stop, fetch at most this many currently missing FSAs.")
    parser.add_argument("--aggregate-only", action="store_true", help="Rebuild final CSV caches from compatible chunk files without hitting Overpass.")
    parser.add_argument("--status", action="store_true", help="Print compatible chunk coverage for the selected road graph.")
    parser.add_argument("--status-only", action="store_true", help="Print chunk/cache status and exit without fetching or rebuilding.")
    parser.add_argument("--list-required-data", action="store_true", help="Print non-OSM datasets that remain assumptions/gaps.")
    args = parser.parse_args()

    if args.list_required_data:
        print(describe_required_external_data().to_string(index=False))

    gdf = load_enriched_geodataframe()
    network = RoadNetwork(gdf, source=args.road_graph_source)
    if args.status or args.status_only:
        print("\nChunk coverage before:")
        _print_status(activity_poi_cache_status(gdf, network))
    if args.status_only:
        return

    start_fsa = args.start_fsa
    stop_fsa = args.stop_fsa
    if args.source == "osm" and args.max_fsas is not None and start_fsa is None and stop_fsa is None:
        status = activity_poi_cache_status(gdf, network)
        missing = status["missing_fsa_ranges"]
        if not missing:
            print("No missing compatible FSA chunks for this road graph.", flush=True)
            return
        first_missing = missing[0]
        start_fsa = int(first_missing[0])
        stop_fsa = min(int(first_missing[1]), start_fsa + max(int(args.max_fsas), 0))
        print(f"Selected missing FSA range {start_fsa}:{stop_fsa}", flush=True)

    if args.aggregate_only:
        catalog = rebuild_activity_poi_cache_from_chunks(
            gdf,
            road_network=network,
            source="osm",
            chunk_size=args.chunk_size,
            limit_fsas=args.limit_fsas,
            start_fsa=start_fsa,
            stop_fsa=stop_fsa,
        )
    else:
        catalog = load_or_fetch_activity_pois(
            gdf,
            road_network=network,
            source=args.source,
            force_download=args.force,
            chunk_size=args.chunk_size,
            limit_fsas=args.limit_fsas,
            start_fsa=start_fsa,
            stop_fsa=stop_fsa,
            request_timeout=args.request_timeout,
            progress=_progress if args.source == "osm" else None,
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
    if args.status:
        print("\nChunk coverage after:")
        _print_status(activity_poi_cache_status(gdf, network))


def _print_status(status: dict[str, object]) -> None:
    summary = {
        "fsa_count": status["fsa_count"],
        "covered_fsa_count": status["covered_fsa_count"],
        "missing_fsa_count": status["missing_fsa_count"],
        "complete": status["complete"],
        "compatible_chunk_count": status["compatible_chunk_count"],
        "chunk_count": status["chunk_count"],
        "missing_fsa_ranges_sample": status["missing_fsa_ranges"][:10],
    }
    print(summary)


def _progress(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    main()
