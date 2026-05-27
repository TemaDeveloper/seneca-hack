# Intraday Routing Distribution Update

## Objective

Replace the fixed daily trip templates with an intraday activity-location route planner for one individual. The planner must generate a feasible one-week route plan where trip count, timestamps, destination type, and destination place emerge from the same stochastic process.

The current model has useful randomness in destination FSA selection, but the daily shape is still template-driven. The new model should make time, feasibility, route length, activity type, and concrete location part of one sampling kernel.

## Core Decision

Use a semi-Markov activity-location process, not a plain Markov chain.

A plain Markov chain over activity labels is too weak because the next state is not just `work` or `retail`. It is:

```text
next = {
  activity_type,
  destination_fsa,
  optional_poi_id,
  departure_time,
  arrival_time,
  dwell_time,
  route,
}
```

The semi-Markov structure matters because each activity has a duration. The model samples the next event, routes to it, samples dwell, advances the clock, and repeats.

## Time Semantics

Do not terminate days at midnight. Use an activity-day boundary at 04:00.

Rationale: late restaurant/bar/leisure trips can naturally spill after midnight, and the share of people away from home after 04:00 is small enough to force home closure without materially affecting grid conclusions.

Definitions:

```text
service_day_start = 04:00
service_day_end   = next day 04:00
home_closure_deadline = service_day_end
```

The simulated week is:

```text
Monday 04:00 -> next Monday 04:00
```

Implementation detail:

- Internally use `hour_abs` in `[0, 168]`, where `0` means Monday 04:00.
- Use helper functions to convert to civil clock:

```text
clock_hour = (hour_abs + 4) % 24
service_day = floor(hour_abs / 24)
```

- Existing downstream charging can consume absolute timestamps unchanged.
- Any hourly reporting that claims civil hour should use the helper conversion, not raw `hour_abs % 24`.

Termination:

```text
while current_time < service_day_end:
  sample feasible next activity-location
  route
  dwell
  advance time

if current_activity != home:
  force return home before 04:00 if feasible, otherwise depart immediately toward home
```

## State

At each decision point:

```text
state_t = {
  person_id,
  person_type,
  service_day,
  day_of_week,
  day_type,
  current_time_abs,
  current_clock_hour,
  current_activity,
  current_fsa_idx,
  home_fsa_idx,
  work_fsa_idx?,
  school_fsa_idx?,
  workday_active,
  schoolday_active,
  stops_today,
  visited_work_today,
  visited_school_today,
  visited_activity_counts_today,
  recent_activity,
}
```

Charging state should not drive route planning in v1. Charging remains downstream and patches the route plan after the itinerary exists.

## Transition Kernel

At each step, sample `(activity_type, destination_fsa)` jointly:

```text
P(a, j | state)
  ∝ base_transition(current_activity, a, person_type, day_type)
  * time_window(a, arrival_clock_hour, day_of_week, person_type)
  * destination_attraction(a, j, arrival_clock_hour)
  * anchor_or_repeat_bias(a, j, person, state)
  * route_distance_bias(current_fsa_idx, j, a)
  * feasibility_mask(state, a, j)
```

Route-distance bias:

```text
route_distance_bias =
  exp(-route_km[current_fsa_idx, j] / tau_activity[a])
  * exp(-max(route_km[current_fsa_idx, j] - soft_max_km[a], 0) / 8)
```

Important: the origin for distance bias is the current FSA, not the home FSA. This makes chained trips plausible, for example `work -> restaurant -> bar -> home`.

## Activity Types

Use these activity types in the target model:

```text
home
work
school
retail
restaurant
bar_nightlife
leisure
errand
transit_hub
other
```

Compatibility step:

- The first code path may map `restaurant`, `bar_nightlife`, and `errand` back into existing broad categories until charger access, validation thresholds, and attraction caches are widened.
- The design target should still keep them separate because their time windows and dwell distributions differ materially.

## Anchors

Stable anchors:

