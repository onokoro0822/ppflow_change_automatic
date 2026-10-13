#!/usr/bin/env python3
"""Estimate separate destination-city MNLs for large-store and other shopping.

The Chukyo PT 2022 export by arrival facility (到着施設) splits shopping trips
(日常的な家事・買物 + 日常的でない買物) into arrivals at 大型規模小売店 and the
rest. Large-store trips travel farther (fewer stay in the origin zone), so one
coefficient set fitted on all shopping may describe them poorly.

Coefficients follow the Java MNL of ``ActGenerator.choiceFreeDestination``:
x = [beta, distance, area, popRatio, officeRatio, same-city] where beta
multiplies the city attraction ln(1 + shopping mesh capacity) and the others
multiply the lab's coefficients. Origin units are split into
train/validation/test (seed 13, as ``pflow_accessibility_estimation``).

This is a surrogate of the city choice on aggregated PT, not a simulation.
PT destinations are physical areas; with ``--facility-city-match on`` the
Java output gcode is the store's own city, which is what this compares with.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import chukyo_pt_origin_distribution as pt
import pflow_mnl_estimation as estimation
from pflow_accessibility_estimation import AccessibilityModel, accessibility, load_meshes, mesh_center, row_split
from pflow_attraction_calibration import SHOPPING_PURPOSES
from pflow_origin_comparison import OUTSIDE_AICHI, ComparisonUnits

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PT = PROJECT_DIR.parent / "中京PTデータ" / "chukyo_pt_2022_od_facility_purpose_age.csv"
LARGE_STORE = "大型規模小売店"
NAKAMURA = "名古屋市中村区"
TARGET_ZONE = "5"
# Adopted single model (2026-10-09): beta, distance, area, pop, office; same-city unscaled.
CURRENT = np.array([0.40625, 0.90625, -0.0625, -4.65625, -0.453125, 1.0])
AGE_COLUMNS = ("10歳未満", "10～19歳", "20～29歳", "30～39歳", "40～49歳",
               "50～59歳", "60～69歳", "70～79歳", "80歳以上")


class StoreTypeModel(AccessibilityModel):
    """AccessibilityModel with a same-city multiplier and a configurable search radius."""

    def __init__(self, original, cities, params, radius_m: float = estimation.SEARCH_RADIUS_M):
        super().__init__(original, cities, params)
        self.same_basis = self.fixed.copy()
        self.fixed = np.zeros_like(self.fixed)
        n = len(self.codes)
        lon = np.array([cities[c]["lon"] for c in self.codes])
        lat = np.array([cities[c]["lat"] for c in self.codes])
        meters = np.array([[estimation.haversine_m(lon[i], lat[i], lon[j], lat[j]) for j in range(n)]
                           for i in range(n)])
        self.available = meters <= radius_m
        self.feature = np.broadcast_to(original.ln_retail, (n, n))

    def city_probabilities(self, x, feature=None):
        feature = self.feature if feature is None else feature
        v = (x[5] * self.same_basis + np.einsum("i,ikod->kod", x[1:5], self.basis)
             + x[0] * feature[None, :, :])
        v = np.where(self.available[None, :, :], v, -np.inf)
        v -= np.max(v, axis=2, keepdims=True)
        e = np.exp(v)
        p = e / e.sum(axis=2, keepdims=True)
        return np.sum(p * self.mixture.T[:, :, None], axis=0)

    def probabilities(self, x, feature=None):
        p = self.city_probabilities(x, feature) @ self.membership
        num = self.membership.T @ (p * self.origin_weights[:, None])
        den = self.membership.T @ self.origin_weights
        return np.divide(num, den[:, None], out=np.zeros_like(num), where=den[:, None] > 0)


def pt_tables(pt_csv: Path, units: ComparisonUnits, names):
    """Aichi unit OD of large-store and other shopping, zone-5 large-store origins, age shares."""
    index = {u: i for i, u in enumerate(names)}
    od = {"large": np.zeros((len(names), len(names))), "other": np.zeros((len(names), len(names)))}
    zone5 = np.zeros(len(names))
    zone5_outside = 0
    ages = {"large": np.zeros(len(AGE_COLUMNS)), "other": np.zeros(len(AGE_COLUMNS))}
    for row in pt.read_pt_table(pt_csv).rows:
        if row[pt.PURPOSE_COLUMN] not in SHOPPING_PURPOSES:
            continue
        kind = "large" if row[pt.FACILITY_COLUMN] == LARGE_STORE else "other"
        line = int(row["__line__"])
        count = pt.parse_count(row[pt.TOTAL_COLUMN], column=pt.TOTAL_COLUMN, line_no=line)
        ages[kind] += [pt.parse_count(row[c], column=c, line_no=line) for c in AGE_COLUMNS]
        o = units.unit_for_zone(pt.normalize_zone(row[pt.ORIGIN_COLUMN]))
        d = units.unit_for_zone(pt.normalize_zone(row[pt.DESTINATION_COLUMN]))
        if kind == "large" and pt.normalize_zone(row[pt.DESTINATION_COLUMN]) == TARGET_ZONE:
            if o in index:
                zone5[index[o]] += count
            else:
                zone5_outside += count
        if OUTSIDE_AICHI in (o, d) or o not in index or d not in index:
            continue
        od[kind][index[o], index[d]] += count
    return od, zone5, zone5_outside, ages


def score(prob, od, origins, mask):
    m = np.zeros_like(od, dtype=bool)
    m[origins] = mask[origins]
    total = float(od[m].sum())
    ll = float((od[m] * np.log(np.maximum(prob[m], 1e-300))).sum())
    covered = float(od[origins][mask[origins]].sum() / max(od[origins].sum(), 1))
    return {"mean_log_probability": ll / total, "trips": total, "reachable_share": covered}


def fit(model, od, origins, mask, start=CURRENT, max_rounds=200):
    x = np.array(start, dtype=float).copy()
    step = np.array([.5, .25, .5, .5, .5, .25])

    def value(z):
        return score(model.probabilities(z), od, origins, mask)["mean_log_probability"]

    best = value(x)
    for _ in range(max_rounds):
        improved = False
        for j in range(len(x)):
            for sign in (1, -1):
                z = x.copy()
                z[j] += sign * step[j]
                if (j == 1 and z[j] <= .05) or (j == 5 and z[j] < 0):
                    continue
                candidate = value(z)
                if candidate > best + 1e-10:
                    best, x, improved = candidate, z, True
        if not improved:
            step /= 2
        if max(step) < .005:
            break
    return x


def tvd(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return float(0.5 * np.abs(a / a.sum() - b / b.sum()).sum())


def destination_origins(model, x, od_all_origins, target):
    """Expected origin distribution of arrivals in the target unit, PT origin volumes as weights."""
    prob = model.probabilities(x)
    return prob[:, target] * od_all_origins


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pt-csv", type=Path, default=DEFAULT_PT)
    parser.add_argument("--pflow-home", type=Path, default=PROJECT_DIR / ".local" / "pflow")
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output", type=Path,
                        default=PROJECT_DIR / "output" / "large_store_estimation" / "estimation.json")
    parser.add_argument("--radii-km", default="20,30,40,60")
    args = parser.parse_args(argv)

    facilities = args.pflow_home / "data" / "facilities"
    baseline_dir = args.baseline_dir or args.pflow_home / "output" / "capacity_experiment" / "baseline" / "23"
    units = ComparisonUnits(pt.load_middle_zone_labels(pt.DEFAULT_ZONE_CODE_XLSX))
    cities = estimation.load_cities(facilities)
    params = estimation.load_params(facilities / "mnl")
    original = estimation.DestinationModel(cities, params, estimation.chooser_weights(baseline_dir), units)
    names = original.unit_names
    od, zone5, zone5_outside, ages = pt_tables(args.pt_csv, units, names)
    target = original.unit_index[NAKAMURA]
    train, valid, test = row_split(len(names))
    split = {"train": train, "validation": valid, "test": test}

    results = {
        "pt_csv": str(args.pt_csv), "split_seed": 13,
        "split_origin_units": {k: [names[i] for i in v] for k, v in split.items()},
        "pt_aichi_trips": {k: float(v.sum()) for k, v in od.items()},
        "pt_same_unit_share": {k: float(np.trace(v) / v.sum()) for k, v in od.items()},
        "large_store_share_by_age": {a: float(ages["large"][i] / (ages["large"][i] + ages["other"][i]))
                                     for i, a in enumerate(AGE_COLUMNS)},
        "large_store_share_all": float(ages["large"].sum() / (ages["large"].sum() + ages["other"].sum())),
        "zone5_large_store_trips_aichi": float(zone5.sum()), "zone5_large_store_trips_outside": zone5_outside,
        "models": {},
    }
    od["all"] = od["large"] + od["other"]
    origins_all = {k: v.sum(axis=1) for k, v in od.items()}
    all_rows = np.arange(len(names))

    def evaluate(model, x, label, mask):
        entry = {"coefficients": x.tolist()}
        for kind in ("large", "other", "all"):
            entry[kind] = {part: score(model.probabilities(x), od[kind], rows, mask)
                           for part, rows in split.items()}
        predicted = destination_origins(model, x, origins_all["large"], target)
        entry["nakamura_large_origin_tvd"] = tvd(predicted, od["large"][:, target])
        entry["zone5_large_origin_tvd"] = tvd(predicted, zone5)
        entry["nakamura_large_inside_share"] = float(predicted[target] / predicted.sum())
        prob = model.probabilities(x)
        entry["same_unit_share_large"] = float((np.diag(prob) * origins_all["large"]).sum() / origins_all["large"].sum())
        results["models"][label] = entry
        print(label, json.dumps({k: entry[k] for k in ("coefficients", "nakamura_large_origin_tvd",
                                                         "zone5_large_origin_tvd", "nakamura_large_inside_share")},
                                ensure_ascii=False),
              "test large", round(entry["large"]["test"]["mean_log_probability"], 5),
              "test other", round(entry["other"]["test"]["mean_log_probability"], 5), flush=True)
        return entry

    base_model = StoreTypeModel(original, cities, params)
    # The vectorised model must reproduce the Java-equivalent reference at the adopted coefficients.
    reference = estimation.DestinationModel(cities, estimation.scaled_params(params, *CURRENT[2:5]),
                                            estimation.chooser_weights(baseline_dir), units)
    np.testing.assert_allclose(base_model.probabilities(CURRENT),
                               np.nan_to_num(reference.unit_probabilities(CURRENT[0], CURRENT[1])),
                               rtol=1e-12, atol=1e-14)
    mask20 = base_model.probabilities(CURRENT) > 0
    results["pt_unreachable_share_20km"] = {k: float(v[~mask20].sum() / v.sum()) for k, v in od.items()}
    results["zone5_large_unreachable_share_20km"] = float(zone5[~mask20[:, target]].sum() / zone5.sum())

    evaluate(base_model, CURRENT, "current_single", mask20)
    single = fit(base_model, od["all"], train, mask20)
    evaluate(base_model, single, "single_refit_train", mask20)
    other = fit(base_model, od["other"], train, mask20)
    evaluate(base_model, other, "other_train_r20", mask20)
    for radius in [float(r) for r in args.radii_km.split(",")]:
        model = base_model if radius == 20 else StoreTypeModel(original, cities, params, radius * 1000)
        mask = model.probabilities(CURRENT) > 0
        large = fit(model, od["large"], train, mask)
        entry = evaluate(model, large, f"large_train_r{radius:g}", mask)
        # Same trips as the 20 km model, for a like-for-like log-probability.
        entry["large_test_on_20km_trips"] = score(model.probabilities(large), od["large"], test, mask20)
        entry["zone5_reachable_share"] = float(zone5[mask[:, target]].sum() / zone5.sum())
        if radius == 20:
            results["selected_radius_20_all_rows"] = fit(model, od["large"], all_rows, mask, start=large).tolist()
        else:
            results[f"large_all_rows_r{radius:g}"] = fit(model, od["large"], all_rows, mask, start=large).tolist()
    results["other_all_rows_r20"] = fit(base_model, od["other"], all_rows, mask20, start=other).tolist()

    # Large-store attraction log(1 + sum_m E_m^p / max(d,1km)^q), as --shopping-large-store-accessibility.
    # p > 1 weights concentrated retail meshes (station centres). Selected on validation, then test reported.
    meshes, capacity, membership = load_meshes(facilities, base_model.codes)
    centers = [mesh_center(m) for m in meshes]
    distances = np.array([[estimation.haversine_m(cities[c]["lon"], cities[c]["lat"], lon, lat) / 1000
                           for lon, lat in centers] for c in base_model.codes])
    grid = []
    for power in (0.5, 1.0, 1.5, 2.0, 3.0):
        for decay in (0.0, 1.0, 2.0):
            model = StoreTypeModel(original, cities, params)
            model.feature = accessibility(capacity, distances, membership, power, decay)
            large = fit(model, od["large"], train, mask20)
            entry = evaluate(model, large, f"large_accessibility_p{power:g}_q{decay:g}", mask20)
            grid.append((entry["large"]["validation"]["mean_log_probability"], power, decay, model, large))
    _, power, decay, model, large = max(grid, key=lambda item: item[0])
    results["large_accessibility_selected"] = {
        "power": power, "decay": decay, "selection": "best validation mean log-probability",
        "train_coefficients": large.tolist(),
        "all_rows_coefficients": fit(model, od["large"], all_rows, mask20, start=large).tolist(),
    }
    # Simulation calibration lowers the large-store distance multiplier to match facility origins;
    # record what that costs in the PT large-store OD fit (all-rows coefficients, other terms fixed).
    sensitivity = []
    selected = np.array(results["large_accessibility_selected"]["all_rows_coefficients"])
    for distance in (selected[1], 0.6, 0.45, 0.3, 0.15):
        x = selected.copy()
        x[1] = distance
        prob = model.probabilities(x)
        predicted = destination_origins(model, x, origins_all["large"], target)
        sensitivity.append({
            "distance_multiplier": float(distance),
            **{part: score(prob, od["large"], rows, mask20)["mean_log_probability"] for part, rows in split.items()},
            "all": score(prob, od["large"], all_rows, mask20)["mean_log_probability"],
            "nakamura_large_origin_tvd": tvd(predicted, od["large"][:, target]),
            "nakamura_large_inside_share": float(predicted[target] / predicted.sum()),
        })
        print("distance", sensitivity[-1], flush=True)
    results["large_distance_sensitivity"] = sensitivity
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
