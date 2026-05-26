# Mobility/EV Model Assumptions

This file records constants that are not directly measured in the current repo. Each value should be treated as a calibrated default, not a truth claim. If a better local dataset is added, the constant should move closer to data-derived estimation.

## Public Data Inventory

### Road graph

**Default source:** OpenStreetMap via OSMnx.

Argument: OSMnx directly downloads and constructs routable `NetworkX` graphs from OpenStreetMap road data, which is the shortest path from open GIS to agent routing. For reproducible offline builds, use Geofabrik OSM extracts rather than live Overpass calls.

Implementation:
- `backend/road_network.py` maps every hackathon FSA polygon/zone/capacity record onto a road graph.
- `road_graph_source="osm"` loads cached OSMnx drive graph `backend/data/cache/gta_drive.graphml`.
- `road_graph_source="auto"` uses cached OSM when present and otherwise uses an offline FSA-adjacency graph built from the supplied hackathon FSA polygons.
- The current real-grid cache covers 260 FSAs, 145,540 OSM road nodes, 383,093 road edges, 1 connected component, and 0 unreachable FSA OD pairs after directed routing with undirected fallback for centroid one-way/dead-end snaps.
- FSA centroid snap distance on the cached OSM graph: median 90.8 m, p95 892.8 m.
- Network circuity on the cached OSM graph: median 1.317, p90 1.652 after measuring distance on the actual time-minimizing route path.
- OSM road nodes are mapped to hackathon FSAs by polygon containment first; nearest-FSA fallback is used only for road nodes outside all FSA polygons. This mapping drives route-edge FSA exposure and near-route public charger lookup.
- Edge-flow aggregation now walks each route path over travel time and assigns edge traversals to the hour when the vehicle reaches that segment, rather than placing every edge into the departure hour.
- Real-grid validation checks every unique generated route path against the OSM graph nodes and edges, not just a prefix sample.

Sources:
- OSMnx documentation: https://osmnx.readthedocs.io/en/stable/
- Geofabrik OSM shapefiles: https://www.geofabrik.de/en/data/shapefiles.html

### Official road/intersection geometry

**Toronto-only source:** Toronto Centreline and Centreline Intersection.

Argument: these are official municipal GIS layers and are useful for snapping Toronto traffic counts to road segments/intersections. They are not enough alone for GTA-wide routing, so they should augment OSM rather than replace it.

Sources:
- Toronto Centreline metadata: https://open.toronto.ca/dataset/toronto-centreline-tcl/
- Toronto Intersection File metadata: https://open.toronto.ca/dataset/intersection-file-city-of-toronto/

### Traffic over time

**Toronto source:** Traffic Volumes at Intersections for All Modes.

Argument: this is the best public traffic dataset currently wired into the repo for time-conditioned road-grid intensity. It exposes intersection coordinates, AM/PM vehicle counts, and approach volumes. Limitation: it is ad-hoc and mostly Toronto/intersection coverage, not a complete GTA OD truth set.

Implementation:
- `data_preparation/fetch_toronto_traffic.py` writes `backend/data/zone_weights.json` and `backend/data/toronto_traffic_fsa_counts.csv`.
- `backend/observed_targets.py` compares simulated weekday AM/PM route-edge exposure by FSA against observed AM/PM traffic-count FSA weights.
- The current traffic-count artifact covers 107 FSA rows.

Secondary source: Midblock speed/volume/classification counts, useful for segment speed and volume calibration.

Sources:
- Turning movement counts: https://open.toronto.ca/dataset/traffic-volumes-at-intersections-for-all-modes/
- Midblock speed/volume/classification counts: https://open.toronto.ca/dataset/traffic-volumes-midblock-vehicle-speed-volume-and-classification-counts/
- FHWA Travel Time Index definition and peak/free-flow ratio framing: https://www.fhwa.dot.gov/publications/research/operations/15071/002.cfm
- TomTom Toronto Traffic Index for current Toronto congestion context: https://www.tomtom.com/traffic-index/toronto-traffic/

### Static traffic multiplier

**Default implementation:** `RoadNetwork.time_multiplier(hour_abs)`.

Defaults:
- weekday AM peak, 07:00-09:30: `1.32`
- weekday PM peak, 15:30-18:30: `1.38`
- weekday midday, 11:30-13:30: `1.10`
- weekend midday/afternoon, 11:00-18:00: `1.14`
- overnight, 22:00-05:00: `0.92`
- other hours: `1.00`

Argument: this is a static travel-time-index approximation, not a calibrated dynamic traffic model. FHWA defines travel time index as peak-period travel time relative to light/free-flow travel time, so multiplying OSM free-flow route time by hour/day factors is the correct simple model shape. Toronto-specific public live speed profiles have not been fitted yet; the values above encode known AM/PM weekday peak structure and should be replaced by snapped Toronto midblock traffic counts or another observed speed profile when time permits.

### Charging stations

**Primary source:** NRCan / NREL alternative fueling station API.

Argument: it covers Canada, supports electric fuel filtering, and exposes station location/status/access fields. City of Toronto also has a city-operated charger layer, but that only covers municipal assets.

Implementation:
- `backend/charger_catalog.py` maps public EV charger points into the hackathon FSA polygons.
- Primary real charger cache is AFDC/NREL Ontario public EV stations at `backend/data/cache/afdc_on_ev_chargers.csv`.
- The current real charger cache maps 2,889 public AFDC/NREL charging stations into the FSA boundary set.
- At runtime, public charger points and private home/work/school charger proxies are snapped to the active road graph. Current AFDC-to-OSM charger snap p95 is `261.13 m`.
- OSM `amenity=charging_station` remains an optional enrichment source because Overpass availability is less reliable for large GTA-wide charger queries.
- Private home/work/school charging is modeled separately as agent access, not as public station points.

Fallback/enrichment sources: OpenChargeMap and OpenStreetMap `amenity=charging_station`.

Sources:
- NRCan station locator: https://natural-resources.canada.ca/energy-efficiency/transportation-alternative-fuels/electric-charging-alternative-fuelling-stationslocator-map/20487
- AFDC station locator and charging terminology: https://afdc.energy.gov/fuels/electricity-stations
- NREL stations API: https://developer.nrel.gov/docs/transportation/alt-fuel-stations-v1/all/
- Toronto city-operated chargers: https://open.toronto.ca/dataset/city-operated-electric-vehicle-charging-station-map/
- OpenChargeMap API: https://www.openchargemap.org/develop/api
- Overpass API: https://wiki.openstreetmap.org/wiki/Overpass_API

### Destination/activity data

**Best OD source:** Transportation Tomorrow Survey (TTS), via the University of Toronto Data Management Group.

Argument: TTS is the closest public/regional source for trip purpose, OD structure, and time-of-day travel behavior. Some open OD matrix products exist, but exact trip-level access may require using DMG tools or data access paths.

**Concrete attraction fields:** Toronto POIs, parks/recreation facilities, schools, parking, zoning, employment summaries, and OSM POIs.

Argument: for v1, use these layers to construct destination candidates by type rather than assigning agents to exact workplaces.

Sources:
- DMG/TTS overview: https://dmg.utoronto.ca/
- DMG open data: https://dmg.utoronto.ca/open-data/
- TTS OD matrices: https://dmg.utoronto.ca/transportation-tomorrow-survey/origin-destination-matrices-2/
- Toronto Places of Interest and Attractions: https://open.toronto.ca/dataset/places-of-interest-and-toronto-attractions/
- Toronto Parks and Recreation Facilities: https://open.toronto.ca/dataset/parks-and-recreation-facilities/
- Toronto School Locations: https://open.toronto.ca/dataset/school-locations-all-types/
- Toronto Zoning By-law: https://open.toronto.ca/dataset/zoning-by-law/
- Toronto Parking Occupancy: https://open.toronto.ca/dataset/parking-occupancy/

### FSA population scaling

**Source:** Statistics Canada table `98-10-0019-01`, population and dwelling counts for Canada and forward sortation areas.

Implementation:
- `data_preparation/fetch_statcan_fsa_population.py` downloads the StatCan bulk CSV and writes `backend/data/fsa_population_scaling.csv`.
- `backend/spatial_assembler.py` joins `population_2021`, `total_private_dwellings_2021`, and `occupied_private_dwellings_2021` onto the hackathon FSA table.
- `MobilitySimulationEngine` uses observed `population_2021` as the home-origin sampling weight when the file is present; otherwise it falls back to the older area/zone proxy.
- The current M/L-prefix FSA boundary set sums to `8,393,632` 2021 residents. This is the model boundary population, not a claim about the narrower municipal Toronto population.
- `vehicle_population_share=0.46` is the default population-to-road-user expansion proxy. It is derived as `0.62` calibrated worker share times the Statistics Canada-reported `73.8%` automobile share for Toronto CMA internal commuters, rounded from `0.4576`.
- `--population-scale-grid` now defaults to this `0.46` road-user share. `--population-share` remains an explicit scenario override.

Sources:
- StatCan population and dwelling counts by FSA: https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=9810001901
- Statistics Canada GTA commuting automobile share: https://www.statcan.gc.ca/o1/en/plus/2697-gta-getting-there-automobile

## EV Penetration

### `p_ev`

**Default:** `0.03`

Argument: for sampling vehicles already on the road, stock share is the correct quantity, not new-sales share. Statistics Canada reports 24.6M Canadian light-duty vehicles in 2024 and 487,618 BEVs plus 197,581 PHEVs, so plug-in stock is about 2.8% nationally. Ontario-specific public stock is less immediately available; Ontario ZEV new registrations were 8.1% of new registrations in 2024, so `0.03` is a conservative current-fleet default and higher values should be explicit adoption scenarios.

