"""Compare a reference-facility profile with the citywide commercial baseline."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BASELINE = BASE_DIR / "output" / "commercial_profiles" / "all_city" / "profile.json"
DEFAULT_FACILITY = (
    BASE_DIR / "output" / "commercial_profiles" / "midland_square_50m" / "profile.json"
)
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "facility_preference" / "midland_square_50m"
DEFAULT_DIMENSIONS = (
    "employment_status",
    "time_band",
    "distance_band",
    "transport_mode",
    "previous_trip_purpose",
    "next_trip_purpose",
    "trip_purpose",
)


def _load_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _distribution_map(profile: dict[str, Any], dimension: str) -> dict[str, dict[str, Any]]:
    return {
        str(row["category"]): row
        for row in profile.get("distributions", {}).get(dimension, [])
    }


def build_facility_preference(
    baseline_profile_path: Path,
    facility_profile_path: Path,
    output_dir: Path,
    *,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    minimum_facility_count: int = 30,
    minimum_baseline_count: int = 100,
    overrepresented_threshold: float = 1.10,
    underrepresented_threshold: float = 0.90,
) -> dict[str, Any]:
    """Calculate FCPI-like composition ratios for profile categories."""

    if minimum_facility_count < 1 or minimum_baseline_count < 1:
        raise ValueError("Minimum counts must be positive")
    if not 0 < underrepresented_threshold <= 1:
        raise ValueError("underrepresented_threshold must be in (0, 1]")
    if overrepresented_threshold < 1:
        raise ValueError("overrepresented_threshold must be at least 1")

    baseline_profile_path = Path(baseline_profile_path)
    facility_profile_path = Path(facility_profile_path)
    output_dir = Path(output_dir)
    baseline = _load_profile(baseline_profile_path)
    facility = _load_profile(facility_profile_path)
    baseline_total = int(baseline["counts"]["selected_trips"])
    facility_total = int(facility["counts"]["selected_trips"])
    if baseline_total <= 0 or facility_total <= 0:
        raise ValueError("Profiles must contain selected trips")

    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        baseline_map = _distribution_map(baseline, dimension)
        facility_map = _distribution_map(facility, dimension)
        categories = sorted(set(baseline_map) | set(facility_map))
        for category in categories:
            baseline_row = baseline_map.get(category, {})
            facility_row = facility_map.get(category, {})
            baseline_count = int(baseline_row.get("count", 0))
            facility_count = int(facility_row.get("count", 0))
            baseline_share = baseline_count / baseline_total
            facility_share = facility_count / facility_total
            ratio = facility_share / baseline_share if baseline_share else None
            eligible = (
                facility_count >= minimum_facility_count
                and baseline_count >= minimum_baseline_count
                and ratio is not None
            )
            if not eligible:
                interpretation = "insufficient_sample"
            elif ratio >= overrepresented_threshold:
                interpretation = "overrepresented"
            elif ratio <= underrepresented_threshold:
                interpretation = "underrepresented"
            else:
                interpretation = "near_baseline"
            rows.append(
                {
                    "dimension": dimension,
                    "category": category,
                    "baseline_count": baseline_count,
                    "baseline_share": baseline_share,
                    "facility_count": facility_count,
                    "facility_share": facility_share,
                    "relative_preference": ratio,
                    "log2_relative_preference": (
                        math.log2(ratio) if ratio is not None and ratio > 0 else None
                    ),
                    "sample_eligible": eligible,
                    "interpretation": interpretation,
                }
            )

    eligible_overrepresented = sorted(
        (row for row in rows if row["interpretation"] == "overrepresented"),
        key=lambda row: (-row["relative_preference"], -row["facility_count"]),
    )
    potential_visitor_scoring_conditions: dict[str, list[dict[str, Any]]] = {}
    for dimension in dimensions:
        candidates = [
            row for row in eligible_overrepresented if row["dimension"] == dimension
        ]
        if candidates:
            potential_visitor_scoring_conditions[dimension] = [
                {
                    "category": row["category"],
                    "relative_preference": row["relative_preference"],
                    "facility_count": row["facility_count"],
                }
                for row in candidates
            ]

    result: dict[str, Any] = {
        "method": {
            "name": "FCPI-like composition ratio",
            "formula": "facility_category_share / citywide_category_share",
            "interpretation": (
                "Values above 1 indicate overrepresentation at the reference facility. "
                "This is a descriptive adaptation, not Liu et al.'s GPS-population FCPI."
            ),
            "minimum_facility_count": minimum_facility_count,
            "minimum_baseline_count": minimum_baseline_count,
            "overrepresented_threshold": overrepresented_threshold,
            "underrepresented_threshold": underrepresented_threshold,
        },
        "baseline": {
            "profile_path": str(baseline_profile_path.resolve()),
            "scope": baseline.get("scope"),
            "selected_trips": baseline_total,
        },
        "facility": {
            "profile_path": str(facility_profile_path.resolve()),
            "scope": facility.get("scope"),
            "selected_trips": facility_total,
            "unique_persons": facility["counts"].get("unique_persons"),
        },
        "potential_visitor_scoring_conditions": potential_visitor_scoring_conditions,
        "top_overrepresented": eligible_overrepresented[:20],
        "comparisons": rows,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "preference.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "preference.csv").open("w", encoding="utf-8", newline="") as output:
        fieldnames = (
            "dimension",
            "category",
            "baseline_count",
            "baseline_share",
            "facility_count",
            "facility_share",
            "relative_preference",
            "log2_relative_preference",
            "sample_eligible",
            "interpretation",
        )
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare a facility profile with the citywide commercial baseline."
    )
    parser.add_argument("--baseline-profile", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--facility-profile", type=Path, default=DEFAULT_FACILITY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--minimum-facility-count", type=int, default=30)
    parser.add_argument("--minimum-baseline-count", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_facility_preference(
        args.baseline_profile,
        args.facility_profile,
        args.output_dir,
        minimum_facility_count=args.minimum_facility_count,
        minimum_baseline_count=args.minimum_baseline_count,
    )
    print(
        json.dumps(
            {
                "facility": result["facility"],
                "top_overrepresented": result["top_overrepresented"][:10],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
