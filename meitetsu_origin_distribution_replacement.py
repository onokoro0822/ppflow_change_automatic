"""Generate a non-destructive Meitetsu-site OD scenario from Chukyo PT origins."""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from shapely import wkt
from shapely.geometry import Point
from shapely.strtree import STRtree

from chukyo_pt_origin_distribution import _xlsx_cell_value, _xlsx_shared_strings, _xlsx_sheet_path
from trip_chains import haversine_m

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = PROJECT_DIR / "output/trip_chains/nagoya_trip_chains.sqlite3"
DEFAULT_DISTRIBUTION = PROJECT_DIR / "output/chukyo_pt_origin_distribution/origin_distribution_by_facility_use.json"
DEFAULT_ZONE_XLSX = PROJECT_DIR.parent / "中京PTデータ/中ゾーンコード表/pt_system_code_table.xlsx"
DEFAULT_SCENARIO = PROJECT_DIR / "config/development_scenarios/meitetsu_origin_distribution_commercial.json"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output/meitetsu_origin_distribution_replacement/commercial"

@dataclass(frozen=True)
class ZoneBoundary:
    basic_zone: str
    middle_zone: str
    geometry: Any

class ZoneClassifier:
    """Classify only points contained by complete official WKT polygons."""
    def __init__(self, boundaries: Sequence[ZoneBoundary]) -> None:
        self.boundaries = tuple(boundaries)
        self.geometries = [item.geometry for item in self.boundaries]
        self.tree = STRtree(self.geometries)
        self.cache: dict[tuple[float, float], tuple[str, str] | None] = {}

    def classify(self, lon: float, lat: float) -> tuple[str, str] | None:
        key = (float(lon), float(lat))
        if key in self.cache:
            return self.cache[key]
        point = Point(*key)
        result = None
        for raw_index in self.tree.query(point):
            index = int(raw_index)
            if self.geometries[index].covers(point):
                boundary = self.boundaries[index]
                result = (boundary.middle_zone, boundary.basic_zone)
                break
        self.cache[key] = result
        return result

