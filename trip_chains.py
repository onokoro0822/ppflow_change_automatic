"""Reconstruct person-level trip chains from headerless Pseudo-PFLOW CSV files.

The Nagoya dataset contains millions of rows, so this module uses SQLite as a
disk-backed index. CSV rows are streamed into the database and only one
person's trips are kept in Python memory while adjacent links are generated.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import resource
import sqlite3
import sys
import time
import tracemalloc
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "input" / "nagoya"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "trip_chains"
DEFAULT_CONTINUITY_TOLERANCE_M = 200.0

SOURCE_COLUMNS = (
    "person_id",
    "departure_time_sec",
    "origin_lon",
    "origin_lat",
    "destination_lon",
    "destination_lat",
    "transport_mode",
    "trip_purpose",
    "employment_status",
)

SAMPLE_COLUMNS = (
    "person_id",
    "sequence_index",
    "chain_length",
    "departure_time_sec",
    "origin_lon",
    "origin_lat",
    "destination_lon",
    "destination_lat",
    "transport_mode",
    "trip_purpose",
    "employment_status",
    "previous_departure_time_sec",
    "previous_trip_purpose",
    "next_departure_time_sec",
    "next_trip_purpose",
    "previous_departure_delta_sec",
    "previous_destination_gap_m",
    "time_order_valid",
    "spatial_continuity_valid",
)


@dataclass(frozen=True)
class StoredTrip:
    trip_id: int
    person_id: str
    departure_time_sec: int
    origin_lon: float
    origin_lat: float
    destination_lon: float
    destination_lat: float
    source_file: str


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Return the great-circle distance between two coordinates in metres."""

    radius_m = 6_371_008.8
    lon1_rad, lat1_rad, lon2_rad, lat2_rad = map(
        math.radians,
        (lon1, lat1, lon2, lat2),
    )
    delta_lon = lon2_rad - lon1_rad
    delta_lat = lat2_rad - lat1_rad
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(min(1.0, math.sqrt(a)))


def _peak_rss_mb() -> float:
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return value / (1024.0 * 1024.0)
    return value / 1024.0


def _parse_source_rows(paths: Sequence[Path]) -> Iterator[tuple[Any, ...]]:
    input_order = 0
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Input CSV not found: {path}")
        with path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            for line_no, row in enumerate(reader, start=1):
                if not row:
                    continue
                if len(row) < len(SOURCE_COLUMNS):
                    raise ValueError(
                        f"{path.name} line {line_no}: expected 9 columns, got {len(row)}"
                    )
                try:
                    parsed = (
                        row[0],
                        int(float(row[1])),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        float(row[5]),
                        row[6],
                        row[7],
                        row[8],
                    )
                except ValueError:
                    if line_no == 1:
                        continue
                    raise ValueError(f"{path.name} line {line_no}: invalid trip row") from None
                input_order += 1
                yield (*parsed, path.name, line_no, input_order)


def _batched(rows: Iterable[tuple[Any, ...]], size: int) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _configure_database(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = FILE")
    connection.execute("PRAGMA cache_size = -65536")


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE trips (
            trip_id INTEGER PRIMARY KEY,
            person_id TEXT NOT NULL,
            departure_time_sec INTEGER NOT NULL,
            origin_lon REAL NOT NULL,
            origin_lat REAL NOT NULL,
            destination_lon REAL NOT NULL,
            destination_lat REAL NOT NULL,
            transport_mode TEXT NOT NULL,
            trip_purpose TEXT NOT NULL,
            employment_status TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_line INTEGER NOT NULL,
            input_order INTEGER NOT NULL UNIQUE
        );

        CREATE TABLE chain_links (
            trip_id INTEGER PRIMARY KEY,
            sequence_index INTEGER NOT NULL,
            chain_length INTEGER NOT NULL,
            previous_trip_id INTEGER,
            next_trip_id INTEGER,
            previous_departure_delta_sec INTEGER,
            previous_destination_gap_m REAL,
            time_order_valid INTEGER,
            spatial_continuity_valid INTEGER,
            FOREIGN KEY (trip_id) REFERENCES trips(trip_id),
            FOREIGN KEY (previous_trip_id) REFERENCES trips(trip_id),
            FOREIGN KEY (next_trip_id) REFERENCES trips(trip_id)
        );

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        """
    )


def _create_chain_view(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE VIEW trip_chain_rows AS
        SELECT
            t.trip_id,
            t.person_id,
            l.sequence_index,
            l.chain_length,
            t.departure_time_sec,
            t.origin_lon,
            t.origin_lat,
            t.destination_lon,
            t.destination_lat,
            t.transport_mode,
            t.trip_purpose,
            t.employment_status,
            t.source_file,
            t.source_line,
            l.previous_trip_id,
            previous.departure_time_sec AS previous_departure_time_sec,
            previous.destination_lon AS previous_destination_lon,
            previous.destination_lat AS previous_destination_lat,
            previous.trip_purpose AS previous_trip_purpose,
            l.next_trip_id,
            next.departure_time_sec AS next_departure_time_sec,
            next.origin_lon AS next_origin_lon,
            next.origin_lat AS next_origin_lat,
            next.trip_purpose AS next_trip_purpose,
            l.previous_departure_delta_sec,
            l.previous_destination_gap_m,
            l.time_order_valid,
            l.spatial_continuity_valid
        FROM trips AS t
        JOIN chain_links AS l ON l.trip_id = t.trip_id
        LEFT JOIN trips AS previous ON previous.trip_id = l.previous_trip_id
        LEFT JOIN trips AS next ON next.trip_id = l.next_trip_id;
        """
    )


