#!/usr/bin/env python3
"""Estimate one-shot Pseudo-PFLOW capacity settings for a target arrival count.

The deterministic capacity sweep is inverted in two stages:
1. target-admin -> target-mesh share: logistic regression on log(mesh multiplier)
2. target-mesh -> injected-facility share: capacity-share roulette equation
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DEMAND_JSON = BASE_DIR / "config/development_scenarios/meitetsu_origin_distribution_commercial.json"
DEFAULT_SUMMARY = BASE_DIR / ".local/pflow/output/capacity_experiment/summary/capacity_sweep_summary.csv"
DEFAULT_SCENARIO_DIR = BASE_DIR / "config/pflow_capacity"
DEFAULT_OUTPUT_DIR = BASE_DIR / ".local/pflow/calibration"
DEFAULT_OUTPUT_SCENARIO = DEFAULT_OUTPUT_DIR / "calibrated_target.json"
DEFAULT_REPORT = DEFAULT_OUTPUT_DIR / "calibration_report.json"
DEFAULT_CALIBRATION_SAMPLE_FACTOR = 50
DEFAULT_FACILITY_SHARE = 0.70
DEFAULT_MAX_MESH_SHARE = 0.95
FACILITY_CAPACITY_ROUNDING = 10_000
TARGET_SCENARIO_TEMPLATE = "facility_10x"


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def load_target_arrivals(
    path: Path,
    scenario_id: str = "all_commercial",
    facility_use: str = "commercial",
) -> tuple[int, str]:
    """Read a target from either the bridge config or Nagoya distribution JSON."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "target_arrival_trip_count" in payload:
        value = int(_positive_number(
            payload["target_arrival_trip_count"], "target_arrival_trip_count"
        ))
        source = str(payload.get("target_arrival_trip_count_source") or path)
        return value, source

    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("Demand JSON needs target_arrival_trip_count or a scenarios list")
    scenario = next((item for item in scenarios if str(item.get("id")) == scenario_id), None)
    if scenario is None:
        raise ValueError(f"Demand scenario not found: {scenario_id}")
    facilities = scenario.get("age_gender_distribution", {}).get("facilities", [])
    facility = next((item for item in facilities if str(item.get("key")) == facility_use), None)
    if facility is None:
        raise ValueError(f"Facility use {facility_use!r} not found in {scenario_id}")
    value = int(_positive_number(facility.get("estimated_arrivals"), "estimated_arrivals"))
    return value, f"{path} / {scenario_id} / {facility_use} / estimated_arrivals"


