# ppflow_change_automatic

自然文の都市計画シナリオから、擬似人流トリップを簡易的に変化させ、変更前後を比較する研究プロトタイプです。

## 最短実行

このリポジトリのルートで実行します。

Web UI:

```bash
python3 web_app.py
```

ブラウザで `http://127.0.0.1:8765` を開きます。画面の「LLM推定」ボタンは
Ollama の `qwen2.5-coder:7b` を使って自然文から初期ルールを生成し、
地点名は OpenStreetMap Nominatim でジオコーディングします。

Ollama を使う場合は、先にモデルを用意します。

```bash
ollama pull qwen2.5-coder:7b
```

別モデルやURLを使う場合:

```bash
python3 web_app.py --ollama-model qwen2.5-coder:7b --ollama-url http://localhost:11434/api/chat
```

LLMなしで従来のキーワード推定だけを使う場合:

```bash
python3 web_app.py --no-llm
```

ジオコーディングなしで、従来のデータ内クラスタ推定だけを使う場合:

```bash
python3 web_app.py --no-geocode
```

Nominatim は無料の公開ジオコーディングAPIです。ローカル研究プロトタイプの
単発検索向けに使い、大量・高頻度の問い合わせには使わないでください。
利用条件は https://operations.osmfoundation.org/policies/nominatim/ を確認してください。

CLI:

```bash
python3 run_prototype.py
```

CLIでOllama推定を使う場合:

```bash
python3 run_prototype.py --llm --yes
```

CLIでジオコーディングを無効にする場合:

```bash
python3 run_prototype.py --yes --no-geocode
```

対話なしで、`input/scenaro.txt` の内容と推定デフォルトを使う場合:

```bash
python3 run_prototype.py --yes
```

出力は `output/` に保存されます。

- `scenario_rule.json`: 自然文から作ったルール
- `baseline_trips.csv`: 変更前トリップ
- `scenario_trips.csv`: 変更後トリップ
- `changed_trips.csv`: 変更されたトリップだけの前後比較
- `comparison_summary.json`: 件数や平均距離などの要約
- `comparison.html`: ブラウザで開ける前後比較レポート。Leaflet と OpenStreetMap タイルで背景地図を表示します。

## おすすめプロンプト

入力データは千葉市中央区周辺のトリップが中心なので、しっかり影響を見たい場合は千葉駅、千葉中央駅、蘇我駅などデータに近い地点を指定します。
影響を強めたいときは、自然文に影響半径、最大選択割合、移動強度を明示してから「LLM推定」を押してください。

例1:

```text
千葉駅前に大型商業施設を新設する。昼から夕方にかけて、買い物と外食目的の人が集まるようにしたい。影響半径は5km、施設直近の最大選択割合は20%、移動強度は0.35にする。
```

例2:

```text
千葉中央駅周辺に飲食店街と娯楽施設を新設する。夕方から夜にかけて、外食と自由行動目的の人が集まるようにしたい。影響半径は4km、施設直近の最大選択割合は18%、移動強度は0.33にする。
```

例3:

```text
蘇我駅前にイベント施設と商業施設を新設する。午前から夕方にかけて、買い物、外食、自由行動目的の人が集まるようにしたい。影響半径は5km、施設直近の最大選択割合は20%、移動強度は0.35にする。
```

現在の入力CSVには曜日列がないため、「休日」は目的や時間帯を推定するための文脈として扱われ、曜日条件としては絞り込まれません。

## 入力データ

初期状態では `input/trip_12101.csv` を読みます。ヘッダーなしで、以下の列順を想定しています。
このCSVは容量やデータ利用条件の都合でGitには含めず、ローカルの `input/` に配置して使います。

```text
person_id, departure_time_sec, origin_lon, origin_lat,
destination_lon, destination_lat, transport_mode, trip_purpose,
employment_status
```

列の対応:

| 実装上の列 | 元仕様の項目 | 説明 |
| --- | --- | --- |
| `person_id` | 個人ID | 世帯を識別するユニークID |
| `departure_time_sec` | 出発時間 | 0時からの秒数 |
| `origin_lon`, `origin_lat` | 出発場所 | 経度、緯度 |
| `destination_lon`, `destination_lat` | 到着場所 | 経度、緯度 |
| `transport_mode` | 交通手段 | 交通手段コード |
| `trip_purpose` | 移動目的 | 活動内容コード |
| `employment_status` | 就業状態 | 就業状況コード |

