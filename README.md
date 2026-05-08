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
  |       +---> llm-playground (agent-gateway) ... LLM 解析
  |
  +---> movie-edit-pilot (clipper) ... 動画クリッピング
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

> **前提**: 以下のサービスが起動済みであること。起動順に注意。
>
> 1. [llm-playground](https://github.com/reisun/llm-playground) — `llm-network` と agent-gateway を提供
> 2. [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) — ハイライト検出（port 8020）
> 3. [movie-edit-pilot](https://github.com/reisun/movie-edit-pilot) — 動画クリッピング（port 8010）

```bash
# 1. 依存サービスを先に起動（未起動の場合）
cd ../llm-playground && docker compose up -d && cd -
cd ../splatoon-battle-analyzer && docker compose up -d && cd -
cd ../movie-edit-pilot && docker compose up -d && cd -

# 2. 本サービスを起動
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

## 依存サービス

本サービスは以下の外部サービスと連携して動作する。

| サービス | 役割 |
|---------|------|
| [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) | ハイライト検出 API |
| [movie-edit-pilot](https://github.com/reisun/movie-edit-pilot) | 動画クリッピング API（clipper） |
| [llm-playground](https://github.com/reisun/llm-playground) | LLM 実行基盤（agent-gateway、analyzer が内部で使用） |

接続先は環境変数 `ANALYZER_URL` と `CLIPPER_URL` で設定する（`.env.example` を参照）。

## テスト

Docker 内で一括実行:

```bash
docker compose exec api sh -c "ruff check . && ruff format --check . && pytest"
```

## 関連プロジェクト

- [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) - 試合動画のフレーム解析・ハイライト検出
- [movie-edit-pilot](https://github.com/reisun/movie-edit-pilot) - 動画クリッピング・編集サービス
- [llm-playground](https://github.com/reisun/llm-playground) - LLM 実行基盤（agent-gateway を提供）

## License

MIT License
