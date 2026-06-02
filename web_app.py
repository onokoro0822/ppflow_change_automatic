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
    DEFAULT_INPUT_CSV,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SCENARIO_FILES,
    apply_scenario,
    build_rule,
    build_summary,
    format_time_window,
    load_trips,
    parse_lon_lat,
    parse_ratio,
    parse_time_window,
    write_changed_trips,
    write_html_report,
    write_json,
    write_trips,
)


WEB_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "web_latest"
MAX_BODY_BYTES = 64 * 1024

TRIPS_CACHE = None


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


def namespace(seed: int = 42, sample_lines: int = 1200, background_points: int = 800):
    return SimpleNamespace(
        yes=True,
        seed=seed,
        sample_lines=sample_lines,
        background_points=background_points,
        output_dir=WEB_OUTPUT_DIR,
    )


def infer_payload(scenario_text: str, seed: int = 42) -> dict[str, object]:
    trips = get_trips()
    rule = build_rule(trips, scenario_text, namespace(seed=seed))
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
    app_args = namespace(seed=seed)
    trips = get_trips()
    rule = build_rule(trips, scenario_text, app_args)

    target_value = f"{payload.get('target_lon', rule.target_lon)},{payload.get('target_lat', rule.target_lat)}"
    target_lon, target_lat = parse_lon_lat(target_value, rule.target_lon, rule.target_lat)
    affected_ratio = parse_ratio(str(payload.get("affected_ratio", rule.affected_ratio)), rule.affected_ratio)
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
    ]

    rule = replace(
        rule,
        target_label=target_label,
        target_lon=target_lon,
        target_lat=target_lat,
        affected_ratio=affected_ratio,
        affected_purposes=affected_purposes,
        time_window=time_window,
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
      .status { text-align: left; }
      aside, .workspace { padding: 12px; }
      .grid2, .actions, .metrics { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>擬似人流シナリオ</h1>
    <div class="status" id="status">待機中</div>
  </header>
  <main>
    <aside>
      <section>
        <h2>シナリオ</h2>
        <div class="field">
          <label for="scenarioText">自然文</label>
          <textarea id="scenarioText"></textarea>
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
        <div class="field">
          <label for="affectedPurposes">目的コード</label>
          <input id="affectedPurposes" type="text">
        </div>
        <div class="actions">
          <button id="inferButton" type="button">推定値</button>
          <button class="primary" id="runButton" type="button">比較を作成</button>
        </div>
      </section>
    </aside>

    <div class="workspace">
      <div class="error" id="errorBox"></div>
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

    function setBusy(message, busy) {
      status.textContent = message;
      runButton.disabled = busy;
      inferButton.disabled = busy;
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
      };
    }

    function fillDefaults(data) {
      $("scenarioText").value = data.scenario_text || $("scenarioText").value;
      $("targetLabel").value = data.target_label || "";
      $("targetLon").value = Number(data.target_lon).toFixed(6);
      $("targetLat").value = Number(data.target_lat).toFixed(6);
      $("affectedRatio").value = data.affected_ratio_percent || Math.round((data.affected_ratio || 0.25) * 100);
      $("timeWindow").value = data.time_window || "all";
      $("affectedPurposes").value = (data.affected_purposes || []).join(",");
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
            self.send_json(infer_payload(default_scenario_text()))
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
                self.send_json(infer_payload(scenario_text))
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    except OSError as exc:
        raise SystemExit(f"Server failed to start on {args.host}:{args.port}: {exc}") from exc

    url = f"http://{args.host}:{args.port}"
    print(f"Serving local UI: {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
