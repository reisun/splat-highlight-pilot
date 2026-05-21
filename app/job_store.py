"""オーケストレーターのインメモリジョブストア."""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class JobPhase(StrEnum):
    UPLOADING = "uploading"
    SCANNING = "scanning"
    ANALYZING = "analyzing"
    CLIPPING = "clipping"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalyzerProgress:
    stage: int = 0
    stage_total: int = 1
    frames_done: int = 0
    frames_total: int = 0


@dataclass
class MatchProgress:
    """試合ごとの進捗."""

    current_match: int = 0
    total_matches: int = 0


@dataclass
class FrameInfo:
    timestamp_seconds: float
    score: int = 0
    score_kills: int = 0
    score_count_gain: int = 0
    score_dead: int = 0
    my_team_count: int | None = None
    enemy_team_count: int | None = None
    kills: int = 0
    is_dead: bool = False
    my_team_count_raw: int | None = None
    enemy_team_count_raw: int | None = None


@dataclass
class HighlightInfo:
    start_seconds: float
    end_seconds: float
    peak_intensity: int = 0


@dataclass
class OrchestratorJob:
    job_id: str
    phase: JobPhase = JobPhase.UPLOADING
    analyzer_progress: AnalyzerProgress = field(default_factory=AnalyzerProgress)
    match_progress: MatchProgress = field(default_factory=MatchProgress)
    highlights: list[HighlightInfo] = field(default_factory=list)
    download_url: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    upload_path: str | None = None
    filename: str | None = None


class OrchestratorJobStore:
    """インメモリのスレッドセーフなジョブストア."""

    def __init__(self) -> None:
        self._jobs: dict[str, OrchestratorJob] = {}
        self._lock = threading.Lock()

    def create(self) -> OrchestratorJob:
        job_id = str(uuid.uuid4())
        job = OrchestratorJob(job_id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> OrchestratorJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def set_phase(self, job_id: str, phase: JobPhase) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.phase = phase

    def set_upload_path(self, job_id: str, path: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.upload_path = path

    def set_filename(self, job_id: str, filename: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.filename = filename

    def update_analyzer_progress(
        self,
        job_id: str,
        stage: int,
        stage_total: int,
        frames_done: int,
        frames_total: int,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.analyzer_progress = AnalyzerProgress(
                    stage=stage,
                    stage_total=stage_total,
                    frames_done=frames_done,
                    frames_total=frames_total,
                )

    def update_match_progress(
        self,
        job_id: str,
        current_match: int,
        total_matches: int,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.match_progress = MatchProgress(
                    current_match=current_match,
                    total_matches=total_matches,
                )

    def set_highlights(self, job_id: str, highlights: list[HighlightInfo]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.highlights = highlights

    def mark_completed(self, job_id: str, download_url: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.phase = JobPhase.COMPLETED
                job.download_url = download_url
                job.completed_at = time.time()

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.phase = JobPhase.FAILED
                job.error = error
                job.completed_at = time.time()

    def cleanup_old(
        self,
        shared_data_dir: Path,
        max_age_seconds: float = 3600,
    ) -> int:
        """期限切れジョブとmtimeベースで古いファイルを削除する."""
        now = time.time()
        removed_jobs = self._cleanup_expired_jobs(max_age_seconds, now)
        removed_files = _cleanup_old_files(shared_data_dir, max_age_seconds, now)
        total = removed_jobs + removed_files
        if total:
            logger.info(
                "クリーンアップ完了: ジョブ%d件, ファイル%d件削除",
                removed_jobs,
                removed_files,
            )
        return total

    def _cleanup_expired_jobs(self, max_age_seconds: float, now: float) -> int:
        removed = 0
        with self._lock:
            to_remove = [
                jid
                for jid, j in self._jobs.items()
                if j.completed_at and (now - j.completed_at) > max_age_seconds
            ]
            for jid in to_remove:
                del self._jobs[jid]
                removed += 1
        return removed


def _cleanup_old_files(
    shared_data_dir: Path,
    max_age_seconds: float,
    now: float,
) -> int:
    """shared_data_dir配下のuploads/results/tmpから古いファイルを削除する."""
    removed = 0
    for subdir in ("results", "uploads", "tmp"):
        target = shared_data_dir / subdir
        if not target.is_dir():
            continue
        for entry in list(target.iterdir()):
            try:
                mtime = entry.stat().st_mtime
                if (now - mtime) <= max_age_seconds:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
            except OSError:
                logger.warning("ファイル削除失敗: %s", entry)
    return removed
