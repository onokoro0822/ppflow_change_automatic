"""Aggregate commercial-trip user profiles from the trip-chain database."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trip_chains import haversine_m


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "output" / "trip_chains" / "nagoya_trip_chains.sqlite3"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "commercial_profiles" / "all_city"
COMMERCIAL_PURPOSES = ("100", "200", "400")

PURPOSE_LABELS = {
    "1": "在宅",
    "2": "通勤",
    "3": "通学",
    "100": "買い物",
    "200": "外食",
    "300": "通院",
    "400": "自由行動",
    "500": "業務",
}

TRANSPORT_LABELS = {
    "0": "未定義・滞在",
    "1": "徒歩",
    "2": "自転車",
    "3": "自動車",
    "4": "電車",
    "5": "バス",
    "6": "複数交通手段",
}

EMPLOYMENT_LABELS = {
    "10": "幼児",
    "11": "学齢前",
    "12": "小学生",
    "13": "中学生",
    "14": "高校生",
    "15": "大学生",
    "16": "短大・専門学校生",
    "21": "就業者",
    "23": "無職者",
}

PROFILE_SAMPLE_COLUMNS = (
    "person_id",
    "departure_time_sec",
    "time_band",
    "origin_lon",
    "origin_lat",
    "destination_lon",
    "destination_lat",
    "trip_distance_km",
    "transport_mode",
    "trip_purpose",
    "employment_status",
    "previous_transport_mode",
    "next_transport_mode",
    "previous_trip_purpose",
    "next_trip_purpose",
    "activity_sequence",
    "transport_sequence",
)


@dataclass(frozen=True)
class PreparedPolygon:
    rings_xy: tuple[tuple[tuple[float, float], ...], ...]
    min_lon: float
    max_lon: float
    min_lat: float
    max_lat: float
    longitude_metres_per_degree: float
    latitude_metres_per_degree: float
    origin_lon: float
    origin_lat: float
    properties: dict[str, Any]


def _prepare_geojson_polygon(path: Path) -> PreparedPolygon:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("type") == "FeatureCollection":
        features = document.get("features", [])
        if len(features) != 1:
            raise ValueError("Boundary FeatureCollection must contain exactly one feature")
        feature = features[0]
    elif document.get("type") == "Feature":
        feature = document
    else:
        feature = {"type": "Feature", "properties": {}, "geometry": document}

    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Polygon":
        raise ValueError("Boundary GeoJSON must contain a Polygon geometry")
    coordinates = geometry.get("coordinates") or []
    if not coordinates or any(len(ring) < 4 for ring in coordinates):
        raise ValueError("Boundary Polygon must contain closed rings with at least four points")

    all_points = [point for ring in coordinates for point in ring]
    if any(len(point) < 2 for point in all_points):
        raise ValueError("Boundary coordinates must contain longitude and latitude")
    all_lons = [float(point[0]) for point in all_points]
    all_lats = [float(point[1]) for point in all_points]
    origin_lon = sum(all_lons) / len(all_lons)
    origin_lat = sum(all_lats) / len(all_lats)
    latitude_metres_per_degree = 111_320.0
    longitude_metres_per_degree = 111_320.0 * math.cos(math.radians(origin_lat))
    rings_xy = tuple(
        tuple(
            (
                (float(point[0]) - origin_lon) * longitude_metres_per_degree,
                (float(point[1]) - origin_lat) * latitude_metres_per_degree,
            )
            for point in ring
        )
        for ring in coordinates
    )
    return PreparedPolygon(
        rings_xy=rings_xy,
        min_lon=min(all_lons),
        max_lon=max(all_lons),
        min_lat=min(all_lats),
        max_lat=max(all_lats),
        longitude_metres_per_degree=longitude_metres_per_degree,
        latitude_metres_per_degree=latitude_metres_per_degree,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        properties=dict(feature.get("properties") or {}),
    )


def _point_in_ring(x: float, y: float, ring: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _point_segment_distance(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    delta_x = x2 - x1
    delta_y = y2 - y1
    squared_length = delta_x * delta_x + delta_y * delta_y
    if squared_length == 0:
        return math.hypot(x - x1, y - y1)
    fraction = max(
        0.0,
        min(
            1.0,
            ((x - x1) * delta_x + (y - y1) * delta_y) / squared_length,
        ),
    )
    return math.hypot(
        x - (x1 + fraction * delta_x),
        y - (y1 + fraction * delta_y),
    )


def _distance_to_polygon_m(lon: float, lat: float, polygon: PreparedPolygon) -> float:
    x = (lon - polygon.origin_lon) * polygon.longitude_metres_per_degree
    y = (lat - polygon.origin_lat) * polygon.latitude_metres_per_degree
    inside_outer = _point_in_ring(x, y, polygon.rings_xy[0])
    inside_hole = any(_point_in_ring(x, y, ring) for ring in polygon.rings_xy[1:])
    if inside_outer and not inside_hole:
        return 0.0
    return min(
        _point_segment_distance(x, y, x1, y1, x2, y2)
        for ring in polygon.rings_xy
        for (x1, y1), (x2, y2) in zip(ring, ring[1:])
    )


def coded_label(value: str | None, labels: dict[str, str], *, none_label: str) -> str:
    if value is None:
        return none_label
    text = str(value).strip()
    if not text:
        return "欠損"
    return f"{text}:{labels.get(text, '未定義')}"


def time_band(departure_time_sec: int) -> str:
    if not 0 <= departure_time_sec < 86_400:
        return "範囲外"
    hour = departure_time_sec / 3600.0
    if hour < 6:
        return "00-06"
    if hour < 10:
        return "06-10"
    if hour < 11:
        return "10-11"
    if hour < 14:
        return "11-14"
    if hour < 18:
        return "14-18"
    return "18-24"


def distance_band(distance_km: float) -> str:
    if distance_km < 0.5:
        return "0-0.5km"
    if distance_km < 2.0:
        return "0.5-2km"
    if distance_km < 18.0:
        return "2-18km"
    return "18km以上"


def _distribution_rows(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    return [
        {
            "category": category,
            "count": count,
            "share": count / total if total else 0.0,
        }
        for category, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _scope_bounds(target_lon: float, target_lat: float, radius_km: float) -> tuple[float, ...]:
    latitude_delta = radius_km / 111.32
    longitude_scale = max(0.01, math.cos(math.radians(target_lat)))
    longitude_delta = radius_km / (111.32 * longitude_scale)
    return (
        target_lon - longitude_delta,
        target_lon + longitude_delta,
        target_lat - latitude_delta,
        target_lat + latitude_delta,
    )


def build_commercial_profile(
    database_path: Path,
    output_dir: Path,
    *,
    target_lon: float | None = None,
    target_lat: float | None = None,
    radius_km: float | None = None,
    boundary_geojson: Path | None = None,
    boundary_buffer_m: float = 0.0,
    purpose_codes: tuple[str, ...] = COMMERCIAL_PURPOSES,
    sample_size: int = 1_000,
    quality_filtered: bool = True,
) -> dict[str, Any]:
    """Build JSON/CSV profiles for all-city or destination-radius scope."""

    database_path = Path(database_path)
    if not database_path.exists():
        raise FileNotFoundError(f"Trip-chain database not found: {database_path}")
    target_values = (target_lon, target_lat, radius_km)
    if any(value is not None for value in target_values) and not all(
        value is not None for value in target_values
    ):
        raise ValueError("target_lon, target_lat, and radius_km must be specified together")
    if radius_km is not None and radius_km <= 0:
        raise ValueError("radius_km must be positive")
    if boundary_buffer_m < 0:
        raise ValueError("boundary_buffer_m must be non-negative")
    if boundary_geojson is not None and any(value is not None for value in target_values):
        raise ValueError("Boundary GeoJSON and destination radius scope cannot be combined")
    if not purpose_codes:
        raise ValueError("At least one purpose code is required")

    boundary = (
        _prepare_geojson_polygon(Path(boundary_geojson))
        if boundary_geojson is not None
        else None
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_json_path = output_dir / "profile.json"
    counts_csv_path = output_dir / "profile_counts.csv"
    sample_csv_path = output_dir / "selected_trip_sample.csv"

    connection = sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    quality_layer_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
        ("analysis_trip_chain_rows",),
    ).fetchone() is not None
    if quality_filtered and not quality_layer_exists:
        connection.close()
        raise ValueError(
            "Analysis-quality layer is missing; run trip_chain_quality.py first "
            "or set quality_filtered=False"
        )
    placeholders = ",".join("?" for _ in purpose_codes)
    commercial_trip_count = connection.execute(
        f"SELECT COUNT(*) FROM trips WHERE trip_purpose IN ({placeholders})",
        purpose_codes,
    ).fetchone()[0]

    if quality_filtered:
        source_view = "analysis_trip_chain_rows"
        previous_trip_id_column = "analysis_previous_trip_id"
        next_trip_id_column = "analysis_next_trip_id"
        previous_purpose_column = "analysis_previous_trip_purpose"
        next_purpose_column = "analysis_next_trip_purpose"
        continuity_column = (
            "CASE WHEN analysis_previous_trip_id IS NULL THEN NULL ELSE 1 END"
        )
        quality_where = " AND analysis_eligible = 1"
    else:
        source_view = "trip_chain_rows"
        previous_trip_id_column = "previous_trip_id"
        next_trip_id_column = "next_trip_id"
        previous_purpose_column = "previous_trip_purpose"
        next_purpose_column = "next_trip_purpose"
        continuity_column = "spatial_continuity_valid"
        quality_where = ""

    eligible_commercial_trip_count = connection.execute(
        f"SELECT COUNT(*) FROM {source_view} "
        f"WHERE trip_purpose IN ({placeholders}){quality_where}",
        purpose_codes,
    ).fetchone()[0]

    query = f"""
        SELECT
            person_id, departure_time_sec,
            origin_lon, origin_lat, destination_lon, destination_lat,
            transport_mode, trip_purpose, employment_status,
            (SELECT transport_mode FROM trips
             WHERE trip_id = {previous_trip_id_column}) AS previous_transport_mode,
            (SELECT transport_mode FROM trips
             WHERE trip_id = {next_trip_id_column}) AS next_transport_mode,
            {previous_purpose_column} AS previous_trip_purpose,
            {next_purpose_column} AS next_trip_purpose,
            {continuity_column} AS spatial_continuity_valid
        FROM {source_view}
        WHERE trip_purpose IN ({placeholders}){quality_where}
    """
    parameters: list[Any] = list(purpose_codes)
    scope: dict[str, Any]
    if boundary is not None:
        latitude_padding = boundary_buffer_m / boundary.latitude_metres_per_degree
        longitude_padding = boundary_buffer_m / boundary.longitude_metres_per_degree
        query += " AND destination_lon BETWEEN ? AND ? AND destination_lat BETWEEN ? AND ?"
        parameters.extend(
            (
                boundary.min_lon - longitude_padding,
                boundary.max_lon + longitude_padding,
                boundary.min_lat - latitude_padding,
                boundary.max_lat + latitude_padding,
            )
        )
        scope = {
            "type": "destination_polygon_buffer",
            "boundary_geojson": str(Path(boundary_geojson).resolve()),
            "boundary_buffer_m": boundary_buffer_m,
            "facility_name": boundary.properties.get("name"),
            "boundary_source": boundary.properties.get("boundary_source"),
        }
    elif target_lon is not None and target_lat is not None and radius_km is not None:
        min_lon, max_lon, min_lat, max_lat = _scope_bounds(target_lon, target_lat, radius_km)
        query += " AND destination_lon BETWEEN ? AND ? AND destination_lat BETWEEN ? AND ?"
        parameters.extend((min_lon, max_lon, min_lat, max_lat))
        scope = {
            "type": "destination_radius",
            "target_lon": target_lon,
            "target_lat": target_lat,
            "radius_km": radius_km,
        }
    else:
        scope = {"type": "all_city"}

    counters: dict[str, Counter[str]] = defaultdict(Counter)
    missing_counts = Counter()
    selected_person_ids: set[str] = set()
    selected_trip_count = 0
    spatial_candidates_scanned = 0
    outside_exact_radius = 0
    outside_exact_boundary = 0
    distance_sum_km = 0.0
    distance_min_km: float | None = None
    distance_max_km: float | None = None
    sample_rows: list[list[Any]] = []

    for row in connection.execute(query, parameters):
        spatial_candidates_scanned += 1
        if boundary is not None:
            boundary_distance_m = _distance_to_polygon_m(
                row["destination_lon"],
                row["destination_lat"],
                boundary,
            )
            if boundary_distance_m > boundary_buffer_m:
                outside_exact_boundary += 1
                continue
        elif target_lon is not None and target_lat is not None and radius_km is not None:
            target_distance_km = haversine_m(
                row["destination_lon"],
                row["destination_lat"],
                target_lon,
                target_lat,
            ) / 1000.0
            if target_distance_km > radius_km:
                outside_exact_radius += 1
                continue

        selected_trip_count += 1
        person_id = str(row["person_id"] or "").strip()
        selected_person_ids.add(person_id)
        if not person_id:
            missing_counts["person_id"] += 1
        for field in ("transport_mode", "trip_purpose", "employment_status"):
            if not str(row[field] or "").strip():
                missing_counts[field] += 1
        for field in ("origin_lon", "origin_lat", "destination_lon", "destination_lat"):
            if row[field] is None or not math.isfinite(float(row[field])):
                missing_counts[field] += 1

        purpose = coded_label(row["trip_purpose"], PURPOSE_LABELS, none_label="なし")
        employment = coded_label(
            row["employment_status"], EMPLOYMENT_LABELS, none_label="欠損"
        )
        transport = coded_label(row["transport_mode"], TRANSPORT_LABELS, none_label="欠損")
        previous_transport = coded_label(
            row["previous_transport_mode"],
            TRANSPORT_LABELS,
            none_label="なし（チェーン先頭）",
        )
        next_transport = coded_label(
            row["next_transport_mode"],
            TRANSPORT_LABELS,
            none_label="なし（チェーン末尾）",
        )
        previous = coded_label(
            row["previous_trip_purpose"],
            PURPOSE_LABELS,
            none_label="なし（チェーン先頭）",
        )
        next_purpose = coded_label(
            row["next_trip_purpose"],
            PURPOSE_LABELS,
            none_label="なし（チェーン末尾）",
        )
        band = time_band(int(row["departure_time_sec"]))
        trip_distance_km = haversine_m(
            row["origin_lon"],
            row["origin_lat"],
            row["destination_lon"],
            row["destination_lat"],
        ) / 1000.0
        trip_distance_band = distance_band(trip_distance_km)
        sequence = f"{previous} → {purpose} → {next_purpose}"
        transport_sequence = (
            f"{previous_transport} → {transport} → {next_transport}"
        )
        continuity = (
            "判定対象外（チェーン先頭）"
            if row["spatial_continuity_valid"] is None
            else ("連続" if row["spatial_continuity_valid"] else "不連続")
        )

        counters["trip_purpose"][purpose] += 1
        counters["employment_status"][employment] += 1
        counters["time_band"][band] += 1
        counters["distance_band"][trip_distance_band] += 1
        counters["transport_mode"][transport] += 1
        counters["previous_transport_mode"][previous_transport] += 1
        counters["next_transport_mode"][next_transport] += 1
        counters["previous_trip_purpose"][previous] += 1
        counters["next_trip_purpose"][next_purpose] += 1
        counters["activity_sequence"][sequence] += 1
        counters["transport_sequence"][transport_sequence] += 1
        counters["previous_spatial_continuity"][continuity] += 1

        distance_sum_km += trip_distance_km
        distance_min_km = (
            trip_distance_km if distance_min_km is None else min(distance_min_km, trip_distance_km)
        )
        distance_max_km = (
            trip_distance_km if distance_max_km is None else max(distance_max_km, trip_distance_km)
        )

        if len(sample_rows) < sample_size:
            sample_rows.append(
                [
                    person_id,
                    row["departure_time_sec"],
                    band,
                    row["origin_lon"],
                    row["origin_lat"],
                    row["destination_lon"],
                    row["destination_lat"],
                    trip_distance_km,
                    transport,
                    purpose,
                    employment,
                    previous_transport,
                    next_transport,
                    previous,
                    next_purpose,
                    sequence,
                    transport_sequence,
                ]
            )
    connection.close()

    distributions = {
        dimension: _distribution_rows(counter, selected_trip_count)
        for dimension, counter in sorted(counters.items())
    }
    profile: dict[str, Any] = {
        "scope": scope,
        "purpose_codes": list(purpose_codes),
        "quality_policy": {
            "quality_filtered": quality_filtered,
            "invalid_time_records_excluded": quality_filtered,
            "spatial_gaps_split_activity_sequences": quality_filtered,
        },
        "counts": {
            "commercial_trips_in_database": commercial_trip_count,
            "eligible_commercial_trips_in_database": eligible_commercial_trip_count,
            "excluded_invalid_time_commercial_trips": (
                commercial_trip_count - eligible_commercial_trip_count
                if quality_filtered
                else 0
            ),
            "spatial_candidates_scanned": spatial_candidates_scanned,
            "excluded_outside_exact_radius": outside_exact_radius,
            "excluded_outside_exact_boundary": outside_exact_boundary,
            "selected_trips": selected_trip_count,
            "unique_persons": len(selected_person_ids),
        },
        "missing_counts": {
            field: missing_counts.get(field, 0)
            for field in (
                "person_id",
                "origin_lon",
                "origin_lat",
                "destination_lon",
                "destination_lat",
                "transport_mode",
                "trip_purpose",
                "employment_status",
            )
        },
        "chain_endpoint_counts": {
            "without_previous_trip": counters["previous_trip_purpose"].get(
                "なし（チェーン先頭）", 0
            ),
            "without_next_trip": counters["next_trip_purpose"].get(
                "なし（チェーン末尾）", 0
            ),
        },
        "trip_distance_km": {
            "mean": distance_sum_km / selected_trip_count if selected_trip_count else None,
            "minimum": distance_min_km,
            "maximum": distance_max_km,
        },
        "distributions": distributions,
    }

    profile_json_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with counts_csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=("dimension", "category", "count", "share"),
        )
        writer.writeheader()
        for dimension, rows in distributions.items():
            for row in rows:
                writer.writerow({"dimension": dimension, **row})
    with sample_csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(PROFILE_SAMPLE_COLUMNS)
        writer.writerows(sample_rows)

    return profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate commercial-trip profiles from a trip-chain SQLite database."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-lon", type=float)
    parser.add_argument("--target-lat", type=float)
    parser.add_argument("--radius-km", type=float)
    parser.add_argument("--boundary-geojson", type=Path)
    parser.add_argument("--boundary-buffer-m", type=float, default=0.0)
    parser.add_argument("--sample-size", type=int, default=1_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = build_commercial_profile(
        args.database,
        args.output_dir,
        target_lon=args.target_lon,
        target_lat=args.target_lat,
        radius_km=args.radius_km,
        boundary_geojson=args.boundary_geojson,
        boundary_buffer_m=args.boundary_buffer_m,
        sample_size=args.sample_size,
    )
    print(json.dumps(profile["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
