# アーキテクチャ設計書

## 概要

splat-highlight-pilot は、スプラトゥーン試合動画のハイライト自動切り出しを行うオーケストレーターサービス。
analyzer（ハイライト検出）と内蔵 FFmpeg クリッピングを組み合わせて動作する。

## システム構成

```
[Web UI]                   <- React + TypeScript + Vite (GitHub Pages)
   |
   |  HTTPS (CORS)
   v
[orchestrator :8030]       <- splat-highlight-pilot (FastAPI + FFmpeg)
   |
   |  HTTP (Docker network / host.docker.internal 経由)
   v
[analyzer :8020]
(splatoon-battle-analyzer)
```

## サービス一覧

| サービス | ポート | リポジトリ | 役割 |
|---|---|---|---|
| orchestrator | 8030 (外部) / 8000 (内部) | splat-highlight-pilot | パイプライン制御 + 動画クリッピング |
| analyzer | 8020 (外部) / 8000 (内部) | splatoon-battle-analyzer | ハイライト区間検出 |
| Web UI | GitHub Pages | splat-highlight-pilot/web | ユーザーインターフェース |

## ネットワーク

- 各サービスは独立した docker-compose で起動し、`host.docker.internal` 経由で通信
- Web UI は外部から orchestrator のポート 8030 にアクセス

## 共有ボリューム

```
shared-data (Docker volume)
+-- uploads/                     <- アップロード動画（処理後削除）
|   +-- {job_id}_{filename}      <- 一時ファイル
+-- results/                     <- 成果物
    +-- {job_id}.zip             <- マルチマッチハイライト zip（v0.4.0〜）
    +-- {job_id}.mp4             <- 旧形式：単一ハイライトクリップ動画（後方互換）
    +-- {job_id}_analysis.json   <- 旧形式：分析データ（後方互換）
```

zip 内構造:

```
match_1/
  highlight.mp4
  analysis.json
match_2/
  highlight.mp4
  analysis.json
...
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
6.  [orchestrator] ---(POST /analyze/matches/scan/jobs)---> [analyzer]  (マッチスキャン)
7.  [orchestrator] ---(GET /analyze/matches/scan/jobs/{id} ポーリング)---> [analyzer]
8.  [analyzer] ---(完了: マッチ境界リスト)---> [orchestrator]
9.  [orchestrator] ---(マッチごとに POST /analyze/highlights/jobs)---> [analyzer]  (ハイライト分析)
10. [orchestrator] ---(GET /analyze/highlights/jobs/{id} ポーリング)---> [analyzer]
11. [analyzer] ---(完了: highlights + frames)---> [orchestrator]
12. [orchestrator] ---(マッチごとに FFmpeg clip)---> /shared-data/results/{job_id}/match_N/
13. [orchestrator] ---(zip build)---> /shared-data/results/{job_id}.zip
14. [orchestrator] ---(delete)---> 一時ファイル（uploads, match dirs）
15. [Web UI] ---(GET /download/{job_id})---> [orchestrator]  (zip or mp4)
```

### パイプライン詳細

バックグラウンドパイプライン（`_run_pipeline`）の処理順序:

1. フェーズを `scanning` に設定
2. analyzer のマッチスキャンAPI（`POST /analyze/matches/scan/jobs`）を呼び出し
3. スキャンジョブの完了をポーリングで待機（`GET /analyze/matches/scan/jobs/{id}`、3秒間隔）
4. スキャン進捗（frames_done/frames_total）をジョブストアに反映
5. マッチが検出されない場合はジョブを `failed` にして終了
6. 検出されたマッチごとに以下を順次実行:
   a. フェーズを `analyzing` に設定、match_progress を更新（N/M）
   b. analyzer の非同期ジョブAPI（`POST /analyze/highlights/jobs`）を `start`/`end` 付きで呼び出し
   c. analyzer ジョブの完了をポーリングで待機
   d. フェーズを `clipping` に設定
   e. 内蔵 FFmpeg でハイライト区間をクリッピング・結合
   f. 分析結果（analysis.json）を一時ディレクトリに保存
7. 全マッチの成果物を zip にまとめる（match_1/, match_2/, ...）
8. フェーズを `completed` に設定、`download_url` を設定
9. 一時ファイル（アップロード、マッチディレクトリ）を削除

## ジョブストア

`app/job_store.py` にインメモリのジョブストア（`OrchestratorJobStore`）を実装。

- スレッドセーフ（`threading.Lock` で排他制御）
- ジョブは UUID で管理
- フェーズ遷移: `uploading` -> `scanning` -> `analyzing` -> `clipping` -> `completed` / `failed`
- マッチ進捗（current_match/total_matches）を追跡
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
- analyzer は非同期ジョブAPI を使用し、ポーリングで完了を待機
- クリッピングは内蔵 FFmpeg でスレッドプールにて実行

### 疎結合

- analyzer の API 仕様は開発中のため、レスポンスモデルで `extra = "allow"` を設定
- analyzer のエラーは RuntimeError としてラップし、ジョブを failed 状態に遷移
- クリッピングのエラーは ClipError として伝播
- analyzer の URL は環境変数で設定可能

### エラーハンドリング

- パイプライン中の例外 -> ジョブを `failed` 状態にしてエラーメッセージを記録
- 外部サービス接続エラー -> RuntimeError として伝播
- マッチ未検出 -> ジョブを `failed` 状態にして "No matches detected" を記録
- ハイライト未検出（マッチ内） -> そのマッチのクリッピングをスキップ
- ジョブ未存在 -> 404 Not Found

### 一時ファイル管理

- アップロード動画は `{job_id}_{filename}` で保存（衝突回避）
- パイプライン完了後（成功・失敗問わず）にアップロード一時ファイルを削除
- 結果ファイル（zip, mp4, JSON）は自動クリーンアップで期限切れ後に削除
