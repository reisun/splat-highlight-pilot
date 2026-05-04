"""FastAPI オーケストレーターアプリケーション."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.schemas import (
    AnalyzerOptions,
    AnalyzerResponse,
    ClipSegment,
    ErrorResponse,
    HealthResponse,
    ServiceStatus,
)

logger = logging.getLogger(__name__)

ANALYZER_URL = os.environ.get("ANALYZER_URL", "http://analyzer:8000")
CLIPPER_URL = os.environ.get("CLIPPER_URL", "http://clipper:8000")
SHARED_DATA_DIR = Path(os.environ.get("SHARED_DATA_DIR", "/shared-data"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "300"))

app = FastAPI(
    title="Splat Highlight Pilot",
    description="スプラトゥーン試合動画ハイライト自動切り出しオーケストレーター",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://reisun.github.io",
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_http_client() -> httpx.AsyncClient:
    """タイムアウト付き httpx クライアントを生成."""
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT))


async def _check_service(
    client: httpx.AsyncClient,
    name: str,
    url: str,
) -> ServiceStatus:
    """外部サービスのヘルスチェックを実行."""
    try:
        resp = await client.get(f"{url}/health")
        resp.raise_for_status()
        return ServiceStatus(name=name, status="connected")
    except Exception as e:  # noqa: BLE001
        return ServiceStatus(name=name, status="disconnected", detail=str(e))


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """自身 + 外部サービスのヘルスチェック."""
    async with _get_http_client() as client:
        analyzer_status = await _check_service(client, "analyzer", ANALYZER_URL)
        clipper_status = await _check_service(client, "clipper", CLIPPER_URL)

    return HealthResponse(
        status="ok",
        services=[analyzer_status, clipper_status],
    )


@app.post(
    "/highlight",
    responses={
        200: {"content": {"video/mp4": {}}, "description": "ハイライト動画"},
        404: {"model": ErrorResponse, "description": "ハイライト未検出"},
        500: {"model": ErrorResponse, "description": "処理エラー"},
        502: {"model": ErrorResponse, "description": "外部サービスエラー"},
    },
)
async def create_highlight(
    file: UploadFile,
    options: str | None = Form(default=None),
) -> StreamingResponse:
    """動画からハイライトを自動切り出し.

    1. アップロード動画を共有ボリュームに保存
    2. analyzer でハイライト区間を検出
    3. clipper でハイライト区間をクリッピング
    4. 結果の mp4 を返却
    """
    # オプション解析
    analyzer_opts = AnalyzerOptions()
    if options:
        try:
            analyzer_opts = AnalyzerOptions(**json.loads(options))
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"options の JSON が不正です: {e}",
            ) from e

    # アップロードファイルを共有ボリュームに保存
    upload_dir = SHARED_DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "video.mp4"
    unique_name = f"{uuid.uuid4()}_{filename}"
    saved_path = upload_dir / unique_name

    try:
        content = await file.read()
        saved_path.write_bytes(content)

        # analyzer にハイライト検出を依頼
        highlights = await _call_analyzer(str(saved_path), analyzer_opts)

        if len(highlights) == 0:
            raise HTTPException(
                status_code=404,
                detail="No highlights detected",
            )

        # segments を構築
        segments = [
            ClipSegment(
                start=str(h.start_time),
                end=str(h.end_time),
            )
            for h in highlights
        ]

        # clipper にクリッピングを依頼
        video_bytes = await _call_clipper(str(saved_path), segments)

        return StreamingResponse(
            content=iter([video_bytes]),
            media_type="video/mp4",
            headers={
                "Content-Disposition": 'attachment; filename="highlight.mp4"',
            },
        )

    except HTTPException:
        _cleanup_file(saved_path)
        raise
    except Exception as e:
        _cleanup_file(saved_path)
        logger.exception("ハイライト処理中に予期しないエラーが発生")
        raise HTTPException(
            status_code=500,
            detail=f"内部エラー: {e}",
        ) from e


async def _call_analyzer(
    file_path: str,
    opts: AnalyzerOptions,
) -> list:
    """analyzer の /analyze/highlights を呼び出し."""
    payload = {
        "file_path": file_path,
        **opts.model_dump(),
    }

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{ANALYZER_URL}/analyze/highlights",
                json=payload,
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"analyzer への接続に失敗しました: {e}",
            ) from e

        if resp.status_code != 200:  # noqa: PLR2004
            raise HTTPException(
                status_code=502,
                detail=(
                    f"analyzer がエラーを返しました "
                    f"(status={resp.status_code}): {resp.text}"
                ),
            )

        try:
            data = AnalyzerResponse(**resp.json())
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"analyzer のレスポンス解析に失敗しました: {e}",
            ) from e

    return data.highlights


async def _call_clipper(
    file_path: str,
    segments: list[ClipSegment],
) -> bytes:
    """clipper の /clip を呼び出し（file_path モード）."""
    payload = {
        "file_path": file_path,
        "segments": [s.model_dump() for s in segments],
        "output_format": "mp4",
    }

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{CLIPPER_URL}/clip",
                json=payload,
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"clipper への接続に失敗しました: {e}",
            ) from e

        if resp.status_code != 200:  # noqa: PLR2004
            raise HTTPException(
                status_code=502,
                detail=(
                    f"clipper がエラーを返しました "
                    f"(status={resp.status_code}): {resp.text}"
                ),
            )

    return resp.content


def _cleanup_file(path: Path) -> None:
    """一時ファイルを削除."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("一時ファイルの削除に失敗: %s", path)
