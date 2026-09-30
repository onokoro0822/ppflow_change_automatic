# ppflow_change_automatic

自然文の都市計画シナリオから、擬似人流トリップを簡易的に変化させ、変更前後を比較する研究プロトタイプです。

## 最短実行

このリポジトリのルートで実行します。

Web UI（Gemini対話 + 必要時だけTavily検索）:

```bash
zsh start_web_app.command
```

初回だけGeminiとTavilyのAPIキーを非表示で入力します。キーはmacOSキーチェーンへ保存され、
2回目以降は入力なしで起動します。キーはリポジトリやブラウザには保存されません。保存したキーを
削除して再登録する場合は、次を実行してから起動し直します。

```bash
zsh start_web_app.command --forget-keys
zsh start_web_app.command
```

ブラウザで `http://127.0.0.1:8765` を開きます。計画エージェントとの対話には、無料枠で
利用できるGemini 3.1 Flash-Liteを使用します。Web検索にはTavilyの無料Researcherプランを
使用します。Geminiが現在情報の確認が必要だと判断したときだけ検索し、その検索結果をGeminiへ
戻して回答を作ります。APIキーは `GEMINI_API_KEY` と `TAVILY_API_KEY` 環境変数で渡し、
どちらもブラウザへ送信しません。

TavilyのAPIキーは https://app.tavily.com で取得できます。Researcherプランはカード登録不要で
月1,000クレジットです。このアプリは `search_depth=basic` に固定しているため、1回の検索は
1クレジット、最大で月1,000回のWeb検索が目安です。対話の全ターンで検索するわけではありません。
例えば10往復中2回だけ検索する使い方なら、Tavily側は約500セッション/月が目安になります
（Gemini側の無料枠と混雑状況は別に制約されます）。通常の対話ターンはGeminiを1回、検索する
ターンは検索前後でGeminiを2回呼びます。Geminiの有効なRPM・TPM・RPD上限はプロジェクトごとに
変わるため、Google AI Studioの Rate limits 画面で確認してください。Tavilyの使用回数は画面に
セッション単位で表示します。

`GEMINI_API_KEY` が未設定の場合、対話はルールベースへ自動的に切り替わります。
`TAVILY_API_KEY` が未設定の場合はGemini対話だけを続け、Web検索を行いません。Tavily検索を
一時的に無効化する場合は `--no-tavily-search` を付けます。別モデルを指定する場合や、検索対応の
課金プロジェクトでGemini内蔵のGoogle検索を明示的に有効にする場合は次のように起動します。

```bash
GEMINI_API_KEY="..." python3 web_app.py --gemini-model gemini-3.5-flash
GEMINI_API_KEY="..." python3 web_app.py --gemini-model gemini-3.5-flash --web-search
```

画面の「LLM推定」ボタンは、対話とは別のシミュレーション初期ルール作成工程です。この工程は
従来どおりOllamaの `qwen2.5-coder:7b` を使い、地点名はOpenStreetMap Nominatimで
ジオコーディングします。

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

Web UIにはGeminiを使う計画エージェントとの対話セッションがあります。対象地、
用途構成、想定利用者、規模、配置・接続、曜日、時間帯、比較条件を一問一答で確認し、LLMが各回答を
踏まえて具体的なたたき台を生成します。対話は人流データを参照せず、右側の計画条件シートだけを更新します。
返答は理解内容と控えめな提案一つに抑えます。実在建物の「4階分」などから面積を検討する場合は
Tavily検索を行い、公開情報に根拠がない数値は計画条件にも仮定欄にも採用しません。検索結果から
算定できる場合も、出典付きの参考推定として確定値と分けて表示します。
階数・床面積・収容人数は不足項目を個別に確認し、Web検索でも推定できない場合は同じ項目に
留まって利用者へ数値を尋ねます。「計画条件を人流解析へ反映」では対象地名をNominatimで
ジオコーディングし、lon/latとGoogleマップを同時に更新します。
利用者が質問の意味や根拠を尋ねたターンでは計画条件を更新せず、同じ項目に留まります。各返答には
そのターンで反映したパラメータ名と値を表示し、質問への回答時は「今回は計画条件を変更していません」
と表示します。
人流シミュレーションは別工程です。計画条件の確定後に「計画条件を人流解析へ反映」を押すと、
会話文ではなく構造化された対象地・用途・規模・日時・接続・比較条件だけを解析パラメータへ変換します。
Gemini APIに接続できない場合や応答が不正な場合は、
ルールベースの対話へ自動的に切り替わります。`--no-llm` で起動した場合もルールベース
で動作します。セッションはローカルサーバーのメモリ内に保持されるため、
`web_app.py` を終了すると消去されます。