- `home`: fixed per person.
- `work`: sampled once per worker.
- `school`: sampled once per student, and optionally for some worker/other profiles if needed.

Daily attendance is separate from anchor assignment:

```text
worker weekday workday_active  ~ Bernoulli(p_worker_weekday_work)
worker weekend workday_active  ~ Bernoulli(p_worker_weekend_work)
student weekday school_active  ~ Bernoulli(p_student_weekday_school)
```

If inactive, the corresponding work/school transition weight is zero.

Anchor constraints:

```text
if next_activity == home:
  destination_fsa = home_fsa

if next_activity == work:
  destination_fsa = work_fsa

if next_activity == school:
  destination_fsa = school_fsa
```

This preserves repeated work/school behavior while still allowing dynamic stop count and dynamic time.

## Time Windows

Use continuous curves, not AM/PM buckets. Each candidate should be scored by expected arrival clock time.

Initial v1 curves:

| Activity | Weekday arrival shape | Weekend arrival shape | Dwell distribution |
|---|---|---|---|
| `work` | main peak 08:45, support 06:30-10:30; small shift tail around 13:00 or 21:30 | low-probability shift work, peak 09:30-11:00 | gamma/lognormal mean 7.5h, clip 4-11h |
| `school` | sharp peak 08:15, support 07:30-09:10; tiny evening class tail | near-zero except college/special activity tail | mean 6h, clip 3.5-8.5h |
| `retail` | broad 10:00-21:00, peaks 12:30 and 17:45, strong decay after 21:00 | stronger 10:00-19:30, peaks 11:30 and 15:30 | median 0.8h, p90 about 2h, clip 0.15-4h |
| `restaurant` | lunch peak 12:15, dinner peak 18:45, Friday dinner later | brunch/lunch 11:30-13:30, dinner 18:00-20:30 | median 1.1h, clip 0.35-2.75h |
| `bar_nightlife` | low Sun-Wed, moderate Thu, high Fri; support 20:00-03:00, peak 22:30 | high Fri/Sat, low Sunday | median 2.2h, clip 0.75-5.5h |
| `leisure` | after-work peak 18:00; small midday mode for retired/nonworker | broad 09:30-22:30, peaks 11:00, 15:30, 19:00 | mean 2.4h, clip 0.5-6h |
| `errand` | 09:00-18:30, peaks 10:30 and 15:30 | 09:30-17:30, peaks 11:30 and 14:30 | median 0.45h, clip 0.15-2.5h |
| `transit_hub` | commute peaks 06:45-09:00 and 15:45-18:45 | flatter 09:30-12:00 and 16:00-20:00 | mixture: short 0.08-0.3h, park-and-ride 6-11h |
| `home` | always feasible; return weight rises after 16:00 and dominates after 20:00 | return weight rises later and dominates after 22:00 | terminal dwell until next activity-day |
| `other` | weak broad daytime 08:00-20:00, small evening tail | similar, slightly more midday | mean 1.5h, clip 0.25-4h |

Arrival-constrained activities:

- `work`
- `school`
- some `transit_hub` park-and-ride cases

For these:

```text
target_arrival = sample_arrival_window(activity)
depart_time = target_arrival - route_travel_time
```

Departure-constrained or flexible activities:

- `retail`
- `restaurant`
- `bar_nightlife`
- `leisure`
- `errand`
- `other`

For these:

```text
depart_time = current_time + sampled_hold_at_current_activity
arrival_time = depart_time + route_travel_time
```

## Feasibility Rules

Reject or heavily downweight candidates when:

- `depart_time < current_time + 0.25h`
- `arrival_time >= service_day_end`
- route is unreachable
- activity window density is effectively zero
- work/school is selected without an active anchor
- non-home activity cannot complete minimum dwell and still return home by 04:00
- route time is too large relative to short activities like errands
- a repeated low-value activity creates unrealistic loops

Minimum dwell for feasibility:

