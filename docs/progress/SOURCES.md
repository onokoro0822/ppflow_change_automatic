# 参照資料

最終更新: 2026-08-13 JST

## MTG記録

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

- [人流分析方針](../人流分析方針.md)
- [2026年8月7日MTG 今後の課題整理](20260807_MTG今後の課題.md)
- [施設種別別の来訪者数根拠調査 初版](20260807_施設種別別来訪者数根拠調査初版.md)
- [教授報告用：来訪需要と変更対象トリップの設計](20260807_教授報告用_来訪需要と変更対象トリップ設計.md)
- [施設別目標分布の作成方法と根拠](20260812_施設別目標分布の作成方法と根拠.md)
- [集計前に確定する施設別属性処理仕様表](20260813_施設別属性処理仕様表.md)
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
