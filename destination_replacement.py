"""Replace selected commercial destinations while preserving trip-chain continuity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from commercial_profiles import distance_band, time_band
from trip_chains import haversine_m


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "output" / "trip_chains" / "nagoya_trip_chains.sqlite3"
DEFAULT_CANDIDATES = (
    BASE_DIR / "output" / "potential_visitors" / "midland_square_50m" / "candidates.csv"
)
DEFAULT_POTENTIAL_SUMMARY = (
    BASE_DIR / "output" / "potential_visitors" / "midland_square_50m" / "summary.json"
)
DEFAULT_SCENARIO = (
    BASE_DIR
    / "config"
    / "development_scenarios"
    / "meitetsu_nagoya_commercial_demo.json"
)
DEFAULT_OUTPUT_DIR = (
    BASE_DIR / "output" / "destination_replacement" / "meitetsu_nagoya_commercial_demo"
)

SELECTED_VISIT_COLUMNS = (
    "person_id",
    "incoming_trip_id",
    "outgoing_trip_id",
    "preference_score",
    "trip_purpose",
    "employment_status",
    "departure_time_sec",
    "time_band",
    "previous_trip_purpose",
    "next_trip_purpose",
    "transport_mode",
    "original_destination_lon",
    "original_destination_lat",
    "new_destination_lon",
    "new_destination_lat",
    "incoming_distance_before_km",
    "incoming_distance_after_km",
    "incoming_distance_band_before",
    "incoming_distance_band_after",
    "outgoing_original_origin_lon",
    "outgoing_original_origin_lat",
    "outgoing_new_origin_lon",
    "outgoing_new_origin_lat",
    "outgoing_destination_lon",
    "outgoing_destination_lat",
    "outgoing_distance_before_km",
    "outgoing_distance_after_km",
    "chain_gap_before_m",
    "chain_gap_after_m",
)
COORDINATE_CHANGE_COLUMNS = (
    "person_id",
    "trip_id",
    "coordinate_role",
    "original_lon",
    "original_lat",
    "new_lon",
    "new_lat",
)


def deterministic_weighted_sample(
    rows: Iterable[dict[str, Any]],
    sample_size: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Return a reproducible weighted sample without replacement."""

    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    seen_people: set[str] = set()
    for row in rows:
        person_id = str(row["person_id"])
        if person_id in seen_people:
            raise ValueError(f"Duplicate person candidate: {person_id}")
        seen_people.add(person_id)
        weight = float(row["preference_score"])
        if weight <= 0:
            raise ValueError("preference_score must be positive")
        digest = hashlib.sha256(f"{seed}:{person_id}".encode("utf-8")).digest()
        uniform = (int.from_bytes(digest[:8], "big") + 1) / (2**64 + 1)
        priority = -math.log(uniform) / weight
        ranked.append((priority, person_id, row))
    if sample_size > len(ranked):
        raise ValueError(
            f"Requested {sample_size} visitors but only {len(ranked)} candidates are eligible"
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked[:sample_size]]


