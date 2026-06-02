#!/usr/bin/env python3
"""End-to-end prototype for scenario-based pseudo people-flow changes.

This script is intentionally stdlib-only so it can run on a fresh Python 3
environment. It reads headerless Pseudo-PFLOW trip OD data, asks a few
clarifying questions, moves a deterministic sample of destinations toward a
target area, and writes a self-contained HTML comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
import sys
from dataclasses import asdict, dataclass, replace
from html import escape
from pathlib import Path
from typing import Iterable


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_CSV = BASE_DIR / "input" / "trip_12101.csv"
DEFAULT_SCENARIO_FILES = [
    BASE_DIR / "input" / "scenario.txt",
    BASE_DIR / "input" / "scenaro.txt",
]
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

TRIP_COLUMNS = [
    "person_id",
    "departure_time_sec",
    "origin_lon",
    "origin_lat",
    "destination_lon",
    "destination_lat",
    "transport_mode",
    "trip_purpose",
    "employment_status",
]

PURPOSE_HINTS = {
    "shopping": ["400"],
    "dining": ["500"],
}


@dataclass(frozen=True)
class Trip:
    person_id: str
    departure_time_sec: int
    origin_lon: float
    origin_lat: float
    destination_lon: float
    destination_lat: float
    transport_mode: str
    trip_purpose: str
    employment_status: str
    changed: bool = False


@dataclass(frozen=True)
class ScenarioRule:
    scenario_text: str
    scenario_name: str
    target_label: str
    target_lon: float
    target_lat: float
    affected_ratio: float
    affected_purposes: list[str]
    time_window: tuple[float, float] | None
    strength: float
    random_seed: int
    questions: list[dict[str, str]]
    notes: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run text-to-scenario pseudo people-flow comparison prototype."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_CSV)
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--scenario", help="Scenario text. If omitted, a file or prompt is used.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--yes", action="store_true", help="Use inferred defaults without prompts.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sample-lines", type=int, default=1200, help="Changed trips drawn in HTML.")
    parser.add_argument("--background-points", type=int, default=800, help="Context points drawn in HTML.")
    return parser.parse_args()


def read_scenario_text(args: argparse.Namespace) -> str:
    if args.scenario:
        return args.scenario.strip()

    if args.scenario_file:
        return args.scenario_file.read_text(encoding="utf-8").strip()

    for path in DEFAULT_SCENARIO_FILES:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()

    if sys.stdin.isatty():
        print("シナリオ文を入力してください。空行で終了します。")
        lines = []
        while True:
            line = input("> ")
            if not line:
                break
            lines.append(line)
        return "\n".join(lines).strip()

    raise FileNotFoundError("Scenario text was not provided and no default scenario file exists.")


def load_trips(path: Path) -> list[Trip]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    trips: list[Trip] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for line_no, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) < len(TRIP_COLUMNS):
                raise ValueError(f"Line {line_no}: expected 9 columns, got {len(row)}")
            try:
                trips.append(
                    Trip(
                        person_id=row[0],
                        departure_time_sec=int(float(row[1])),
                        origin_lon=float(row[2]),
                        origin_lat=float(row[3]),
                        destination_lon=float(row[4]),
                        destination_lat=float(row[5]),
                        transport_mode=row[6],
                        trip_purpose=row[7],
                        employment_status=row[8],
                    )
                )
            except ValueError:
                if line_no == 1:
                    continue
                raise

    if not trips:
        raise ValueError(f"No trips were loaded from {path}")
    return trips


def infer_target_from_destinations(trips: list[Trip], grid_size: float = 0.01) -> tuple[float, float]:
    buckets: dict[tuple[int, int], list[float]] = {}
    for trip in trips:
        key = (round(trip.destination_lon / grid_size), round(trip.destination_lat / grid_size))
        bucket = buckets.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += 1.0
        bucket[1] += trip.destination_lon
        bucket[2] += trip.destination_lat

    _, (count, sum_lon, sum_lat) = max(buckets.items(), key=lambda item: item[1][0])
    return sum_lon / count, sum_lat / count


def infer_target_label(text: str) -> str:
    if "柏の葉キャンパス駅" in text:
        return "柏の葉キャンパス駅前"
    if "駅前" in text:
        return "駅前"
    if "商業施設" in text:
        return "新設商業施設"
    return "仮想目的地"


def infer_purpose_codes(text: str) -> list[str]:
    purposes: list[str] = []
    if any(word in text for word in ["買い物", "買物", "商業", "ショッピング"]):
        purposes.extend(PURPOSE_HINTS["shopping"])
    if any(word in text for word in ["飲食", "食事", "レストラン", "カフェ"]):
        purposes.extend(PURPOSE_HINTS["dining"])
    return sorted(set(purposes))


def infer_time_window(text: str) -> tuple[float, float] | None:
    has_noon = any(word in text for word in ["昼", "ランチ", "正午"])
    has_evening = any(word in text for word in ["夕方", "夕", "夕刻"])
    has_morning = any(word in text for word in ["朝", "午前"])
    has_night = any(word in text for word in ["夜", "夜間"])

    if has_noon and has_evening:
        return (12.0, 18.0)
    if has_noon:
        return (11.0, 15.0)
    if has_evening:
        return (15.0, 19.0)
    if has_morning:
        return (7.0, 10.0)
    if has_night:
        return (18.0, 22.0)
    return None


def infer_ratio(text: str) -> float:
    if any(word in text for word in ["大型", "大規模", "集中"]):
        return 0.30
    if any(word in text for word in ["小規模", "少し", "一部"]):
        return 0.15
    return 0.25


def ask(prompt: str, default: str, enabled: bool) -> tuple[str, str]:
    if not enabled:
        return default, ""
    answer = input(f"{prompt} [{default}]: ").strip()
    return (answer or default), answer


def parse_lon_lat(value: str, default_lon: float, default_lat: float) -> tuple[float, float]:
    parts = [part for part in re.split(r"[\s,，]+", value.strip()) if part]
    if len(parts) != 2:
        return default_lon, default_lat
    first, second = float(parts[0]), float(parts[1])
    if abs(first) <= 90 and abs(second) > 90:
        return second, first
    return first, second


def parse_ratio(value: str, default: float) -> float:
    text = value.strip().replace("%", "")
    if not text:
        return default
    ratio = float(text)
    if ratio > 1.0:
        ratio = ratio / 100.0
    return clamp(ratio, 0.05, 0.50)


def parse_time_window(value: str, default: tuple[float, float] | None) -> tuple[float, float] | None:
    text = value.strip().lower()
    if text in {"", "all", "none", "なし", "全日", "終日"}:
        return default if text == "" else None
    match = re.search(r"(\d{1,2})(?::\d{2})?\s*[-~〜]\s*(\d{1,2})(?::\d{2})?", text)
    if not match:
        return default
    start = clamp(float(match.group(1)), 0.0, 24.0)
    end = clamp(float(match.group(2)), 0.0, 24.0)
    if start == end:
        return default
    return start, end


def build_rule(trips: list[Trip], scenario_text: str, args: argparse.Namespace) -> ScenarioRule:
    target_lon, target_lat = infer_target_from_destinations(trips)
    target_label = infer_target_label(scenario_text)
    ratio = infer_ratio(scenario_text)
    purposes = infer_purpose_codes(scenario_text)
    time_window = infer_time_window(scenario_text)
    strength = 0.82
    questions: list[dict[str, str]] = []
    notes = [
        "目的コードはデータ仕様に依存するため、買い物=400、飲食=500の仮定で処理します。",
        "地点名のジオコーディングは行わず、既存トリップの高密度な目的地クラスタを初期値にします。",
    ]

    interactive = (not args.yes) and sys.stdin.isatty()

    default_point = f"{target_lon:.6f},{target_lat:.6f}"
    value, raw = ask("集めたい地点の lon,lat を入力してください", default_point, interactive)
    target_lon, target_lat = parse_lon_lat(value, target_lon, target_lat)
    questions.append(
        {
            "id": "target_location",
            "question": "集めたい地点の lon,lat",
            "answer": raw or default_point,
        }
    )

    default_ratio = f"{ratio:.0%}"
    value, raw = ask("影響させる候補トリップの割合を入力してください", default_ratio, interactive)
    ratio = parse_ratio(value, ratio)
    questions.append(
        {
            "id": "affected_ratio",
            "question": "影響させる候補トリップの割合",
            "answer": raw or default_ratio,
        }
    )

    default_time = format_time_window(time_window)
    value, raw = ask("対象時間帯を入力してください。例: 12-18 / all", default_time, interactive)
    time_window = parse_time_window(value, time_window)
    questions.append(
        {
            "id": "time_window",
            "question": "対象時間帯",
            "answer": raw or default_time,
        }
    )

    name = scenario_text.replace("\n", " ")[:48] or "scenario"
    return ScenarioRule(
        scenario_text=scenario_text,
        scenario_name=name,
        target_label=target_label,
        target_lon=target_lon,
        target_lat=target_lat,
        affected_ratio=ratio,
        affected_purposes=purposes,
        time_window=time_window,
        strength=strength,
        random_seed=args.seed,
        questions=questions,
        notes=notes,
    )


def format_time_window(window: tuple[float, float] | None) -> str:
    if window is None:
        return "all"
    return f"{window[0]:g}-{window[1]:g}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def hour_of_day(departure_time_sec: int) -> float:
    return (departure_time_sec % 86400) / 3600.0


def is_in_time_window(trip: Trip, window: tuple[float, float] | None) -> bool:
    if window is None:
        return True
    start, end = window
    hour = hour_of_day(trip.departure_time_sec)
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def choose_candidates(trips: list[Trip], rule: ScenarioRule) -> tuple[list[int], list[str]]:
    notes: list[str] = []
    purpose_set = set(rule.affected_purposes)
    candidates = [
        i
        for i, trip in enumerate(trips)
        if (not purpose_set or trip.trip_purpose in purpose_set)
        and is_in_time_window(trip, rule.time_window)
    ]

    minimum = max(20, int(len(trips) * 0.002))
    if len(candidates) < minimum and purpose_set:
        notes.append("候補が少ないため、目的コード条件を外して時間帯のみで選定しました。")
        candidates = [i for i, trip in enumerate(trips) if is_in_time_window(trip, rule.time_window)]

    if not candidates:
        notes.append("候補が空だったため、全トリップから選定しました。")
        candidates = list(range(len(trips)))

    return candidates, notes


def meters_to_lat_delta(meters: float) -> float:
    return meters / 111_320.0


def meters_to_lon_delta(meters: float, lat: float) -> float:
    return meters / (111_320.0 * max(0.2, math.cos(math.radians(lat))))


def apply_scenario(trips: list[Trip], rule: ScenarioRule) -> tuple[list[Trip], list[int], list[int], list[str]]:
    candidates, notes = choose_candidates(trips, rule)
    rng = random.Random(rule.random_seed)
    changed_count = max(1, int(round(len(candidates) * rule.affected_ratio)))
    changed_count = min(changed_count, len(candidates))
    changed_indices = sorted(rng.sample(candidates, changed_count))
    changed_set = set(changed_indices)

    scenario_trips: list[Trip] = []
    for i, trip in enumerate(trips):
        if i not in changed_set:
            scenario_trips.append(trip)
            continue

        jitter_m = rng.gauss(0.0, 85.0)
        jitter_angle = rng.random() * math.tau
        jitter_lon = meters_to_lon_delta(math.cos(jitter_angle) * jitter_m, rule.target_lat)
        jitter_lat = meters_to_lat_delta(math.sin(jitter_angle) * jitter_m)

        new_lon = (
            trip.destination_lon
            + (rule.target_lon - trip.destination_lon) * rule.strength
            + jitter_lon
        )
        new_lat = (
            trip.destination_lat
            + (rule.target_lat - trip.destination_lat) * rule.strength
            + jitter_lat
        )

        scenario_trips.append(
            replace(
                trip,
                destination_lon=new_lon,
                destination_lat=new_lat,
                changed=True,
            )
        )

    return scenario_trips, candidates, changed_indices, notes


def write_trips(path: Path, trips: Iterable[Trip]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRIP_COLUMNS + ["changed"])
        writer.writeheader()
        for trip in trips:
            writer.writerow(asdict(trip))


def write_changed_trips(path: Path, baseline: list[Trip], scenario: list[Trip], changed_indices: list[int]) -> None:
    fieldnames = [
        "person_id",
        "departure_time_sec",
        "trip_purpose",
        "before_destination_lon",
        "before_destination_lat",
        "after_destination_lon",
        "after_destination_lat",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i in changed_indices:
            before = baseline[i]
            after = scenario[i]
            writer.writerow(
                {
                    "person_id": before.person_id,
                    "departure_time_sec": before.departure_time_sec,
                    "trip_purpose": before.trip_purpose,
                    "before_destination_lon": before.destination_lon,
                    "before_destination_lat": before.destination_lat,
                    "after_destination_lon": after.destination_lon,
                    "after_destination_lat": after.destination_lat,
                }
            )


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_km = 6371.0088
    lon1_rad = math.radians(lon1)
    lat1_rad = math.radians(lat1)
    lon2_rad = math.radians(lon2)
    lat2_rad = math.radians(lat2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2.0) ** 2
    )
    return radius_km * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def build_summary(
    baseline: list[Trip],
    scenario: list[Trip],
    rule: ScenarioRule,
    candidates: list[int],
    changed_indices: list[int],
    notes: list[str],
) -> dict[str, object]:
    before_distances = [
        haversine_km(
            baseline[i].destination_lon,
            baseline[i].destination_lat,
            rule.target_lon,
            rule.target_lat,
        )
        for i in changed_indices
    ]
    after_distances = [
        haversine_km(
            scenario[i].destination_lon,
            scenario[i].destination_lat,
            rule.target_lon,
            rule.target_lat,
        )
        for i in changed_indices
    ]
    return {
        "total_trips": len(baseline),
        "candidate_trips": len(candidates),
        "changed_trips": len(changed_indices),
        "changed_share_of_all": len(changed_indices) / len(baseline),
        "target": {
            "label": rule.target_label,
            "lon": rule.target_lon,
            "lat": rule.target_lat,
        },
        "affected_purposes": rule.affected_purposes,
        "time_window": format_time_window(rule.time_window),
        "movement_strength": rule.strength,
        "avg_distance_to_target_before_km": statistics.fmean(before_distances),
        "avg_distance_to_target_after_km": statistics.fmean(after_distances),
        "notes": rule.notes + notes,
    }


def sample_indices(indices: list[int], max_count: int, seed: int) -> list[int]:
    if len(indices) <= max_count:
        return indices
    rng = random.Random(seed)
    return sorted(rng.sample(indices, max_count))


def make_bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    min_lon = min(point[0] for point in points)
    max_lon = max(point[0] for point in points)
    min_lat = min(point[1] for point in points)
    max_lat = max(point[1] for point in points)
    lon_pad = max((max_lon - min_lon) * 0.08, 0.003)
    lat_pad = max((max_lat - min_lat) * 0.08, 0.003)
    return min_lon - lon_pad, max_lon + lon_pad, min_lat - lat_pad, max_lat + lat_pad


def project(
    lon: float,
    lat: float,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[float, float]:
    min_lon, max_lon, min_lat, max_lat = bbox
    x = (lon - min_lon) / max(max_lon - min_lon, 1e-9) * width
    y = (max_lat - lat) / max(max_lat - min_lat, 1e-9) * height
    return x, y


def svg_panel(
    title: str,
    trips: list[Trip],
    baseline: list[Trip],
    changed_indices: list[int],
    background_indices: list[int],
    bbox: tuple[float, float, float, float],
    rule: ScenarioRule,
    changed_color: str,
) -> str:
    width, height = 780, 520
    elements: list[str] = [
        f'<rect width="{width}" height="{height}" fill="#f7f5ef" />',
    ]

    for i in background_indices:
        trip = baseline[i]
        x, y = project(trip.destination_lon, trip.destination_lat, bbox, width, height)
        elements.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.7" fill="#868686" opacity="0.22" />')

    for i in changed_indices:
        before = baseline[i]
        trip = trips[i]
        x1, y1 = project(before.origin_lon, before.origin_lat, bbox, width, height)
        x2, y2 = project(trip.destination_lon, trip.destination_lat, bbox, width, height)
        elements.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{changed_color}" stroke-width="1.15" opacity="0.33" />'
        )
        elements.append(f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="2.5" fill="{changed_color}" opacity="0.62" />')

    tx, ty = project(rule.target_lon, rule.target_lat, bbox, width, height)
    elements.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="8" fill="none" stroke="#111" stroke-width="2.4" />')
    elements.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="3" fill="#111" />')

    return f"""
    <section class="panel">
      <h2>{escape(title)}</h2>
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
        {''.join(elements)}
      </svg>
    </section>
    """


def write_html_report(
    path: Path,
    baseline: list[Trip],
    scenario: list[Trip],
    rule: ScenarioRule,
    summary: dict[str, object],
    candidates: list[int],
    changed_indices: list[int],
    args: argparse.Namespace,
) -> None:
    changed_sample = sample_indices(changed_indices, args.sample_lines, rule.random_seed)
    changed_set = set(changed_indices)
    background_pool = [i for i in candidates if i not in changed_set]
    if not background_pool:
        background_pool = [i for i in range(len(baseline)) if i not in changed_set]
    background_sample = sample_indices(background_pool, args.background_points, rule.random_seed + 1)

    points: list[tuple[float, float]] = [(rule.target_lon, rule.target_lat)]
    for i in changed_sample:
        before = baseline[i]
        after = scenario[i]
        points.extend(
            [
                (before.origin_lon, before.origin_lat),
                (before.destination_lon, before.destination_lat),
                (after.destination_lon, after.destination_lat),
            ]
        )
    for i in background_sample:
        trip = baseline[i]
        points.append((trip.destination_lon, trip.destination_lat))
    bbox = make_bbox(points)

    before_panel = svg_panel(
        "Before: 変更前の目的地",
        baseline,
        baseline,
        changed_sample,
        background_sample,
        bbox,
        rule,
        "#2563eb",
    )
    after_panel = svg_panel(
        "After: シナリオ適用後",
        scenario,
        baseline,
        changed_sample,
        background_sample,
        bbox,
        rule,
        "#dc2626",
    )

    rows = [
        ("総トリップ数", f"{summary['total_trips']:,}"),
        ("候補トリップ数", f"{summary['candidate_trips']:,}"),
        ("変更トリップ数", f"{summary['changed_trips']:,} ({summary['changed_share_of_all']:.1%})"),
        ("対象地点", f"{rule.target_label} / {rule.target_lon:.6f}, {rule.target_lat:.6f}"),
        ("対象時間帯", str(summary["time_window"])),
        ("対象目的コード", ", ".join(rule.affected_purposes) or "all"),
        ("平均距離 Before", f"{summary['avg_distance_to_target_before_km']:.2f} km"),
        ("平均距離 After", f"{summary['avg_distance_to_target_after_km']:.2f} km"),
    ]
    metrics_html = "".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>" for label, value in rows
    )
    notes_html = "".join(f"<li>{escape(note)}</li>" for note in summary["notes"])

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>擬似人流シナリオ比較</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --ink: #1e293b;
      --muted: #64748b;
      --line: #d4d0c7;
      --panel: #fffdf8;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 24px clamp(18px, 4vw, 48px) 14px;
      border-bottom: 1px solid var(--line);
      background: #fffaf0;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(24px, 3vw, 38px);
      letter-spacing: 0;
    }}
    .scenario {{
      max-width: 980px;
      margin: 0;
      color: var(--muted);
      line-height: 1.65;
      font-size: 15px;
    }}
    main {{ padding: 20px clamp(14px, 3vw, 38px) 34px; }}
    .metrics {{
      display: grid;
      grid-template-columns: minmax(260px, 440px) 1fr;
      gap: 18px;
      align-items: start;
      margin-bottom: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
    }}
    th {{ width: 42%; color: var(--muted); font-weight: 600; }}
    .notes {{
      margin: 0;
      padding: 14px 18px 14px 30px;
      background: var(--panel);
      border: 1px solid var(--line);
      line-height: 1.6;
      color: var(--muted);
      font-size: 14px;
    }}
    .comparison {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .panel {{
      min-width: 0;
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 12px;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 17px;
      letter-spacing: 0;
    }}
    svg {{
      width: 100%;
      aspect-ratio: 3 / 2;
      display: block;
      border: 1px solid var(--line);
      background: #f7f5ef;
    }}
    footer {{
      padding: 0 clamp(14px, 3vw, 38px) 28px;
      color: var(--muted);
      font-size: 13px;
    }}
    code {{ color: var(--accent); }}
    @media (max-width: 900px) {{
      .metrics, .comparison {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>擬似人流シナリオ比較</h1>
    <p class="scenario">{escape(rule.scenario_text)}</p>
  </header>
  <main>
    <section class="metrics">
      <table>{metrics_html}</table>
      <ul class="notes">{notes_html}</ul>
    </section>
    <section class="comparison">
      {before_panel}
      {after_panel}
    </section>
  </main>
  <footer>
    出力: <code>{escape(str(path.name))}</code>。線はサンプル表示で、CSVには全件を書き出しています。
  </footer>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    scenario_text = read_scenario_text(args)
    trips = load_trips(args.input)
    rule = build_rule(trips, scenario_text, args)
    scenario_trips, candidates, changed_indices, selection_notes = apply_scenario(trips, rule)
    summary = build_summary(trips, scenario_trips, rule, candidates, changed_indices, selection_notes)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline_csv = args.output_dir / "baseline_trips.csv"
    scenario_csv = args.output_dir / "scenario_trips.csv"
    changed_csv = args.output_dir / "changed_trips.csv"
    rule_json = args.output_dir / "scenario_rule.json"
    summary_json = args.output_dir / "comparison_summary.json"
    html_report = args.output_dir / "comparison.html"

    write_trips(baseline_csv, trips)
    write_trips(scenario_csv, scenario_trips)
    write_changed_trips(changed_csv, trips, scenario_trips, changed_indices)
    write_json(rule_json, asdict(rule))
    write_json(summary_json, summary)
    write_html_report(html_report, trips, scenario_trips, rule, summary, candidates, changed_indices, args)

    print("Done.")
    print(f"Rule: {rule_json}")
    print(f"Summary: {summary_json}")
    print(f"Baseline trips: {baseline_csv}")
    print(f"Scenario trips: {scenario_csv}")
    print(f"Changed trips: {changed_csv}")
    print(f"HTML comparison: {html_report}")
    print(
        "Changed "
        f"{summary['changed_trips']:,} / {summary['total_trips']:,} trips "
        f"toward {rule.target_label} ({rule.target_lon:.6f}, {rule.target_lat:.6f})."
    )


if __name__ == "__main__":
    main()
