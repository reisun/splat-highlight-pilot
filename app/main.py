"""FastAPI オーケストレーターアプリケーション."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from app.clip import clip_video_async
from app.job_store import HighlightInfo, JobPhase, OrchestratorJobStore
from app.schemas import (
    AnalyzerFrameResult,
    AnalyzerHighlight,
    AnalyzerJobResponse,
    AnalyzerJobStatus,
    AnalyzerOptions,
    AnalyzerResponse,
    ErrorResponse,
    HealthResponse,
    MatchScanJobStatus,
    OrchestratorAnalyzerProgress,
    OrchestratorJobStatusResponse,
    OrchestratorMatchProgress,
    ServiceStatus,
)

logger = logging.getLogger(__name__)

ANALYZER_URL = os.environ.get("ANALYZER_URL", "http://analyzer:8000")
SHARED_DATA_DIR = Path(os.environ.get("SHARED_DATA_DIR", "/shared-data"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "300"))
POLL_INTERVAL = 3
CLEANUP_INTERVAL = float(os.environ.get("CLEANUP_INTERVAL", "3600"))
CLEANUP_MAX_AGE = float(os.environ.get("CLEANUP_MAX_AGE", "3600"))

orchestrator_jobs = OrchestratorJobStore()
_STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    description=("スプラトゥーン試合動画ハイライト自動切り出しオーケストレーター"),
    version="0.4.0",
    lifespan=lifespan,
)


async def _periodic_cleanup() -> None:
    """定期的に古いジョブとファイルを削除する."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            orchestrator_jobs.cleanup_old(SHARED_DATA_DIR, CLEANUP_MAX_AGE)
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

    return HealthResponse(
        status="ok",
        updated_at=_STARTED_AT,
        services=[analyzer_status],
    )


@app.get(
    "/download/{job_id}",
    responses={404: {"model": ErrorResponse}},
)
async def download(job_id: str) -> FileResponse:
    """zip または mp4 のダウンロード."""
    # zip を優先
    zip_path = SHARED_DATA_DIR / "results" / f"{job_id}.zip"
    if zip_path.exists():
        job = orchestrator_jobs.get(job_id)
        zip_filename = "highlight.zip"
        if job and job.filename:
            stem = Path(job.filename).stem
            zip_filename = f"{stem}_highlight.zip"
        encoded = urllib.parse.quote(zip_filename)
        cd = f"attachment; filename=\"highlight.zip\"; filename*=UTF-8''{encoded}"
        return FileResponse(
            path=str(zip_path),
            media_type="application/zip",
            filename=zip_filename,
            headers={"Content-Disposition": cd},
        )

    # 後方互換: 旧形式の mp4
    mp4_path = SHARED_DATA_DIR / "results" / f"{job_id}.mp4"
    if mp4_path.exists():
        return FileResponse(
            path=str(mp4_path),
            media_type="video/mp4",
            filename="highlight.mp4",
        )

    raise HTTPException(status_code=404, detail="File not found")


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
async def get_job_status(
    job_id: str,
) -> OrchestratorJobStatusResponse:
    """ジョブの状態を返す."""
    job = orchestrator_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    progress = None
    if job.phase in (
        JobPhase.SCANNING,
        JobPhase.ANALYZING,
        JobPhase.CLIPPING,
        JobPhase.COMPLETED,
    ):
        progress = OrchestratorAnalyzerProgress(
            stage=job.analyzer_progress.stage,
            stage_total=job.analyzer_progress.stage_total,
            frames_done=job.analyzer_progress.frames_done,
            frames_total=job.analyzer_progress.frames_total,
        )

    match_progress = None
    if job.match_progress.total_matches > 0:
        match_progress = OrchestratorMatchProgress(
            current_match=job.match_progress.current_match,
            total_matches=job.match_progress.total_matches,
        )

    analysis_url = (
        f"/download/{job.job_id}/analysis" if job.phase == JobPhase.COMPLETED else None
    )

    return OrchestratorJobStatusResponse(
        job_id=job.job_id,
        phase=job.phase.value,
        analyzer_progress=progress,
        match_progress=match_progress,
        download_url=job.download_url,
        analysis_url=analysis_url,
        error=job.error,
        started_at=job.started_at,
    )


@app.websocket("/ws/upload")
async def ws_upload(websocket: WebSocket) -> None:
    """動画アップロード専用WebSocket。"""
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
        options_raw = start_msg.get("options") or {}
        opts = AnalyzerOptions(**options_raw)

        job = orchestrator_jobs.create()
        job_id = job.job_id

        orchestrator_jobs.set_filename(job_id, filename)

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
                        {
                            "type": "error",
                            "message": "Unexpected message",
                        }
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
            _run_pipeline(job_id, upload_path, opts)
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
                frame.score = 0.0
                frame.score_count_gain = 0.0
                frame.enemy_score_gain = 0.0
                break


