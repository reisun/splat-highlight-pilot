# Splat Highlight Pilot

スプラトゥーン試合動画からハイライトを自動切り出しするサービス。
analyzer（ハイライト検出）と内蔵 FFmpeg クリッピングを組み合わせて動作する。

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
  +---> FFmpeg (内蔵)             ... 動画クリッピング
  |
  v
ハイライト動画 + 分析結果JSON
```

1. ユーザーが WebUI から動画をアップロード（WebSocket）
2. オーケストレーターがジョブを作成し、analyzer にハイライト検出を依頼
3. 検出結果をもとに内蔵 FFmpeg で動画をクリッピング
4. ユーザーがハイライト動画と分析結果 JSON をダウンロード

## 技術スタック

- Python 3.12 + FastAPI + httpx
- FFmpeg（動画クリッピング、コンテナ内蔵）
- React + Vite（フロントエンド）
- Docker / Docker Compose
- pytest + ruff（テスト / リント）

## Quick Start

> **前提**: 以下のサービスが起動済みであること。起動順に注意。
>
> 1. [llm-playground](https://github.com/reisun/llm-playground) — `llm-network` と agent-gateway を提供
> 2. [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) — ハイライト検出（port 8020）

```bash
# 1. 依存サービスを先に起動（未起動の場合）
cd ../llm-playground && docker compose up -d && cd -
cd ../splatoon-battle-analyzer && docker compose up -d && cd -

# 2. 本サービスを起動
cp .env.example .env
docker compose up -d
curl http://localhost:8030/health
```

## WebUI

`web/` ディレクトリに React + Vite で構築されたフロントエンドがある。
GitHub Pages にデプロイして使用する。API は Cloudflare Quick Tunnel から公開し、Caddy は使用しない。

Pages は起動時に同じディレクトリの `config.json` をキャッシュせず読み込み、
`apiBaseUrl` を HTTP、WebSocket、ダウンロードに共通で使用する。
本番で設定がない場合は画面にエラーを表示する。ローカル開発は従来の
`VITE_API_BASE_URL` または同一 origin にフォールバックする。

トンネル起動・URL 検出は `../reverse-proxy/scripts/quick-tunnels.py start splat-highlight-pilot` から実行する。
検出した HTTPS origin をリポジトリ変数 `QUICK_TUNNEL_URL` に設定して
`deploy-pages.yml` を `workflow_dispatch`（`api_base_url` に同じ URL を指定）で再実行すると、Pages 用
`config.json` が生成される。URL 更新でソースのコミットは不要。
手動設定例は `web/public/config.json.example` を参照。

Quick Tunnel の接続先は `http://api:8000`、ヘルスチェックは `/health`。
動画アップロードは WebSocket、進捗表示は HTTP ポーリングを使用し、SSE は使用しない。
API の CORS は `https://reisun.github.io` とローカル開発 origin を許可する。
起動スクリプトは `docker-compose.prod.yml` と `../reverse-proxy/tunnels/splat-highlight-pilot.yml` を組み合わせる。
API のホストポート 8030 はローカル接続専用。外部ネットワークの override は不要。analyzer の接続先と共有データ volume は従来どおり必要。

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/health` | ヘルスチェック（analyzer の接続状態を含む） |
| WebSocket | `/ws/upload` | 動画アップロード。完了後にジョブを自動開始 |
| GET | `/jobs/{job_id}` | ジョブの状態取得 |
| GET | `/download/{job_id}` | ハイライト動画のダウンロード |
| GET | `/download/{job_id}/analysis` | 分析結果 JSON のダウンロード |

## 依存サービス

本サービスは以下の外部サービスと連携して動作する（動画クリッピングは FFmpeg で内蔵済み）。

| サービス | 役割 |
|---------|------|
| [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) | ハイライト検出 API |
| [llm-playground](https://github.com/reisun/llm-playground) | LLM 実行基盤（agent-gateway、analyzer が内部で使用） |

接続先は環境変数 `ANALYZER_URL` で設定する（`.env.example` を参照）。

## テスト

Docker 内で一括実行:

```bash
docker compose exec api sh -c "ruff check . && ruff format --check . && pytest"
```

## 関連プロジェクト

- [splatoon-battle-analyzer](https://github.com/reisun/splatoon-battle-analyzer) - 試合動画のフレーム解析・ハイライト検出
- [llm-playground](https://github.com/reisun/llm-playground) - LLM 実行基盤（agent-gateway を提供）

## License

MIT License

本プロジェクトのソースコードは MIT License で提供されます。
動画クリッピングに使用する FFmpeg は LGPL/GPL でライセンスされた独立したソフトウェアであり、Docker イメージ内にバイナリとして同梱されます。詳細は [FFmpeg License](https://ffmpeg.org/legal.html) を参照してください。
