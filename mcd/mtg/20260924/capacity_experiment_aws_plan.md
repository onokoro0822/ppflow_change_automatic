# Pseudo-PFLOWキャパシティ実験・AWS取得実装

更新日: 2026-09-29 JST

## 実装した方針

[2026年9月24日MTG要約](summary.md)に従い、Markovモデルによる活動系列は変更しない。
目的地決定のうち、名鉄跡地を含む3次メッシュ`52366700`の買物キャパシティと、同メッシュへ追加する
仮想商業施設のキャパシティだけをscenario JSONで上書きする。元のS3入力CSVは変更しない。

愛知県の最小入力はプロジェクト配下の`.local/pflow/`へ置き、Git対象外とした。内蔵領域では
PFLOW入力・出力の合計を10 GiB以下、ディスク空きを20 GiB以上に保つ。全国入力の取得は
SSD移行後だけ許可する。

## 目的地決定コードの確認結果

Pseudo-PFLOW-v3の`ActGenerator.choiceFreeDestination`は、自由活動の目的地を次の順で選ぶ。

1. MNLパラメータで行先行政界を選ぶ。距離、性別、同一行政界、65歳以上、行政界面積、人口比、従業者比を使う。
2. 行先行政界内でメッシュを選ぶ。買物では`mesh_ecensus.csv`から読んだ経済センサス特徴量のindex 4をキャパシティに使う。同一行政界内は`log(1 + capacity) / distance^beta`、別行政界はキャパシティ比で選ぶ。
3. 選択メッシュ内の小売施設を施設キャパシティ比で選ぶ。現行`city_retail.csv`の全小売施設はコード上、一律10,000で読み込まれる。

したがって、メッシュ／施設キャパシティの変更は行政界MNLの説明変数へは伝播しない。メッシュ変更は
選択済み行政界内のメッシュ配分を、施設変更は選択済みメッシュ内の施設配分を変える。これが今回の
感度実験で別々の水準を置く理由である。

## コード変更

隣接する`Pseudo-PFLOW-v3`へ次を実装した。

- `--prefectures`、`--sample-factor`、`--seed`、`--scenario`、`--output-dir`を3生成器へ追加した。
- 既定値は従来互換の県13、標本率50、seed 42、従来出力先とした。
- 乱数列をseedとperson IDから作るため、並列タスクの実行順に依存せず、同一seedで人物ごとの活動系列を再現できる。
- 入力agent CSVをファイル名順に処理し、行政界候補を行政コード、メッシュ候補をメッシュID、学校候補を施設ID順に固定する。
- 同一baselineを2回実行し、207 CSV・31,788,077 bytesがバイト単位で完全一致することを確認した。
- `CapacityScenario`が共通CSV読込後にメッシュindex 4を倍率変更し、必要なら仮想小売施設をメモリ上だけ追加する。
- 愛知県23が参照するMarkovパスを、S3の実配置`markov/v2/chukyo2011_*`へ合わせた。

## 10ケース

`config/pflow_capacity/`に以下を置いた。全ケースで県23、標本率50（2%）、seed 42を使う。

| ケース | メッシュ倍率 | 仮想施設capacity |
| --- | ---: | ---: |
| baseline | 1 | なし |
| facility_1x | 1 | 10,000 |
| facility_10x | 1 | 100,000 |
| facility_100x | 1 | 1,000,000 |
| mesh_2x | 2 | なし |
| mesh_5x | 5 | なし |
| mesh_10x | 10 | なし |
| combined_mesh_2x_facility_10x | 2 | 100,000 |
| combined_mesh_5x_facility_10x | 5 | 100,000 |
| combined_mesh_10x_facility_10x | 10 | 100,000 |

代表点は経度136.88397984、緯度35.16967402、行政コード23105で、3次メッシュ`52366700`に一致する。
25,284到着トリップをcapacityへ直接入力していない。感度曲線を確認後、全数実行候補を1ケース選ぶ。

## AWSデータ取得

取得元は`s3://pseudo-pflow/`である。

```bash
# 対象確認
scripts/download_pflow_inputs.sh --scope aichi-minimal --dry-run

# SSD未購入時: 共通入力と愛知agentだけ
scripts/download_pflow_inputs.sh --scope aichi-minimal
```

最小範囲は`processing/ver3.0/`から実行に必要な共通CSV、MNL 4ファイル、Chukyo Markov 8ファイル、
愛知の小中学校参照2ファイルを、`ver3.0/agent/23/`から69 CSVを取得する。`aws s3 sync`へ
`--case-conflict error`を付け、`--delete`は使わない。

SSD到着後は、APFS・ボリューム名`PFLOW_SSD`を前提に次を実行する。

```bash
scripts/migrate_pflow_to_ssd.sh --dry-run
scripts/migrate_pflow_to_ssd.sh
export PFLOW_HOME=/Volumes/PFLOW_SSD/PFLOW
scripts/download_pflow_inputs.sh --scope full-processing --dest "$PFLOW_HOME" --dry-run
scripts/download_pflow_inputs.sh --scope full-processing --dest "$PFLOW_HOME"
```

移行スクリプトはファイル数とKiB合計を照合し、内蔵側を削除しない。`aws s3 sync`は同じ配置の既存ファイルを
再取得せず残りを取得する。実験確認前に内蔵データは削除しない。

## 実験と集計

```bash
# 1ケースで確認
scripts/run_pflow_capacity_sweep.sh --scenario baseline

# 全10ケース
scripts/run_pflow_capacity_sweep.sh --all

# Pseudo-PFLOWを事前コンパイル済みの場合
scripts/run_pflow_capacity_sweep.sh --scenario mesh_2x --skip-compile

# 集計
python3 pflow_capacity_analysis.py \
  --input-root .local/pflow/output/capacity_experiment \
  --output-dir .local/pflow/output/capacity_experiment/summary
```

