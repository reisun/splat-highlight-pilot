"""FastAPI オーケストレーターアプリケーション."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.schemas import (
    AnalyzerHighlight,
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


def _get_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT))


async def _check_service(
    client: httpx.AsyncClient,
    name: str,
    url: str,
) -> ServiceStatus:
    try:
        resp = await client.get(f"{url}/health")
        resp.raise_for_status()
        return ServiceStatus(name=name, status="connected")
    except Exception as e:  # noqa: BLE001
        return ServiceStatus(name=name, status="disconnected", detail=str(e))


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    async with _get_http_client() as client:
        analyzer_status = await _check_service(client, "analyzer", ANALYZER_URL)
        clipper_status = await _check_service(client, "clipper", CLIPPER_URL)

    return HealthResponse(
        status="ok",
        services=[analyzer_status, clipper_status],
    )


@app.get(
    "/download/{job_id}",
    responses={404: {"model": ErrorResponse}},
)
async def download(job_id: str) -> FileResponse:
    result_path = SHARED_DATA_DIR / "results" / f"{job_id}.mp4"
    if not result_path.exists():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(result_path),
        media_type="video/mp4",
        filename="highlight.mp4",
        headers={"Content-Disposition": 'attachment; filename="highlight.mp4"'},
        background=BackgroundTask(_cleanup_file, result_path),
    )


@app.websocket("/ws/highlight")
async def ws_highlight(websocket: WebSocket) -> None:
    await websocket.accept()
    upload_path: Path | None = None

    try:
        start_raw = await websocket.receive_text()
        start_msg = json.loads(start_raw)

        if start_msg.get("type") != "start":
            await websocket.send_json(
                {"type": "error", "message": "Expected start message"}
            )
            await websocket.close()
            return

        filename = start_msg.get("filename", "video.mp4")
        total_size = start_msg.get("size", 0)

        job_id = str(uuid.uuid4())
        upload_dir = SHARED_DATA_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / f"{job_id}_{filename}"

        received = 0
        with open(upload_path, "wb") as f:
            while True:
                msg = await websocket.receive()

                if "text" in msg and msg["text"] is not None:
                    text_data = json.loads(msg["text"])
                    if text_data.get("type") == "upload_complete":
                        break
                    await websocket.send_json(
                        {"type": "error", "message": "Unexpected message"}
                    )
                    await websocket.close()
                    return

                if "bytes" in msg and msg["bytes"] is not None:
                    chunk = msg["bytes"]
                    f.write(chunk)
                    received += len(chunk)
                    percent = int(received / total_size * 100) if total_size else 0
                    await websocket.send_json(
                        {"type": "progress", "phase": "uploading", "percent": percent}
                    )

        await websocket.send_json({"type": "progress", "phase": "analyzing"})
        highlights = await _run_with_heartbeat(
            websocket, "analyzing", _call_analyzer(str(upload_path), AnalyzerOptions())
        )

        if len(highlights) == 0:
            await websocket.send_json(
                {"type": "error", "message": "No highlights detected"}
            )
            await websocket.close()
            return

        segments = [
            ClipSegment(start=str(h.start_seconds), end=str(h.end_seconds))
            for h in highlights
        ]

        await websocket.send_json({"type": "progress", "phase": "clipping"})
        video_bytes = await _run_with_heartbeat(
            websocket, "clipping", _call_clipper(str(upload_path), segments)
        )

        results_dir = SHARED_DATA_DIR / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / f"{job_id}.mp4"
        result_path.write_bytes(video_bytes)

        await websocket.send_json(
            {"type": "done", "download_url": f"/download/{job_id}"}
        )
        await websocket.close()

    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.exception("WebSocket処理中にエラーが発生")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
        except Exception:  # noqa: BLE001, S110
            pass
    finally:
        if upload_path:
            _cleanup_file(upload_path)


HEARTBEAT_INTERVAL = 10


async def _run_with_heartbeat(
    websocket: WebSocket,
    phase: str,
    coro,  # noqa: ANN001
):
    async def _heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send_json({"type": "progress", "phase": phase})

    task = asyncio.create_task(_heartbeat())
    try:
        return await coro
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _call_analyzer(
    file_path: str,
    opts: AnalyzerOptions,
) -> list[AnalyzerHighlight]:
    payload = {
        "file_path": file_path,
        **opts.model_dump(exclude_none=True),
    }

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{ANALYZER_URL}/analyze/highlights",
                json=payload,
            )
        except httpx.RequestError as e:
            msg = f"analyzer への接続に失敗しました: {e}"
            raise RuntimeError(msg) from e

        if resp.status_code != 200:  # noqa: PLR2004
            msg = (
                f"analyzer がエラーを返しました "
                f"(status={resp.status_code}): {resp.text}"
            )
            raise RuntimeError(msg)

        try:
            data = AnalyzerResponse(**resp.json())
        except Exception as e:
            msg = f"analyzer のレスポンス解析に失敗しました: {e}"
            raise RuntimeError(msg) from e

    return data.highlights


async def _call_clipper(
    file_path: str,
    segments: list[ClipSegment],
) -> bytes:
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
            msg = f"clipper への接続に失敗しました: {e}"
            raise RuntimeError(msg) from e

        if resp.status_code != 200:  # noqa: PLR2004
            msg = (
                f"clipper がエラーを返しました (status={resp.status_code}): {resp.text}"
            )
            raise RuntimeError(msg)

    return resp.content


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("一時ファイルの削除に失敗: %s", path)