Use:
- baseline road-fleet sampling: `is_ev ~ Bernoulli(0.03)`
- scenario slider: `p_ev in [0.03, 0.50]`

Sources:
- StatCan vehicle registrations 2024: https://www150.statcan.gc.ca/n1/daily-quotidien/251017/dq251017c-eng.htm
- StatCan new ZEV registrations Q4/2024: https://www150.statcan.gc.ca/n1/daily-quotidien/250313/dq250313c-eng.htm

## Initial State of Charge

### `soc_initial_distribution`

**Default for start-of-day / population initialization:** `Beta(alpha=6, beta=2)`

Argument: the user-level simulation begins with a population already distributed across charging histories. A right-skewed Beta is preferable to uniform because most EVs should not start a day near empty, especially with home charging. `Beta(6,2)` has mean `0.75`, keeping low SoC possible but uncommon. It replaced `Beta(5,2)` after candidate-grid fitting reduced validation loss while preserving all real-grid gates.

### `soc_at_charging_opportunity_distribution`

**Reference/calibration distribution:** `Beta(alpha=2.27, beta=2.18)`

Argument: this is not the start-of-day distribution. It is useful for validating simulated SoC at plug-in/charging decision events. Dixon et al. report that Beta fits charging-event SoC better than Gaussian because SoC is bounded on `[0, 1]`, and cite fitted parameters `alpha=2.27`, `beta=2.18`, mean SoC about 51%, from public/workplace charging events.

Source:
- Dixon et al., "Evaluating the likely temporal variation in electric vehicle charging demand": https://strathprints.strath.ac.uk/71417/1/Dixon_etal_ITS2020_Evaluating_the_likely_temporal_variation_in_electric_vehicle_charging_demand.pdf
- 2024 Canadian Electric Vehicle Owner Charging Experience Survey: https://www.pollutionprobe.org/wp-content/uploads/2025/03/2024_EV-report_E_25.pdf
- EV Database real-world consumption reference: https://ev-database.org/cheatsheet/energy-consumption-electric-car

## Charging Decision

### `P(charge)`

**Default model:**

```text
P_charge = sigmoid(
  -3.0
  + 3.0 * soc_need
  + 3.0 * range_anxiety
  + 1.2 * proximity
  + 1.0 * dwell
  - 0.6 * detour_km
) * availability
```

**Transforms:**

```text
soc_need = clamp((0.50 - soc) / 0.30, 0, 1)
projected_arrival_soc = soc - route_energy_kwh / battery_capacity_kwh
range_anxiety = clamp((0.25 - projected_arrival_soc) / 0.25, 0, 1)
proximity = exp(-walk_m / 500)
dwell = 1 - exp(-dwell_hours / 2)
availability = charger_availability_factor
```

Argument: agent-based EV models commonly make charge decisions after trips based on SoC and other behavioral/economic variables. Range anxiety is negatively related to remaining SoC, and it becomes important below a comfort threshold. Dwell time, proximity, detour, and waiting/availability are supported behavioral terms; the coefficients above are starting weights for sensitivity analysis, not estimated coefficients.

Sources:
- Probabilistic ABM with charge decisions based on SoC and other variables: https://www.mdpi.com/1996-1073/8/5/4160
- Range anxiety and remaining SoC relationship: https://www.mdpi.com/2071-1050/14/7/4213
- ABM charging behavior review/threshold examples: https://www.mdpi.com/2032-6653/12/1/18
- Distance and waiting-time anxiety: https://arxiv.org/abs/2306.05768

### `availability`

**Keep as a variable, but do not build full queues in v1.**

Defaults:
- home/work private charger: `0.95`
- public Level 2: `0.85`
- DCFC / corridor / peak-period: `0.75`

Sensitivity values: `0.65`, `0.85`, `0.95`

Argument: public charger utilization in Toronto appears low-to-moderate on average, so a queueing simulator is not justified for v1. But availability should not be dropped entirely because failures, blocked plugs, peak clustering, and downtown/corridor pressure affect perceived access and charging decisions.

Sources:
- Toronto EV charging plan background, utilization/queuing caveats: https://www.toronto.ca/legdocs/mmis/2026/ie/bgrd/backgroundfile-285816.pdf
- Toronto Parking Authority utilization report: https://www.toronto.ca/legdocs/mmis/2026/ie/bgrd/backgroundfile-285817.pdf
- CAA EV driver survey on public fast-charger availability/reliability: https://caaneo.ca/news/lack-fast-and-reliable-public-charging-tops-list-challenges-canadian-ev-owners-says-caa-survey/

## Destination Sampling

### `dest_type`

**Default structure:**

```text
dest_type ~ Categorical(P(type | hour, day_of_week, home_zone_class))
```

Candidate types:
- `work`
- `school`
- `retail`
- `leisure`
- `home`
- `transit_hub`
- `other`

Argument: destination purpose should be sampled before concrete destination. This avoids pretending we know a person's exact workplace while still preserving trip-purpose structure.

### `picked_destination`

**Default structure:**

```text
P(destination_node | origin_node, dest_type, hour, day_of_week)
  proportional_to attraction(destination_node, dest_type)
                * exp(-travel_time(origin_node, destination_node) / tau_dest_type)
                * local_traffic_intensity(destination_edge, hour, day_of_week)
```

Recommended v1 attraction fields:
- `work`: employment summaries, office zoning, business districts, daytime parking occupancy
- `school`: school locations
- `retail`: malls/retail zoning, Green P/parking, OSM retail POIs
- `leisure`: Toronto attractions, parks/recreation facilities
- `home`: residential zoning, FSA population proxy
- `transit_hub`: transit-oriented communities, airports/stations, OSM transit POIs

Unresolved calibration: public data can support destination candidates and rough day/hour weighting, but exact GTA-wide trip-purpose by day/hour is best calibrated from TTS if accessible. Without TTS extraction, use a hand-built weekly schedule matrix and mark it as an assumption.

### `outing_type_tail`

**Default weekly itinerary tail:**

```text
weekday non-work outing: retail 0.59, leisure 0.30, transit_hub 0.06, other 0.05
weekend first outing: retail 0.48, leisure 0.42, transit_hub 0.06, other 0.04
```

Argument: the weekly planner previously declared `transit_hub` and `other` as destination types but did not sample them in weekly plans. This made those purpose-zone gates sample-insufficient and hid a dead branch in the model. The small tail keeps retail/leisure dominant while generating enough transit/other evidence for strict validation. Transit-hub destinations use a stronger transit-hub zone attraction so that sampled trips actually map to airport/station-style FSAs instead of generic retail/residential zones. Validation bounds transit-hub destinations to `0.3-3.0%` and other destinations to `0.3-2.5%`; calibration targets `1.0%` and `0.8%`, respectively.

## Weekly Itinerary And Charging V1

### `weekly_cycle`

**Default:** one simulation cycle is exactly one week, Monday 00:00 through Sunday 24:00.

Argument: a weekly cycle captures commute repetition, weekend retail/leisure shifts, and SoC carryover. Most active person-days start and end at home. Low-travel days may contain no car legs.

Current implementation:
- sample each person once: home FSA, person type, EV flag, charger access, initial SoC
- generate all trip legs for the week before charging
- simulate SoC forward through the ordered weekly plan
- insert charge events without ever marking trips failed

### `person_type_distribution`

**Default:**

```text
worker = 0.62
student = 0.08
retired = 0.12
other = 0.18
```

Use: selects weekday/weekend template probabilities. These are rough behavioral segments, not demographic claims. They should be replaced with TTS/person survey calibration.

### `normal_charge_probability`

**Default structure:**

```text
P(charge at dwell) = sigmoid(
  base_by_location
  + 3.0 * normalized_soc_gap
  + 2.0 * low_soc_pressure
  + 1.0 * dwell_sufficiency
  + habit_bias
) * charger_access
```

Use: normal charging at home/work/retail/leisure/etc. Home/work have higher baseline probabilities, but they are not deterministic resets.

Implementation detail: charge events now carry charger identity/location/source, charger FSA/zone, origin activity/FSA/index, destination FSA/index when attached to a trip leg, and modeled detour distance. Public dwell charging uses the nearest catalog charger around the current FSA; private dwell charging uses the home/work/school FSA.

Defaults:

```text
base_by_location:
  home = -1.2
  work = -1.4
  school = -1.6
  retail = -1.8
  leisure = -2.0
  transit_hub = -1.8
  other = -1.8

home_charger_probability = 0.70
work_charger_probability = 0.35
home_public_charger_access = 0.30 when no private home charger is available
work_public_charger_access = 0.25 when no private work charger is available
retail_public_charger_access = 0.45
reserve_soc = 0.15
```

Argument: agents charge when the current SoC is low or insufficient for the future plan plus reserve. Home/work are strong opportunities because dwell is long and access is likelier, but high SoC plus a light future plan can still mean no charge.

Implementation invariant: a person without home/work charger access cannot use a private home/work placeholder charger. Those events are mapped to the nearest public catalog charger instead. Regression tests cover this because otherwise public charger geography can be falsely hidden inside private FSA-centroid charging.

### `patch_charge_probability`

**Default structure:** if a future leg would violate reserve, build feasible patch candidates and sample with softmax utility.

Candidate classes:
- `previous_home`
- `previous_work`
- `current_origin`
- `near_route_public`
- `forced_origin_public`

Utility:

```text
U = base
  + charger_power_score
  + dwell_fit_score
  - 0.25 * detour_km
  - 0.03 * wait_minutes
  - 0.05 * extra_minutes
  + log(access)

P(candidate) = softmax(U / 0.90)
```