Gemini対話のタイムアウトは既定で60秒です。必要に応じて `--gemini-timeout` で変更できます。

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

## 中京PTから用途別の出発地分布を作る

第6回中京都市圏PT調査のOD集計表から、名鉄名古屋周辺を含む到着中ゾーン5について、
商業・オフィス・ホテル別の出発中ゾーン分布を作ります。

初回はプロジェクト専用のPython環境を作成します。

```bash
./setup_analysis_env.command
```

構築した環境で集計と図表生成を実行します。

```bash
.venv/bin/python chukyo_pt_origin_distribution.py
```

出力は`output/chukyo_pt_origin_distribution/`のPNG・SVG・CSV・JSONです。3用途の比較図、
用途別の個別図、上位15ゾーンの要約表、全ゾーンの縦長表を生成します。別の到着中ゾーンを対象にする場合は
`--destination-zone`を指定します。複数ゾーンも指定できます。

```bash
.venv/bin/python chukyo_pt_origin_distribution.py \
  --destination-zone 5 \
  --output-dir output/chukyo_pt_origin_distribution
```

現在の`chukyo_pt_2022_od_purpose_mode_raw.csv`には到着施設列がないため、用途別の目的集合を使う
暫定的な`purpose_proxy`です。`到着施設`列を含む同形式の明細CSVを入力すると、施設名と目的の両方で
集計する`destination_facility_and_purpose`へ自動的に切り替わります。出発地は居住地ではなく、
施設へ向かう直前活動の中ゾーンです。詳しい式と制約は
[中京PT用途別出発地分布 初版](docs/progress/20260909_中京PT用途別出発地分布初版.md)を参照してください。

## 名鉄跡地のOD差分を生成する

用途別出発地分布を作成後、全商業型25,284到着トリップの初版を実行します。

```bash
.venv/bin/python meitetsu_origin_distribution_replacement.py
```

元のSQLiteは変更しません。出力はoutput/meitetsu_origin_distribution_replacement/commercial/に保存し、
教授説明用のPNG・SVG、選択した到着トリップ、変更対象OD行、座標差分、分布比較、監査JSONを生成します。
初版は買物目的100を一人一到着まで選び、到着目的地と後続トリップ出発地を名鉄跡地代表点へ変更します。
詳しい結果と限界は
[名鉄跡地への出発地分布反映とOD変更 初版](docs/progress/20260909_名鉄跡地OD分布反映初版.md)を参照してください。

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

Web UIは `input/nagoya/` の名古屋市16区CSVを読みます。シミュレーション時は16ファイルの
全件をストリーミング走査し、地図表示用のサンプルだけをメモリに保持します。CSVは
ヘッダーなしで、以下の列順を想定しています。
このCSVは容量やデータ利用条件の都合でGitには含めず、ローカルの `input/` に配置して使います。

```text
person_id, departure_time_sec, origin_lon, origin_lat,
destination_lon, destination_lat, transport_mode, trip_purpose,
employment_status
```

列の対応:

| 実装上の列                           | 元仕様の項目 | 説明                     |
| ------------------------------------ | ------------ | ------------------------ |
| `person_id`                          | 個人ID       | 世帯を識別するユニークID |
| `departure_time_sec`                 | 出発時間     | 0時からの秒数            |
| `origin_lon`, `origin_lat`           | 出発場所     | 経度、緯度               |
| `destination_lon`, `destination_lat` | 到着場所     | 経度、緯度               |
| `transport_mode`                     | 交通手段     | 交通手段コード           |
| `trip_purpose`                       | 移動目的     | 活動内容コード           |
| `employment_status`                  | 就業状態     | 就業状況コード           |

