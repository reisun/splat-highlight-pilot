# アーキテクチャ設計書

## 概要

splat-highlight-pilot は、スプラトゥーン試合動画のハイライト自動切り出しを行うオーケストレーターサービス。
既存の analyzer（ハイライト検出）と clipper（動画クリッピング）を連携させる中間層として機能する。

## システム構成

```
[Web UI]                   <- React + TypeScript + Vite (GitHub Pages)
   |
   |  HTTPS (CORS)
   v
[orchestrator :8030]       <- splat-highlight-pilot (FastAPI)
   |
   |  HTTP (Docker network / host.docker.internal 経由)
   +----------------+
   v                v
[analyzer :8020]   [clipper :8010]
(splatoon-         (movie-edit-pilot)
 battle-analyzer)
```

## サービス一覧

| サービス | ポート | リポジトリ | 役割 |
|---|---|---|---|
| orchestrator | 8030 (外部) / 8000 (内部) | splat-highlight-pilot | パイプライン制御 |
| analyzer | 8020 (外部) / 8000 (内部) | splatoon-battle-analyzer | ハイライト区間検出 |
| clipper | 8010 (外部) / 8000 (内部) | movie-edit-pilot | 動画クリッピング |
| Web UI | GitHub Pages | splat-highlight-pilot/web | ユーザーインターフェース |

## ネットワーク

- 各サービスは独立した docker-compose で起動し、`host.docker.internal` 経由で通信
- Web UI は外部から orchestrator のポート 8030 にアクセス

## 共有ボリューム

```
shared-data (Docker volume)
+-- uploads/                     <- アップロード動画（処理後削除）
|   +-- {job_id}_{filename}      <- 一時ファイル
+-- results/                     <- クリップ動画 + 分析JSON
    +-- {job_id}.mp4             <- ハイライトクリップ動画
    +-- {job_id}_analysis.json   <- 分析データ（highlights, frames, scan_summary）
```

### ボリュームマウント設定

各サービスの docker-compose.yml で同一ボリュームをマウントする必要がある:

```yaml
# orchestrator (splat-highlight-pilot)
volumes:
  - shared-data:/shared-data

# analyzer (splatoon-battle-analyzer)
volumes:
  - shared-data:/shared-data

# clipper (movie-edit-pilot)
volumes:
  - shared-data:/shared-data
```

ボリュームは external として定義し、事前に作成しておく:

```bash
docker volume create shared-data
```

## データフロー

```
1.  [Web UI] ---(WebSocket /ws/upload)---> [orchestrator]
2.  [Web UI] ---(バイナリチャンク送信)---> [orchestrator]
3.  [orchestrator] ---(write file)---> /shared-data/uploads/{job_id}_{filename}
4.  [orchestrator] ---(job_created + close)---> [Web UI]
5.  [Web UI] ---(GET /jobs/{job_id} ポーリング)---> [orchestrator]
6.  [orchestrator] ---(POST /analyze/highlights/jobs)---> [analyzer]  (非同期ジョブ作成)
7.  [orchestrator] ---(GET /analyze/highlights/jobs/{id} ポーリング)---> [analyzer]
8.  [analyzer] ---(完了: highlights + frames)---> [orchestrator]
9.  [orchestrator] ---(write)---> /shared-data/results/{job_id}_analysis.json
10. [orchestrator] ---(POST /clip/jobs)---> [clipper]  (非同期ジョブ作成)
11. [orchestrator] ---(GET /clip/jobs/{id} ポーリング)---> [clipper]
12. [clipper] ---(完了: result_path)---> [orchestrator]
13. [orchestrator] ---(move)---> /shared-data/results/{job_id}.mp4
14. [orchestrator] ---(delete)---> /shared-data/uploads/{job_id}_{filename}
15. [Web UI] ---(GET /download/{job_id})---> [orchestrator]  (mp4)
16. [Web UI] ---(GET /download/{job_id}/analysis)---> [orchestrator]  (JSON)
```