Defaults:

```text
base:
  previous_home = 2.0
  previous_work = 0.8
  current_origin = 0.2
  near_route_public = 0.4
  forced_origin_public = -2.0
```

Argument: patching is a repair pass, not the normal charging model. Patch charging is inserted chronologically before the leg it enables; if it consumes more time than the available dwell, the trip is delayed. Destination charging is not allowed to make the current trip feasible because that would charge before arrival. The model never fails trips. If no comfortable patch is available, `forced_origin_public` guarantees feasibility and records inconvenience/detour stress.

Implementation detail: `near_route_public` patch charging uses the route FSA path when available and selects a catalog charger on or near that route. `forced_origin_public` uses the nearest public catalog charger to the current origin. Patch charging starts after any normal charging already booked in the same dwell, preventing charge-charge overlap.

Inconvenience definition: patch plug-in time that fits inside an already available dwell is not counted as user delay. Patch inconvenience is wait time, detour, plus charging time beyond the available dwell. This prevents home/work dwell patches from being incorrectly penalized as if the agent had to wait beside the charger.

## Parameter Backing Ledger

This ledger distinguishes real-data inputs from calibrated behavioral defaults. A calibrated value is acceptable for v1 only if it has a named validation target; it should be replaced by TTS, observed charger sessions, or traffic counts when those are ingested.

| Parameter area | Current values | Backing | Replacement path |
| --- | --- | --- | --- |
| Road graph | OSM graph; fallback FSA-adjacency graph; fallback `road_circuity=1.25` | Real OSM graph is cached and validated. Fallback `1.25` sits near the observed OSM median circuity of `1.317`; OSM p90 is `1.652`. | Keep OSM as default; use fallback only for unit tests/offline demos. |
| Charger catalog | AFDC public chargers, optional OSM enrichment, proxy fallback | Real AFDC cache maps `2,889` public chargers into hackathon FSAs. Proxy fallback is not used in real-grid validation. | Use AFDC plus OSM/OpenChargeMap dedupe when time permits. |
| EV share | `ev_probability=0.03`; validation stress scenario `0.20` | Stock share default comes from StatCan plug-in vehicle registrations. `0.20` is a stress scenario, not a present-day claim. | Replace with Ontario/GTA vehicle stock if available. |
| Battery/range | `battery_capacity_kwh=70`, `ev_efficiency_kwh_per_km=0.18` | Implied range is about `389 km`. This is consistent with Canadian EV owner survey evidence that most owners report at least `300 km` range and with EV Database real-world average consumption near `0.189 kWh/km`. | Replace scalar with make/model fleet distribution. |
| Charger power | private/home/school `7 kW`; public L2 `7 kW`; DCFC `50 kW`; hub fast `150 kW`; AFDC connector power when supplied | AFDC/NRCan charging definitions support Level 2 around single-digit kW and DC fast charging at tens to hundreds of kW. AFDC connector data overrides defaults when present. | Use per-port AFDC connector power and port counts everywhere. |
| Initial SoC | `Beta(6,2)` for population start; `Beta(2.27,2.18)` reference for charging events | Dixon et al. supports beta-distributed SoC at charging events. `Beta(6,2)` is calibrated as a start-of-week prior so the population is charged but not all full, and it beat `Beta(5,2)` in the real-grid candidate screen. Final-SoC drift is now a validation target, not an unchecked consequence of the prior. | Warm-up weeks, then carry final SoC into the measured week. |
| SoC policy | `reserve_soc=0.15`, normal `target_soc=0.82`, week-end prep target `0.75`, activity target beta draws shifted around the normal target | Backed by range-anxiety literature and validated by zero reserve/pre-departure violations, low near-reserve exposure, no excessive full-battery pile-up, and final mean SoC drift within `+/-8` percentage points. The normal target is a live weekly-model parameter, not only a legacy one-day parameter; `0.82` reduced real-grid fit loss while still passing strict sensitivity, while `0.85` improved loss further but failed stress validation and `0.90` broke full-battery sanity. The week-end target reduced repeated-week drift without deterministic full resets. | Estimate from charger-session arrival/departure SoC if available. |
| Home/work access | `home_charger_probability=0.70`, `work_charger_probability=0.35`; no-private public access home `0.30`, work `0.25`, retail `0.45` | Canadian EV owner survey reports high home access by dwelling type and `44%` workplace access among commuting EV owners. Private-access defaults are conservative below survey values because survey respondents are EV-owner-biased. Public-access terms are calibrated against charge rate, patch rate, final-SoC drift, AFDC charger-zone distribution, and detour sanity. The latest exhaustive-grid chunk confirmed this public-access tuple lowered 500-person x 3-seed loss from `0.6490` to `0.6107`, with zero gate breaks and strict sensitivity pass. | Fit by dwelling type, income, employment geography, and observed charging sessions. |
| Person mix | worker `0.62`, student `0.08`, retired `0.12`, other `0.18` | Calibrated schedule segments, not demographic truth. Validation target: median `10-16` legs/person-week and higher active days for workers/students than retired/other. | Replace with TTS person weights or StatCan labour/student/age stratification. |
| Weekly schedule | worker weekday work `0.84`, student weekday school `0.86`, non-work weekday outing `0.50`, after-work stop `0.28`, worker weekend work `0.12`, weekend outing `0.66`, weekend second stop `0.34`; work arrive-by mean `8.95h`, school `8.35h` | Calibrated against common commute structure: weekday AM work/school, weekday PM home, weekend retail/leisure, active-day sanity, and arrive-by delay gates. The schedule-only fitted values reduced validation loss while preserving charger-access assumptions. | Fit purpose/time matrices from TTS OD records. |
| Destination choice | zone attraction weights, traffic-count attraction exponent `0.25`, distance decays `tau`: work `24 km`, school `7`, retail `10`, leisure `16`, home `20`, transit `28`, other `13`; soft caps `35-120 km`; general outing tails include transit/other at `4-6%` | Gravity model calibrated to OSM route distribution, zone-purpose alignment, destination-share sanity, and Toronto traffic-count FSA exposure. Under the prior objective, `traffic_attraction_exponent=0.25` improved 500-person x 3-seed mean loss from `0.4548` to `0.4488`, AM FSA L1 from `0.6901` to `0.6788`, and PM FSA L1 from `0.6705` to `0.6415`, with zero gate breaks. The misc/transit tail was added after strict sample-evidence validation exposed zero/low transit samples in weekly plans. | Replace attractions with POI/employment/school counts and TTS OD calibration. |
| Static traffic | weekday AM `1.32`, PM `1.38`, midday `1.10`, weekend midday `1.14`, overnight `0.92` | FHWA travel-time-index framing plus Toronto congestion context. It is a static multiplier, not an observed edge-speed fit. | Snap Toronto midblock/turning counts to OSM and derive hour/day speed profiles. |
| Normal charging | base logits home `-1.2`, work `-1.4`, school `-1.6`, retail/other `-1.8`, leisure `-2.0`; coefficients `3.0`, `2.0`, `1.0`; dwell threshold `0.20h`; public access home `0.30`, work `0.25`, retail `0.45` | ABM charging literature supports SoC, future need, dwell, access, and proximity terms. Coefficients are calibrated to charges/EV-week, patch share, public-use sanity, AFDC public charger zone distribution, and final-SoC drift. | Fit from observed session start SoC, dwell, and charger access data. |
| Patch charging | utilities previous home `2.0`, previous work `0.8`, current origin `0.2`, near-route public `0.4`, forced public `-2.0`; detour coefficient `-0.25/km`; wait `-0.03/min`; extra time `-0.05/min`; softmax temperature `0.90` | Calibrated repair layer. Validation target: zero failed/negative-battery trips, forced patches rare, patch share stress-but-not-default, target SoC not full, and median inconvenience plausible. The calibration grid now tests `0.50`, `0.90`, and `2.00` because `0.70` vs `0.90` was too narrow to reliably move patch-choice mix. | Fit from route deviation and emergency/fast-charge behavior if session data is available. |
| Fallback public charger proxies | per-FSA proxy counts residential `0.35`, leisure `1.25`, office `2.25`, retail `3.50`, transit `5.00`; proxy distance by zone `0.30-1.40 km`; availability `0.75-0.95` | Only used when real charger data is missing. Values encode zone intensity and keep tests deterministic. | Prefer AFDC/OSM/OpenChargeMap, then remove proxy from real runs. |
| Grid baseline/headroom | `baseline_peak_utilization=0.82`; `grid_ev_load_scale=1.0` default; `vehicle_population_share=0.46`; optional `--population-scale-grid` | Baseline load is modeled as `IESO_hour_fraction * baseline_peak_utilization * proxy_capacity_kw`, so the no-EV baseline cannot overload by construction. `grid_ev_load_scale` is an explicit representative-agent multiplier for regional scaling or adoption stress tests; it defaults to `1.0` so simulation outputs remain per simulated agent unless the scenario says otherwise. `--population-scale-grid` sets scale to observed FSA population times the default road-user share, or explicit `--population-share`, divided by simulated people. | Replace proxy capacities and scaling with feeder/transformer data, vehicle ownership by FSA, or explicit expansion weights. |

## Calibration And Validation Harness