async def _run_pipeline(job_id: str, upload_path: Path, opts: AnalyzerOptions) -> None:
    """バックグラウンドで scan -> analyze(per match) -> clip -> zip."""
    try:
        # --- Phase 1: Scanning ---
        orchestrator_jobs.set_phase(job_id, JobPhase.SCANNING)
        scan_data = await _call_match_scan(job_id, str(upload_path))
        matches = scan_data["matches"]
        scan_readings = scan_data["readings"]
        scan_job_id = scan_data.get("scan_job_id")

        if not matches:
            orchestrator_jobs.mark_failed(job_id, "No matches detected")
            return

        total_matches = len(matches)
        orchestrator_jobs.update_match_progress(job_id, 0, total_matches)

        results_dir = SHARED_DATA_DIR / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        # --- Phase 2: Per-match analysis ---
        orchestrator_jobs.set_phase(job_id, JobPhase.ANALYZING)
        match_analyses: list[dict] = []
        match_infos: list[dict] = []

        for i, match in enumerate(matches):
            orchestrator_jobs.update_match_progress(job_id, i + 1, total_matches)

            match_start = match["start_seconds"]
            match_duration = match["duration_seconds"]
            match_end = match_start + match_duration
            if i + 1 < total_matches:
                next_start = matches[i + 1]["start_seconds"]
                match_end = min(match_end, next_start)

            match_infos.append(
                {
                    "match_number": i + 1,
                    "start_seconds": match_start,
                    "end_seconds": match_end,
                    "duration_type": match.get("duration_type", "unknown"),
                    "knockout": match_end < match_start + match_duration,
                }
            )

            match_opts = AnalyzerOptions(
                start=match_start,
                end=match_end,
                interval=opts.interval,
                threshold=opts.threshold,
                model=opts.model,
                concurrency=opts.concurrency,
                duration_type=match.get("duration_type"),
                scan_job_id=scan_job_id,
                weights=opts.weights,
            )

            analyzer_result = await _call_analyzer_background(
                job_id, str(upload_path), match_opts
            )

            if not analyzer_result or not analyzer_result.highlights:
                logger.warning(
                    "試合 %d/%d でハイライト未検出 job=%s",
                    i + 1,
                    total_matches,
                    job_id,
                )
                continue

            highlights = analyzer_result.highlights
            all_frames = analyzer_result.frames

            analysis_data = {
                "match_index": i + 1,
                "match_start_seconds": match_start,
                "match_duration_seconds": match_duration,
                "match_duration_type": match.get("duration_type", "unknown"),
                "scan_summary": analyzer_result.scan_summary,
                "highlights": [
                    {
                        "start_seconds": h.start_seconds,
                        "end_seconds": h.end_seconds,
                        "peak_intensity": h.peak_intensity,
                    }
                    for h in highlights
                ],
                "scoring": analyzer_result.scoring.model_dump(),
                "frames": [f.model_dump() for f in all_frames],
            }

            _flatten_clipped_scores(all_frames, highlights)

            segments = [
                {
                    "start": str(h.start_seconds),
                    "end": str(h.end_seconds),
                }
                for h in highlights
            ]

            match_analyses.append(
                {
                    "match_index": i + 1,
                    "analysis_data": analysis_data,
                    "segments": segments,
                    "highlights": [
                        {
                            "start_seconds": h.start_seconds,
                            "end_seconds": h.end_seconds,
                            "peak_intensity": h.peak_intensity,
                        }
                        for h in highlights
                    ],
                }
            )

        if not match_analyses:
            orchestrator_jobs.mark_failed(job_id, "No highlights detected in any match")
            return

        # --- Phase 3: Clipping + Build zip ---
        orchestrator_jobs.set_phase(job_id, JobPhase.CLIPPING)

        if opts.per_match:
            match_outputs = await _clip_per_match(
                job_id, upload_path, match_analyses, results_dir
            )
        else:
            match_outputs = await _clip_combined(
                job_id, upload_path, match_analyses, results_dir
            )

        zip_path = results_dir / f"{job_id}.zip"
        _build_zip(
            match_outputs,
            zip_path,
            match_infos,
            scan_readings,
            per_match=opts.per_match,
        )

        all_highlights = []
        for mo in match_outputs:
            for h in mo["highlights"]:
                all_highlights.append(
                    HighlightInfo(
                        start_seconds=h["start_seconds"],
                        end_seconds=h["end_seconds"],
                        peak_intensity=h["peak_intensity"],
                    )
                )
        orchestrator_jobs.set_highlights(job_id, all_highlights)

        for mo in match_outputs:
            for p in mo.get("temp_files", []):
                _cleanup_file(Path(p))
            temp_dir = mo.get("temp_dir")
            if temp_dir:
                with contextlib.suppress(OSError):
                    Path(temp_dir).rmdir()

        orchestrator_jobs.mark_completed(job_id, f"/download/{job_id}")
    except Exception as e:  # noqa: BLE001
        logger.exception("パイプライン処理中にエラー job=%s", job_id)
        orchestrator_jobs.mark_failed(job_id, str(e))
    finally:
        _cleanup_file(upload_path)


