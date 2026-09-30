# 参照資料

最終更新: 2026-09-29 JST

## Pseudo-PFLOWキャパシティ実験・AWS

- [AWS CLI `s3 sync`リファレンス](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)
  - 差分同期、dry-run、case-conflictを確認し、削除を行わない部分取得・SSD移行後の継続取得に使用した。
- `s3://pseudo-pflow/processing/ver3.0/`（2026年9月29日にAWS CLIで確認）
  - 共通施設、経済センサス、MNL、Markov、学校参照の取得元。愛知最小入力は24ファイル。
- `s3://pseudo-pflow/ver3.0/agent/23/`（2026年9月29日にAWS CLIで確認）
  - 愛知県agent 69 CSV、433,970,226 bytesの取得元。
- 隣接`Pseudo-PFLOW-v3`の`ActGenerator.java`、`DataAccessor.java`、`Country.java`、`City.java`、`GMesh.java`（2026年9月29日確認）
  - 行政界MNL、買物メッシュ特徴量index 4、施設capacity比の3段階と、小売capacity一律10,000を特定した。
- [キャパシティ実験・AWS取得実装](../../mcd/mtg/20260924/capacity_experiment_aws_plan.md)
  - 10ケース、実行手順、2%実測結果、SSD移行、既知の入力品質問題を記録した。

## MTG記録

- [2026年9月24日 MyCityDevelopers定例 要約](../../mcd/mtg/20260924/summary.md)
- [2026年9月24日 MyCityDevelopers定例 文字起こし](<../../mcd/mtg/20260924/MyCityDevelopers定例 2026-09-24 17:58(GMT+9:00).txt>)
  - Pseudo-PFLOWの行政界選択、メッシュキャパシティ、ファシリティキャパシティの関係を先に確認し、対象メッシュ・施設の最小実験から始める方針を確認した。
  - 外部需要人数をキャパシティへ直接投入できない問題、生成結果の属性・出発地構成比を外部総数へ適用する案、既存実行環境への依頼、大容量データのCLI取得を確認した。
  - 需要からキャパシティへの変換式、各特徴量の連動、最終採用方式、10月15日の対象成果物は未確定である。

- [2026年8月7日 MTG文字起こし](/Users/yuuki/Desktop/syuron/mcd/mtg/20260807.txt)
  - 閉店後再配分の精密化より、名鉄跡地の計画A・B・C、用途別来訪者数、属性構成の根拠設定を優先する方針を確認した。
  - 経路生成後にMobmapで可視化すること、市外利用者を考慮して愛知県全体への拡大を検討すること、中間確認日程を確認した。
- [2026年7月22日 MTG要約](/Users/yuuki/Desktop/syuron/mcd/mtg/20260722/summary.md)
- [2026年7月22日 MTG文字起こし](/Users/yuuki/Desktop/syuron/mcd/mtg/20260722.txt)
- [2026年7月15日 DCBC2要約](</Users/yuuki/Desktop/syuron/DCBC/meeting/0715/DCBC2 2026-07-15 15_56(GMT+9_00)_要約.md>)
  - 二階層のショッピングセンター選択、MNL・MLP比較、感度分析計画を確認した。
- [2026年8月5日 DCBC文字起こし](/Users/yuuki/Desktop/syuron/DCBC/meeting/0805/20260805.txt)
  - ショッピングセンター選択モデルの完成状況、4施設でのシナリオ予測・検証計画、別研究の施設増減による活動系列生成を確認した。

## 人流・商業施設利用者分析

