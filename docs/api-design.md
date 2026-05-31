# API 設計書

## 概要

splat-highlight-pilot orchestrator の API 仕様。
REST エンドポイントと WebSocket エンドポイントで構成される。

## ベース URL

- 開発: `http://localhost:8030`
- Docker ネットワーク内: `http://orchestrator:8000`

## エンドポイント

### GET /health

自身および外部サービスの接続状態を返す。

#### レスポンス

```json
{
  "status": "ok",
  "updated_at": "2025-01-01T00:00:00Z",
  "services": [
    {"name": "analyzer", "status": "connected", "detail": null}
  ]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| status | string | 常に "ok"（自身が起動していれば） |
| services | array | 外部サービスの状態一覧 |
| services[].name | string | サービス名 |
| services[].status | string | "connected" or "disconnected" |
| services[].detail | string? | エラー時の詳細メッセージ |

#### ステータスコード

| コード | 説明 |
|---|---|
| 200 | 正常（外部サービスが停止していても 200） |

---

### WebSocket /ws/upload

動画アップロード専用の WebSocket エンドポイント。
アップロード完了後にバックグラウンドパイプラインを起動し、job_id を返して接続を閉じる。

#### プロトコル

1. クライアントが WebSocket 接続を開く
2. クライアントが開始メッセージを送信（テキストフレーム）:
   ```json
   {"type": "start", "filename": "video.mp4", "size": 123456789}
   ```
3. サーバーが接続を受け付ける
4. クライアントが動画データをバイナリフレームでチャンク送信
5. サーバーがチャンクごとに進捗を返す（テキストフレーム）:
   ```json
   {"type": "progress", "phase": "uploading", "percent": 42}
   ```
6. 全チャンク送信後、クライアントが完了メッセージを送信（テキストフレーム）:
   ```json
   {"type": "upload_complete"}
   ```
7. サーバーがバックグラウンドパイプラインを開始し、job_id を返す（テキストフレーム）:
   ```json
   {"type": "job_created", "job_id": "uuid-string"}
   ```
8. サーバーが WebSocket 接続を閉じる

#### 開始メッセージのフィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| type | string | Yes | 固定値 "start" |
| filename | string | No | ファイル名（デフォルト: "video.mp4"） |
| size | integer | No | ファイルサイズ（バイト）。進捗計算に使用 |
| options | object | No | AnalyzerOptions（下記参照） |

#### エラーメッセージ

```json
{"type": "error", "message": "エラー内容"}
```

---

### GET /jobs/{job_id}

ジョブの状態をポーリングで取得する。

#### パスパラメータ

| パラメータ | 型 | 説明 |
|---|---|---|
| job_id | string | WebSocket アップロード時に返された job_id |

#### レスポンス

```json
{
  "job_id": "uuid-string",
  "phase": "scanning",
  "analyzer_progress": {
    "stage": 1,
    "stage_total": 1,
    "frames_done": 30,
    "frames_total": 100
  },
  "match_progress": {
    "current_match": 2,
    "total_matches": 3
  },
  "download_url": null,
  "analysis_url": null,
  "error": null,
  "started_at": 1700000000.0
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| job_id | string | ジョブID |
| phase | string | ジョブのフェーズ（下記参照） |
| analyzer_progress | object? | analyzer の進捗（scanning/analyzing/clipping/completed 時） |
| analyzer_progress.stage | int | 現在のステージ番号 |
| analyzer_progress.stage_total | int | 全ステージ数 |
| analyzer_progress.frames_done | int | 処理済みフレーム数 |
| analyzer_progress.frames_total | int | 全フレーム数 |
| match_progress | object? | マッチ処理の進捗（マッチ検出後） |
| match_progress.current_match | int | 現在処理中のマッチ番号 |
| match_progress.total_matches | int | 検出されたマッチ総数 |
| download_url | string? | ダウンロードURL（completed 時のみ、zip または mp4） |
| analysis_url | string? | 分析データのダウンロードURL（completed 時のみ、後方互換） |
| error | string? | エラーメッセージ（failed 時のみ） |
| started_at | float? | ジョブ開始時刻（Unix timestamp） |

#### phase の値

| phase | 説明 |
|---|---|
| uploading | アップロード中 |
| scanning | マッチ境界スキャン中 |
| analyzing | ハイライト分析中（マッチごとに順次実行） |
| clipping | 動画クリッピング中（マッチごとに順次実行） |
| completed | 完了。download_url が設定される |
| failed | 失敗。error にメッセージが設定される |

#### ステータスコード

| コード | 説明 |
|---|---|
| 200 | 正常 |
| 404 | ジョブが存在しない |

---

### GET /download/{job_id}

ハイライト成果物をダウンロードする。zip を優先し、存在しなければ mp4 にフォールバック（後方互換）。

#### レスポンス（成功 - zip）

- Content-Type: `application/zip`
- Content-Disposition: `attachment; filename="{元ファイル名}_highlight.zip"`
- Body: zip バイナリ（下記 zip 内構造参照）

##### zip 内構造（デフォルト: 統合モード）

```
highlight.mp4              <- 全試合のハイライトを1本に結合
analysis/
  match.json               <- 試合境界情報
  analysis.json             <- 全試合の分析データ
```

##### zip 内構造（per_match=true: 試合別モード）

```
highlight-match-1.mp4      <- 試合1のハイライト
highlight-match-2.mp4      <- 試合2のハイライト
analysis/
  match.json               <- 試合境界情報
  analysis-match-1.json    <- 試合1の分析データ
  analysis-match-2.json    <- 試合2の分析データ
```

#### レスポンス（成功 - mp4、後方互換）

- Content-Type: `video/mp4`
- Content-Disposition: `attachment; filename="highlight.mp4"`
- Body: mp4 バイナリ

#### ステータスコード

| コード | 説明 |
|---|---|
| 200 | 成功 |
| 404 | ファイルが存在しない |

---

### GET /download/{job_id}/analysis

分析データ（JSON）をダウンロードする。

#### レスポンス（成功）

- Content-Type: `application/json`
- Content-Disposition: `attachment; filename="analysis.json"`

#### JSON 構造

```json
{
  "highlights": [
    {
      "start_seconds": 120.0,
      "end_seconds": 145.0,
      "peak_intensity": 85,
      "description": "..."
    }
  ],
  "frames": [
    {
      "timestamp_seconds": 5.0,
      "score": 0,
      "kills": 1,
      "assists": 1,
      "score_gain": 1,
      "special": 1,
      "is_dead": false,
      "description": "",
      "my_team_color": "",
      "enemy_team_color": "",
      "my_team_count": null,
      "enemy_team_count": null
    }
  ],
  "scan_summary": {}
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| highlights | array | 検出されたハイライト区間のリスト |
| frames | array | 全フレームの解析データ |
| scan_summary | object | スキャンのサマリー情報 |

#### ステータスコード

| コード | 説明 |
|---|---|
| 200 | 成功 |
| 404 | ファイルが存在しない |

---

## AnalyzerOptions

WebSocket アップロード時に `options` フィールドで渡せるオプション。

```json
{
  "start": null,
  "end": null,
  "interval": 5.0,
  "threshold": 100,
  "model": null,
  "concurrency": 4,
  "weights": null,
  "per_match": false
}
```

| フィールド | 型 | デフォルト | 説明 |
|---|---|---|---|
| start | float? | null | 分析開始位置（秒） |
| end | float? | null | 分析終了位置（秒） |
| interval | float | 5.0 | フレーム解析間隔（秒） |
| threshold | int | 100 | ハイライト検出閾値 |
| model | string? | null | 使用モデル |
| concurrency | int | 4 | 並列処理数 |
| weights | object? | null | スコア要素の重みづけ（下記参照） |
| per_match | bool | false | true: 試合ごとに個別ハイライト動画を作成。false: 全試合を1本に結合 |

### weights

スコア要素ごとの重み係数を指定する。省略時はanalyzerのデフォルト値が使用される。

```json
{
  "weights": {
    "score_count_gain": 0
  }
}
```

| キー | 型 | 説明 |
|---|---|---|
| score_kills | float | キルスコアの重み |
| score_count_gain | float | カウント獲得スコアの重み（0 で無効化 = 観戦用モード） |
| score_dead | float | デスペナルティの重み |
| enemy_score_gain | float | 敵カウント獲得スコアの重み |

#### 使用例: 観戦モード

自チーム視点でない動画（観戦・配信視聴など）では、カウント獲得情報が正しく取得できないため、
`score_count_gain: 0` を指定してキルのみでハイライトを判定する。

```json
{
  "type": "start",
  "filename": "stream.mp4",
  "size": 123456789,
  "options": {
    "weights": {
      "score_count_gain": 0
    }
  }
}
```

## タイムアウト

外部サービスへの HTTP リクエストは環境変数 `HTTP_TIMEOUT`（デフォルト 300 秒）のタイムアウトを設定。