def _store_metadata(connection: sqlite3.Connection, summary: dict[str, Any]) -> None:
    connection.executemany(
        "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
        ((key, json.dumps(value, ensure_ascii=False)) for key, value in summary.items()),
    )


def _write_sample_csv(
    connection: sqlite3.Connection,
    output_path: Path,
    sample_size: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    placeholders = ", ".join(SAMPLE_COLUMNS)
    rows = connection.execute(
        f"SELECT {placeholders} FROM trip_chain_rows "
        "ORDER BY person_id, sequence_index LIMIT ?",
        (sample_size,),
    )
    with output_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(SAMPLE_COLUMNS)
        writer.writerows(rows)


def build_trip_chain_database(
    input_paths: Sequence[Path],
    database_path: Path,
    *,
    summary_path: Path | None = None,
    sample_csv_path: Path | None = None,
    sample_size: int = 1_000,
    continuity_tolerance_m: float = DEFAULT_CONTINUITY_TOLERANCE_M,
    batch_size: int = 20_000,
) -> dict[str, Any]:
    """Build a disk-backed trip-chain database and return its summary."""

    paths = [Path(path) for path in input_paths]
    if not paths:
        raise ValueError("At least one input CSV is required")
    if database_path.exists():
        raise FileExistsError(f"Output database already exists: {database_path}")
    if continuity_tolerance_m < 0:
        raise ValueError("continuity_tolerance_m must be non-negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.perf_counter()
    tracemalloc.start()
    connection = sqlite3.connect(database_path)
    try:
        _configure_database(connection)
        _create_schema(connection)

        insert_sql = """
            INSERT INTO trips (
                person_id, departure_time_sec, origin_lon, origin_lat,
                destination_lon, destination_lat, transport_mode, trip_purpose,
                employment_status, source_file, source_line, input_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        invalid_time_records = 0
        input_trips = 0
        for batch in _batched(_parse_source_rows(paths), batch_size):
            connection.executemany(insert_sql, batch)
            input_trips += len(batch)
            invalid_time_records += sum(not 0 <= int(row[1]) < 86_400 for row in batch)
        connection.commit()
        if input_trips == 0:
            raise ValueError("No trip rows were loaded")

        connection.execute(
            "CREATE INDEX idx_trips_person_time_order "
            "ON trips(person_id, departure_time_sec, input_order)"
        )
        connection.commit()

        ordered_rows = connection.execute(
            """
            SELECT
                trip_id, person_id, departure_time_sec,
                origin_lon, origin_lat, destination_lon, destination_lat,
                source_file
            FROM trips
            ORDER BY person_id, departure_time_sec, input_order
            """
        )

        persons = 0
        multi_trip_persons = 0
        persons_spanning_multiple_files = 0
        adjacent_pairs = 0
        continuous_pairs = 0
        discontinuous_pairs = 0
        same_departure_time_pairs = 0
        max_trips_per_person = 0
        link_batch: list[tuple[Any, ...]] = []
        current_person: str | None = None
        current_chain: list[StoredTrip] = []

        link_sql = """
            INSERT INTO chain_links (
                trip_id, sequence_index, chain_length,
                previous_trip_id, next_trip_id,
                previous_departure_delta_sec, previous_destination_gap_m,
                time_order_valid, spatial_continuity_valid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        def flush_chain() -> None:
            nonlocal persons, multi_trip_persons, persons_spanning_multiple_files
            nonlocal adjacent_pairs, continuous_pairs, discontinuous_pairs
            nonlocal same_departure_time_pairs, max_trips_per_person, link_batch
            if not current_chain:
                return
            persons += 1
            chain_length = len(current_chain)
            max_trips_per_person = max(max_trips_per_person, chain_length)
            if chain_length > 1:
                multi_trip_persons += 1
            if len({trip.source_file for trip in current_chain}) > 1:
                persons_spanning_multiple_files += 1

            for index, trip in enumerate(current_chain):
                previous = current_chain[index - 1] if index else None
                next_trip = current_chain[index + 1] if index + 1 < chain_length else None
                departure_delta: int | None = None
                destination_gap: float | None = None
                time_order_valid: int | None = None
                spatial_continuity_valid: int | None = None
                if previous is not None:
                    adjacent_pairs += 1
                    departure_delta = trip.departure_time_sec - previous.departure_time_sec
                    destination_gap = haversine_m(
                        previous.destination_lon,
                        previous.destination_lat,
                        trip.origin_lon,
                        trip.origin_lat,
                    )
                    time_order_valid = int(departure_delta >= 0)
                    spatial_continuity_valid = int(
                        destination_gap <= continuity_tolerance_m
                    )
                    if departure_delta == 0:
                        same_departure_time_pairs += 1
                    if time_order_valid and spatial_continuity_valid:
                        continuous_pairs += 1
                    else:
                        discontinuous_pairs += 1

                link_batch.append(
                    (
                        trip.trip_id,
                        index + 1,
                        chain_length,
                        previous.trip_id if previous else None,
                        next_trip.trip_id if next_trip else None,
                        departure_delta,
                        destination_gap,
                        time_order_valid,
                        spatial_continuity_valid,
                    )
                )
                if len(link_batch) >= batch_size:
                    connection.executemany(link_sql, link_batch)
                    link_batch = []

        for row in ordered_rows:
            trip = StoredTrip(*row)
            if current_person is not None and trip.person_id != current_person:
                flush_chain()
                current_chain = []
            current_person = trip.person_id
            current_chain.append(trip)
        flush_chain()
        if link_batch:
            connection.executemany(link_sql, link_batch)
        connection.commit()

        connection.execute("CREATE INDEX idx_chain_links_previous ON chain_links(previous_trip_id)")
        connection.execute("CREATE INDEX idx_chain_links_next ON chain_links(next_trip_id)")
        _create_chain_view(connection)

        elapsed_seconds = time.perf_counter() - started_at
        _, peak_python_bytes = tracemalloc.get_traced_memory()
        summary: dict[str, Any] = {
            "input_files": len(paths),
            "input_trips": input_trips,
            "persons": persons,
            "single_trip_persons": persons - multi_trip_persons,
            "multi_trip_persons": multi_trip_persons,
            "persons_spanning_multiple_files": persons_spanning_multiple_files,
            "adjacent_pairs": adjacent_pairs,
            "continuous_pairs": continuous_pairs,
            "discontinuous_pairs": discontinuous_pairs,
            "continuity_rate": continuous_pairs / adjacent_pairs if adjacent_pairs else None,
            "same_departure_time_pairs": same_departure_time_pairs,
            "invalid_time_records": invalid_time_records,
            "max_trips_per_person": max_trips_per_person,
            "continuity_tolerance_m": continuity_tolerance_m,
            "peak_python_memory_mb": peak_python_bytes / (1024.0 * 1024.0),
            "peak_process_rss_mb": _peak_rss_mb(),
            "elapsed_seconds": elapsed_seconds,
            "database_path": str(database_path.resolve()),
        }
        _store_metadata(connection, summary)
        connection.commit()

        if sample_csv_path is not None:
            _write_sample_csv(connection, sample_csv_path, sample_size)
        if summary_path is not None:
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return summary
    except Exception:
        connection.close()
        if database_path.exists():
            database_path.unlink()
        raise
    finally:
        if connection:
            connection.close()
        tracemalloc.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstruct person-level trip chains from Pseudo-PFLOW CSV files."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--pattern", default="trip_231*.csv")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "nagoya_trip_chains.sqlite3",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "summary.json",
    )
    parser.add_argument(
        "--sample-csv",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "trip_chain_sample.csv",
    )
    parser.add_argument("--sample-size", type=int, default=1_000)
    parser.add_argument(
        "--continuity-tolerance-m",
        type=float,
        default=DEFAULT_CONTINUITY_TOLERANCE_M,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_paths = sorted(args.input_dir.glob(args.pattern))
    summary = build_trip_chain_database(
        input_paths,
        args.database,
        summary_path=args.summary,
        sample_csv_path=args.sample_csv,
        sample_size=args.sample_size,
        continuity_tolerance_m=args.continuity_tolerance_m,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
