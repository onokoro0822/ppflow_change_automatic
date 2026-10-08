#!/usr/bin/env python3
"""Effects of a facility scenario that are not inputs to it.

Compares a scenario run with its baseline (same people, same activity
sequences) and reports:

* Hourly occupancy at the facility: how many visitors are inside during each
  hour (arrivals alone do not give crowding). TripGenerator takes the outbound
  trip out of the end of the shopping activity, so the stay is the activity
  duration minus the straight-line trip to the next activity at car speed.
* How the visitors' day changes: straight-line distance of the trip into the
  shopping activity, the trip out of it, and the whole day's chain, before and
  after the move.

Counts are scaled by the sample factor to full-population equivalents.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Sequence

from pflow_capacity_analysis import SHOPPING_PURPOSE
from pflow_combined_origin_adjustment import is_facility, read_activity_files
from trip_chains import haversine_m

SAMPLE_FACTOR = 50
STAY_SPEED_MPS = 20.0 * 1000 / 3600  # pseudo.res.Speed.CAR
DISTANCE_CHANGE_BANDS_KM = ((-5, "5km以上短く"), (-1, "1〜5km短く"), (1, "±1km以内"), (5, "1〜5km長く"), (float("inf"), "5km以上長く"))


def hourly_occupancy(stays: Sequence[tuple[int, int]]) -> dict[int, float]:
    """Visitor-hours inside the facility per clock hour (fractional overlap)."""
    occupancy: Counter[int] = Counter()
    for start, end in stays:
        if end <= start:
            continue
        for hour in range(start // 3600, (end - 1) // 3600 + 1):
            overlap = min(end, (hour + 1) * 3600) - max(start, hour * 3600)
            if overlap > 0:
                occupancy[hour] += overlap / 3600
    return dict(sorted(occupancy.items()))


def chain_km(rows: Sequence[Sequence[str]]) -> float:
    return sum(
        haversine_m(float(a[7]), float(a[8]), float(b[7]), float(b[8]))
        for a, b in zip(rows, rows[1:])
    ) / 1000


def change_band(km: float) -> str:
    return next(label for limit, label in DISTANCE_CHANGE_BANDS_KM if km < limit)


def scenario_effects(baseline_root: Path, scenario_root: Path) -> dict[str, object]:
    baseline = read_activity_files(baseline_root)
    scenario = read_activity_files(scenario_root)
    stays: list[tuple[int, int]] = []
    people = 0
    before_day = after_day = before_in = after_in = before_out = after_out = 0.0
    day_change_bands: Counter[str] = Counter()
    for base_file, scenario_file in zip(baseline, scenario):
        persons: dict[str, list[int]] = {}
        for index, fields in enumerate(scenario_file.rows):
            persons.setdefault(fields[0], []).append(index)
        for indexes in persons.values():
            after = [scenario_file.rows[i] for i in indexes]
            before = [base_file.rows[i] for i in indexes]
            moved = [
                k for k, (b, a) in enumerate(zip(before, after))
                if int(a[6]) == SHOPPING_PURPOSE and is_facility(a) and b[7:9] != a[7:9]
            ]
            for k, a in enumerate(after):
                if int(a[6]) != SHOPPING_PURPOSE or not is_facility(a):
                    continue
                start, end = int(a[4]), int(a[4]) + int(a[5])
                if k + 1 < len(after):
                    outbound = haversine_m(float(a[7]), float(a[8]), float(after[k + 1][7]), float(after[k + 1][8]))
                    end -= int(outbound / STAY_SPEED_MPS)
                stays.append((start, max(start, end)))
            if not moved:
                continue
            people += 1
            day_before, day_after = chain_km(before), chain_km(after)
            before_day += day_before
            after_day += day_after
            day_change_bands[change_band(day_after - day_before)] += 1
            for k in moved:
                before_in += haversine_m(float(before[k - 1][7]), float(before[k - 1][8]), float(before[k][7]), float(before[k][8])) / 1000
                after_in += haversine_m(float(after[k - 1][7]), float(after[k - 1][8]), float(after[k][7]), float(after[k][8])) / 1000
                if k + 1 < len(after):
                    before_out += haversine_m(float(before[k][7]), float(before[k][8]), float(before[k + 1][7]), float(before[k + 1][8])) / 1000
                    after_out += haversine_m(float(after[k][7]), float(after[k][8]), float(after[k + 1][7]), float(after[k + 1][8])) / 1000
    occupancy = hourly_occupancy(stays)
    stay_minutes = sorted((end - start) / 60 for start, end in stays)
    peak_hour = max(occupancy, key=occupancy.get) if occupancy else None
    return {
        "sample_factor": SAMPLE_FACTOR,
        "facility_arrivals": len(stays) * SAMPLE_FACTOR,
        "stay_minutes": {
            "median": stay_minutes[len(stay_minutes) // 2] if stay_minutes else 0,
            "p90": stay_minutes[int(0.9 * (len(stay_minutes) - 1))] if stay_minutes else 0,
        },
        "visitors_still_inside_at_21_full_scale": sum(1 for start, end in stays if start < 21 * 3600 < end) * SAMPLE_FACTOR,
        "hourly_occupancy_full_scale": {str(h): round(v * SAMPLE_FACTOR) for h, v in occupancy.items()},
        "peak_hour": peak_hour,
        "peak_occupancy_full_scale": round(occupancy[peak_hour] * SAMPLE_FACTOR) if peak_hour is not None else 0,
        "moved_people": people,
        "mean_day_km": {"before": before_day / people, "after": after_day / people} if people else {},
        "total_day_km_full_scale": {"before": before_day * SAMPLE_FACTOR, "after": after_day * SAMPLE_FACTOR},
        "mean_trip_into_shopping_km": {"before": before_in / people, "after": after_in / people} if people else {},
        "mean_trip_out_of_shopping_km": {"before": before_out / people, "after": after_out / people} if people else {},
        "day_distance_change_share": {
            label: day_change_bands.get(label, 0) / people if people else 0.0
            for _, label in DISTANCE_CHANGE_BANDS_KM
        },
    }


def write_chart(effects: dict[str, object], output_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for family in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP"):
        if any(font.name == family for font in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = family
            break
    occupancy = effects["hourly_occupancy_full_scale"]
    bands = effects["day_distance_change_share"]
    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 5.5))
    left.bar([int(h) for h in occupancy], list(occupancy.values()))
    left.set_xlabel("時刻")
    left.set_ylabel("跡地にいる人数（全数換算、1時間平均）")
    left.set_title(f"時間帯別の滞在人数（ピーク {effects['peak_hour']}時台 約{effects['peak_occupancy_full_scale']:,}人）")
    right.bar(list(bands), [100 * v for v in bands.values()])
    right.set_ylabel("来訪者の割合（%）")
    before, after = effects["mean_day_km"]["before"], effects["mean_day_km"]["after"]
    right.set_title(f"1日の移動距離の変化（平均 {before:.1f}km → {after:.1f}km）")
    figure.tight_layout()
    for suffix in ("png", "svg"):
        figure.savefig(output_dir / f"scenario_effects.{suffix}", dpi=200)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chart", action="store_true")
    args = parser.parse_args(argv)
    effects = scenario_effects(args.baseline_dir / "23", args.scenario_dir / "23")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "scenario_effects.json").write_text(
        json.dumps(effects, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.chart:
        write_chart(effects, args.output_dir)
    print(json.dumps({k: v for k, v in effects.items() if k != "hourly_occupancy_full_scale"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