Implementation:
- `backend/simulation_validation.py` provides single-run gates, multi-seed stability gates, strict real-grid requirements, hackathon-data mapping checks, purpose-zone alignment checks, charger-concentration checks, and multi-seed directional stress checks.
- `backend/model_calibration.py` converts sanity bands into a numeric fit loss over mobility, charging, patching, and edge-flow metrics. Candidate-grid fitting can run candidates in parallel with `jobs > 1`; the parallel path is candidate-level only, preserves the same deterministic ranking as the serial path, checkpoints completed candidate rows, reports progress after each completed candidate, and falls back to serial execution for stdin/ad-hoc snippets where Python process spawning has no importable main file.
- `backend/observed_targets.py` extracts observed/proxy targets from current repo data artifacts: Toronto traffic-count-derived zone/FSA weights, AFDC charger zone distribution, and the IESO-style hourly load profile.
- `data_preparation/run_model_validation.py` is the repeatable CLI for validation, sensitivity checks, and candidate-grid calibration ranking. Use `--fit-strategy adaptive` for normal iteration: it screens a representative candidate set with fast FSA-corridor flows, re-runs the best candidates at fit scale, then confirms finalists with full validation-scale OSM edge traversal. Use `--fit-jobs N` to parallelize candidate screens, `--fit-num-people` / `--fit-seeds` to bound fit size separately from validation size, and `--fit-checkpoint` / `--resume-fit` for long real-grid screens.
- `MobilitySimulationEngine` keeps a process-local static context cache for immutable FSA tables, road graphs, route matrices, and snapped charger catalogs. The cache key includes data-file signatures, so changing FSA, population, traffic, route-cache, graph, or charger-cache files forces a reload while behavioral candidate parameters can vary without reloading GIS inputs.
- `MobilitySimulationEngine.aggregate_fsa_corridor_flows` is a calibration-only speed path. It preserves real route distance, travel-time buckets, FSA exposure, and route-km conservation, but aggregates each OSM route to its FSA corridor instead of expanding through every road edge. Final validation and map artifacts still use full edge traversal.
- GPU note: the current bottleneck is Python control flow over agents, pandas aggregation, process startup/cache loading, and road-route bookkeeping. The model has little dense tensor work, and the local machine has no CUDA GPU. Meaningful GPU acceleration would require rewriting the simulator into vectorized array kernels first; the higher-return near-term path is successive-halving candidate search, FSA-corridor screening, caching, and CPU process parallelism.
- `backend/road_grid_dashboard.py` is the app/runtime adapter; it runs the same weekly road-grid model and collapses FSA/day/hour load into one peak row per FSA for map and optimizer views.
- Strict real-grid validation now checks that hackathon FSA polygons/zone/capacity/population/traffic artifacts are joined onto the road-grid model, sampled route paths use valid OSM nodes and edges, the public charger catalog is only real AFDC/OSM data, public normal/patch charge events do not silently fall back to zone-proxy chargers, executed arrive-by trips keep low delay tails after charging/patching, sampled destination purposes align with the FSA zone taxonomy, and public charging does not collapse onto a few stations.
- Temporal validation checks leg bounds, travel-time consistency, no overlapping drive legs, charge bounds, charge-duration consistency, no charge-drive overlap, no charge-charge overlap, and no destination-prepay patch charging.
- Optional repeated-week validation replays the same weekly plan using week-one final SoC as week-two initial SoC. It checks second-week SoC drift, reserve safety, full-battery pile-up, charge/patch frequency, and temporal consistency.

Current fit target status: the default v1 config is not a statistically estimated model; it is a calibrated parameter set. The current loss is computed against named target bands and observed/proxy target distances. It should be reduced further when TTS trip tables, snapped traffic counts, or observed charging sessions are added.

Calibration scoring caveat: metrics that validation treats as sample-insufficient for rare events, currently median patch inconvenience and public charge/patch detour p95, have `missing_loss=0.0` in `backend/model_calibration.py`. Public charger concentration and transit-hub purpose alignment use small `missing_loss=0.10` values so insufficient rare-event evidence is not free, but it does not dominate small calibration screens. Transit-hub and other destination shares are direct fit targets, so the tail is frequency-bounded in addition to zone-aligned. The model is still penalized through patch rate, charge rate, public charge events per EV, reserve violations, purpose-zone alignment, charger concentration when enough public events are sampled, and observed-target distances.

Observed/proxy target layer:
- `traffic_morning_zone_l1`: L1 distance between simulated weekday AM trip endpoint-zone exposure and Toronto traffic-count-derived Morning zone weights.
- `traffic_evening_zone_l1`: same for weekday PM.
- `traffic_morning_fsa_l1`: L1 distance between simulated weekday AM route-edge exposure by FSA and Toronto traffic-count FSA weights.
- `traffic_evening_fsa_l1`: same for weekday PM.
- `public_charger_zone_l1`: L1 distance between public charge-event energy by zone and AFDC public charger distribution by zone.
- `hourly_load_profile_corr`: correlation between simulated hourly charging energy and the IESO-style baseline profile. This is a loose diagnostic because EV load need not match baseline demand.
- Public charger distribution is only a strict gate once at least `30` public charge events are sampled; below that the metric is reported but treated as sample-insufficient.

Current observed/proxy fit, 500 people x seeds `101,202,303`:
- mean loss with observed targets, geography/concentration fit terms, and arrive-by-tail fit terms included: `0.6107`
- arrive-by delay tail means: `0.084%` of commute legs over 20 minutes late; max delay `20.28 min`
- morning endpoint-zone L1: `0.3082`
- evening endpoint-zone L1: `0.3072`
- morning FSA route-exposure L1: `0.5925`
- evening FSA route-exposure L1: `0.5050`
- public charger zone L1: `0.2622`
- destination alignment means: home `79.61%`, work `59.21%`, retail `40.08%`, leisure `98.45%`
- public charger concentration means: top 1% of used public chargers `8.05%` of public charging energy; top 10% `32.93%`
- hourly load profile correlation: `0.7284`
- final SoC mean drift: `-2.0890` percentage points
- final SoC at full: `0.0%`
- charges/EV-week: `2.940`
- patches/EV-week: `0.187`
- public charges/EV-week: `0.685`
- public charge detour p95: `9.1120 km`
- public patch detour p95: `8.5651 km`

Latest focused calibration screen:
- Updated-objective real-grid screen, 250 people x seeds `101,202`, 16 representative candidates: a candidate with lower home/work charger access, lower patch softmax temperature, and higher traffic-attraction exponent scored `0.6426` vs default `0.7245`, with zero breaks. This was treated as a screening result only because the sample was small and it partially conflicted with external home/work charger-access evidence.
- Stronger follow-up, 500 people x seeds `101,202,303`: current default remained best with loss `0.6674` and zero breaks. The component checks scored patch-softmax-only `0.6928`, traffic-attraction-only `0.6961`, the combined screen winner `0.7867`, and lower-home/work-access-only `1.0442` with one break. No parameter change is justified by the current target set.
- Wider parallel real-grid screen, 250 people x seeds `101,202`, 32 representative candidates, `--fit-jobs 4`: two tied screen winners scored `0.6426` vs default `0.7245`, again by lowering home/work charger access and increasing traffic-attraction exponent. Confirmation at 500 people x seeds `101,202,303` rejected both: default `0.6674`, screen variant with default target/patch temperature `0.7796`, screen variant with lower target/patch temperature `0.7867`, all zero-break. No parameter change is justified.
- Target-SoC audit after fixing weekly normal charging to use `target_soc` as its activity-target anchor: 500 people x seeds `101,202,303` scored `target_soc=0.85` best on fit loss at `0.5606`, but it failed strict sensitivity because higher reserve did not increase patching and the smaller validation sample crossed the full-battery pile-up gate. The accepted default is `target_soc=0.82`: loss `0.6490` vs old `0.6674`, zero fit breaks, zero small-sample base breaks, strict sensitivity pass, final SoC drift `-4.12` percentage points, and final full SoC `0.0%`. Nearby rejected values: `0.83` loss `0.6073` but one small-sample final-SoC drift break; `0.84` loss `0.5841` but one sensitivity break; `0.90` loss `0.7501` with one full-battery break.
- Patch-softmax audit: direct stressed patch-choice sampling showed `0.70` vs `0.90` can be nearly degenerate, while high temperature materially increases near-route/forced public patch alternatives. Candidate fitting now scans `0.50`, `0.90`, and `2.00`; default remains `0.90` until a wider real-grid screen justifies changing it.

Grid/headroom validation:
- weekly grid load is emitted by `MobilitySimulationEngine.aggregate_weekly_grid_load`
- no-EV baseline overloads are forbidden
- EV load is conserved exactly from hourly charge aggregation into the grid table
- baseline peak utilization is `82%`, leaving explicit headroom before EV load
- stress scenarios can use `--grid-ev-load-scale`; for example, `--grid-ev-load-scale 20` produced 466 overloaded FSA-hours in a 500-person real-grid run, while preserving baseline semantics
- population scaling can use `--population-scale-grid`; with the default road-user share, `--num-people 500` produces scale `7722.141` from `8,393,632 * 0.46 / 500`
- explicit stress overrides remain available; for example, `--population-share 0.05 --num-people 500` produces scale `839.363` from `8,393,632 * 0.05 / 500`

Latest real-grid candidate screen:
- baseline before fitting: `initial_soc_alpha=5`, `initial_soc_beta=2`, `target_soc=0.80`, `patch_softmax_temperature=0.70`
- fitted default: `initial_soc_alpha=6`, `initial_soc_beta=2`, `target_soc=0.82`, `patch_softmax_temperature=0.90`
- 250-person, two-seed candidate screen: mean loss improved from `0.2421` to `0.2216`, with zero gate breaks
- 800-person, three-seed confirmation: mean loss `0.2190`, zero gate breaks
- 1,500-person strict confirmation: all gates passed