交通手段コード:

| コード | 内容           |
| -----: | -------------- |
|      0 | 未定義・滞在   |
|      1 | 徒歩           |
|      2 | 自転車         |
|      3 | 自動車         |
|      4 | 電車           |
|      5 | バス           |
|      6 | 複数の交通手段 |

移動目的コード:

| コード | 内容     |
| -----: | -------- |
|      1 | 在宅     |
|      2 | 通勤     |
|      3 | 通学     |
|    100 | 買い物   |
|    200 | 外食     |
|    300 | 通院     |
|    400 | 自由行動 |
|    500 | 業務     |

就業状況コード:

| コード | 内容                     |
| -----: | ------------------------ |
|     10 | 幼児                     |
|     11 | 学齢前                   |
|     12 | 小学生                   |
|     13 | 中学生                   |
|     14 | 高校生                   |
|     15 | 大学生                   |
|     16 | 短期大学（専門学校含む） |
|     21 | 就業者                   |
|     23 | 無職者                   |

## トリップチェーンの復元

名古屋市16区のCSVから、同一人物のトリップを出発時刻順に並べ、前後トリップと
空間的な連続性を参照できるSQLiteデータベースを作成します。

```bash
python3 trip_chains.py
```

出力先は `output/trip_chains/` です。

- `nagoya_trip_chains.sqlite3`: 全トリップ、人物別リンク、前後トリップ参照ビュー
- `summary.json`: 人物数、連続性率、処理時間、メモリ使用量
- `trip_chain_sample.csv`: 内容確認用の先頭1,000件

SQLiteの `trip_chain_rows` ビューでは、`sequence_index`、`chain_length`、
`previous_trip_purpose`、`next_trip_purpose`、前トリップ目的地との距離などを
一行で参照できます。空間連続性は既定で200m以内です。

復元後、分析用の品質レイヤーを追加します。

```bash
python3 trip_chain_quality.py
```

0〜86,399秒の範囲外にある時刻は補正せず分析対象外とし、200m超の空間ギャップでは
分析系列を分割します。元のトリップとチェーンは保持されます。結果は
`quality_summary.json`、SQLiteの`trip_analysis_quality`テーブル、
`analysis_trip_chain_rows`ビューへ保存されます。

同名のデータベースが存在する場合は上書きせず停止します。再作成する場合は、既存の
解析出力を別名で保存するか、不要であることを確認してから削除してください。

## 商業施設利用者プロファイル

品質レイヤーを適用したチェーンから、買い物・外食・自由行動目的の利用者プロファイルを集計します。

全市集計:

```bash
python3 commercial_profiles.py
```

指定地点の周辺集計:

```bash
python3 commercial_profiles.py \
  --output-dir output/commercial_profiles/example_1km \
  --target-lon 136.8845 \
  --target-lat 35.1708 \
  --radius-km 1.0
```

就業状態、時間帯、移動距離帯、交通手段、直前・直後の交通手段と移動目的、
3段階の活動・交通系列を
集計し、`profile.json`、`profile_counts.csv`、`selected_trip_sample.csv`を出力します。
指定地点集計では、目的地が指定半径内にあるトリップだけを対象にします。

GeoJSONの施設境界とバッファを使う場合:

```bash
python3 commercial_profiles.py \
  --output-dir output/commercial_profiles/midland_square_50m \
  --boundary-geojson data/reference_facilities/midland_square.geojson \
  --boundary-buffer-m 50
```

全市構成比とのFCPI型比較と、潜在来訪者の初期抽出:

```bash
python3 facility_preference.py
python3 potential_visitors.py
python3 destination_replacement.py
python3 commercial_sensitivity.py
```

参照施設設定は`config/reference_facilities/midland_square.json`、比較結果は
`output/facility_preference/`、候補者集計は`output/potential_visitors/`、目的地置換は
`output/destination_replacement/`、感度分析は`output/sensitivity/`へ保存されます。
目的地置換は元のSQLiteを変更せず、到着トリップの目的地と同一分析系列の後続トリップ出発地を
座標差分として出力します。

