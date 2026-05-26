import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def test_offline_road_graph_maps_every_hackathon_fsa():
    from road_network import RoadNetwork
    from spatial_assembler import load_enriched_geodataframe

    gdf = load_enriched_geodataframe()
    network = RoadNetwork(gdf, source="fsa_adjacency")
    summary = network.summary()

    assert summary.node_count == len(gdf)
    assert summary.edge_count >= len(gdf)
    assert summary.unreachable_od_pairs == 0
    assert np.isfinite(network.distance_km).all()
    assert (network.distance_km > 0).all()


def test_charger_catalog_maps_public_chargers_to_fsas():
    from charger_catalog import ChargerCatalog
    from road_network import RoadNetwork
    from spatial_assembler import load_enriched_geodataframe

    gdf = load_enriched_geodataframe()
    catalog = ChargerCatalog(gdf, source="zone_proxy")
    catalog.snap_to_road_network(RoadNetwork(gdf, source="fsa_adjacency"))

    assert not catalog.chargers.empty
    assert catalog.chargers["fsa"].isin(set(gdf["fsa"])).all()
    assert catalog.chargers["fsa_idx"].between(0, len(gdf) - 1).all()
    assert catalog.chargers["lat"].between(40, 46).all()
    assert catalog.chargers["lon"].between(-82, -76).all()
    assert catalog.chargers["road_node_id"].notna().all()
    assert catalog.chargers["road_snap_distance_m"].notna().all()


def test_route_patch_charger_selection_prefers_nearby_fast_charger():
    import pandas as pd

    from charger_catalog import CHARGER_COLUMNS, ChargerCatalog
    from spatial_assembler import load_enriched_geodataframe

    gdf = load_enriched_geodataframe().head(1).copy()
    catalog = ChargerCatalog(gdf, source="zone_proxy")
    lat = float(gdf.iloc[0]["centroid_lat"])
    lon = float(gdf.iloc[0]["centroid_lon"])
    catalog.public = pd.DataFrame([
        ["slow", gdf.iloc[0]["fsa"], 0, gdf.iloc[0]["zone_type"], lat, lon, 7.0, "afdc"],
        ["fast", gdf.iloc[0]["fsa"], 0, gdf.iloc[0]["zone_type"], lat + 0.01, lon, 150.0, "afdc"],
    ], columns=CHARGER_COLUMNS)

    choice = catalog.nearest_public_to_route((0,), 0, 0)
    assert choice.charger_id == "fast"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml")),
    reason="real OSM graph cache missing; run data_preparation/fetch_real_world_grid.py",
)
def test_cached_real_osm_road_graph_routes_all_fsa_centroids():
    from road_network import RoadNetwork
    from spatial_assembler import load_enriched_geodataframe

    gdf = load_enriched_geodataframe()
    network = RoadNetwork(gdf, source="osm")
    summary = network.summary()

    assert summary.source == "osm"
    assert summary.node_count > len(gdf)
    assert summary.edge_count > summary.node_count
    assert summary.unreachable_od_pairs == 0
    assert summary.p95_snap_distance_m < 5_000


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml")),
    reason="real OSM graph cache missing; run data_preparation/fetch_real_world_grid.py",
)
def test_real_osm_nodes_map_to_containing_hackathon_fsa():
    import geopandas as gpd

    from road_network import RoadNetwork
    from spatial_assembler import load_enriched_geodataframe

    gdf = load_enriched_geodataframe()
    network = RoadNetwork(gdf, source="osm")
    sampled = list(network.graph.nodes(data=True))[::50]
    node_gdf = gpd.GeoDataFrame(
        {"node_id": [int(node_id) for node_id, _ in sampled]},
        geometry=gpd.points_from_xy(
            [float(data["x"]) for _, data in sampled],
            [float(data["y"]) for _, data in sampled],
        ),
        crs="EPSG:4326",
    )
    fsa_polygons = gdf[["geometry"]].reset_index(names="expected_idx")
    joined = gpd.sjoin(node_gdf, fsa_polygons, predicate="within", how="inner").drop_duplicates("node_id")

    assert len(joined) > 500
    actual = joined["node_id"].head(500).map(network._osm_node_to_fsa).astype(int)
    expected = joined["expected_idx"].head(500).astype(int)
    assert np.array_equal(actual.to_numpy(), expected.to_numpy())


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml")),
    reason="real OSM graph cache missing; run data_preparation/fetch_real_world_grid.py",
)
def test_real_osm_edge_flows_use_osm_node_ids():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(ev_probability=0.30, road_graph_source="osm", charger_source="zone_proxy"))
    _, itinerary = engine.generate_weekly_itinerary(num_people=120, seed=109)
    flows = engine.aggregate_edge_flows(itinerary)

    assert not flows.empty
    graph_nodes = set(engine.road_network.graph.nodes)
    sampled_nodes = set(flows["edge_u"].head(200).astype(int)) | set(flows["edge_v"].head(200).astype(int))
    assert sampled_nodes.issubset(graph_nodes)


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml")),
    reason="real OSM graph cache missing; run data_preparation/fetch_real_world_grid.py",
)
def test_real_public_chargers_snap_to_osm_road_nodes():
    from mobility_simulator import MobilityConfig, MobilitySimulationEngine

    engine = MobilitySimulationEngine(MobilityConfig(road_graph_source="osm", charger_source="afdc"))
    public = engine.charger_catalog.public

    assert len(public) >= 1_000
    assert public["road_node_id"].notna().all()
    assert public["road_snap_distance_m"].quantile(0.95) < 2_000
    assert set(public["road_node_id"].astype(int)).issubset(set(engine.road_network.graph.nodes))


@pytest.mark.skipif(
    not (
        os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "gta_drive.graphml"))
        and os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "data", "cache", "afdc_on_ev_chargers.csv"))
    ),
    reason="real OSM graph or AFDC charger cache missing; run data_preparation/fetch_real_world_grid.py",
)
def test_real_validation_uses_real_public_charger_sources_and_osm_edges():
    from mobility_simulator import MobilityConfig
    from simulation_validation import ValidationOptions, validate_weekly_simulation

    cfg = MobilityConfig(
        ev_probability=1.0,
        home_charger_probability=0.0,
        work_charger_probability=0.0,
        home_public_charger_access=1.0,
        work_public_charger_access=1.0,
        retail_public_charger_access=1.0,
        road_graph_source="osm",
        charger_source="afdc",
    )
    report, artifacts = validate_weekly_simulation(
        num_people=120,
        seed=313,
        config=cfg,
        options=ValidationOptions(require_real_grid=True, require_real_chargers=True),
    )
    status_by_metric = report.set_index("metric")["status"]
    public = artifacts["charges"][artifacts["charges"]["charger_source"].isin(["afdc", "osm", "zone_proxy"])]

    assert status_by_metric["real_charger_catalog_only"] == "PASS"
    assert status_by_metric["public_charge_sources_real"] == "PASS"
    assert status_by_metric["route_path_edges_exist"] == "PASS"
    assert not public.empty
    assert set(public["charger_source"]).issubset({"afdc", "osm"})