Latest public-charging geography fit:
- previous public-access defaults: home-public `0.15`, work-public `0.25`, retail-public `0.70`
- fitted public-access defaults: home-public `0.30`, work-public `0.25`, retail-public `0.45`
- 500-person, three-seed comparison after polygon-based OSM node-to-FSA mapping, detour targets, and final-SoC drift targets under the prior objective: mean loss improved from `0.6237` to `0.5726`; AFDC public charger zone L1 improved from `0.3872` to `0.2498`; forced-origin patches stayed `0.0/EV-week`; all strict gates passed
- exhaustive-grid chunk `[0,48)` found the updated public-access tuple as candidate `2`; 250-person x seeds `101,202` loss `0.7255` vs default `0.7443`, both zero-break. Confirmation at 500 people x seeds `101,202,303` scored `0.6107` vs old default `0.6490`, with zero breaks, lower final-SoC drift magnitude, lower patch rate, and strict sensitivity pass. Public charger zone L1 worsened modestly from `0.2434` to `0.2622`, but remained well inside the target band.

Latest schedule fit:
- previous schedule defaults: worker weekday work `0.88`, weekday non-worker outing `0.55`, after-work stop `0.34`, weekend outing `0.72`
- fitted schedule defaults: worker weekday work `0.84`, weekday non-worker outing `0.50`, after-work stop `0.28`, weekend outing `0.66`
- 500-person, three-seed comparison: mean loss improved from `0.5783` to `0.3878`, with zero gate breaks and passing sensitivity checks
- lower charger-access candidates scored slightly better, but were not adopted because home/work charger access already has external survey backing

Sensitivity checks required for v1:
- lower initial SoC increases patching
- lower home/work charger access increases patching
- higher reserve increases patching
- worse driving efficiency increases patching
- lower initial SoC increases total charging
- all stress scenarios still avoid reserve/pre-departure reserve violations

## Real-Grid Validation Snapshot

Command:

```bash
PYTHONPATH=backend uv run python data_preparation/fetch_real_world_grid.py
PYTHONPATH=backend uv run python - <<'PY'
from mobility_simulator import MobilityConfig
from simulation_validation import ValidationOptions, validate_weekly_simulation
cfg = MobilityConfig(ev_probability=0.20, road_graph_source="osm", charger_source="afdc")
opts = ValidationOptions(require_real_grid=True, require_real_chargers=True, include_observed_targets=True)
report, artifacts = validate_weekly_simulation(num_people=1500, seed=101, config=cfg, options=opts)
print(report.to_string(index=False))
PY
```

Result on the cached full-GTA road graph and AFDC charger catalog:
- hackathon data mapping: 260 GTA FSA rows, unique FSA codes, required spatial/model columns present, positive proxy capacity, centroids in GTA bounds, 100.0% positive `population_2021`, Toronto traffic counts affecting 41.15% of FSAs, 260 FSA road anchors, and a 260 x 260 route matrix
- road graph: OSM, 145,540 nodes, 383,093 edges, 0 unreachable FSA OD pairs
- route paths use OSM node IDs and sampled adjacent route edges exist in the OSM graph for time-resolved edge-flow aggregation
- chargers: 2,889 AFDC public chargers mapped to FSAs and snapped to OSM road nodes; charger snap p95 `261.13 m`
- strict source checks: public charger catalog source mix `{'afdc': 2889}`, public normal charge sources real, public patch charge sources real
- destination purpose-zone alignment in 500-person real-grid seeds `101,202,303`: home-to-residential `77.68-81.10%`, work-to-office/retail/transit `56.06-61.14%`, school-to-residential/office/leisure `93.67-100.00%`, retail-to-retail/transit/office `38.37-42.42%`, leisure-to-leisure/retail/residential `98.37-98.54%`
- public charger concentration in 500-person real-grid seeds `101,202,303`: 78-82 public charge events, 51-56 public chargers used, top 1% of used public chargers carrying `7.89-9.52%` of public charging energy, and top 10% carrying `25.47-35.51%`
- mobility: median 12 legs/person-week; median weekly distance 230.41 km/person
- route km: p50 19.14 km, p90 49.89 km
- schedule: p95 arrive-by delay 0.0 min in the 1,500-person validation run; in the 500-person x seeds `101,202,303` fit run, `0.084%` of commute legs were over 20 minutes late and max delay averaged `20.28 min`
- charging: 0 reserve violations; 0 pre-departure reserve violations
- final SoC stability: mean drift `-5.1` percentage points, 0 final below-reserve EVs, 0.0% final full SoC
- charging frequency: 2.81 charges/EV-week; 0.19 patches/EV-week
- forced public patches: 0.0/EV-week
- charge-event mapping: origin context complete, nonnegative detours, public charge detour p95 `8.89 km`, public patch detour p95 `9.68 km`
- temporal consistency: leg bounds pass, charge bounds pass, charge-drive overlaps `0`, charge-charge overlaps `0`, destination-prepay patches `0`; 500-person real-grid seed `101` had max leg timing residual `0.03 min`
- repeated-week stability with `--repeat-week`: 500-person real-grid seed `101` replayed from week-one final SoC with 102 EVs, second-week mean SoC drift `+0.43` percentage points, 0 final below-reserve EVs, 0.0% final full SoC, 3.33 charges/EV, 0.41 patches/EV, reserve and temporal replay checks passing
- patch inconvenience: median 2.26 minutes because most patch charging occurs within existing home/work/current-origin dwell; public route patches still carry detour/delay
- edge-flow concentration: top 1% OSM edge-hour buckets carry 8.98% of simulated vehicle-edge traversals
- edge-flow volume: 745,024 edge-hour rows, 1,242,036 simulated vehicle-edge traversals, and 115 occupied day-hour buckets in the 1,500-person validation run
- edge-flow route-km conservation: pass, with `0.0 km` rounded difference between routed leg distance and edge-bucketed distance
- load aggregation: hourly energy conservation passes exactly within floating tolerance
- grid aggregation: no-EV baseline has 0 overloads, baseline peak utilization is 82%, default `grid_ev_load_scale=1.0`, and 1,500-person default run has 0 overloaded FSA-hours. Validation now checks that observed EV load scale matches configured `grid_ev_load_scale` and that EV load is conserved against the configured scale, not the observed scale. Population-scaled scenario runs are explicitly separate from default per-agent validation.
- observed/proxy targets: morning zone L1 `0.2998`, evening zone L1 `0.3060`, morning FSA route-exposure L1 `0.5604`, evening FSA route-exposure L1 `0.4915`, public charger zone L1 `0.2709`, hourly load correlation `0.6973`
- population-scaled current-stock smoke test: `--population-scale-grid --ev-probability 0.03 --num-people 500 --seeds 101` passes road/grid/charging gates with scale `7722.141`, 55 overloaded FSA-hours, no patch events because the EV sample is small, and public charger distribution reported as sample-insufficient with only 3 public charge events.
- population-scaled validation-share run: `--population-scale-grid --ev-probability 0.20 --num-people 500 --seeds 101` passes all gates with scale `7722.141`, 905 overloaded FSA-hours, charges/EV-week `2.78`, patches/EV-week `0.22`, final SoC drift `-3.52` percentage points, and public charger zone L1 `0.1539`.

Multi-seed strict validation:

```bash
PYTHONPATH=backend uv run python data_preparation/run_model_validation.py \
  --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity
```

Result: seed pass rate 100%, broken gate count 0, and all directional sensitivity checks pass. Stress scenarios can break baseline plausibility bands, which is acceptable; the required invariant is that reserve/pre-departure reserve violations remain absent and patching responds in the expected direction. The sensitivity suite now checks both sides of charger-access logic: lower home/work access must raise patching, while higher home/work access must lower total and weekday patching.