交通手段コード:

| コード | 内容 |
| ---: | --- |
| 0 | 未定義・滞在 |
| 1 | 徒歩 |
| 2 | 自転車 |
| 3 | 自動車 |
| 4 | 電車 |
| 5 | バス |
| 6 | 複数の交通手段 |

移動目的コード:

| コード | 内容 |
| ---: | --- |
| 1 | 在宅 |
| 2 | 通勤 |
| 3 | 通学 |
| 100 | 買い物 |
| 200 | 外食 |
| 300 | 通院 |
| 400 | 自由行動 |
| 500 | 業務 |

就業状況コード:

| コード | 内容 |
| ---: | --- |
| 10 | 幼児 |
| 11 | 学齢前 |
| 12 | 小学生 |
| 13 | 中学生 |
| 14 | 高校生 |
| 15 | 大学生 |
| 16 | 短期大学（専門学校含む） |
| 21 | 就業者 |
| 23 | 無職者 |

## 現状の注意

- 地点名のジオコーディングに失敗した場合は、データ内の高密度な目的地クラスタを初期値にします。
- 目的コードは上記の入力CSV仕様に従います。自然文からの推定では、買い物を `100`、外食・飲食を `200` として扱います。
- ジオコーディングできた地点は、入力データから遠くてもそのまま採用します。遠い地点の場合は、影響半径内を通るトリップがほぼ無くなるため、変更が発生しない結果になります。
- 変更対象は、目的・時間で絞った候補のうち、出発地から目的地への移動経路が施設から影響半径内を通るトリップだけです。施設直近を通る場合の最大選択確率を `12%` 程度にし、施設から遠くなるほど線形に選択確率を下げます。
- 目的地移動強度は `0.28` を標準にし、目的地へ完全には集めません。大型施設の影響半径は `3km` 程度を標準にしています。
- `comparison.html` の背景地図はブラウザからOpenStreetMapタイルを読みます。ネットワークに接続できない場合は点と線だけの表示になります。
- `01_make_rule_ollama.py`、`02_apply_scenario.py`、`03_make_maps.py` は初期実験用です。最短デモは `run_prototype.py` を使ってください。

---

# English

This is a research prototype that takes a natural-language urban planning scenario, applies a simple change to pseudo people-flow trips, and compares the before/after results.

## Quick Start

Run commands from the repository root.

Web UI:

```bash
python3 web_app.py
```

Open `http://127.0.0.1:8765` in your browser. The `Infer` button uses Ollama with `qwen2.5-coder:7b` to generate initial scenario rules from text, and place names are geocoded with OpenStreetMap Nominatim.

Prepare the Ollama model first if you want to use LLM inference.

```bash
ollama pull qwen2.5-coder:7b
```

Use another model or URL:

```bash
python3 web_app.py --ollama-model qwen2.5-coder:7b --ollama-url http://localhost:11434/api/chat
```

Use keyword-based inference without LLM:

```bash
python3 web_app.py --no-llm
```

Disable geocoding and use the data-cluster fallback:

```bash
python3 web_app.py --no-geocode
```

Nominatim is a free public geocoding API. Use it only for occasional local research prototype queries, not for large or high-frequency requests. Check the usage policy at https://operations.osmfoundation.org/policies/nominatim/.

CLI:

```bash
python3 run_prototype.py
```

Use Ollama inference from the CLI:

```bash
python3 run_prototype.py --llm --yes
```

Disable geocoding from the CLI:

```bash
python3 run_prototype.py --yes --no-geocode
```

Run non-interactively with `input/scenaro.txt` and inferred defaults:

```bash
python3 run_prototype.py --yes
```

Outputs are written to `output/`.

- `scenario_rule.json`: scenario rule generated from text
- `baseline_trips.csv`: trips before the change
- `scenario_trips.csv`: trips after the change
- `changed_trips.csv`: before/after rows for changed trips only
- `comparison_summary.json`: summary metrics such as counts and average distances
- `comparison.html`: browser report with Leaflet and OpenStreetMap background tiles