```text
work: 4.0h
school: 3.5h
retail: 0.25h
restaurant: 0.35h
bar_nightlife: 0.75h
leisure: 0.5h
errand: 0.15h
transit_hub: 0.08h
other: 0.25h
```

For any non-home candidate:

```text
return_depart = arrival_time + min_dwell[activity]
return_home_time = travel_time(destination_fsa, home_fsa, return_depart)
candidate_feasible = return_depart + return_home_time <= service_day_end
```

## Stop Condition

Include a sentinel candidate:

```text
STOP_AT_HOME
```

Stop the activity day when:

- current activity is `home` and `STOP_AT_HOME` is sampled;
- no non-home candidate is feasible;
- `stops_today >= max_stops_per_activity_day`;
- current time is close enough to 04:00 that only returning home is feasible.

Initial bound:

```text
max_stops_per_activity_day = 6
```

This is a computational and sanity guard, not a behavioral claim.

## Destination Resolution

Destination is both type and place.

The target hierarchy:

```text
activity_type -> destination_fsa -> optional concrete poi_id / road_node_id
```

For v1, route FSA-to-FSA for speed and reuse current route caches. Do not route arbitrary POI-to-POI pairs yet because route-cache size will explode.

Within the selected FSA, optionally sample a concrete POI for metadata and later node-level routing.

Destination FSA attraction:

```text
destination_attraction(a, j, hour)
  = poi_activity_weight(a, j)
  * zone_attraction(a, zone_type[j])
  * traffic_attraction(j, hour)
  * optional_population_or_employment_weight(a, j)
```

Fallback if no POI layer exists:

```text
destination_attraction = current zone attraction * traffic attraction
```

## Additional Data To Source

The current repo is not enough for a defensible concrete-place model. It can support a coarse FSA-level mock, but concrete destination behavior needs external POI and land-use data.

Required new data:

| Data | Purpose | Preferred source | Fallback |
|---|---|---|---|
| OSM POIs | GTA-wide activity attractions | OpenStreetMap via OSMnx/Overpass or downloaded extract | current FSA zone weights |
| OSM land-use polygons | retail/commercial/industrial/leisure area weights | OpenStreetMap landuse/building tags | broad FSA zone classification |
| schools | school anchors and attraction | municipal open data, Ontario school lists, OSM `amenity=school/college/university` | OSM only |
| employment/commercial clusters | work anchors | municipal employment areas, zoning, business improvement areas, OSM office/commercial/industrial tags | FSA zone type + traffic counts |
| retail centers | retail attraction | municipal shopping/plaza data where available, OSM `shop=*`, `landuse=retail`, malls | OSM POI count |
| restaurants/cafes | restaurant timing and attraction | OSM `amenity=restaurant/cafe/fast_food/food_court` | merged retail/leisure |
| bars/nightlife | late-night activity | OSM `amenity=bar/pub/nightclub` | leisure tail assumption |
| parks/recreation/attractions | leisure activity | municipal parks/recreation/culture open data, OSM `leisure=*`, `tourism=*` | FSA leisure zone |
| errands/services | short daytime stops | OSM `pharmacy/doctors/clinic/hospital/bank/post_office/townhall` | `other` |
| transit hubs | commute transfer / park-and-ride | transit agency station datasets, OSM stations | filtered OSM stations |
| parking/access modifiers | charging and destination access modifier | municipal parking, Green P, mall lots, station parking | ignore in route planner v1 |
| trip-purpose/time survey | fit activity windows and transitions | Transportation Tomorrow Survey or equivalent public summaries | documented assumptions |
| observed speeds/travel times | fit time-of-day route multipliers | municipal traffic speed feeds if available | existing static multipliers |

POI tag mapping:

