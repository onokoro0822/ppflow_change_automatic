import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

INPUT_CSV = BASE_DIR / "input" / "peopleflow_kashiwa.csv"
RULE_JSON = BASE_DIR / "output" / "scenario_rule.json"

OUT_BASELINE = BASE_DIR / "output" / "peopleflow_baseline.csv"
OUT_SCENARIO = BASE_DIR / "output" / "peopleflow_scenario.csv"

RANDOM_SEED = 42


def find_col(df, candidates):
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def detect_columns(df):
    lat_col = find_col(df, ["lat", "latitude", "y", "緯度"])
    lon_col = find_col(df, ["lon", "lng", "longitude", "x", "経度"])
    id_col = find_col(df, ["id", "person_id", "uid", "pid", "agent_id", "ユーザーid"])
    time_col = find_col(df, ["time", "datetime", "timestamp", "record_dt", "date_time", "時刻"])

    if lat_col is None or lon_col is None:
        raise ValueError(
            f"緯度経度カラムが見つかりません。columns={list(df.columns)}"
        )

    return lat_col, lon_col, id_col, time_col


def choose_target_location(df, lat_col, lon_col, rule):
    """
    LLMが緯度経度を出せなかった場合は、データ中心から少しずらした場所を仮の新施設地点にする。
    これで必ずbefore/afterの差が出る。
    """
    target = rule.get("target_location", {})
    lat = target.get("lat")
    lon = target.get("lon")

    if lat is not None and lon is not None:
        return float(lat), float(lon)

    center_lat = df[lat_col].astype(float).median()
    center_lon = df[lon_col].astype(float).median()

    # デモ用：中心から少し北東へずらした仮想施設
    return center_lat + 0.003, center_lon + 0.003


def select_affected_people_or_rows(df, id_col, affected_ratio):
    rng = np.random.default_rng(RANDOM_SEED)

    affected_ratio = max(0.05, min(float(affected_ratio), 0.5))

    if id_col is not None:
        ids = df[id_col].dropna().unique()
        n = max(1, int(len(ids) * affected_ratio))
        selected_ids = rng.choice(ids, size=n, replace=False)
        mask = df[id_col].isin(selected_ids)
    else:
        n = max(1, int(len(df) * affected_ratio))
        selected_index = rng.choice(df.index.to_numpy(), size=n, replace=False)
        mask = df.index.isin(selected_index)

    return mask


def apply_destination_shift(df, lat_col, lon_col, id_col, target_lat, target_lon, mask, strength):
    """
    選ばれた人/点を新施設側へ寄せる。
    発表用なので、目的地選択が変わったように見えることを優先。
    """
    scenario = df.copy()

    strength = max(0.1, min(float(strength), 1.0))
    rng = np.random.default_rng(RANDOM_SEED)

    # 少しばらけさせる。約100〜200m程度の見た目の分散。
    jitter_lat = rng.normal(0, 0.0008, size=mask.sum())
    jitter_lon = rng.normal(0, 0.0008, size=mask.sum())

    old_lat = scenario.loc[mask, lat_col].astype(float)
    old_lon = scenario.loc[mask, lon_col].astype(float)

    scenario.loc[mask, lat_col] = old_lat + (target_lat - old_lat) * strength + jitter_lat
    scenario.loc[mask, lon_col] = old_lon + (target_lon - old_lon) * strength + jitter_lon

    return scenario


def main():
    df = pd.read_csv(INPUT_CSV)
    rule = json.loads(RULE_JSON.read_text(encoding="utf-8"))

    lat_col, lon_col, id_col, time_col = detect_columns(df)

    target_lat, target_lon = choose_target_location(df, lat_col, lon_col, rule)

    affected_ratio = rule.get("affected_ratio", 0.25)
    strength = rule.get("movement_change", {}).get("strength", 0.8)

    mask = select_affected_people_or_rows(df, id_col, affected_ratio)

    scenario = apply_destination_shift(
        df=df,
        lat_col=lat_col,
        lon_col=lon_col,
        id_col=id_col,
        target_lat=target_lat,
        target_lon=target_lon,
        mask=mask,
        strength=strength
    )

    OUT_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_BASELINE, index=False)
    scenario.to_csv(OUT_SCENARIO, index=False)

    print("Detected columns")
    print(f"lat_col: {lat_col}")
    print(f"lon_col: {lon_col}")
    print(f"id_col: {id_col}")
    print(f"time_col: {time_col}")
    print()
    print(f"Target location: {target_lat}, {target_lon}")
    print(f"Affected records: {mask.sum()} / {len(df)}")
    print(f"Saved: {OUT_BASELINE}")
    print(f"Saved: {OUT_SCENARIO}")

if __name__ == "__main__":
    main()