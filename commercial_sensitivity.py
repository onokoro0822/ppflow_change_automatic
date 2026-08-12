"""Summarize reference-boundary and candidate-threshold sensitivity results."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "sensitivity" / "midland_square"
BUFFER_VARIANTS = {
    0: "midland_square_0m",
    50: "midland_square_50m",
    100: "midland_square_100m",
}
THRESHOLD_VARIANTS = {
    1.05: "midland_square_50m_threshold_1_05",
    1.10: "midland_square_50m",
    1.15: "midland_square_50m_threshold_1_15",
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _category_count(profile: dict[str, Any], dimension: str, category: str) -> int:
    for row in profile["distributions"].get(dimension, []):
        if row["category"] == category:
            return int(row["count"])
    return 0


def _category_ratio(
    preference: dict[str, Any], dimension: str, category: str
) -> float | None:
    for row in preference["comparisons"]:
        if row["dimension"] == dimension and row["category"] == category:
            ratio = row["relative_preference"]
            return float(ratio) if ratio is not None else None
    return None


def _candidate_purpose_counts(path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            code = str(row["trip_purpose"]).split(":", 1)[0]
            counts[code] += 1
    return counts


def build_sensitivity_summary(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    buffer_rows: list[dict[str, Any]] = []
    for buffer_m, name in BUFFER_VARIANTS.items():
        profile = _load_json(
            BASE_DIR / "output" / "commercial_profiles" / name / "profile.json"
        )
        preference = _load_json(
            BASE_DIR / "output" / "facility_preference" / name / "preference.json"
        )
        potential = _load_json(
            BASE_DIR / "output" / "potential_visitors" / name / "summary.json"
        )
        previous_rail = _category_count(
            profile, "previous_transport_mode", "4:電車"
        )
        rail_to_walk = sum(
            int(row["count"])
            for row in profile["distributions"].get("transport_sequence", [])
            if str(row["category"]).startswith("4:電車 → 1:徒歩")
        )
        selected_trips = int(profile["counts"]["selected_trips"])
        buffer_rows.append(
            {
                "boundary_buffer_m": buffer_m,
                "facility_trips": selected_trips,
                "facility_persons": int(profile["counts"]["unique_persons"]),
                "direct_rail_trips": _category_count(
                    profile, "transport_mode", "4:電車"
                ),
                "previous_rail_trips": previous_rail,
                "previous_rail_share": (
                    previous_rail / selected_trips if selected_trips else None
                ),
                "previous_rail_to_walk_trips": rail_to_walk,
                "shopping_relative_preference": _category_ratio(
                    preference, "trip_purpose", "100:買い物"
                ),
                "dining_relative_preference": _category_ratio(
                    preference, "trip_purpose", "200:外食"
                ),
                "free_activity_relative_preference": _category_ratio(
                    preference, "trip_purpose", "400:自由行動"
                ),
                "candidate_persons_at_1_10": int(
                    potential["counts"]["candidate_persons"]
                ),
                "estimated_reference_equivalent_visitors": float(
                    potential["counts"]["estimated_reference_equivalent_visitors"]
                ),
                "estimated_high_preference_visitors": float(
                    potential["counts"]["estimated_high_preference_visitors"]
                ),
            }
        )

    threshold_rows: list[dict[str, Any]] = []
    for threshold, name in THRESHOLD_VARIANTS.items():
        directory = BASE_DIR / "output" / "potential_visitors" / name
        potential = _load_json(directory / "summary.json")
        purposes = _candidate_purpose_counts(directory / "candidates.csv")
        threshold_rows.append(
            {
                "boundary_buffer_m": 50,
                "candidate_score_threshold": threshold,
                "candidate_trips": int(potential["counts"]["candidate_trips"]),
                "candidate_persons": int(potential["counts"]["candidate_persons"]),
                "shopping_candidate_persons": purposes["100"],
                "dining_candidate_persons": purposes["200"],
                "free_activity_candidate_persons": purposes["400"],
                "estimated_high_preference_visitors": float(
                    potential["counts"]["estimated_high_preference_visitors"]
                ),
            }
        )

    result = {
        "method": {
            "boundary_comparison_m": sorted(BUFFER_VARIANTS),
            "candidate_score_thresholds": sorted(THRESHOLD_VARIANTS),
            "status": "descriptive sensitivity analysis; parameters are not validated",
        },
        "boundary_sensitivity": buffer_rows,
        "candidate_threshold_sensitivity": threshold_rows,
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for filename, rows in (
        ("boundary_sensitivity.csv", buffer_rows),
        ("candidate_threshold_sensitivity.csv", threshold_rows),
    ):
        with (output_dir / filename).open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=tuple(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    print(
        json.dumps(
            build_sensitivity_summary(args.output_dir),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
