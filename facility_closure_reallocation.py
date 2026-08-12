"""商業施設の閉店後を想定し、買い物トリップを代替目的地へ再配分する。

Pseudo-PFLOWには施設IDがないため、閉店施設は代表点と半径で表す。代替先には
データ内で観測された買い物目的地の集積点を使い、元DBを変更せず、座標差分と
Mobmap用Before/After CSVを別フォルダへ出力する。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from commercial_profiles import distance_band, time_band
from trip_chains import haversine_m


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "output" / "trip_chains" / "nagoya_trip_chains.sqlite3"
DEFAULT_SCENARIO = (
    BASE_DIR
    / "config"
    / "closure_scenarios"
    / "meitetsu_nagoya_closure_demo.json"
)
DEFAULT_OUTPUT_DIR = (
    BASE_DIR / "output" / "facility_closure" / "meitetsu_nagoya_closure_demo"
)

SELECTED_COLUMNS = (
    "mobmap_id",
    "focused_mobmap_id",
    "person_id",
    "incoming_trip_id",
    "outgoing_trip_id",
    "departure_time_sec",
    "time_band",
    "employment_status",
    "transport_mode",
    "trip_purpose",
    "previous_trip_purpose",
    "next_trip_purpose",
    "original_destination_lon",
    "original_destination_lat",
    "new_destination_lon",
    "new_destination_lat",
    "alternative_node_id",
    "alternative_observed_shopping_trips",
    "incoming_distance_before_km",
    "incoming_distance_after_km",
    "incoming_distance_band_before",
    "incoming_distance_band_after",
    "outgoing_distance_before_km",
    "outgoing_distance_after_km",
    "chain_gap_before_m",
    "chain_gap_after_m",
)

PERSON_SUMMARY_COLUMNS = (
    "person_id",
    "affected_visit_count",
    "changed_outgoing_origin_count",
    "mobmap_segment_count",
    "mobmap_ids",
)

COORDINATE_CHANGE_COLUMNS = (
    "person_id",
    "trip_id",
    "coordinate_role",
    "original_lon",
    "original_lat",
    "new_lon",
    "new_lat",
)

ALTERNATIVE_COLUMNS = (
    "alternative_node_id",
    "longitude",
    "latitude",
    "observed_shopping_trips",
    "observed_shopping_persons",
    "selected_people",
)

MOBMAP_COLUMNS = (
    "id",
    "time",
    "longitude",
    "latitude",
    "scenario",
    "person_id",
    "trip_id",
    "point_role",
    "trip_purpose",
    "transport_mode",
    "changed",
)

MODE_SPEED_KMH = {
    "0": 4.8,
    "1": 4.8,
    "2": 15.0,
    "3": 25.0,
    "4": 35.0,
    "5": 18.0,
    "6": 25.0,
}


def _hash_uniform(seed: int, key: str) -> float:
    """シードと識別キーから、再実行しても同じになる0〜1の疑似乱数を作る。"""

    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") + 1) / (2**64 + 1)


def deterministic_person_sample(
    rows: Iterable[dict[str, Any]], sample_size: int, seed: int
) -> list[dict[str, Any]]:
    """人物候補から、重複なしで指定人数を再現可能に抽出する。"""

    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        person_id = str(row["person_id"])
        if person_id in seen:
            raise ValueError(f"Duplicate person candidate: {person_id}")
        seen.add(person_id)
        ranked.append((_hash_uniform(seed, f"person:{person_id}"), person_id, row))
    if sample_size > len(ranked):
        raise ValueError(
            f"Requested {sample_size} people but only {len(ranked)} are affected"
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked[:sample_size]]


def _load_source_visits(
    connection: sqlite3.Connection,
    source: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    """閉店施設の範囲内に到着した対象目的のトリップと人物数を取得する。"""

    source_lon = float(source["longitude"])
    source_lat = float(source["latitude"])
    radius_m = float(source["radius_m"])
    purposes = {str(value) for value in source["eligible_trip_purpose_codes"]}
    latitude_delta = radius_m / 111_320.0
    longitude_delta = radius_m / (
        111_320.0 * math.cos(math.radians(source_lat))
    )
    placeholders = ",".join("?" for _ in purposes)
    rows = connection.execute(
        f"""
        SELECT
            trip_id, person_id, departure_time_sec,
            origin_lon, origin_lat, destination_lon, destination_lat,
            transport_mode, trip_purpose, employment_status,
            analysis_previous_trip_purpose, analysis_next_trip_id,
            analysis_next_trip_purpose, segment_index
        FROM analysis_trip_chain_rows
        WHERE analysis_eligible = 1
          AND trip_purpose IN ({placeholders})
          AND destination_lon BETWEEN ? AND ?
          AND destination_lat BETWEEN ? AND ?
        ORDER BY person_id, departure_time_sec, trip_id
        """,
        (
            *sorted(purposes),
            source_lon - longitude_delta,
            source_lon + longitude_delta,
            source_lat - latitude_delta,
            source_lat + latitude_delta,
        ),
    )
    visits: list[dict[str, Any]] = []
    people: set[str] = set()
    for row in rows:
        if (
            haversine_m(
                row["destination_lon"],
                row["destination_lat"],
                source_lon,
                source_lat,
            )
            > radius_m
        ):
            continue
        visits.append(dict(row))
        people.add(str(row["person_id"]))
    return visits, len(people)


def _load_alternative_nodes(
    connection: sqlite3.Connection,
    source: dict[str, Any],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """買い物目的地を座標ごとに集約し、閉店施設外の代替候補地点を作る。"""

    decimals = int(settings["coordinate_rounding_decimals"])
    if not 3 <= decimals <= 7:
        raise ValueError("coordinate_rounding_decimals must be between 3 and 7")
    minimum_visits = int(settings["minimum_observed_shopping_trips"])
    source_lon = float(source["longitude"])
    source_lat = float(source["latitude"])
    source_radius_m = float(source["radius_m"])
    rows = connection.execute(
        """
        SELECT
            ROUND(destination_lon, ?) AS rounded_lon,
            ROUND(destination_lat, ?) AS rounded_lat,
            AVG(destination_lon) AS longitude,
            AVG(destination_lat) AS latitude,
            COUNT(*) AS observed_shopping_trips,
            COUNT(DISTINCT person_id) AS observed_shopping_persons
        FROM analysis_trip_chain_rows
        WHERE analysis_eligible = 1 AND trip_purpose = '100'
        GROUP BY rounded_lon, rounded_lat
        HAVING COUNT(*) >= ?
        ORDER BY observed_shopping_trips DESC, rounded_lon, rounded_lat
        """,
        (decimals, decimals, minimum_visits),
    )
    nodes: list[dict[str, Any]] = []
    for row in rows:
        if (
            haversine_m(
                row["longitude"], row["latitude"], source_lon, source_lat
            )
            <= source_radius_m
        ):
            continue
        node = dict(row)
        node["alternative_node_id"] = (
            f"{float(row['rounded_lon']):.{decimals}f},"
            f"{float(row['rounded_lat']):.{decimals}f}"
        )
        nodes.append(node)
    if not nodes:
        raise ValueError("No alternative shopping destination nodes were found")
    return nodes


def choose_alternative_node(
    nodes: list[dict[str, Any]],
    origin_lon: float,
    origin_lat: float,
    settings: dict[str, Any],
    seed: int,
    selection_key: str,
) -> dict[str, Any]:
    """観測買い物件数と出発地からの距離を使い、Huff型確率で代替先を選ぶ。"""

    attraction_exponent = float(settings["attractiveness_exponent"])
    distance_exponent = float(settings["distance_decay_exponent"])
    minimum_distance_m = float(settings["minimum_distance_m"])
    if attraction_exponent < 0 or distance_exponent < 0:
        raise ValueError("Choice exponents must be non-negative")
    if minimum_distance_m <= 0:
        raise ValueError("minimum_distance_m must be positive")

    weighted: list[tuple[dict[str, Any], float]] = []
    total = 0.0
    for node in nodes:
        distance_m = max(
            minimum_distance_m,
            haversine_m(
                origin_lon,
                origin_lat,
                float(node["longitude"]),
                float(node["latitude"]),
            ),
        )
        attractiveness = float(node["observed_shopping_trips"])
        weight = attractiveness**attraction_exponent / distance_m**distance_exponent
        if weight <= 0 or not math.isfinite(weight):
            continue
        total += weight
        weighted.append((node, total))
    if not weighted:
        raise ValueError("No alternative destination has a positive choice weight")
    threshold = _hash_uniform(seed, f"destination:{selection_key}") * total
    for node, cumulative in weighted:
        if threshold <= cumulative:
            return node
    return weighted[-1][0]


def _fetch_outgoing_trip(
    connection: sqlite3.Connection, trip_id: int | None
) -> sqlite3.Row | None:
    """指定トリップの直後に続くトリップをSQLiteから取得する。"""

    if trip_id is None:
        return None
    return connection.execute(
        """
        SELECT trip_id, person_id, departure_time_sec, origin_lon, origin_lat,
               destination_lon, destination_lat, transport_mode, trip_purpose,
               analysis_next_departure_time_sec
        FROM analysis_trip_chain_rows WHERE trip_id = ?
        """,
        (trip_id,),
    ).fetchone()


def _fetch_analysis_segment(
    connection: sqlite3.Connection, person_id: str, segment_index: int
) -> list[dict[str, Any]]:
    """指定人物の同一分析系列に含まれる全トリップを時刻順に取得する。"""

    rows = connection.execute(
        """
        SELECT trip_id, person_id, departure_time_sec,
               origin_lon, origin_lat, destination_lon, destination_lat,
               transport_mode, trip_purpose,
               analysis_next_departure_time_sec
        FROM analysis_trip_chain_rows
        WHERE analysis_eligible = 1
          AND person_id = ? AND segment_index = ?
        ORDER BY segment_trip_index, trip_id
        """,
        (person_id, segment_index),
    )
    return [dict(row) for row in rows]


def _format_time(base_date: str, seconds: int) -> str:
    """0時からの秒数を、Mobmapで読める日時文字列へ変換する。"""

    base = datetime.strptime(base_date, "%Y-%m-%d")
    return (base + timedelta(seconds=int(seconds))).strftime("%Y-%m-%d %H:%M:%S")


def _estimated_arrival_time(
    trip: dict[str, Any],
    origin_lon: float,
    origin_lat: float,
    destination_lon: float,
    destination_lat: float,
) -> int:
    """OD間の直線距離と交通手段別の仮定速度から到着時刻を推定する。"""

    departure = int(trip["departure_time_sec"])
    distance_km = haversine_m(
        origin_lon, origin_lat, destination_lon, destination_lat
    ) / 1000.0
    speed_kmh = MODE_SPEED_KMH.get(str(trip["transport_mode"]), 20.0)
    duration = max(60, round(distance_km / speed_kmh * 3600))
    arrival = departure + duration
    next_departure = trip.get("analysis_next_departure_time_sec")
    if next_departure is not None and int(next_departure) > departure:
        arrival = min(arrival, int(next_departure) - 1)
    return max(departure, min(arrival, 86_399))


def _mobmap_rows_for_segment(
    segment: list[dict[str, Any]],
    mobmap_id: int,
    base_date: str,
    scenario_name: str,
    changes: dict[tuple[int, str], tuple[float, float]],
) -> list[dict[str, Any]]:
    """分析系列を、各トリップの出発点・到着点からなるMobmap用行へ変換する。"""

    result: list[dict[str, Any]] = []
    for trip in segment:
        trip_id = int(trip["trip_id"])
        original_origin = (float(trip["origin_lon"]), float(trip["origin_lat"]))
        original_destination = (
            float(trip["destination_lon"]),
            float(trip["destination_lat"]),
        )
        origin = changes.get((trip_id, "origin"), original_origin)
        destination = changes.get((trip_id, "destination"), original_destination)
        departure = int(trip["departure_time_sec"])
        arrival = _estimated_arrival_time(
            trip, origin[0], origin[1], destination[0], destination[1]
        )
        common = {
            "id": mobmap_id,
            "scenario": scenario_name,
            "person_id": trip["person_id"],
            "trip_id": trip_id,
            "trip_purpose": trip["trip_purpose"],
            "transport_mode": trip["transport_mode"],
        }
        result.append(
            {
                **common,
                "time": _format_time(base_date, departure),
                "longitude": origin[0],
                "latitude": origin[1],
                "point_role": "origin",
                "changed": int(origin != original_origin),
            }
        )
        result.append(
            {
                **common,
                "time": _format_time(base_date, arrival),
                "longitude": destination[0],
                "latitude": destination[1],
                "point_role": "destination",
                "changed": int(destination != original_destination),
            }
        )
    return result


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    """指定した列順とUTF-8形式で辞書データをCSVへ保存する。"""

    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _movement_only_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """MobmapのIDをトリップ単位に変え、移動間の滞在時間を表示対象から外す。"""

    return [{**row, "id": int(row["trip_id"])} for row in rows]


def build_closure_reallocation(
    database_path: Path,
    scenario_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """閉店後再配分の抽出・代替先選択・チェーン更新・CSV出力を一括実行する。"""

    database_path = Path(database_path)
    scenario_path = Path(scenario_path)
    output_dir = Path(output_dir)
    for path in (database_path, scenario_path):
        if not path.exists():
            raise FileNotFoundError(path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    source = scenario["source_facility"]
    alternatives_settings = scenario["alternative_destinations"]
    seed = int(scenario["random_seed"])
    configured_count = scenario.get("changed_person_count")

    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    affected_visits, affected_unique_people = _load_source_visits(connection, source)
    if not affected_visits:
        connection.close()
        raise ValueError("No affected shopping visitors were found")
    if configured_count is None:
        selected = list(affected_visits)
        selection_mode = "all_affected_people"
    else:
        requested_count = int(configured_count)
        first_visit_by_person: dict[str, dict[str, Any]] = {}
        for visit in affected_visits:
            first_visit_by_person.setdefault(str(visit["person_id"]), visit)
        selected = deterministic_person_sample(
            first_visit_by_person.values(), requested_count, seed
        )
        selection_mode = "deterministic_sample"
    alternative_nodes = _load_alternative_nodes(
        connection, source, alternatives_settings
    )

    selected_rows: list[dict[str, Any]] = []
    coordinate_changes: list[dict[str, Any]] = []
    mobmap_before: list[dict[str, Any]] = []
    mobmap_after: list[dict[str, Any]] = []
    focused_mobmap_before: list[dict[str, Any]] = []
    focused_mobmap_after: list[dict[str, Any]] = []
    selected_node_counts: Counter[str] = Counter()
    selected_person_ids: set[str] = set()
    selected_trip_ids: set[int] = set()
    with_outgoing = 0
    time_order_violations = 0
    continuity_violations = 0
    incoming_before_total = 0.0
    incoming_after_total = 0.0
    outgoing_before_total = 0.0
    outgoing_after_total = 0.0
    base_date = scenario["mobmap"]["base_date"]

    selected_segment_keys = sorted(
        {
            (str(row["person_id"]), int(row["segment_index"]))
            for row in selected
        }
    )
    mobmap_id_by_segment = {
        key: index for index, key in enumerate(selected_segment_keys, start=1)
    }
    changes_by_segment: dict[
        tuple[str, int], dict[tuple[int, str], tuple[float, float]]
    ] = {key: {} for key in selected_segment_keys}

    selected.sort(key=lambda row: (str(row["person_id"]), int(row["trip_id"])))
    for focused_mobmap_id, current in enumerate(selected, start=1):
        person_id = str(current["person_id"])
        trip_id = int(current["trip_id"])
        segment_key = (person_id, int(current["segment_index"]))
        mobmap_id = mobmap_id_by_segment[segment_key]
        alternative = choose_alternative_node(
            alternative_nodes,
            float(current["origin_lon"]),
            float(current["origin_lat"]),
            alternatives_settings,
            seed,
            f"{person_id}:{trip_id}",
        )
        new_lon = float(alternative["longitude"])
        new_lat = float(alternative["latitude"])
        node_id = str(alternative["alternative_node_id"])
        selected_node_counts[node_id] += 1
        selected_person_ids.add(person_id)
        selected_trip_ids.add(trip_id)

        outgoing_trip_id = current["analysis_next_trip_id"]
        outgoing = _fetch_outgoing_trip(connection, outgoing_trip_id)
        incoming_before_km = haversine_m(
            current["origin_lon"],
            current["origin_lat"],
            current["destination_lon"],
            current["destination_lat"],
        ) / 1000.0
        incoming_after_km = haversine_m(
            current["origin_lon"], current["origin_lat"], new_lon, new_lat
        ) / 1000.0
        incoming_before_total += incoming_before_km
        incoming_after_total += incoming_after_km

        outgoing_before_km = None
        outgoing_after_km = None
        gap_before_m = None
        gap_after_m = None
        if outgoing is not None:
            with_outgoing += 1
            if int(outgoing["departure_time_sec"]) < int(current["departure_time_sec"]):
                time_order_violations += 1
            outgoing_before_km = haversine_m(
                outgoing["origin_lon"],
                outgoing["origin_lat"],
                outgoing["destination_lon"],
                outgoing["destination_lat"],
            ) / 1000.0
            outgoing_after_km = haversine_m(
                new_lon,
                new_lat,
                outgoing["destination_lon"],
                outgoing["destination_lat"],
            ) / 1000.0
            outgoing_before_total += outgoing_before_km
            outgoing_after_total += outgoing_after_km
            gap_before_m = haversine_m(
                current["destination_lon"],
                current["destination_lat"],
                outgoing["origin_lon"],
                outgoing["origin_lat"],
            )
            gap_after_m = 0.0
            if gap_after_m > 0.001:
                continuity_violations += 1

        selected_rows.append(
            {
                "mobmap_id": mobmap_id,
                "focused_mobmap_id": focused_mobmap_id,
                "person_id": person_id,
                "incoming_trip_id": trip_id,
                "outgoing_trip_id": outgoing_trip_id,
                "departure_time_sec": current["departure_time_sec"],
                "time_band": time_band(int(current["departure_time_sec"])),
                "employment_status": current["employment_status"],
                "transport_mode": current["transport_mode"],
                "trip_purpose": current["trip_purpose"],
                "previous_trip_purpose": current["analysis_previous_trip_purpose"],
                "next_trip_purpose": current["analysis_next_trip_purpose"],
                "original_destination_lon": current["destination_lon"],
                "original_destination_lat": current["destination_lat"],
                "new_destination_lon": new_lon,
                "new_destination_lat": new_lat,
                "alternative_node_id": node_id,
                "alternative_observed_shopping_trips": alternative[
                    "observed_shopping_trips"
                ],
                "incoming_distance_before_km": incoming_before_km,
                "incoming_distance_after_km": incoming_after_km,
                "incoming_distance_band_before": distance_band(incoming_before_km),
                "incoming_distance_band_after": distance_band(incoming_after_km),
                "outgoing_distance_before_km": outgoing_before_km,
                "outgoing_distance_after_km": outgoing_after_km,
                "chain_gap_before_m": gap_before_m,
                "chain_gap_after_m": gap_after_m,
            }
        )
        coordinate_changes.append(
            {
                "person_id": person_id,
                "trip_id": trip_id,
                "coordinate_role": "destination",
                "original_lon": current["destination_lon"],
                "original_lat": current["destination_lat"],
                "new_lon": new_lon,
                "new_lat": new_lat,
            }
        )
        after_changes = changes_by_segment[segment_key]
        after_changes[(trip_id, "destination")] = (new_lon, new_lat)
        focused_changes = {(trip_id, "destination"): (new_lon, new_lat)}
        focused_segment = [dict(current)]
        if outgoing is not None:
            outgoing_id = int(outgoing["trip_id"])
            coordinate_changes.append(
                {
                    "person_id": person_id,
                    "trip_id": outgoing_id,
                    "coordinate_role": "origin",
                    "original_lon": outgoing["origin_lon"],
                    "original_lat": outgoing["origin_lat"],
                    "new_lon": new_lon,
                    "new_lat": new_lat,
                }
            )
            after_changes[(outgoing_id, "origin")] = (new_lon, new_lat)
            focused_changes[(outgoing_id, "origin")] = (new_lon, new_lat)
            focused_segment.append(dict(outgoing))
        focused_mobmap_before.extend(
            _mobmap_rows_for_segment(
                focused_segment,
                focused_mobmap_id,
                base_date,
                "before",
                {},
            )
        )
        focused_mobmap_after.extend(
            _mobmap_rows_for_segment(
                focused_segment,
                focused_mobmap_id,
                base_date,
                "after",
                focused_changes,
            )
        )
    for person_id, segment_index in selected_segment_keys:
        mobmap_id = mobmap_id_by_segment[(person_id, segment_index)]
        segment = _fetch_analysis_segment(connection, person_id, segment_index)
        mobmap_before.extend(
            _mobmap_rows_for_segment(
                segment, mobmap_id, base_date, "before", {}
            )
        )
        mobmap_after.extend(
            _mobmap_rows_for_segment(
                segment,
                mobmap_id,
                base_date,
                "after",
                changes_by_segment[(person_id, segment_index)],
            )
        )
    connection.close()

    if len(selected_trip_ids) != len(selected_rows):
        raise AssertionError("Selected incoming trips are not unique")

    person_visit_counts = Counter(str(row["person_id"]) for row in selected_rows)
    person_outgoing_counts = Counter(
        str(row["person_id"])
        for row in selected_rows
        if row["outgoing_trip_id"] is not None
    )
    person_segments: dict[str, list[int]] = {}
    for (person_id, _segment_index), mobmap_id in mobmap_id_by_segment.items():
        person_segments.setdefault(person_id, []).append(mobmap_id)
    person_summary_rows = [
        {
            "person_id": person_id,
            "affected_visit_count": person_visit_counts[person_id],
            "changed_outgoing_origin_count": person_outgoing_counts[person_id],
            "mobmap_segment_count": len(person_segments[person_id]),
            "mobmap_ids": "|".join(
                str(value) for value in sorted(person_segments[person_id])
            ),
        }
        for person_id in sorted(selected_person_ids)
    ]

    node_lookup = {
        str(node["alternative_node_id"]): node for node in alternative_nodes
    }
    alternative_rows = []
    for node_id, count in sorted(
        selected_node_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        node = node_lookup[node_id]
        alternative_rows.append(
            {
                "alternative_node_id": node_id,
                "longitude": node["longitude"],
                "latitude": node["latitude"],
                "observed_shopping_trips": node["observed_shopping_trips"],
                "observed_shopping_persons": node["observed_shopping_persons"],
                "selected_people": count,
            }
        )

    point_order = {"origin": 0, "destination": 1}
    mobmap_before.sort(
        key=lambda row: (
            row["time"],
            int(row["id"]),
            int(row["trip_id"]),
            point_order[row["point_role"]],
        )
    )
    mobmap_after.sort(
        key=lambda row: (
            row["time"],
            int(row["id"]),
            int(row["trip_id"]),
            point_order[row["point_role"]],
        )
    )
    focused_mobmap_before.sort(
        key=lambda row: (
            row["time"],
            int(row["id"]),
            int(row["trip_id"]),
            point_order[row["point_role"]],
        )
    )
    focused_mobmap_after.sort(
        key=lambda row: (
            row["time"],
            int(row["id"]),
            int(row["trip_id"]),
            point_order[row["point_role"]],
        )
    )
    moving_only_before = _movement_only_rows(focused_mobmap_before)
    moving_only_after = _movement_only_rows(focused_mobmap_after)
    moving_only_leg_ids = {int(row["id"]) for row in moving_only_before}
    if len(moving_only_before) != len(moving_only_leg_ids) * 2:
        raise AssertionError("Every movement-only trip ID must have exactly two points")
    summary = {
        "scenario": scenario,
        "source_extraction": {
            "affected_shopping_trips": len(affected_visits),
            "affected_unique_people": affected_unique_people,
            "selection_mode": selection_mode,
            "configured_changed_person_count": configured_count,
            "selected_people": len(selected_person_ids),
            "selected_shopping_trips": len(selected_rows),
            "selection_fraction_of_affected_people": (
                len(selected_person_ids) / affected_unique_people
                if affected_unique_people
                else None
            ),
        },
        "alternative_model": {
            "eligible_empirical_destination_nodes": len(alternative_nodes),
            "selected_destination_nodes": len(selected_node_counts),
            "selection_method": alternatives_settings["selection_method"],
        },
        "changes": {
            "incoming_destinations_changed": len(selected_rows),
            "outgoing_origins_changed": with_outgoing,
            "coordinate_change_records": len(coordinate_changes),
            "departure_times_changed": 0,
            "transport_modes_changed": 0,
            "trip_purposes_changed": 0,
        },
        "chain_validation": {
            "all_affected_people_selected": (
                selection_mode != "all_affected_people"
                or len(selected_person_ids) == affected_unique_people
            ),
            "selected_incoming_trips_unique": len(selected_trip_ids) == len(selected_rows),
            "time_order_violations": time_order_violations,
            "post_change_continuity_violations": continuity_violations,
        },
        "distance_km": {
            "incoming_before_mean": incoming_before_total / len(selected_rows),
            "incoming_after_mean": incoming_after_total / len(selected_rows),
            "outgoing_before_mean": (
                outgoing_before_total / with_outgoing if with_outgoing else None
            ),
            "outgoing_after_mean": (
                outgoing_after_total / with_outgoing if with_outgoing else None
            ),
        },
        "mobmap": {
            "before_rows": len(mobmap_before),
            "after_rows": len(mobmap_after),
            "exported_analysis_segments": len(selected_segment_keys),
            "focused_before_rows": len(focused_mobmap_before),
            "focused_after_rows": len(focused_mobmap_after),
            "focused_changed_visit_ids": len(selected_rows),
            "focused_incoming_trips": len(selected_rows),
            "focused_outgoing_trips": with_outgoing,
            "moving_only_before_rows": len(moving_only_before),
            "moving_only_after_rows": len(moving_only_after),
            "moving_only_leg_ids": len(moving_only_leg_ids),
            "moving_only_points_per_leg": 2,
            "required_columns": ["id", "time", "longitude", "latitude"],
            "trajectory_note": "OD straight-line interpolation, not observed road trajectories",
        },
        "limitations": scenario.get("limitations", []),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        output_dir / "selected_people.csv",
        PERSON_SUMMARY_COLUMNS,
        person_summary_rows,
    )
    _write_csv(output_dir / "selected_visits.csv", SELECTED_COLUMNS, selected_rows)
    _write_csv(
        output_dir / "coordinate_changes.csv",
        COORDINATE_CHANGE_COLUMNS,
        coordinate_changes,
    )
    _write_csv(
        output_dir / "alternative_destinations.csv",
        ALTERNATIVE_COLUMNS,
        alternative_rows,
    )
    _write_csv(output_dir / "mobmap_before.csv", MOBMAP_COLUMNS, mobmap_before)
    _write_csv(output_dir / "mobmap_after.csv", MOBMAP_COLUMNS, mobmap_after)
    _write_csv(
        output_dir / "mobmap_changed_trips_before.csv",
        MOBMAP_COLUMNS,
        focused_mobmap_before,
    )
    _write_csv(
        output_dir / "mobmap_changed_trips_after.csv",
        MOBMAP_COLUMNS,
        focused_mobmap_after,
    )
    _write_csv(
        output_dir / "mobmap_changed_trips_moving_only_before.csv",
        MOBMAP_COLUMNS,
        moving_only_before,
    )
    _write_csv(
        output_dir / "mobmap_changed_trips_moving_only_after.csv",
        MOBMAP_COLUMNS,
        moving_only_after,
    )
    (output_dir / "Mobmapでの確認方法.md").write_text(
        """# MobmapでのBefore/After確認方法

