"""
Intraday activity-location route planner.

This module generates routed itinerary rows before charging is simulated. It is
deliberately independent from the SoC/charging model: it plans where people go
and when, then the existing weekly charging pass decides whether/where they
charge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from activity_poi_catalog import ActivityPOICatalog


ACTIVITIES = [
    "work",
    "school",
    "retail",
    "restaurant",
    "bar_nightlife",
    "leisure",
    "errand",
    "transit_hub",
    "other",
    "home",
]

ANCHOR_ACTIVITIES = {"work", "school"}
DISCRETIONARY_ACTIVITIES = ["retail", "restaurant", "bar_nightlife", "leisure", "errand", "transit_hub", "other"]

MIN_DWELL_H = {
    "home": 0.25,
    "work": 4.0,
    "school": 3.5,
    "retail": 0.25,
    "restaurant": 0.35,
    "bar_nightlife": 0.75,
    "leisure": 0.50,
    "errand": 0.15,
    "transit_hub": 0.08,
    "other": 0.25,
}

MEAN_DWELL_H = {
    "home": 2.5,
    "work": 7.5,
    "school": 6.0,
    "retail": 0.9,
    "restaurant": 1.2,
    "bar_nightlife": 2.2,
    "leisure": 2.4,
    "errand": 0.55,
    "transit_hub": 0.35,
    "other": 1.5,
}

MAX_DWELL_H = {
    "home": 14.0,
    "work": 11.0,
    "school": 8.5,
    "retail": 4.0,
    "restaurant": 2.75,
    "bar_nightlife": 5.5,
    "leisure": 6.0,
    "errand": 2.5,
    "transit_hub": 1.5,
    "other": 4.0,
}

TAU_KM = {
    "work": 24.0,
    "school": 7.0,
    "retail": 10.0,
    "restaurant": 9.0,
    "bar_nightlife": 16.0,
    "leisure": 16.0,
    "errand": 6.5,
    "transit_hub": 28.0,
    "other": 13.0,
    "home": 20.0,
}

SOFT_MAX_KM = {
    "work": 80.0,
    "school": 35.0,
    "retail": 45.0,
    "restaurant": 35.0,
    "bar_nightlife": 65.0,
    "leisure": 90.0,
    "errand": 25.0,
    "transit_hub": 120.0,
    "other": 60.0,
    "home": 80.0,
}

DESTINATION_ZONE_MULTIPLIER = {
    "work": {"residential": 0.55, "leisure": 0.85, "office_park": 3.2, "retail_hub": 2.6, "transit_hub": 2.0},
    "school": {"residential": 1.15, "leisure": 1.2, "office_park": 1.2, "retail_hub": 0.75, "transit_hub": 0.5},
    "retail": {"residential": 0.35, "leisure": 0.70, "office_park": 2.5, "retail_hub": 5.0, "transit_hub": 2.0},
    "restaurant": {"residential": 0.40, "leisure": 3.0, "office_park": 2.0, "retail_hub": 4.5, "transit_hub": 2.0},
    "bar_nightlife": {"residential": 0.25, "leisure": 4.0, "office_park": 0.8, "retail_hub": 4.0, "transit_hub": 2.5},
    "leisure": {"residential": 1.1, "leisure": 3.0, "office_park": 0.65, "retail_hub": 1.8, "transit_hub": 1.0},
    "errand": {"residential": 1.2, "leisure": 0.8, "office_park": 1.4, "retail_hub": 2.5, "transit_hub": 0.8},
    "transit_hub": {"residential": 0.05, "leisure": 0.5, "office_park": 3.0, "retail_hub": 4.0, "transit_hub": 20.0},
    "other": {"residential": 0.7, "leisure": 1.4, "office_park": 1.4, "retail_hub": 1.6, "transit_hub": 1.2},
}


@dataclass(frozen=True)
class ActivityCandidate:
    activity: str
    dest_idx: int
    depart_abs: float
    arrival_abs: float
    planned_arrival_abs: float
    schedule_delay_min: float
    route: object
    score: float


class IntradayActivityModel:
    def __init__(
        self,
        engine: object,
        activity_catalog: ActivityPOICatalog,
        *,
        activity_day_start_hour: float = 4.0,
        max_stops_per_activity_day: int = 6,
    ):
        self.engine = engine
        self.cfg = engine.config
        self.catalog = activity_catalog
        self.activity_day_start_hour = float(activity_day_start_hour)
        self.max_stops_per_activity_day = int(max_stops_per_activity_day)
        self.fsas = engine.fsas
        self.zone_types = engine.zone_types
        self.road_network = engine.road_network
        self.route_km = engine.route_km
        self._unit_zone_multiplier = np.ones(len(self.fsas), dtype=float)
        self._activity_vectors = {
            activity: self._normalize_activity_vector(activity_catalog.attraction_vector(activity))
            for activity in ACTIVITIES
            if activity != "home"
        }
        self._zone_multiplier_vectors = {
            activity: np.asarray(
                [float(weights.get(str(zone), 1.0)) for zone in self.zone_types],
                dtype=float,
            )
            for activity, weights in DESTINATION_ZONE_MULTIPLIER.items()
        }

    def generate_weekly_itinerary(self, people: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for person in people.to_dict("records"):
            rows.extend(self.plan_person_week(person, rng))
        itinerary = pd.DataFrame(rows)
        if itinerary.empty:
            from mobility_simulator import ITINERARY_COLUMNS

            return pd.DataFrame(columns=ITINERARY_COLUMNS)
        return itinerary.sort_values(["person_id", "depart_hour_abs"]).reset_index(drop=True)

    def _normalize_activity_vector(self, values: np.ndarray) -> np.ndarray:
        vector = np.maximum(np.asarray(values, dtype=float), 0.001)
        positive = vector[np.isfinite(vector) & (vector > 0)]
        scale = float(np.median(positive)) if len(positive) else 1.0
        if scale <= 0 or not np.isfinite(scale):
            scale = 1.0
        return vector / scale

    def plan_person_week(self, person: dict[str, object], rng: np.random.Generator) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for day in range(7):
            rows.extend(self.plan_person_activity_day(person, int(day), rng))
        return rows

    def plan_person_activity_day(self, person: dict[str, object], day: int, rng: np.random.Generator) -> list[dict[str, object]]:
        day_start = day * 24.0 + self.activity_day_start_hour
        day_end = min(day_start + 24.0, 168.0)
        if day_start >= 168.0 or day_end - day_start < 1.0:
            return []

        day_type = "weekday" if day < 5 else "weekend"
        person_type = str(person["person_type"])
        home_idx = int(person["home_idx"])
        work_idx = int(person.get("work_idx", -1))
        school_idx = int(person.get("school_idx", -1))
        workday_active = self._workday_active(person_type, day_type, work_idx, rng)
        schoolday_active = self._schoolday_active(person_type, day_type, school_idx, rng)
        discretionary_remaining = self._sample_discretionary_budget(
            rng,
            person_type=person_type,
            day=day,
            day_type=day_type,
            workday_active=workday_active,
            schoolday_active=schoolday_active,
        )

        rows: list[dict[str, object]] = []
        current_idx = home_idx
        current_activity = "home"
        current_time = day_start
        visited_work = False
        visited_school = False
        stops_today = 0

        while current_time < day_end - 0.25 and stops_today < self.max_stops_per_activity_day:
            candidates = self._candidate_set(
                person=person,
                rng=rng,
                day=day,
                day_type=day_type,
                current_idx=current_idx,
                current_activity=current_activity,
                current_time=current_time,
                day_end=day_end,
                workday_active=workday_active and not visited_work,
                schoolday_active=schoolday_active and not visited_school,
                discretionary_remaining=discretionary_remaining,
                work_idx=work_idx,
                school_idx=school_idx,
                home_idx=home_idx,
            )
            if current_idx == home_idx:
                stop_score = self._stop_at_home_score(person_type, day_type, current_time, day_start, day_end)
                if stop_score > 0:
                    candidates.append(ActivityCandidate(
                        activity="STOP_AT_HOME",
                        dest_idx=home_idx,
                        depart_abs=current_time,
                        arrival_abs=current_time,
                        planned_arrival_abs=np.nan,
                        schedule_delay_min=0.0,
                        route=self.road_network.route(home_idx, home_idx),
                        score=stop_score,
                    ))

            if not candidates:
                break
            candidate = self._choose_candidate(rng, candidates)
            if candidate.activity == "STOP_AT_HOME":
                break
            if candidate.activity == "home" and current_idx == home_idx:
                break

            rows.append(self._leg_row(person, day, day_type, current_idx, current_activity, candidate, current_time))
            current_idx = int(candidate.dest_idx)
            current_activity = candidate.activity
            current_time = float(candidate.arrival_abs)
            stops_today += 1
            visited_work = visited_work or current_activity == "work"
            visited_school = visited_school or current_activity == "school"
            if current_activity in DISCRETIONARY_ACTIVITIES:
                discretionary_remaining = max(0, discretionary_remaining - 1)

            home_clock = current_time % 24.0
            if current_activity == "home" and (current_time >= day_start + 16.0 or home_clock >= 20.0 or home_clock < self.activity_day_start_hour):
                if rng.random() < min(0.97, self._stop_at_home_score(person_type, day_type, current_time, day_start, day_end) / 3.0):
                    break

        if current_activity != "home" and current_time < 168.0:
            forced = self._forced_home_candidate(current_idx, home_idx, current_time, day_end, current_activity)
            if forced is not None:
                rows.append(self._leg_row(person, day, day_type, current_idx, current_activity, forced, current_time))

        return rows

    def _candidate_set(
        self,
        *,
        person: dict[str, object],
        rng: np.random.Generator,
        day: int,
        day_type: str,
        current_idx: int,
        current_activity: str,
        current_time: float,
        day_end: float,
        workday_active: bool,
        schoolday_active: bool,
        discretionary_remaining: int,
        work_idx: int,
        school_idx: int,
        home_idx: int,
    ) -> list[ActivityCandidate]:
        activities: list[str] = []
        if workday_active:
            activities.append("work")
        if schoolday_active:
            activities.append("school")
        if discretionary_remaining > 0:
            activities.extend(DISCRETIONARY_ACTIVITIES)
        if current_idx != home_idx:
            activities.append("home")

        candidates: list[ActivityCandidate] = []
        for activity in activities:
            if activity == current_activity and activity not in {"home", "work", "school"}:
                continue
            dest_idx = self._sample_destination_for_activity(
                rng,
                activity,
                current_idx=current_idx,
                home_idx=home_idx,
                work_idx=work_idx,
                school_idx=school_idx,
                clock_hour=int((current_time % 24)),
            )
            candidate = self._build_candidate(
                rng,
                activity=activity,
                dest_idx=dest_idx,
                current_idx=current_idx,
                current_activity=current_activity,
                current_time=current_time,
                day=day,
                day_type=day_type,
                person_type=str(person["person_type"]),
                day_end=day_end,
                home_idx=home_idx,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _build_candidate(
        self,
        rng: np.random.Generator,
        *,
        activity: str,
        dest_idx: int,
        current_idx: int,
        current_activity: str,
        current_time: float,
        day: int,
        day_type: str,
        person_type: str,
        day_end: float,
        home_idx: int,
    ) -> ActivityCandidate | None:
        route = self.road_network.route(current_idx, dest_idx)
        if not route.reachable:
            return None

        planned_arrival = np.nan
        if activity in ANCHOR_ACTIVITIES:
            target = self._sample_anchor_arrival_abs(rng, activity, day, day_type)
            if target <= current_time:
                return None
            first_depart = max(current_time + 0.25, target - self.road_network.travel_time_h(current_idx, dest_idx, target, route=route))
            duration = self.road_network.travel_time_h(current_idx, dest_idx, first_depart, route=route)
            depart_abs = max(current_time + 0.25, target - duration)
            duration = self.road_network.travel_time_h(current_idx, dest_idx, depart_abs, route=route)
            arrival_abs = depart_abs + duration
            planned_arrival = target
            schedule_delay = max(0.0, arrival_abs - target) * 60.0
        else:
            target_arrival = self._sample_flexible_arrival_abs(rng, activity, day, day_type, current_time, day_end)
            hold = self._sample_hold_before_departure(rng, current_activity)
            estimate = self.road_network.travel_time_h(current_idx, dest_idx, max(current_time, target_arrival), route=route)
            depart_abs = max(current_time + hold, target_arrival - estimate)
            if depart_abs < current_time + 0.25:
                depart_abs = current_time + 0.25
            duration = self.road_network.travel_time_h(current_idx, dest_idx, depart_abs, route=route)
            arrival_abs = depart_abs + duration
            schedule_delay = 0.0

        if depart_abs < current_time + 0.25 - 1e-9:
            return None
        if arrival_abs >= day_end:
            return None
        if activity != "home" and not self._can_return_home(activity, dest_idx, arrival_abs, home_idx, day_end):
            return None

        arrival_clock = arrival_abs % 24.0
        time_score = self._time_window_score(activity, arrival_clock, day, day_type, person_type)
        if time_score <= 1e-6:
            return None
        transition_score = self._transition_score(current_activity, activity, person_type, day_type)
        attraction_score = self._destination_attraction(activity, current_idx, dest_idx, arrival_clock)
        distance_score = self._route_distance_bias(current_idx, dest_idx, activity)
        loop_penalty = 0.25 if dest_idx == current_idx and activity != "home" else 1.0
        score = transition_score * time_score * attraction_score * distance_score * loop_penalty
        if score <= 0 or not np.isfinite(score):
            return None

        return ActivityCandidate(
            activity=activity,
            dest_idx=int(dest_idx),
            depart_abs=float(depart_abs),
            arrival_abs=float(arrival_abs),
            planned_arrival_abs=float(planned_arrival) if np.isfinite(planned_arrival) else np.nan,
            schedule_delay_min=float(schedule_delay),
            route=route,
            score=float(score),
        )

    def _sample_destination_for_activity(
        self,
        rng: np.random.Generator,
        activity: str,
        *,
        current_idx: int,
        home_idx: int,
        work_idx: int,
        school_idx: int,
        clock_hour: int,
    ) -> int:
        if activity == "home":
            return int(home_idx)
        if activity == "work":
            return int(work_idx)
        if activity == "school":
            return int(school_idx)

        vector = self._activity_vectors.get(activity)
        if vector is None:
            vector = np.ones(len(self.fsas), dtype=float)
        attraction = vector * self._traffic_attraction(activity, clock_hour) * self._zone_multiplier_vector(activity)
        route_km = self.route_km[int(current_idx)]
        distance = np.exp(-route_km / TAU_KM[activity])
        long_trip = np.exp(-np.maximum(route_km - SOFT_MAX_KM[activity], 0.0) / 8.0)
        probs = attraction * distance * long_trip
        probs[int(current_idx)] *= 0.20
        total = float(np.sum(probs))
        if total <= 0 or not np.isfinite(total):
            probs = np.ones(len(self.fsas), dtype=float) / len(self.fsas)
        else:
            probs = probs / total
        return int(rng.choice(np.arange(len(self.fsas), dtype=int), p=probs))

    def _can_return_home(self, activity: str, dest_idx: int, arrival_abs: float, home_idx: int, day_end: float) -> bool:
        return_depart = arrival_abs + MIN_DWELL_H.get(activity, 0.25)
        if return_depart >= day_end:
            return False
        route = self.road_network.route(dest_idx, home_idx)
        if not route.reachable:
            return False
        return_time = self.road_network.travel_time_h(dest_idx, home_idx, return_depart, route=route)
        return return_depart + return_time <= day_end

    def _forced_home_candidate(self, current_idx: int, home_idx: int, current_time: float, day_end: float, current_activity: str) -> ActivityCandidate | None:
        route = self.road_network.route(current_idx, home_idx)
        if not route.reachable:
            return None
        min_dwell = MIN_DWELL_H.get(current_activity, 0.25)
        depart_abs = current_time + min_dwell
        duration = self.road_network.travel_time_h(current_idx, home_idx, depart_abs, route=route)
        if depart_abs + duration > day_end:
            depart_abs = max(current_time, day_end - duration)
            duration = self.road_network.travel_time_h(current_idx, home_idx, depart_abs, route=route)
        if depart_abs + duration > 168.0:
            return None
        return ActivityCandidate(
            activity="home",
            dest_idx=home_idx,
            depart_abs=float(depart_abs),
            arrival_abs=float(depart_abs + duration),
            planned_arrival_abs=np.nan,
            schedule_delay_min=0.0,
            route=route,
            score=1.0,
        )

    def _leg_row(
        self,
        person: dict[str, object],
        day: int,
        day_type: str,
        current_idx: int,
        current_activity: str,
        candidate: ActivityCandidate,
        last_arrival_abs: float,
    ) -> dict[str, object]:
        route = candidate.route
        route_km = max(float(route.distance_km), 0.5 if int(current_idx) == int(candidate.dest_idx) else 0.001)
        cfg = self.cfg
        return {
            "person_id": str(person["person_id"]),
            "person_type": str(person["person_type"]),
            "is_ev": bool(person["is_ev"]),
            "day": int(day),
            "day_type": day_type,
            "origin_fsa": self.fsas[int(current_idx)],
            "origin_zone_type": self.zone_types[int(current_idx)],
            "origin_activity": current_activity,
            "dest_fsa": self.fsas[int(candidate.dest_idx)],
            "dest_zone_type": self.zone_types[int(candidate.dest_idx)],
            "dest_type": candidate.activity,
            "origin_idx": int(current_idx),
            "dest_idx": int(candidate.dest_idx),
            "depart_hour_abs": float(candidate.depart_abs),
            "arrival_hour_abs": float(candidate.arrival_abs),
            "planned_arrival_hour_abs": candidate.planned_arrival_abs,
            "schedule_delay_min": candidate.schedule_delay_min,
            "dwell_before_h": max(0.0, float(candidate.depart_abs) - float(last_arrival_abs)),
            "route_km": round(route_km, 2),
            "freeflow_time_h": round(float(route.freeflow_time_h), 3),
            "travel_time_h": round(float(candidate.arrival_abs - candidate.depart_abs), 3),
            "trip_kwh": round(route_km * float(cfg.ev_efficiency_kwh_per_km), 2),
            "route_path": "|".join(map(str, route.path)),
            "reachable_route": bool(route.reachable),
        }

    def _choose_candidate(self, rng: np.random.Generator, candidates: Iterable[ActivityCandidate]) -> ActivityCandidate:
        candidates = list(candidates)
        scores = np.asarray([max(candidate.score, 0.0) for candidate in candidates], dtype=float)
        if scores.sum() <= 0 or not np.isfinite(scores.sum()):
            return candidates[int(rng.integers(0, len(candidates)))]
        probs = scores / scores.sum()
        return candidates[int(rng.choice(np.arange(len(candidates), dtype=int), p=probs))]

    def _workday_active(self, person_type: str, day_type: str, work_idx: int, rng: np.random.Generator) -> bool:
        if person_type != "worker" or work_idx < 0:
            return False
        p = self.cfg.worker_weekday_work_probability if day_type == "weekday" else self.cfg.worker_weekend_work_probability
        return bool(rng.random() < p)

    def _schoolday_active(self, person_type: str, day_type: str, school_idx: int, rng: np.random.Generator) -> bool:
        if person_type != "student" or day_type != "weekday" or school_idx < 0:
            return False
        return bool(rng.random() < self.cfg.student_weekday_school_probability)

    def _sample_discretionary_budget(
        self,
        rng: np.random.Generator,
        *,
        person_type: str,
        day: int,
        day_type: str,
        workday_active: bool,
        schoolday_active: bool,
    ) -> int:
        if workday_active or schoolday_active:
            budget = 1 if rng.random() < self.cfg.after_work_stop_probability else 0
            if day in {4, 5} and rng.random() < 0.10:
                budget += 1
            return min(budget, 2)
        if day_type == "weekend":
            if rng.random() >= self.cfg.weekend_outing_probability:
                return 0
            return 2 if rng.random() < self.cfg.weekend_second_stop_probability else 1
        p = self.cfg.weekday_nonworker_outing_probability
        if person_type in {"worker", "student"}:
            p *= 0.45
        if rng.random() >= p:
            return 0
        return 1

    def _sample_anchor_arrival_abs(self, rng: np.random.Generator, activity: str, day: int, day_type: str) -> float:
        if activity == "work":
            if day_type == "weekday":
                hour = float(np.clip(rng.normal(8.85, 0.55), 6.5, 10.5))
            else:
                hour = float(np.clip(rng.normal(10.0, 1.1), 7.0, 14.0))
        else:
            hour = float(np.clip(rng.normal(8.25, 0.25), 7.5, 9.1))
        return day * 24.0 + hour

    def _sample_flexible_arrival_abs(
        self,
        rng: np.random.Generator,
        activity: str,
        day: int,
        day_type: str,
        current_time: float,
        day_end: float,
    ) -> float:
        for _ in range(8):
            hour = self._sample_arrival_clock(rng, activity, day, day_type)
            target = day * 24.0 + hour
            if hour < self.activity_day_start_hour:
                target += 24.0
            if current_time + 0.25 <= target < day_end:
                return float(target)
        return float(min(day_end - 0.5, current_time + max(0.25, rng.gamma(2.0, 0.8))))

    def _sample_arrival_clock(self, rng: np.random.Generator, activity: str, day: int, day_type: str) -> float:
        if activity == "home":
            center = 22.0 if day_type == "weekend" else 20.2
            return float(np.clip(rng.normal(center, 2.0), 12.0, 27.5))
        if activity == "retail":
            centers = [12.5, 17.8] if day_type == "weekday" else [11.5, 15.5]
            return float(np.clip(rng.normal(rng.choice(centers), 1.3), 9.5, 21.25))
        if activity == "restaurant":
            centers = [12.2, 18.8] if day_type == "weekday" else [12.0, 19.0]
            return float(np.clip(rng.normal(rng.choice(centers, p=[0.35, 0.65]), 0.9), 10.5, 22.25))
        if activity == "bar_nightlife":
            peak = 22.7 if day in {4, 5} else 21.5
            return float(np.clip(rng.normal(peak, 1.4), 19.0, 26.75))
        if activity == "leisure":
            centers = [17.8] if day_type == "weekday" else [11.0, 15.5, 19.0]
            return float(np.clip(rng.normal(rng.choice(centers), 1.6), 9.0, 22.5))
        if activity == "errand":
            centers = [10.5, 15.5] if day_type == "weekday" else [11.5, 14.5]
            return float(np.clip(rng.normal(rng.choice(centers), 1.0), 8.5, 18.75))
        if activity == "transit_hub":
            centers = [8.0, 17.2] if day_type == "weekday" else [11.0, 17.0]
            return float(np.clip(rng.normal(rng.choice(centers), 0.9), 6.25, 20.5))
        return float(np.clip(rng.normal(13.0 if day_type == "weekday" else 14.0, 2.5), 8.0, 21.0))

    def _sample_hold_before_departure(self, rng: np.random.Generator, current_activity: str) -> float:
        mean = MEAN_DWELL_H.get(current_activity, 1.0)
        minimum = MIN_DWELL_H.get(current_activity, 0.25)
        maximum = MAX_DWELL_H.get(current_activity, 4.0)
        if current_activity == "home":
            return float(np.clip(rng.gamma(2.0, mean / 2.0), minimum, maximum))
        return float(np.clip(rng.gamma(2.5, mean / 2.5), minimum, maximum))

    def _time_window_score(self, activity: str, arrival_clock: float, day: int, day_type: str, person_type: str) -> float:
        hour = arrival_clock
        if hour < self.activity_day_start_hour:
            hour += 24.0
        if activity == "work":
            if day_type == "weekday":
                score = _gaussian(hour, 8.85, 0.85) + 0.04 * _gaussian(hour, 21.5, 1.2)
            else:
                score = 0.18 * _gaussian(hour, 10.0, 1.5)
            return score * (1.0 if person_type == "worker" else 0.0)
        if activity == "school":
            return _gaussian(hour, 8.25, 0.4) * (1.0 if day_type == "weekday" and person_type == "student" else 0.0)
        if activity == "retail":
            return 0.70 * _gaussian(hour, 12.5, 2.2) + 0.90 * _gaussian(hour, 17.8, 1.9)
        if activity == "restaurant":
            return 0.55 * _gaussian(hour, 12.2, 0.9) + 1.00 * _gaussian(hour, 18.8, 1.35)
        if activity == "bar_nightlife":
            day_boost = 1.0 if day in {4, 5} else 0.35 if day == 3 else 0.10
            return day_boost * _gaussian(hour, 22.7, 1.9)
        if activity == "leisure":
            if day_type == "weekend":
                return 0.50 * _gaussian(hour, 11.0, 2.0) + 0.65 * _gaussian(hour, 15.5, 2.2) + 0.45 * _gaussian(hour, 19.0, 2.0)
            retired_boost = 0.25 if person_type in {"retired", "other"} else 0.0
            return retired_boost * _gaussian(hour, 12.5, 2.0) + 0.75 * _gaussian(hour, 18.0, 2.0)
        if activity == "errand":
            return 0.75 * _gaussian(hour, 10.5, 1.6) + 0.65 * _gaussian(hour, 15.5, 1.8)
        if activity == "transit_hub":
            if day_type == "weekday":
                return 0.8 * _gaussian(hour, 8.0, 1.0) + 0.7 * _gaussian(hour, 17.2, 1.2)
            return 0.25 * _gaussian(hour, 11.0, 1.7) + 0.35 * _gaussian(hour, 17.0, 2.0)
        if activity == "home":
            return 0.15 + 1.15 / (1.0 + np.exp(-(hour - (20.0 if day_type == "weekday" else 22.0))))
        return 0.15 + 0.45 * _gaussian(hour, 13.0, 4.0)

    def _transition_score(self, current_activity: str, next_activity: str, person_type: str, day_type: str) -> float:
        if next_activity == "home":
            return 2.2 if current_activity != "home" else 0.2
        if current_activity == "home":
            base = {
                "work": 5.0,
                "school": 4.5,
                "retail": 1.0,
                "restaurant": 0.65,
                "bar_nightlife": 0.20,
                "leisure": 0.85,
                "errand": 0.85,
                "transit_hub": 0.06,
                "other": 0.42,
            }
        elif current_activity in {"work", "school"}:
            base = {
                "retail": 1.0,
                "restaurant": 0.75,
                "bar_nightlife": 0.20,
                "leisure": 0.55,
                "errand": 0.35,
                "transit_hub": 0.045,
                "other": 0.30,
            }
        else:
            base = {
                "retail": 0.45,
                "restaurant": 0.55,
                "bar_nightlife": 0.35,
                "leisure": 0.45,
                "errand": 0.15,
                "transit_hub": 0.025,
                "other": 0.24,
            }
        value = float(base.get(next_activity, 0.0))
        if day_type == "weekend" and next_activity in {"leisure", "restaurant", "bar_nightlife", "retail"}:
            value *= 1.35
        if person_type == "retired" and next_activity in {"work", "school", "bar_nightlife"}:
            value *= 0.1
        if person_type == "retired" and next_activity in {"errand", "leisure", "retail"}:
            value *= 1.3
        return value

    def _destination_attraction(self, activity: str, current_idx: int, dest_idx: int, arrival_clock: float) -> float:
        if activity == "home":
            return 1.0
        vector = self._activity_vectors.get(activity)
        if vector is None:
            return 1.0
        zone_multiplier = self._zone_multiplier(activity, int(dest_idx))
        return float(max(vector[int(dest_idx)] * zone_multiplier, 0.001))

    def _zone_multiplier_vector(self, activity: str) -> np.ndarray:
        vector = self._zone_multiplier_vectors.get(activity)
        return self._unit_zone_multiplier if vector is None else vector

    def _zone_multiplier(self, activity: str, dest_idx: int) -> float:
        weights = DESTINATION_ZONE_MULTIPLIER.get(activity)
        if not weights:
            return 1.0
        return float(weights.get(str(self.zone_types[int(dest_idx)]), 1.0))

    def _traffic_attraction(self, activity: str, clock_hour: int) -> np.ndarray:
        if activity == "home":
            return np.ones(len(self.fsas), dtype=float)
        return self.engine._traffic_attraction_for(_legacy_activity(activity), int(clock_hour))

    def _route_distance_bias(self, current_idx: int, dest_idx: int, activity: str) -> float:
        km = float(self.route_km[int(current_idx), int(dest_idx)])
        tau = TAU_KM.get(activity, 13.0)
        soft = SOFT_MAX_KM.get(activity, 60.0)
        return float(np.exp(-km / tau) * np.exp(-max(km - soft, 0.0) / 8.0))

    def _stop_at_home_score(self, person_type: str, day_type: str, current_time: float, day_start: float, day_end: float) -> float:
        elapsed = current_time - day_start
        clock = current_time % 24.0
        if elapsed < 5.0 and person_type in {"worker", "student"}:
            return 0.03
        if elapsed < 5.0:
            return 0.35
        if current_time >= day_end - 3.0:
            return 10.0
        night_pull = 1.0 / (1.0 + np.exp(-((clock if clock >= 4 else clock + 24) - (20.0 if day_type == "weekday" else 22.0))))
        return float(0.10 + 2.4 * night_pull)


def _gaussian(x: float, center: float, sd: float) -> float:
    return float(np.exp(-0.5 * ((x - center) / sd) ** 2))


def _legacy_activity(activity: str) -> str:
    if activity in {"restaurant", "bar_nightlife", "errand"}:
        return "retail" if activity in {"restaurant", "errand"} else "leisure"
    return activity
