# ppflow_change_automatic

自然文の都市計画シナリオから、擬似人流トリップを簡易的に変化させ、変更前後を比較する研究プロトタイプです。

## 最短実行

このリポジトリのルートで実行します。

Web UI:

```bash
python3 web_app.py
```

ブラウザで `http://127.0.0.1:8765` を開きます。

CLI:

```bash
python3 run_prototype.py
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
- `comparison.html`: ブラウザで開ける前後比較レポート

## 入力データ

初期状態では `input/trip_12101.csv` を読みます。ヘッダーなしで、以下の列順を想定しています。
このCSVは容量やデータ利用条件の都合でGitには含めず、ローカルの `input/` に配置して使います。

```text
person_id, departure_time_sec, origin_lon, origin_lat,
destination_lon, destination_lat, transport_mode, trip_purpose,
employment_status
```

## 現状の注意

- 地点名のジオコーディングはまだ行わず、データ内の高密度な目的地クラスタを初期値にします。
- 目的コードはデータ仕様に依存します。プロトタイプでは、買い物を `400`、飲食を `500` と仮定しています。
- `01_make_rule_ollama.py`、`02_apply_scenario.py`、`03_make_maps.py` は初期実験用です。最短デモは `run_prototype.py` を使ってください。
