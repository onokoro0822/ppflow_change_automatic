"""Add an analysis-quality layer to a reconstructed trip-chain database.

Raw Pseudo-PFLOW rows are preserved. Trips with departure times outside the
documented 24-hour range are excluded from analysis sequences, and spatially
discontinuous adjacent trips start a new sequence segment.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trip_chains import DEFAULT_CONTINUITY_TOLERANCE_M


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = BASE_DIR / "output" / "trip_chains" / "nagoya_trip_chains.sqlite3"
DEFAULT_SUMMARY = BASE_DIR / "output" / "trip_chains" / "quality_summary.json"


@dataclass(frozen=True)
class QualitySourceTrip:
    trip_id: int
    person_id: str
    departure_time_sec: int
    previous_destination_gap_m: float | None


def _has_object(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (name,),
        ).fetchone()
        is not None
    )


def _create_quality_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE trip_analysis_quality (
            trip_id INTEGER PRIMARY KEY,
            time_valid INTEGER NOT NULL,
            analysis_eligible INTEGER NOT NULL,
            segment_index INTEGER,
            segment_trip_index INTEGER,
            segment_length INTEGER,
            segment_previous_trip_id INTEGER,
            segment_next_trip_id INTEGER,
            break_before_reason TEXT NOT NULL,
            FOREIGN KEY (trip_id) REFERENCES trips(trip_id),
            FOREIGN KEY (segment_previous_trip_id) REFERENCES trips(trip_id),
            FOREIGN KEY (segment_next_trip_id) REFERENCES trips(trip_id)
        );
        """
    )


