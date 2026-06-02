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

ジオコーディング結果が入力データの主要な目的地クラスタから大きく離れる場合は、
誤った地点として採用しません。既定ではデータ中心から `20km` を超える地点を拒否します。
この距離は変更できます。

```bash
python3 run_prototype.py --yes --geocoder-max-distance-km 30
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
- 変更対象は、目的・時間で絞った候補のうち施設から影響半径内にある既存目的地だけです。施設直近の最大選択確率を `12%` 程度にし、施設から遠くなるほど線形に選択確率を下げます。
- 目的地移動強度は `0.28` を標準にし、目的地へ完全には集めません。大型施設の影響半径は `3km` 程度を標準にしています。
- `comparison.html` の背景地図はブラウザからOpenStreetMapタイルを読みます。ネットワークに接続できない場合は点と線だけの表示になります。
- `01_make_rule_ollama.py`、`02_apply_scenario.py`、`03_make_maps.py` は初期実験用です。最短デモは `run_prototype.py` を使ってください。