```text
work:
  office=*, building=office/commercial/industrial, landuse=commercial/industrial

school:
  amenity=school, kindergarten, college, university

retail:
  shop=*, landuse=retail, building=retail, amenity=marketplace

restaurant:
  amenity=restaurant, cafe, fast_food, food_court

bar_nightlife:
  amenity=bar, pub, nightclub

leisure:
  leisure=*, tourism=attraction/museum/gallery, amenity=cinema/theatre/library/community_centre

errand:
  amenity=pharmacy, doctors, clinic, hospital, dentist, bank, post_office, townhall, courthouse

transit_hub:
  public_transport=station, railway=station, amenity=bus_station, aeroway=aerodrome
```

Filter ordinary bus stops out of `transit_hub` unless they are major terminals or park-and-ride locations.

## POI Cache Schema

Add a normalized POI cache:

```text
activity_pois.csv
```

Columns:

```text
poi_id
source
source_id
source_layer
name
raw_tags_json
activity_type
activity_subtype
lat
lon
geometry_wkt
area_m2
weight
capacity_proxy
confidence
fsa
fsa_idx
zone_type
road_node_id
road_snap_distance_m
snap_status
dedupe_key
source_fingerprint
graph_fingerprint
mapping_version
```

Add FSA-level activity attraction cache:

```text
activity_fsa_attractions.csv
```

Columns:

```text
activity_type
fsa
fsa_idx
zone_type
poi_count
weighted_poi_count
population_weight
employment_weight
traffic_weight
attraction_weight
source_mix_json
confidence
```

Add optional road-node attraction cache:

```text
activity_node_attractions.csv
```

Columns:

```text
activity_type
fsa_idx
road_node_id
lat
lon
weighted_poi_count
representative_poi_id
snap_distance_m
```

Add cache coverage metadata:

```text
activity_poi_metadata.json
```

Required fields:

```text
cache_schema_version
mapping_version
source
complete
fsa_count
fetch_fsa_count
limit_fsas
chunk_size
poi_count
fsa_attraction_rows
node_attraction_rows
activity_types
graph_fingerprint
road_graph_source
written_utc
```

`auto` and `cache` mode may only load the POI cache when this metadata says the cache is complete, has no `limit_fsas`, matches the current FSA count, and matches the current mapping/cache schema versions. Limited smoke fetches may write CSVs, but must not be treated as full GTA POI evidence.

## POI Processing Pipeline

1. Fetch OSM POIs and land-use polygons for GTA FSA envelope.
2. Optionally merge municipal open datasets by category.
3. Normalize all geometries to EPSG:4326.
4. Convert polygons to representative points while retaining area for weighting.
5. Spatial-join POIs to FSA polygons.
6. Assign nearest FSA for boundary misses only within a tight distance threshold; otherwise flag/drop.
7. Deduplicate municipal and OSM records by category, normalized name, and distance.
8. Snap POIs to nearest OSM drive graph node.
9. Record snap distance and graph fingerprint.
10. Aggregate to FSA attraction by activity.
11. Save caches and expose loader used by the intraday planner.

Weighting:

- raw POI counts are insufficient;
- polygons should use area;
- schools may use enrollment if available, otherwise category weight;
- hospitals/clinics may use subtype/capacity proxy;
- transit should use station class, not raw stop count;
- work should prefer employment/commercial area over individual office POI count.

## Fit Strategy

Fit what current data can support, and mark the rest as assumptions.

Can fit or calibrate from current repo:

| Parameter | Data | Method | Confidence |
|---|---|---|---|
| home FSA weights | population scaling / FSA boundaries | normalize population/home weight | high |
| route km and route feasibility | OSM graph and FSA route cache | direct route matrix and reachability gates | high |
| base route time | OSM route freeflow and current route matrix | direct route time | medium |
| traffic attraction | Toronto traffic FSA counts / zone weights | tune attraction exponent to AM/PM FSA/zone exposure | medium-low |
| zone attraction | FSA zone classification and validation metrics | calibrate against purpose-zone alignment | medium-low |
| distance decay | route km and weekly-km targets | grid-search `tau` and soft caps | low-medium |
| stop count / active days | generated itinerary and sanity targets | calibrate to target bands | medium-low |
| charger-stop geography | AFDC charger catalog and charge events | validate public charge zone mix and detours | medium |

