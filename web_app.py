#!/usr/bin/env python3
"""Local web UI for the pseudo people-flow scenario prototype."""

from __future__ import annotations

import argparse
import csv
import json
import math
import mimetypes
import os
import random
import sys
import traceback
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from dialogue_session import DIALOGUE_SESSIONS
from run_prototype import (
    BASE_DIR,
    DEFAULT_GEOCODER_TIMEOUT,
    DEFAULT_GEOCODER_URL,
    DEFAULT_INPUT_CSV,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SCENARIO_FILES,
    Trip,
    build_rule,
    format_time_window,
    geocode_place,
    haversine_km,
    is_in_time_window,
    iter_trips,
    meters_to_lat_delta,
    meters_to_lon_delta,
    parse_influence_radius_km,
    parse_lon_lat,
    parse_ratio,
    parse_strength,
    parse_time_window,
    point_to_trip_segment_distance_km,
    write_html_report,
    write_json,
    write_trips,
)


WEB_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "web_latest"
NAGOYA_INPUT_DIR = BASE_DIR / "input" / "nagoya"
NAGOYA_PREVIEW_PER_FILE = 2000
MAX_BODY_BYTES = 64 * 1024
DEFAULT_WEB_OLLAMA_TIMEOUT = 60.0
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GEMINI_TIMEOUT = 60.0
DEFAULT_TAVILY_TIMEOUT = 20.0

TRIPS_CACHE = None
WEB_LLM_ENABLED = True
WEB_OLLAMA_URL = DEFAULT_OLLAMA_URL
WEB_OLLAMA_MODEL = DEFAULT_OLLAMA_MODEL
WEB_OLLAMA_TIMEOUT = DEFAULT_WEB_OLLAMA_TIMEOUT
WEB_GEMINI_API_KEY = ""
WEB_GEMINI_MODEL = DEFAULT_GEMINI_MODEL
WEB_GEMINI_TIMEOUT = DEFAULT_GEMINI_TIMEOUT
WEB_GEMINI_SEARCH_ENABLED = False
WEB_TAVILY_API_KEY = ""
WEB_TAVILY_TIMEOUT = DEFAULT_TAVILY_TIMEOUT
WEB_TAVILY_SEARCH_ENABLED = False


def nagoya_input_files() -> list[Path]:
    paths = sorted(NAGOYA_INPUT_DIR.glob("trip_231*.csv"))
    if len(paths) != 16:
        raise FileNotFoundError(
            f"名古屋市16区のCSVが必要です。{NAGOYA_INPUT_DIR} で {len(paths)} ファイル見つかりました。"
        )
    return paths


def get_trips():
    """Load a small balanced preview; full simulation streams all 16 files."""
    global TRIPS_CACHE
    if TRIPS_CACHE is None:
        TRIPS_CACHE = []
        for path in nagoya_input_files():
            TRIPS_CACHE.extend(islice(iter_trips([path]), NAGOYA_PREVIEW_PER_FILE))
    return TRIPS_CACHE


def default_scenario_text() -> str:
    for path in DEFAULT_SCENARIO_FILES:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def namespace(
    seed: int = 42,
    sample_lines: int = 1200,
    background_points: int = 800,
    use_llm: bool | None = None,
):
    return SimpleNamespace(
        yes=True,
        seed=seed,
        sample_lines=sample_lines,
        background_points=background_points,
        output_dir=WEB_OUTPUT_DIR,
        llm=WEB_LLM_ENABLED if use_llm is None else use_llm,
        ollama_url=WEB_OLLAMA_URL,
        ollama_model=WEB_OLLAMA_MODEL,
        ollama_timeout=WEB_OLLAMA_TIMEOUT,
    )


def infer_payload(
    scenario_text: str,
    seed: int = 42,
    use_llm: bool | None = None,
) -> dict[str, object]:
    trips = get_trips()
    args = namespace(seed=seed, use_llm=use_llm)
    rule = build_rule(trips, scenario_text, args)
    return {
        "scenario_text": scenario_text,
        "target_label": rule.target_label,
        "target_lon": rule.target_lon,
        "target_lat": rule.target_lat,
        "affected_ratio": rule.affected_ratio,
        "affected_ratio_percent": round(rule.affected_ratio * 100),
        "affected_purposes": rule.affected_purposes,
        "time_window": format_time_window(rule.time_window),
        "strength": rule.strength,
        "influence_radius_km": rule.influence_radius_km,
        "notes": rule.notes,
        "llm_enabled": args.llm,
        "dataset_label": "名古屋市16区",
        "dataset_files": len(nagoya_input_files()),
        "preview_trips": len(trips),
    }


def geocode_payload(label: str) -> dict[str, object]:
    target_label = label.strip()
    if not target_label:
        raise ValueError("座標を取得する対象地名がありません。")
    search_labels = [target_label]
    if "名鉄百貨店" in target_label:
        # The department-store name is fuzzily matched to an unrelated store by
        # Nominatim.  Its adjacent railway station is a stable map anchor.
        search_labels.insert(0, "名鉄名古屋駅")
    geocoder_args = SimpleNamespace(
        geocoder_url=DEFAULT_GEOCODER_URL,
        geocoder_timeout=DEFAULT_GEOCODER_TIMEOUT,
    )
    result = None
    for search_label in search_labels:
        candidate = geocode_place(search_label, geocoder_args)
        if candidate is None:
            continue
        matched_label = str(candidate.get("label") or "")
        if "名鉄百貨店" in target_label and not any(
            marker in matched_label for marker in ["名鉄名古屋駅", "名古屋駅"]
        ):
            continue
        result = candidate
        break
    if result is None:
        raise ValueError(
            f"「{target_label}」の座標を取得できませんでした。対象地名またはlon/latを確認してください。"
        )
    return {
        "target_label": target_label,
        "matched_label": str(result.get("label") or target_label),
        "target_lon": float(result["lon"]),
        "target_lat": float(result["lat"]),
    }


def parse_purposes(value: object, default: list[str]) -> list[str]:
    if value is None:
        return default
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if text.lower() in {"", "all", "none", "なし", "全て", "すべて"}:
        return []
    return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]