## 名鉄百貨店本店の営業終了後を想定した買い物目的地再配分

現在の研究の中心シナリオは、名鉄付近へ新しい来訪者を追加する処理ではなく、
Pseudo-PFLOWに含まれる旧本店付近の買い物利用者が、店舗営業終了後に別の商業目的地へ
移る変化の推定です。

```bash
python3 facility_closure_reallocation.py
```

旧本店の暫定代表点50m以内へ買い物目的で到着する全人物を抽出し、
既存データで観測された別の買い物目的地点へ、観測訪問数と出発地距離を使って再配分します。
到着目的地と同一分析系列の後続トリップ出発地を連続して変更します。

出力先は`output/facility_closure/meitetsu_nagoya_closure_demo/`です。

- `summary.json`: 抽出、再配分、距離、チェーン検査の要約
- `selected_people.csv`: 該当した全1,977人の集計
- `mobmap_changed_trips_before.csv`: 変更対象の到着・直後トリップだけを抽出したBefore（7,518点）
- `mobmap_changed_trips_after.csv`: 同じトリップのAfter（7,518点）
- `mobmap_changed_trips_moving_only_before.csv`: 区間ごとにIDを分け、移動中だけ表示するBefore（7,518点・3,759 objects）
- `mobmap_changed_trips_moving_only_after.csv`: 同じ移動区間のAfter（7,518点・3,759 objects）
- `mobmap_changed_trips_display.jpg`: Mobmapで2レイヤーと移動線を表示した確認画像
- `selected_visits.csv`: 変更した全1,986買い物トリップの詳細
- `alternative_destinations.csv`: 選ばれた代替目的地点と人数
- `coordinate_changes.csv`: 元DBへ適用可能な座標差分
- `mobmap_before.csv`: Mobmap用の変更前チェーン
- `mobmap_after.csv`: Mobmap用の変更後チェーン
- `Mobmapでの確認方法.md`: 読み込み手順と注意点

現在の50m条件では1,977人全員を処理します。ただし、この人数は実測来館者数ではなく、
Pseudo-PFLOW上で暫定条件に該当した人物数です。また、
Pseudo-PFLOWはODデータなので、Mobmap上の線は実道路・鉄道軌跡ではなくOD間の補間です。

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

| Implementation column                | Source item       | Description                                     |
| ------------------------------------ | ----------------- | ----------------------------------------------- |
| `person_id`                          | personal ID       | unique ID identifying a household/person record |
| `departure_time_sec`                 | departure time    | seconds from midnight                           |
| `origin_lon`, `origin_lat`           | origin            | longitude and latitude                          |
| `destination_lon`, `destination_lat` | destination       | longitude and latitude                          |
| `transport_mode`                     | transport mode    | transport mode code                             |
| `trip_purpose`                       | trip purpose      | activity code                                   |
| `employment_status`                  | employment status | employment status code                          |

Transport mode codes:

| Code | Meaning        |
| ---: | -------------- |
|    0 | undefined/stay |
|    1 | walk           |
|    2 | bicycle        |
|    3 | car            |
|    4 | train          |
|    5 | bus            |
|    6 | multiple modes |

Trip purpose codes:

| Code | Meaning               |
| ---: | --------------------- |
|    1 | home                  |
|    2 | commute               |
|    3 | school                |
|  100 | shopping              |
|  200 | dining out            |
|  300 | hospital visit        |
|  400 | leisure/free activity |
|  500 | business              |

Employment status codes:

| Code | Meaning                                    |
| ---: | ------------------------------------------ |
|   10 | infant                                     |
|   11 | preschool child                            |
|   12 | elementary school student                  |
|   13 | junior high school student                 |
|   14 | high school student                        |
|   15 | university student                         |
|   16 | junior college / vocational school student |
|   21 | employed                                   |
|   23 | unemployed                                 |

## Current Notes