Cannot honestly fit from current repo alone:

| Parameter | Status |
|---|---|
| true activity transition matrix | assumption until trip-diary data exists |
| true time-window curves | assumption until trip-purpose/hour data exists |
| dwell distributions | assumption until parking/activity dwell data exists |
| restaurant/bar/errand concrete attractiveness | requires POI layer |
| true OD matrix | not present |
| real time-of-day speed curves | not present |
| shift-worker timing tails | assumption unless external labor/trip data found |

Every assumed parameter must be documented in `docs/model_assumptions.md` with source, argument, and validation result before becoming default behavior.

## Implementation Plan

### Module Boundaries

Add:

```text
backend/intraday_activity_model.py
backend/activity_poi_catalog.py
data_preparation/fetch_activity_pois.py
tests/test_intraday_activity_model.py
tests/test_activity_poi_catalog.py
```

Update:

```text
backend/mobility_simulator.py
backend/road_grid_dashboard.py
data_preparation/run_model_validation.py
data_preparation/benchmark_simulation_scale.py
docs/model_assumptions.md
```

### Config

Add to `MobilityConfig`:

```text
itinerary_model: "template" | "intraday"
activity_poi_source: "auto" | "osm" | "cache" | "none"
force_activity_poi_download: bool
activity_day_start_hour: 4
max_stops_per_activity_day: 6
```

Default should remain current behavior until validation passes:

```text
itinerary_model = "template"
```

After validation, switch road-grid workflows to:

```text
itinerary_model = "intraday"
```

### Planner API

`IntradayActivityModel` should produce the same itinerary schema currently consumed by charging:

```text
person_id
person_type
is_ev
day
day_type
origin_fsa
origin_zone_type
origin_activity
dest_fsa
dest_zone_type
dest_type
origin_idx
dest_idx
depart_hour_abs
arrival_hour_abs
planned_arrival_hour_abs
schedule_delay_min
dwell_before_h
route_km
freeflow_time_h
travel_time_h
trip_kwh
route_path
reachable_route
```

Optional future columns, if added, must not break downstream consumers:

```text
service_day
clock_depart_hour
clock_arrival_hour
dest_poi_id
dest_road_node_id
activity_window_score
destination_attraction_score
```

### Individual Planning Algorithm

For each person and service day:

1. Start at home at service-day start.
2. Sample daily work/school attendance flags.
3. Build candidate activity set.
4. For each candidate activity:
   - resolve destination FSA or anchor;
   - estimate route and arrival time;
   - compute transition score;
   - apply feasibility mask.
5. Sample one candidate from normalized scores.
6. Append routed leg.
7. Sample dwell for destination activity.
8. Advance time.
9. Repeat until stop condition.
10. Force home return by 04:00 if away from home.

Candidate score:

```text
score =
  base_transition
  * time_window_score
  * destination_attraction
  * anchor_or_repeat_bias
  * route_distance_bias
  * feasibility
```

If all non-home candidates are infeasible:

```text
return home if away from home
stop if already home
```

### Performance Constraints

- Use FSA-to-FSA route cache for v1.
- Do not compute arbitrary POI-to-POI shortest paths.
- Precompute destination probability matrices by activity/hour where possible.
- Bound candidate FSAs by top attraction candidates plus local neighborhood candidates if full sampling is too slow.
- Collect rows into lists of dicts; avoid DataFrame mutation inside person loops.
- Keep batched high-scale path compatible by dispatching inside `generate_weekly_itinerary`.

## Validation Gates

Pre-charging itinerary gates:

