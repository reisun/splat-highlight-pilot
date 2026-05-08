"""FastAPI オーケストレーターアプリケーション."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.job_store import HighlightInfo, JobPhase, OrchestratorJobStore
from app.schemas import (
    AnalyzerFrameResult,
    AnalyzerHighlight,
    AnalyzerJobResponse,
    AnalyzerJobStatus,
    AnalyzerOptions,
    AnalyzerResponse,
    ClipperJobResponse,
    ClipperJobStatus,
    ClipSegment,
    ErrorResponse,
    HealthResponse,
    OrchestratorAnalyzerProgress,
    OrchestratorJobStatusResponse,
    ServiceStatus,
)

logger = logging.getLogger(__name__)

ANALYZER_URL = os.environ.get("ANALYZER_URL", "http://analyzer:8000")
CLIPPER_URL = os.environ.get("CLIPPER_URL", "http://clipper:8000")
SHARED_DATA_DIR = Path(os.environ.get("SHARED_DATA_DIR", "/shared-data"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "300"))
POLL_INTERVAL = 3
CLEANUP_INTERVAL = float(os.environ.get("CLEANUP_INTERVAL", "3600"))
CLEANUP_MAX_AGE = float(os.environ.get("CLEANUP_MAX_AGE", "3600"))

orchestrator_jobs = OrchestratorJobStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):  # noqa: ANN201
    """アプリ起動時に定期クリーンアップタスクを開始する."""
    task = asyncio.create_task(_periodic_cleanup())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="Splat Highlight Pilot",
    description="スプラトゥーン試合動画ハイライト自動切り出しオーケストレーター",
    version="0.3.0",
    lifespan=lifespan,
)


async def _periodic_cleanup() -> None:
    """定期的に古いジョブとファイルを削除する."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            results_dir = SHARED_DATA_DIR / "results"
            orchestrator_jobs.cleanup_old(results_dir, CLEANUP_MAX_AGE)
        except Exception:  # noqa: BLE001
            logger.exception("クリーンアップ中にエラー")


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
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(result_path),
        media_type="video/mp4",
        filename="highlight.mp4",
        headers={"Content-Disposition": 'attachment; filename="highlight.mp4"'},
    )


@app.get(
    "/download/{job_id}/analysis",
    responses={404: {"model": ErrorResponse}},
)
async def download_analysis(job_id: str) -> FileResponse:
    """解析結果のJSONファイルをダウンロードする."""
    result_path = SHARED_DATA_DIR / "results" / f"{job_id}_analysis.json"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Analysis file not found")

    return FileResponse(
        path=str(result_path),
        media_type="application/json",
        filename="analysis.json",
        headers={"Content-Disposition": 'attachment; filename="analysis.json"'},
    )


@app.get("/jobs/{job_id}", response_model=OrchestratorJobStatusResponse)
async def get_job_status(job_id: str) -> OrchestratorJobStatusResponse:
    """ジョブの状態を返す."""
    job = orchestrator_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    progress = None
    if job.phase in (JobPhase.ANALYZING, JobPhase.CLIPPING, JobPhase.COMPLETED):
        progress = OrchestratorAnalyzerProgress(
            stage=job.analyzer_progress.stage,
            stage_total=job.analyzer_progress.stage_total,
            frames_done=job.analyzer_progress.frames_done,
            frames_total=job.analyzer_progress.frames_total,
        )

    analysis_url = (
        f"/download/{job.job_id}/analysis" if job.phase == JobPhase.COMPLETED else None
    )

    return OrchestratorJobStatusResponse(
        job_id=job.job_id,
        phase=job.phase.value,
        analyzer_progress=progress,
        download_url=job.download_url,
        analysis_url=analysis_url,
        error=job.error,
        started_at=job.started_at,
    )


@app.websocket("/ws/upload")
async def ws_upload(websocket: WebSocket) -> None:
    """動画アップロード専用WebSocket。アップロード完了後にjob_idを返してclose."""
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

        job = orchestrator_jobs.create()
        job_id = job.job_id

        upload_dir = SHARED_DATA_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / f"{job_id}_{filename}"

        received = 0
        with open(upload_path, "wb") as f:  # noqa: PTH123
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
                        {
                            "type": "progress",
                            "phase": "uploading",
                            "percent": percent,
                        }
                    )

        orchestrator_jobs.set_upload_path(job_id, str(upload_path))
        asyncio.create_task(  # noqa: RUF006
            _run_pipeline(job_id, upload_path, AnalyzerOptions())
        )

        await websocket.send_json({"type": "job_created", "job_id": job_id})
        await websocket.close()
        upload_path = None

    except WebSocketDisconnect:
        upload_path = None
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


def _flatten_clipped_scores(
    frames: list[AnalyzerFrameResult],
    highlights: list[AnalyzerHighlight],
) -> None:
    """クリップ済み区間のスコアを0に置き換え、再選定を防ぐ."""
    for frame in frames:
        for h in highlights:
            if h.start_seconds <= frame.timestamp_seconds <= h.end_seconds:
                frame.score = 0
                frame.score_count_gain = 0
                break


