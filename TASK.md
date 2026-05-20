# TASK

## 未対応タスク

### BUG: 試合境界スキャンの精度問題（2件）

**BUG-1: 1フレーム孤立クラスタによる偽試合検出**
- 再現: エリア2試合動画 (`area5m_knockout_normal_2-match.mp4`)、混合3試合動画 (`5m_3m_5m_3-match.mp4`)
- 症状: 1フレームだけ他と離れたmatch_startが計算され、偽の試合として検出される
- 例: ts=40sで`timer=250s`→match_start=-10s、他フレーム群はmatch_start=45s。30s超離れるため別クラスタに
- 対策案: `cluster_readings`で最小フレーム数フィルタ（2フレーム未満のクラスタを除外）

**BUG-2: ナワバリ試合のタイマー誤読による5min誤判定**
- 再現: ナワバリ2試合動画 (`nawabari_multi_2-match.mp4`)
- 症状: match_1のタイマーが204s, 173sなど180超で読み取られ、5minルールと誤判定される
- 原因: ナワバリのタイマー最大値は180sだが、Visionが画面上の別の数値（ポイント表示等）を誤読している可能性
- 対策案: タイマー読み取りプロンプトの改善、または180s超のタイマー値に対する補正ロジック

## 完了タスク

| # | タスク | PR |
|---|--------|-----|
| 1 | キャプチャ解像度のリサイズ廃止 | [analyzer#12](https://github.com/reisun/splatoon-battle-analyzer/pull/12) |
| 2 | Step1/Step2 の段階制度を廃止し、全体を5秒間隔で分析 | [analyzer#12](https://github.com/reisun/splatoon-battle-analyzer/pull/12) |
| 3 | 分析の並行実行 | [analyzer#12](https://github.com/reisun/splatoon-battle-analyzer/pull/12) |
| 4 | WebUI の簡易表示を廃止し、解析ファイルの一括ダウンロードに変更 | [pilot#6](https://github.com/reisun/splat-highlight-pilot/pull/6) |
| 5 | 解析内容の日本語化 | [analyzer#13](https://github.com/reisun/splatoon-battle-analyzer/pull/13) |
| 6 | 自チーム・相手チームの色判定の精度改善 | [analyzer#13](https://github.com/reisun/splatoon-battle-analyzer/pull/13) |
| 7 | 分析結果にチームカラー情報を追加 | [analyzer#13](https://github.com/reisun/splatoon-battle-analyzer/pull/13) |
| 8 | 分析結果に試合ポイント情報を追加 | [analyzer#13](https://github.com/reisun/splatoon-battle-analyzer/pull/13) |
| 9 | クリップ動画の自動クリーンアップ | [pilot#7](https://github.com/reisun/splat-highlight-pilot/pull/7) |
| 10 | 全フレーム分析データの返却 | [analyzer#18](https://github.com/reisun/splatoon-battle-analyzer/pull/18), [pilot#8](https://github.com/reisun/splat-highlight-pilot/pull/8) |
| 11 | ハイライト区間選出アルゴリズムの検証・修正 | [analyzer#17](https://github.com/reisun/splatoon-battle-analyzer/pull/17) |
| 12 | analyzer 内の clutch 要素の削除 | [analyzer#14](https://github.com/reisun/splatoon-battle-analyzer/pull/14) |
| 13 | score_gain をAI評価からプログラム計算に変更 | [analyzer#22](https://github.com/reisun/splatoon-battle-analyzer/pull/22) |
| 14 | score をプログラム計算に統一（対応不要: 元々AI非出力） | - |
| 15 | ゲームカウントの正規化（raw/normalized 分離） | [analyzer#23](https://github.com/reisun/splatoon-battle-analyzer/pull/23), [pilot#12](https://github.com/reisun/splat-highlight-pilot/pull/12) |
| 16 | score_gain の基準値を直近40秒平均に変更 | [analyzer#24](https://github.com/reisun/splatoon-battle-analyzer/pull/24) |
| 17 | 異常値検出を統計的アプローチに変更（2パスIQR+連続性チェック） | [analyzer#25](https://github.com/reisun/splatoon-battle-analyzer/pull/25), [analyzer#26](https://github.com/reisun/splatoon-battle-analyzer/pull/26) |
| 18 | 異常値検出を選択的中央値フィルタに改善（radius=3, threshold=40） | [analyzer#28](https://github.com/reisun/splatoon-battle-analyzer/pull/28) |
| 19 | 出現頻度ベースフィルタに置換（全データ統計で正常範囲を決定） | [analyzer#29](https://github.com/reisun/splatoon-battle-analyzer/pull/29) |
| 20 | score_gain を未来ベースに変更、スコアリング係数を設定ファイル化 | [analyzer#30](https://github.com/reisun/splatoon-battle-analyzer/pull/30) |
| 21 | マルチマッチ対応（マッチスキャンAPI） | [analyzer#61](https://github.com/reisun/splatoon-battle-analyzer/pull/61) |
| 22 | マルチマッチ対応（パイプライン改修 + Web UI改修） | [pilot#30](https://github.com/reisun/splat-highlight-pilot/pull/30) |
| 23 | 試合終了時刻のノックアウト対応 | [pilot#32](https://github.com/reisun/splat-highlight-pilot/pull/32) |
| 24 | zip に matches.json を追加 | [pilot#34](https://github.com/reisun/splat-highlight-pilot/pull/34) |
| 25 | 上半分クロップ縮小（上部30%に） | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64) |
| 26 | 下半分クロップ縮小（下部30%に） | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64) |
| 27 | 2パス方式（粗スキャン→密スキャン） | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64) |
| 28 | PAV正規化の改善（急降下追従） | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64) |
| 29 | ナワバリ時の上半分スキップ | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64), [pilot#38](https://github.com/reisun/splat-highlight-pilot/pull/38) |
| 30 | ヤグラ/ホコ向けプロンプト改善（カウントバー進行方向） | [analyzer#64](https://github.com/reisun/splatoon-battle-analyzer/pull/64) |
| 31 | フレーム1/2リサイズによるトークン削減 | [analyzer#65](https://github.com/reisun/splatoon-battle-analyzer/pull/65) |
| 32 | Phase A/B専用フロー（上半分15秒→下半分5秒gain>1区間） | [analyzer#66](https://github.com/reisun/splatoon-battle-analyzer/pull/66), [pilot#40](https://github.com/reisun/splat-highlight-pilot/pull/40) |
| 33 | score関連フィールドの全float化 | [analyzer#67](https://github.com/reisun/splatoon-battle-analyzer/pull/67), [pilot#42](https://github.com/reisun/splat-highlight-pilot/pull/42) |
| 34 | ヤグラ・ホコ向けレール検出によるゲームカウント入れ替え | [analyzer#67](https://github.com/reisun/splatoon-battle-analyzer/pull/67), [pilot#42](https://github.com/reisun/splat-highlight-pilot/pull/42) |