### パイプライン詳細

バックグラウンドパイプライン（`_run_pipeline`）の処理順序:

1. フェーズを `analyzing` に設定
2. analyzer の非同期ジョブAPI（`POST /analyze/highlights/jobs`）を呼び出し
3. analyzer ジョブの完了をポーリングで待機（`GET /analyze/highlights/jobs/{id}`、3秒間隔）
4. 進捗をジョブストアに反映（クライアントは `/jobs/{job_id}` で取得可能）
5. 分析結果（highlights, frames, scan_summary）を JSON ファイルとして保存
6. フェーズを `clipping` に設定
7. clipper の非同期ジョブAPI（`POST /clip/jobs`）を呼び出し
8. clipper ジョブの完了をポーリングで待機（`GET /clip/jobs/{id}`、3秒間隔）
9. 出力ファイルを `{job_id}.mp4` にリネーム移動
10. フェーズを `completed` に設定、`download_url` を設定
11. アップロード一時ファイルを削除

## ジョブストア

`app/job_store.py` にインメモリのジョブストア（`OrchestratorJobStore`）を実装。

- スレッドセーフ（`threading.Lock` で排他制御）
- ジョブは UUID で管理
- フェーズ遷移: `uploading` -> `analyzing` -> `clipping` -> `completed` / `failed`
- analyzer の進捗（stage, frames_done 等）をリアルタイムに更新

## 自動クリーンアップ

- lifespan イベントで定期クリーンアップタスクを起動
- `CLEANUP_INTERVAL`（デフォルト 3600 秒 = 1時間）ごとに実行
- `CLEANUP_MAX_AGE`（デフォルト 3600 秒 = 1時間）より古い完了済みジョブを削除
- ジョブストアからの削除と、関連ファイル（mp4, analysis JSON）の物理削除を行う

## 環境変数

| 変数名 | デフォルト | 説明 |
|---|---|---|
| ANALYZER_URL | http://analyzer:8000 | analyzer サービスの URL |
| CLIPPER_URL | http://clipper:8000 | clipper サービスの URL |
| SHARED_DATA_DIR | /shared-data | 共有ボリュームのパス |
| HTTP_TIMEOUT | 300 | 外部サービスへのタイムアウト（秒） |
| API_HOST | 0.0.0.0 | API リッスンホスト |
| API_PORT | 8000 | API リッスンポート |
| CLEANUP_INTERVAL | 3600 | クリーンアップ実行間隔（秒） |
| CLEANUP_MAX_AGE | 3600 | ジョブの最大保持時間（秒） |

## 設計方針

### 非同期パイプライン

- WebSocket でアップロードを受け付け、即座に job_id を返す
- パイプラインはバックグラウンドタスクとして非同期実行
- クライアントは REST API でポーリングして進捗・結果を取得
- analyzer / clipper ともに非同期ジョブAPI を使用し、ポーリングで完了を待機

### 疎結合

- analyzer の API 仕様は開発中のため、レスポンスモデルで `extra = "allow"` を設定
- analyzer/clipper のエラーは RuntimeError としてラップし、ジョブを failed 状態に遷移
- 各外部サービスの URL は環境変数で設定可能

### エラーハンドリング

- パイプライン中の例外 -> ジョブを `failed` 状態にしてエラーメッセージを記録
- 外部サービス接続エラー -> RuntimeError として伝播
- ハイライト未検出 -> ジョブを `failed` 状態にして "No highlights detected" を記録
- ジョブ未存在 -> 404 Not Found

### 一時ファイル管理

- アップロード動画は `{job_id}_{filename}` で保存（衝突回避）
- パイプライン完了後（成功・失敗問わず）にアップロード一時ファイルを削除
- 結果ファイル（mp4, JSON）は自動クリーンアップで期限切れ後に削除