| Gate | Target |
|---|---|
| schema validity | all expected itinerary columns present |
| time bounds | `0 <= depart <= arrival <= 168` |
| duration consistency | `abs((arrival - depart) - travel_time_h) <= 0.02h` |
| continuity | next origin equals previous destination per person |
| no overlap | each depart is after previous arrival |
| nonnegative dwell | `dwell_before_h >= 0` |
| home closure | active service days ending home >= 99.5% |
| reachable routes | unreachable OD pairs = 0 after filtering |
| work/school timing | work/school arrivals concentrated in morning windows |
| late-night feasibility | bar/nightlife returns before 04:00 |
| anchor stickiness | >= 90% of workers/students use one work/school FSA |
| route km | p50 about 5-25 km, p90 about 40-90 km |
| weekly km | median about 150-350 km |
| legs/person-week | median about 10-16 |
| active days | workers/students > retired/other |

Observed-proxy gates:

| Gate | Method |
|---|---|
| home distribution | compare generated home FSA distribution to population weights |
| AM/PM traffic exposure | compare route exposure to Toronto traffic FSA counts |
| zone fit | compare endpoint/route zone distribution to `zone_weights.json` |
| purpose-zone alignment | work to office/retail/transit; retail to retail hubs; home to residential |
| edge/corridor conservation | summed edge/corridor km equals summed itinerary route km |

Broken-model signals:

- empty or near-empty itinerary;
- workers mostly staying home on weekdays;
- most agents following identical paths;
- non-home late-night trips dominating ordinary weekdays;
- work/school after evening without shift-worker flag;
- retail after 21:00 too frequent;
- one or two FSAs absorbing most destinations;
- route km or weekly km exploding;
- top 1% of road/corridor buckets carrying implausibly high flow;
- downstream charging patch rate spikes because mobility is creating impossible energy demand.

## Relationship To Charging

Charging remains downstream.

The route planner should not decide charge events. It should produce a feasible human itinerary. Then the existing SoC model:

- simulates energy consumption;
- opportunistically charges at dwell locations;
- patches charging when reserve would be violated;
- aggregates load to grid.

This separation is important. If charging starts shaping the route planner too early, route behavior becomes hard to validate independently.

## V1 Scope

V1 is complete when:

1. POI acquisition/cache exists or there is an explicit `activity_poi_source="none"` fallback.
2. `IntradayActivityModel` can generate one person's week and a population week.
3. It preserves the current itinerary schema.
4. It supports the 04:00 activity-day termination rule.
5. It samples dynamic stop count from feasibility and transition scores.
6. It uses route-distance bias from the current FSA, not only home.
7. It keeps work/school/home anchors sticky.
8. It passes pre-charging itinerary sanity checks.
9. It passes downstream charging/grid tests without changing the charging model.
10. Every assumed parameter is recorded in `docs/model_assumptions.md`.

## Current Implementation Status

- Implemented `backend/intraday_activity_model.py` as an opt-in `MobilityConfig.itinerary_model="intraday"` planner.
- Implemented `backend/activity_poi_catalog.py` and `data_preparation/fetch_activity_pois.py` for OSM POI acquisition, FSA/node attraction caches, deterministic zone-proxy fallback, and cache coverage metadata.
- Limited OSM smoke fetch is working, but the current local POI cache is marked `complete: false` because it only covers `1/260` FSAs. `activity_poi_source="auto"` therefore ignores it and uses zone-proxy attractions until the full GTA POI fetch completes.
- Charging simulation now preserves the activity-day label through SoC/charging delays, so after-midnight returns still close the correct activity day while road/load aggregation continues to use absolute timestamps.
- Real-grid intraday smoke validation passed: `PYTHONPATH=backend .venv/bin/python data_preparation/run_model_validation.py --real-grid --itinerary-model intraday --activity-poi-source auto --observed-targets --repeat-week --num-people 300 --seeds 101 202 303 --validation-jobs 1 --out-dir backend/data/validation/intraday_real_grid_smoke_after_activity_metadata` produced seed pass rate `100%` and broken gate count `0`.

## Non-Goals For V1

- No live traffic assignment.
- No POI-to-POI routing at full scale.
- No overnight away-from-home population.
- No charger-driven route choice inside the route planner.
- No claim that transition/time-window parameters are empirically fitted unless an external trip-purpose/time dataset is actually added.
