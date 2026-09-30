#!/usr/bin/env python3
"""Aggregate deterministic Pseudo-PFLOW capacity-sweep activity CSVs."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

SHOPPING_PURPOSE = 100
TARGET_MESH = "52366700"
TARGET_GCODE = "23105"
TARGET_LON = 136.88397984
TARGET_LAT = 35.16967402
FACILITY_TOLERANCE = 1e-6


def third_mesh_code(lon: float, lat: float) -> str:
    first_lat = int(lat * 1.5)
    first_lon = int(lon) - 100
    lat_minutes = lat * 60 - first_lat * 40
    lon_minutes = (lon - int(lon)) * 60
    second_lat = int(lat_minutes / 5)
    second_lon = int(lon_minutes / 7.5)
    lat_seconds = (lat_minutes - second_lat * 5) * 60
    lon_seconds = (lon_minutes - second_lon * 7.5) * 60
    third_lat = int(lat_seconds / 30)
    third_lon = int(lon_seconds / 45)
    return f"{first_lat:02d}{first_lon:02d}{second_lat}{second_lon}{third_lat}{third_lon}"


def _iter_rows(csv_path: Path) -> Iterable[dict[str, object]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for line_number, row in enumerate(reader, 1):
            if len(row) != 10:
                raise ValueError(f"{csv_path}:{line_number}: expected 10 columns, got {len(row)}")
            yield {
                "person_id": int(row[0]), "age": int(row[1]), "gender": int(row[2]),
                "labor": int(row[3]), "start": int(row[4]), "duration": int(row[5]),
                "purpose": int(row[6]), "lon": float(row[7]), "lat": float(row[8]),
                "gcode": row[9],
            }


def _time_band(seconds: int) -> str:
    hour = seconds // 3600
    if hour < 6:
        return "00-05"
    if hour < 10:
        return "06-09"
    if hour < 16:
        return "10-15"
    if hour < 20:
        return "16-19"
    return "20-23"


def summarize_scenario(scenario_dir: Path) -> dict[str, object]:
    metrics: dict[str, object] = {
        "scenario_id": scenario_dir.name,
        "shopping_total": 0,
        "target_facility": 0,
        "target_mesh": 0,
        "target_mesh_other_facilities": 0,
        "target_admin_shopping": 0,
    }
    inflow: Counter[str] = Counter()
    age: Counter[str] = Counter()
    gender: Counter[str] = Counter()
    labor: Counter[str] = Counter()
    time_band: Counter[str] = Counter()
    csv_files = sorted(path for path in scenario_dir.rglob("*.csv") if path.is_file())
    if not csv_files:
        raise FileNotFoundError(f"No activity CSVs under {scenario_dir}")

    for csv_path in csv_files:
        previous_by_person: dict[int, dict[str, object]] = {}
        for row in _iter_rows(csv_path):
            person_id = int(row["person_id"])
            previous = previous_by_person.get(person_id)
            if int(row["purpose"]) == SHOPPING_PURPOSE:
                metrics["shopping_total"] = int(metrics["shopping_total"]) + 1
                if str(row["gcode"]) == TARGET_GCODE:
                    metrics["target_admin_shopping"] = int(metrics["target_admin_shopping"]) + 1
                is_target_mesh = third_mesh_code(float(row["lon"]), float(row["lat"])) == TARGET_MESH
                is_target_facility = (
                    abs(float(row["lon"]) - TARGET_LON) <= FACILITY_TOLERANCE
                    and abs(float(row["lat"]) - TARGET_LAT) <= FACILITY_TOLERANCE
                )
                if is_target_mesh:
                    metrics["target_mesh"] = int(metrics["target_mesh"]) + 1
                    age[str(row["age"])] += 1
                    gender[str(row["gender"])] += 1
                    labor[str(row["labor"])] += 1
                    time_band[_time_band(int(row["start"]))] += 1
                    inflow[str(previous["gcode"]) if previous else "NO_PREVIOUS"] += 1
                if is_target_facility:
                    metrics["target_facility"] = int(metrics["target_facility"]) + 1
            previous_by_person[person_id] = row

    metrics["target_mesh_other_facilities"] = int(metrics["target_mesh"]) - int(metrics["target_facility"])
    metrics["target_mesh_inflow_by_previous_gcode"] = dict(sorted(inflow.items()))
    metrics["target_mesh_age"] = dict(sorted(age.items(), key=lambda item: int(item[0])))
    metrics["target_mesh_gender"] = dict(sorted(gender.items()))
    metrics["target_mesh_labor"] = dict(sorted(labor.items()))
    metrics["target_mesh_time_band"] = dict(sorted(time_band.items()))
    metrics["csv_file_count"] = len(csv_files)
    return metrics


def write_sensitivity_chart(results: list[dict[str, object]], output_dir: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("Matplotlib is unavailable; skipped sensitivity chart")
        return

    by_id = {str(row["scenario_id"]): row for row in results}
    required = {
        "baseline", "facility_1x", "facility_10x", "facility_100x",
        "mesh_2x", "mesh_5x", "mesh_10x",
        "combined_mesh_2x_facility_10x", "combined_mesh_5x_facility_10x",
        "combined_mesh_10x_facility_10x",
    }
    if not required.issubset(by_id):
        return

    figure, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    mesh_x = [1, 2, 5, 10]
    mesh_y = [int(by_id[name]["target_mesh"]) for name in
              ["baseline", "mesh_2x", "mesh_5x", "mesh_10x"]]
    axes[0].plot(mesh_x, mesh_y, marker="o")
    axes[0].set(title="Mesh capacity sensitivity", xlabel="Mesh multiplier", ylabel="Shopping arrivals")
    axes[0].grid(alpha=0.3)

    facility_x = [10_000, 100_000, 1_000_000]
    facility_y = [int(by_id[name]["target_facility"]) for name in
                  ["facility_1x", "facility_10x", "facility_100x"]]
    axes[1].plot(facility_x, facility_y, marker="o", color="#d55e00")
    axes[1].set_xscale("log")
    axes[1].set(title="Facility capacity sensitivity", xlabel="Injected facility capacity", ylabel="Facility arrivals")
    axes[1].grid(alpha=0.3)

    combined_names = ["facility_10x", "combined_mesh_2x_facility_10x",
                      "combined_mesh_5x_facility_10x", "combined_mesh_10x_facility_10x"]
    combined_mesh = [int(by_id[name]["target_mesh"]) for name in combined_names]
    combined_facility = [int(by_id[name]["target_facility"]) for name in combined_names]
    axes[2].plot(mesh_x, combined_mesh, marker="o", label="Target mesh")
    axes[2].plot(mesh_x, combined_facility, marker="s", label="Injected facility")
    axes[2].set(title="Combined (facility capacity 100,000)", xlabel="Mesh multiplier", ylabel="Shopping arrivals")
    axes[2].grid(alpha=0.3)
    axes[2].legend()

    figure.suptitle("Aichi 2% sample, seed 42")
    figure.tight_layout()
    figure.savefig(output_dir / "capacity_sensitivity.png", dpi=180, bbox_inches="tight")
    figure.savefig(output_dir / "capacity_sensitivity.svg", bbox_inches="tight")
    plt.close(figure)

def analyze(input_root: Path, output_dir: Path) -> list[dict[str, object]]:
    scenario_dirs = sorted(
        path for path in input_root.iterdir()
        if path.is_dir() and (path / ".complete").is_file()
    )
    results = [summarize_scenario(path) for path in scenario_dirs if list(path.rglob("*.csv"))]
    if not results:
        raise FileNotFoundError(f"No scenario activity CSVs under {input_root}")
    baseline = next((row for row in results if row["scenario_id"] == "baseline"), None)
    comparison_keys = ("target_facility", "target_mesh", "target_mesh_other_facilities", "target_admin_shopping")
    for row in results:
        if baseline:
            row["delta_from_baseline"] = {
                key: int(row[key]) - int(baseline[key]) for key in comparison_keys
            }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "capacity_sweep_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "capacity_sweep_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["scenario_id", "shopping_total", *comparison_keys, "csv_file_count"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({key: row[key] for key in fieldnames})
    write_sensitivity_chart(results, output_dir)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    results = analyze(args.input_root, args.output_dir)
    print(f"Wrote {len(results)} scenario summaries to {args.output_dir}")


if __name__ == "__main__":
    main()