- Luyao Liu, Jue Ma, Yoshihide Sekimoto, *Uncovering Dynamic Consumer Behavior Patterns Across Commercial Spaces: A Mobility-Based Evidence of Core Consumer Groups in the 23 Wards of Tokyo*, 2026.
  - [保存した原PDF](/Users/yuuki/Desktop/syuron/関連論文/Liu_et_al_2026_Uncovering_Dynamic_Consumer_Behavior_Patterns.pdf)
  - [日本語要約](/Users/yuuki/Desktop/syuron/関連論文/日本語要約_Liu_et_al_2026.md)
  - [SSRN公開ページ](https://ssrn.com/abstract=6520042)
  - 年齢区分、FCPIの式と閾値1.2、30〜49歳の男女が全商業カテゴリに共通する中核群であることを、初回の年齢ターゲット選定に使用した。
- Luyao Liu, Jue Ma, Yoshihide Sekimoto, [Spatiotemporal Patterns of Consumer Behavior across Commercial Spaces in the 23 wards of Tokyo: Insights from Mobility Data](https://ssrn.com/abstract=5560670), 2025.
- Luyao Liu, Jue Ma, Yoshihide Sekimoto, Yuya Shibuya, "Identifying and analyzing consumer behaviors in shopping center from GPS data based on machine learning", CUPUM 2025.
- [JoRAS: モビリティデジタルツインの共通シミュレーションパッケージの設計と開発](https://joras.csis.u-tokyo.ac.jp/project/?id=1336)

## 施設種別別の来訪需要

- [国土交通省 大規模開発地区関連交通計画マニュアル（2014年改訂版）](https://www.mlit.go.jp/toshi/city_plan/content/001480895.pdf)
  - 事務所・商業施設の延床面積当たり発生集中原単位、平日・休日、駅距離等の補正、複合用途の内々交通を確認した。
  - ホテル・イベント施設は室数・席数と類似事例から設定する方針を確認した。
  - 商業施設の中京都市圏中心部は名古屋市中区と定義されるため、中村区の名鉄名古屋駅跡地には区分bを適用した。
- [名古屋鉄道「名古屋駅地区再開発計画・中長期経営戦略説明会資料」（2025年6月5日）](https://www.meitetsu.co.jp/ir/reference/results_briefing/__icsFiles/afieldfile/2025/06/06/keieisenryaku_setsumeikai250605.pdf)
  - 見直し前計画の敷地約32,700㎡、総延床約520,000㎡、商業約95,000㎡、オフィス計約200,000㎡、ホテル約27,000㎡を確認した。
  - 2025年12月以降に計画の再検証・見直しへ入ったため、現時点の確定規模ではなくシナリオ妥当性の比較基準として使用する。
  - オフィスを再開発の収益の柱とし、駅直結のSクラスオフィスへ国内外の企業を誘致する方針と、ホテル約27,000㎡・約150室の計画を確認した。
  - 商業95,000㎡、オフィス200,000㎡、ホテル27,000㎡は複数事業者・南北街区を含む再開発全体の値であり、旧・名鉄百貨店本店跡地単独の値ではない。
- [名古屋鉄道 有価証券報告書（第151期）](https://www.meitetsu.co.jp/ir/reference/securities/__icsFiles/afieldfile/2021/06/10/yukashoken151.pdf)
  - 旧・名鉄百貨店本店の売場面積54,374㎡を確認した。
  - 名鉄ビルは土地4,533㎡・賃貸面積47,564㎡、名鉄バスターミナルビルは土地12,574㎡・賃貸面積80,389㎡で、後者には名鉄百貨店のほか名鉄グランドホテル等が入ることを確認した。
- [日本政策投資銀行・価値総合研究所「リニア中央新幹線の開通と名駅再開発が名古屋エリア及び東海圏に与える影響の展望」（2025年9月30日）](https://www.dbj.jp/upload/investigate/docs/fb8d7f68a2fb26bbd3d80d97b309796e.pdf)
  - 名駅地区の業務中心性が他地区を上回る状況が続く見込みであること、名駅・栄は勤務者と来街者が拮抗して業務・商業の双方が強いことを確認した。
  - 名駅の大型小売店面積は名古屋市全体の15.8%、栄は21.9%であり、来街者数と店舗面積、勤務者数とオフィス稼働面積に正の関係があるとの分析を確認した。
- [CBRE「名古屋―市場動向と賃料相場 2026年3月期」](https://www.cbre.co.jp/properties/column/office/market/market_trend-3-2026-index/market_trend-3-2026-nagoya)
  - 2026年第1四半期末の名古屋オールグレード空室率2.2%、名駅1.4%と、低い空室率および賃料上昇傾向を確認した。
- [経済産業省 大規模小売店舗立地法](https://www.meti.go.jp/policy/economy/distribution/daikibokouritenporittiho.html)
- [群馬県掲載の大規模小売店舗立地法指針](https://www.pref.gunma.jp/page/10092.html)
  - 小売店舗面積から日来客数を直接算定する原単位と式を確認した。
- [観光庁 宿泊旅行統計調査 2025年年間値（確定値）](https://www.mlit.go.jp/kankocho/content/002010555.pdf)
  - 愛知県の施設タイプ別客室稼働率をホテル宿泊需要の基準候補として確認した。
- [観光庁 2026年宿泊旅行統計調査記入要領](https://www.mlit.go.jp/kankocho/content/001858808.pdf)
  - 延べ人数、実人数、平均連泊数、利用客室数の定義を確認し、ホテルの人泊と新規チェックイン人数を分ける根拠にした。
- [観光庁 旅行・観光消費動向調査 2025年年間集計表](https://www.mlit.go.jp/kankocho/content/001998225.xlsx)
  - 第11表の国内宿泊旅行・出張業務の延べ旅行者数を再集計し、30〜59歳67.8%、男78.8%・女21.2%を確認した。
- [国土交通省 令和3年度都市公園利用実態調査](https://www.mlit.go.jp/toshi/park/content/001519624.pdf)
  - 公園種別別の平日・休日1箇所当たり平均利用者数と、全国推計の誤差上の注意を確認した。
  - 地区公園の年齢・性別構成、公園全体の年齢構成、平日・休日差を公園案の属性条件に使用した。
  - 地区公園の平均在園時間が平日1.04時間、休日1.80時間であることと、在園時間の計算式を確認した。
- [e-Stat 令和6年度社会教育調査・博物館の入館者数](https://www.e-stat.go.jp/index.php/stat-search/files?cycle=0&cycle_facet=tclass1&layout=datalist&page=1&stat_infid=000040439521&tclass1=000001242203&tclass2=000001242205&tclass3=000001242211&tclass4val=0&toukei=00400004&tstat=000001017254)
- [e-Stat 令和6年度社会教育調査・劇場、音楽堂調査](https://www.e-stat.go.jp/stat-search/files?cycle=0&layout=datalist&month=0&tclass1=000001230866&tclass2=000001230868&tclass3=000001230880&toukei=00400004&tstat=000001017254&year=20241)
- [文部科学省 令和6年度社会教育調査票・劇場、音楽堂等](https://www.mext.go.jp/content/20240824-mxt_chousa01-000037636_08.pdf)
  - 300席以上の舞台芸術施設を対象とし、主催・共催事業の実施件数と入場者・参加者数を収集することを、座席型エンタメ施設の類似事例校正の根拠にした。
- 矢島隆・中野敦（1997）[大規模施設の発生集中交通特性に関する基礎的分析](https://doi.org/10.2208/jscej.1997.562_69)
- 北島由実ほか（2004）[大規模小売店舗における日来客数原単位の変動に関する研究](https://doi.org/10.2208/journalip.21.473)
- 新貝航平ほか（2020）[携帯電話基地局データからみた商業施設の来客数原単位に関する研究](https://doi.org/10.11361/journalcpij.55.1041)
- 福本大輔ほか（2023）[大規模開発に伴う交通影響評価制度に関する日韓比較](https://www.jstage.jst.go.jp/article/journalcpij/58/3/58_1071/_pdf)
  - 日本の商業原単位の元データ・改定時期の古さと、その他用途では類似施設調査が必要であることを確認した。
- 青木陽二・布施六郎・青木宏一郎（1983）[公園緑地の種類と周辺条件による誘致率の変化に関する研究](https://doi.org/10.5632/jila1934.47.112)
- 仙田満ほか（1999）[歴史博物館における年間入館者数の経年変化に関する研究](https://doi.org/10.3130/aija.64.139_1)
  - 開館10年後の年間入館者数が開館1〜3年平均の約58%となる経年変化を確認した。

## 擬似人流

- [Pseudo-PFLOW: Development of nationwide synthetic open dataset for people movement based on limited travel survey and open statistical data](https://arxiv.org/abs/2205.00657)
- [Pseudo-PFLOW仕様書 ver.2.01](https://pflowwp.sekilab.global/wp-content/uploads/Pseudo-PFLOW-Specification-ver2.01.pdf)
  - 24時間データであること、時刻が0時からの秒数であること、個人レベルの精度上の注意を品質基準の根拠として使用した。

## 施設別の行動目標分布

- [chukyo_pt_2022_destination_facility_purpose_time.csv](/Users/yuuki/Desktop/university/syuron/中京PTデータ/chukyo_pt_2022_destination_facility_purpose_time.csv)
  - 第6回中京都市圏PT調査の、到着中ゾーン×到着施設×目的細分類×移動終了時の拡大トリップ集計表。
  - 2026年9月9日に中ゾーン5の商業・オフィス・ホテル別到着時間分布へ使用した。
  - PTの「移動終了時」を到着時間帯として使えるが、滞在時間・退出時刻・平休日区分は含まない。

- [`chukyo_pt_2022_od_purpose_mode_raw.csv`](/Users/yuuki/Desktop/university/syuron/中京PTデータ/chukyo_pt_2022_od_purpose_mode_raw.csv)
  - 第6回中京都市圏PT調査の、出発中ゾーン×到着中ゾーン×目的細分類×交通手段の拡大トリップ集計表。
  - 2026年9月9日に到着中ゾーン5の商業・オフィス・ホテル別出発地代理分布へ使用した。
  - 到着施設列を含まないため、施設用途別目的集合による代理集計であり、厳密な施設別クロス表ではない。
- [pt_system_code_table.xlsx](/Users/yuuki/Desktop/university/syuron/中京PTデータ/中ゾーンコード表/pt_system_code_table.xlsx)
  - 中京PT中ゾーンコードの住所対応と、第6回基本ゾーンWKTを含む公式コード表。
  - 名鉄跡地代表点を中ゾーン5・基本ゾーン510と確認し、Pseudo-PFLOW出発座標のゾーン判定に使用した。
  - 499 WKTのうち255セルはExcel上限32,767文字で途中切れしているため、完全GIS入手まで該当点を分類不能として扱う。
- [中京PT用途別出発地分布 初版](20260909_中京PT用途別出発地分布初版.md)
  - 到着中ゾーン5の用途別出発中ゾーン代理分布を記録する。
- [名鉄跡地への出発地分布反映とOD変更 初版](20260909_名鉄跡地OD分布反映初版.md)
  - 全商業型25,284到着トリップの候補選択、OD差分、検証値、教授説明用成果物を記録する。
- [中京PT施設用途別到着時間分布 初版](20260909_中京PT施設用途別到着時間分布初版.md)
  - 到着施設と目的を同時に限定した時間帯分布、全商業型への整数按分、図表と限界を記録する。

- [`chukyo_pt_2022_age_gender_distribution_reference.json`](/Users/yuuki/Desktop/university/syuron/nagoya_caluclation/chukyo_pt_2022_age_gender_distribution_reference.json)
  - 第6回中京都市圏PT調査から作成した、商業施設・オフィス・ホテル別の年齢9階級×性別3区分の到着トリップ構成比。
  - 名古屋施設交通量計算Webアプリで、推定到着人トリップの属性配分とグラフ・CSV出力に使用する。
- [`nagoya_three_development_scenarios.json`](/Users/yuuki/Desktop/university/syuron/nagoya_caluclation/nagoya_three_development_scenarios.json)
  - 商業中心型・オフィス中心型・ホテル中心型と、全商業型・全オフィス型・全ホテル型の用途構成比、参考施設、54,374㎡換算面積、暫定共通計算条件を保存する。ファイル名は既存参照との互換性のため`three`を維持する。
- [`nagoya_three_development_scenario_distributions.json`](/Users/yuuki/Desktop/university/syuron/nagoya_caluclation/nagoya_three_development_scenario_distributions.json)
  - 6案の人TE、推定到着人トリップ、施設別年齢×性別分布、シナリオ別PNG・SVGの保存先を再生成可能な形で保存する。2026年8月26日に現行割合で再生成済みである。
- [`output/scenario_charts/`](/Users/yuuki/Desktop/university/syuron/nagoya_caluclation/output/scenario_charts/)
  - 6案それぞれの用途別推定到着量と年齢×性別構成を、一図ごとのPNG・SVGとして保存する。年齢図は構成比、年齢合計値、性別内訳表を併記する。
- [`output/history/`](/Users/yuuki/Desktop/university/syuron/nagoya_caluclation/output/history/)
  - シナリオJSONまたは年齢性別参照JSONの内容が変わった実行について、入力、結果JSON、比較図、個別図、入力ハッシュ付きマニフェストを日時別に保存する。

- [第6回中京都市圏パーソントリップ調査データ提供](https://www.cbr.mlit.go.jp/kikaku/chukyo-pt/offer/index.html)
  - 愛知・岐阜南部・三重北勢の目的、OD、時刻、代表交通手段、トリップチェーンを、施設別分布の地域基準に使う。
  - 2022年の指定平日1日、中京都市圏居住者対象、圏外居住者を含まないこと、コロナ影響と拡大誤差に注意する。
- [第6回中京都市圏PT調査結果・本編](https://www.cbr.mlit.go.jp/kikaku/chukyo-pt/persontrip/pdf/no06_honpen.pdf)
  - 目的、代表交通手段、駅端末手段、トリップチェーンの定義を確認した。
- [国土交通省・パーソントリップ調査票記入方法](https://ptplatform.mlit.go.jp/assets/pdf/PTSample_FillingInstructions.pdf)
  - 複数活動を同じ場所で行った場合は、最も主要な目的を一つ回答する仕様を確認した。
- [国土交通省・全国都市交通特性調査](https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000033.html)
  - 平日・休日の目的別トリップ、交通手段分担を補正・比較する全国基準として使用する。
- [国土交通省・第12回大都市交通センサス](https://www.mlit.go.jp/sogoseisaku/transport/sosei_transport_tk_000035.html)
  - 中京圏の目的別乗降時刻、駅間移動、端末交通手段を駅型施設の補助資料にする。
- [国土交通省・令和6年度活動調査試行調査](https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000212.html)
  - 移動だけでなく活動に着目した調査設計が進められていることを、活動系列モデルの政策的背景として使用する。
- David L. Huff (1964), [Defining and Estimating a Trading Area](https://doi.org/10.1177/002224296402800307)
  - 商圏を施設魅力度と距離抵抗による確率として扱う根拠にする。距離指数は観測ODで校正する。
- Bowman and Ben-Akiva (2001), [Activity-based disaggregate travel demand model system with activity schedules](https://doi.org/10.1016/S0965-8564(99)00043-9)
  - 活動パターンを上位に置き、時間、目的地、交通手段を条件付きで生成する設計根拠にする。
- Deville, Särndal and Sautory (1993), [Generalized Raking Procedures in Survey Sampling](https://doi.org/10.1080/01621459.1993.10476369)
  - 外部の周辺合計へ候補トリップの重みを合わせる方法の根拠にする。
- Hainmueller (2012), [Entropy Balancing for Causal Effects](https://doi.org/10.1093/pan/mpr025)
  - 複数の既知モーメントへ重みを合わせる代替手法として参照する。本研究では因果推定には使用しない。

## プロジェクト内資料

- [2026年8月26日進捗報告・日本語17枚版](../../output/presentations/20260826/jp_26_0826_進捗報告.pptx)
  - 2026年9月8日に、現在の最新進捗を表す資料として本人確認した。
  - 用途別発生集中原単位、施設別年齢×性別分布、6開発シナリオ、今後の擬似人流変更モデルを収録する。
- [2026年8月26日成果報告・新シナリオ反映版PPTX](../../output/presentations/20260826/26_0826_進捗報告_新シナリオ反映版.pptx)
- [2026年8月26日成果報告・シナリオ割合更新版PPTX](../../output/presentations/20260826/26_0826_進捗報告_シナリオ割合更新版.pptx)
- [2026年8月26日成果報告・0820追記版PPTX（互換性再構築版・19枚）](../../output/presentations/20260826_進捗報告_0820追記版.pptx)
- [2026年8月26日成果報告・日本語版PPTX](../../output/presentations/20260826_進捗報告_日本語版.pptx)
- [2026年8月26日成果報告・英語版PPTX](../../output/presentations/20260826_progress_report_english.pptx)
- [英語版PPTXの参照テンプレート](/Users/yuuki/Desktop/university/syuron/mcd/2Q/PP/26_0820_進捗報告.pptx)
- [人流分析方針](../人流分析方針.md)
- [2026年8月7日MTG 今後の課題整理](20260807_MTG今後の課題.md)
- [施設種別別の来訪者数根拠調査 初版](20260807_施設種別別来訪者数根拠調査初版.md)
- [教授報告用：来訪需要と変更対象トリップの設計](20260807_教授報告用_来訪需要と変更対象トリップ設計.md)
- [施設別目標分布の作成方法と根拠](20260812_施設別目標分布の作成方法と根拠.md)
- [集計前に確定する施設別属性処理仕様表](20260813_施設別属性処理仕様表.md)
- [商業・宿泊・エンタメ施設の来訪者数予測方法](20260813_商業宿泊エンタメ来訪者数予測.md)
- [トリップチェーン品質基準](20260802_トリップチェーン品質基準.md)
- [参照施設と潜在来訪者推定 初版（旧実験）](20260802_参照施設と潜在来訪者初版.md)
- [商業目的地置換モデルと感度分析 初版（旧・新施設誘引実験）](20260805_商業目的地置換と感度分析初版.md)
- [README](../../README.md)
- [Liu研究とDCBCログの確認](20260806_Liu研究とDCBCログ確認.md)

## 旧実験で使用した暫定参照施設（現行研究では不使用）

以下は2026年8月2日〜5日の新施設誘引実験を再現するための資料である。ミッドランドスクエアは現在の研究対象・参照施設ではない。

- [ミッドランドスクエア 営業時間](https://www.midland-square.com/open/)
- [ミッドランドスクエア アクセス](https://mlh.midland-square.com/access/)
- [ミッドランドスクエア 店舗カテゴリ](https://www.midland-square.com/category/)
- [OpenStreetMap way 199919504](https://www.openstreetmap.org/way/199919504)
  - 建物ポリゴンを2026年8月2日に取得し、OD目的地抽出の分析境界として使用した。

## 旧・新施設誘引実験の資料（現行研究では不使用）

- [旧・名鉄百貨店本店 アクセス](https://www.e-meitetsu.com/mds/access/)
  - 旧本店の住所を暫定開発代表点の所在地確認に使用した。
- [2026年3月期決算説明会資料：名古屋駅地区再開発計画の見直し状況](https://www.meitetsu.co.jp/ir/reference/results_briefing/__icsFiles/afieldfile/2026/05/13/kaiken20260515_1.pdf)
  - 2026年5月時点で計画見直し中であることを確認し、シナリオを確定計画ではなく仮想デモとして扱う根拠にした。

## 名鉄百貨店本店の営業終了と可視化

- [名鉄グループ 第162回定時株主総会資料](https://www.meitetsu.co.jp/ir/stock_info/meeting/__icsFiles/afieldfile/2026/05/28/162shosyu_1.pdf)
  - 名鉄百貨店本店が2026年2月28日に店舗営業を終了したことの根拠として使用した。
- [名鉄百貨店本店 営業終了後のお知らせ](https://mds.e-meitetsu.com/event/eigyousyuryogonoosihrase.html)
- [名古屋鉄道 有価証券報告書（第151期）](https://www.meitetsu.co.jp/ir/reference/securities/__icsFiles/afieldfile/2021/06/10/yukashoken151.pdf)
  - 名鉄百貨店本店の売場面積54,374平方メートルを、施設規模と暫定抽出人数を比較する参考に使用した。
- [Mobmap公式ドキュメント](https://mobmap.locationmind.com/doc/)
  - CSVにID・時刻・経度・緯度が必須で、追加属性を使用できることを確認した。
- [Mobmap Tour・CSV規則](https://mobmap.locationmind.com/mm/tour.html)
  - 行を時刻順に並べ、整数IDを推奨する入力規則の根拠として使用した。

## 再配分先上位地点の施設・営業状態確認

- [ジェイアール名古屋タカシマヤ公式サイト](https://www.jr-takashimaya.co.jp/)
  - 名古屋駅側の上位座標をJRセントラルタワーズ・タカシマヤ周辺と暫定判定するために使用した。
- [JRセントラルタワーズ・JRゲートタワー企業情報](https://www.towers.jp/company/)
  - 名駅一丁目1番4号の商業施設群を確認した。
- [近鉄パッセ営業終了の案内](https://www.passe.co.jp/contact_form)
  - 近鉄パッセが2026年2月28日に営業終了しており、現在の代替候補から除外すべきことを確認した。
- [名古屋栄三越 アクセス・営業情報](https://www.mitsukoshi.mistore.jp/nagoya/access.html)
  - 栄三丁目5番1号で現在営業する商業施設として、栄側上位座標との対応確認に使用した。
- [アスナル金山 アクセス](https://www.asunal.jp/access/)
  - 金山駅北口の営業中商業施設として、金山側座標クラスタとの対応候補に使用した。
