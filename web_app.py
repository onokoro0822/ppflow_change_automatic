#!/usr/bin/env python3
"""Local web UI for the pseudo people-flow scenario prototype."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import traceback
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

from run_prototype import (
    BASE_DIR,
    DEFAULT_GEOCODER_TIMEOUT,
    DEFAULT_GEOCODER_URL,
    DEFAULT_INPUT_CSV,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SCENARIO_FILES,
    apply_scenario,
    build_rule,
    build_summary,
    format_time_window,
    load_trips,
    parse_influence_radius_km,
    parse_lon_lat,
    parse_ratio,
    parse_strength,
    parse_time_window,
    write_changed_trips,
    write_html_report,
    write_json,
    write_trips,
)


WEB_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "web_latest"
MAX_BODY_BYTES = 64 * 1024

TRIPS_CACHE = None
WEB_LLM_ENABLED = True
WEB_OLLAMA_URL = DEFAULT_OLLAMA_URL
WEB_OLLAMA_MODEL = DEFAULT_OLLAMA_MODEL
WEB_OLLAMA_TIMEOUT = DEFAULT_OLLAMA_TIMEOUT
WEB_GEOCODE_ENABLED = True
WEB_GEOCODER_URL = DEFAULT_GEOCODER_URL
WEB_GEOCODER_TIMEOUT = DEFAULT_GEOCODER_TIMEOUT


def get_trips(input_csv: Path = DEFAULT_INPUT_CSV):
    global TRIPS_CACHE
    if TRIPS_CACHE is None:
        TRIPS_CACHE = load_trips(input_csv)
    return TRIPS_CACHE


def default_scenario_text() -> str:
    for path in DEFAULT_SCENARIO_FILES:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def namespace(
    seed: int = 42,
    sample_lines: int = 550,
    background_points: int = 800,
    use_llm: bool | None = None,
    use_geocode: bool | None = None,
):
    geocode = WEB_GEOCODE_ENABLED if use_geocode is None else use_geocode
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
        no_geocode=not geocode,
        geocoder_url=WEB_GEOCODER_URL,
        geocoder_timeout=WEB_GEOCODER_TIMEOUT,
    )


def infer_payload(
    scenario_text: str,
    seed: int = 42,
    use_llm: bool | None = None,
    use_geocode: bool | None = None,
) -> dict[str, object]:
    trips = get_trips()
    args = namespace(seed=seed, use_llm=use_llm, use_geocode=use_geocode)
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
        "geocode_enabled": not args.no_geocode,
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


def run_pipeline(payload: dict[str, object]) -> dict[str, object]:
    scenario_text = str(payload.get("scenario_text") or "").strip()
    if not scenario_text:
        raise ValueError("シナリオ文を入力してください。")

    seed = int(payload.get("seed") or 42)
    use_geocode = payload.get("use_geocode")
    app_args = namespace(
        seed=seed,
        use_llm=bool(payload.get("use_llm", False)),
        use_geocode=None if use_geocode is None else bool(use_geocode),
    )
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
            "question": "影響半径 km",
            "answer": f"{influence_radius_km:g}",
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

    scenario_trips, candidates, changed_indices, selection_notes = apply_scenario(trips, rule)
    summary = build_summary(trips, scenario_trips, rule, candidates, changed_indices, selection_notes)

    WEB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    baseline_csv = WEB_OUTPUT_DIR / "baseline_trips.csv"
    scenario_csv = WEB_OUTPUT_DIR / "scenario_trips.csv"
    changed_csv = WEB_OUTPUT_DIR / "changed_trips.csv"
    rule_json = WEB_OUTPUT_DIR / "scenario_rule.json"
    summary_json = WEB_OUTPUT_DIR / "comparison_summary.json"
    html_report = WEB_OUTPUT_DIR / "comparison.html"

    write_trips(baseline_csv, trips)
    write_trips(scenario_csv, scenario_trips)
    write_changed_trips(changed_csv, trips, scenario_trips, changed_indices)
    write_json(rule_json, asdict(rule))
    write_json(summary_json, summary)
    write_html_report(html_report, trips, scenario_trips, rule, summary, candidates, changed_indices, app_args)

    return {
        "rule": asdict(rule),
        "summary": summary,
        "files": {
            "comparison_html": "/output/web_latest/comparison.html",
            "baseline_csv": "/output/web_latest/baseline_trips.csv",
            "scenario_csv": "/output/web_latest/scenario_trips.csv",
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
    .header-tools {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      margin-left: auto;
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
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
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
    label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .field-note { height: 8px; }
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
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
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
    .language-toggle {
      min-height: 32px;
      min-width: 68px;
      padding: 6px 10px;
      font-size: 13px;
      white-space: nowrap;
    }
    .actions button {
      padding: 8px 8px;
      white-space: nowrap;
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
      grid-template-columns: 48px minmax(0, 1fr);
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
      iframe { height: 680px; }
    }
    @media (max-width: 560px) {
      header { align-items: flex-start; flex-direction: column; padding: 12px 16px; }
      .header-tools { width: 100%; justify-content: space-between; margin-left: 0; }
      .status { text-align: left; }
      aside, .workspace { padding: 12px; }
      .grid2, .actions, .metrics { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1 data-i18n="app.title">擬似人流シナリオ</h1>
    <div class="header-tools">
      <button class="language-toggle" id="languageButton" type="button">EN</button>
      <div class="status" id="status">待機中</div>
    </div>
  </header>
  <main>
    <aside>
      <section>
        <h2 data-i18n="sections.scenario">シナリオ</h2>
        <div class="field">
          <label for="scenarioText" data-i18n="labels.scenarioText">自然文</label>
          <textarea id="scenarioText"></textarea>
        </div>
        <div class="actions">
          <button id="inferButton" type="button" data-i18n="actions.infer">LLM推定</button>
          <button class="primary" id="inferRunButton" type="button" data-i18n="actions.inferRun">推論して比較</button>
        </div>
      </section>

      <section>
        <h2 data-i18n="sections.confirm">確認</h2>
        <div class="field">
          <label for="targetLabel" data-i18n="labels.targetLabel">地点名</label>
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
            <label for="affectedRatio" data-i18n="labels.affectedRatio">影響割合</label>
            <input id="affectedRatio" type="number" min="1" max="30" step="1">
          </div>
          <div>
            <label for="timeWindow" data-i18n="labels.timeWindow">時間帯</label>
            <input id="timeWindow" type="text">
          </div>
        </div>
        <div class="field grid2">
          <div>
            <label for="affectedPurposes" data-i18n="labels.affectedPurposes">目的コード</label>
            <input id="affectedPurposes" type="text">
            <div class="legend" aria-label="目的コード凡例" data-i18n-aria="aria.purposeLegend">
              <div class="legend-row"><span class="legend-code">1</span><span data-i18n="purposes.home">在宅</span></div>
              <div class="legend-row"><span class="legend-code">2</span><span data-i18n="purposes.commute">通勤</span></div>
              <div class="legend-row"><span class="legend-code">3</span><span data-i18n="purposes.school">通学</span></div>
              <div class="legend-row"><span class="legend-code">100</span><span data-i18n="purposes.shopping">買い物</span></div>
              <div class="legend-row"><span class="legend-code">200</span><span data-i18n="purposes.dining">外食</span></div>
              <div class="legend-row"><span class="legend-code">300</span><span data-i18n="purposes.hospital">通院</span></div>
              <div class="legend-row"><span class="legend-code">400</span><span data-i18n="purposes.free">自由行動</span></div>
              <div class="legend-row"><span class="legend-code">500</span><span data-i18n="purposes.business">業務</span></div>
              <div class="legend-row"><span class="legend-code" data-i18n="purposes.blankCode">空欄</span><span data-i18n="purposes.blankDescription">目的コードで絞り込まない</span></div>
            </div>
          </div>
          <div>
            <label for="strength" data-i18n="labels.strength">移動強度</label>
            <input id="strength" type="number" min="0.05" max="0.7" step="0.05">
            <div class="field-note"></div>
            <label for="influenceRadius" data-i18n="labels.influenceRadius">影響半径 km</label>
            <input id="influenceRadius" type="number" min="0.3" max="10" step="0.1">
          </div>
        </div>
        <div class="actions">
          <button id="resetButton" type="button" data-i18n="actions.reset" data-i18n-title="actions.resetTitle">リセット</button>
          <button class="primary" id="runButton" type="button" data-i18n="actions.run">比較を作成</button>
        </div>
      </section>

      <section>
        <h2 data-i18n="sections.estimatedPoint">推定地点</h2>
        <div class="map-preview">
          <iframe id="targetMap" title="推定地点のGoogle Maps" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
          <div class="map-meta">
            <strong id="mapLabel">-</strong>
            <span id="mapCoords">-</span>
            <a id="mapLink" href="#" target="_blank" rel="noreferrer" data-i18n="actions.openGoogleMaps">Google Mapsで開く</a>
          </div>
        </div>
      </section>
    </aside>

    <div class="workspace">
      <div class="error" id="errorBox"></div>
      <div class="metrics">
        <div class="metric"><span data-i18n="metrics.changedTrips">変更トリップ</span><strong id="changedTrips">-</strong></div>
        <div class="metric"><span data-i18n="metrics.candidateTrips">候補トリップ</span><strong id="candidateTrips">-</strong></div>
        <div class="metric before"><span data-i18n="metrics.beforeKm">Before 平均距離</span><strong id="beforeKm">-</strong></div>
        <div class="metric after"><span data-i18n="metrics.afterKm">After 平均距離</span><strong id="afterKm">-</strong></div>
      </div>
      <div class="viewer">
        <div class="viewer-head">
          <h2 data-i18n="sections.comparison">比較</h2>
          <div class="links" id="links"></div>
        </div>
        <div class="empty" id="emptyState" data-i18n="empty.comparison">比較結果はここに表示されます</div>
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
    const inferRunButton = $("inferRunButton");
    const resetButton = $("resetButton");
    const languageButton = $("languageButton");
    let confirmationDefaults = null;
    let latestFiles = null;
    let statusKey = "idle";
    let currentLanguage = localStorage.getItem("ppflowLanguage") === "en" ? "en" : "ja";

    const I18N = {
      ja: {
        app: { title: "擬似人流シナリオ" },
        sections: {
          scenario: "シナリオ",
          confirm: "確認",
          estimatedPoint: "推定地点",
          comparison: "比較",
        },
        labels: {
          scenarioText: "自然文",
          targetLabel: "地点名",
          affectedRatio: "影響割合",
          timeWindow: "時間帯",
          affectedPurposes: "目的コード",
          strength: "移動強度",
          influenceRadius: "影響半径 km",
        },
        purposes: {
          home: "在宅",
          commute: "通勤",
          school: "通学",
          shopping: "買い物",
          dining: "外食",
          hospital: "通院",
          free: "自由行動",
          business: "業務",
          blankCode: "空欄",
          blankDescription: "目的コードで絞り込まない",
        },
        actions: {
          infer: "LLM推定",
          inferRun: "推論して比較",
          reset: "リセット",
          resetTitle: "現在の自然文から確認欄を再推定します",
          run: "比較を作成",
          openGoogleMaps: "Google Mapsで開く",
          switchLanguage: "英語表示に切り替え",
        },
        status: {
          idle: "待機中",
          loading: "読み込み中",
          inferring: "推定中",
          inferRunning: "推論と比較を作成中",
          resetting: "リセット中",
          running: "作成中",
          done: "完了",
          resetDone: "確認をリセットしました",
          error: "エラー",
        },
        metrics: {
          changedTrips: "変更トリップ",
          candidateTrips: "候補トリップ",
          beforeKm: "Before 平均距離",
          afterKm: "After 平均距離",
        },
        links: {
          html: "HTML",
          changedCsv: "変更CSV",
          ruleJson: "ルールJSON",
          summaryJson: "要約JSON",
        },
        empty: { comparison: "比較結果はここに表示されます" },
        map: {
          defaultLabel: "推定地点",
          iframeTitle: "推定地点のGoogle Maps",
          comparisonTitle: "擬似人流比較",
        },
        aria: { purposeLegend: "目的コード凡例" },
        errors: {
          noScenario: "自然文を入力してください。",
        },
      },
      en: {
        app: { title: "Pseudo People-Flow Scenario" },
        sections: {
          scenario: "Scenario",
          confirm: "Confirmation",
          estimatedPoint: "Estimated Point",
          comparison: "Comparison",
        },
        labels: {
          scenarioText: "Scenario text",
          targetLabel: "Place name",
          affectedRatio: "Impact rate",
          timeWindow: "Time window",
          affectedPurposes: "Purpose codes",
          strength: "Move strength",
          influenceRadius: "Influence radius km",
        },
        purposes: {
          home: "Home",
          commute: "Commute",
          school: "School",
          shopping: "Shopping",
          dining: "Dining out",
          hospital: "Hospital visit",
          free: "Leisure/free activity",
          business: "Business",
          blankCode: "Blank",
          blankDescription: "Do not filter by purpose",
        },
        actions: {
          infer: "Infer",
          inferRun: "Infer & Compare",
          reset: "Reset",
          resetTitle: "Re-infer the confirmation fields from the current scenario text",
          run: "Create comparison",
          openGoogleMaps: "Open in Google Maps",
          switchLanguage: "Switch to Japanese",
        },
        status: {
          idle: "Idle",
          loading: "Loading",
          inferring: "Inferring",
          inferRunning: "Inferring and creating comparison",
          resetting: "Resetting",
          running: "Creating",
          done: "Done",
          resetDone: "Confirmation reset",
          error: "Error",
        },
        metrics: {
          changedTrips: "Changed trips",
          candidateTrips: "Candidate trips",
          beforeKm: "Before avg distance",
          afterKm: "After avg distance",
        },
        links: {
          html: "HTML",
          changedCsv: "Changed CSV",
          ruleJson: "Rule JSON",
          summaryJson: "Summary JSON",
        },
        empty: { comparison: "Comparison results will appear here" },
        map: {
          defaultLabel: "Estimated point",
          iframeTitle: "Google Maps for the estimated point",
          comparisonTitle: "Pseudo people-flow comparison",
        },
        aria: { purposeLegend: "Purpose code legend" },
        errors: {
          noScenario: "Enter scenario text.",
        },
      },
    };

    function cloneData(data) {
      return JSON.parse(JSON.stringify(data));
    }

    function t(key) {
      return key.split(".").reduce((value, part) => value && value[part], I18N[currentLanguage]) || key;
    }

    const PLACE_TRANSLATIONS = [
      ["千葉駅", "Chiba Station"],
      ["千葉駅前", "Chiba Station area"],
      ["千葉中央駅", "Chiba-Chuo Station"],
      ["千葉中央駅周辺", "Chiba-Chuo Station area"],
      ["蘇我駅", "Soga Station"],
      ["蘇我駅前", "Soga Station area"],
      ["柏の葉キャンパス駅前", "Kashiwanoha-campus Station area"],
      ["データ内高密度目的地クラスタ", "High-density destination cluster in the data"],
      ["アメリカ", "United States"],
      ["米国", "United States"],
    ];

    function localizeTargetLabel(label) {
      const text = String(label || "").trim();
      const match = PLACE_TRANSLATIONS.find(([ja, en]) => text === ja || text === en);
      if (!match) return text;
      return currentLanguage === "en" ? match[1] : match[0];
    }

    function localizeTimeWindow(value) {
      const text = String(value || "").trim();
      if (text.toLowerCase() === "all" || text === "全日" || text === "終日") {
        return currentLanguage === "en" ? "All" : "終日";
      }
      return text;
    }

    function renderLinks(files) {
      latestFiles = files;
      $("links").innerHTML = [
        [t("links.html"), files.comparison_html],
        [t("links.changedCsv"), files.changed_csv],
        [t("links.ruleJson"), files.rule_json],
        [t("links.summaryJson"), files.summary_json],
      ].map(([label, href]) => `<a href="${href}" target="_blank" rel="noreferrer">${label}</a>`).join("");
    }

    function applyLanguage() {
      document.documentElement.lang = currentLanguage;
      document.title = t("app.title");
      document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = t(node.dataset.i18n);
      });
      document.querySelectorAll("[data-i18n-title]").forEach((node) => {
        node.title = t(node.dataset.i18nTitle);
      });
      document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
        node.setAttribute("aria-label", t(node.dataset.i18nAria));
      });
      languageButton.textContent = currentLanguage === "ja" ? "EN" : "日本語";
      languageButton.setAttribute("aria-label", t("actions.switchLanguage"));
      $("targetMap").title = t("map.iframeTitle");
      $("comparisonFrame").title = t("map.comparisonTitle");
      status.textContent = t(`status.${statusKey}`);
      $("targetLabel").value = localizeTargetLabel($("targetLabel").value);
      $("timeWindow").value = localizeTimeWindow($("timeWindow").value);
      if (latestFiles) renderLinks(latestFiles);
      updateTargetMap();
    }

    function setBusy(nextStatusKey, busy) {
      statusKey = nextStatusKey;
      status.textContent = t(`status.${statusKey}`);
      runButton.disabled = busy;
      inferButton.disabled = busy;
      inferRunButton.disabled = busy;
      resetButton.disabled = busy;
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

    function updateTargetMap() {
      const lonText = $("targetLon").value.trim();
      const latText = $("targetLat").value.trim();
      const lon = Number(lonText);
      const lat = Number(latText);
      const label = $("targetLabel").value || t("map.defaultLabel");

      if (!lonText || !latText || !Number.isFinite(lon) || !Number.isFinite(lat)) {
        $("targetMap").removeAttribute("src");
        $("mapLabel").textContent = "-";
        $("mapCoords").textContent = "-";
        $("mapLink").href = "#";
        return;
      }

      const query = encodeURIComponent(`${lat},${lon}`);
      $("targetMap").src = `https://maps.google.com/maps?q=${query}&z=16&output=embed`;
      $("mapLabel").textContent = label;
      $("mapCoords").textContent = `${lon.toFixed(6)}, ${lat.toFixed(6)}`;
      $("mapLink").href = `https://www.google.com/maps/search/?api=1&query=${query}`;
    }

    function fillConfirmation(data) {
      $("targetLabel").value = localizeTargetLabel(data.target_label || "");
      $("targetLon").value = Number(data.target_lon).toFixed(6);
      $("targetLat").value = Number(data.target_lat).toFixed(6);
      $("affectedRatio").value = data.affected_ratio_percent || Math.round((data.affected_ratio || 0.08) * 100);
      $("timeWindow").value = localizeTimeWindow(data.time_window || "all");
      $("affectedPurposes").value = (data.affected_purposes || []).join(",");
      $("strength").value = Number(data.strength || 0.28).toFixed(2);
      $("influenceRadius").value = Number(data.influence_radius_km || 2).toFixed(1);
      updateTargetMap();
    }

    function fillDefaults(data) {
      $("scenarioText").value = data.scenario_text || $("scenarioText").value;
      confirmationDefaults = cloneData(data);
      fillConfirmation(confirmationDefaults);
      resetButton.disabled = false;
    }

    function ollamaWarning(data) {
      return (data.notes || []).find((note) => note.includes("Ollama推定に失敗")) || "";
    }

    function formatNumber(value) {
      return Number(value).toLocaleString(currentLanguage === "ja" ? "ja-JP" : "en-US");
    }

    function updateResults(data) {
      const summary = data.summary;
      $("changedTrips").textContent = formatNumber(summary.changed_trips);
      $("candidateTrips").textContent = formatNumber(summary.candidate_trips);
      $("beforeKm").textContent = `${Number(summary.avg_distance_to_target_before_km).toFixed(2)} km`;
      $("afterKm").textContent = `${Number(summary.avg_distance_to_target_after_km).toFixed(2)} km`;

      renderLinks(data.files);

      $("emptyState").hidden = true;
      $("comparisonFrame").hidden = false;
      $("comparisonFrame").src = `${data.files.comparison_html}?t=${Date.now()}`;
    }

    async function loadDefaults() {
      setBusy("loading", true);
      setError("");
      try {
        const data = await requestJSON("/api/defaults");
        fillDefaults(data);
        setBusy("idle", false);
      } catch (error) {
        setBusy("error", false);
        setError(error.message);
      }
    }

    async function infer() {
      setBusy("inferring", true);
      setError("");
      try {
        const data = await requestJSON("/api/infer", { scenario_text: $("scenarioText").value });
        fillDefaults(data);
        setBusy("idle", false);
        setError(ollamaWarning(data));
      } catch (error) {
        setBusy("error", false);
        setError(error.message);
      }
    }

    async function run() {
      setBusy("running", true);
      setError("");
      try {
        const data = await requestJSON("/api/run", formPayload());
        updateResults(data);
        setBusy("done", false);
      } catch (error) {
        setBusy("error", false);
        setError(error.message);
      }
    }

    async function inferAndRun() {
      setBusy("inferRunning", true);
      setError("");
      try {
        const inferred = await requestJSON("/api/infer", { scenario_text: $("scenarioText").value });
        fillDefaults(inferred);
        setBusy("inferRunning", true);
        const data = await requestJSON("/api/run", formPayload());
        updateResults(data);
        setBusy("done", false);
        setError(ollamaWarning(inferred));
      } catch (error) {
        setBusy("error", false);
        setError(error.message);
      }
    }

    async function resetConfirmation() {
      const scenarioText = $("scenarioText").value.trim();
      setBusy("resetting", true);
      setError("");
      try {
        const data = scenarioText
          ? await requestJSON("/api/infer", { scenario_text: scenarioText })
          : await requestJSON("/api/defaults");
        fillDefaults(data);
        setBusy("resetDone", false);
        setError(ollamaWarning(data));
      } catch (error) {
        if (confirmationDefaults) fillConfirmation(confirmationDefaults);
        setBusy("error", false);
        setError(error.message || t("errors.noScenario"));
      }
    }

    languageButton.addEventListener("click", () => {
      currentLanguage = currentLanguage === "ja" ? "en" : "ja";
      localStorage.setItem("ppflowLanguage", currentLanguage);
      applyLanguage();
    });
    inferButton.addEventListener("click", infer);
    inferRunButton.addEventListener("click", inferAndRun);
    resetButton.addEventListener("click", resetConfirmation);
    runButton.addEventListener("click", run);
    ["targetLabel", "targetLon", "targetLat"].forEach((id) => {
      $(id).addEventListener("input", updateTargetMap);
    });
    applyLanguage();
    loadDefaults();
  </script>
</body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    server_version = "PPFlowWeb/0.1"

    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        if request_path == "/":
            self.send_html(INDEX_HTML)
            return
        if request_path == "/api/defaults":
            self.send_json(infer_payload(default_scenario_text(), use_llm=False))
            return
        if request_path.startswith("/output/"):
            self.send_output_file(request_path.removeprefix("/output/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/infer":
                payload = self.read_json()
                scenario_text = str(payload.get("scenario_text") or default_scenario_text()).strip()
                self.send_json(infer_payload(scenario_text, use_llm=WEB_LLM_ENABLED))
                return
            if self.path == "/api/run":
                payload = self.read_json()
                self.send_json(run_pipeline(payload))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
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
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--no-geocode", action="store_true", help="Disable Nominatim geocoding.")
    parser.add_argument("--geocoder-url", default=DEFAULT_GEOCODER_URL)
    parser.add_argument("--geocoder-timeout", type=float, default=DEFAULT_GEOCODER_TIMEOUT)
    return parser.parse_args()


def main() -> None:
    global WEB_GEOCODE_ENABLED, WEB_GEOCODER_TIMEOUT, WEB_GEOCODER_URL
    global WEB_LLM_ENABLED, WEB_OLLAMA_MODEL, WEB_OLLAMA_TIMEOUT, WEB_OLLAMA_URL
    args = parse_args()
    WEB_LLM_ENABLED = not args.no_llm
    WEB_OLLAMA_URL = args.ollama_url
    WEB_OLLAMA_MODEL = args.ollama_model
    WEB_OLLAMA_TIMEOUT = args.ollama_timeout
    WEB_GEOCODE_ENABLED = not args.no_geocode
    WEB_GEOCODER_URL = args.geocoder_url
    WEB_GEOCODER_TIMEOUT = args.geocoder_timeout
    try:
        server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    except OSError as exc:
        raise SystemExit(f"Server failed to start on {args.host}:{args.port}: {exc}") from exc

    url = f"http://{args.host}:{args.port}"
    print(f"Serving local UI: {url}")
    if WEB_LLM_ENABLED:
        print(f"Ollama inference: {WEB_OLLAMA_MODEL} at {WEB_OLLAMA_URL}")
    else:
        print("Ollama inference: disabled")
    if WEB_GEOCODE_ENABLED:
        print(f"Geocoding: {WEB_GEOCODER_URL}")
    else:
        print("Geocoding: disabled")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
