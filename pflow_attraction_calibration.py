#!/usr/bin/env python3
"""Calibrate the shopping-attraction coefficient of the destination-city MNL.

Simplified approach A: Pseudo-PFLOW-v3 can add ``beta * ln(1 + shopping mesh
capacity in the city)`` to the destination-city MNL for shopping
(``--shopping-attraction-beta``). This script compares baseline runs made with
several beta values against Chukyo PT shopping trips (Aichi only, common
comparison units):

* origins of shopping arrivals in Nakamura Ward (the calibration target), and
* destinations of all shopping trips in Aichi (side effects elsewhere),
* the share of shopping trips that stay in the same unit, and the mean
  straight-line shopping trip length.

This is a one-parameter calibration, not an estimation of the MNL.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

import chukyo_pt_origin_distribution as pt
from pflow_capacity_analysis import SHOPPING_PURPOSE, TARGET_GCODE, _iter_rows
from pflow_origin_comparison import (
    AICHI_MUNICIPALITIES,
    OUTSIDE_AICHI,
    ComparisonUnits,
    normalize,
    total_variation_distance,
)
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_GRID_ROOT = PROJECT_DIR / ".local" / "pflow" / "output" / "attraction_grid"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "pflow_attraction_calibration"
SHOPPING_PURPOSES = ("日常的な家事・買物", "日常的でない買物")
NAKAMURA = AICHI_MUNICIPALITIES[TARGET_GCODE]


def pt_shopping_counts(pt_csv: Path, units: ComparisonUnits) -> dict[str, object]:
    """PT shopping trips with both ends in Aichi, by destination and for Nakamura's origins."""
    table = pt.read_pt_table(pt_csv)
    destinations: Counter[str] = Counter()
    nakamura_origins: Counter[str] = Counter()
    same_unit = 0
    for row in table.rows:
        if row[pt.PURPOSE_COLUMN] not in SHOPPING_PURPOSES:
            continue
        count = pt.parse_count(row[pt.TOTAL_COLUMN], column=pt.TOTAL_COLUMN, line_no=int(row["__line__"]))
        origin = units.unit_for_zone(pt.normalize_zone(row[pt.ORIGIN_COLUMN]))
        destination = units.unit_for_zone(pt.normalize_zone(row[pt.DESTINATION_COLUMN]))
        if OUTSIDE_AICHI in (origin, destination) or not count:
            continue
        destinations[destination] += count
        same_unit += count if origin == destination else 0
        if destination == NAKAMURA:
            nakamura_origins[origin] += count
    return {"destinations": destinations, "nakamura_origins": nakamura_origins, "same_unit": same_unit}


def pflow_shopping_counts(run_dir: Path, units: ComparisonUnits) -> dict[str, object]:
    destinations: Counter[str] = Counter()
    nakamura_origins: Counter[str] = Counter()
    same_unit = trips = 0
    distance_m = 0.0
    for csv_path in sorted(path for path in run_dir.rglob("*.csv") if path.is_file()):
        previous_by_person: dict[int, dict[str, object]] = {}
        for row in _iter_rows(csv_path):
            person_id = int(row["person_id"])
            previous = previous_by_person.get(person_id)
            previous_by_person[person_id] = row
            if int(row["purpose"]) != SHOPPING_PURPOSE or previous is None:
                continue
            origin = units.unit_for_gcode(str(previous["gcode"]))
            destination = units.unit_for_gcode(str(row["gcode"]))
            if OUTSIDE_AICHI in (origin, destination):
                continue
            trips += 1
            destinations[destination] += 1
            same_unit += origin == destination
            distance_m += haversine_m(previous["lon"], previous["lat"], row["lon"], row["lat"])
            if str(row["gcode"]) == TARGET_GCODE:
                nakamura_origins[origin] += 1
    return {
        "destinations": destinations,
        "nakamura_origins": nakamura_origins,
        "same_unit_share": same_unit / trips if trips else 0.0,
        "mean_trip_km": distance_m / trips / 1000 if trips else 0.0,
        "trips": trips,
        "nakamura_arrivals": sum(nakamura_origins.values()),
    }


def evaluate(grid: Mapping[float, Path], units: ComparisonUnits, pt_counts) -> list[dict[str, float]]:
    pt_destinations = normalize(pt_counts["destinations"])
    pt_nakamura = normalize(pt_counts["nakamura_origins"])
    pt_same = pt_counts["same_unit"] / sum(pt_counts["destinations"].values())
    rows = []
    for beta, run_dir in sorted(grid.items()):
        counts = pflow_shopping_counts(run_dir, units)
        rows.append({
            "beta": beta,
            "tvd_nakamura_origins": total_variation_distance(normalize(counts["nakamura_origins"]), pt_nakamura),
            "tvd_destinations": total_variation_distance(normalize(counts["destinations"]), pt_destinations),
            "same_unit_share": counts["same_unit_share"],
            "pt_same_unit_share": pt_same,
            "mean_trip_km": counts["mean_trip_km"],
            "nakamura_arrivals": counts["nakamura_arrivals"],
            "nakamura_share_of_destinations": counts["destinations"][NAKAMURA] / counts["trips"],
            "pt_nakamura_share_of_destinations": pt_destinations.get(NAKAMURA, 0.0),
        })
    return rows


def write_chart(rows: Sequence[Mapping[str, float]], output_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for family in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP"):
        if any(font.name == family for font in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = family
            break
    betas = [row["beta"] for row in rows]
    figure, (left, right) = plt.subplots(1, 2, figsize=(12, 5))
    left.plot(betas, [row["tvd_nakamura_origins"] for row in rows], marker="o", label="中村区への買物の出発地")
    left.plot(betas, [row["tvd_destinations"] for row in rows], marker="s", label="愛知県全体の買物の行き先")
    left.set_xlabel("商業量の係数 β")
    left.set_ylabel("中京PTとのずれ（TVD）")
    left.set_title("係数βと中京PTとのずれ")
    left.legend()
    right.plot(betas, [100 * row["same_unit_share"] for row in rows], marker="o", label="Pseudo-PFLOW")
    right.axhline(100 * rows[0]["pt_same_unit_share"], color="gray", linestyle="--", label="中京PT")
    right.set_xlabel("商業量の係数 β")
    right.set_ylabel("同じ市区町村内で買物する割合（%）")
    right.set_title("地元で買物する割合")
    right.legend()
    figure.tight_layout()
    for suffix in ("png", "svg"):
        figure.savefig(output_dir / f"attraction_calibration.{suffix}", dpi=200)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid-root", type=Path, default=DEFAULT_GRID_ROOT, help="contains beta<value>/baseline/23")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pt-csv", type=Path, default=pt.DEFAULT_INPUT)
    parser.add_argument("--zone-xlsx", type=Path, default=pt.DEFAULT_ZONE_CODE_XLSX)
    args = parser.parse_args(argv)

    grid = {}
    for directory in sorted(args.grid_root.glob("beta*")):
        match = re.fullmatch(r"beta([0-9.]+)", directory.name)
        run_dir = directory / "baseline"
        if match and (run_dir / ".complete").exists():
            grid[float(match.group(1))] = run_dir / "23"
    if not grid:
        raise FileNotFoundError(f"No completed beta runs under {args.grid_root}")
    units = ComparisonUnits(pt.load_middle_zone_labels(args.zone_xlsx))
    rows = evaluate(grid, units, pt_shopping_counts(args.pt_csv, units))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "attraction_calibration.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_chart(rows, args.output_dir)
    for row in rows:
        print(json.dumps({key: round(value, 4) for key, value in row.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
