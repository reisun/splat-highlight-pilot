# Splat Highlight Pilot

スプラトゥーン試合動画からハイライトを自動切り出しするサービス。
analyzer（ハイライト検出）と内蔵 FFmpeg クリッピングで動作する。

## Quick Start

```bash
# 環境変数ファイルをコピー
cp .env.example .env

# ビルド & 起動
docker compose up -d

# 動作確認
curl http://localhost:8030/health
```

## Development

### テスト実行

テストは Docker 内で実行:

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

一括実行:

```bash
docker compose exec api sh -c "ruff check . && ruff format --check . && pytest"
```

### ビルド

```bash
docker compose build
```

## Tech Stack

- Python 3.12 + FastAPI
- httpx（外部サービス連携）
- FFmpeg（動画クリッピング）
- Docker for deployment
- pytest + ruff for testing and linting

## Project Structure

- `app/` - アプリケーションソースコード
  - `main.py` - FastAPI アプリとエンドポイント
  - `clip.py` - FFmpeg ベース動画クリッピング
  - `schemas.py` - Pydantic リクエスト/レスポンスモデル
  - `job_store.py` - インメモリジョブストア（ジョブ状態管理、クリーンアップ）
- `tests/` - テストスイート
- `web/` - React + Vite フロントエンド
- `docs/` - 設計書

## Rules

- AGENTS.md（親ワークスペース）のルールに従う
- feature ブランチのみ、main への直接コミット禁止
- `.env` はコミットしない
- 全変更は ruff + pytest をパスすること