def reservoir_add(
    sample: list[object],
    item: object,
    seen_count: int,
    limit: int,
    rng: random.Random,
) -> None:
    if len(sample) < limit:
        sample.append(item)
        return
    replace_at = rng.randrange(seen_count)
    if replace_at < limit:
        sample[replace_at] = item


def apply_streaming_nagoya(
    rule,
    app_args: SimpleNamespace,
    changed_csv: Path,
) -> tuple[list[Trip], list[Trip], list[int], list[int], dict[str, object]]:
    purpose_set = set(rule.affected_purposes)
    influence_radius_km = max(rule.influence_radius_km, 0.001)
    selection_rng = random.Random(rule.random_seed)
    jitter_rng = random.Random(rule.random_seed + 17)
    candidate_sample_rng = random.Random(rule.random_seed + 29)
    changed_sample_rng = random.Random(rule.random_seed + 43)
    candidate_sample: list[object] = []
    changed_sample: list[object] = []
    candidate_sample_limit = max(app_args.background_points * 2, 1600)
    changed_sample_limit = max(app_args.sample_lines * 2, 2400)
    total_trips = 0
    purpose_time_candidates = 0
    candidate_trips = 0
    changed_trips = 0
    before_distance_sum = 0.0
    after_distance_sum = 0.0

    changed_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "person_id",
        "departure_time_sec",
        "trip_purpose",
        "before_destination_lon",
        "before_destination_lat",
        "after_destination_lon",
        "after_destination_lat",
    ]
    with changed_csv.open("w", encoding="utf-8", newline="") as changed_file:
        writer = csv.DictWriter(changed_file, fieldnames=fieldnames)
        writer.writeheader()
        for trip in iter_trips(nagoya_input_files()):
            total_trips += 1
            if purpose_set and trip.trip_purpose not in purpose_set:
                continue
            if not is_in_time_window(trip, rule.time_window):
                continue
            purpose_time_candidates += 1
            distance_km = point_to_trip_segment_distance_km(
                trip,
                rule.target_lon,
                rule.target_lat,
            )
            if distance_km > influence_radius_km:
                continue
            candidate_trips += 1
            reservoir_add(
                candidate_sample,
                trip,
                candidate_trips,
                candidate_sample_limit,
                candidate_sample_rng,
            )
            distance_weight = 1.0 - (distance_km / influence_radius_km)
            if selection_rng.random() >= rule.affected_ratio * distance_weight:
                continue

            jitter_m = jitter_rng.gauss(0.0, 85.0)
            jitter_angle = jitter_rng.random() * math.tau
            jitter_lon = meters_to_lon_delta(
                math.cos(jitter_angle) * jitter_m,
                rule.target_lat,
            )
            jitter_lat = meters_to_lat_delta(math.sin(jitter_angle) * jitter_m)
            changed = replace(
                trip,
                destination_lon=(
                    trip.destination_lon
                    + (rule.target_lon - trip.destination_lon) * rule.strength
                    + jitter_lon
                ),
                destination_lat=(
                    trip.destination_lat
                    + (rule.target_lat - trip.destination_lat) * rule.strength
                    + jitter_lat
                ),
                changed=True,
            )
            changed_trips += 1
            before_distance_sum += haversine_km(
                trip.destination_lon,
                trip.destination_lat,
                rule.target_lon,
                rule.target_lat,
            )
            after_distance_sum += haversine_km(
                changed.destination_lon,
                changed.destination_lat,
                rule.target_lon,
                rule.target_lat,
            )
            writer.writerow(
                {
                    "person_id": trip.person_id,
                    "departure_time_sec": trip.departure_time_sec,
                    "trip_purpose": trip.trip_purpose,
                    "before_destination_lon": trip.destination_lon,
                    "before_destination_lat": trip.destination_lat,
                    "after_destination_lon": changed.destination_lon,
                    "after_destination_lat": changed.destination_lat,
                }
            )
            reservoir_add(
                changed_sample,
                (trip, changed),
                changed_trips,
                changed_sample_limit,
                changed_sample_rng,
            )

    before_sample = [pair[0] for pair in changed_sample]
    after_sample = [pair[1] for pair in changed_sample]
    background = [trip for trip in candidate_sample]
    baseline_display = before_sample + background
    scenario_display = after_sample + background
    changed_indices = list(range(len(before_sample)))
    candidate_indices = list(range(len(baseline_display)))
    avg_before = before_distance_sum / changed_trips if changed_trips else 0.0
    avg_after = after_distance_sum / changed_trips if changed_trips else 0.0
    summary = {
        "total_trips": total_trips,
        "candidate_trips": candidate_trips,
        "changed_trips": changed_trips,
        "changed_share_of_all": changed_trips / total_trips if total_trips else 0.0,
        "target": {
            "label": rule.target_label,
            "lon": rule.target_lon,
            "lat": rule.target_lat,
        },
        "affected_purposes": rule.affected_purposes,
        "time_window": format_time_window(rule.time_window),
        "movement_strength": rule.strength,
        "influence_radius_km": rule.influence_radius_km,
        "avg_distance_to_target_before_km": avg_before,
        "avg_distance_to_target_after_km": avg_after,
        "notes": rule.notes
        + [
            "名古屋市16区のCSV全件をストリーミング走査しました。",
            f"目的・時間条件に合う {purpose_time_candidates:,} 件のうち、移動経路が対象地点から {influence_radius_km:g}km 以内を通る {candidate_trips:,} 件を候補にしました。",
            "地図とbaseline/scenario CSVは表示用サンプル、changed CSVは変更対象の全件です。",
        ],
    }
    return baseline_display, scenario_display, candidate_indices, changed_indices, summary


