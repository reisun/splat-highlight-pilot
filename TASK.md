# TASK

## 未対応タスク

（なし）

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
