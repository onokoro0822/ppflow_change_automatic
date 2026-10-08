#!/usr/bin/env python3
"""Combine the capacity intervention with origin-distribution reselection.

The calibrated capacity run decides how many shopping arrivals reach the
injected facility. This step keeps that count and reselects *which* shopping
arrivals go there so that their immediately preceding activity locations follow
the Chukyo PT shopping origin distribution:

1. Allocate the facility count to comparison units by PT shares (Aichi only,
   largest remainder, bounded by available candidates).
2. In each unit, keep arrivals the capacity run already sent to the facility,
   then add other shopping arrivals from that unit by a deterministic priority.
3. Facility arrivals that exceed their unit's allocation are released back to
   the location they had in the baseline run (same person and activity row).

Optionally only arrivals whose schedule still works at the facility are
eligible: Pseudo-PFLOW's TripGenerator departs at ``next.start - distance /
speed``, so the straight-line trip from the preceding activity must fit in that
activity's duration and the trip to the next activity in the shopping stay.

Activity rows carry their own location, so moving one shopping activity changes
both the inbound and the outbound trip. Times, purposes and attributes are kept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Sequence

import chukyo_pt_origin_distribution as pt
from meitetsu_origin_distribution_replacement import bounded_allocation, largest_remainder_counts
from pflow_capacity_analysis import FACILITY_TOLERANCE, SHOPPING_PURPOSE, TARGET_GCODE, TARGET_LAT, TARGET_LON
from pflow_origin_comparison import (
    OUTSIDE_AICHI,
    SERIES_LABELS,
    ComparisonUnits,
    compare,
    pflow_origin_counts,
    pt_origin_counts,
    write_outputs,
)
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PFLOW_HOME = PROJECT_DIR / ".local" / "pflow"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "pflow_combined_origin"
DEFAULT_SEED = 42
DEFAULT_FEASIBILITY_SPEED_KMH = 20.0  # pseudo.res.Speed.CAR
FACILITY_LON_TEXT = "136.883980"
FACILITY_LAT_TEXT = "35.169674"


@dataclass
class ActivityFile:
    relative_path: Path
    rows: list[list[str]]


@dataclass(frozen=True)
class Candidate:
    file_index: int
    row_index: int
    person_id: str
    unit: str
    at_facility: bool
    previous_lon: float
    previous_lat: float
    lon: float
    lat: float
    previous_duration: int = 0
    duration: int = 0
    next_lon: float | None = None
    next_lat: float | None = None

    @property
    def key(self) -> str:
        return f"{self.file_index}:{self.row_index}"


def read_activity_files(root: Path) -> list[ActivityFile]:
    files = []
    for path in sorted(p for p in root.rglob("*.csv") if p.is_file()):
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            fields = line.split(",")
            if len(fields) != 10:
                raise ValueError(f"{path}:{line_number}: expected 10 columns, got {len(fields)}")
            rows.append(fields)
        files.append(ActivityFile(path.relative_to(root), rows))
    if not files:
        raise FileNotFoundError(f"No activity CSVs under {root}")
    return files


def is_facility(fields: Sequence[str]) -> bool:
    return (
        abs(float(fields[7]) - TARGET_LON) <= FACILITY_TOLERANCE
        and abs(float(fields[8]) - TARGET_LAT) <= FACILITY_TOLERANCE
    )


def check_aligned(calibrated: Sequence[ActivityFile], baseline: Sequence[ActivityFile]) -> None:
    """Capacity scenarios keep activity sequences; only locations may differ."""
    if [f.relative_path for f in calibrated] != [f.relative_path for f in baseline]:
        raise ValueError("Calibrated and baseline runs have different activity files")
    for cal, base in zip(calibrated, baseline):
        if len(cal.rows) != len(base.rows):
            raise ValueError(f"{cal.relative_path}: row counts differ")
        for index, (a, b) in enumerate(zip(cal.rows, base.rows)):
            if a[:7] != b[:7]:
                raise ValueError(f"{cal.relative_path}:{index + 1}: person/time/purpose differ")


def collect_candidates(files: Sequence[ActivityFile], units: ComparisonUnits) -> list[Candidate]:
    candidates = []
    for file_index, activity_file in enumerate(files):
        rows = activity_file.rows
        for row_index, fields in enumerate(rows):
            if row_index == 0 or int(fields[6]) != SHOPPING_PURPOSE:
                continue
            previous = rows[row_index - 1]
            if previous[0] != fields[0]:
                continue  # first activity of this person
            if is_facility(previous):
                continue  # the preceding activity is already at the facility
            following = rows[row_index + 1] if row_index + 1 < len(rows) else None
            if following is not None and following[0] != fields[0]:
                following = None
            candidates.append(
                Candidate(
                    file_index=file_index,
                    row_index=row_index,
                    person_id=fields[0],
                    unit=units.unit_for_gcode(previous[9]),
                    at_facility=is_facility(fields),
                    previous_lon=float(previous[7]),
                    previous_lat=float(previous[8]),
                    lon=float(fields[7]),
                    lat=float(fields[8]),
                    previous_duration=int(previous[5]),
                    duration=int(fields[5]),
                    next_lon=float(following[7]) if following else None,
                    next_lat=float(following[8]) if following else None,
                )
            )
    return candidates


def fits_schedule_at_facility(candidate: Candidate, speed_kmh: float) -> bool:
    """True when straight-line trips to and from the facility fit the schedule."""
    speed = speed_kmh * 1000 / 3600
    inbound = haversine_m(candidate.previous_lon, candidate.previous_lat, TARGET_LON, TARGET_LAT) / speed
    if inbound > candidate.previous_duration:
        return False
    if candidate.next_lon is None:
        return True
    outbound = haversine_m(TARGET_LON, TARGET_LAT, candidate.next_lon, candidate.next_lat) / speed
    return outbound <= candidate.duration


def priority(seed: int, candidate: Candidate) -> int:
    digest = hashlib.sha256(f"{seed}:{candidate.person_id}:{candidate.key}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def select(
    candidates: Sequence[Candidate],
    weights: Mapping[str, float],
    total: int,
    seed: int,
    *,
    feasibility_speed_kmh: float | None = None,
) -> tuple[list[Candidate], dict[str, object]]:
    """Choose `total` arrivals, one per person, following unit weights."""
    by_unit: dict[str, list[Candidate]] = defaultdict(list)
    excluded = Counter()
    for candidate in candidates:
        if candidate.unit == OUTSIDE_AICHI:
            continue
        if feasibility_speed_kmh and not fits_schedule_at_facility(candidate, feasibility_speed_kmh):
            excluded["facility" if candidate.at_facility else "other"] += 1
            continue
        by_unit[candidate.unit].append(candidate)
    for unit_candidates in by_unit.values():
        # Facility arrivals chosen by the capacity run come first.
        unit_candidates.sort(key=lambda c: (not c.at_facility, priority(seed, c)))
    availability = {unit: len({c.person_id for c in rows}) for unit, rows in by_unit.items()}
    usable_weights = {unit: weight for unit, weight in weights.items() if unit in by_unit}
    allocation, shortfall = bounded_allocation(usable_weights, availability, total)

    selected: list[Candidate] = []
    used_people: set[str] = set()
    for unit in sorted(allocation):
        taken = 0
        for candidate in by_unit[unit]:
            if taken >= allocation[unit]:
                break
            if candidate.person_id in used_people:
                continue
            selected.append(candidate)
            used_people.add(candidate.person_id)
            taken += 1
        shortfall += allocation[unit] - taken
    audit = {
        "requested": largest_remainder_counts(usable_weights, total),
        "allocated": allocation,
        "units_without_candidates": sorted(set(weights) - set(by_unit)),
        "shortfall": shortfall,
        "excluded_by_schedule": dict(excluded),
        "feasibility_speed_kmh": feasibility_speed_kmh,
    }
    return selected, audit


def apply_selection(
    calibrated: Sequence[ActivityFile],
    baseline: Sequence[ActivityFile],
    candidates: Sequence[Candidate],
    selected: Sequence[Candidate],
    *,
    start_from_baseline: bool = False,
) -> tuple[list[ActivityFile], dict[str, int]]:
    """Write the selection onto the calibrated run, or onto the baseline run.

    Starting from the baseline keeps only the facility moves: side effects of the
    capacity run (other shops in the boosted mesh, random-number cascades) are
    dropped, so the scenario differs from the baseline in exactly the selected rows.
    """
    selected_keys = {c.key for c in selected}
    source = baseline if start_from_baseline else calibrated
    output = [ActivityFile(f.relative_path, [list(row) for row in f.rows]) for f in source]
    counts = Counter()
    for candidate in candidates:
        fields = output[candidate.file_index].rows[candidate.row_index]
        if candidate.key in selected_keys:
            counts["kept" if candidate.at_facility else "added"] += 1
            fields[7], fields[8], fields[9] = FACILITY_LON_TEXT, FACILITY_LAT_TEXT, TARGET_GCODE
        elif candidate.at_facility:
            counts["released"] += 1
            base = baseline[candidate.file_index].rows[candidate.row_index]
            fields[7], fields[8], fields[9] = base[7], base[8], base[9]
    # Facility arrivals that were not candidates (preceded by the facility itself) stay.
    for activity_file in output:
        for index, fields in enumerate(activity_file.rows):
            if int(fields[6]) == SHOPPING_PURPOSE and is_facility(fields):
                counts["facility_arrivals_after"] += 1
    return output, dict(counts)


def write_activity_files(files: Sequence[ActivityFile], root: Path) -> None:
    for activity_file in files:
        path = root / activity_file.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(",".join(row) + "\n" for row in activity_file.rows), encoding="utf-8")


def distance_summary(rows: Sequence[Candidate], *, to_facility: bool) -> dict[str, float]:
    distances = sorted(
        haversine_m(c.previous_lon, c.previous_lat, TARGET_LON, TARGET_LAT)
        if to_facility
        else haversine_m(c.previous_lon, c.previous_lat, c.lon, c.lat)
        for c in rows
    )
    if not distances:
        return {}
    return {
        "count": len(distances),
        "mean_km": sum(distances) / len(distances) / 1000,
        "median_km": distances[len(distances) // 2] / 1000,
        "p90_km": distances[min(len(distances) - 1, math.floor(0.9 * len(distances)))] / 1000,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pflow-home", type=Path, default=DEFAULT_PFLOW_HOME)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--calibrated-dir", type=Path)
    parser.add_argument("--activity-output-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-count", type=int, help="default: facility arrivals in the calibrated run")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--start-from-baseline", action="store_true",
        help="apply only the facility moves to the baseline run (drop capacity-run side effects)",
    )
    parser.add_argument(
        "--feasibility-speed-kmh", type=float, default=0.0,
        help="exclude arrivals whose trips to/from the facility do not fit at this speed (0 = off)",
    )
    parser.add_argument("--pt-csv", type=Path, default=pt.DEFAULT_INPUT)
    parser.add_argument("--zone-xlsx", type=Path, default=pt.DEFAULT_ZONE_CODE_XLSX)
    args = parser.parse_args(argv)

    baseline_dir = args.baseline_dir or args.pflow_home / "output" / "capacity_experiment" / "baseline"
    calibrated_dir = args.calibrated_dir or args.pflow_home / "output" / "calibrated_target" / "calibrated_target"
    activity_output_dir = args.activity_output_dir or (
        args.pflow_home / "output" / "combined_origin" / f"seed{args.seed}"
    )
    if activity_output_dir.exists() and any(activity_output_dir.rglob("*.csv")):
        raise FileExistsError(f"Refusing to overwrite existing activity output: {activity_output_dir}")

    zone_labels = pt.load_middle_zone_labels(args.zone_xlsx)
    units = ComparisonUnits(zone_labels)
    pt_counts = pt_origin_counts(args.pt_csv, units, zone_labels)
    weights = {unit: count for unit, count in pt_counts["pt_shopping"].items() if unit != OUTSIDE_AICHI}

    calibrated = read_activity_files(calibrated_dir / "23")
    baseline = read_activity_files(baseline_dir / "23")
    check_aligned(calibrated, baseline)
    if args.start_from_baseline:
        # Origins and schedules come from the baseline rows that will be edited; the
        # capacity run only marks which arrivals it already sent to the facility.
        candidates = [
            replace(c, at_facility=is_facility(calibrated[c.file_index].rows[c.row_index]))
            for c in collect_candidates(baseline, units)
        ]
    else:
        candidates = collect_candidates(calibrated, units)
    facility_before = [c for c in candidates if c.at_facility]
    total = args.target_count if args.target_count is not None else len(facility_before)
    selected, audit = select(
        candidates, weights, total, args.seed, feasibility_speed_kmh=args.feasibility_speed_kmh or None
    )
    adjusted, change_counts = apply_selection(
        calibrated, baseline, candidates, selected, start_from_baseline=args.start_from_baseline
    )
    write_activity_files(adjusted, activity_output_dir / "23")

    distributions = dict(pt_counts)
    distributions["calibrated_facility"] = pflow_origin_counts(calibrated_dir, units)["facility"]
    distributions["combined_facility"] = pflow_origin_counts(activity_output_dir, units)["facility"]
    comparison = compare(distributions, reference_key="pt_shopping")
    released = [c for c in facility_before if c.key not in {s.key for s in selected}]
    added = [c for c in selected if not c.at_facility]
    comparison["adjustment"] = {
        "seed": args.seed,
        "start_from_baseline": args.start_from_baseline,
        "target_count": total,
        "facility_arrivals_before": len(facility_before),
        "changes": change_counts,
        "allocation": audit,
        "previous_to_facility_distance": {
            "capacity_run": distance_summary(facility_before, to_facility=True),
            "combined": distance_summary(selected, to_facility=True),
        },
        "added_original_trip_distance": distance_summary(added, to_facility=False),
        "added_new_trip_distance": distance_summary(added, to_facility=True),
        "released_count": len(released),
        "activity_output_dir": str(activity_output_dir),
    }
    comparison["notes"] = [
        "Weights are Chukyo PT shopping trips into middle zone 5, restricted to Aichi units.",
        "Moved activities keep start time and duration; travel feasibility is not re-checked.",
        "Released facility arrivals return to their baseline-run location.",
        "Pseudo-PFLOW counts are a 2% sample (sample factor 50).",
    ]
    labels = dict(SERIES_LABELS, combined_facility="組み合わせ方式 対象施設")
    write_outputs(comparison, args.output_dir, labels)
    for key, values in comparison["series"].items():
        print(f"{labels.get(key, key)}: n={values['count']:.0f} TVD={values['tvd_vs_reference']:.3f} "
              f"TVD(Aichi)={values['tvd_vs_reference_aichi_only']:.3f}")
    print(json.dumps(comparison["adjustment"], ensure_ascii=False, indent=1, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
