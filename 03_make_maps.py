from pathlib import Path

import folium
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

BASELINE_CSV = BASE_DIR / "output" / "peopleflow_baseline.csv"
SCENARIO_CSV = BASE_DIR / "output" / "peopleflow_scenario.csv"

OUT_BEFORE = BASE_DIR / "output" / "map_before.html"
OUT_AFTER = BASE_DIR / "output" / "map_after.html"


def find_col(df, candidates):
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def detect_lat_lon(df):
    lat_col = find_col(df, ["lat", "latitude", "y", "緯度"])
    lon_col = find_col(df, ["lon", "lng", "longitude", "x", "経度"])

    if lat_col is None or lon_col is None:
        raise ValueError(f"緯度経度カラムが見つかりません。columns={list(df.columns)}")

    return lat_col, lon_col


def sample_points(df, max_points=3000):
    if len(df) > max_points:
        return df.sample(max_points, random_state=42)
    return df


def make_map(csv_path, out_path, title):
    df = pd.read_csv(csv_path)
    lat_col, lon_col = detect_lat_lon(df)

    df = df.dropna(subset=[lat_col, lon_col]).copy()
    df[lat_col] = df[lat_col].astype(float)
    df[lon_col] = df[lon_col].astype(float)

    center = [df[lat_col].median(), df[lon_col].median()]
    m = folium.Map(location=center, zoom_start=14, tiles="cartodbpositron")

    folium.map.CustomPane("points").add_to(m)

    sampled = sample_points(df)

    for _, row in sampled.iterrows():
        folium.CircleMarker(
            location=[row[lat_col], row[lon_col]],
            radius=2,
            fill=True,
            fill_opacity=0.45,
            opacity=0.45,
            popup=title
        ).add_to(m)

    title_html = f"""
    <div style="
        position: fixed;
        top: 12px;
        left: 50px;
        z-index: 9999;
        background: white;
        padding: 10px 14px;
        border: 1px solid #999;
        font-size: 18px;
        font-weight: bold;">
        {title}
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    m.save(out_path)
    print(f"Saved: {out_path}")


def main():
    make_map(BASELINE_CSV, OUT_BEFORE, "Before: baseline pseudo people flow")
    make_map(SCENARIO_CSV, OUT_AFTER, "After: LLM-generated scenario")

if __name__ == "__main__":
    main()