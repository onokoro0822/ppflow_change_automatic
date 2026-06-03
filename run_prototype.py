#!/usr/bin/env python3
"""End-to-end prototype for scenario-based pseudo people-flow changes.

This script is intentionally stdlib-only so it can run on a fresh Python 3
environment. It reads headerless Pseudo-PFLOW trip OD data, optionally asks
Ollama to infer scenario settings, moves a deterministic sample of destinations
toward a target area, and writes a self-contained HTML comparison.
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
import urllib.error
import urllib.parse
import urllib.request
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
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"
DEFAULT_OLLAMA_TIMEOUT = 120.0
DEFAULT_GEOCODER_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_GEOCODER_TIMEOUT = 15.0
DEFAULT_GEOCODER_USER_AGENT = "ppflow-change-automatic/0.1 local research prototype"

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
    "home": ["1"],
    "commute": ["2"],
    "school": ["3"],
    "shopping": ["100"],
    "dining": ["200"],
    "hospital": ["300"],
    "free": ["400"],
    "business": ["500"],
}

GENERIC_TARGET_LABELS = {"", "駅前", "商業施設", "大型商業施設", "新設商業施設", "仮想目的地"}
GEOCODE_CACHE: dict[tuple[str, str], dict[str, object] | None] = {}

LLM_SYSTEM_PROMPT = """
あなたは都市計画シナリオを擬似人流データの変更ルールに変換するアシスタントです。

自然文を読み、以下のJSONだけを返してください。説明文、Markdown、コードブロックは禁止です。

{
  "scenario_name": "",
  "target_label": "",
  "target_location": {
    "lon": null,
    "lat": null
  },
  "affected_ratio": 0.08,
  "affected_purposes": [],
  "time_window": "all",
  "strength": 0.28,
  "influence_radius_km": 3.0,
  "notes": []
}

制約:
- affected_ratio は 0.02 から 0.20 の数値にしてください。大規模な施設でも 0.12 程度を標準にしてください。
- strength は 0.05 から 0.35 の数値にしてください。目的地へ完全に集めず、現実的な部分移動にしてください。
- influence_radius_km は 0.5 から 8.0 の数値にしてください。大型商業施設でも 3.0km 程度を標準にしてください。
- time_window は "12-18" のような24時間表記、または "all" にしてください。
- affected_purposes はこのプロトタイプで使える目的コードだけを返してください。
  - 在宅: "1"
  - 通勤: "2"
  - 通学: "3"
  - 買い物、商業、ショッピング: "100"
  - 外食、飲食、食事、レストラン、カフェ: "200"
  - 通院、病院: "300"
  - 自由行動、余暇、レジャー: "400"
  - 業務、仕事、出張、営業: "500"
  - 対象が不明、または上記以外なら [] にしてください。
