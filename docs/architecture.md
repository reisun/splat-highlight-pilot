# アーキテクチャ設計書

## 概要

splat-highlight-pilot は、スプラトゥーン試合動画のハイライト自動切り出しを行うオーケストレーターサービス。
既存の analyzer（ハイライト検出）と clipper（動画クリッピング）を連携させる中間層として機能する。

## システム構成

```
[Web UI]                   ← React + TypeScript + Vite (GitHub Pages)
   │
   │  HTTPS (CORS)
   ▼
[orchestrator :8030]       ← splat-highlight-pilot (FastAPI)
   │
   │  HTTP (host.docker.internal 経由)
   ├────────────────┐
   ▼                ▼
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
├── uploads/               ← orchestrator がアップロード動画を保存
│   └── {uuid}_{filename}  ← 一時ファイル（処理後に削除）
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
1. [Web UI] ---(multipart upload)---> [orchestrator]
2. [orchestrator] ---(write file)---> /shared-data/uploads/
3. [orchestrator] ---(POST /analyze/highlights {file_path})---> [analyzer]
4. [analyzer] ---(read file)---> /shared-data/uploads/
5. [analyzer] ---(highlights[])---> [orchestrator]
6. [orchestrator] ---(POST /clip multipart {file, segments})---> [clipper]
7. [clipper] ---(mp4 binary)---> [orchestrator]
9. [orchestrator] ---(mp4 stream)---> [Web UI]
10. [orchestrator] ---(delete file)---> /shared-data/uploads/
```

## 環境変数

| 変数名 | デフォルト | 説明 |
|---|---|---|
| ANALYZER_URL | http://host.docker.internal:8020 | analyzer サービスの URL |
| CLIPPER_URL | http://host.docker.internal:8010 | clipper サービスの URL |
| SHARED_DATA_DIR | /shared-data | 共有ボリュームのパス |
| HTTP_TIMEOUT | 300 | 外部サービスへのタイムアウト（秒） |
| API_HOST | 0.0.0.0 | API リッスンホスト |
| API_PORT | 8000 | API リッスンポート |

## 設計方針

### 疎結合

- analyzer の API 仕様は開発中のため、レスポンスモデルで `extra = "allow"` を設定
- analyzer/clipper のエラーは 502 としてラップし、詳細をクライアントに伝達
- 各外部サービスの URL は環境変数で設定可能

### エラーハンドリング

- 外部サービス接続エラー → 502 Bad Gateway
- ハイライト未検出 → 404 Not Found
- リクエスト不正 → 400 Bad Request
- 内部エラー → 500 Internal Server Error

### 一時ファイル管理

- アップロード動画は UUID 付きファイル名で保存（衝突回避）
- 処理完了後（成功・失敗問わず）に一時ファイルを削除
