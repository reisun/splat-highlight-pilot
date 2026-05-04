# API 設計書

## 概要

splat-highlight-pilot orchestrator の REST API 仕様。

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
  "services": [
    {"name": "analyzer", "status": "connected", "detail": null},
    {"name": "clipper", "status": "connected", "detail": null}
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

### POST /highlight

動画ファイルをアップロードし、ハイライト区間を自動切り出しした mp4 を返す。

#### リクエスト

Content-Type: `multipart/form-data`

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| file | file | Yes | 動画ファイル（mp4 推奨） |
| options | string | No | analyzer オプション（JSON 文字列） |

##### options の JSON 構造

```json
{
  "start": null,
  "end": null,
  "stage1_interval": 30,
  "stage2_interval": 5,
  "threshold": 5,
  "max_highlights": null,
  "model": null,
  "concurrency": 1
}
```

すべてのフィールドは任意。省略時はデフォルト値が使用される。

#### レスポンス（成功）

- Content-Type: `video/mp4`
- Content-Disposition: `attachment; filename="highlight.mp4"`
- Body: mp4 バイナリストリーム

#### エラーレスポンス

```json
{
  "detail": "エラーメッセージ"
}
```

#### ステータスコード

| コード | 説明 |
|---|---|
| 200 | 成功。ハイライト動画を返却 |
| 400 | リクエスト不正（ファイル未指定、options JSON 不正） |
| 404 | ハイライト未検出（"No highlights detected"） |
| 500 | 内部エラー |
| 502 | 外部サービス（analyzer / clipper）のエラー |

---

## 処理フロー

1. クライアントが動画ファイルを `POST /highlight` にアップロード
2. orchestrator が共有ボリュームに動画を保存
3. analyzer の `POST /analyze/highlights` にファイルパスを送信
4. analyzer がハイライト区間のリストを返却
5. ハイライトが 0 件の場合は 404 を返却
6. ハイライト区間を segments 形式に変換
7. clipper の `POST /clip` にファイルパスと segments を送信
8. clipper が結合済み mp4 を返却
9. orchestrator がクライアントに mp4 をストリーミング返却

## CORS

許可オリジン:
- `https://reisun.github.io`
- `http://localhost:5173`
- `http://localhost:3000`

## タイムアウト

外部サービスへの HTTP リクエストは 300 秒のタイムアウトを設定。
動画の分析・クリッピングに 1-3 分かかるため。
