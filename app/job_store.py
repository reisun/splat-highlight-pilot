"""オーケストレーターのインメモリジョブストア."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


class JobPhase(StrEnum):
    UPLOADING = "uploading"
    ANALYZING = "analyzing"
    CLIPPING = "clipping"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalyzerProgress:
    stage: int = 0
    stage_total: int = 2
    frames_done: int = 0
    frames_total: int = 0


@dataclass
class HighlightInfo:
    start_seconds: float
    end_seconds: float
    peak_intensity: int = 0
    description: str = ""


@dataclass
class OrchestratorJob:
    job_id: str
    phase: JobPhase = JobPhase.UPLOADING
    analyzer_progress: AnalyzerProgress = field(default_factory=AnalyzerProgress)
    highlights: list[HighlightInfo] = field(default_factory=list)
    download_url: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    upload_path: str | None = None


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

    def cleanup_old(self, max_age_seconds: float = 3600) -> None:
        now = time.time()
        with self._lock:
            to_remove = [
                jid
                for jid, j in self._jobs.items()
                if j.completed_at and (now - j.completed_at) > max_age_seconds
            ]
            for jid in to_remove:
                del self._jobs[jid]
