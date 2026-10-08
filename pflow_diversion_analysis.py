#!/usr/bin/env python3
"""Where do the new facility's shoppers come from, in terms of destinations?

Compares an adjusted scenario with its baseline run row by row. Capacity
scenarios keep every person's activity sequence, so any shopping row whose
location differs from the baseline is a diverted shopping activity. Each one is
classified by where it went (the new facility, another shop in the target mesh,
elsewhere) and attributed to the district and retail store it left.

Retail store names come from ``city_retail.csv`` (TelePoint), using the same
Tokyo Datum to WGS84 shift as ``pseudo.acs.DataAccessor``.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from pflow_capacity_analysis import SHOPPING_PURPOSE, TARGET_LAT, TARGET_LON, TARGET_MESH, third_mesh_code
from pflow_combined_origin_adjustment import is_facility, read_activity_files
from pflow_origin_comparison import AICHI_MUNICIPALITIES
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PFLOW_HOME = PROJECT_DIR / ".local" / "pflow"
TOKYO_TO_WGS84_DLON = -0.000293000
TOKYO_TO_WGS84_DLAT = +0.000106950
SAMPLE_FACTOR = 50
DISTANCE_BANDS_M = ((500, "0〜500m"), (1000, "500m〜1km"), (3000, "1〜3km"), (10000, "3〜10km"), (float("inf"), "10km以上"))


def distance_band(meters: float) -> str:
    return next(label for limit, label in DISTANCE_BANDS_M if meters < limit)


def load_retail_names(path: Path, gcode_prefix: str = "23") -> dict[tuple[str, str], tuple[str, str]]:
    """Map rounded WGS84 coordinates to (store name, the store's own municipality code)."""
    names: dict[tuple[str, str], tuple[str, str]] = {}
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if len(row) <= 21 or not row[7].startswith(gcode_prefix):
                continue
            try:
                lon = float(row[20]) + TOKYO_TO_WGS84_DLON
                lat = float(row[21]) + TOKYO_TO_WGS84_DLAT
            except ValueError:
                continue
            names.setdefault((f"{lon:.6f}", f"{lat:.6f}"), (row[0], row[7]))
    return names


def diversions(baseline_root: Path, scenario_root: Path, retail_names: Mapping[tuple[str, str], tuple[str, str]]):
    baseline = read_activity_files(baseline_root)
    scenario = read_activity_files(scenario_root)
    if [f.relative_path for f in baseline] != [f.relative_path for f in scenario]:
        raise ValueError("Baseline and scenario runs have different activity files")
    by_kind: Counter[str] = Counter()
    lost_district: Counter[str] = Counter()
    lost_store: Counter[str] = Counter()
    lost_store_district: dict[str, str] = {}
    lost_distance: Counter[str] = Counter()
    for base_file, scenario_file in zip(baseline, scenario):
        if len(base_file.rows) != len(scenario_file.rows):
            raise ValueError(f"{base_file.relative_path}: row counts differ")
        for before, after in zip(base_file.rows, scenario_file.rows):
            if before[:7] != after[:7]:
                raise ValueError(f"{base_file.relative_path}: activity sequences differ")
            if int(after[6]) != SHOPPING_PURPOSE or before[7:9] == after[7:9]:
                continue
            if is_facility(after):
                kind = "new_facility"
            elif third_mesh_code(float(after[7]), float(after[8])) == TARGET_MESH:
                kind = "other_store_in_target_mesh"
            else:
                kind = "elsewhere"
            by_kind[kind] += 1
            if kind != "new_facility":
                continue
            # The row's gcode is the city Pseudo-PFLOW chose; meshes on a boundary belong
            # to several cities, so prefer the store's own municipality when it is known.
            store, store_gcode = retail_names.get((before[7], before[8]), (None, before[9]))
            district = AICHI_MUNICIPALITIES.get(store_gcode, store_gcode)
            lost_district[district] += 1
            store = store or f"名称不明（{before[7]}, {before[8]}）"
            lost_store[store] += 1
            lost_store_district[store] = district
            meters = haversine_m(float(before[7]), float(before[8]), TARGET_LON, TARGET_LAT)
            lost_distance[distance_band(meters)] += 1
    return {
        "changed_shopping_rows_by_destination": dict(by_kind),
        "new_facility_visitors_previous_district": dict(lost_district.most_common()),
        "new_facility_visitors_previous_distance": {
            label: lost_distance.get(label, 0) for _, label in DISTANCE_BANDS_M
        },
        "new_facility_visitors_previous_store": [
            {"store": store, "district": lost_store_district[store], "count": count}
            for store, count in lost_store.most_common(30)
        ],
    }


def write_chart(result: Mapping[str, object], output_dir: Path, top_n: int = 15) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for family in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP"):
        if any(font.name == family for font in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = family
            break
    districts = list(result["new_facility_visitors_previous_district"].items())[:top_n]
    bands = list(result["new_facility_visitors_previous_distance"].items())
    total = sum(result["new_facility_visitors_previous_district"].values())
    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 6.5))
    left.barh([name for name, _ in districts][::-1], [count * SAMPLE_FACTOR for _, count in districts][::-1])
    left.set_xlabel("跡地へ移る買物トリップ数（全数換算）")
    left.set_title("元の買物先の市区町村")
    right.bar([label for label, _ in bands], [count * SAMPLE_FACTOR for _, count in bands])
    right.set_ylabel("跡地へ移る買物トリップ数（全数換算）")
    right.set_title("元の買物先から跡地までの距離")
    figure.suptitle(f"名鉄跡地の来訪者は、どこで買物をしていた人か（計{total * SAMPLE_FACTOR:,}トリップ/日）")
    figure.tight_layout()
    for suffix in ("png", "svg"):
        figure.savefig(output_dir / f"diversion.{suffix}", dpi=200)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pflow-home", type=Path, default=DEFAULT_PFLOW_HOME)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chart", action="store_true")
    args = parser.parse_args(argv)

    retail_names = load_retail_names(args.pflow_home / "data" / "facilities" / "city_retail.csv")
    result = diversions(args.baseline_dir / "23", args.scenario_dir / "23", retail_names)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "diversion.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.chart:
        write_chart(result, args.output_dir)
    print(json.dumps(result["changed_shopping_rows_by_destination"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
