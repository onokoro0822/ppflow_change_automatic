#!/usr/bin/env python3
"""Validate facility arrivals before and after the combined origin reselection.

Checks, for shopping arrivals at the injected facility:

* Schedule feasibility. Pseudo-PFLOW's TripGenerator departs at
  ``next.start - distance / speed``, so the inbound trip consumes the end of the
  preceding activity and the outbound trip consumes the end of the shopping
  stay. An arrival is infeasible at a speed when either straight-line travel time
  exceeds the duration it eats into. Speeds follow ``pseudo.res.Speed``.
* Age x gender composition against the Chukyo PT commercial reference.
* Arrival hour composition against the Chukyo PT commercial arrival hours.

All shopping arrivals in the baseline run are reported as the model-native
reference for feasibility.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pflow_capacity_analysis import SHOPPING_PURPOSE, _iter_rows
from pflow_combined_origin_adjustment import is_facility
from pflow_origin_comparison import normalize, total_variation_distance
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PFLOW_HOME = PROJECT_DIR / ".local" / "pflow"
DEFAULT_OUTPUT = PROJECT_DIR / "output" / "pflow_combined_origin" / "validation.json"
DEFAULT_AGE_GENDER = PROJECT_DIR.parent / "nagoya_caluclation" / "chukyo_pt_2022_age_gender_distribution_reference.json"
DEFAULT_ARRIVAL_HOURS = (
    PROJECT_DIR / "output" / "chukyo_pt_facility_time_distribution" / "time_distribution_by_facility_use.json"
)
SPEEDS_KMH = {"walk": 4.8, "bicycle": 15.0, "car": 20.0, "train": 32.0}
AGE_BANDS = ("10歳未満", "10～19歳", "20～29歳", "30～39歳", "40～49歳", "50～59歳", "60～69歳", "70～79歳", "80歳以上")
GENDERS = {"1": "male", "2": "female"}


def age_band(age: int) -> str:
    return AGE_BANDS[min(age // 10, len(AGE_BANDS) - 1)]


def iter_arrivals(root: Path, *, facility_only: bool) -> Iterable[dict[str, object]]:
    """Yield shopping arrivals with their previous and next activities."""
    for csv_path in sorted(path for path in root.rglob("*.csv") if path.is_file()):
        by_person: dict[int, list[dict[str, object]]] = {}
        for row in _iter_rows(csv_path):
            by_person.setdefault(int(row["person_id"]), []).append(row)
        for rows in by_person.values():
            for index, row in enumerate(rows):
                if index == 0 or int(row["purpose"]) != SHOPPING_PURPOSE:
                    continue
                at_facility = is_facility([str(row[key]) for key in (
                    "person_id", "age", "gender", "labor", "start", "duration", "purpose", "lon", "lat", "gcode"
                )])
                if facility_only and not at_facility:
                    continue
                yield {
                    "row": row,
                    "previous": rows[index - 1],
                    "next": rows[index + 1] if index + 1 < len(rows) else None,
                }


def feasibility(arrivals: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {"count": len(arrivals)}
    inbound_km = []
    for speed_name, kmh in SPEEDS_KMH.items():
        speed = kmh * 1000 / 3600
        infeasible = 0
        for arrival in arrivals:
            row, previous, following = arrival["row"], arrival["previous"], arrival["next"]
            inbound = haversine_m(previous["lon"], previous["lat"], row["lon"], row["lat"]) / speed
            ok = inbound <= int(previous["duration"])
            if following is not None:
                outbound = haversine_m(row["lon"], row["lat"], following["lon"], following["lat"]) / speed
                ok = ok and outbound <= int(row["duration"])
            infeasible += not ok
        result[f"infeasible_share_{speed_name}"] = infeasible / len(arrivals) if arrivals else 0.0
    for arrival in arrivals:
        row, previous = arrival["row"], arrival["previous"]
        inbound_km.append(haversine_m(previous["lon"], previous["lat"], row["lon"], row["lat"]) / 1000)
    inbound_km.sort()
    if inbound_km:
        result["inbound_km_median"] = inbound_km[len(inbound_km) // 2]
        result["inbound_km_p90"] = inbound_km[int(0.9 * (len(inbound_km) - 1))]
    result["stay_minutes_median"] = (
        sorted(int(a["row"]["duration"]) for a in arrivals)[len(arrivals) // 2] / 60 if arrivals else 0
    )
    return result


def composition(arrivals: Sequence[Mapping[str, object]]) -> dict[str, dict[str, float]]:
    age_gender: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    for arrival in arrivals:
        row = arrival["row"]
        gender = GENDERS.get(str(row["gender"]), "other")
        age_gender[f"{age_band(int(row['age']))}|{gender}"] += 1
        hours[str(int(row["start"]) // 3600)] += 1
    return {"age_gender": normalize(age_gender), "hour": normalize(hours)}


def load_pt_references(age_gender_path: Path, hours_path: Path) -> dict[str, dict[str, float]]:
    commercial = json.loads(age_gender_path.read_text(encoding="utf-8"))["facilities"]["商業施設"]
    age_gender = {}
    for band, values in commercial.items():
        age_gender[f"{band}|male"] = values["male_pct"]
        age_gender[f"{band}|female"] = values["female_pct"]
    hourly = json.loads(hours_path.read_text(encoding="utf-8"))["facilities"]["commercial"]["hourly_distribution"]
    hours = {str(item["hour"]): item["trip_count"] for item in hourly}
    return {"age_gender": normalize(age_gender), "hour": normalize(hours)}


def summarize_marginals(shares: Mapping[str, float]) -> dict[str, float]:
    result: Counter[str] = Counter()
    for key, share in shares.items():
        band, gender = key.split("|")
        result[band] += share
        result[gender] += share
    return dict(result)


def validate(series_dirs: Mapping[str, Path], baseline_dir: Path, references) -> dict[str, object]:
    report: dict[str, object] = {
        "reference_all_shopping_baseline": feasibility(list(iter_arrivals(baseline_dir, facility_only=False))),
        "pt_reference": {
            "age_gender_marginals": summarize_marginals(references["age_gender"]),
        },
        "series": {},
    }
    for name, directory in series_dirs.items():
        arrivals = list(iter_arrivals(directory, facility_only=True))
        shares = composition(arrivals)
        report["series"][name] = {
            "feasibility": feasibility(arrivals),
            "tvd_age_gender_vs_pt": total_variation_distance(shares["age_gender"], references["age_gender"]),
            "tvd_hour_vs_pt": total_variation_distance(shares["hour"], references["hour"]),
            "age_gender_marginals": summarize_marginals(shares["age_gender"]),
            "hour_shares": dict(sorted(shares["hour"].items(), key=lambda item: int(item[0]))),
        }
    report["pt_reference"]["hour_shares"] = dict(
        sorted(references["hour"].items(), key=lambda item: int(item[0]))
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pflow-home", type=Path, default=DEFAULT_PFLOW_HOME)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--calibrated-dir", type=Path)
    parser.add_argument("--combined-dir", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--age-gender-json", type=Path, default=DEFAULT_AGE_GENDER)
    parser.add_argument("--arrival-hours-json", type=Path, default=DEFAULT_ARRIVAL_HOURS)
    args = parser.parse_args(argv)

    output = args.pflow_home / "output"
    baseline_dir = args.baseline_dir or output / "capacity_experiment" / "baseline"
    calibrated_dir = args.calibrated_dir or output / "calibrated_target" / "calibrated_target"
    combined_dir = args.combined_dir or output / "combined_origin" / f"seed{args.seed}"
    references = load_pt_references(args.age_gender_json, args.arrival_hours_json)
    report = validate(
        {"calibrated_facility": calibrated_dir, "combined_facility": combined_dir}, baseline_dir, references
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["reference_all_shopping_baseline"], ensure_ascii=False))
    for name, values in report["series"].items():
        print(name, json.dumps({k: v for k, v in values.items() if k != "hour_shares"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