async def _run_pipeline(job_id: str, upload_path: Path, opts: AnalyzerOptions) -> None:
    """バックグラウンドでanalyze->clipパイプラインを実行."""
    try:
        orchestrator_jobs.set_phase(job_id, JobPhase.ANALYZING)
        analyzer_result = await _call_analyzer_background(
            job_id, str(upload_path), opts
        )

        if not analyzer_result or not analyzer_result.highlights:
            orchestrator_jobs.mark_failed(job_id, "No highlights detected")
            return

        highlights = analyzer_result.highlights
        all_frames = analyzer_result.frames

        # 解析結果をJSONファイルとして保存（元のスコアを保持）
        results_dir = SHARED_DATA_DIR / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        analysis_path = results_dir / f"{job_id}_analysis.json"
        analysis_data = {
            "highlights": [
                {
                    "start_seconds": h.start_seconds,
                    "end_seconds": h.end_seconds,
                    "peak_intensity": h.peak_intensity,
                }
                for h in highlights
            ],
            "frames": [f.model_dump() for f in all_frames],
            "scan_summary": analyzer_result.scan_summary,
        }

        _flatten_clipped_scores(all_frames, highlights)
        analysis_path.write_text(
            json.dumps(analysis_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ハイライト情報を保存してログに出力
        highlight_infos = [
            HighlightInfo(
                start_seconds=h.start_seconds,
                end_seconds=h.end_seconds,
                peak_intensity=h.peak_intensity,
            )
            for h in highlights
        ]
        orchestrator_jobs.set_highlights(job_id, highlight_infos)

        logger.info(
            "ハイライト検出完了 job=%s: %s",
            job_id,
            [
                {
                    "start": h.start_seconds,
                    "end": h.end_seconds,
                    "intensity": h.peak_intensity,
                }
                for h in highlights
            ],
        )

        segments = [
            ClipSegment(start=str(h.start_seconds), end=str(h.end_seconds))
            for h in highlights
        ]

        orchestrator_jobs.set_phase(job_id, JobPhase.CLIPPING)
        await _call_clipper_background(
            str(upload_path),
            segments,
            str(results_dir),
            job_id,
        )

        orchestrator_jobs.mark_completed(job_id, f"/download/{job_id}")
    except Exception as e:  # noqa: BLE001
        logger.exception("パイプライン処理中にエラー job=%s", job_id)
        orchestrator_jobs.mark_failed(job_id, str(e))
    finally:
        _cleanup_file(upload_path)


async def _call_analyzer_background(
    job_id: str,
    file_path: str,
    opts: AnalyzerOptions,
) -> AnalyzerResponse | None:
    """バックグラウンド用: ジョブストアに進捗を書き込む版."""
    payload = {
        "file_path": file_path,
        **opts.model_dump(exclude_none=True),
    }

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{ANALYZER_URL}/analyze/highlights/jobs",
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

        job_data = AnalyzerJobResponse(**resp.json())
        analyzer_job_id = job_data.job_id

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                resp = await client.get(
                    f"{ANALYZER_URL}/analyze/highlights/jobs/{analyzer_job_id}",
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

            job_status = AnalyzerJobStatus(**resp.json())

            if job_status.progress:
                orchestrator_jobs.update_analyzer_progress(
                    job_id,
                    stage=job_status.progress.phase,
                    stage_total=job_status.progress.phase_total,
                    frames_done=job_status.progress.frames_done,
                    frames_total=job_status.progress.frames_total,
                )

            if job_status.status == "completed":
                return job_status.result

            if job_status.status == "failed":
                msg = f"analyzer がエラーを返しました: {job_status.error}"
                raise RuntimeError(msg)


async def _call_clipper_background(
    file_path: str,
    segments: list[ClipSegment],
    output_dir: str,
    job_id: str,
) -> None:
    """clipper の非同期ジョブAPIを呼び出し、完了までポーリング."""
    payload = {
        "file_path": file_path,
        "segments": [s.model_dump() for s in segments],
        "output_dir": output_dir,
        "output_format": "mp4",
    }

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{CLIPPER_URL}/clip/jobs",
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

        clipper_job = ClipperJobResponse(**resp.json())
        clipper_job_id = clipper_job.job_id

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                resp = await client.get(
                    f"{CLIPPER_URL}/clip/jobs/{clipper_job_id}",
                )
            except httpx.RequestError as e:
                msg = f"clipper への接続に失敗しました: {e}"
                raise RuntimeError(msg) from e

            if resp.status_code != 200:  # noqa: PLR2004
                msg = (
                    f"clipper がエラーを返しました "
                    f"(status={resp.status_code}): {resp.text}"
                )
                raise RuntimeError(msg)

            status = ClipperJobStatus(**resp.json())

            if status.status == "completed":
                result_path = Path(status.result_path or "")
                final = Path(output_dir) / f"{job_id}.mp4"
                if result_path.exists() and result_path != final:
                    shutil.move(str(result_path), str(final))
                return

            if status.status == "failed":
                msg = f"clipper がエラーを返しました: {status.error}"
                raise RuntimeError(msg)


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("一時ファイルの削除に失敗: %s", path)