def load_sweep_summary(path: Path) -> list[dict[str, float | str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"Capacity sweep summary is empty: {path}")
    numeric_fields = (
        "shopping_total", "target_facility", "target_mesh",
        "target_mesh_other_facilities", "target_admin_shopping",
    )
    converted: list[dict[str, float | str]] = []
    for row in rows:
        item: dict[str, float | str] = {"scenario_id": str(row["scenario_id"])}
        for field in numeric_fields:
            item[field] = float(row[field])
        converted.append(item)
    return converted


def load_capacity_scenarios(directory: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = str(payload.get("scenarioId") or "")
        if scenario_id:
            result[scenario_id] = payload
    if not result:
        raise ValueError(f"No capacity scenarios found under {directory}")
    return result


def _logit(probability: float) -> float:
    if not 0 < probability < 1:
        raise ValueError(f"Probability must be between zero and one: {probability}")
    return math.log(probability / (1.0 - probability))


def _logistic(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def fit_mesh_response(
    rows: list[dict[str, float | str]],
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Fit logit(mesh/admin share) = intercept + slope * log(multiplier)."""
    points: list[dict[str, float | str]] = []
    for row in rows:
        scenario_id = str(row["scenario_id"])
        scenario = scenarios.get(scenario_id)
        if not scenario or bool(scenario.get("injectFacility")):
            continue
        admin = float(row["target_admin_shopping"])
        mesh = float(row["target_mesh"])
        multiplier = float(scenario["meshCapacityMultiplier"])
        if admin <= 0 or not 0 < mesh < admin or multiplier <= 0:
            continue
        points.append({
            "scenario_id": scenario_id,
            "multiplier": multiplier,
            "mesh_share": mesh / admin,
            "x": math.log(multiplier),
            "y": _logit(mesh / admin),
        })
    if len(points) < 3:
        raise ValueError("At least three mesh-only sweep points are required")

    mean_x = statistics.fmean(float(point["x"]) for point in points)
    mean_y = statistics.fmean(float(point["y"]) for point in points)
    denominator = sum((float(point["x"]) - mean_x) ** 2 for point in points)
    if denominator == 0:
        raise ValueError("Mesh multipliers do not vary")
    slope = sum(
        (float(point["x"]) - mean_x) * (float(point["y"]) - mean_y)
        for point in points
    ) / denominator
    if slope <= 0:
        raise ValueError("Mesh response must increase with its multiplier")
    intercept = mean_y - slope * mean_x
    predictions = [intercept + slope * float(point["x"]) for point in points]
    residuals = [float(point["y"]) - prediction for point, prediction in zip(points, predictions)]
    ss_res = sum(value * value for value in residuals)
    ss_tot = sum((float(point["y"]) - mean_y) ** 2 for point in points)
    return {
        "formula": "logit(mesh_share) = intercept + slope * ln(mesh_multiplier)",
        "intercept": intercept,
        "slope": slope,
        "r_squared": 1.0 - ss_res / ss_tot if ss_tot else 1.0,
        "rmse_logit": math.sqrt(ss_res / len(points)),
        "points": [
            {key: point[key] for key in ("scenario_id", "multiplier", "mesh_share")}
            for point in points
        ],
    }


def estimate_existing_facility_capacity(
    rows: list[dict[str, float | str]],
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Infer competing capacity B from facility_share = C / (B + C)."""
    estimates: list[dict[str, float | str]] = []
    for row in rows:
        scenario_id = str(row["scenario_id"])
        scenario = scenarios.get(scenario_id)
        if not scenario or not bool(scenario.get("injectFacility")):
            continue
        facility = float(row["target_facility"])
        mesh = float(row["target_mesh"])
        if facility <= 0 or mesh <= facility:
            continue
        capacity = _positive_number(
            scenario.get("facility", {}).get("capacity"),
            f"{scenario_id}.facility.capacity",
        )
        competing_capacity = capacity * (mesh - facility) / facility
        estimates.append({
            "scenario_id": scenario_id,
            "injected_capacity": capacity,
            "observed_facility_share": facility / mesh,
            "estimated_competing_capacity": competing_capacity,
        })
    if len(estimates) < 2:
        raise ValueError("At least two non-zero facility sweep points are required")
    values = [float(item["estimated_competing_capacity"]) for item in estimates]
    return {
        "formula": "facility_share = capacity / (competing_capacity + capacity)",
        "competing_capacity_median": statistics.median(values),
        "competing_capacity_min": min(values),
        "competing_capacity_max": max(values),
        "points": estimates,
    }


def _round_to(value: float, unit: int) -> int:
    return max(unit, int(math.floor(value / unit + 0.5)) * unit)


def calibrate_capacity(
    rows: list[dict[str, float | str]],
    scenarios: dict[str, dict[str, Any]],
    target_arrivals: int,
    calibration_sample_factor: int = DEFAULT_CALIBRATION_SAMPLE_FACTOR,
    preferred_facility_share: float = DEFAULT_FACILITY_SHARE,
    max_mesh_share: float = DEFAULT_MAX_MESH_SHARE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a Pseudo-PFLOW scenario and its calibration audit report."""
    target = int(_positive_number(target_arrivals, "target_arrivals"))
    factor = int(_positive_number(calibration_sample_factor, "calibration_sample_factor"))
    if not 0 < preferred_facility_share < 1:
        raise ValueError("preferred_facility_share must be between zero and one")
    if not 0 < max_mesh_share < 1:
        raise ValueError("max_mesh_share must be between zero and one")

    by_id = {str(row["scenario_id"]): row for row in rows}
    baseline = by_id.get("baseline")
    if baseline is None:
        raise ValueError("baseline is required in the sweep summary")
    admin_arrivals = float(baseline["target_admin_shopping"])
    if admin_arrivals <= 0:
        raise ValueError("baseline target_admin_shopping must be positive")

    target_at_sample = target / factor
    safe_max = admin_arrivals * max_mesh_share
    if target_at_sample >= safe_max:
        raise ValueError(
            "Target is not reachable by capacities inside the current target admin: "
            f"sample target={target_at_sample:.3f}, safe maximum={safe_max:.3f}. "
            "Change the admin-choice model or use post-generation adjustment."
        )

    minimum_facility_share = target_at_sample / safe_max
    facility_share = max(preferred_facility_share, minimum_facility_share)
    if facility_share >= 0.99:
        raise ValueError("Required facility share is too close to one for stable calibration")
    desired_mesh_arrivals = target_at_sample / facility_share
    desired_mesh_share = desired_mesh_arrivals / admin_arrivals

    mesh_model = fit_mesh_response(rows, scenarios)
    raw_multiplier = math.exp(
        (_logit(desired_mesh_share) - float(mesh_model["intercept"]))
        / float(mesh_model["slope"])
    )
    mesh_multiplier = round(raw_multiplier, 3)

    facility_model = estimate_existing_facility_capacity(rows, scenarios)
    competing_capacity = float(facility_model["competing_capacity_median"])
    raw_facility_capacity = competing_capacity * facility_share / (1.0 - facility_share)
    facility_capacity = _round_to(raw_facility_capacity, FACILITY_CAPACITY_ROUNDING)

    predicted_mesh_share = _logistic(
        float(mesh_model["intercept"])
        + float(mesh_model["slope"]) * math.log(mesh_multiplier)
    )
    predicted_mesh_arrivals = admin_arrivals * predicted_mesh_share
    predicted_facility_share = facility_capacity / (competing_capacity + facility_capacity)
    predicted_sample_arrivals = predicted_mesh_arrivals * predicted_facility_share
    predicted_full_arrivals = predicted_sample_arrivals * factor

    template = scenarios.get(TARGET_SCENARIO_TEMPLATE)
    if template is None or not template.get("facility"):
        raise ValueError(f"Scenario template is missing: {TARGET_SCENARIO_TEMPLATE}")
    scenario = {
        "scenarioId": f"calibrated_target_{target}",
        "transition": "SHOPPING",
        "target": dict(template["target"]),
        "meshCapacityMultiplier": mesh_multiplier,
        "injectFacility": True,
        "facility": {
            "id": int(template["facility"]["id"]),
            "longitude": float(template["facility"]["longitude"]),
            "latitude": float(template["facility"]["latitude"]),
            "capacity": float(facility_capacity),
        },
    }

    report = {
        "schema_version": 1,
        "method": "one_shot_hierarchical_capacity_calibration",
        "target": {
            "full_population_arrival_trips": target,
            "calibration_sample_factor": factor,
            "equivalent_calibration_sample_arrivals": target_at_sample,
            "unit": "arrival trips/day; not guaranteed unique people",
        },
        "assumptions": {
            "preferred_facility_share": preferred_facility_share,
            "minimum_feasible_facility_share": minimum_facility_share,
            "used_facility_share": facility_share,
            "max_mesh_share": max_mesh_share,
            "target_admin_arrivals_in_calibration_sample": admin_arrivals,
            "markov_activity_sequence_is_fixed": True,
            "admin_choice_is_not_changed_by_capacity": True,
        },
        "mesh_model": mesh_model,
        "facility_model": facility_model,
        "recommended": {
            "mesh_capacity_multiplier_raw": raw_multiplier,
            "mesh_capacity_multiplier": mesh_multiplier,
            "facility_capacity_raw": raw_facility_capacity,
            "facility_capacity": facility_capacity,
            "desired_mesh_arrivals_in_calibration_sample": desired_mesh_arrivals,
        },
        "prediction_after_rounding": {
            "mesh_share": predicted_mesh_share,
            "target_mesh_arrivals_in_calibration_sample": predicted_mesh_arrivals,
            "facility_share_within_target_mesh": predicted_facility_share,
            "target_facility_arrivals_in_calibration_sample": predicted_sample_arrivals,
            "scaled_full_population_arrivals": predicted_full_arrivals,
            "difference_from_target": predicted_full_arrivals - target,
        },
        "scenario": scenario,
        "warnings": [
            "This extrapolates beyond tested mesh multiplier and facility capacity.",
            "A deterministic run can differ because destination choices are discrete.",
            "If target exceeds target-admin demand, capacity alone cannot reach it.",
        ],
    }
    return scenario, report


def build_evaluation(
    report: dict[str, Any], actual_sample_arrivals: int, execution_sample_factor: int,
) -> dict[str, Any]:
    target = float(report["target"]["full_population_arrival_trips"])
    actual = int(actual_sample_arrivals)
    factor = int(_positive_number(execution_sample_factor, "execution_sample_factor"))
    scaled = actual * factor
    error = scaled - target
    return {
        "execution_sample_factor": factor,
        "actual_target_facility_arrivals": actual,
        "scaled_full_population_arrivals": scaled,
        "target_full_population_arrivals": target,
        "error_arrivals": error,
        "absolute_percentage_error": abs(error) / target * 100.0,
    }


def write_calibration(
    scenario: dict[str, Any], report: dict[str, Any], scenario_path: Path, report_path: Path,
) -> None:
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _evaluate_existing_run(args: argparse.Namespace) -> None:
    from pflow_capacity_analysis import summarize_scenario

    if args.scenario_output is None:
        raise ValueError("--scenario-output is required with --evaluate-only")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    actual = summarize_scenario(args.scenario_output)
    report["evaluation"] = build_evaluation(
        report, int(actual["target_facility"]), args.execution_sample_factor
    )
    report["evaluation"]["actual_target_mesh_arrivals"] = int(actual["target_mesh"])
    report["evaluation"]["actual_target_admin_arrivals"] = int(actual["target_admin_shopping"])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evaluation = report["evaluation"]
    print(
        f"Actual target arrivals: {evaluation['actual_target_facility_arrivals']:,} "
        f"at 1/{evaluation['execution_sample_factor']} sample; "
        f"scaled={evaluation['scaled_full_population_arrivals']:,}; "
        f"APE={evaluation['absolute_percentage_error']:.2f}%"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate capacities for a target Pseudo-PFLOW facility arrival count."
    )
    parser.add_argument("--target-arrivals", type=int)
    parser.add_argument("--demand-json", type=Path, default=DEFAULT_DEMAND_JSON)
    parser.add_argument("--demand-scenario", default="all_commercial")
    parser.add_argument("--facility-use", default="commercial")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scenario-dir", type=Path, default=DEFAULT_SCENARIO_DIR)
    parser.add_argument("--calibration-sample-factor", type=int, default=DEFAULT_CALIBRATION_SAMPLE_FACTOR)
    parser.add_argument("--facility-share", type=float, default=DEFAULT_FACILITY_SHARE)
    parser.add_argument("--output-scenario", type=Path, default=DEFAULT_OUTPUT_SCENARIO)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--scenario-output", type=Path)
    parser.add_argument("--execution-sample-factor", type=int, default=50)
    args = parser.parse_args()

    if args.evaluate_only:
        _evaluate_existing_run(args)
        return
    if args.target_arrivals is None:
        target_arrivals, target_source = load_target_arrivals(
            args.demand_json, args.demand_scenario, args.facility_use
        )
    else:
        target_arrivals = args.target_arrivals
        target_source = "command line --target-arrivals"
    scenario, report = calibrate_capacity(
        load_sweep_summary(args.summary),
        load_capacity_scenarios(args.scenario_dir),
        target_arrivals,
        args.calibration_sample_factor,
        args.facility_share,
    )
    report["sources"] = {
        "target": target_source,
        "demand_json": str(args.demand_json.resolve()),
        "capacity_sweep_summary": str(args.summary.resolve()),
        "capacity_scenario_directory": str(args.scenario_dir.resolve()),
    }
    write_calibration(scenario, report, args.output_scenario, args.report)
    prediction = report["prediction_after_rounding"]
    print(f"Target arrivals: {target_arrivals:,} trips/day")
    print(f"Mesh capacity multiplier: {scenario['meshCapacityMultiplier']:.3f}")
    print(f"Facility capacity: {scenario['facility']['capacity']:,.0f}")
    print(
        f"Predicted arrivals: {prediction['target_facility_arrivals_in_calibration_sample']:.1f} "
        f"at 1/{args.calibration_sample_factor} sample; "
        f"scaled={prediction['scaled_full_population_arrivals']:.0f}"
    )
    print(f"Scenario: {args.output_scenario}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