async def _clip_per_match(
    job_id: str,
    upload_path: Path,
    match_analyses: list[dict],
    results_dir: Path,
) -> list[dict]:
    """試合ごとに個別のハイライト動画を作成する."""
    match_outputs: list[dict] = []
    for ma in match_analyses:
        match_dir = results_dir / f"{job_id}_match_{ma['match_index']}"
        match_dir.mkdir(parents=True, exist_ok=True)

        analysis_path = match_dir / "analysis.json"
        analysis_path.write_text(
            json.dumps(ma["analysis_data"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        highlight_path = match_dir / "highlight.mp4"
        await clip_video_async(upload_path, ma["segments"], highlight_path, intro=True)

        match_outputs.append(
            {
                "match_index": ma["match_index"],
                "highlight_path": str(highlight_path),
                "analysis_path": str(analysis_path),
                "highlights": ma["highlights"],
                "temp_files": [str(highlight_path), str(analysis_path)],
                "temp_dir": str(match_dir),
            }
        )
    return match_outputs


async def _clip_combined(
    job_id: str,
    upload_path: Path,
    match_analyses: list[dict],
    results_dir: Path,
) -> list[dict]:
    """全試合のハイライト区間を1本の動画に結合する."""
    temp_dir = results_dir / f"{job_id}_combined"
    temp_dir.mkdir(parents=True, exist_ok=True)

    all_segments: list[dict[str, str]] = []
    all_analysis: list[dict] = []
    all_highlights: list[dict] = []
    for ma in match_analyses:
        all_segments.extend(ma["segments"])
        all_analysis.append(ma["analysis_data"])
        all_highlights.extend(ma["highlights"])

    combined_analysis = {
        "matches": all_analysis,
    }
    analysis_path = temp_dir / "analysis.json"
    analysis_path.write_text(
        json.dumps(combined_analysis, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    highlight_path = temp_dir / "highlight.mp4"
    await clip_video_async(upload_path, all_segments, highlight_path, intro=True)

    return [
        {
            "combined": True,
            "highlight_path": str(highlight_path),
            "analysis_path": str(analysis_path),
            "highlights": all_highlights,
            "temp_files": [str(highlight_path), str(analysis_path)],
            "temp_dir": str(temp_dir),
        }
    ]


def _build_zip(
    match_outputs: list[dict],
    zip_path: Path,
    match_infos: list[dict] | None = None,
    scan_readings: list[dict] | None = None,
    *,
    per_match: bool = False,
) -> None:
    """ハイライトと分析結果を zip にまとめる."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if match_infos is not None:
            matches_data = {
                "matches": match_infos,
                "scan_readings": scan_readings or [],
            }
            zf.writestr(
                "analysis/match.json",
                json.dumps(matches_data, ensure_ascii=False, indent=2),
            )

        if per_match:
            for mo in match_outputs:
                match_idx = mo["match_index"]

                highlight_path = Path(mo["highlight_path"])
                if highlight_path.exists():
                    zf.write(highlight_path, f"highlight-match-{match_idx}.mp4")

                analysis_path = Path(mo["analysis_path"])
                if analysis_path.exists():
                    zf.write(analysis_path, f"analysis/analysis-match-{match_idx}.json")
        else:
            mo = match_outputs[0]
            highlight_path = Path(mo["highlight_path"])
            if highlight_path.exists():
                zf.write(highlight_path, "highlight.mp4")

            analysis_path = Path(mo["analysis_path"])
            if analysis_path.exists():
                zf.write(analysis_path, "analysis/analysis.json")


async def _call_match_scan(
    job_id: str,
    file_path: str,
) -> dict:
    """analyzer の試合境界スキャンAPIを呼び出す.matches と readings を返す."""
    payload = {"file_path": file_path, "interval": 30.0}

    async with _get_http_client() as client:
        try:
            resp = await client.post(
                f"{ANALYZER_URL}/analyze/matches/scan/jobs",
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

        scan_job_data = resp.json()
        scan_job_id = scan_job_data["job_id"]

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                resp = await client.get(
                    f"{ANALYZER_URL}/analyze/matches/scan/jobs/{scan_job_id}",
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

            scan_status = MatchScanJobStatus(**resp.json())

            if scan_status.progress:
                orchestrator_jobs.update_analyzer_progress(
                    job_id,
                    stage=0,
                    stage_total=1,
                    frames_done=scan_status.progress.frames_done,
                    frames_total=scan_status.progress.frames_total,
                )

            if scan_status.status == "completed":
                if scan_status.result:
                    result = scan_status.result
                    return {
                        "matches": [m.model_dump() for m in result.matches],
                        "readings": [r.model_dump() for r in result.readings],
                        "scan_job_id": scan_job_id,
                    }
                return {"matches": [], "readings": [], "scan_job_id": scan_job_id}

            if scan_status.status == "failed":
                msg = f"analyzer スキャンエラー: {scan_status.error}"
                raise RuntimeError(msg)


async def _call_analyzer_background(
    job_id: str,
    file_path: str,
    opts: AnalyzerOptions,
) -> AnalyzerResponse | None:
    """バックグラウンド用: ジョブストアに進捗を書き込む版."""
    payload = {
        "file_path": file_path,
        **opts.model_dump(exclude_none=True, exclude={"per_match"}),
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


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("一時ファイルの削除に失敗: %s", path)