## Recommended Prompts

The current input data mainly covers trips around Chiba City's central area. For visible effects, specify places close to the data, such as Chiba Station, Chiba-Chuo Station, or Soga Station. To make the effect stronger, explicitly include the influence radius, maximum selection rate, and movement strength before pressing `Infer`.

Example 1:

```text
Build a large shopping mall in front of Chiba Station. From noon to evening, shopping and dining trips should be attracted to the station area. Use a 5 km influence radius, a 20% maximum selection rate near the facility, and a movement strength of 0.35.
```

Example 2:

```text
Create a dining district and entertainment facility around Chiba-Chuo Station. From evening to night, dining and leisure trips should be attracted to the area. Use a 4 km influence radius, an 18% maximum selection rate near the facility, and a movement strength of 0.33.
```

Example 3:

```text
Build an event venue and commercial facility in front of Soga Station. From morning to evening, shopping, dining, and leisure trips should be attracted to the station area. Use a 5 km influence radius, a 20% maximum selection rate near the facility, and a movement strength of 0.35.
```

The current CSV has no weekday or holiday column. Words such as "holiday" are used only as context for inferring purposes and time windows; they are not applied as a weekday filter.

## Input Data

By default, the prototype reads `input/trip_12101.csv`. The CSV is expected to have no header and to use the following column order. The CSV itself is not committed to Git because of size and data-use constraints; place it locally under `input/`.

```text
person_id, departure_time_sec, origin_lon, origin_lat,
destination_lon, destination_lat, transport_mode, trip_purpose,
employment_status
```

Column mapping:

| Implementation column | Source item | Description |
| --- | --- | --- |
| `person_id` | personal ID | unique ID identifying a household/person record |
| `departure_time_sec` | departure time | seconds from midnight |
| `origin_lon`, `origin_lat` | origin | longitude and latitude |
| `destination_lon`, `destination_lat` | destination | longitude and latitude |
| `transport_mode` | transport mode | transport mode code |
| `trip_purpose` | trip purpose | activity code |
| `employment_status` | employment status | employment status code |

Transport mode codes:

| Code | Meaning |
| ---: | --- |
| 0 | undefined/stay |
| 1 | walk |
| 2 | bicycle |
| 3 | car |
| 4 | train |
| 5 | bus |
| 6 | multiple modes |

Trip purpose codes:

| Code | Meaning |
| ---: | --- |
| 1 | home |
| 2 | commute |
| 3 | school |
| 100 | shopping |
| 200 | dining out |
| 300 | hospital visit |
| 400 | leisure/free activity |
| 500 | business |

Employment status codes:

| Code | Meaning |
| ---: | --- |
| 10 | infant |
| 11 | preschool child |
| 12 | elementary school student |
| 13 | junior high school student |
| 14 | high school student |
| 15 | university student |
| 16 | junior college / vocational school student |
| 21 | employed |
| 23 | unemployed |

## Current Notes

- If place-name geocoding fails, the prototype falls back to a high-density destination cluster in the data.
- Purpose codes follow the input CSV specification above. Natural-language inference treats shopping as `100`, dining/food as `200`, hospital visits as `300`, leisure/free activity as `400`, and business as `500`.
- If geocoding succeeds, the geocoded point is used even when it is far away from the input data. In that case, almost no trips will pass within the influence radius, so the result may have zero changed trips.
- Changed trips are selected from the purpose/time candidates whose origin-to-destination segment passes within the influence radius of the facility. The maximum selection probability near the facility is about `12%` by default, and the probability decreases linearly with distance from the facility.
- The default destination movement strength is `0.28`, so destinations are not moved all the way to the facility. The default influence radius for a large facility is about `3 km`.
- `comparison.html` loads OpenStreetMap tiles from the browser. If the browser has no network connection, only points and lines are displayed.
- `01_make_rule_ollama.py`, `02_apply_scenario.py`, and `03_make_maps.py` are early experiment scripts. Use `run_prototype.py` for the shortest demo path.
