#!/usr/bin/env python3
"""Search commercial increments with count-constrained PT origin calibration.

Runs the existing generator-based capacity calibration for each increment,
then refines around the best count-feasible PT fits. This is a bounded search,
not proof of a global optimum, and PT is fitting data, not external validation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent
ENGINE = PROJECT / "pflow_joint_target_calibration.py"


def select_candidates(rows, targets, count_tolerance, tvd_tolerance):
    best = {}
    for target in targets:
        candidates = [r for r in rows if r["target"] == target
                      and math.isfinite(r["tvd_aichi"])
                      and r["arrivals_full_equivalent"] > 0
                      and abs(r["arrivals_full_equivalent"] - target) / target <= count_tolerance]
        if not candidates:
            best[str(target)] = None
            continue
        row = dict(min(candidates, key=lambda r: (
            r["tvd_aichi"], abs(r["arrivals_full_equivalent"] - target))))
        row.update(count_met=True, distribution_met=row["tvd_aichi"] <= tvd_tolerance,
                   joint_met=row["tvd_aichi"] <= tvd_tolerance)
        best[str(target)] = row
    return best


def refinement_points(rows, targets, tried, count_tolerance, tvd_tolerance):
    """Bisect neighboring intervals of each best feasible candidate.

    If a target has no feasible candidate, refine around its nearest count fit.
    Values remain inside the initial range; round to integer employee increments.
    """
    grid = sorted(set(tried))
    best = select_candidates(rows, targets, count_tolerance, tvd_tolerance)
    points = set()
    for target in targets:
        row = best[str(target)]
        if row is None:
            available = [r for r in rows if r["target"] == target]
            if not available:
                continue
            row = min(available, key=lambda r: abs(r["arrivals_full_equivalent"] - target))
        index = grid.index(row["mesh_addition"])
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(grid):
                midpoint = float(round((grid[index] + grid[neighbor]) / 2))
                if min(grid[index], grid[neighbor]) < midpoint < max(grid[index], grid[neighbor]):
                    if midpoint not in tried:
                        points.add(midpoint)
    return sorted(points)


def save(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pt-csv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--targets", type=int, nargs="+", default=[3000, 6000])
    p.add_argument("--mesh-additions", type=float, nargs="+",
                   default=[745.942954248366, 1500, 3000, 6000, 12000])
    p.add_argument("--stages", type=int, default=2, help="Initial grid plus refinement stages")
    p.add_argument("--capacity-rounds", type=int, default=5)
    p.add_argument("--count-tolerance", type=float, default=.10)
    p.add_argument("--tvd-tolerance", type=float, default=.15)
    p.add_argument("--sample-factor", type=int, default=50)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pflow-home", type=Path, default=PROJECT / ".local/pflow")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--reference-ceiling", type=float, default=122)
    p.add_argument("--reference-sample-factor", type=int, default=50)
    p.add_argument("--reference-addition", type=float, default=745.942954248366)
    args = p.parse_args(argv)
    if (any(t <= 0 for t in args.targets) or args.stages < 1 or args.capacity_rounds < 1
            or args.sample_factor < 1 or args.reference_sample_factor < 1):
        p.error("Targets, stage/round counts and sample factors must be positive")
    numeric = [*args.mesh_additions, args.reference_ceiling, args.reference_addition,
               args.count_tolerance, args.tvd_tolerance]
    if not all(math.isfinite(x) for x in numeric) or any(x < 0 for x in args.mesh_additions):
        p.error("Commercial increments must be finite and nonnegative")
    if not 0 <= args.count_tolerance < 1 or not 0 <= args.tvd_tolerance <= 1:
        p.error("Count tolerance must be in [0,1); TVD tolerance in [0,1]")
    if args.reference_ceiling <= 0 or args.reference_addition < 0:
        p.error("Reference ceiling must be positive and reference addition nonnegative")
    args.targets = sorted(set(args.targets))
    additions = sorted(set(args.mesh_additions))
    # Match the engine's initial estimate, preserving the reference scenario
    # even when a refinement batch starts at a different increment.
    def ceiling_for(addition):
        return args.reference_ceiling * (22230 + addition) / (22230 + args.reference_addition)
    if max(args.targets) >= ceiling_for(additions[0]) * args.reference_sample_factor:
        p.error("Initial grid exceeds the engine reference estimate; use a measured reference or a larger minimum increment")
    if args.output.exists():
        p.error("Use a new output directory; existing results are never overwritten")
    if not args.pt_csv.is_file():
        p.error("PT CSV does not exist")
    out = args.output.resolve()
    out.mkdir(parents=True)
    report = {"status": "running", "method": "bounded_commercial_search_count_constrained_pt_fit",
              "targets": args.targets, "seed": args.seed, "sample_factor": args.sample_factor,
              "count_tolerance": args.count_tolerance, "tvd_tolerance": args.tvd_tolerance,
              "stage_budget": args.stages, "capacity_round_budget": args.capacity_rounds,
              "trials": [], "best": {}, "mesh_additions": [], "stages": [],
              "notes": ["PT origin means preceding activity location, not residence or former shop.",
                        "Counts are shopping arrival trips, not unique visitors.",
                        "PT fit is conditional on Aichi; tvd_all remains a diagnostic.",
                        "Commercial increments are calibration parameters, not employment forecasts.",
                        "Bounded single-seed search; no global optimum guarantee.",
                        "Pilot full-equivalent counts need full-population verification."]}
    save(out / "report.json", report)
    engine_hash = hashlib.sha256(ENGINE.read_bytes()).hexdigest()
    tried = set()
    try:
        for stage in range(args.stages):
            if not additions:
                break
            if hashlib.sha256(ENGINE.read_bytes()).hexdigest() != engine_hash:
                raise RuntimeError("Calibration engine changed during search; retry after concurrent work finishes")
            stage_out = out / f"stage{stage}"
            command = [sys.executable, str(ENGINE), "--pt-csv", str(args.pt_csv.resolve()),
                       "--output", str(stage_out), "--targets", *map(str, args.targets),
                       "--mesh-additions", *map(str, additions),
                       "--rounds", str(args.capacity_rounds), "--workers", "1",
                       "--count-tolerance", str(args.count_tolerance),
                       "--tvd-tolerance", str(args.tvd_tolerance),
                       "--sample-factor", str(args.sample_factor), "--seed", str(args.seed),
                       "--pflow-home", str(args.pflow_home.resolve()),
                       "--baseline", str(args.baseline.resolve()),
                       "--reference-ceiling", str(ceiling_for(additions[0])),
                       "--reference-sample-factor", str(args.reference_sample_factor)]
            report["stages"].append({"stage": stage, "command": command, "additions": additions})
            save(out / "report.json", report)
            subprocess.run(command, check=True)
            if hashlib.sha256(ENGINE.read_bytes()).hexdigest() != engine_hash:
                raise RuntimeError("Calibration engine changed during batch; results cannot be pooled")
            batch = json.loads((stage_out / "report.json").read_text())
            if batch["status"] != "finished":
                raise RuntimeError("Calibration batch did not finish")
            if report.get("pt_sha256", batch["pt_sha256"]) != batch["pt_sha256"]:
                raise RuntimeError("PT input changed during search; results cannot be pooled")
            for key in ("pt_raw_counts", "pt_aichi_normalized", "pt_aichi_class_shares",
                        "pt_csv", "pt_sha256", "pflow_args", "competing_capacity"):
                report[key] = batch[key]
            report["trials"].extend(batch["trials"])
            tried.update(additions)
            report["mesh_additions"] = sorted(tried)
            report["best"] = select_candidates(report["trials"], args.targets,
                                                args.count_tolerance, args.tvd_tolerance)
            save(out / "report.json", report)
            additions = refinement_points(report["trials"], args.targets, tried,
                                          args.count_tolerance, args.tvd_tolerance)
        report["status"] = "finished"
        report["joint_met"] = all(r is not None and r["joint_met"] for r in report["best"].values())
        # Compatible with the existing full verifier only if every target has
        # a count-feasible candidate. Never verify an infeasible fallback.
        if all(report["best"].get(str(t)) is not None for t in args.targets):
            save(out / "selected_pilot.json", report)
    except BaseException as error:
        report.update(status="failed", error=str(error))
        save(out / "report.json", report)
        raise
    save(out / "report.json", report)
    print(json.dumps({"status": report["status"], "joint_met": report["joint_met"],
                      "selected_report": str(out / "selected_pilot.json")
                      if (out / "selected_pilot.json").exists() else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