Latest implementation verification:
- Reduced real-grid calibration screen, 250 people x seed `101`, 8 candidates: default ranked first under updated geography/concentration objective, loss `0.6739`, zero breaks.
- 500-person x seeds `101,202,303` current default fit summary after fixing target-SoC fitting: loss `0.6490`, zero breaks; arrive-by over-20-minute tail `0.084%`, max delay `20.28 min`.
- Updated-objective 16-candidate screen, 250 people x seeds `101,202`: screen winner loss `0.6426` vs default `0.7245`, zero breaks; 500-person x seeds `101,202,303` confirmation rejected the screen winner and kept the default at loss `0.6674`.
- Wider parallel real-grid screen, 250 people x seeds `101,202`, 32 candidates, `--fit-jobs 4`, output directory `backend/data/validation/parallel_fit_32`: strict pre-fit suite passed with seed pass rate 100% and broken gate count 0; top two tied screen winners were rejected by 500-person x seeds `101,202,303` confirmation, so defaults remain unchanged.
- Target-SoC strict validation after accepting `target_soc=0.82`: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity` passed with seed pass rate 100%, broken gate count 0, and all directional sensitivity checks passing.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity`: seed pass rate 100%, broken gate count 0; sensitivity still passes after adding public-use, geography, concentration, and arrive-by-tail fit metrics.
- Real-grid fit operability update: a 36-candidate, 500-person, three-seed screen was alive but too slow for interactive validation because old `executor.map` output was all-or-nothing. The fitter now writes `fit_candidate_checkpoint.csv` incrementally and prints one line per completed candidate.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 1 passed in 10.11s, including checkpoint/resume coverage.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --observed-targets --num-people 120 --seeds 111 --fit --fit-num-people 80 --fit-seeds 111 --max-candidates 3 --fit-jobs 2 --out-dir backend/data/validation/progress_fit_smoke`: passed. The report used cached OSM/AFDC data (`145,540` nodes, `383,093` edges, `2,889` chargers, zero unreachable OD pairs) and wrote both `fit_candidate_checkpoint.csv` and `fit_candidate_ranking.csv` with three candidate rows.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 250 --seeds 101 --fit --fit-num-people 150 --fit-seeds 101 --max-candidates 8 --fit-jobs 4 --out-dir backend/data/validation/real_grid_fit_progress_8`: passed strict OSM/AFDC validation and wrote an eight-row checkpoint/ranking. Candidate `0` (current default) was the best zero-break fit candidate with loss `0.8663`; nearest zero-break challenger candidate `3` scored `0.8767`, so this screen does not justify a default change.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-num-people 250 --fit-seeds 101 202 --max-candidates 36 --fit-jobs 4 --out-dir backend/data/validation/real_grid_fit_progress_36 --resume-fit`: passed strict validation first (`100%` seed pass rate, zero broken gates) and completed all 36 checkpointed representative fit candidates. Under the then-current defaults, candidate `0` was best zero-break with loss `0.7443`; this was superseded by the exhaustive chunk below.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-num-people 250 --fit-seeds 101 202 --all-candidates --candidate-start 0 --candidate-stop 48 --fit-jobs 4 --out-dir backend/data/validation/full_grid_chunk_000_048 --resume-fit`: passed strict validation first (`100%` seed pass rate, zero broken gates) and completed the first exhaustive full-grid chunk. Candidate `2` won the chunk at loss `0.7255` vs old default `0.7443`, both zero-break.
- Candidate `2` confirmation at 500 people x seeds `101,202,303`: loss `0.6107` vs old default `0.6490`, zero breaks. Repeat-week remained zero-break; sensitivity passed with patch-rate increases under low initial SoC `+0.408`, low access `+0.327`, higher reserve `+0.204`, cold efficiency `+0.224`, low initial SoC charge-rate increase `+0.531`, and no reserve violations. Accepted defaults: home-public `0.30`, work-public `0.25`, retail-public `0.45`.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_public_access_fit`: passed with seed pass rate `100%`, broken gate count `0`, and all sensitivity checks passing.
- Current-default exhaustive chunk `[0,48)`: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-num-people 250 --fit-seeds 101 202 --all-candidates --candidate-start 0 --candidate-stop 48 --fit-jobs 4 --out-dir backend/data/validation/full_grid_current_default_chunk_000_048 --resume-fit` passed strict validation and completed 48 candidates. Current default candidate `0` remained best zero-break with fit-screen loss `0.7255`; the old public-access tuple appeared as candidate `2` at `0.7443`.
- Current-default exhaustive chunk `[48,96)`: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-num-people 250 --fit-seeds 101 202 --all-candidates --candidate-start 48 --candidate-stop 96 --fit-jobs 4 --out-dir backend/data/validation/full_grid_current_default_chunk_048_096 --resume-fit` passed strict validation and completed 48 candidates. Best zero-break candidate in the chunk was `74` at loss `0.7829`, so it did not beat the current default.
- Current-default exhaustive chunk `[96,144)`: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-num-people 250 --fit-seeds 101 202 --all-candidates --candidate-start 96 --candidate-stop 144 --fit-jobs 4 --out-dir backend/data/validation/full_grid_current_default_chunk_096_144 --resume-fit` passed strict validation and completed 48 candidates. Best zero-break candidate in the chunk was `128` at loss `0.7500`, so it did not beat the current default.
- Combined current-default exhaustive coverage so far: 144/652 candidates screened, 60 zero-break rows; best remains current default candidate `0` with fit-screen loss `0.7255`.
- Speed-path update: exhaustive chunk `[144,192)` was stopped after 20/48 candidates because interactive brute-force screening is too slow for the current stage. Added calibration-only FSA-corridor flow aggregation and `--fit-strategy adaptive`. Smoke command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 80 --seeds 111 --fit --fit-strategy adaptive --fit-num-people 50 --fit-seeds 111 --adaptive-stage1-candidates 6 --adaptive-stage1-people 40 --adaptive-stage2-top 3 --adaptive-final-top 2 --fit-jobs 2 --out-dir backend/data/validation/adaptive_fit_smoke --resume-fit` passed. The strict pre-fit validation used full OSM/AFDC data; adaptive candidate screens used the FSA flow proxy; final candidate confirmation used full validation options.
- FSA-corridor flow benchmark on a 500-person real-grid run: full edge expansion produced `321,360` rows in `1.821s`; FSA-corridor aggregation produced `39,305` rows in `0.279s`, a `6.5x` aggregation speedup, with exact routed-km conservation in both modes. This does not remove the need for final full-edge validation; it makes candidate screening cheaper.
- Practical adaptive real-grid fit: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-candidates 128 --adaptive-stage1-people 120 --adaptive-stage2-top 24 --adaptive-final-top 6 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_current_default --resume-fit` passed strict pre-fit validation (`100%` seed pass rate, zero broken gates). Stage 1 screened 128 candidates with FSA-corridor flows; best screen candidate was `343` at loss `0.7208`. Stage 2 re-ranked 24 candidates at 250 people x seeds `101,202`; best was `563` at `0.6746`, with current default candidate `0` at `0.7223`. Final full OSM-edge confirmation at 500 people x seeds `101,202,303` kept current default as the best zero-break candidate: candidate `0` loss `0.6107`; next zero-break candidates were `297` at `0.7928`, `246` at `0.8028`, `240` at `0.8201`, and `563` at `0.9453`; candidate `251` scored `0.7289` but had one broken gate. No parameter change is justified.
- Full-grid adaptive screen attempt: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_all_candidates --resume-fit` passed strict pre-fit validation and completed 105/652 stage-1 candidates before being stopped for speed redesign. Best completed zero-break stage-1 candidates were `6`, `12`, and `18` at loss `0.7889`; current default candidate `0` scored `0.8735` in that cheap screen. No conclusion should be drawn until the remaining stage-1 candidates and final full-edge confirmation complete.
- Resume-safety update: adaptive stage checkpoint filenames now include stage population, seed set, edge-flow detail, candidate selection, and top-N shape. This prevents incompatible `--resume-fit` reuse when changing from a 120-person screen to a faster or slower screen in the same output directory.
- Completed all-candidate adaptive fit after speed redesign: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_all_candidates_fast --resume-fit` passed strict pre-fit validation with `291` PASS rows, zero failures, and seed pass `{101: True, 202: True, 303: True}`. Stage 1 screened all `652` candidates; `159` were zero-break, with best cheap-screen candidate `242` at loss `0.7032`. Stage 2 re-ranked `32` candidates; current default candidate `0` was the best zero-break at loss `0.7223`. Final full OSM-edge confirmation on `8` candidates kept candidate `0` as the best zero-break at loss `0.6107`; next zero-break candidates were `332` at `0.7367`, `338` at `0.7440`, `344` at `0.7581`, `279` at `0.7761`, `285` at `0.7807`, and `291` at `0.8070`. Candidate `228` had loss `1.1318` and one broken gate. No default parameter change is justified.
- Strict sample-evidence validation: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --require-sample-evidence --num-people 1000 --seeds 101 202 303 --out-dir backend/data/validation/strict_sample_evidence_1000` passed with `291` PASS rows and zero failures. Transit-hub purpose-zone samples were `135`, `124`, and `138`; aligned shares were `62.22%`, `59.68%`, and `66.67%` against the `40%` threshold. Public charge events were `184`, `162`, and `181`; public patch detour p95 values were `9.03`, `9.76`, and `8.85 km`.
- Standard current-default suite after adding the misc/transit destination tail: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_misc_destination_tail` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing. Transit-hub samples were `62`, `56`, and `47`, with aligned shares `56.45%`, `67.86%`, and `63.83%`.
- Post-tail all-candidate adaptive fit: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_after_misc_tail --resume-fit` passed strict pre-fit validation and screened all `652` candidates. Final full OSM-edge confirmation kept current default candidate `0` as best zero-break with loss `0.6981`; next zero-break candidates were `337` and `331` at `0.8496`, `284` and `278` at `0.8770`, and `249`/`243` at `0.9040`. Candidate `255` had one broken gate. No default parameter change is justified.
- Post-transit-fit-target all-candidate adaptive fit: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_after_transit_target --resume-fit` passed strict pre-fit validation and screened all `652` candidates using `transit_hub_zone_alignment_pct` in the fit objective. Final full OSM-edge confirmation kept current default candidate `0` as best zero-break with loss `0.7000`; next zero-break candidates were `337` at `0.8559`, `331` at `0.8560`, `284` and `278` at `0.8834`, and `249`/`243` at `0.9047`. Candidate `255` had one broken gate. No default parameter change is justified.
- Destination-share-target validation: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_destination_share_targets` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing. Transit-hub destination shares were `1.06%`, `0.93%`, and `0.78%`; other destination shares were `0.73%`, `0.97%`, and `0.79%`.
- Post-destination-share-target all-candidate adaptive fit: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_after_destination_share_targets --resume-fit` passed strict pre-fit validation and screened all `652` candidates using destination-share targets in the fit objective. Final full OSM-edge confirmation kept current default candidate `0` as best zero-break with loss `0.7006`, transit-hub share `0.92%`, and other share `0.83%`; next zero-break candidates were `337` at `0.8565`, `331` at `0.8566`, `284` and `278` at `0.8840`, and `249`/`243` at `0.9048`. Candidate `255` had one broken gate. No default parameter change is justified.
- Access-sensitivity validation: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_access_sensitivity_gate` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing. Low access raised patching by `+0.633` patches/EV-week; high access reduced patching by `0.204` patches/EV-week and weekday patching by `0.122` patches/EV-week.
- Grid-conservation validation fix: `ev_load_energy_conservation_kw` now compares grid EV load against raw hourly charge load times configured `grid_ev_load_scale`; a focused regression test catches deliberately mis-scaled grid load. `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_grid_conservation_fix` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing.
- Full route-path validation: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_full_route_path_validation` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing. It validated `3,072`, `3,050`, and `3,180` unique full route paths for seeds `101`, `202`, and `303`; all referenced OSM nodes and route edges existed.
- Hardware note for this environment: `sysctl` reports 10 logical CPUs, 4 performance physical CPUs, and 16 GB RAM. Use `--fit-jobs 4` as the default for real-grid fitting; more workers may help only after measuring memory pressure and throughput.
- Full-edge speed update: `RoadNetwork.route_edge_segments` now caches segment decompositions per route path, and `RoadNetwork.route_edge_template` caches OD-level segment plus FSA-bucket metadata. On a 500-person cached real OSM run, full edge aggregation measured `1.4720s` cold and `1.0241s` warm with identical routed-km conservation and `3,105` cached OD templates. This helps final strict validation and dashboard rerenders; early fitting still uses FSA-corridor aggregation.
- Runtime speed update: `load_or_fetch_osm_drive_graph` now writes `backend/data/cache/gta_drive_graph.pkl` after the first GraphML load. On this machine, repeated cold real-grid engine initialization dropped from `11.620s` to `2.095s`. Edge-flow aggregation now uses tuple iteration and column-list construction instead of `iterrows` and per-row dicts; on a 2,500-person real-grid run, warm full OSM edge aggregation dropped from about `6.93-7.00s` to `3.20s`, while FSA-corridor aggregation dropped to about `0.69s`. Strict validation after this change passed: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_edge_flow_speedup` returned seed pass rate `100%`, broken gate count `0`, and all sensitivity checks passing.
- Charging-loop speed update: `simulate_weekly_charging` now uses per-person dict records and precomputed future-needed-energy arrays instead of repeatedly materializing pandas Series rows inside the SoC state machine. On a 2,500-person real-grid run, charging simulation dropped from about `9.669s` to `2.018s` with the same leg/charge counts for seed `101/102` (`29,441` legs, `265` charge events). Strict validation after this change passed: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_charging_speedup` returned seed pass rate `100%`, broken gate count `0`, and all sensitivity checks passing.
- Charge-accounting validation update: validation now checks event-level `duration_h * charger_kw == energy_delivered_kwh`, max-duration bounds, SoC bounds, target-SoC consistency, and a chronological replay of visible charge events plus drive legs. This exposed hidden sub-`0.25 kWh` charge events that changed SoC but were omitted from the charge table; `simulate_weekly_charging` now records every positive delivered charge event. Strict validation after this change passed: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_charge_accounting_gates` returned seed pass rate `100%`, broken gate count `0`, and all sensitivity checks passing. Base charges rose slightly from `3.265` to `3.286` charges/EV-week and patches from `0.265` to `0.286` patches/EV-week because previously hidden tiny events are now visible.
- Post-charge-accounting adaptive fit: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy adaptive --all-candidates --fit-num-people 250 --fit-seeds 101 202 --adaptive-stage1-people 120 --adaptive-stage2-top 32 --adaptive-final-top 8 --fit-jobs 4 --out-dir backend/data/validation/adaptive_fit_after_charge_accounting --resume-fit` passed strict pre-fit validation and screened all `652` candidates. Final full OSM-edge confirmation found candidate `293` best zero-break at loss `0.6892` vs current default candidate `0` at `0.7013`; candidate `293` only lowers `patch_softmax_temperature` from `0.9` to `0.5`. Because the improvement was small relative to seed variance, it was tested out of sample on seeds `404,505,606`; default scored `0.7506` with zero breaks while candidate `293` worsened to `0.8395` and failed one full validation gate (`public_patch_detour_p95_km` on seed `606`). No default parameter change is justified.
- Sensitivity validation now aggregates stress-scenario metrics across all supplied seeds before applying monotonic directional checks. This replaces the prior single-seed CLI check, which could fail on one noisy seed even when the multi-seed response was correct. Out-of-sample command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 404 505 606 --sensitivity --out-dir backend/data/validation/default_multiseed_sensitivity_404_505_606` passed with seed pass rate `100%`, broken gate count `0`, and all averaged sensitivity checks passing; averaged cold-efficiency patch delta was `+0.170` patches/EV-week.
- Process-safety and speed update: validation multiprocessing no longer uses macOS-unsafe `fork`; spawned workers are used only from real script entrypoints, while stdin/ad-hoc runs fall back to serial before child processes are created. A CLI smoke command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --num-people 120 --seeds 101 202 --validation-jobs 2 --out-dir backend/data/validation/parallel_spawn_smoke` passed with seed pass rate `100%` and zero broken gates, and no orphan `spawn_main` or resource-tracker workers remained afterward. OD edge-template expansion is now persisted; on a 1,000-person real-grid run, full OSM edge aggregation dropped from `3.763s` before cache reuse to `1.319s` after reload with the same `549,469` edge-flow rows.
- Public charger mapping validation update: validation now checks that every simulated public charge event references a public charger ID in the loaded catalog and that source/FSA/location/power attributes match the catalog row. The regression test mutates one public charge ID and confirms both catalog gates break. Real-grid command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --validation-jobs 2 --out-dir backend/data/validation/default_parallel_mapping_gate` passed with seed pass rate `100%`, broken gate count `0`, and both new catalog-mapping gates at `100%` for seeds `101`, `202`, and `303`.
- Edge-flow artifact integrity update: validation now checks that edge-flow rows have required columns, finite/nonnegative numeric counts, `ev_count <= vehicle_count`, valid time buckets, valid FSA/zone labels, and for full OSM validation that every aggregated `edge_u`/`edge_v` is an OSM node pair with a real graph edge. A regression test corrupts `ev_count`, `route_km`, and hour values and confirms the new gates break. Real-grid command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --validation-jobs 2 --out-dir backend/data/validation/default_after_edge_flow_integrity_gate` passed with seed pass rate `100%`, broken gate count `0`, and all seven edge-flow integrity metrics passing for seeds `101`, `202`, and `303`.
- Private charger mapping validation update: validation now checks that private charge events use valid home/work/school private charger IDs, match the current origin FSA and zone, use the FSA centroid coordinates, use 7 kW / zero-detour semantics, and reference a valid road node. A regression test mutates one private event's FSA, charger ID, and detour and confirms the private mapping gates break. Real-grid command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --validation-jobs 2 --out-dir backend/data/validation/default_after_private_charge_mapping_gate` passed with seed pass rate `100%`, broken gate count `0`; private events were `215`, `204`, and `204` for seeds `101`, `202`, and `303`, all at `100%` location/power-detour match with valid road nodes.
- Cache-safety update: route caches and OD edge-template caches now include a fingerprint over graph source, FSA labels, rounded FSA centroids, FSA anchor node IDs, and graph node/edge counts; stale edge-template caches are rejected, and compatible legacy route caches are migrated to fingerprinted payloads. Cache writes now use atomic temp-file replacement to avoid partial files during parallel validation. Focused cache tests passed, and `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --validation-jobs 2 --out-dir backend/data/validation/default_after_cache_fingerprints` passed with seed pass rate `100%`, broken gate count `0`. Afterward, `gta_fsa_routes_osm.pkl` was version `3` with a fingerprint and `260` route rows, `gta_fsa_edge_templates_osm.pkl` was version `2` with a fingerprint and `5,622` templates, and no `*.tmp` cache files remained.
- Out-of-sample validation update: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 404 505 606 --validation-jobs 2 --sensitivity --sensitivity-jobs 2 --out-dir backend/data/validation/out_of_sample_404_505_606_after_cache_private_edge_gates` passed with seed pass rate `100%`, broken gate count `0`, and all directional sensitivity checks passing. Key unseen-seed gates remained stable: `edge_flow_edges_exist` PASS for all three seeds; public charge catalog membership `100%`; private charge origin mapping `100%`; charges/EV-week `3.16`, `2.92`, `3.12`; patches/EV-week `0.20`, `0.22`, `0.23`; repeat-week SoC drift `+4.37`, `+4.35`, `+2.77` percentage points. Sensitivity deltas were positive in expected directions: low initial SoC patch `+0.481`, low access patch `+0.245`, higher reserve patch `+0.128`, cold efficiency patch `+0.290`, low initial SoC charges `+0.908`; high access reduced total patching by `0.154` and weekday patching by `0.095`; stress reserve violations remained absent.
- Current-fit verification after new validation gates: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 500 --seeds 101 202 303 --fit --fit-strategy grid --max-candidates 1 --fit-num-people 250 --fit-seeds 101 202 --fit-jobs 2 --out-dir backend/data/validation/current_fit_after_mapping_cache_gates --resume-fit` passed strict full real-grid validation first (`100%` seed pass rate, zero broken gates). Candidate `0` completed with zero validation breaks; 500-person current summary mean loss was `0.6966`, and the 250-person fit-ranking row for candidate `0` had mean loss `0.8138`, zero max breaks, charges/EV-week `3.19`, patches/EV-week `0.33`, public charges/EV-week `0.86`, and observed hourly load correlation `0.6426`. The audit did not justify changing fitted defaults.
- `PYTHONPATH=backend uv run pytest -q`: `88 passed` in `124.08s` after adding multi-seed sensitivity validation, process-safe validation multiprocessing, persistent OD edge-template caching, public charge-event catalog membership checks, edge-flow artifact integrity checks, private charger mapping checks, and cache-fingerprint migration checks; warnings are PuLP deprecations from optimizer tests.
- Validation manifest update: validation CLI runs with `--out-dir` now write `run_metadata.json` containing command args, Python/runtime, config, validation options, seeds, artifact row counts, validation broken-gate summary, fit/sensitivity summaries when present, and cache-file metadata including route/edge-template cache versions and fingerprint presence. Focused test `PYTHONPATH=backend uv run pytest tests/test_run_model_validation.py -q` passed, and smoke command `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --num-people 120 --seeds 101 202 --validation-jobs 2 --out-dir backend/data/validation/metadata_smoke` passed with seed pass rate `100%`, broken gate count `0`; the emitted manifest recorded Python `3.13.13`, seeds `[101, 202]`, `120` people rows, `74` charge rows, and OSM route cache version `3`.
- `PYTHONPATH=backend uv run pytest -q`: `89 passed` in `113.97s` under Python `3.13.13` after adding validation-run manifest coverage; warnings are PuLP deprecations from optimizer tests.
- Manifest-backed current-fit diagnostic: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --validation-jobs 2 --fit --fit-strategy grid --max-candidates 1 --fit-num-people 250 --fit-seeds 101 202 --fit-jobs 2 --out-dir backend/data/validation/current_fit_with_manifest_break_details_101_202_303` completed under Python `3.13.13`. Full 500-person validation had seed pass rate `100%` and broken gate count `0`. The 250-person fit screen for candidate `0` had `mean_loss=0.813789`, `max_break_count=1`, and explicit break detail `seed 202: repeat_week_patches_per_ev=1`; the 500-person current summary had `mean_loss=0.696563`, `max_break_count=0`, charges/EV-week `2.934969`, patches/EV-week `0.136604`, public charges/EV-week `0.729546`, and observed hourly load correlation `0.70314`. This supports keeping current defaults and treating the fit-screen break as small-sample repeat-week noise unless it recurs in full validation.
- High-scale speed update: destination choice now caches origin-to-destination probability matrices by `(dest_type, hour)` and weekly itinerary generation iterates over NumPy arrays instead of pandas row Series. On the real OSM/AFDC path with FSA-corridor edge aggregation, the 10k benchmark improved from `21.981s` total (`11.918s` itinerary generation) to `10.993s` total (`1.455s` itinerary generation). The new `run_weekly_batched_aggregation` path processes exact agents in bounded-memory chunks and aggregates hourly charging / road flows per chunk. Measured command equivalent to `PYTHONPATH=backend uv run python data_preparation/benchmark_simulation_scale.py --real-grid --num-people 200000 --batch-size 25000 --edge-flow-detail fsa` completed `200,000` people in `222.555s`, producing `2,365,161` legs, `121,956` charge events, `41,141` hourly rows, `43,680` grid rows, and `158,394` FSA-corridor edge-flow rows. This supports 100k-300k exact FSA-corridor simulations under 10 minutes on the local machine; full OSM edge expansion remains for smaller final validation/inspection runs.
- Dashboard high-scale update: `run_weekly_road_grid_simulation` now accepts `batch_size` and `edge_flow_detail`, returning batch summaries plus aggregated hourly/grid/edge outputs for bounded-memory runs. The Streamlit app exposes up to `300,000` sampled drivers and uses `batch_size=25,000` with `edge_flow_detail="fsa"`, keeping exact per-person SoC decisions while avoiding raw leg retention for large runs. Focused runtime smoke with `1,200` people, `batch_size=500`, and FSA-corridor flow returned `3` batches, `14,228` legs, `828` charge events, `62,775` edge-flow rows, and `260` peak-grid rows.
- `PYTHONPATH=backend uv run pytest -q`: `91 passed` in `93.72s` under Python `3.13.13` after wiring the high-scale batched path into the dashboard adapter; warnings are PuLP deprecations from optimizer tests.
- `PYTHONPATH=backend uv run pytest -q`: `90 passed` in `93.72s` under Python `3.13.13` after the high-scale destination-cache and batched-aggregation speed changes; warnings are PuLP deprecations from optimizer tests.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 60 --seeds 111 --fit --fit-strategy adaptive --fit-num-people 40 --fit-seeds 111 --adaptive-stage1-candidates 4 --adaptive-stage1-people 30 --adaptive-stage2-top 2 --adaptive-final-top 2 --fit-jobs 2 --out-dir backend/data/validation/adaptive_checkpoint_smoke --resume-fit`: passed, exercising the stage-specific adaptive checkpoint paths.
- Current-default strict suite after adaptive fitting: `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity --out-dir backend/data/validation/default_after_adaptive_fit` passed with seed pass rate `100%`, broken gate count `0`, and all sensitivity checks passing: low initial SoC patch delta `+0.408`, low access `+0.327`, higher reserve `+0.204`, cold efficiency `+0.224`, low initial SoC charge delta `+0.531`, and no stress reserve violations.
- `PYTHONPATH=backend uv run python -m py_compile backend/mobility_simulator.py backend/simulation_validation.py data_preparation/run_model_validation.py && PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_validation_reports_hackathon_mapping_and_purpose_alignment tests/test_mobility_simulator.py::test_purpose_zone_alignment_gate_catches_bad_destination_mapping tests/test_mobility_simulator.py::test_strict_sample_evidence_breaks_insufficient_purpose_samples tests/test_mobility_simulator.py::test_weekly_itinerary_samples_misc_outing_purposes tests/test_mobility_simulator.py::test_charger_concentration_gate_catches_single_station_collapse -q`: 5 passed in 4.60s.
- `PYTHONPATH=backend uv run python -m py_compile backend/road_network.py backend/mobility_simulator.py && PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_weekly_trips_aggregate_to_road_edge_flows tests/test_mobility_simulator.py::test_edge_flows_are_bucketed_by_traversal_time_not_departure_only tests/test_mobility_simulator.py::test_road_network_reuses_edge_segment_expansion_cache tests/test_mobility_simulator.py::test_road_network_reuses_od_edge_template_cache -q`: 4 passed in 1.06s.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_edge_flows_are_bucketed_by_traversal_time_not_departure_only tests/test_mobility_simulator.py::test_road_network_reuses_edge_segment_expansion_cache tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 3 passed in 11.90s.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_fsa_corridor_flows_are_fast_calibration_proxy tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 2 passed in 12.82s.
- `PYTHONPATH=backend uv run pytest -q`: 79 passed in 232.69s; warnings are PuLP deprecations from optimizer tests.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_engine_reuses_static_grid_context_across_behavior_configs tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 2 passed in 8.84s.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py -q`: 34 passed in 305.90s.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_weekly_charging_recomputes_arrive_by_delay_after_charging tests/test_mobility_simulator.py::test_validation_reports_arrive_by_delay_tail tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 3 passed in 11.46s.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 1 passed.
- `PYTHONPATH=backend uv run python -m py_compile backend/model_calibration.py data_preparation/run_model_validation.py`: passed.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --observed-targets --num-people 120 --seeds 111 --fit --max-candidates 3 --fit-jobs 2`: passed, exercising the parallel candidate-fit CLI path.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_patch_softmax_temperature_changes_patch_choice_mix tests/test_mobility_simulator.py::test_calibration_extracts_and_scores_fit_metrics -q`: 2 passed in 10.33s.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --observed-targets --num-people 250 --seeds 111 --fit --max-candidates 2 --fit-jobs 2`: passed, exercising the widened patch-temperature fit grid and process-pool CLI path.
- `PYTHONPATH=backend uv run pytest -q`: 74 passed in 209.41s. Warnings are PuLP deprecations from optimizer tests.
- `PYTHONPATH=backend uv run pytest tests/test_road_grid_mapping.py tests/test_road_grid_dashboard.py tests/test_mobility_simulator.py -q`: 47 passed in 447.73s, including cached OSM/AFDC integration, dashboard adapter, real-route edge flows, hackathon-data mapping gates, purpose-zone alignment gates, and charger-concentration gates.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_validation_reports_hackathon_mapping_and_purpose_alignment tests/test_mobility_simulator.py::test_purpose_zone_alignment_gate_catches_bad_destination_mapping tests/test_mobility_simulator.py::test_charger_concentration_gate_catches_single_station_collapse -q`: 3 passed.
- `PYTHONPATH=backend uv run pytest tests/test_road_grid_dashboard.py tests/test_road_grid_mapping.py -q`: 11 passed, including the dashboard adapter over cached OSM/AFDC data.
- `PYTHONPATH=backend uv run python -m py_compile app.py backend/road_grid_dashboard.py`: passed.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --num-people 1500 --seeds 101`: all gates passed on OSM road graph and AFDC chargers.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_validation_options_can_require_real_grid_and_chargers tests/test_road_grid_mapping.py::test_real_validation_uses_real_public_charger_sources_and_osm_edges -q`: 2 passed.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_weekly_final_soc_balance_is_bounded tests/test_mobility_simulator.py::test_validation_can_check_repeated_week_stability -q`: 2 passed.
- `PYTHONPATH=backend uv run pytest tests/test_mobility_simulator.py::test_weekly_grid_load_scale_can_stress_capacity tests/test_road_grid_dashboard.py::test_weekly_dashboard_adapter_scales_ev_load_not_baseline -q`: 2 passed.
- `PYTHONPATH=backend uv run python data_preparation/run_model_validation.py --real-grid --observed-targets --repeat-week --num-people 500 --seeds 101 202 303 --sensitivity`: seed pass rate 100%, broken gate count 0.
