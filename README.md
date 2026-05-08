# Splat Highlight Pilot

スプラトゥーン試合動画からハイライトを自動切り出しするオーケストレーターサービス。
analyzer（ハイライト検出）と clipper（動画クリッピング）を連携させる中間サービスとして機能する。

## アーキテクチャ

```
WebUI (React + Vite)
  |
  | WebSocket upload
  v
Orchestrator (FastAPI, port 8030)
  |
  +---> splatoon-battle-analyzer  ... ハイライト検出
  |
  +---> clipper                   ... 動画クリッピング
  |
  v
ハイライト動画 + 分析結果JSON
```

1. ユーザーが WebUI から動画をアップロード（WebSocket）
2. オーケストレーターがジョブを作成し、analyzer にハイライト検出を依頼
3. 検出結果をもとに clipper で動画をクリッピング
4. ユーザーがハイライト動画と分析結果 JSON をダウンロード

## 技術スタック

- Python 3.12 + FastAPI + httpx
- React + Vite（フロントエンド）
- Docker / Docker Compose
- pytest + ruff（テスト / リント）

## Quick Start

```bash
cp .env.example .env
docker compose up -d
curl http://localhost:8030/health
```

## WebUI

`web/` ディレクトリに React + Vite で構築されたフロントエンドがある。
GitHub Pages にデプロイして使用する。

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | ヘルスチェック（analyzer / clipper の接続状態を含む） |
| WebSocket | `/ws/upload` | 動画アップロード。完了後にジョブを自動開始 |
| GET | `/jobs/{job_id}` | ジョブの状態取得 |
| GET | `/download/{job_id}` | ハイライト動画のダウンロード |
| GET | `/download/{job_id}/analysis` | 分析結果 JSON のダウンロード |

## テスト

Docker 内で一括実行:

```bash
docker compose exec api sh -c "ruff check . && ruff format --check . && pytest"
```

## License

MIT License