集計は買物目的100について、対象仮想施設、対象メッシュ、同一メッシュの他施設、対象行政界、
直前活動行政界別の流入、年齢・性別・就業、時間帯を数える。baselineとの差分もJSONへ出す。
全数実行は感度曲線を確認してから、`--sample-factor 1`で選択した1ケースだけ行う。

## 目標人数への1回校正

来訪需要の計算元は`../nagoya_caluclation/scenario_distributions.py`である。全商業型では
`nagoya_three_development_scenario_distributions.json`の`all_commercial / commercial / estimated_arrivals`
が25,284到着トリップ/日となり、橋渡しJSON
`config/development_scenarios/meitetsu_origin_distribution_commercial.json`へ保存されている。

`pflow_capacity_calibration.py`は10ケースの2%感度結果から、次の2段階を逆算する。

1. `logit(対象メッシュ到着 / 対象行政界到着)`を`log(メッシュ倍率)`で回帰する。
2. 施設選択を`施設capacity / (既存競合capacity + 施設capacity)`として競合capacityを推定する。

25,284件を2%標本の505.68件へ換算し、対象施設が対象メッシュ到着の70%を取る条件で、
メッシュ倍率15.681、施設capacity 9,750,000を得た。予測は505.7件、実際の単一実行は515件で、
全数換算25,750件、目標差+466件、絶対誤差率1.84%だった。3生成器のmotifは既存ケースと一致した。

```bash
scripts/run_pflow_target_arrivals.sh --dry-run --skip-compile
scripts/run_pflow_target_arrivals.sh --skip-compile
# SSD到着後の全数1回
PFLOW_HOME=/Volumes/PFLOW_SSD/PFLOW \
  scripts/run_pflow_target_arrivals.sh --sample-factor 1 --skip-compile
```

この推定は感度範囲外への外挿を含む。対象行政界の買物需要そのものはcapacityで増えないため、
対象行政界需要の安全上限を超える目標は事前に拒否し、行政界モデル変更または生成後補正へ切り替える。

## 勝谷さんへの確認が必要な点

- 既存環境に`processing/ver3.0`と愛知県`ver3.0/agent/23`があるか。
- 今回のコード差分とscenario JSONだけを渡して実行できるか。
- 2%標本と全数の所要時間、必要メモリ、出力容量。
- 結果CSVまたは集計JSONの受け渡し場所と命名規則。
- 既存環境で使うPseudo-PFLOW-v3のcommit／branchとJava・Maven版。

詳細な送信文は[勝谷さん確認文案](slack_to_katsuya.md)を参照する。

## 2%感度実験の実測結果

2026年9月29日に全10ケースを完走した。各ケースはactivity CSV 207本で、買物目的100の結果は次の通り。

| ケース | 全買物 | 対象施設 | 対象メッシュ | 同メッシュ他施設 | 対象行政界 |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 38,487 | 0 | 262 | 262 | 896 |
| facility_1x | 38,487 | 0 | 262 | 262 | 896 |
| facility_10x | 38,487 | 6 | 262 | 256 | 896 |
| facility_100x | 38,487 | 55 | 262 | 207 | 896 |
| mesh_2x | 38,487 | 0 | 377 | 377 | 896 |
| mesh_5x | 38,487 | 0 | 556 | 556 | 896 |
| mesh_10x | 38,487 | 0 | 661 | 661 | 896 |
| combined_mesh_2x_facility_10x | 38,487 | 9 | 377 | 368 | 896 |
| combined_mesh_5x_facility_10x | 38,487 | 13 | 556 | 543 | 896 |
| combined_mesh_10x_facility_10x | 38,487 | 14 | 661 | 647 | 896 |

メッシュ倍率では対象メッシュ到着が262→377→556→661と単調に増えた。施設単独ではcapacity 10,000、
100,000、1,000,000に対して対象施設0、6、55件となり、対象メッシュ総数262件は変わらず、
同一メッシュ内の取り分だけを動かした。全ケースで全買物38,487件・対象行政界896件・3生成器の
motifが一致し、メッシュ／施設変更が活動系列や行政界MNLへ伝播しないコード確認と整合する。

`combined_mesh_10x_facility_10x`は対象メッシュ+399、対象施設+14で両層の効果を同時に確認できるため、
全数実行の暫定候補とする。ただし、対象施設14件の単純50倍は700件で、外部目標25,284件とは大きく
異なる。したがって、このケースを「目標人数への校正済み」とは扱わず、勝谷さんへの計算負荷確認と
本人確認後にSSD上で全数実行する。

数値は`.local/pflow/output/capacity_experiment/summary/capacity_sweep_summary.csv`とJSON、感度曲線は
`capacity_sensitivity.png`／SVGに保存した。ローカルデータ全体は再現性検証用出力を含め3.7 GiB、実行後の空きは39 GiBで、
安全基準内だった。入力読込では`city_retail.csv`のメッシュ化できない564行をスキップしたため、
データ品質上の既知事項として残す。

## 検証結果

- プロジェクトのPythonテスト: 59件成功。
- Pseudo-PFLOW main compile: 成功。
- 同一baseline再実行: 207 CSV・31,788,077 bytesが完全一致。
- 愛知県2%実データ: 再現性修正後の10ケース×3生成器が終了コード0で完走。
- Pseudo-PFLOWの`mvn test`: testCompileで既存truckテスト5件が失敗。`DestinationSelectorTest`の削除済みAPI参照2件、`ZoneLoadResultTest`のコンストラクタ不一致2件、`FleetFactoryTest`のコンストラクタ不一致1件で、今回の活動生成変更とは別系統である。
