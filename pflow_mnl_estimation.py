#!/usr/bin/env python3
"""Estimate a shopping-attraction term on top of the lab's destination-city MNL.

Pseudo-PFLOW chooses the destination city of a shopping activity with an MNL
(``ActGenerator.choiceFreeDestination``) whose coefficients come from the lab's
``mnl/*_params.csv``. Its utility for destination city d seen from origin city o:

    p0 * euclidean degree distance(o, d) + p1 * female + p2 * [d == o] + p3 * senior
    + p4 * area_d + p5 * popRatio_d / 1000 + p6 * officeRatio_d / 1000

over the cities whose centroids lie within 20 km of o's centroid. The female and
senior terms are the same for every alternative, so they cancel. Nothing in it
measures shopping supply. This script keeps the lab coefficients fixed and
estimates, by maximum likelihood on Chukyo PT shopping OD counts,

    + beta * ln(1 + shopping mesh capacity of d)      (what --shopping-attraction-beta adds)

and optionally a multiplier lambda on the distance coefficient. Choosers are a
mix of agent types (worker, non-worker, students) weighted by the baseline run's
shopping trips from each origin city, and city probabilities are summed into the
PT comparison units. PT trips between units the model can never connect (beyond
20 km) are reported and left out of the likelihood.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

import chukyo_pt_origin_distribution as pt
from pflow_attraction_calibration import SHOPPING_PURPOSES
from pflow_capacity_analysis import SHOPPING_PURPOSE
from pflow_origin_comparison import OUTSIDE_AICHI, ComparisonUnits
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PFLOW_HOME = PROJECT_DIR / ".local" / "pflow"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "pflow_mnl_estimation"
SEARCH_RADIUS_M = 20000.0  # ActGenerator.MAX_SEARCH_DISTANCE
AGENT_PARAM_FILES = {"worker": "labor_params.csv", "nolabor": "nolabor_params.csv",
                     "student1": "student1_params.csv", "student2": "student2_params.csv"}


def agent_type(labor: int) -> str:
    if labor == 21:
        return "worker"
    if labor in (11, 12, 13):
        return "student1"
    if labor in (14, 15, 16):
        return "student2"
    return "nolabor"


def city_type(population: float) -> int:
    return 3 if population >= 500000 else 2 if population >= 100000 else 1


def load_cities(facilities: Path, prefix: str = "23") -> dict[str, dict[str, float]]:
    cities = {}
    with (facilities / "city_boundary.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["n03_007"].startswith(prefix):
                cities[row["n03_007"]] = {
                    "type": city_type(float(row["city_pop"])), "area": float(row["area"]),
                    "pop_ratio": float(row["pop_ratio"]), "office_ratio": float(row["office_ratio"]),
                    "lon": float(row["st_x"]), "lat": float(row["st_y"]),
                }
    # Shopping capacity = economic-census column index 4, first row per mesh wins (as in Java).
    seen: set[str] = set()
    retail = Counter()
    with (facilities / "mesh_ecensus.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            mesh, gcode = row[0], row[1]
            if gcode in cities and mesh not in seen:
                retail[gcode] += float(row[2 + 4])
            seen.add(mesh)
    stores = Counter()
    with (facilities / "city_retail.csv").open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            # Same validity rule as DataAccessor.loadRetailData (TelePoint retail stores).
            if len(row) > 21 and row[7] in cities:
                try:
                    float(row[20]), float(row[21])
                except ValueError:
                    continue
                stores[row[7]] += 1
    for gcode, city in cities.items():
        city["ln_wholesale_retail_employees"] = math.log1p(retail[gcode])
        city["ln_retail_stores"] = math.log1p(stores[gcode])
        city["ln_retail"] = city["ln_wholesale_retail_employees"]
    return cities


def load_params(mnl_dir: Path) -> dict[str, dict[int, list[float]]]:
    params: dict[str, dict[int, list[float]]] = {}
    for kind, name in AGENT_PARAM_FILES.items():
        with (mnl_dir / name).open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if int(row["transition"]) == SHOPPING_PURPOSE:
                    params.setdefault(kind, {})[int(row["ecity"])] = [float(row[f"param{i}"]) for i in range(1, 8)]
    return params


def chooser_weights(baseline_dir: Path) -> dict[str, Counter]:
    """Shopping trips by origin city (previous activity) and agent type in the baseline run."""
    weights: dict[str, Counter] = defaultdict(Counter)
    for csv_path in sorted(baseline_dir.rglob("*.csv")):
        previous: dict[str, list[str]] = {}
        with csv_path.open(encoding="utf-8") as handle:
            for line in handle:
                fields = line.rstrip("\n").split(",")
                before = previous.get(fields[0])
                previous[fields[0]] = fields
                if before is not None and int(fields[6]) == SHOPPING_PURPOSE:
                    weights[before[9]][agent_type(int(fields[3]))] += 1
    return weights


class DestinationModel:
    """Vectorised lab MNL over Aichi cities, aggregated to comparison units."""

    def __init__(self, cities, params, weights, units: ComparisonUnits):
        self.codes = sorted(cities)
        self.index = {code: i for i, code in enumerate(self.codes)}
        n = len(self.codes)
        lon = np.array([cities[c]["lon"] for c in self.codes])
        lat = np.array([cities[c]["lat"] for c in self.codes])
        self.deg = np.hypot(lon[:, None] - lon[None, :], lat[:, None] - lat[None, :])
        meters = np.array([[haversine_m(lon[i], lat[i], lon[j], lat[j]) for j in range(n)] for i in range(n)])
        self.available = meters <= SEARCH_RADIUS_M
        self.same = np.eye(n)
        self.ln_retail = np.array([cities[c]["ln_retail"] for c in self.codes])
        self.static = {}  # (kind, origin type) -> destination term without distance/same
        self.coef = {}
        for kind, by_type in params.items():
            for ctype, p in by_type.items():
                self.coef[(kind, ctype)] = p
                self.static[(kind, ctype)] = (
                    p[4] * np.array([cities[c]["area"] for c in self.codes])
                    + p[5] * np.array([cities[c]["pop_ratio"] for c in self.codes]) / 1000
                    + p[6] * np.array([cities[c]["office_ratio"] for c in self.codes]) / 1000
                )
        self.origin_type = np.array([cities[c]["type"] for c in self.codes])
        self.weights = np.zeros((n, len(AGENT_PARAM_FILES)))
        self.kinds = list(AGENT_PARAM_FILES)
        for code, counter in weights.items():
            if code in self.index:
                for k, kind in enumerate(self.kinds):
                    self.weights[self.index[code], k] = counter[kind]
        self.unit_of = [units.unit_for_gcode(c) for c in self.codes]
        self.unit_names = sorted(set(self.unit_of))
        self.unit_index = {u: i for i, u in enumerate(self.unit_names)}
        self.membership = np.zeros((n, len(self.unit_names)))
        for i, unit in enumerate(self.unit_of):
            self.membership[i, self.unit_index[unit]] = 1.0

    def city_probabilities(self, beta: float, distance_scale: float = 1.0) -> np.ndarray:
        """P(d | o) for each origin city, mixed over agent types."""
        n = len(self.codes)
        mixed = np.zeros((n, n))
        totals = self.weights.sum(axis=1)
        for o in range(n):
            if totals[o] == 0:
                continue
            for k, kind in enumerate(self.kinds):
                w = self.weights[o, k] / totals[o]
                if w == 0:
                    continue
                p = self.coef[(kind, int(self.origin_type[o]))]
                v = (distance_scale * p[0] * self.deg[o] + p[2] * self.same[o]
                     + self.static[(kind, int(self.origin_type[o]))] + beta * self.ln_retail)
                v = np.where(self.available[o], v, -np.inf)
                v = v - v[self.available[o]].max()
                e = np.exp(v)
                mixed[o] += w * e / e.sum()
        return mixed

    def unit_probabilities(self, beta: float, distance_scale: float = 1.0) -> np.ndarray:
        """P(destination unit | origin unit), origin cities weighted by their shopping trips."""
        city = self.city_probabilities(beta, distance_scale) @ self.membership
        origin_weight = self.weights.sum(axis=1)
        num = self.membership.T @ (city * origin_weight[:, None])
        den = self.membership.T @ origin_weight
        with np.errstate(invalid="ignore", divide="ignore"):
            return num / den[:, None]


def pt_unit_od(pt_csv: Path, units: ComparisonUnits, names: Sequence[str]) -> np.ndarray:
    index = {u: i for i, u in enumerate(names)}
    od = np.zeros((len(names), len(names)))
    table = pt.read_pt_table(pt_csv)
    for row in table.rows:
        if row[pt.PURPOSE_COLUMN] not in SHOPPING_PURPOSES:
            continue
        o = units.unit_for_zone(pt.normalize_zone(row[pt.ORIGIN_COLUMN]))
        d = units.unit_for_zone(pt.normalize_zone(row[pt.DESTINATION_COLUMN]))
        if OUTSIDE_AICHI in (o, d) or o not in index or d not in index:
            continue
        od[index[o], index[d]] += pt.parse_count(row[pt.TOTAL_COLUMN], column=pt.TOTAL_COLUMN, line_no=int(row["__line__"]))
    return od


def log_likelihood(model: DestinationModel, od: np.ndarray, beta: float, distance_scale: float) -> tuple[float, float]:
    prob = model.unit_probabilities(beta, distance_scale)
    reachable = prob > 0
    ll = float((od[reachable] * np.log(prob[reachable])).sum())
    unreachable_share = float(od[~reachable].sum() / od.sum())
    return ll, unreachable_share


def maximize(model, od, *, estimate_distance: bool) -> dict[str, float]:
    """Coordinate search with shrinking steps (two parameters at most)."""
    beta, scale = 0.0, 1.0
    best, _ = log_likelihood(model, od, beta, scale)
    step_beta, step_scale = 0.5, 0.25
    while step_beta > 1e-3:
        improved = False
        for db, ds in ((step_beta, 0), (-step_beta, 0), (0, step_scale), (0, -step_scale)):
            if ds and not estimate_distance:
                continue
            candidate = (beta + db, max(0.05, scale + ds))
            ll, _ = log_likelihood(model, od, *candidate)
            if ll > best:
                best, (beta, scale), improved = ll, candidate, True
        if not improved:
            step_beta /= 2
            step_scale /= 2
    # Standard error of beta from the numerical second derivative.
    h = 1e-3
    f0 = best
    fp, _ = log_likelihood(model, od, beta + h, scale)
    fm, _ = log_likelihood(model, od, beta - h, scale)
    second = (fp - 2 * f0 + fm) / h**2
    return {"beta": beta, "distance_scale": scale, "log_likelihood": best,
            "beta_se": math.sqrt(-1 / second) if second < 0 else float("nan")}


def scaled_params(params, area: float, pop: float, office: float):
    """Lab coefficients with the destination terms (area, popRatio, officeRatio) multiplied."""
    return {kind: {ctype: p[:4] + [p[4] * area, p[5] * pop, p[6] * office] for ctype, p in by_type.items()}
            for kind, by_type in params.items()}


def maximize_destination_terms(cities, params, weights, units, od, *, time_limit_s: float = 300.0):
    """Also re-scale the lab's destination terms, keeping distance and same-city from the lab.

    Coordinate search over beta, the distance multiplier and multipliers on the area,
    popRatio and officeRatio coefficients. No standard errors are reported.
    """
    import time

    x = {"beta": 0.0, "distance_scale": 1.0, "area": 1.0, "pop": 1.0, "office": 1.0}
    models: dict[tuple, DestinationModel] = {}

    def model_for(values):
        key = (values["area"], values["pop"], values["office"])
        if key not in models:
            models.clear()
            models[key] = DestinationModel(cities, scaled_params(params, *key), weights, units)
        return models[key]

    def ll(values):
        return log_likelihood(model_for(values), od, values["beta"], values["distance_scale"])[0]

    best = ll(x)
    step = {"beta": 0.5, "distance_scale": 0.25, "area": 0.5, "pop": 0.5, "office": 0.5}
    started = time.time()
    while max(step.values()) > 0.01 and time.time() - started < time_limit_s:
        improved = False
        for key in step:
            for delta in (step[key], -step[key]):
                candidate = dict(x)
                candidate[key] = x[key] + delta
                if key == "distance_scale" and candidate[key] <= 0.05:
                    continue
                value = ll(candidate)
                if value > best:
                    best, x, improved = value, candidate, True
        if not improved:
            step = {key: value / 2 for key, value in step.items()}
    model = model_for(x)
    return {**{f"{k}_multiplier" if k in ("area", "pop", "office") else k: v for k, v in x.items()},
            "log_likelihood": best, **fit_summary(model, od, x["beta"], x["distance_scale"])}


def fit_summary(model, od, beta, scale) -> dict[str, float]:
    """How well the implied unit OD matches PT: overall destinations and Nakamura's origins."""
    prob = model.unit_probabilities(beta, scale)
    origins = od.sum(axis=1)
    predicted = np.nan_to_num(prob) * origins[:, None]
    nakamura = model.unit_index["名古屋市中村区"]
    def tvd(a, b):
        return float(0.5 * np.abs(a / a.sum() - b / b.sum()).sum())
    same_pt = float(np.trace(od) / od.sum())
    same_model = float(np.trace(predicted) / predicted.sum())
    return {
        "tvd_destinations": tvd(predicted.sum(axis=0), od.sum(axis=0)),
        "tvd_nakamura_origins": tvd(predicted[:, nakamura], od[:, nakamura]),
        "same_unit_share_model": same_model,
        "same_unit_share_pt": same_pt,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pflow-home", type=Path, default=DEFAULT_PFLOW_HOME)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--attraction", choices=("ln_wholesale_retail_employees", "ln_retail_stores"),
        default="ln_wholesale_retail_employees",
        help="city attraction measure: mesh capacity (census wholesale+retail employees, as Pseudo-PFLOW) or TelePoint retail store count",
    )
    parser.add_argument("--pt-csv", type=Path, default=pt.DEFAULT_INPUT)
    parser.add_argument("--zone-xlsx", type=Path, default=pt.DEFAULT_ZONE_CODE_XLSX)
    args = parser.parse_args(argv)

    facilities = args.pflow_home / "data" / "facilities"
    baseline_dir = args.baseline_dir or args.pflow_home / "output" / "capacity_experiment" / "baseline" / "23"
    units = ComparisonUnits(pt.load_middle_zone_labels(args.zone_xlsx))
    cities = load_cities(facilities)
    for city in cities.values():
        city["ln_retail"] = city[args.attraction]
    model = DestinationModel(cities, load_params(facilities / "mnl"),
                             chooser_weights(baseline_dir), units)
    od = pt_unit_od(args.pt_csv, units, model.unit_names)

    results = {}
    ll0, unreachable = log_likelihood(model, od, 0.0, 1.0)
    results["lab_model"] = {"beta": 0.0, "distance_scale": 1.0, "log_likelihood": ll0,
                            **fit_summary(model, od, 0.0, 1.0)}
    for name, estimate_distance in (("beta_only", False), ("beta_and_distance", True)):
        fit = maximize(model, od, estimate_distance=estimate_distance)
        results[name] = {**fit, **fit_summary(model, od, fit["beta"], fit["distance_scale"])}
    results["beta_distance_and_destination_terms"] = maximize_destination_terms(
        cities, load_params(facilities / "mnl"), chooser_weights(baseline_dir), units, od
    )
    results["pt_trips_unreachable_within_20km_share"] = unreachable
    results["pt_shopping_trips_aichi"] = float(od.sum())
    results["attraction"] = args.attraction
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / f"mnl_estimation_{args.attraction}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