def load_zone_boundaries(path: Path, *, sheet_name: str = "Sheet2", wkt_column: str = "P", basic_zone_column: str = "Q") -> tuple[list[ZoneBoundary], dict[str, int]]:
    """Load complete sixth-survey WKT cells; report truncated cells, never guess."""
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    boundaries: list[ZoneBoundary] = []
    audit = Counter()
    with ZipFile(path) as archive:
        strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_sheet_path(archive, sheet_name)
        with archive.open(sheet_path) as source:
            for _, element in ET.iterparse(source, events=("end",)):
                if element.tag != f"{namespace}row":
                    continue
                values: dict[str, str] = {}
                for cell in element.findall(f"{namespace}c"):
                    match = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
                    if match and match.group() in {wkt_column, basic_zone_column}:
                        values[match.group()] = _xlsx_cell_value(cell, strings).strip()
                text = values.get(wkt_column, "")
                basic = values.get(basic_zone_column, "")
                if not text.upper().startswith(("POLYGON", "MULTIPOLYGON")):
                    element.clear()
                    continue
                audit["wkt_rows"] += 1
                if not basic.isdigit():
                    audit["invalid_code_rows"] += 1
                    element.clear()
                    continue
                try:
                    geometry = wkt.loads(text)
                except Exception:
                    audit["truncated_or_invalid_wkt_rows"] += 1
                    element.clear()
                    continue
                if geometry.is_empty:
                    audit["empty_geometry_rows"] += 1
                    element.clear()
                    continue
                boundaries.append(ZoneBoundary(basic, str(int(basic) // 100), geometry))
                audit["loaded_boundaries"] += 1
                element.clear()
    return boundaries, dict(audit)

def largest_remainder_counts(weights: dict[str, float], total: int) -> dict[str, int]:
    if total < 0:
        raise ValueError("total must be non-negative")
    positive = {key: float(value) for key, value in weights.items() if value > 0}
    weight_sum = sum(positive.values())
    if total and not weight_sum:
        raise ValueError("positive weights are required")
    raw = {key: total * value / weight_sum for key, value in positive.items()}
    counts = {key: math.floor(value) for key, value in raw.items()}
    order = sorted(positive, key=lambda key: (-(raw[key] - counts[key]), str(key)))
    for key in order[: total - sum(counts.values())]:
        counts[key] += 1
    return counts

def bounded_allocation(weights: dict[str, float], availability: dict[str, int], total: int) -> tuple[dict[str, int], int]:
    requested = largest_remainder_counts(weights, total)
    allocated = {zone: min(count, max(0, int(availability.get(zone, 0)))) for zone, count in requested.items()}
    remaining = total - sum(allocated.values())
    while remaining:
        active = {zone: weights[zone] for zone in weights if availability.get(zone, 0) > allocated.get(zone, 0)}
        if not active:
            break
        proposal = largest_remainder_counts(active, remaining)
        added = 0
        for zone, count in proposal.items():
            take = min(count, availability[zone] - allocated.get(zone, 0))
            allocated[zone] = allocated.get(zone, 0) + take
            added += take
        if added == 0:
            zone = sorted(active, key=lambda key: (-active[key], key))[0]
            allocated[zone] = allocated.get(zone, 0) + 1
            added = 1
        remaining -= added
    return allocated, remaining

def deterministic_priority(seed: int, trip_id: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}:{trip_id}".encode()).digest()[:8], "big")

def _candidate_query(codes: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    placeholders = ",".join("?" for _ in codes)
    return f"""SELECT trip_id, person_id, departure_time_sec,
        origin_lon, origin_lat, destination_lon, destination_lat,
        transport_mode, trip_purpose, employment_status,
        analysis_previous_trip_id, analysis_previous_trip_purpose,
        analysis_next_trip_id, analysis_next_trip_purpose
        FROM analysis_trip_chain_rows
        WHERE analysis_eligible = 1 AND trip_purpose IN ({placeholders})
        ORDER BY trip_id""", tuple(str(code) for code in codes)

def _iter_candidates(connection: sqlite3.Connection, codes: Sequence[str]) -> Iterable[sqlite3.Row]:
    query, params = _candidate_query(codes)
    yield from connection.execute(query, params)

def _is_near_target(row: sqlite3.Row, target: dict[str, Any]) -> bool:
    return haversine_m(row["destination_lon"], row["destination_lat"], float(target["longitude"]), float(target["latitude"])) <= float(target["exclude_existing_destination_radius_m"])


def collect_person_candidates(
    connection: sqlite3.Connection,
    classifier: ZoneClassifier,
    codes: Sequence[str],
    target: dict[str, Any],
    seed: int,
) -> tuple[Counter[str], dict[str, int], list[dict[str, Any]]]:
    """Keep one reproducibly chosen eligible arrival per person."""
    best_by_person: dict[str, tuple[int, int, dict[str, Any]]] = {}
    audit = Counter()
    for row in _iter_candidates(connection, codes):
        audit["purpose_and_quality_eligible"] += 1
        if _is_near_target(row, target):
            audit["excluded_already_near_target"] += 1
            continue
        classification = classifier.classify(row["origin_lon"], row["origin_lat"])
        if classification is None:
            audit["unclassified_origin"] += 1
            continue
        audit["classified_candidate"] += 1
        middle, basic = classification
        candidate = dict(row)
        candidate.update(origin_middle_zone=middle, origin_basic_zone=basic)
        priority = deterministic_priority(seed, int(row["trip_id"]))
        person_id = str(row["person_id"])
        item = (priority, int(row["trip_id"]), candidate)
        current = best_by_person.get(person_id)
        if current is None or item[:2] < current[:2]:
            best_by_person[person_id] = item
    people = [item[2] for item in best_by_person.values()]
    availability = Counter(row["origin_middle_zone"] for row in people)
    audit["unique_origin_coordinates"] = len(classifier.cache)
    audit["eligible_unique_people"] = len(people)
    audit["discarded_repeat_person_candidates"] = audit["classified_candidate"] - len(people)
    return availability, dict(audit), people

def select_candidates(
    people: Sequence[dict[str, Any]], allocation: dict[str, int], seed: int
) -> list[dict[str, Any]]:
    heaps: dict[str, list[tuple[int, int, dict[str, Any]]]] = {
        zone: [] for zone, count in allocation.items() if count > 0
    }
    for candidate in people:
        middle = candidate["origin_middle_zone"]
        if allocation.get(middle, 0) <= 0:
            continue
        priority = deterministic_priority(seed, int(candidate["trip_id"]))
        item = (-priority, -int(candidate["trip_id"]), candidate)
        heap = heaps[middle]
        if len(heap) < allocation[middle]:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    return sorted(
        (item[2] for heap in heaps.values() for item in heap),
        key=lambda row: int(row["trip_id"]),
    )

def _fetch_trips(connection: sqlite3.Connection, trip_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    ids = sorted(set(int(value) for value in trip_ids))
    rows: dict[int, dict[str, Any]] = {}
    for start in range(0, len(ids), 800):
        chunk = ids[start : start + 800]
        placeholders = ",".join("?" for _ in chunk)
        query = f"""SELECT trip_id, person_id, departure_time_sec,
            origin_lon, origin_lat, destination_lon, destination_lat,
            transport_mode, trip_purpose, employment_status, source_file, source_line
            FROM trips WHERE trip_id IN ({placeholders})"""
        for row in connection.execute(query, chunk):
            rows[int(row["trip_id"])] = dict(row)
    return rows

def build_changed_od_rows(connection: sqlite3.Connection, selected: Sequence[dict[str, Any]], target: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    target_lon, target_lat = float(target["longitude"]), float(target["latitude"])
    current_ids = {int(row["trip_id"]) for row in selected}
    next_ids = {int(row["analysis_next_trip_id"]) for row in selected if row["analysis_next_trip_id"] is not None}
    trips = _fetch_trips(connection, current_ids | next_ids)
    affected: dict[int, dict[str, Any]] = {}
    def ensure(trip_id: int) -> dict[str, Any]:
        if trip_id not in affected:
            source = trips[trip_id]
            affected[trip_id] = {**source,
                "origin_lon_before": source["origin_lon"], "origin_lat_before": source["origin_lat"],
                "destination_lon_before": source["destination_lon"], "destination_lat_before": source["destination_lat"],
                "origin_lon_after": source["origin_lon"], "origin_lat_after": source["origin_lat"],
                "destination_lon_after": source["destination_lon"], "destination_lat_after": source["destination_lat"],
                "changed_origin": False, "changed_destination": False}
        return affected[trip_id]
    selected_output, gaps = [], []
    for row in selected:
        incoming = ensure(int(row["trip_id"]))
        incoming.update(destination_lon_after=target_lon, destination_lat_after=target_lat, changed_destination=True)
        next_id, gap = row["analysis_next_trip_id"], None
        if next_id is not None:
            outgoing = ensure(int(next_id))
            outgoing.update(origin_lon_after=target_lon, origin_lat_after=target_lat, changed_origin=True)
            gap = haversine_m(incoming["destination_lon_after"], incoming["destination_lat_after"], outgoing["origin_lon_after"], outgoing["origin_lat_after"])
            gaps.append(gap)
        selected_output.append({**row, "new_destination_lon": target_lon, "new_destination_lat": target_lat, "chain_gap_after_m": gap})
    changed_rows = [affected[key] for key in sorted(affected)]
    unique_people = len({str(row["person_id"]) for row in selected})
    consecutive_selected = sum(
        row["analysis_next_trip_id"] is not None
        and int(row["analysis_next_trip_id"]) in current_ids
        for row in selected
    )
    validation = {
        "selected_arrival_trips": len(selected), "unique_selected_people": unique_people,
        "duplicate_selected_people": len(selected) - unique_people,
        "consecutive_selected_arrivals": consecutive_selected,
        "affected_od_rows": len(changed_rows),
        "destination_updates": sum(bool(row["changed_destination"]) for row in changed_rows),
        "next_trip_origin_updates": sum(bool(row["changed_origin"]) for row in changed_rows),
        "selected_with_next_trip": len(gaps), "max_chain_gap_after_m": max(gaps, default=0.0),
        "departure_time_changes": 0, "transport_mode_changes": 0,
        "source_database_modified": False}
    return selected_output, changed_rows, validation

def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def _configure_matplotlib() -> Any:
    os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ppflow-matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("Hiragino Sans", "Yu Gothic", "Noto Sans CJK JP", "IPAexGothic"):
        if candidate in available:
            plt.rcParams["font.family"] = candidate
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "none"
    return plt

def write_professor_figure(summary: dict[str, Any], fit_rows: Sequence[dict[str, Any]], output_dir: Path) -> list[Path]:
    plt = _configure_matplotlib()
    figure = plt.figure(figsize=(16, 9), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=(0.38, 0.62), width_ratios=(1.25, 0.75))
    flow_axis = figure.add_subplot(grid[0, :])
    bar_axis = figure.add_subplot(grid[1, 0])
    note_axis = figure.add_subplot(grid[1, 1])
    flow_axis.set_axis_off()
    boxes = [
        ("① 中京PT", "中ゾーン5への\n用途別出発地分布"),
        ("② 割当", "目標到着数を\n出発中ゾーン別に配分"),
        ("③ Pseudo-PFLOW", "同じ出発分布になる\n商業目的トリップを抽出"),
        ("④ OD変更", "到着地を名鉄跡地へ\n次トリップ出発地も更新"),
    ]
    colors = ["#E9F0FF", "#EAF8F4", "#FFF4E5", "#F6ECFF"]
    for index, ((heading, body), color) in enumerate(zip(boxes, colors, strict=True)):
        x = 0.02 + index * 0.25
        flow_axis.text(x, 0.52, f"{heading}\n{body}", transform=flow_axis.transAxes,
                       ha="left", va="center", fontsize=13, linespacing=1.4,
                       bbox={"boxstyle": "round,pad=0.75", "facecolor": color, "edgecolor": "#9AA5B1"})
        if index < 3:
            flow_axis.annotate("", xy=(x + 0.235, 0.52), xytext=(x + 0.205, 0.52),
                               xycoords="axes fraction", arrowprops={"arrowstyle": "->", "lw": 2, "color": "#657382"})
    top = list(fit_rows)[:10]
    top.reverse()
    labels = [f'{row["origin_zone"]}  {row["origin_label_short"]}' for row in top]
    target_share = [100 * float(row["target_share"]) for row in top]
    realized_share = [100 * float(row["realized_share"]) for row in top]
    positions = list(range(len(top)))
    bar_axis.barh([p + 0.18 for p in positions], target_share, height=0.34, color="#2F6BFF", label="中京PT目標")
    bar_axis.barh([p - 0.18 for p in positions], realized_share, height=0.34, color="#F28E2B", label="選択後Pseudo-PFLOW")
    bar_axis.set_yticks(positions, labels)
    bar_axis.set_xlabel("全到着トリップに占める割合（%）")
    bar_axis.set_title("出発地分布の再現結果（上位10中ゾーン）", loc="left", fontsize=14, fontweight="bold")
    bar_axis.grid(axis="x", color="#D9DEE7", linewidth=0.8)
    bar_axis.set_axisbelow(True)
    bar_axis.legend(loc="lower right", frameon=False)
    bar_axis.spines[["top", "right", "left"]].set_visible(False)
    bar_axis.tick_params(axis="y", length=0)
    note_axis.set_axis_off()
    metrics, validation, audit = summary["results"], summary["validation"], summary["candidate_audit"]
    note_axis.text(0, 0.98, "今回の試算", fontsize=15, fontweight="bold", va="top")
    note_axis.text(0, 0.88, "\n".join([
        f'対象：{summary["scenario"]["name"]}', f'用途：{summary["facility_use_name"]}',
        f'目標到着数：{metrics["target_arrival_trips"]:,} トリップ/日',
        f'変更できた到着数：{metrics["selected_arrival_trips"]:,}',
        f'変更OD行：{validation["affected_od_rows"]:,}',
        f'分布差（TVD）：{100 * metrics["total_variation_distance"]:.2f} pt',
        f'分類不能候補：{audit["unclassified_origin"]:,}',
    ]), fontsize=12.5, va="top", linespacing=1.55)
    note_axis.text(0, 0.47, "説明時の注意", fontsize=15, fontweight="bold", va="top")
    note_axis.text(0, 0.39,
        "• 跡地の観測値ではなく、中ゾーン5への分布を移植\n"
        "• 到着施設列がないため、商業目的集合による代理分布\n"
        "• ODの時刻・交通手段は初版では維持\n"
        "• 元SQLiteは変更せず、再現可能な差分CSVを出力\n"
        "• 途中切れWKTの区域は推測せず分類不能として除外",
        fontsize=11.5, va="top", linespacing=1.55, color="#374151")
    figure.suptitle("名鉄跡地の擬似人流へ、中京PTの出発地分布をどう融合するか",
                    fontsize=20, fontweight="bold", x=0.04, ha="left", y=0.985)
    figure.text(0.04, 0.935, "初版：全商業型 25,284到着トリップ/日（比較用の暫定需要シナリオ）",
                fontsize=11, color="#5B6472")
    figure.tight_layout(rect=(0.025, 0.03, 0.98, 0.92))
    paths = []
    for suffix in (".png", ".svg"):
        path = output_dir / f"professor_summary{suffix}"
        figure.savefig(path, dpi=200, bbox_inches="tight", facecolor="white")
        paths.append(path)
    plt.close(figure)
    return paths

def _short_label(origin: dict[str, Any]) -> str:
    municipalities = origin.get("origin_municipalities", [])
    if municipalities:
        text = "・".join(municipalities[:2])
        return text + (f"ほか{len(municipalities) - 2}" if len(municipalities) > 2 else "")
    return origin.get("origin_label", "") or "名称不明"

def write_explanation(summary: dict[str, Any], output_dir: Path) -> Path:
    result, validation = summary["results"], summary["validation"]
    path = output_dir / "教授説明用メモ.md"
    text = f"""# 名鉄跡地への中京PT出発地分布の適用（初版）

## 結論

名鉄跡地でも適用できる。ただし、跡地単体で観測された分布ではなく、**名鉄跡地を含む中京PT中ゾーン5への商業用途代理分布を、新施設へ移植した仮説シナリオ**である。

## 計算の流れ

1. 中京PTの到着中ゾーン5について、商業目的集合から出発中ゾーン別構成比を作る。
2. 全商業型の暫定需要 {result['target_arrival_trips']:,} 到着トリップ/日を構成比で中ゾーン別に整数配分する。
3. Pseudo-PFLOWの品質基準適合・買物目的100のトリップから、出発座標が各中ゾーンに入るトリップを再現可能なハッシュ順で選ぶ。
4. 選んだ到着トリップの目的地を名鉄跡地代表点へ変更する。
5. チェーンを切らないため、同じ人の次トリップの出発地も名鉄跡地へ変更する。

## 実行結果

- 目標到着トリップ: {result['target_arrival_trips']:,}
- 選択できた到着トリップ: {result['selected_arrival_trips']:,}
- 変更対象OD行: {validation['affected_od_rows']:,}
- 出発地分布の全変動距離（TVD）: {100 * result['total_variation_distance']:.3f}ポイント
- 変更後の最大チェーン接続誤差: {validation['max_chain_gap_after_m']:.6f} m
- 元SQLite: 変更していない（差分CSVのみ出力）

## 教授への説明で強調する点

- 「需要量」は跡地の比較用開発シナリオ、「どこから来るか」は中京PT、「個々の移動」はPseudo-PFLOW、と役割を分けている。
- 中ゾーン5は中村区相当の集計区域であり、名鉄跡地という単一施設の実測商圏ではない。
- 現行OD表に到着施設列がないため、用途別分布は目的による代理値である。
- Pseudo-PFLOWの時刻・交通手段は初版では変えていない。将来は時間帯・交通手段分布も同時に合わせる必要がある。
- Excelセル上限でWKTが欠けた基本ゾーンは、誤分類を避けるため分類不能として候補から外した。
"""
    path.write_text(text, encoding="utf-8")
    return path

def build_scenario(database_path: Path, distribution_path: Path, zone_xlsx_path: Path, scenario_path: Path, output_dir: Path) -> dict[str, Any]:
    for path in (database_path, distribution_path, zone_xlsx_path, scenario_path):
        if not Path(path).exists():
            raise FileNotFoundError(path)
    scenario = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    distribution = json.loads(Path(distribution_path).read_text(encoding="utf-8"))
    facility_key = scenario["facility_use_key"]
    facility = distribution["facilities"][facility_key]
    target_count = int(scenario["target_arrival_trip_count"])
    codes = tuple(str(code) for code in scenario["eligible_trip_purpose_codes"])
    if not codes:
        raise ValueError("eligible_trip_purpose_codes must not be empty")
    target = scenario["target"]
    weights = {row["origin_zone"]: float(row["share"]) for row in facility["origin_distribution"] if float(row["share"]) > 0}
    labels = {row["origin_zone"]: row for row in facility["origin_distribution"]}
    boundaries, boundary_audit = load_zone_boundaries(Path(zone_xlsx_path))
    classifier = ZoneClassifier(boundaries)
    target_classification = classifier.classify(float(target["longitude"]), float(target["latitude"]))
    expected_zone = str(scenario["destination_middle_zone"])
    if target_classification is None or target_classification[0] != expected_zone:
        raise ValueError(f"Target point was not classified into middle zone {expected_zone}: {target_classification}")
    connection = sqlite3.connect(f"file:{Path(database_path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    seed = int(scenario["random_seed"])
    availability, candidate_audit, people = collect_person_candidates(connection, classifier, codes, target, seed)
    requested = largest_remainder_counts(weights, target_count)
    allocation, unmet = bounded_allocation(weights, availability, target_count)
    selected = select_candidates(people, allocation, seed)
    selected_output, changed_rows, validation = build_changed_od_rows(connection, selected, target)
    connection.close()
    selected_counts = Counter(row["origin_middle_zone"] for row in selected_output)
    selected_total = len(selected_output)
    fit_rows = []
    for zone in sorted(weights, key=lambda key: (-weights[key], key)):
        origin = labels[zone]
        fit_rows.append({
            "origin_zone": zone, "origin_label": origin.get("origin_label", ""),
            "origin_label_short": _short_label(origin), "target_share": weights[zone],
            "requested_count": requested.get(zone, 0), "available_candidates": availability.get(zone, 0),
            "allocated_count": allocation.get(zone, 0), "selected_count": selected_counts.get(zone, 0),
            "realized_share": selected_counts.get(zone, 0) / selected_total if selected_total else 0.0,
            "count_change_from_requested": selected_counts.get(zone, 0) - requested.get(zone, 0)})
    tvd = 0.5 * sum(abs(float(row["target_share"]) - float(row["realized_share"])) for row in fit_rows)
    if selected_total != sum(allocation.values()):
        raise AssertionError("selected count differs from allocated count")
    if validation["destination_updates"] != selected_total:
        raise AssertionError("not every selected arrival received a destination update")
    if validation["duplicate_selected_people"] or validation["consecutive_selected_arrivals"]:
        raise AssertionError("selected arrivals must be unique people and non-consecutive")
    if validation["max_chain_gap_after_m"] > 1e-6:
        raise AssertionError("chain continuity was not preserved")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_fields = ("person_id", "trip_id", "analysis_next_trip_id", "departure_time_sec", "origin_middle_zone", "origin_basic_zone", "origin_lon", "origin_lat", "destination_lon", "destination_lat", "new_destination_lon", "new_destination_lat", "transport_mode", "trip_purpose", "employment_status", "analysis_previous_trip_purpose", "analysis_next_trip_purpose", "chain_gap_after_m")
    changed_fields = ("person_id", "trip_id", "departure_time_sec", "trip_purpose", "transport_mode", "employment_status", "source_file", "source_line", "origin_lon_before", "origin_lat_before", "destination_lon_before", "destination_lat_before", "origin_lon_after", "origin_lat_after", "destination_lon_after", "destination_lat_after", "changed_origin", "changed_destination")
    fit_fields = ("origin_zone", "origin_label", "target_share", "requested_count", "available_candidates", "allocated_count", "selected_count", "realized_share", "count_change_from_requested")
    selected_path, changed_path = output_dir / "selected_arrival_trips.csv", output_dir / "changed_od_trips.csv"
    fit_path, changes_path = output_dir / "origin_distribution_fit.csv", output_dir / "coordinate_changes.csv"
    _write_csv(selected_path, selected_output, selected_fields)
    _write_csv(changed_path, changed_rows, changed_fields)
    _write_csv(fit_path, fit_rows, fit_fields)
    coordinate_changes = []
    for row in changed_rows:
        if row["changed_origin"]:
            coordinate_changes.append({"person_id": row["person_id"], "trip_id": row["trip_id"], "coordinate_role": "origin", "original_lon": row["origin_lon_before"], "original_lat": row["origin_lat_before"], "new_lon": row["origin_lon_after"], "new_lat": row["origin_lat_after"]})
        if row["changed_destination"]:
            coordinate_changes.append({"person_id": row["person_id"], "trip_id": row["trip_id"], "coordinate_role": "destination", "original_lon": row["destination_lon_before"], "original_lat": row["destination_lat_before"], "new_lon": row["destination_lon_after"], "new_lat": row["destination_lat_after"]})
    _write_csv(changes_path, coordinate_changes, ("person_id", "trip_id", "coordinate_role", "original_lon", "original_lat", "new_lon", "new_lat"))
    summary = {
        "schema_version": 1, "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
        "scenario": scenario,
        "interpretation": "中京PT中ゾーン5への用途代理出発地分布を名鉄跡地の施設点へ移植する仮説であり、跡地固有の実測商圏ではない。",
        "facility_use_name": facility["name"],
        "source": {"database": str(Path(database_path).resolve()), "origin_distribution": str(Path(distribution_path).resolve()), "official_zone_workbook": str(Path(zone_xlsx_path).resolve())},
        "zone_boundary_audit": boundary_audit | {"target_middle_zone": target_classification[0], "target_basic_zone": target_classification[1]},
        "candidate_audit": candidate_audit | {"classified_candidates_in_target_distribution_zones": sum(availability.get(zone, 0) for zone in weights)},
        "results": {"target_arrival_trips": target_count, "allocated_arrival_trips": sum(allocation.values()), "selected_arrival_trips": selected_total, "unmet_arrival_trips": unmet, "target_origin_zone_count": len(weights), "selected_origin_zone_count": len(selected_counts), "total_variation_distance": tvd},
        "validation": validation,
        "outputs": {"selected_arrival_trips": str(selected_path.resolve()), "changed_od_trips": str(changed_path.resolve()), "coordinate_changes": str(changes_path.resolve()), "origin_distribution_fit": str(fit_path.resolve())}}
    summary_path = output_dir / "summary.json"
    summary["outputs"]["summary"] = str(summary_path.resolve())
    figure_paths = write_professor_figure(summary, fit_rows, output_dir)
    memo_path = write_explanation(summary, output_dir)
    summary["outputs"].update(professor_summary_png=str(figure_paths[0].resolve()), professor_summary_svg=str(figure_paths[1].resolve()), explanation_memo=str(memo_path.resolve()))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="中京PTの用途別出発地分布で名鉄跡地へのOD差分を作ります。")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--distribution", type=Path, default=DEFAULT_DISTRIBUTION)
    parser.add_argument("--zone-xlsx", type=Path, default=DEFAULT_ZONE_XLSX)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)

def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = build_scenario(args.database, args.distribution, args.zone_xlsx, args.scenario, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