## 移動している時間だけを確認する（推奨）

1. Mobmapを開き、`mobmap_changed_trips_moving_only_before.csv`を移動体レイヤーとして追加する。
2. 列指定で`id`、`time`、`longitude`、`latitude`を対応させる。
3. 同様に`mobmap_changed_trips_moving_only_after.csv`を別レイヤーとして追加する。
4. BeforeとAfterを別の色にし、同じ時刻範囲で表示する。
5. レイヤメニューの「範囲外の時刻でもマーカーを表示」は有効にしない。

この2ファイルでは、買い物への到着トリップと直後の出発トリップへ、それぞれ別の`id`を
割り当てている。各`id`は出発・到着の2点だけなので、移動区間の開始から推定到着までだけ
Mobmapに現れ、買い物先で停止している時間は表示されない。

## 変更された訪問の到着・出発を連続して確認する

1. Mobmapを開き、`mobmap_changed_trips_before.csv`を移動体レイヤーとして追加する。
2. 列指定で`id`、`time`、`longitude`、`latitude`を対応させる。
3. 同様に`mobmap_changed_trips_after.csv`を別レイヤーとして追加する。
4. Beforeを青、Afterを赤など別の色にし、同じ時刻範囲で表示する。

この2ファイルには、目的地を変更した買い物到着トリップと、チェーン連続性のために
出発地を変更した直後のトリップだけが入る。`id`は買い物訪問ごとに独立しているため、
別の買い物訪問同士は線で接続されない。ただし、同一訪問の到着から直後の出発までは
同じ`id`なので、買い物先での滞在時間もマーカーが表示される。

## 対象者の一日全体を確認する

- `mobmap_before.csv`: 対象者の変更前分析系列
- `mobmap_after.csv`: 対象者の変更後分析系列

全系列版では`changed = 1`を使うと、座標を変更した点を絞り込める。

`id`は可視化用の1から始まる連番で、元の人物IDは`person_id`に保持している。
Pseudo-PFLOWには通過点や実経路がないため、表示される移動線はOD点間の補間であり、
実際の道路・鉄道上の軌跡ではない。到着時刻も距離と交通手段別の仮定速度から推定している。
""",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    """入力DB、シナリオ設定、出力先のコマンドライン引数を読み取る。"""

    parser = argparse.ArgumentParser(
        description="Reallocate shopping trips after a commercial facility closure."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    """コマンドライン引数で再配分処理を実行し、結果要約を画面へ表示する。"""

    args = parse_args()
    summary = build_closure_reallocation(
        args.database, args.scenario, args.output_dir
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
