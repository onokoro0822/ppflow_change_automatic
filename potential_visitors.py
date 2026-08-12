"""Extract potential visitors using a reference facility's FCPI-like profile."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from commercial_profiles import (
    EMPLOYMENT_LABELS,
    PURPOSE_LABELS,
    TRANSPORT_LABELS,
    _distance_to_polygon_m,
    _prepare_geojson_polygon,
    coded_label,
    distance_band,
    time_band,
)
from trip_chains import haversine_m


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "output" / "trip_chains" / "nagoya_trip_chains.sqlite3"
DEFAULT_BASELINE_PROFILE = (
    BASE_DIR / "output" / "commercial_profiles" / "all_city" / "profile.json"
)
DEFAULT_FACILITY_PROFILE = (
    BASE_DIR / "output" / "commercial_profiles" / "midland_square_50m" / "profile.json"
)
DEFAULT_PREFERENCE = (
    BASE_DIR / "output" / "facility_preference" / "midland_square_50m" / "preference.json"
)
DEFAULT_FACILITY_CONFIG = (
    BASE_DIR / "config" / "reference_facilities" / "midland_square.json"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "potential_visitors" / "midland_square_50m"
SCORING_DIMENSIONS = (
    "employment_status",
    "time_band",
    "distance_band",
    "transport_mode",
    "previous_trip_purpose",
    "next_trip_purpose",
    "trip_purpose",
)
SAMPLE_COLUMNS = (
    "person_id",
    "trip_id",
    "preference_score",
    "estimated_visit_probability",
    *SCORING_DIMENSIONS,
)


def geometric_preference_score(ratios: list[float]) -> float:
    if not ratios or any(ratio <= 0 for ratio in ratios):
        raise ValueError("Preference ratios must be positive")
    return math.exp(sum(math.log(ratio) for ratio in ratios) / len(ratios))


def _relative_preference_lookup(preference: dict[str, Any]) -> dict[str, dict[str, float]]:
    lookup: dict[str, dict[str, float]] = {dimension: {} for dimension in SCORING_DIMENSIONS}
    for row in preference["comparisons"]:
        dimension = row["dimension"]
        ratio = row["relative_preference"]
        if (
            dimension in lookup
            and row["sample_eligible"]
            and ratio is not None
            and ratio > 0
        ):
            lookup[dimension][row["category"]] = float(ratio)
    return lookup


def build_potential_visitors(
    database_path: Path,
    baseline_profile_path: Path,
    facility_profile_path: Path,
    preference_path: Path,
    facility_config_path: Path,
    output_dir: Path,
    *,
    candidate_score_threshold: float = 1.10,
    sample_size: int = 1_000,
    facility_boundary_buffer_m: float | None = None,
) -> dict[str, Any]:
    """Score commercial-trip persons and estimate reference-equivalent visitors."""

    if candidate_score_threshold <= 0:
        raise ValueError("candidate_score_threshold must be positive")
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")

    paths = [
        Path(database_path),
        Path(baseline_profile_path),
        Path(facility_profile_path),
        Path(preference_path),
        Path(facility_config_path),
    ]
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(path)

    baseline = json.loads(paths[1].read_text(encoding="utf-8"))
    facility = json.loads(paths[2].read_text(encoding="utf-8"))
    preference = json.loads(paths[3].read_text(encoding="utf-8"))
    facility_config = json.loads(paths[4].read_text(encoding="utf-8"))
    boundary_path = BASE_DIR / facility_config["boundary_geojson"]
    boundary = _prepare_geojson_polygon(boundary_path)
    boundary_buffer_m = (
        float(facility_config["destination_buffer_m"])
        if facility_boundary_buffer_m is None
        else float(facility_boundary_buffer_m)
    )
    if boundary_buffer_m < 0:
        raise ValueError("facility_boundary_buffer_m must be non-negative")
    target_lon = float(facility_config["representative_point"]["longitude"])
    target_lat = float(facility_config["representative_point"]["latitude"])
    purpose_codes = tuple(facility_config["commercial_purpose_codes"])

    baseline_persons = int(baseline["counts"]["unique_persons"])
    observed_facility_persons = int(facility["counts"]["unique_persons"])
    base_person_visit_rate = observed_facility_persons / baseline_persons
    ratio_lookup = _relative_preference_lookup(preference)

    connection = sqlite3.connect(f"file:{paths[0].resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    quality_summary_row = connection.execute(
        "SELECT value_json FROM metadata WHERE key = 'analysis_quality_summary'"
    ).fetchone()
    total_analysis_persons = (
        json.loads(quality_summary_row[0])["persons_with_eligible_trips"]
        if quality_summary_row is not None
        else connection.execute(
            "SELECT COUNT(DISTINCT person_id) FROM analysis_trip_chain_rows "
            "WHERE analysis_eligible = 1"
        ).fetchone()[0]
    )
    placeholders = ",".join("?" for _ in purpose_codes)
    rows = connection.execute(
        f"""
        SELECT
            trip_id, person_id, departure_time_sec,
            origin_lon, origin_lat, destination_lon, destination_lat,
            transport_mode, trip_purpose, employment_status,
            analysis_previous_trip_purpose, analysis_next_trip_purpose
        FROM analysis_trip_chain_rows
        WHERE analysis_eligible = 1
          AND trip_purpose IN ({placeholders})
        """,
        purpose_codes,
    )

    person_best: dict[str, dict[str, Any]] = {}
    all_person_max_scores: dict[str, float] = {}
    scored_trips = 0
    excluded_existing_facility_trips = 0
    candidate_trips = 0
    for row in rows:
        scored_trips += 1
        if (
            _distance_to_polygon_m(
                row["destination_lon"], row["destination_lat"], boundary
            )
            <= boundary_buffer_m
        ):
            excluded_existing_facility_trips += 1
            continue

        attributes = {
            "employment_status": coded_label(
                row["employment_status"], EMPLOYMENT_LABELS, none_label="欠損"
            ),
            "time_band": time_band(int(row["departure_time_sec"])),
            "distance_band": distance_band(
                haversine_m(
                    row["origin_lon"], row["origin_lat"], target_lon, target_lat
                )
                / 1000.0
            ),
            "transport_mode": coded_label(
                row["transport_mode"], TRANSPORT_LABELS, none_label="欠損"
            ),
            "previous_trip_purpose": coded_label(
                row["analysis_previous_trip_purpose"],
                PURPOSE_LABELS,
                none_label="なし（チェーン先頭）",
            ),
            "next_trip_purpose": coded_label(
                row["analysis_next_trip_purpose"],
                PURPOSE_LABELS,
                none_label="なし（チェーン末尾）",
            ),
            "trip_purpose": coded_label(
                row["trip_purpose"], PURPOSE_LABELS, none_label="なし"
            ),
        }
        ratios = [
            ratio_lookup[dimension].get(attributes[dimension], 1.0)
            for dimension in SCORING_DIMENSIONS
        ]
        score = geometric_preference_score(ratios)
        person_id = str(row["person_id"])
        previous_score = all_person_max_scores.get(person_id)
        if previous_score is None or score > previous_score:
            all_person_max_scores[person_id] = score
        if score < candidate_score_threshold:
            continue
        candidate_trips += 1
        previous_best = person_best.get(person_id)
        if previous_best is None or score > previous_best["preference_score"]:
            person_best[person_id] = {
                "person_id": person_id,
                "trip_id": int(row["trip_id"]),
                "preference_score": score,
                **attributes,
            }
    connection.close()

    expected_high_preference_visitors = 0.0
    for candidate in person_best.values():
        probability = min(
            1.0,
            base_person_visit_rate * candidate["preference_score"],
        )
        candidate["estimated_visit_probability"] = probability
        expected_high_preference_visitors += probability
    expected_reference_equivalent_visitors = sum(
        min(1.0, base_person_visit_rate * score)
        for score in all_person_max_scores.values()
    )

    ranked_candidates = sorted(
        person_best.values(),
        key=lambda row: (-row["preference_score"], row["person_id"]),
    )
    result: dict[str, Any] = {
        "method": {
            "name": "reference-facility relative-preference scoring",
            "candidate_score": (
                "geometric mean of eligible category-level FCPI-like ratios"
            ),
            "candidate_score_threshold": candidate_score_threshold,
            "person_score": "maximum score among a person's eligible commercial trips",
            "base_person_visit_rate": base_person_visit_rate,
            "estimated_probability": "min(1, base_person_visit_rate * person_score)",
            "status": "provisional; requires supervisor review and external validation",
        },
        "reference_facility": {
            "facility_id": facility_config["facility_id"],
            "name": facility_config["name"],
            "boundary_buffer_m": boundary_buffer_m,
            "observed_trips": facility["counts"]["selected_trips"],
            "observed_persons": observed_facility_persons,
        },
        "counts": {
            "all_analysis_persons": total_analysis_persons,
            "commercial_baseline_persons": baseline_persons,
            "commercial_trips_scored": scored_trips,
            "excluded_existing_facility_trips": excluded_existing_facility_trips,
            "commercial_opportunity_persons": len(all_person_max_scores),
            "candidate_trips": candidate_trips,
            "candidate_persons": len(person_best),
            "estimated_reference_equivalent_visitors": (
                expected_reference_equivalent_visitors
            ),
            "estimated_high_preference_visitors": expected_high_preference_visitors,
        },
        "limitations": [
            "The estimate is for a reference-equivalent commercial facility; floor-area scaling is not applied.",
            "Departure time is used as a proxy for visit timing because arrival time is unavailable.",
            "Category ratios are combined descriptively and are not a causal choice model.",
            "Correlated dimensions are damped with a geometric mean but are not statistically independent.",
        ],
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "candidate_sample.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=SAMPLE_COLUMNS)
        writer.writeheader()
        writer.writerows(ranked_candidates[:sample_size])
    with (output_dir / "candidates.csv").open(
        "w", encoding="utf-8", newline=""
    ) as output:
        writer = csv.DictWriter(output, fieldnames=SAMPLE_COLUMNS)
        writer.writeheader()
        writer.writerows(ranked_candidates)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract potential visitors from FCPI-like reference preferences."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--baseline-profile", type=Path, default=DEFAULT_BASELINE_PROFILE)
    parser.add_argument("--facility-profile", type=Path, default=DEFAULT_FACILITY_PROFILE)
    parser.add_argument("--preference", type=Path, default=DEFAULT_PREFERENCE)
    parser.add_argument("--facility-config", type=Path, default=DEFAULT_FACILITY_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--candidate-score-threshold", type=float, default=1.10)
    parser.add_argument("--facility-boundary-buffer-m", type=float)
    parser.add_argument("--sample-size", type=int, default=1_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_potential_visitors(
        args.database,
        args.baseline_profile,
        args.facility_profile,
        args.preference,
        args.facility_config,
        args.output_dir,
        candidate_score_threshold=args.candidate_score_threshold,
        sample_size=args.sample_size,
        facility_boundary_buffer_m=args.facility_boundary_buffer_m,
    )
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