def _load_candidate_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _fetch_trip_context(
    connection: sqlite3.Connection,
    trip_id: int,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT
            current.trip_id,
            current.person_id,
            current.departure_time_sec,
            current.origin_lon,
            current.origin_lat,
            current.destination_lon,
            current.destination_lat,
            current.transport_mode,
            current.trip_purpose,
            current.employment_status,
            chain.analysis_previous_trip_purpose,
            chain.analysis_next_trip_id,
            chain.analysis_next_trip_purpose
        FROM trips AS current
        JOIN analysis_trip_chain_rows AS chain ON chain.trip_id = current.trip_id
        WHERE current.trip_id = ? AND chain.analysis_eligible = 1
        """,
        (trip_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Eligible trip not found: {trip_id}")
    return row


def _fetch_outgoing_trip(
    connection: sqlite3.Connection,
    trip_id: int | None,
) -> sqlite3.Row | None:
    if trip_id is None:
        return None
    return connection.execute(
        """
        SELECT
            trip_id, departure_time_sec,
            origin_lon, origin_lat, destination_lon, destination_lat,
            transport_mode, trip_purpose
        FROM trips WHERE trip_id = ?
        """,
        (trip_id,),
    ).fetchone()


def _distribution_rows(
    selected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dimensions = {
        "trip_purpose": ("trip_purpose", "trip_purpose"),
        "employment_status": ("employment_status", "employment_status"),
        "time_band": ("time_band", "time_band"),
        "transport_mode": ("transport_mode", "transport_mode"),
        "previous_trip_purpose": (
            "previous_trip_purpose",
            "previous_trip_purpose",
        ),
        "next_trip_purpose": ("next_trip_purpose", "next_trip_purpose"),
        "incoming_distance_band": (
            "incoming_distance_band_before",
            "incoming_distance_band_after",
        ),
    }
    result: list[dict[str, Any]] = []
    for dimension, (before_key, after_key) in dimensions.items():
        before = Counter(str(row[before_key]) for row in selected_rows)
        after = Counter(str(row[after_key]) for row in selected_rows)
        for category in sorted(set(before) | set(after)):
            result.append(
                {
                    "dimension": dimension,
                    "category": category,
                    "before_count": before[category],
                    "after_count": after[category],
                }
            )
    return result


def build_destination_replacement(
    database_path: Path,
    candidates_path: Path,
    potential_summary_path: Path,
    scenario_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Select visitors, create coordinate deltas, and validate changed chains."""

    database_path = Path(database_path)
    candidates_path = Path(candidates_path)
    potential_summary_path = Path(potential_summary_path)
    scenario_path = Path(scenario_path)
    for path in (
        database_path,
        candidates_path,
        potential_summary_path,
        scenario_path,
    ):
        if not path.exists():
            raise FileNotFoundError(path)

    potential_summary = json.loads(potential_summary_path.read_text(encoding="utf-8"))
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    target = scenario["target"]
    target_lon = float(target["longitude"])
    target_lat = float(target["latitude"])
    exclusion_radius_m = float(target["exclude_existing_destination_radius_m"])
    eligible_purposes = {str(code) for code in scenario["eligible_trip_purpose_codes"]}
    configured_target_visitor_count = scenario.get("target_visitor_count")

    candidates = _load_candidate_rows(candidates_path)
    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    eligible: list[dict[str, Any]] = []
    excluded_purpose = 0
    excluded_near_target = 0
    contexts: dict[int, sqlite3.Row] = {}
    for candidate in candidates:
        purpose_code = str(candidate["trip_purpose"]).split(":", 1)[0]
        if purpose_code not in eligible_purposes:
            excluded_purpose += 1
            continue
        trip_id = int(candidate["trip_id"])
        context = _fetch_trip_context(connection, trip_id)
        if (
            haversine_m(
                context["destination_lon"],
                context["destination_lat"],
                target_lon,
                target_lat,
            )
            <= exclusion_radius_m
        ):
            excluded_near_target += 1
            continue
        contexts[trip_id] = context
        eligible.append(candidate)

    eligible_expected_visitors = sum(
        float(candidate["estimated_visit_probability"])
        for candidate in eligible
    )
    target_visitor_count = (
        round(eligible_expected_visitors)
        if configured_target_visitor_count is None
        else int(configured_target_visitor_count)
    )
    if target_visitor_count <= 0:
        raise ValueError("target_visitor_count must be positive")

    selected = deterministic_weighted_sample(
        eligible,
        target_visitor_count,
        int(scenario["random_seed"]),
    )
    selected_rows: list[dict[str, Any]] = []
    coordinate_change_rows: list[dict[str, Any]] = []
    with_outgoing_trip = 0
    time_order_violations = 0
    post_change_continuity_violations = 0
    incoming_distance_before_total = 0.0
    incoming_distance_after_total = 0.0
    outgoing_distance_before_total = 0.0
    outgoing_distance_after_total = 0.0

    for candidate in selected:
        trip_id = int(candidate["trip_id"])
        current = contexts[trip_id]
        outgoing_trip_id = current["analysis_next_trip_id"]
        outgoing = _fetch_outgoing_trip(connection, outgoing_trip_id)
        incoming_before_km = haversine_m(
            current["origin_lon"],
            current["origin_lat"],
            current["destination_lon"],
            current["destination_lat"],
        ) / 1000.0
        incoming_after_km = haversine_m(
            current["origin_lon"], current["origin_lat"], target_lon, target_lat
        ) / 1000.0
        incoming_distance_before_total += incoming_before_km
        incoming_distance_after_total += incoming_after_km

        outgoing_before_km: float | None = None
        outgoing_after_km: float | None = None
        chain_gap_before_m: float | None = None
        chain_gap_after_m: float | None = None
        if outgoing is not None:
            with_outgoing_trip += 1
            if outgoing["departure_time_sec"] < current["departure_time_sec"]:
                time_order_violations += 1
            outgoing_before_km = haversine_m(
                outgoing["origin_lon"],
                outgoing["origin_lat"],
                outgoing["destination_lon"],
                outgoing["destination_lat"],
            ) / 1000.0
            outgoing_after_km = haversine_m(
                target_lon,
                target_lat,
                outgoing["destination_lon"],
                outgoing["destination_lat"],
            ) / 1000.0
            outgoing_distance_before_total += outgoing_before_km
            outgoing_distance_after_total += outgoing_after_km
            chain_gap_before_m = haversine_m(
                current["destination_lon"],
                current["destination_lat"],
                outgoing["origin_lon"],
                outgoing["origin_lat"],
            )
            chain_gap_after_m = haversine_m(
                target_lon, target_lat, target_lon, target_lat
            )
            if chain_gap_after_m > 0.001:
                post_change_continuity_violations += 1

        selected_rows.append(
            {
                "person_id": current["person_id"],
                "incoming_trip_id": trip_id,
                "outgoing_trip_id": outgoing_trip_id,
                "preference_score": float(candidate["preference_score"]),
                "trip_purpose": current["trip_purpose"],
                "employment_status": current["employment_status"],
                "departure_time_sec": current["departure_time_sec"],
                "time_band": time_band(current["departure_time_sec"]),
                "previous_trip_purpose": current["analysis_previous_trip_purpose"],
                "next_trip_purpose": current["analysis_next_trip_purpose"],
                "transport_mode": current["transport_mode"],
                "original_destination_lon": current["destination_lon"],
                "original_destination_lat": current["destination_lat"],
                "new_destination_lon": target_lon,
                "new_destination_lat": target_lat,
                "incoming_distance_before_km": incoming_before_km,
                "incoming_distance_after_km": incoming_after_km,
                "incoming_distance_band_before": distance_band(incoming_before_km),
                "incoming_distance_band_after": distance_band(incoming_after_km),
                "outgoing_original_origin_lon": (
                    outgoing["origin_lon"] if outgoing is not None else None
                ),
                "outgoing_original_origin_lat": (
                    outgoing["origin_lat"] if outgoing is not None else None
                ),
                "outgoing_new_origin_lon": target_lon if outgoing is not None else None,
                "outgoing_new_origin_lat": target_lat if outgoing is not None else None,
                "outgoing_destination_lon": (
                    outgoing["destination_lon"] if outgoing is not None else None
                ),
                "outgoing_destination_lat": (
                    outgoing["destination_lat"] if outgoing is not None else None
                ),
                "outgoing_distance_before_km": outgoing_before_km,
                "outgoing_distance_after_km": outgoing_after_km,
                "chain_gap_before_m": chain_gap_before_m,
                "chain_gap_after_m": chain_gap_after_m,
            }
        )
        coordinate_change_rows.append(
            {
                "person_id": current["person_id"],
                "trip_id": trip_id,
                "coordinate_role": "destination",
                "original_lon": current["destination_lon"],
                "original_lat": current["destination_lat"],
                "new_lon": target_lon,
                "new_lat": target_lat,
            }
        )
        if outgoing is not None:
            coordinate_change_rows.append(
                {
                    "person_id": current["person_id"],
                    "trip_id": outgoing_trip_id,
                    "coordinate_role": "origin",
                    "original_lon": outgoing["origin_lon"],
                    "original_lat": outgoing["origin_lat"],
                    "new_lon": target_lon,
                    "new_lat": target_lat,
                }
            )
    connection.close()

    selected_persons = {row["person_id"] for row in selected_rows}
    selected_trip_ids = {row["incoming_trip_id"] for row in selected_rows}
    if len(selected_persons) != len(selected_rows):
        raise AssertionError("More than one visit was selected for a person")
    if len(selected_trip_ids) != len(selected_rows):
        raise AssertionError("Duplicate incoming trip was selected")

    purpose_counts = Counter(str(row["trip_purpose"]) for row in selected_rows)
    coordinate_change_count = len(selected_rows) + with_outgoing_trip
    summary: dict[str, Any] = {
        "scenario": scenario,
        "selection": {
            "input_high_preference_candidates": len(candidates),
            "input_high_preference_expected_visitors": float(
                potential_summary["counts"]["estimated_high_preference_visitors"]
            ),
            "excluded_non_explicit_commercial_purpose": excluded_purpose,
            "excluded_already_near_target": excluded_near_target,
            "eligible_candidates": len(eligible),
            "eligible_candidate_expected_visitors": eligible_expected_visitors,
            "target_visitors": target_visitor_count,
            "selected_visitors": len(selected_rows),
            "selected_purpose_counts": dict(sorted(purpose_counts.items())),
        },
        "changes": {
            "incoming_destinations_changed": len(selected_rows),
            "outgoing_origins_changed": with_outgoing_trip,
            "coordinate_change_records": coordinate_change_count,
            "departure_times_changed": 0,
            "transport_modes_changed": 0,
            "trip_purposes_changed": 0,
        },
        "chain_validation": {
            "selected_persons_unique": len(selected_persons) == len(selected_rows),
            "selected_incoming_trips_unique": len(selected_trip_ids) == len(selected_rows),
            "time_order_violations": time_order_violations,
            "post_change_continuity_violations": post_change_continuity_violations,
            "maximum_post_change_gap_m": max(
                (
                    float(row["chain_gap_after_m"])
                    for row in selected_rows
                    if row["chain_gap_after_m"] is not None
                ),
                default=None,
            ),
        },
        "distance_km": {
            "incoming_before_total": incoming_distance_before_total,
            "incoming_after_total": incoming_distance_after_total,
            "outgoing_before_total": outgoing_distance_before_total,
            "outgoing_after_total": outgoing_distance_after_total,
            "changed_legs_before_total": (
                incoming_distance_before_total + outgoing_distance_before_total
            ),
            "changed_legs_after_total": (
                incoming_distance_after_total + outgoing_distance_after_total
            ),
            "incoming_before_mean": incoming_distance_before_total / len(selected_rows),
            "incoming_after_mean": incoming_distance_after_total / len(selected_rows),
            "outgoing_before_mean": (
                outgoing_distance_before_total / with_outgoing_trip
                if with_outgoing_trip
                else None
            ),
            "outgoing_after_mean": (
                outgoing_distance_after_total / with_outgoing_trip
                if with_outgoing_trip
                else None
            ),
        },
        "limitations": [
            "The target is a provisional representative point, not a confirmed development boundary.",
            "Transport modes are retained and must be re-estimated in a later step.",
            "Travel times and activity durations are unavailable, so departure times are unchanged.",
            "The model replaces existing shopping/dining destinations and does not add stopovers.",
        ],
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "selected_visits.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=SELECTED_VISIT_COLUMNS)
        writer.writeheader()
        writer.writerows(selected_rows)
    with (output_dir / "coordinate_changes.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=COORDINATE_CHANGE_COLUMNS)
        writer.writeheader()
        writer.writerows(coordinate_change_rows)
    distribution_rows = _distribution_rows(selected_rows)
    with (output_dir / "before_after_counts.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(
            output,
            fieldnames=("dimension", "category", "before_count", "after_count"),
        )
        writer.writeheader()
        writer.writerows(distribution_rows)
    with (output_dir / "chain_examples.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=SELECTED_VISIT_COLUMNS)
        writer.writeheader()
        writer.writerows(selected_rows[:100])
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace high-preference commercial destinations in trip chains."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--potential-summary", type=Path, default=DEFAULT_POTENTIAL_SUMMARY)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_destination_replacement(
        args.database,
        args.candidates,
        args.potential_summary,
        args.scenario,
        args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