def run_pipeline(payload: dict[str, object]) -> dict[str, object]:
    scenario_text = str(payload.get("scenario_text") or "").strip()
    if not scenario_text:
        raise ValueError("シナリオ文を入力してください。")

    seed = int(payload.get("seed") or 42)
    app_args = namespace(seed=seed, use_llm=bool(payload.get("use_llm", False)))
    trips = get_trips()
    rule = build_rule(trips, scenario_text, app_args)

    target_value = f"{payload.get('target_lon', rule.target_lon)},{payload.get('target_lat', rule.target_lat)}"
    target_lon, target_lat = parse_lon_lat(target_value, rule.target_lon, rule.target_lat)
    affected_ratio = parse_ratio(str(payload.get("affected_ratio", rule.affected_ratio)), rule.affected_ratio)
    strength = parse_strength(str(payload.get("strength", rule.strength)), rule.strength)
    influence_radius_km = parse_influence_radius_km(
        str(payload.get("influence_radius_km", rule.influence_radius_km)),
        rule.influence_radius_km,
    )
    time_window = parse_time_window(str(payload.get("time_window", format_time_window(rule.time_window))), rule.time_window)
    affected_purposes = parse_purposes(payload.get("affected_purposes"), rule.affected_purposes)
    target_label = str(payload.get("target_label") or rule.target_label).strip() or rule.target_label

    questions = [
        {
            "id": "target_location",
            "question": "集めたい地点の lon,lat",
            "answer": f"{target_lon:.6f},{target_lat:.6f}",
        },
        {
            "id": "affected_ratio",
            "question": "影響させる候補トリップの割合",
            "answer": f"{affected_ratio:.0%}",
        },
        {
            "id": "time_window",
            "question": "対象時間帯",
            "answer": format_time_window(time_window),
        },
        {
            "id": "influence_radius_km",
            "question": "対象地点からの影響半径",
            "answer": f"{influence_radius_km:g}km",
        },
    ]

    rule = replace(
        rule,
        target_label=target_label,
        target_lon=target_lon,
        target_lat=target_lat,
        affected_ratio=affected_ratio,
        affected_purposes=affected_purposes,
        time_window=time_window,
        strength=strength,
        influence_radius_km=influence_radius_km,
        questions=questions,
    )

    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    baseline_csv = WEB_OUTPUT_DIR / "baseline_sample.csv"
    scenario_csv = WEB_OUTPUT_DIR / "scenario_sample.csv"
    changed_csv = WEB_OUTPUT_DIR / "changed_trips.csv"
    rule_json = WEB_OUTPUT_DIR / "scenario_rule.json"
    summary_json = WEB_OUTPUT_DIR / "comparison_summary.json"
    html_report = WEB_OUTPUT_DIR / "comparison.html"

    baseline_sample, scenario_sample, candidates, changed_indices, summary = apply_streaming_nagoya(
        rule,
        app_args,
        changed_csv,
    )
    write_trips(baseline_csv, baseline_sample)
    write_trips(scenario_csv, scenario_sample)
    write_json(rule_json, asdict(rule))
    write_json(summary_json, summary)
    write_html_report(
        html_report,
        baseline_sample,
        scenario_sample,
        rule,
        summary,
        candidates,
        changed_indices,
        app_args,
    )

    return {
        "rule": asdict(rule),
        "summary": summary,
        "files": {
            "comparison_html": "/output/web_latest/comparison.html",
            "baseline_csv": "/output/web_latest/baseline_sample.csv",
            "scenario_csv": "/output/web_latest/scenario_sample.csv",
            "changed_csv": "/output/web_latest/changed_trips.csv",
            "rule_json": "/output/web_latest/scenario_rule.json",
            "summary_json": "/output/web_latest/comparison_summary.json",
        },
    }


INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>擬似人流シナリオ</title>
  <style>
    :root {
      color-scheme: light;
      --page: #f6f7f9;
      --surface: #ffffff;
      --surface-soft: #f9faf7;
      --ink: #16202a;
      --muted: #607080;
      --line: #d8dee5;
      --teal: #0f766e;
      --teal-dark: #115e59;
      --blue: #2563eb;
      --red: #dc2626;
      --amber: #b45309;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif;
      background: var(--page);
      color: var(--ink);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 58px;
      padding: 0 22px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0;
    }
    .dataset-note {
      margin-top: 3px;
      color: var(--muted);
      font-size: 11px;
    }
    .status {
      min-width: 160px;
      color: var(--muted);
      font-size: 13px;
      text-align: right;
      white-space: nowrap;
    }
    main {
      display: grid;
      grid-template-columns: minmax(380px, 480px) minmax(0, 1fr);
      min-height: calc(100vh - 58px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--surface);
      padding: 18px;
      overflow: auto;
    }
    .workspace {
      min-width: 0;
      padding: 18px;
      overflow: auto;
    }
    section {
      border: 1px solid var(--line);
      background: var(--surface);
      margin-bottom: 14px;
    }
    section > h2 {
      margin: 0;
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 14px;
      letter-spacing: 0;
    }
    .field {
      padding: 12px;
      border-bottom: 1px solid #edf0f3;
    }
    .field:last-child { border-bottom: 0; }
    .chat-log {
      display: flex;
      flex-direction: column;
      gap: 9px;
      height: 320px;
      overflow: auto;
      padding: 12px;
      background: var(--surface-soft);
      border-bottom: 1px solid var(--line);
    }
    .chat-message {
      max-width: 91%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 9px 11px;
      font-size: 13px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .chat-message.assistant {
      align-self: flex-start;
      background: #fff;
    }
    .chat-message.user {
      align-self: flex-end;
      border-color: #93c5c0;
      background: #e7f4f1;
    }
    .chat-message.pending {
      color: var(--muted);
      border-style: dashed;
    }
    .web-sources {
      margin-top: 8px;
      padding-top: 7px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 11px;
    }
    .web-sources a {
      display: block;
      margin-top: 4px;
      color: var(--teal);
      overflow-wrap: anywhere;
    }
    .message-updates {
      margin-top: 8px;
      padding: 8px;
      border: 1px solid #d6e8e5;
      border-radius: 6px;
      background: #f3faf8;
      color: var(--muted);
      font-size: 11px;
      white-space: normal;
    }
    .message-updates strong {
      display: block;
      margin-bottom: 4px;
      color: var(--teal-dark);
    }
    .message-update-row {
      margin-top: 3px;
      overflow-wrap: anywhere;
    }
    .search-entry-point {
      margin-top: 7px;
      max-width: 100%;
      overflow: hidden;
    }
    textarea.chat-input {
      min-height: 84px;
    }
    .chat-hint {
      margin-top: 6px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.4;
    }
    label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    textarea,
    input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-size: 14px;
      line-height: 1.5;
      padding: 9px 10px;
      outline: none;
    }
    textarea {
      min-height: 154px;
      resize: vertical;
    }
    textarea:focus,
    input:focus {
      border-color: var(--teal);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.13);
    }
    .grid2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      padding: 12px;
    }
    .actions .wide { grid-column: 1 / -1; }
    .workflow-note {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    button,
    a.button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      font-size: 14px;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      padding: 8px 12px;
    }
    button.primary {
      border-color: var(--teal);
      background: var(--teal);
      color: #fff;
    }
    button.primary:hover { background: var(--teal-dark); }
    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric {
      border: 1px solid var(--line);
      background: var(--surface);
      padding: 12px;
      min-height: 82px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .metric strong {
      display: block;
      margin-top: 8px;
      font-size: clamp(18px, 2.2vw, 28px);
      letter-spacing: 0;
    }
    .metric.before strong { color: var(--blue); }
    .metric.after strong { color: var(--red); }
    .metric.note strong { color: var(--amber); }
    .plan-panel {
      border: 1px solid var(--line);
      background: var(--surface);
      margin-bottom: 14px;
    }
    .plan-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .plan-head h2 {
      margin: 0;
      font-size: 14px;
    }
    .phase-badge {
      border: 1px solid #8cc7c1;
      border-radius: 999px;
      background: #edf8f6;
      color: var(--teal-dark);
      font-size: 11px;
      font-weight: 700;
      padding: 4px 8px;
    }
    .plan-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .plan-item {
      min-width: 0;
      min-height: 70px;
      padding: 10px 12px;
      border-right: 1px solid #edf0f3;
      border-bottom: 1px solid #edf0f3;
    }
    .plan-item span {
      display: block;
      margin-bottom: 5px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }
    .plan-item strong {
      display: block;
      font-size: 13px;
      font-weight: 600;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .assumption-box {
      padding: 9px 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .assumption-box strong { color: var(--ink); }
    .viewer {
      border: 1px solid var(--line);
      background: var(--surface);
      min-height: 640px;
    }
    .viewer-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .viewer-head h2 {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0;
    }
    .links {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .links a {
      color: var(--teal-dark);
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
    }
    iframe {
      display: block;
      width: 100%;
      height: 720px;
      border: 0;
      background: var(--surface-soft);
    }
    .map-preview iframe {
      height: 220px;
      border-bottom: 1px solid var(--line);
    }
    .map-meta {
      display: grid;
      gap: 4px;
      padding: 10px 12px;
      font-size: 12px;
    }
    .map-meta strong {
      font-size: 13px;
      letter-spacing: 0;
    }
    .map-meta span {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .map-meta a {
      color: var(--teal-dark);
      font-weight: 700;
      text-decoration: none;
    }
    .legend {
      display: grid;
      gap: 6px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .legend-row {
      display: grid;
      grid-template-columns: 52px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
    }
    .legend-code {
      display: inline-flex;
      justify-content: center;
      min-width: 44px;
      border: 1px solid var(--line);
      border-radius: 4px;
      background: var(--surface-soft);
      color: var(--ink);
      font-weight: 700;
      padding: 2px 6px;
    }
    .empty {
      display: grid;
      place-items: center;
      min-height: 520px;
      color: var(--muted);
      text-align: center;
      padding: 24px;
    }
    .error {
      margin-bottom: 14px;
      border: 1px solid #f3b4b4;
      background: #fff5f5;
      color: #991b1b;
      padding: 12px;
      font-size: 14px;
      display: none;
    }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .plan-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      iframe { height: 680px; }
    }
    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; padding: 12px 16px; }
      .status { text-align: left; }
      aside, .workspace { padding: 12px; }
      .grid2, .actions, .metrics, .plan-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>都市計画対話・擬似人流シナリオ</h1>
      <div class="dataset-note">計画対話と人流シミュレーションを別工程で実行</div>
    </div>
    <div class="status" id="status">待機中</div>
  </header>
  <main>
    <aside>
      <section>
        <h2>計画エージェントとの対話</h2>
        <div class="chat-log" id="chatLog" aria-live="polite"></div>
        <div class="field">
          <label for="chatInput">回答・追加条件</label>
          <textarea class="chat-input" id="chatInput" placeholder="例：名鉄百貨店本店の空きフロアを暫定活用したい"></textarea>
          <div class="chat-hint" id="chatMode">対話セッションを準備しています</div>
        </div>
        <div class="actions">
          <button id="newSessionButton" type="button">新しい対話</button>
          <button class="primary" id="sendMessageButton" type="button">送信</button>
        </div>
      </section>

      <section>
        <h2>シミュレーション入力（別工程）</h2>
        <div class="field">
          <p class="workflow-note">上の対話はトリップデータを参照しません。計画条件の確定後、構造化された項目だけを人流解析パラメータへ変換します。人流計算では名古屋市16区のCSVを使用します。</p>
        </div>
        <div class="field">
          <label for="scenarioText">解析条件（構造化計画から生成）</label>
          <textarea id="scenarioText"></textarea>
        </div>
        <div class="actions">
          <button class="wide" id="applyDialogueButton" type="button" disabled>計画条件を人流解析へ反映</button>
        </div>
      </section>

      <section>
        <h2>確認</h2>
        <div class="field">
          <label for="targetLabel">地点名</label>
          <input id="targetLabel" type="text">
        </div>
        <div class="field grid2">
          <div>
            <label for="targetLon">lon</label>
            <input id="targetLon" type="number" step="0.000001">
          </div>
          <div>
            <label for="targetLat">lat</label>
            <input id="targetLat" type="number" step="0.000001">
          </div>
        </div>
        <div class="field grid2">
          <div>
            <label for="affectedRatio">影響割合</label>
            <input id="affectedRatio" type="number" min="5" max="50" step="1">
          </div>
          <div>
            <label for="timeWindow">時間帯</label>
            <input id="timeWindow" type="text">
          </div>
        </div>
        <div class="field grid2">
          <div>
            <label for="affectedPurposes">目的コード</label>
            <input id="affectedPurposes" type="text">
            <div class="legend" aria-label="目的コード凡例">
              <div class="legend-row"><span class="legend-code">100</span><span>買い物・商業・ショッピング</span></div>
              <div class="legend-row"><span class="legend-code">200</span><span>飲食・食事・レストラン・カフェ</span></div>
              <div class="legend-row"><span class="legend-code">400</span><span>自由行動・イベント・文化</span></div>
              <div class="legend-row"><span class="legend-code">500</span><span>業務・オフィス</span></div>
              <div class="legend-row"><span class="legend-code">空欄</span><span>目的コードで絞り込まない</span></div>
            </div>
          </div>
          <div>
            <label for="strength">移動強度</label>
            <input id="strength" type="number" min="0.1" max="1" step="0.05">
          </div>
          <div>
            <label for="influenceRadius">影響半径（km）</label>
            <input id="influenceRadius" type="number" min="0.3" max="10" step="0.1">
          </div>
        </div>
        <div class="actions">
          <button id="inferButton" type="button">LLM推定</button>
          <button class="primary" id="runButton" type="button">比較を作成</button>
        </div>
      </section>

      <section>
        <h2>推定地点</h2>
        <div class="map-preview">
          <iframe id="targetMap" title="推定地点のGoogle Maps" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
          <div class="map-meta">
            <strong id="mapLabel">-</strong>
            <span id="mapCoords">-</span>
            <a id="mapLink" href="#" target="_blank" rel="noreferrer">Google Mapsで開く</a>
          </div>
        </div>
      </section>
    </aside>

    <div class="workspace">
      <div class="error" id="errorBox"></div>
      <div class="plan-panel">
        <div class="plan-head">
          <h2>構造化された計画条件</h2>
          <span class="phase-badge" id="phaseBadge">対象地</span>
        </div>
        <div class="plan-grid" id="planSummary"></div>
        <div class="assumption-box" id="assumptionBox">LLMの仮定はここに表示されます。</div>
      </div>
      <div class="metrics">
        <div class="metric"><span>変更トリップ</span><strong id="changedTrips">-</strong></div>
        <div class="metric"><span>候補トリップ</span><strong id="candidateTrips">-</strong></div>
        <div class="metric before"><span>Before 平均距離</span><strong id="beforeKm">-</strong></div>
        <div class="metric after"><span>After 平均距離</span><strong id="afterKm">-</strong></div>
      </div>
      <div class="viewer">
        <div class="viewer-head">
          <h2>比較</h2>
          <div class="links" id="links"></div>
        </div>
        <div class="empty" id="emptyState">比較結果はここに表示されます</div>
        <iframe id="comparisonFrame" title="擬似人流比較" hidden></iframe>
      </div>
    </div>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const status = $("status");
    const errorBox = $("errorBox");
    const runButton = $("runButton");
    const inferButton = $("inferButton");
    const sendMessageButton = $("sendMessageButton");
    const newSessionButton = $("newSessionButton");
    const applyDialogueButton = $("applyDialogueButton");
    let dialogueSessionId = null;
    let latestDialogueDraft = null;

    function setBusy(message, busy) {
      status.textContent = message;
      runButton.disabled = busy;
      inferButton.disabled = busy;
      sendMessageButton.disabled = busy;
      newSessionButton.disabled = busy;
      applyDialogueButton.disabled = busy || !(latestDialogueDraft && latestDialogueDraft.ready);
    }

    function setError(message) {
      errorBox.textContent = message || "";
      errorBox.style.display = message ? "block" : "none";
    }

    async function requestJSON(url, payload) {
      const options = payload
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          }
        : {};
      const response = await fetch(url, options);
      const data = await response.json();
      if (!response.ok || data.error) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    function formPayload() {
      return {
        scenario_text: $("scenarioText").value,
        target_label: $("targetLabel").value,
        target_lon: $("targetLon").value,
        target_lat: $("targetLat").value,
        affected_ratio: Number($("affectedRatio").value || 0) / 100,
        time_window: $("timeWindow").value,
        affected_purposes: $("affectedPurposes").value,
        strength: Number($("strength").value || 0),
        influence_radius_km: Number($("influenceRadius").value || 0),
      };
    }

    function displayValue(value, suffix = "") {
      if (Array.isArray(value)) {
        return value.length ? value.join("、") : "未設定";
      }
      if (value === null || value === undefined || value === "") {
        return "未設定";
      }
      return `${value}${suffix}`;
    }

    function appendPlanItem(label, value) {
      const item = document.createElement("div");
      item.className = "plan-item";
      const heading = document.createElement("span");
      heading.textContent = label;
      const content = document.createElement("strong");
      content.textContent = value;
      item.append(heading, content);
      $("planSummary").appendChild(item);
    }

    function renderDialogue(session) {
      dialogueSessionId = session.id;
      const chatLog = $("chatLog");
      chatLog.replaceChildren();
      (session.messages || []).forEach((message) => {
        const node = document.createElement("div");
        node.className = `chat-message ${message.role === "user" ? "user" : "assistant"}`;
        const text = document.createElement("div");
        text.textContent = message.content;
        node.appendChild(text);
        const appliedUpdates = message.applied_updates || [];
        const appliedAssumptions = message.applied_assumptions || [];
        if (appliedUpdates.length || appliedAssumptions.length || message.turn_type === "clarification" || message.turn_type === "confirmation") {
          const updateBox = document.createElement("div");
          updateBox.className = "message-updates";
          const updateLabel = document.createElement("strong");
          if (message.turn_type === "clarification") {
            updateLabel.textContent = "今回は計画条件を変更していません";
          } else if (message.turn_type === "confirmation") {
            updateLabel.textContent = "計画条件を確定しました";
          } else {
            updateLabel.textContent = "今回反映した計画条件";
          }
          updateBox.appendChild(updateLabel);
          appliedUpdates.forEach((item) => {
            const row = document.createElement("div");
            row.className = "message-update-row";
            row.textContent = `${item.label}（${item.field}）= ${item.value}`;
            updateBox.appendChild(row);
          });
          appliedAssumptions.forEach((item) => {
            const row = document.createElement("div");
            row.className = "message-update-row";
            row.textContent = `参考推定（${item.field}）= ${item.value} / ${item.reason || "Web検索結果に基づく"}`;
            updateBox.appendChild(row);
          });
          node.appendChild(updateBox);
        }
        const sources = message.web_sources || [];
        if (message.web_search_used || sources.length) {
          const sourceBox = document.createElement("div");
          sourceBox.className = "web-sources";
          const label = document.createElement("div");
          label.textContent = `${message.web_search_provider || "Web"}検索で確認`;
          sourceBox.appendChild(label);
          sources.forEach((source) => {
            const link = document.createElement("a");
            link.href = source.url;
            link.target = "_blank";
            link.rel = "noreferrer";
            link.textContent = source.title || source.url;
            sourceBox.appendChild(link);
          });
          node.appendChild(sourceBox);
        }
        if (message.search_entry_point) {
          const entryPoint = document.createElement("div");
          entryPoint.className = "search-entry-point";
          entryPoint.innerHTML = message.search_entry_point;
          node.appendChild(entryPoint);
        }
        if (message.warning) {
          const warning = document.createElement("div");
          warning.className = "web-sources";
          warning.textContent = message.warning;
          node.appendChild(warning);
        }
        chatLog.appendChild(node);
      });
      chatLog.scrollTop = chatLog.scrollHeight;

      const plan = session.plan;
      $("phaseBadge").textContent = session.phase_label || "計画整理中";
      $("planSummary").replaceChildren();
      appendPlanItem("対象地", displayValue(plan.site.name));
      appendPlanItem("現状", displayValue(plan.site.existing_state));
      appendPlanItem("制約", displayValue(plan.site.constraints));
      appendPlanItem("施設種別", displayValue(plan.intent.facility_type));
      appendPlanItem("用途", displayValue(plan.intent.candidate_uses));
      appendPlanItem("想定利用者", displayValue(plan.intent.target_users));
      appendPlanItem("階数", displayValue(plan.scale.floors, "階"));
      appendPlanItem("床面積", displayValue(plan.scale.floor_area_sqm, "㎡"));
      appendPlanItem("収容人数", displayValue(plan.scale.capacity, "人"));
      appendPlanItem("曜日・時間帯", [plan.operations.days, plan.operations.time_window].filter(Boolean).join(" / ") || "未設定");
      appendPlanItem("接続条件", displayValue(plan.connections.items));
      appendPlanItem("比較条件", displayValue(plan.scenario.comparison_request));

      const assumptions = session.assumptions || [];
      if (assumptions.length) {
        $("assumptionBox").textContent = `LLMの仮定: ${assumptions.map((item) => `${item.field}=${item.value}（${item.status === "confirmed" ? "確認済み" : "要確認"}）`).join(" / ")}`;
      } else {
        $("assumptionBox").textContent = "LLMの仮定はまだありません。推定値は確定情報と分けて表示します。";
      }

      const modeLabels = {
        enabled: session.external_search_enabled
          ? `Geminiと対話中（必要時にTavily検索 / このセッション ${session.web_search_count || 0} 回・${session.web_search_credits_used || 0}クレジット）`
          : (session.web_search_provider === "Google"
            ? `Geminiと対話中（必要時にGoogle検索 / 検索 ${session.web_search_count || 0} 回）`
            : "Geminiと対話中（Tavily未設定または無効・Web検索なし）"),
        fallback: "Geminiに接続できないためルールベースで継続中",
        missing_key: "GEMINI_API_KEY未設定のためルールベースで動作中",
        rule_based: "高速対話モード（具体案を即時作成）",
      };
      $("chatMode").textContent = `${modeLabels[session.llm_status] || "対話中"} / セッション ${session.id.slice(0, 8)}`;

      latestDialogueDraft = session.simulation_draft || null;
      applyDialogueButton.disabled = !(latestDialogueDraft && latestDialogueDraft.ready);
    }

    async function applyDialogueToSimulation() {
      const draft = latestDialogueDraft;
      if (!draft || !draft.ready || !draft.scenario_text) {
        setError("計画条件を確定してから人流解析へ反映してください。");
        return;
      }
      setError("");
      setBusy("対象地の座標を取得中", true);
      $("scenarioText").value = draft.scenario_text;
      if (draft.target_label) {
        $("targetLabel").value = draft.target_label;
      }
      if ((draft.affected_purposes || []).length) {
        $("affectedPurposes").value = draft.affected_purposes.join(",");
      }
      if (draft.time_window) {
        $("timeWindow").value = draft.time_window;
      }
      if (draft.affected_ratio) {
        $("affectedRatio").value = Math.round(draft.affected_ratio * 100);
      }
      if (draft.strength) {
        $("strength").value = Number(draft.strength).toFixed(2);
      }
      if (draft.influence_radius_km) {
        $("influenceRadius").value = Number(draft.influence_radius_km).toFixed(1);
      }
      try {
        if (draft.target_label) {
          const location = await requestJSON("/api/geocode", { label: draft.target_label });
          $("targetLon").value = Number(location.target_lon).toFixed(6);
          $("targetLat").value = Number(location.target_lat).toFixed(6);
        }
        updateTargetMap();
        status.textContent = "構造化された計画条件を人流解析へ反映しました";
      } catch (error) {
        $("targetLon").value = "";
        $("targetLat").value = "";
        updateTargetMap();
        setError(error.message);
      } finally {
        setBusy(errorBox.textContent ? "座標取得エラー" : "待機中", false);
      }
    }

    function appendChatMessage(role, content, extraClass = "") {
      const node = document.createElement("div");
      node.className = `chat-message ${role} ${extraClass}`.trim();
      node.textContent = content;
      $("chatLog").appendChild(node);
      $("chatLog").scrollTop = $("chatLog").scrollHeight;
      return node;
    }

    async function createDialogueSession() {
      setBusy("対話を準備中", true);
      setError("");
      try {
        const session = await requestJSON("/api/sessions", { use_llm: true });
        renderDialogue(session);
        $("chatInput").value = "";
        setBusy("待機中", false);
      } catch (error) {
        setBusy("エラー", false);
        setError(error.message);
      }
    }

    async function sendDialogueMessage() {
      const message = $("chatInput").value.trim();
      if (!message || !dialogueSessionId) return;
      setBusy("具体案を整理中", true);
      setError("");
      $("chatInput").value = "";
      appendChatMessage("user", message);
      const pendingMessage = appendChatMessage("assistant", "入力内容から具体案を組み立てています…", "pending");
      let elapsedSeconds = 0;
      const progressTimer = window.setInterval(() => {
        elapsedSeconds += 1;
        if (elapsedSeconds >= 6) {
          status.textContent = "具体案を整理中";
          pendingMessage.textContent = "Geminiの応答を待っています。必要な場合はWebも確認しています…";
        }
      }, 1000);
      try {
        const session = await requestJSON(`/api/sessions/${dialogueSessionId}/messages`, { message });
        renderDialogue(session);
      } catch (error) {
        pendingMessage.textContent = "応答を取得できませんでした。もう一度送信してください。";
        pendingMessage.classList.remove("pending");
        $("chatInput").value = message;
        setError(error.message);
      } finally {
        window.clearInterval(progressTimer);
        setBusy(errorBox.textContent ? "エラー" : "待機中", false);
      }
    }

    function updateTargetMap() {
      const lonValue = $("targetLon").value.trim();
      const latValue = $("targetLat").value.trim();
      const label = $("targetLabel").value || "推定地点";

      if (!lonValue || !latValue) {
        $("targetMap").removeAttribute("src");
        $("mapLabel").textContent = "-";
        $("mapCoords").textContent = "-";
        $("mapLink").href = "#";
        return;
      }
      const lon = Number(lonValue);
      const lat = Number(latValue);
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) return;

      const query = encodeURIComponent(`${lat},${lon}`);
      $("targetMap").src = `https://maps.google.com/maps?q=${query}&z=16&output=embed`;
      $("mapLabel").textContent = label;
      $("mapCoords").textContent = `${lon.toFixed(6)}, ${lat.toFixed(6)}`;
      $("mapLink").href = `https://www.google.com/maps/search/?api=1&query=${query}`;
    }

    function fillDefaults(data) {
      $("scenarioText").value = data.scenario_text || $("scenarioText").value;
      $("targetLabel").value = data.target_label || "";
      $("targetLon").value = Number(data.target_lon).toFixed(6);
      $("targetLat").value = Number(data.target_lat).toFixed(6);
      $("affectedRatio").value = data.affected_ratio_percent || Math.round((data.affected_ratio || 0.25) * 100);
      $("timeWindow").value = data.time_window || "all";
      $("affectedPurposes").value = (data.affected_purposes || []).join(",");
      $("strength").value = Number(data.strength || 0.82).toFixed(2);
      $("influenceRadius").value = Number(data.influence_radius_km || 1.5).toFixed(1);
      updateTargetMap();
    }

    function ollamaWarning(data) {
      return (data.notes || []).find((note) => note.includes("Ollama推定に失敗")) || "";
    }

    function formatNumber(value) {
      return Number(value).toLocaleString("ja-JP");
    }

    function updateResults(data) {
      const summary = data.summary;
      $("changedTrips").textContent = formatNumber(summary.changed_trips);
      $("candidateTrips").textContent = formatNumber(summary.candidate_trips);
      $("beforeKm").textContent = `${Number(summary.avg_distance_to_target_before_km).toFixed(2)} km`;
      $("afterKm").textContent = `${Number(summary.avg_distance_to_target_after_km).toFixed(2)} km`;

      const files = data.files;
      $("links").innerHTML = [
        ["HTML", files.comparison_html],
        ["変更CSV", files.changed_csv],
        ["ルールJSON", files.rule_json],
        ["要約JSON", files.summary_json],
      ].map(([label, href]) => `<a href="${href}" target="_blank" rel="noreferrer">${label}</a>`).join("");

      $("emptyState").hidden = true;
      $("comparisonFrame").hidden = false;
      $("comparisonFrame").src = `${files.comparison_html}?t=${Date.now()}`;
    }

    async function loadDefaults() {
      setBusy("読み込み中", true);
      setError("");
      try {
        const data = await requestJSON("/api/defaults");
        fillDefaults(data);
        setBusy("待機中", false);
      } catch (error) {
        setBusy("エラー", false);
        setError(error.message);
      }
    }

    async function infer() {
      setBusy("推定中", true);
      setError("");
      try {
        const data = await requestJSON("/api/infer", { scenario_text: $("scenarioText").value });
        fillDefaults(data);
        setBusy("待機中", false);
        setError(ollamaWarning(data));
      } catch (error) {
        setBusy("エラー", false);
        setError(error.message);
      }
    }

    async function run() {
      setBusy("作成中", true);
      setError("");
      try {
        const data = await requestJSON("/api/run", formPayload());
        updateResults(data);
        setBusy("完了", false);
      } catch (error) {
        setBusy("エラー", false);
        setError(error.message);
      }
    }

    inferButton.addEventListener("click", infer);
    runButton.addEventListener("click", run);
    sendMessageButton.addEventListener("click", sendDialogueMessage);
    newSessionButton.addEventListener("click", createDialogueSession);
    applyDialogueButton.addEventListener("click", applyDialogueToSimulation);
    $("chatInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        sendDialogueMessage();
      }
    });
    ["targetLabel", "targetLon", "targetLat"].forEach((id) => {
      $(id).addEventListener("input", updateTargetMap);
    });
    loadDefaults().then(createDialogueSession);
  </script>
</body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PPFlowWeb/0.1"

    def do_GET(self) -> None:
        try:
            request_path = urlparse(self.path).path
            if request_path == "/":
                self.send_html(INDEX_HTML)
                return
            if request_path == "/api/defaults":
                self.send_json(infer_payload(default_scenario_text(), use_llm=False))
                return
            parts = request_path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                self.send_json(DIALOGUE_SESSIONS.get(parts[2]))
                return
            if request_path.startswith("/output/"):
                self.send_output_file(request_path.removeprefix("/output/"))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.send_json({"error": str(exc.args[0])}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        try:
            request_path = urlparse(self.path).path
            if request_path == "/api/sessions":
                payload = self.read_json()
                requested_llm = bool(payload.get("use_llm", True))
                configured = bool(WEB_GEMINI_API_KEY)
                dialogue_enabled = WEB_LLM_ENABLED and requested_llm and configured
                initial_status = None
                if WEB_LLM_ENABLED and requested_llm and not configured:
                    initial_status = "missing_key"
                self.send_json(
                    DIALOGUE_SESSIONS.create(
                        dialogue_enabled,
                        llm_model=WEB_GEMINI_MODEL,
                        web_search_enabled=WEB_GEMINI_SEARCH_ENABLED,
                        external_search_enabled=WEB_TAVILY_SEARCH_ENABLED,
                        initial_status=initial_status,
                    )
                )
                return
            if request_path == "/api/geocode":
                payload = self.read_json()
                self.send_json(geocode_payload(str(payload.get("label") or "")))
                return
            parts = request_path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "messages":
                payload = self.read_json()
                session = DIALOGUE_SESSIONS.add_message(
                    parts[2],
                    str(payload.get("message") or ""),
                    gemini_api_key=WEB_GEMINI_API_KEY,
                    gemini_model=WEB_GEMINI_MODEL,
                    gemini_timeout=WEB_GEMINI_TIMEOUT,
                    enable_web_search=WEB_GEMINI_SEARCH_ENABLED,
                    tavily_api_key=WEB_TAVILY_API_KEY,
                    tavily_timeout=WEB_TAVILY_TIMEOUT,
                    enable_external_search=WEB_TAVILY_SEARCH_ENABLED,
                )
                self.send_json(session)
                return
            if request_path == "/api/infer":
                payload = self.read_json()
                scenario_text = str(payload.get("scenario_text") or default_scenario_text()).strip()
                self.send_json(infer_payload(scenario_text, use_llm=WEB_LLM_ENABLED))
                return
            if request_path == "/api/run":
                payload = self.read_json()
                self.send_json(run_pipeline(payload))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.send_json({"error": str(exc.args[0])}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise ValueError("リクエストが大きすぎます。")
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_output_file(self, relative_path: str) -> None:
        safe_relative = Path(unquote(relative_path))
        if safe_relative.is_absolute() or ".." in safe_relative.parts:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        path = DEFAULT_OUTPUT_DIR / safe_relative
        try:
            path.resolve().relative_to(DEFAULT_OUTPUT_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return

        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local pseudo people-flow web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-llm", action="store_true", help="Disable Ollama inference in the web UI.")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_WEB_OLLAMA_TIMEOUT)
    parser.add_argument("--gemini-model", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--gemini-timeout", type=float, default=DEFAULT_GEMINI_TIMEOUT)
    parser.add_argument("--tavily-timeout", type=float, default=DEFAULT_TAVILY_TIMEOUT)
    parser.add_argument(
        "--no-tavily-search",
        action="store_true",
        help="Disable Tavily search even when TAVILY_API_KEY is set.",
    )
    parser.add_argument(
        "--web-search",
        action="store_true",
        help="Enable Google Search grounding (requires a supported paid Gemini API project).",
    )
    parser.add_argument(
        "--no-web-search",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> None:
    global WEB_GEMINI_API_KEY, WEB_GEMINI_MODEL, WEB_GEMINI_SEARCH_ENABLED, WEB_GEMINI_TIMEOUT
    global WEB_TAVILY_API_KEY, WEB_TAVILY_SEARCH_ENABLED, WEB_TAVILY_TIMEOUT
    global WEB_LLM_ENABLED, WEB_OLLAMA_MODEL, WEB_OLLAMA_TIMEOUT, WEB_OLLAMA_URL
    args = parse_args()
    WEB_LLM_ENABLED = not args.no_llm
    WEB_OLLAMA_URL = args.ollama_url
    WEB_OLLAMA_MODEL = args.ollama_model
    WEB_OLLAMA_TIMEOUT = args.ollama_timeout
    WEB_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    WEB_GEMINI_MODEL = args.gemini_model
    WEB_GEMINI_TIMEOUT = args.gemini_timeout
    WEB_GEMINI_SEARCH_ENABLED = args.web_search and not args.no_web_search
    WEB_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
    WEB_TAVILY_TIMEOUT = args.tavily_timeout
    WEB_TAVILY_SEARCH_ENABLED = bool(WEB_TAVILY_API_KEY) and not args.no_tavily_search
    try:
        server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    except OSError as exc:
        raise SystemExit(f"Server failed to start on {args.host}:{args.port}: {exc}") from exc

    url = f"http://{args.host}:{args.port}"
    print(f"Serving local UI: {url}")
    if WEB_LLM_ENABLED:
        if WEB_GEMINI_API_KEY:
            google_search_mode = "automatic" if WEB_GEMINI_SEARCH_ENABLED else "disabled"
            print(f"Gemini dialogue: {WEB_GEMINI_MODEL} (Google Search: {google_search_mode})")
            tavily_mode = "automatic / basic" if WEB_TAVILY_SEARCH_ENABLED else "disabled"
            print(f"External web search: Tavily ({tavily_mode})")
        else:
            print("Gemini dialogue: GEMINI_API_KEY is not set; using rule-based fallback")
        print(f"Simulation-rule Ollama inference: {WEB_OLLAMA_MODEL} at {WEB_OLLAMA_URL}")
    else:
        print("LLM inference: disabled")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
