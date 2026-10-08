"""FastAPI オーケストレーターアプリケーション."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import clip
from app.admission import AdmissionController
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
from app.upload_policy import UploadPolicy

logger = logging.getLogger(__name__)

ANALYZER_URL = os.environ.get("ANALYZER_URL", "http://analyzer:8000")
SHARED_DATA_DIR = Path(os.environ.get("SHARED_DATA_DIR", "/shared-data"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "300"))
POLL_INTERVAL = 3
CLEANUP_INTERVAL = float(os.environ.get("CLEANUP_INTERVAL", "3600"))
CLEANUP_MAX_AGE = float(os.environ.get("CLEANUP_MAX_AGE", "3600"))

MAX_UPLOAD_BYTES = 15_000_000_000
MAX_CHUNK_BYTES = 1024 * 1024
UPLOAD_MAX_SECONDS = 3600
UPLOAD_IDLE_SECONDS = 60
UPLOAD_GRACE_SECONDS = 120
UPLOAD_SLOW_SECONDS = 60
admission: AdmissionController | None = None
_pipeline_tasks: set[asyncio.Task] = set()
orchestrator_jobs = OrchestratorJobStore()
_STARTED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@asynccontextmanager
async def lifespan(_app: FastAPI):  # noqa: ANN201
    """アプリ起動時に定期クリーンアップタスクを開始する."""
    global admission
    admission = AdmissionController(SHARED_DATA_DIR / ".state")
    orchestrator_jobs.configure(SHARED_DATA_DIR / ".state" / "jobs.json")
    orchestrator_jobs.on_change = _persist_progress
    await _reconcile_abandoned()
    task = asyncio.create_task(_periodic_cleanup())
    recovery_task = asyncio.create_task(_periodic_reconcile())
    try:
        yield
    finally:
        task.cancel()
        recovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await recovery_task
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for pipeline in list(_pipeline_tasks):
            pipeline.cancel()
        await asyncio.gather(*list(_pipeline_tasks), return_exceptions=True)
        orchestrator_jobs.on_change = None
        admission.close()
        admission = None


app = FastAPI(
    title="Splat Highlight Pilot",
    description=("スプラトゥーン試合動画ハイライト自動切り出しオーケストレーター"),
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://reisun.github.io"],
    allow_origin_regex=r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?",
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition"],
)


async def _reconcile_abandoned() -> None:
    if admission is None or admission.lock_fd is not None:
        return
    if not admission.try_acquire():
        return
    try:
        if admission.snapshot() is None:
            admission.release()
            return
        safe = await _recover_owned_record()
        admission.release(clear=safe)
    finally:
        admission.close()


async def _periodic_reconcile() -> None:
    while True:
        await asyncio.sleep(15)
        try:
            await _reconcile_abandoned()
        except Exception:  # noqa: BLE001
            logger.exception("中断した処理の状態確認に失敗")


async def _periodic_cleanup() -> None:
    """定期的に古いジョブとファイルを削除する."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            # Preserve input/results while processing or awaiting reconciliation.
            if admission and admission.snapshot() is None:
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


def _download_expires_at(job_id: str) -> str | None:
    """Use the same file mtime and retention age as the cleanup routine."""
    for suffix in (".zip", ".mp4"):
        path = SHARED_DATA_DIR / "results" / f"{job_id}{suffix}"
        try:
            expires = path.stat().st_mtime + CLEANUP_MAX_AGE
        except FileNotFoundError:
            continue
        return datetime.fromtimestamp(expires, UTC).isoformat()
    return None


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
        download_expires_at=(
            _download_expires_at(job.job_id)
            if job.phase == JobPhase.COMPLETED
            else None
        ),
        analysis_url=analysis_url,
        error=job.error,
        started_at=job.started_at,
    )


def _persist_progress(job) -> None:  # noqa: ANN001
    if admission and admission.lock_fd is not None:
        record = admission.snapshot()
        if record and record.get("job_id") == job.job_id:
            admission.update(phase=job.phase.value, job=asdict(job))