def _create_analysis_view(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE VIEW analysis_trip_chain_rows AS
        SELECT
            raw.*,
            q.time_valid,
            q.analysis_eligible,
            q.segment_index,
            q.segment_trip_index,
            q.segment_length,
            q.break_before_reason,
            q.segment_previous_trip_id AS analysis_previous_trip_id,
            previous.departure_time_sec AS analysis_previous_departure_time_sec,
            previous.destination_lon AS analysis_previous_destination_lon,
            previous.destination_lat AS analysis_previous_destination_lat,
            previous.trip_purpose AS analysis_previous_trip_purpose,
            q.segment_next_trip_id AS analysis_next_trip_id,
            next.departure_time_sec AS analysis_next_departure_time_sec,
            next.origin_lon AS analysis_next_origin_lon,
            next.origin_lat AS analysis_next_origin_lat,
            next.trip_purpose AS analysis_next_trip_purpose
        FROM trip_chain_rows AS raw
        JOIN trip_analysis_quality AS q ON q.trip_id = raw.trip_id
        LEFT JOIN trips AS previous ON previous.trip_id = q.segment_previous_trip_id
        LEFT JOIN trips AS next ON next.trip_id = q.segment_next_trip_id;
        """
    )


def apply_analysis_quality(
    database_path: Path,
    *,
    summary_path: Path | None = None,
    continuity_tolerance_m: float = DEFAULT_CONTINUITY_TOLERANCE_M,
    batch_size: int = 20_000,
) -> dict[str, Any]:
    """Create quality flags and analysis segments without changing raw rows."""

    database_path = Path(database_path)
    if not database_path.exists():
        raise FileNotFoundError(f"Trip-chain database not found: {database_path}")
    if continuity_tolerance_m < 0:
        raise ValueError("continuity_tolerance_m must be non-negative")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    started_at = time.perf_counter()
    connection = sqlite3.connect(database_path)
    try:
        if _has_object(connection, "trip_analysis_quality") or _has_object(
            connection, "analysis_trip_chain_rows"
        ):
            raise FileExistsError("Analysis-quality layer already exists")

        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = FILE")
        connection.execute("PRAGMA cache_size = -65536")
        _create_quality_schema(connection)

        rows = connection.execute(
            """
            SELECT
                t.trip_id, t.person_id, t.departure_time_sec,
                l.previous_destination_gap_m
            FROM trips AS t
            JOIN chain_links AS l ON l.trip_id = t.trip_id
            ORDER BY t.person_id, t.departure_time_sec, t.input_order
            """
        )

        insert_sql = """
            INSERT INTO trip_analysis_quality (
                trip_id, time_valid, analysis_eligible,
                segment_index, segment_trip_index, segment_length,
                segment_previous_trip_id, segment_next_trip_id,
                break_before_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        insert_batch: list[tuple[Any, ...]] = []
        current_person: str | None = None
        current_trips: list[QualitySourceTrip] = []

        total_trips = 0
        time_valid_trips = 0
        negative_time_trips = 0
        after_day_time_trips = 0
        persons = 0
        persons_with_eligible_trips = 0
        persons_with_invalid_time = 0
        persons_with_multiple_segments = 0
        analysis_segments = 0
        singleton_segments = 0
        retained_adjacent_pairs = 0
        spatial_gap_breaks = 0
        previous_invalid_time_breaks = 0

        def append_insert(values: tuple[Any, ...]) -> None:
            nonlocal insert_batch
            insert_batch.append(values)
            if len(insert_batch) >= batch_size:
                connection.executemany(insert_sql, insert_batch)
                insert_batch = []

        def flush_person() -> None:
            nonlocal persons, persons_with_eligible_trips, persons_with_invalid_time
            nonlocal persons_with_multiple_segments, analysis_segments
            nonlocal singleton_segments, retained_adjacent_pairs
            nonlocal spatial_gap_breaks, previous_invalid_time_breaks
            if not current_trips:
                return

            persons += 1
            segments: list[list[QualitySourceTrip]] = []
            segment_reasons: list[str] = []
            invalid_trip_ids: set[int] = set()
            previous: QualitySourceTrip | None = None
            person_has_invalid = False

            for trip in current_trips:
                valid_time = 0 <= trip.departure_time_sec < 86_400
                if not valid_time:
                    invalid_trip_ids.add(trip.trip_id)
                    person_has_invalid = True
                    previous = trip
                    continue

                if previous is None:
                    reason = "person_start"
                elif previous.trip_id in invalid_trip_ids:
                    reason = "previous_invalid_time"
                elif (
                    trip.previous_destination_gap_m is None
                    or trip.previous_destination_gap_m > continuity_tolerance_m
                ):
                    reason = "spatial_gap"
                else:
                    reason = "none"

                if reason != "none":
                    segments.append([])
                    segment_reasons.append(reason)
                    if reason == "spatial_gap":
                        spatial_gap_breaks += 1
                    elif reason == "previous_invalid_time":
                        previous_invalid_time_breaks += 1
                segments[-1].append(trip)
                previous = trip

            if person_has_invalid:
                persons_with_invalid_time += 1
            if segments:
                persons_with_eligible_trips += 1
            if len(segments) > 1:
                persons_with_multiple_segments += 1
            analysis_segments += len(segments)

            for trip in current_trips:
                if trip.trip_id in invalid_trip_ids:
                    append_insert(
                        (trip.trip_id, 0, 0, None, None, None, None, None, "invalid_time")
                    )

            for segment_index, (segment, start_reason) in enumerate(
                zip(segments, segment_reasons, strict=True), start=1
            ):
                segment_length = len(segment)
                if segment_length == 1:
                    singleton_segments += 1
                retained_adjacent_pairs += max(0, segment_length - 1)
                for index, trip in enumerate(segment):
                    previous_trip_id = segment[index - 1].trip_id if index else None
                    next_trip_id = (
                        segment[index + 1].trip_id if index + 1 < segment_length else None
                    )
                    append_insert(
                        (
                            trip.trip_id,
                            1,
                            1,
                            segment_index,
                            index + 1,
                            segment_length,
                            previous_trip_id,
                            next_trip_id,
                            start_reason if index == 0 else "none",
                        )
                    )

        for row in rows:
            trip = QualitySourceTrip(*row)
            if current_person is not None and trip.person_id != current_person:
                flush_person()
                current_trips = []
            current_person = trip.person_id
            current_trips.append(trip)
            total_trips += 1
            if 0 <= trip.departure_time_sec < 86_400:
                time_valid_trips += 1
            elif trip.departure_time_sec < 0:
                negative_time_trips += 1
            else:
                after_day_time_trips += 1
        flush_person()
        if insert_batch:
            connection.executemany(insert_sql, insert_batch)

        connection.execute(
            "CREATE INDEX idx_trip_analysis_segment_previous "
            "ON trip_analysis_quality(segment_previous_trip_id)"
        )
        connection.execute(
            "CREATE INDEX idx_trip_analysis_segment_next "
            "ON trip_analysis_quality(segment_next_trip_id)"
        )
        _create_analysis_view(connection)

        elapsed_seconds = time.perf_counter() - started_at
        summary: dict[str, Any] = {
            "policy": {
                "valid_time_range_sec": [0, 86_400],
                "time_upper_bound_exclusive": True,
                "invalid_time_action": "preserve_raw_and_exclude_from_analysis",
                "spatial_gap_action": "start_new_analysis_segment",
                "continuity_tolerance_m": continuity_tolerance_m,
            },
            "total_trips": total_trips,
            "time_valid_trips": time_valid_trips,
            "invalid_time_trips": total_trips - time_valid_trips,
            "negative_time_trips": negative_time_trips,
            "after_day_time_trips": after_day_time_trips,
            "persons": persons,
            "persons_with_eligible_trips": persons_with_eligible_trips,
            "persons_with_invalid_time": persons_with_invalid_time,
            "analysis_segments": analysis_segments,
            "persons_with_multiple_segments": persons_with_multiple_segments,
            "singleton_segments": singleton_segments,
            "retained_adjacent_pairs": retained_adjacent_pairs,
            "spatial_gap_breaks": spatial_gap_breaks,
            "previous_invalid_time_breaks": previous_invalid_time_breaks,
            "elapsed_seconds": elapsed_seconds,
            "database_path": str(database_path.resolve()),
        }
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value_json) VALUES (?, ?)",
            ("analysis_quality_summary", json.dumps(summary, ensure_ascii=False)),
        )
        connection.commit()

        if summary_path is not None:
            summary_path = Path(summary_path)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return summary
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add time-validity flags and analysis sequence segments."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--continuity-tolerance-m",
        type=float,
        default=DEFAULT_CONTINUITY_TOLERANCE_M,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = apply_analysis_quality(
        args.database,
        summary_path=args.summary,
        continuity_tolerance_m=args.continuity_tolerance_m,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
