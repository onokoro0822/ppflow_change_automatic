"""Audit a named existing retail facility and its model-coordinate proxies.

This describes generated shopping arrivals, not observed facility visitors.
Coordinate sharing is reported rather than silently treated as a building boundary.
"""
import argparse
import csv
import json
import math
import unicodedata
from collections import Counter
from pathlib import Path

from pflow_capacity_analysis import SHOPPING_PURPOSE, _iter_rows, third_mesh_code

# Same constant shifts as DataAccessor.loadRetailData, not a new datum transform.
JAVA_DLON = -0.000293000
JAVA_DLAT = 0.000106950


def normalize_name(name):
    return unicodedata.normalize('NFKC', name).replace(' ', '').replace('\u3000', '')


def model_point(lon, lat):
    lon, lat = float(lon), float(lat)
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError('Coordinates must be finite')
    return lon + JAVA_DLON, lat + JAVA_DLAT


def audit_rows(rows, config):
    aliases = [normalize_name(x) for x in config['name_aliases']]
    exclusions = [normalize_name(x) for x in config.get('exclude_name_terms', [])]
    valid = []
    for row in rows:
        if row['ADDCD'] != config['gcode']:
            continue
        try:
            point = model_point(row['LON'], row['LAT'])
        except (ValueError, TypeError):
            continue
        name = normalize_name(row['DN'])
        matched = any(alias in name for alias in aliases) and not any(x in name for x in exclusions)
        valid.append((row, point, matched))
    candidate_points = {point for _, point, matched in valid if matched}
    if not candidate_points:
        raise ValueError('No named facility candidate in retail input')
    result = []
    for point in sorted(candidate_points):
        collocated = [(r, matched) for r, p, matched in valid if p == point]
        result.append({'longitude': point[0], 'latitude': point[1],
                       'mesh_code': third_mesh_code(*point),
                       'all_retail_rows': len(collocated),
                       'name_matched_rows': sum(m for _, m in collocated),
                       'other_name_rows': sum(not m for _, m in collocated)})
    return result


def summarize_rows(rows, points, gcode, tolerance=1e-6):
    counts = Counter()
    destination_gcodes = Counter()
    previous = {}
    for row in rows:
        pid = int(row['person_id'])
        prev = previous.get(pid)
        previous[pid] = row
        if int(row['purpose']) != SHOPPING_PURPOSE:
            continue
        matched = any(abs(float(row['lon']) - p['longitude']) <= tolerance and
                      abs(float(row['lat']) - p['latitude']) <= tolerance for p in points)
        if not matched:
            continue
        destination_gcodes[str(row["gcode"])] += 1
        if prev is None:
            counts['unknown_previous'] += 1
        elif str(prev['gcode']) == gcode:
            counts['same_ward'] += 1
        else:
            counts['outside_ward'] += 1
    total = sum(counts.values())
    known = counts['same_ward'] + counts['outside_ward']
    return {'shopping_arrivals': total, 'same_ward': counts['same_ward'],
            'outside_ward': counts['outside_ward'], 'unknown_previous': counts['unknown_previous'],
            'outside_ward_share_known': counts['outside_ward'] / known if known else None,
            'destination_gcodes': dict(destination_gcodes)}


def audit(config, retail_csv, baseline_dir):
    if not (baseline_dir / '.complete').exists():
        raise ValueError('Baseline must have a complete marker')
    with retail_csv.open(encoding='utf-8', errors='replace', newline='') as f:
        points = audit_rows(csv.DictReader(f), config)
    paths = sorted((baseline_dir / '23').glob('*.csv'))
    if not paths:
        raise ValueError('No baseline activity CSVs')
    counts = Counter()
    destination_gcodes = Counter()
    for path in paths:
        result = summarize_rows(_iter_rows(path), points, config['gcode'])
        counts.update({k: result[k] for k in ('shopping_arrivals', 'same_ward', 'outside_ward', 'unknown_previous')})
        destination_gcodes.update(result['destination_gcodes'])
    known = counts['same_ward'] + counts['outside_ward']
    return {'facility_id': config['facility_id'], 'status': 'coordinate_proxy_only_not_observed_validation',
            'csv_file_count': len(paths), 'model_coordinate_points': points,
            'generated_arrivals': {**dict(counts), 'outside_ward_share_known': counts['outside_ward']/known if known else None,
                                   'destination_gcodes': dict(destination_gcodes)},
            'baseline_dir': str(baseline_dir), 'sources': config['sources'],
            'limitations': config['limitations']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--retail-csv', type=Path, default=Path('.local/pflow/data/facilities/city_retail.csv'))
    parser.add_argument('--baseline-dir', type=Path, default=Path('.local/pflow/output/retail_floor_area_20261009/shared_mesh_fixed/seed42/baseline'))
    parser.add_argument('--output', type=Path, default=Path('output/existing_facility_audit/jr_nagoya_takashimaya_seed42.json'))
    args = parser.parse_args()
    result = audit(json.loads(args.config.read_text()), args.retail_csv, args.baseline_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