- If place-name geocoding fails, the prototype falls back to a high-density destination cluster in the data.
- Purpose codes follow the input CSV specification above. Natural-language inference treats shopping as `100`, dining/food as `200`, hospital visits as `300`, leisure/free activity as `400`, and business as `500`.
- If geocoding succeeds, the geocoded point is used even when it is far away from the input data. In that case, almost no trips will pass within the influence radius, so the result may have zero changed trips.
- Changed trips are selected from the purpose/time candidates whose origin-to-destination segment passes within the influence radius of the facility. The maximum selection probability near the facility is about `12%` by default, and the probability decreases linearly with distance from the facility.
- The default destination movement strength is `0.28`, so destinations are not moved all the way to the facility. The default influence radius for a large facility is about `3 km`.
- `comparison.html` loads OpenStreetMap tiles from the browser. If the browser has no network connection, only points and lines are displayed.
- `01_make_rule_ollama.py`, `02_apply_scenario.py`, and `03_make_maps.py` are early experiment scripts. Use `run_prototype.py` for the shortest demo path.

## Pseudo-PFLOWキャパシティ感度実験（2026-09-29）

外付けSSD到着前は、Git対象外の`.local/pflow/`へ愛知県の最小入力だけを置いて2%標本を実行する。
内蔵側の上限は10 GiB、空き下限は20 GiBで、スクリプトが開始前後に検査する。

```bash
# 取得対象だけ確認してから、愛知県最小入力を取得
scripts/download_pflow_inputs.sh --scope aichi-minimal --dry-run
scripts/download_pflow_inputs.sh --scope aichi-minimal

# baseline 1件、または全10件を実行
scripts/run_pflow_capacity_sweep.sh --scenario baseline
scripts/run_pflow_capacity_sweep.sh --all --resume

# 事前コンパイル済みの複数ケースを別プロセスで回す場合
scripts/run_pflow_capacity_sweep.sh --scenario mesh_2x --skip-compile

# 結果を集計
python3 pflow_capacity_analysis.py \
  --input-root .local/pflow/output/capacity_experiment \
  --output-dir .local/pflow/output/capacity_experiment/summary
```

同一seedのbaselineを2回実行し、207 CSV・31,788,077 bytesの完全一致を確認済みです。
感度実験では全10ケースの活動数・motif・対象行政界到着が一致し、capacity差分は対象行政界内の
メッシュ・施設配分だけを変更しました。

### 目標来訪人数からcapacityを一度で逆算する

来訪需要は`../nagoya_caluclation/scenario_distributions.py`で計算され、全商業型25,284件は
`config/development_scenarios/meitetsu_origin_distribution_commercial.json`へ接続されています。
既存10ケースからメッシュ選択率と施設選択率を逆算し、1ケースだけ実行するには次を使います。

```bash
# capacityと実行コマンドだけ確認
scripts/run_pflow_target_arrivals.sh --dry-run --skip-compile

# 内蔵ディスクで2%を1回実行
scripts/run_pflow_target_arrivals.sh --skip-compile

# SSD上で全数を1回実行
PFLOW_HOME=/Volumes/PFLOW_SSD/PFLOW \
  scripts/run_pflow_target_arrivals.sh --sample-factor 1 --skip-compile
```

25,284件ではメッシュ倍率15.681、施設capacity 9,750,000を推定した。2%の単一実行は515件、
全数換算25,750件で、目標との差+466件・絶対誤差率1.84%だった。capacityだけでは対象行政界の
需要上限を超えられないため、到達不能な目標は実行前にエラーとする。

SSD到着後は`PFLOW_SSD`をAPFSで用意し、`scripts/migrate_pflow_to_ssd.sh`でコピー・照合する。
その後`PFLOW_HOME=/Volumes/PFLOW_SSD/PFLOW`として、`download_pflow_inputs.sh --scope full-processing`
をまず`--dry-run`、次に実行する。どの処理も`--delete`は使わず、SSD側の動作確認前に内蔵データを消さない。

scenario JSONは`config/pflow_capacity/`、詳しい設計・データ範囲・勝谷さんへの確認事項は
[`mcd/mtg/20260924/capacity_experiment_aws_plan.md`](mcd/mtg/20260924/capacity_experiment_aws_plan.md)に記録した。
