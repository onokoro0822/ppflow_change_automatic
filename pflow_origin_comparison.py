#!/usr/bin/env python3
"""Compare Pseudo-PFLOW shopping-arrival origins with Chukyo PT origin distributions.

Pseudo-PFLOW reports the municipality code (gcode) of each activity, while the
Chukyo PT OD table uses middle zones. Some zones contain several municipalities
(for example zone 1 = Chikusa + part of Higashi), so both sides are aggregated
into the coarsest common units: connected groups of zones and municipalities.

"Origin" means the immediately preceding activity location on both sides, not
the residence.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import chukyo_pt_origin_distribution as pt
from pflow_capacity_analysis import (
    FACILITY_TOLERANCE,
    SHOPPING_PURPOSE,
    TARGET_GCODE,
    TARGET_LAT,
    TARGET_LON,
    TARGET_MESH,
    _iter_rows,
    third_mesh_code,
)

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PFLOW_HOME = PROJECT_DIR / ".local" / "pflow"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output" / "pflow_origin_comparison"
PT_DESTINATION_ZONE = "5"  # 名古屋市中村区
AICHI = "愛知県"
OUTSIDE_AICHI = "愛知県外・不明"

# JIS municipality codes for Aichi (2022), named as in the Chukyo PT code table.
AICHI_MUNICIPALITIES = {
    "23101": "名古屋市千種区", "23102": "名古屋市東区", "23103": "名古屋市北区",
    "23104": "名古屋市西区", "23105": "名古屋市中村区", "23106": "名古屋市中区",
    "23107": "名古屋市昭和区", "23108": "名古屋市瑞穂区", "23109": "名古屋市熱田区",
    "23110": "名古屋市中川区", "23111": "名古屋市港区", "23112": "名古屋市南区",
    "23113": "名古屋市守山区", "23114": "名古屋市緑区", "23115": "名古屋市名東区",
    "23116": "名古屋市天白区",
    "23201": "豊橋市", "23202": "岡崎市", "23203": "一宮市", "23204": "瀬戸市",
    "23205": "半田市", "23206": "春日井市", "23207": "豊川市", "23208": "津島市",
    "23209": "碧南市", "23210": "刈谷市", "23211": "豊田市", "23212": "安城市",
    "23213": "西尾市", "23214": "蒲郡市", "23215": "犬山市", "23216": "常滑市",
    "23217": "江南市", "23219": "小牧市", "23220": "稲沢市", "23221": "新城市",
    "23222": "東海市", "23223": "大府市", "23224": "知多市", "23225": "知立市",
    "23226": "尾張旭市", "23227": "高浜市", "23228": "岩倉市", "23229": "豊明市",
    "23230": "日進市", "23231": "田原市", "23232": "愛西市", "23233": "清須市",
    "23234": "北名古屋市", "23235": "弥富市", "23236": "みよし市", "23237": "あま市",
    "23238": "長久手市",
    "23302": "愛知郡東郷町", "23342": "西春日井郡豊山町", "23361": "丹羽郡大口町",
    "23362": "丹羽郡扶桑町", "23424": "海部郡大治町", "23425": "海部郡蟹江町",
    "23427": "海部郡飛島村", "23441": "知多郡阿久比町", "23442": "知多郡東浦町",
    "23445": "知多郡南知多町", "23446": "知多郡美浜町", "23447": "知多郡武豊町",
    "23501": "額田郡幸田町", "23561": "北設楽郡設楽町", "23562": "北設楽郡東栄町",
    "23563": "北設楽郡豊根村",
}

PT_PROFILES = (
    pt.FACILITY_USES[0],  # commercial: shopping + dining + leisure (used by OD replacement)
    pt.FacilityUse(
        key="shopping",
        name="買物のみ",
        destination_facility="大型規模小売店",
        purposes=("日常的な家事・買物", "日常的でない買物"),
    ),
)


class ComparisonUnits:
    """Coarsest common partition of PT middle zones and municipalities."""

    def __init__(self, zone_labels: Mapping[str, pt.MiddleZoneInfo]):
        parent: dict[str, str] = {}

        def find(node: str) -> str:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: str, b: str) -> None:
            parent[find(a)] = find(b)

        aichi_zones = set()
        for zone, info in zone_labels.items():
            if info.prefectures != (AICHI,):
                continue
            aichi_zones.add(zone)
            for municipality in info.municipalities:
                union(f"z:{zone}", f"m:{municipality}")

        members: dict[str, set[str]] = defaultdict(set)
        for node in list(parent):
            if node.startswith("m:"):
                members[find(node)].add(node[2:])
        self._unit_by_root = {
            root: "・".join(sorted(names)) for root, names in members.items()
        }
        self._find = find
        self._aichi_zones = aichi_zones
        self._known_municipalities = {
            name for names in members.values() for name in names
        }

    def unit_for_zone(self, zone: str) -> str:
        if zone not in self._aichi_zones:
            return OUTSIDE_AICHI
        return self._unit_by_root[self._find(f"z:{zone}")]

    def unit_for_gcode(self, gcode: str) -> str:
        name = AICHI_MUNICIPALITIES.get(gcode)
        if name is None:
            return OUTSIDE_AICHI
        if name not in self._known_municipalities:
            raise ValueError(f"Municipality {name} ({gcode}) is missing from the PT zone table")
        return self._unit_by_root[self._find(f"m:{name}")]


def total_variation_distance(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(key, 0.0) - b.get(key, 0.0)) for key in keys)


def normalize(counts: Mapping[str, float], *, exclude: Iterable[str] = ()) -> dict[str, float]:
    excluded = set(exclude)
    kept = {key: value for key, value in counts.items() if key not in excluded}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in kept.items()}


def pt_origin_counts(pt_csv: Path, units: ComparisonUnits, zone_labels) -> dict[str, dict[str, float]]:
    result = pt.build_origin_distributions(
        pt_csv, [PT_DESTINATION_ZONE], zone_labels=zone_labels, facility_uses=PT_PROFILES
    )
    counts: dict[str, dict[str, float]] = {}
    for spec in PT_PROFILES:
        aggregated: Counter[str] = Counter()
        for origin in result["facilities"][spec.key]["origin_distribution"]:
            aggregated[units.unit_for_zone(str(origin["origin_zone"]))] += origin["trip_count"]
        counts[f"pt_{spec.key}"] = dict(aggregated)
    return counts


def pflow_origin_counts(scenario_dir: Path, units: ComparisonUnits) -> dict[str, dict[str, float]]:
    """Count shopping arrivals by the unit of the immediately preceding activity."""
    selections = {"admin": Counter(), "mesh": Counter(), "facility": Counter()}
    csv_files = sorted(path for path in scenario_dir.rglob("*.csv") if path.is_file())
    if not csv_files:
        raise FileNotFoundError(f"No activity CSVs under {scenario_dir}")
    for csv_path in csv_files:
        previous_by_person: dict[int, dict[str, object]] = {}
        for row in _iter_rows(csv_path):
            person_id = int(row["person_id"])
            previous = previous_by_person.get(person_id)
            previous_by_person[person_id] = row
            if int(row["purpose"]) != SHOPPING_PURPOSE or previous is None:
                continue
            unit = units.unit_for_gcode(str(previous["gcode"]))
            if str(row["gcode"]) == TARGET_GCODE:
                selections["admin"][unit] += 1
            if third_mesh_code(float(row["lon"]), float(row["lat"])) == TARGET_MESH:
                selections["mesh"][unit] += 1
            if (
                abs(float(row["lon"]) - TARGET_LON) <= FACILITY_TOLERANCE
                and abs(float(row["lat"]) - TARGET_LAT) <= FACILITY_TOLERANCE
            ):
                selections["facility"][unit] += 1
    return {key: dict(value) for key, value in selections.items()}


def unit_class(unit: str) -> str:
    if unit == OUTSIDE_AICHI:
        return OUTSIDE_AICHI
    if unit == AICHI_MUNICIPALITIES[TARGET_GCODE]:
        return "中村区内"
    if all(name.startswith("名古屋市") for name in unit.split("・")):
        return "名古屋市内（中村区以外）"
    return "名古屋市外の愛知県"


def compare(distributions: Mapping[str, Mapping[str, float]], reference_key: str) -> dict[str, object]:
    reference_all = normalize(distributions[reference_key])
    reference_aichi = normalize(distributions[reference_key], exclude=[OUTSIDE_AICHI])
    series = {}
    for key, counts in distributions.items():
        shares = normalize(counts)
        classes: Counter[str] = Counter()
        for unit, share in shares.items():
            classes[unit_class(unit)] += share
        series[key] = {
            "count": sum(counts.values()),
            "unit_count": len(counts),
            "tvd_vs_reference": total_variation_distance(shares, reference_all),
            "tvd_vs_reference_aichi_only": total_variation_distance(
                normalize(counts, exclude=[OUTSIDE_AICHI]), reference_aichi
            ),
            "class_shares": dict(classes),
            "shares": dict(sorted(shares.items(), key=lambda item: -item[1])),
        }
    return {"reference": reference_key, "series": series}


def write_outputs(comparison: dict[str, object], output_dir: Path, labels: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "origin_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    series = comparison["series"]
    keys = list(series)
    units = sorted(
        {unit for key in keys for unit in series[key]["shares"]},
        key=lambda unit: -series[comparison["reference"]]["shares"].get(unit, 0.0),
    )
    with (output_dir / "origin_comparison.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["origin_unit", "class", *[labels.get(key, key) for key in keys]])
        for unit in units:
            writer.writerow([unit, unit_class(unit), *[
                f"{series[key]['shares'].get(unit, 0.0):.4f}" for key in keys
            ]])
    _write_chart(comparison, output_dir, labels, units[:15])


def _write_chart(comparison, output_dir: Path, labels: Mapping[str, str], units: Sequence[str]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for family in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP"):
        if any(font.name == family for font in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = family
            break
    series = comparison["series"]
    keys = list(series)
    figure, axis = plt.subplots(figsize=(12, 6.5))
    width = 0.8 / len(keys)
    for index, key in enumerate(keys):
        values = [100 * series[key]["shares"].get(unit, 0.0) for unit in units]
        positions = [position + (index - (len(keys) - 1) / 2) * width for position in range(len(units))]
        tvd = series[key]["tvd_vs_reference"]
        label = labels.get(key, key)
        if key != comparison["reference"]:
            label += f"（TVD {tvd:.3f}）"
        axis.bar(positions, values, width=width, label=label)
    axis.set_xticks(range(len(units)))
    axis.set_xticklabels([unit.replace("・", "\n") for unit in units], rotation=60, ha="right", fontsize=8)
    axis.set_ylabel("出発地構成比（%）")
    axis.set_title("名古屋市中村区への買物到着：直前活動地の構成比（中京PT上位15単位）")
    axis.legend(fontsize=9)
    figure.tight_layout()
    for suffix in ("png", "svg"):
        figure.savefig(output_dir / f"origin_comparison.{suffix}", dpi=200)
    plt.close(figure)


SERIES_LABELS = {
    "pt_commercial": "中京PT 商業（買物＋飲食・娯楽）",
    "pt_shopping": "中京PT 買物のみ",
    "baseline_admin": "PFLOW baseline 中村区全体",
    "baseline_mesh": "PFLOW baseline 対象メッシュ",
    "calibrated_admin": "PFLOW 校正 中村区全体",
    "calibrated_mesh": "PFLOW 校正 対象メッシュ",
    "calibrated_facility": "PFLOW 校正 対象施設",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pflow-home", type=Path, default=DEFAULT_PFLOW_HOME)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--calibrated-dir", type=Path)
    parser.add_argument("--pt-csv", type=Path, default=pt.DEFAULT_INPUT)
    parser.add_argument("--zone-xlsx", type=Path, default=pt.DEFAULT_ZONE_CODE_XLSX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    baseline_dir = args.baseline_dir or args.pflow_home / "output" / "capacity_experiment" / "baseline"
    calibrated_dir = args.calibrated_dir or (
        args.pflow_home / "output" / "calibrated_target" / "calibrated_target"
    )
    zone_labels = pt.load_middle_zone_labels(args.zone_xlsx)
    units = ComparisonUnits(zone_labels)

    distributions: dict[str, dict[str, float]] = {}
    distributions.update(pt_origin_counts(args.pt_csv, units, zone_labels))
    for prefix, directory in (("baseline", baseline_dir), ("calibrated", calibrated_dir)):
        for selection, counts in pflow_origin_counts(directory, units).items():
            if prefix == "baseline" and selection == "facility":
                continue  # the injected facility does not exist in the baseline
            distributions[f"{prefix}_{selection}"] = counts

    comparison = compare(distributions, reference_key="pt_shopping")
    comparison["notes"] = [
        "Origins are immediately preceding activity locations on both sides.",
        "PT destination is middle zone 5 (Nakamura Ward) by purpose, not the facility itself.",
        "The Pseudo-PFLOW runs simulate Aichi residents only, so origins outside Aichi cannot appear.",
        "Pseudo-PFLOW counts are a 2% sample (sample factor 50).",
    ]
    write_outputs(comparison, args.output_dir, SERIES_LABELS)
    for key, values in comparison["series"].items():
        print(
            f"{SERIES_LABELS.get(key, key)}: n={values['count']:.0f} units={values['unit_count']} "
            f"TVD={values['tvd_vs_reference']:.3f} TVD(Aichi)={values['tvd_vs_reference_aichi_only']:.3f} "
            + " ".join(f"{name}={share:.3f}" for name, share in sorted(values["class_shares"].items()))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