- lon/lat は文章に明示されている場合だけ数値にしてください。推測で座標を作らないでください。
- target_label は地名や施設名を短く入れてください。
"""


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
    influence_radius_km: float
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
    parser.add_argument("--sample-lines", type=int, default=550, help="Changed trips drawn in HTML.")
    parser.add_argument("--background-points", type=int, default=800, help="Context points drawn in HTML.")
    parser.add_argument("--llm", action="store_true", help="Use Ollama to infer scenario settings.")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--no-geocode", action="store_true", help="Disable Nominatim geocoding.")
    parser.add_argument("--geocoder-url", default=DEFAULT_GEOCODER_URL)
    parser.add_argument("--geocoder-timeout", type=float, default=DEFAULT_GEOCODER_TIMEOUT)
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
    explicit_places = [
        ("千葉駅", "千葉駅"),
        ("千葉駅前", "千葉駅"),
        ("千葉市", "千葉市"),
        ("アメリカ", "アメリカ"),
        ("米国", "アメリカ"),
        ("USA", "United States"),
        ("United States", "United States"),
    ]
    for keyword, label in explicit_places:
        if keyword in text:
            return label

    if "柏の葉キャンパス駅" in text:
        return "柏の葉キャンパス駅前"
    if "駅前" in text:
        return "駅前"
    if "商業施設" in text:
        return "新設商業施設"
    return "仮想目的地"


def is_compatible_llm_label(scenario_text: str, label: str) -> bool:
    if not label:
        return False
    if label in GENERIC_TARGET_LABELS or any(word in label for word in ["商業施設", "新設", "施設"]):
        return False
    if label in scenario_text or scenario_text in label:
        return True
    if label in {"United States"} and any(word in scenario_text for word in ["アメリカ", "米国", "USA"]):
        return True
    if label in {"アメリカ"} and any(word in scenario_text for word in ["United States", "USA", "米国"]):
        return True
    return False


def infer_purpose_codes(text: str) -> list[str]:
    purposes: list[str] = []
    if any(word in text for word in ["在宅", "自宅"]):
        purposes.extend(PURPOSE_HINTS["home"])
    if any(word in text for word in ["通勤", "出勤"]):
        purposes.extend(PURPOSE_HINTS["commute"])
    if any(word in text for word in ["通学", "登校"]):
        purposes.extend(PURPOSE_HINTS["school"])
    if any(word in text for word in ["買い物", "買物", "商業", "ショッピング"]):
        purposes.extend(PURPOSE_HINTS["shopping"])
    if any(word in text for word in ["外食", "飲食", "食事", "レストラン", "カフェ"]):
        purposes.extend(PURPOSE_HINTS["dining"])
    if any(word in text for word in ["通院", "病院", "診療", "クリニック"]):
        purposes.extend(PURPOSE_HINTS["hospital"])
    if any(word in text for word in ["自由行動", "余暇", "レジャー", "娯楽", "観光"]):
        purposes.extend(PURPOSE_HINTS["free"])
    if any(word in text for word in ["業務", "仕事", "出張", "営業"]):
        purposes.extend(PURPOSE_HINTS["business"])
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
        return 0.12
    if any(word in text for word in ["小規模", "少し", "一部"]):
        return 0.05
    return 0.08


def infer_influence_radius_km(text: str) -> float:
    if any(word in text for word in ["大型", "大規模", "広域", "集客"]):
        return 3.0
    if any(word in text for word in ["小規模", "少し", "一部"]):
        return 1.0
    if any(word in text for word in ["駅前", "商業施設", "ショッピング", "外食"]):
        return 2.0
    return 1.5


def clean_text(value: object) -> str:
    return str(value or "").strip()


def extract_json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Ollama response did not contain a JSON object.")
        payload = json.loads(cleaned[start : end + 1])

    if not isinstance(payload, dict):
        raise ValueError("Ollama response JSON was not an object.")
    return payload


def infer_rule_with_ollama(scenario_text: str, args: argparse.Namespace) -> dict[str, object]:
    payload = {
        "model": getattr(args, "ollama_model", DEFAULT_OLLAMA_MODEL),
        "messages": [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": scenario_text},
        ],
        "format": "json",
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        getattr(args, "ollama_url", DEFAULT_OLLAMA_URL),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(  # nosec B310: local configurable prototype endpoint.
            request,
            timeout=float(getattr(args, "ollama_timeout", DEFAULT_OLLAMA_TIMEOUT)),
        ) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama APIに接続できません: {exc}") from exc

    response_json = json.loads(response_body)
    message = response_json.get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if not content:
        raise ValueError("Ollama response did not include message.content.")
    return extract_json_object(str(content))


def normalize_purpose_codes(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("，", ",").split(",")]
    elif isinstance(value, list):
        items = [clean_text(item) for item in value]
    else:
        items = [clean_text(value)]

    codes: list[str] = []
    for item in items:
        if not item:
            continue
        lowered = item.lower()
        if lowered in {"all", "none", "null", "なし", "全て", "すべて"}:
            continue
        if re.fullmatch(r"\d+", item):
            codes.append(item)
            continue
        if any(word in item for word in ["在宅", "自宅"]):
            codes.extend(PURPOSE_HINTS["home"])
        if any(word in item for word in ["通勤", "出勤"]):
            codes.extend(PURPOSE_HINTS["commute"])
        if any(word in item for word in ["通学", "登校"]):
            codes.extend(PURPOSE_HINTS["school"])
        if any(word in item for word in ["買い物", "買物", "商業", "ショッピング"]):
            codes.extend(PURPOSE_HINTS["shopping"])
        if any(word in item for word in ["外食", "飲食", "食事", "レストラン", "カフェ"]):
            codes.extend(PURPOSE_HINTS["dining"])
        if any(word in item for word in ["通院", "病院", "診療", "クリニック"]):
            codes.extend(PURPOSE_HINTS["hospital"])
        if any(word in item for word in ["自由行動", "余暇", "レジャー", "娯楽", "観光"]):
            codes.extend(PURPOSE_HINTS["free"])
        if any(word in item for word in ["業務", "仕事", "出張", "営業"]):
            codes.extend(PURPOSE_HINTS["business"])

    return sorted(set(codes))


def parse_hour_value(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return clamp(float(value), 0.0, 24.0)
    match = re.search(r"\d{1,2}(?:\.\d+)?", clean_text(value))
    if not match:
        return None
    return clamp(float(match.group(0)), 0.0, 24.0)


def normalize_time_window(
    value: object,
    default: tuple[float, float] | None,
) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) >= 2:
        start = parse_hour_value(value[0])
        end = parse_hour_value(value[1])
        if start is None or end is None or start == end:
            return default
        return start, end
    return parse_time_window(clean_text(value), default)


def normalize_location(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lon = value.get("lon", value.get("lng"))
    lat = value.get("lat")
    if lon is None or lat is None:
        return None
    try:
        lon_float = float(lon)
        lat_float = float(lat)
    except (TypeError, ValueError):
        return None
    if not (-180.0 <= lon_float <= 180.0 and -90.0 <= lat_float <= 90.0):
        return None
    return lon_float, lat_float


def geocode_queries(label: str) -> list[str]:
    text = label.strip()
    candidates: list[str] = []
    if text:
        candidates.append(text)
    if "駅前" in text:
        candidates.append(text.replace("駅前", "駅"))
    if text.endswith("前") and len(text) > 1:
        candidates.append(text[:-1])

    expanded: list[str] = []
    for candidate in candidates:
        expanded.extend([candidate, f"{candidate} 日本"])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in expanded:
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def geocode_place(label: str, args: argparse.Namespace) -> dict[str, object] | None:
    endpoint = getattr(args, "geocoder_url", DEFAULT_GEOCODER_URL)
    cache_key = (endpoint, label.strip())
    if cache_key in GEOCODE_CACHE:
        return GEOCODE_CACHE[cache_key]

    headers = {
        "Accept": "application/json",
        "Accept-Language": "ja,en;q=0.8",
        "User-Agent": DEFAULT_GEOCODER_USER_AGENT,
    }

    for query in geocode_queries(label):
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "limit": "1",
            }
        )
        url = f"{endpoint}?{params}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(  # nosec B310: user-configurable prototype endpoint.
                request,
                timeout=float(getattr(args, "geocoder_timeout", DEFAULT_GEOCODER_TIMEOUT)),
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.URLError:
            continue

        try:
            results = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(results, list) or not results:
            continue

        first = results[0]
        if not isinstance(first, dict):
            continue
        try:
            lat = float(first["lat"])
            lon = float(first["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        result = {
            "query": query,
            "label": clean_text(first.get("display_name")) or query,
            "lon": lon,
            "lat": lat,
        }
        GEOCODE_CACHE[cache_key] = result
        return result

    GEOCODE_CACHE[cache_key] = None
    return None


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
    return clamp(ratio, 0.01, 0.30)


def parse_strength(value: str, default: float) -> float:
    text = value.strip()
    if not text:
        return default
    return clamp(float(text), 0.05, 0.70)


def parse_influence_radius_km(value: str, default: float) -> float:
    text = value.strip().lower().replace("km", "").replace("キロ", "")
    if not text:
        return default
    return clamp(float(text), 0.3, 10.0)


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
    strength = 0.28
    influence_radius_km = infer_influence_radius_km(scenario_text)
    scenario_name = scenario_text.replace("\n", " ")[:48] or "scenario"
    questions: list[dict[str, str]] = []
    notes = [
        "移動目的コードは入力CSV仕様に従い、100=買い物、200=外食、300=通院、400=自由行動、500=業務として処理します。",
        "地点名はNominatimでジオコーディングし、取得できない場合だけ既存トリップの高密度な目的地クラスタを使います。",
    ]
    location_source = "destination_cluster"

    if bool(getattr(args, "llm", False)):
        try:
            llm_rule = infer_rule_with_ollama(scenario_text, args)
            notes.append(
                f"Ollama({getattr(args, 'ollama_model', DEFAULT_OLLAMA_MODEL)})で自然文から初期ルールを生成しました。"
            )

            llm_name = clean_text(llm_rule.get("scenario_name"))
            if llm_name:
                scenario_name = llm_name[:48]

            llm_label = clean_text(
                llm_rule.get("target_label")
                or llm_rule.get("target_area")
                or (
                    llm_rule.get("target_location", {}).get("label")
                    if isinstance(llm_rule.get("target_location"), dict)
                    else ""
                )
            )
            if llm_label and is_compatible_llm_label(scenario_text, llm_label):
                target_label = llm_label
            elif llm_label:
                notes.append(f"LLMの地点名が自然文と一致しないため採用しませんでした: {llm_label}")

            location = normalize_location(llm_rule.get("target_location"))
            if location is not None:
                target_lon, target_lat = location
                location_source = "llm"
                notes.append(f"LLMが返した座標を目的地にしました: {target_lon:.6f}, {target_lat:.6f}")

            if "affected_ratio" in llm_rule:
                try:
                    ratio = parse_ratio(str(llm_rule["affected_ratio"]), ratio)
                except (TypeError, ValueError):
                    notes.append("LLMの影響割合が読めなかったため、従来推定値を使いました。")

            if "affected_purposes" in llm_rule:
                normalized_purposes = normalize_purpose_codes(llm_rule.get("affected_purposes"))
                if normalized_purposes is not None:
                    purposes = normalized_purposes

            time_value = llm_rule.get("time_window", llm_rule.get("affected_time"))
            if "time_window" in llm_rule or "affected_time" in llm_rule:
                time_window = normalize_time_window(time_value, time_window)

            if "strength" in llm_rule:
                try:
                    strength = min(parse_strength(str(llm_rule["strength"]), strength), 0.35)
                except (TypeError, ValueError):
                    notes.append("LLMの移動強度が読めなかったため、既定値を使いました。")

            if "influence_radius_km" in llm_rule:
                try:
                    influence_radius_km = parse_influence_radius_km(
                        str(llm_rule["influence_radius_km"]),
                        influence_radius_km,
                    )
                except (TypeError, ValueError):
                    notes.append("LLMの影響半径が読めなかったため、従来推定値を使いました。")

            llm_notes = llm_rule.get("notes", [])
            if isinstance(llm_notes, list):
                notes.extend(clean_text(note) for note in llm_notes[:3] if clean_text(note))
        except Exception as exc:
            notes.append(f"Ollama推定に失敗したため、従来のキーワード推定を使いました: {exc}")

    geocode_enabled = not bool(getattr(args, "no_geocode", False))
    if geocode_enabled and target_label not in GENERIC_TARGET_LABELS:
        try:
            geocoded = geocode_place(target_label, args)
        except Exception as exc:
            geocoded = None
            notes.append(f"ジオコーディングに失敗しました: {exc}")

        if geocoded is not None:
            geocoded_lon = float(geocoded["lon"])
            geocoded_lat = float(geocoded["lat"])
            target_lon = geocoded_lon
            target_lat = geocoded_lat
            location_source = "geocoder"
            notes.append(
                "Nominatimジオコーディングで目的地を設定しました: "
                f"{geocoded['query']} -> {geocoded['label']}"
            )
        elif location_source == "destination_cluster":
            target_label = "データ内高密度目的地クラスタ"
            notes.append("ジオコーディングで地点を取得できなかったため、データ内の高密度目的地クラスタを使いました。")
    elif not geocode_enabled and location_source == "destination_cluster":
        notes.append("ジオコーディングは無効です。データ内の高密度目的地クラスタを使いました。")

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

    default_radius = f"{influence_radius_km:g}"
    value, raw = ask("影響半径 km を入力してください", default_radius, interactive)
    influence_radius_km = parse_influence_radius_km(value, influence_radius_km)
    questions.append(
        {
            "id": "influence_radius_km",
            "question": "影響半径 km",
            "answer": raw or default_radius,
        }
    )

    return ScenarioRule(
        scenario_text=scenario_text,
        scenario_name=scenario_name,
        target_label=target_label,
        target_lon=target_lon,
        target_lat=target_lat,
        affected_ratio=ratio,
        affected_purposes=purposes,
        time_window=time_window,
        strength=strength,
        influence_radius_km=influence_radius_km,
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


def local_xy_km(lon: float, lat: float, ref_lon: float, ref_lat: float) -> tuple[float, float]:
    x = (lon - ref_lon) * 111.320 * max(0.2, math.cos(math.radians(ref_lat)))
    y = (lat - ref_lat) * 111.320
    return x, y


def point_to_trip_segment_distance_km(trip: Trip, target_lon: float, target_lat: float) -> float:
    ox, oy = local_xy_km(trip.origin_lon, trip.origin_lat, target_lon, target_lat)
    dx, dy = local_xy_km(trip.destination_lon, trip.destination_lat, target_lon, target_lat)
    vx = dx - ox
    vy = dy - oy
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(ox, oy)
    t = clamp(-((ox * vx + oy * vy) / denom), 0.0, 1.0)
    closest_x = ox + t * vx
    closest_y = oy + t * vy
    return math.hypot(closest_x, closest_y)


def apply_scenario(trips: list[Trip], rule: ScenarioRule) -> tuple[list[Trip], list[int], list[int], list[str]]:
    base_candidates, notes = choose_candidates(trips, rule)
    rng = random.Random(rule.random_seed)

    influence_radius_km = max(rule.influence_radius_km, 0.001)
    weighted_candidates: list[tuple[int, float, float]] = []
    for i in base_candidates:
        trip = trips[i]
        distance_km = point_to_trip_segment_distance_km(
            trip,
            rule.target_lon,
            rule.target_lat,
        )
        if distance_km > influence_radius_km:
            continue
        distance_weight = 1.0 - (distance_km / influence_radius_km)
        weighted_candidates.append((i, distance_km, distance_weight))

    notes.append(
        "影響圏モデル: "
        f"目的・時間候補 {len(base_candidates):,} 件のうち、"
        f"移動経路が施設から {influence_radius_km:g}km 以内を通る {len(weighted_candidates):,} 件を対象にしました。"
    )
    notes.append(
        f"選択確率は経路と施設の近さに対して線形減衰し、施設直近の最大確率を {rule.affected_ratio:.0%} としました。"
    )

    candidates = [i for i, _, _ in weighted_candidates]
    changed_indices = sorted(
        i
        for i, _, distance_weight in weighted_candidates
        if rng.random() < rule.affected_ratio * distance_weight
    )
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
    avg_before = statistics.fmean(before_distances) if before_distances else 0.0
    avg_after = statistics.fmean(after_distances) if after_distances else 0.0
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
        "influence_radius_km": rule.influence_radius_km,
        "avg_distance_to_target_before_km": avg_before,
        "avg_distance_to_target_after_km": avg_after,
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

    min_lon, max_lon, min_lat, max_lat = bbox
    map_payload = {
        "target": {
            "label": rule.target_label,
            "lon": rule.target_lon,
            "lat": rule.target_lat,
        },
        "bounds": [[min_lat, min_lon], [max_lat, max_lon]],
        "background": [
            [baseline[i].destination_lat, baseline[i].destination_lon] for i in background_sample
        ],
        "before": [
            [baseline[i].destination_lat, baseline[i].destination_lon] for i in changed_sample
        ],
        "after": [
            [scenario[i].destination_lat, scenario[i].destination_lon] for i in changed_sample
        ],
        "movements": [
            [
                baseline[i].destination_lat,
                baseline[i].destination_lon,
                scenario[i].destination_lat,
                scenario[i].destination_lon,
            ]
            for i in changed_sample
        ],
    }
    map_json = json.dumps(map_payload, ensure_ascii=False)

    rows = [
        ("総トリップ数", f"{summary['total_trips']:,}"),
        ("候補トリップ数", f"{summary['candidate_trips']:,}"),
        ("変更トリップ数", f"{summary['changed_trips']:,} ({summary['changed_share_of_all']:.1%})"),
        ("対象地点", f"{rule.target_label} / {rule.target_lon:.6f}, {rule.target_lat:.6f}"),
        ("対象時間帯", str(summary["time_window"])),
        ("対象目的コード", ", ".join(rule.affected_purposes) or "all"),
        ("影響半径", f"{rule.influence_radius_km:g} km"),
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
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
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
    .map {{
      width: 100%;
      height: min(62vh, 620px);
      min-height: 430px;
      border: 1px solid var(--line);
      background: #eef0ea;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .swatch {{
      width: 12px;
      height: 12px;
      border-radius: 999px;
      border: 1px solid rgba(15, 23, 42, 0.2);
      background: var(--swatch);
    }}
    .line-swatch {{
      width: 22px;
      height: 2px;
      background: var(--swatch);
      opacity: 0.65;
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
      <section class="panel">
        <h2>Before: 変更前の目的地</h2>
        <div id="beforeMap" class="map"></div>
        <div class="legend">
          <span><i class="swatch" style="--swatch:#2563eb"></i>変更対象の変更前目的地</span>
          <span><i class="swatch" style="--swatch:#8a8f98"></i>候補トリップの背景点</span>
          <span><i class="swatch" style="--swatch:#111827"></i>シナリオ対象地点</span>
        </div>
      </section>
      <section class="panel">
        <h2>After: シナリオ適用後</h2>
        <div id="afterMap" class="map"></div>
        <div class="legend">
          <span><i class="swatch" style="--swatch:#dc2626"></i>変更後目的地</span>
          <span><i class="line-swatch" style="--swatch:#dc2626"></i>変更前から変更後への移動</span>
          <span><i class="swatch" style="--swatch:#111827"></i>シナリオ対象地点</span>
        </div>
      </section>
    </section>
  </main>
  <footer>
    出力: <code>{escape(str(path.name))}</code>。線はサンプル表示で、CSVには全件を書き出しています。
  </footer>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const report = {map_json};

    function makeMap(id) {{
      const map = L.map(id, {{
        scrollWheelZoom: false,
        preferCanvas: true,
      }});
      L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }}).addTo(map);
      map.fitBounds(report.bounds, {{ padding: [18, 18] }});
      return map;
    }}

    function addPoints(map, points, color, radius, opacity) {{
      points.forEach(([lat, lon]) => {{
        L.circleMarker([lat, lon], {{
          radius,
          color,
          weight: 1,
          fillColor: color,
          fillOpacity: opacity,
          opacity: Math.min(1, opacity + 0.18),
        }}).addTo(map);
      }});
    }}

    function addTarget(map) {{
      const target = [report.target.lat, report.target.lon];
      L.circleMarker(target, {{
        radius: 8,
        color: "#111827",
        weight: 3,
        fillColor: "#ffffff",
        fillOpacity: 0.92,
      }}).addTo(map).bindTooltip(report.target.label, {{ direction: "top" }});
    }}

    function addMovements(map) {{
      report.movements.forEach(([beforeLat, beforeLon, afterLat, afterLon]) => {{
        L.polyline([[beforeLat, beforeLon], [afterLat, afterLon]], {{
          color: "#dc2626",
          weight: 1,
          opacity: 0.24,
        }}).addTo(map);
      }});
    }}

    function syncMaps(left, right) {{
      let moving = false;
      function mirror(source, target) {{
        if (moving) return;
        moving = true;
        target.setView(source.getCenter(), source.getZoom(), {{ animate: false }});
        moving = false;
      }}
      left.on("moveend", () => mirror(left, right));
      right.on("moveend", () => mirror(right, left));
    }}

    if (window.L) {{
      const beforeMap = makeMap("beforeMap");
      const afterMap = makeMap("afterMap");
      addPoints(beforeMap, report.background, "#8a8f98", 2, 0.22);
      addPoints(afterMap, report.background, "#8a8f98", 2, 0.16);
      addPoints(beforeMap, report.before, "#2563eb", 3, 0.58);
      addMovements(afterMap);
      addPoints(afterMap, report.after, "#dc2626", 3, 0.56);
      addTarget(beforeMap);
      addTarget(afterMap);
      syncMaps(beforeMap, afterMap);
    }} else {{
      document.querySelectorAll(".map").forEach((node) => {{
        node.textContent = "地図ライブラリを読み込めませんでした。ネットワーク接続を確認してください。";
      }});
    }}
  </script>
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