def _converter_identity(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        # Field 22 is starttime; comm can contain spaces and parentheses.
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _observe_converter(pid: int | None) -> None:
    if admission and admission.lock_fd is not None:
        admission.update(
            converter_pid=pid, converter_start=_converter_identity(pid) if pid else None
        )


def _converter_state(record: dict | None) -> dict:
    pid = record.get("converter_pid") if record else None
    identity = _converter_identity(pid) if isinstance(pid, int) else None
    return {
        "pid": pid,
        "running": bool(identity and identity == record.get("converter_start")),
        "lock_held": bool(admission and admission.is_locked()),
    }


async def _backend_state() -> dict:
    async with _get_http_client() as client:
        response = await client.get(f"{ANALYZER_URL}/processing", timeout=5)
        response.raise_for_status()
        data = response.json()
        if (
            not isinstance(data, dict)
            or type(data.get("busy")) is not bool
            or not isinstance(data.get("operations"), list)
            or data["busy"] != bool(data["operations"])
        ):
            raise RuntimeError("Invalid analyzer processing state")
        return data


async def _recover_owned_record() -> bool:
    """Caller holds flock. Never treat an unreachable analyzer as idle."""
    assert admission is not None  # noqa: S101
    record = admission.snapshot()
    try:
        actual = await _backend_state()
    except (httpx.HTTPError, ValueError, RuntimeError):
        admission.update(phase="checking", estimated_finish_at=None)
        return False
    admission.update(actual_analyzer=actual, estimated_finish_at=None)
    if actual["busy"]:
        return False
    if record:
        job_id = record.get("job_id")
        job = orchestrator_jobs.get(job_id) if job_id else None
        if job and job.phase not in (JobPhase.COMPLETED, JobPhase.FAILED):
            orchestrator_jobs.mark_failed(
                job_id, "処理が中断されました。再度アップロードしてください。"
            )
        path = record.get("upload_path")
        if path:
            _cleanup_file(Path(path))
    return True


@app.get("/processing")
async def processing_status() -> dict:
    """Compare recorded progress with actual analyzer and converter activity."""
    record = admission.snapshot() if admission else None
    try:
        actual = await _backend_state()
    except (httpx.HTTPError, ValueError, RuntimeError):
        actual = None
    return {
        "busy": bool(record)
        or bool(admission and admission.is_locked())
        or actual is None
        or bool(actual["busy"]),
        "recorded": None
        if not record
        else {
            key: record.get(key)
            for key in (
                "phase",
                "received_bytes",
                "total_bytes",
                "updated_at",
                "backend_job_id",
                "backend_kind",
                "estimated_finish_at",
            )
        },
        "recorded_progress": (record.get("job") or {}).get("analyzer_progress")
        if record
        else None,
        "actual_analyzer": actual,
        "actual_converter": _converter_state(record),
    }


async def _run_owned_pipeline(
    job_id: str, upload_path: Path, opts: AnalyzerOptions
) -> None:
    assert admission is not None  # noqa: S101
    try:
        await _run_pipeline(job_id, upload_path, opts)
    except asyncio.CancelledError:
        orchestrator_jobs.mark_failed(job_id, "処理が中断されました。")
        raise
    finally:
        clip.PROCESS_LOCK_FD = None
        clip.PROCESS_OBSERVER = None
        record = admission.snapshot() or {}
        safe = not record.get("backend_pending", False)
        if not safe:
            try:
                safe = not (await _backend_state())["busy"]
            except (httpx.HTTPError, ValueError, RuntimeError):
                safe = False
        if safe:
            _cleanup_file(upload_path)
        admission.release(clear=safe)


@app.websocket("/ws/upload")
async def ws_upload(websocket: WebSocket) -> None:
    """Admit one bounded upload and hand its lock to the whole processing pipeline."""
    await websocket.accept()
    upload_path: Path | None = None
    owns_lock = False
    handed_off = False
    job_id = None
    try:
        start_msg = json.loads(
            await asyncio.wait_for(websocket.receive_text(), timeout=10)
        )
        if start_msg.get("type") != "start":
            raise ValueError("Expected start message")
        total_size = start_msg.get("size")
        if type(total_size) is not int or not 0 < total_size <= MAX_UPLOAD_BYTES:
            raise ValueError("動画サイズは0バイトより大きく、15GB以下にしてください。")
        filename = start_msg.get("filename", "video.mp4")
        if (
            not isinstance(filename, str)
            or not filename
            or "/" in filename
            or "\\" in filename
        ):
            raise ValueError("Invalid filename")
        opts = AnalyzerOptions(**(start_msg.get("options") or {}))
        if admission is None:
            raise RuntimeError("受付サービスが準備中です。")
        owns_lock = admission.try_acquire()
        if not owns_lock or not await _recover_owned_record():
            await websocket.send_json(
                {
                    "type": "busy",
                    "message": (
                        "現在処理中、または状態確認中です。"
                        "時間をおいて再度アクセスしてください。"
                    ),
                    "estimated_finish_at": None,
                    "retry_after_seconds": 60,
                }
            )
            await websocket.close(code=1013)
            if owns_lock:
                admission.release(clear=False)
                owns_lock = False
            return
        job = orchestrator_jobs.create()
        job_id = job.job_id
        orchestrator_jobs.set_filename(job_id, filename)
        upload_dir = SHARED_DATA_DIR / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / f"{job_id}_{filename}"
        orchestrator_jobs.set_upload_path(job_id, str(upload_path))
        admission.update(
            job_id=job_id,
            phase="uploading",
            received_bytes=0,
            total_bytes=total_size,
            upload_path=str(upload_path),
            backend_pending=False,
            backend_job_id=None,
            backend_kind=None,
            converter_pid=None,
            converter_start=None,
            estimated_finish_at=None,
            job=asdict(job),
        )
        await websocket.send_json({"type": "ready"})
        received = 0
        policy = UploadPolicy(
            total_size,
            clock=time.monotonic,
            max_seconds=UPLOAD_MAX_SECONDS,
            grace=UPLOAD_GRACE_SECONDS,
            slow_seconds=UPLOAD_SLOW_SECONDS,
        )
        with upload_path.open("wb") as f:
            while True:
                remaining = policy.remaining_seconds
                if remaining <= 0:
                    raise ValueError("アップロードが1時間を超えたため中断しました。")
                msg = await asyncio.wait_for(
                    websocket.receive(), timeout=min(UPLOAD_IDLE_SECONDS, remaining)
                )
                if msg["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect()
                if msg.get("text") is not None:
                    control = json.loads(msg["text"])
                    if control.get("type") != "upload_complete":
                        raise ValueError("Unexpected message")
                    if received != total_size:
                        raise ValueError("申告サイズと受信サイズが一致しません。")
                    break
                chunk = msg.get("bytes")
                if not chunk or len(chunk) > MAX_CHUNK_BYTES:
                    raise ValueError("Invalid upload chunk")
                if received + len(chunk) > min(total_size, MAX_UPLOAD_BYTES):
                    raise ValueError("動画の受信量がサイズ上限を超えました。")
                f.write(chunk)
                received += len(chunk)
                try:
                    policy.note_received(received)
                except ValueError as exc:
                    raise ValueError(
                        "完了まで1時間を超えるアップロードのため中断しました。"
                    ) from exc
                admission.update(
                    received_bytes=received,
                    upload_elapsed_seconds=policy.elapsed,
                    estimated_finish_at=None,
                )
                await websocket.send_json(
                    {
                        "type": "progress",
                        "phase": "uploading",
                        "percent": int(received / total_size * 100),
                    }
                )
        clip.PROCESS_LOCK_FD = admission.lock_fd
        clip.PROCESS_OBSERVER = _observe_converter
        task = asyncio.create_task(_run_owned_pipeline(job_id, upload_path, opts))
        _pipeline_tasks.add(task)
        task.add_done_callback(_pipeline_tasks.discard)
        handed_off = True
        owns_lock = False
        await websocket.send_json({"type": "job_created", "job_id": job_id})
        await websocket.close()
    except WebSocketDisconnect:
        if job_id and not handed_off:
            orchestrator_jobs.mark_failed(job_id, "アップロードが中断されました。")
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
        if job_id and not handed_off:
            orchestrator_jobs.mark_failed(
                job_id, str(exc) or "アップロードが中断されました。"
            )
        with contextlib.suppress(Exception):
            message = (
                "受信が停止したためアップロードを中断しました。"
                if isinstance(exc, TimeoutError)
                else str(exc)
            )
            await websocket.send_json({"type": "error", "message": message})
            await websocket.close(code=1008)
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        if not handed_off:
            if upload_path:
                _cleanup_file(upload_path)
            if owns_lock and admission:
                admission.release()


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

            if not analyzer_result:
                logger.warning(
                    "試合 %d/%d で分析結果なし job=%s",
                    i + 1,
                    total_matches,
                    job_id,
                )
                continue

            highlights = analyzer_result.highlights
            all_frames = analyzer_result.frames

            if not highlights:
                logger.info(
                    "試合 %d/%d でハイライト未検出 job=%s",
                    i + 1,
                    total_matches,
                    job_id,
                )

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
            orchestrator_jobs.mark_failed(job_id, "No analysis data from any match")
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

        temp_files = [str(analysis_path)]
        highlight_path = None
        if ma["segments"]:
            highlight_path = match_dir / "highlight.mp4"
            await clip_video_async(
                upload_path, ma["segments"], highlight_path, intro=True
            )
            temp_files.append(str(highlight_path))

        match_outputs.append(
            {
                "match_index": ma["match_index"],
                "highlight_path": str(highlight_path) if highlight_path else None,
                "analysis_path": str(analysis_path),
                "highlights": ma["highlights"],
                "temp_files": temp_files,
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
    all_highlights: list[dict] = []
    analysis_paths: list[str] = []
    for ma in match_analyses:
        all_segments.extend(ma["segments"])
        all_highlights.extend(ma["highlights"])

        analysis_path = temp_dir / f"analysis-match-{ma['match_index']}.json"
        analysis_path.write_text(
            json.dumps(ma["analysis_data"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        analysis_paths.append(str(analysis_path))

    temp_files = list(analysis_paths)
    highlight_path = None
    if all_segments:
        highlight_path = temp_dir / "highlight.mp4"
        await clip_video_async(upload_path, all_segments, highlight_path, intro=True)
        temp_files.append(str(highlight_path))

    return [
        {
            "combined": True,
            "highlight_path": str(highlight_path) if highlight_path else None,
            "analysis_paths": analysis_paths,
            "match_analyses": match_analyses,
            "highlights": all_highlights,
            "temp_files": temp_files,
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

                if mo.get("highlight_path"):
                    highlight_path = Path(mo["highlight_path"])
                    if highlight_path.exists():
                        zf.write(highlight_path, f"highlight-match-{match_idx}.mp4")

                analysis_path = Path(mo["analysis_path"])
                if analysis_path.exists():
                    zf.write(analysis_path, f"analysis/analysis-match-{match_idx}.json")
        else:
            mo = match_outputs[0]
            if mo.get("highlight_path"):
                highlight_path = Path(mo["highlight_path"])
                if highlight_path.exists():
                    zf.write(highlight_path, "highlight.mp4")

            if mo.get("analysis_paths"):
                for ap in mo["analysis_paths"]:
                    ap_path = Path(ap)
                    if ap_path.exists():
                        zf.write(ap_path, f"analysis/{ap_path.name}")
            elif mo.get("analysis_path"):
                analysis_path = Path(mo["analysis_path"])
                if analysis_path.exists():
                    zf.write(analysis_path, f"analysis/{analysis_path.name}")


async def _call_match_scan(
    job_id: str,
    file_path: str,
) -> dict:
    """analyzer の試合境界スキャンAPIを呼び出す.matches と readings を返す."""
    payload = {"file_path": file_path, "interval": 30.0}

    if admission and admission.lock_fd is not None:
        admission.update(
            backend_pending=True, backend_kind="dispatching", backend_job_id=None
        )
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
        if admission and admission.lock_fd is not None:
            admission.update(backend_kind="scan", backend_job_id=scan_job_id)

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

            if (
                scan_status.status in ("completed", "failed")
                and admission
                and admission.lock_fd is not None
            ):
                admission.update(backend_pending=False)
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

    if admission and admission.lock_fd is not None:
        admission.update(
            backend_pending=True, backend_kind="dispatching", backend_job_id=None
        )
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
        if admission and admission.lock_fd is not None:
            admission.update(backend_kind="highlights", backend_job_id=analyzer_job_id)

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

            if (
                job_status.status in ("completed", "failed")
                and admission
                and admission.lock_fd is not None
            ):
                admission.update(backend_pending=False)
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
