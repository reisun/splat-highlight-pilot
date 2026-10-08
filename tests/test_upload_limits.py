"""Upload admission, bounded reception, and restart recovery integration tests."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.admission import AdmissionController
from app.job_store import JobPhase, OrchestratorJobStore


@pytest.fixture
def backend_state():
    with patch(
        "app.main._backend_state",
        AsyncMock(return_value={"busy": False, "operations": []}),
    ) as state:
        yield state


@pytest.fixture
def client(tmp_path, backend_state):
    with patch("app.main.SHARED_DATA_DIR", tmp_path), TestClient(main.app) as client:
        yield client
    main.orchestrator_jobs._storage_path = None
    main.orchestrator_jobs._jobs.clear()


def start(ws, size=10):
    ws.send_json({"type": "start", "filename": "video.mp4", "size": size})
    return ws.receive_json()


def assert_released(client, tmp_path):
    assert client.get("/processing").json()["busy"] is False
    assert not list((tmp_path / "uploads").glob("*"))
    assert main.admission.lock_fd is None
    assert main.admission.snapshot() is None


@pytest.mark.parametrize("size", [15_000_000_001, True, False, 0, -1, 1.5, "10", None])
def test_invalid_size_rejected_before_job_creation(client, size):
    before = set(main.orchestrator_jobs._jobs)
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws, size)["type"] == "error"
    assert set(main.orchestrator_jobs._jobs) == before
    assert main.admission.lock_fd is None


def test_15gb_boundary_admitted_without_allocating_video(client, tmp_path):
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws, 15_000_000_000)["type"] == "ready"
    assert_released(client, tmp_path)


@pytest.mark.parametrize("data", [b"", b"abc", b"abcdef"])
def test_size_mismatch_cleans_file_and_releases(client, tmp_path, data):
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws, 5)["type"] == "ready"
        if data:
            ws.send_bytes(data)
            response = ws.receive_json()
        else:
            response = None
        if response is None or response["type"] == "progress":
            ws.send_json({"type": "upload_complete"})
            response = ws.receive_json()
        assert response["type"] == "error"
    assert_released(client, tmp_path)
    assert next(iter(main.orchestrator_jobs._jobs.values())).phase == JobPhase.FAILED


def test_oversized_message_rejected_before_write(client, tmp_path):
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws, main.MAX_CHUNK_BYTES + 1)["type"] == "ready"
        ws.send_bytes(b"x" * (main.MAX_CHUNK_BYTES + 1))
        assert ws.receive_json()["type"] == "error"
    assert_released(client, tmp_path)


def test_concurrent_upload_rejected_without_ticket(client, tmp_path):
    with client.websocket_connect("/ws/upload") as first:
        assert start(first)["type"] == "ready"
        with client.websocket_connect("/ws/upload") as second:
            response = start(second)
            assert response["type"] == "busy"
            assert "再度アクセス" in response["message"]
            assert "job_id" not in response
            assert response["estimated_finish_at"] is None
        assert len(main.orchestrator_jobs._jobs) == 1
        assert client.get("/processing").json()["recorded"]["phase"] == "uploading"
    assert_released(client, tmp_path)


def test_disconnect_after_partial_receive_cleans_file(client, tmp_path):
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws)["type"] == "ready"
        ws.send_bytes(b"abc")
        assert ws.receive_json()["percent"] == 30
    assert_released(client, tmp_path)


def test_pipeline_keeps_lock_until_processing_finishes(client, tmp_path):
    finished = threading.Event()

    async def pipeline(job_id, _path, _opts):
        main.orchestrator_jobs.set_phase(job_id, JobPhase.CLIPPING)
        while not finished.is_set():
            await asyncio.sleep(0.005)
        main.orchestrator_jobs.mark_completed(job_id, f"/download/{job_id}")

    with patch("app.main._run_pipeline", pipeline):
        with client.websocket_connect("/ws/upload") as ws:
            assert start(ws, 3)["type"] == "ready"
            ws.send_bytes(b"abc")
            assert ws.receive_json()["percent"] == 100
            ws.send_json({"type": "upload_complete"})
            job = ws.receive_json()
            assert job["type"] == "job_created"
        try:
            with client.websocket_connect("/ws/upload") as other:
                assert start(other)["type"] == "busy"
            assert len(main.orchestrator_jobs._jobs) == 1
            assert main.admission.lock_fd is not None
        finally:
            finished.set()
        for _ in range(100):
            if not client.get("/processing").json()["busy"]:
                break
            time.sleep(0.005)
        assert_released(client, tmp_path)
        assert main.orchestrator_jobs.get(job["job_id"]).phase == JobPhase.COMPLETED


def test_receive_idle_timeout_cleans_file(client, tmp_path):
    with (
        patch("app.main.UPLOAD_IDLE_SECONDS", 0.02),
        client.websocket_connect("/ws/upload") as ws,
    ):
        assert start(ws)["type"] == "ready"
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "受信が停止" in error["message"]
    assert_released(client, tmp_path)


def test_slow_upload_aborts_after_grace_and_sustained_slowness(client, tmp_path):
    clock = SimpleNamespace(monotonic=Mock(return_value=0))
    with (
        patch("app.main.time", clock),
        client.websocket_connect("/ws/upload") as ws,
    ):
        assert start(ws, 1000)["type"] == "ready"
        ws.send_bytes(b"initial")
        assert ws.receive_json()["type"] == "progress"
        clock.monotonic.return_value = 120
        ws.send_bytes(b"a")
        assert ws.receive_json()["type"] == "progress"
        clock.monotonic.return_value = 180
        ws.send_bytes(b"b")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "1時間" in error["message"]
    assert_released(client, tmp_path)


def test_hour_limit_aborts_without_waiting_an_hour(client, tmp_path):
    clock = SimpleNamespace(monotonic=Mock(return_value=0))
    with (
        patch("app.main.time", clock),
        client.websocket_connect("/ws/upload") as ws,
    ):
        assert start(ws)["type"] == "ready"
        ws.send_bytes(b"a")
        assert ws.receive_json()["type"] == "progress"
        clock.monotonic.return_value = 3600
        ws.send_bytes(b"b")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "1時間" in error["message"]
    assert_released(client, tmp_path)


def seed_interrupted_job(tmp_path):
    upload = tmp_path / "uploads" / "interrupted.mp4"
    upload.parent.mkdir()
    upload.write_bytes(b"partial")
    store = OrchestratorJobStore()
    store.configure(tmp_path / ".state" / "jobs.json")
    job = store.create()
    store.set_upload_path(job.job_id, str(upload))
    store.set_phase(job.job_id, JobPhase.ANALYZING)
    controller = AdmissionController(tmp_path / ".state")
    assert controller.try_acquire()
    controller.update(
        job_id=job.job_id,
        phase="analyzing",
        upload_path=str(upload),
        backend_job_id="old-analysis",
        backend_pending=True,
    )
    controller.close()
    return job.job_id, upload


@pytest.mark.parametrize("unavailable", [False, True])
def test_restart_reconciles_before_accepting(tmp_path, backend_state, unavailable):
    job_id, upload = seed_interrupted_job(tmp_path)
    actual = {"busy": True, "operations": [{"job_id": "old-analysis"}]}
    if unavailable:
        backend_state.side_effect = httpx.ConnectError("unavailable")
    else:
        backend_state.return_value = actual
    with patch("app.main.SHARED_DATA_DIR", tmp_path), TestClient(main.app) as client:
        assert main.orchestrator_jobs.get(job_id).phase == JobPhase.ANALYZING
        with client.websocket_connect("/ws/upload") as ws:
            assert start(ws)["type"] == "busy"
        assert len(main.orchestrator_jobs._jobs) == 1
        assert upload.exists()
        status = client.get("/processing").json()
        assert status["busy"] is True
        assert status["actual_analyzer"] == (None if unavailable else actual)
        backend_state.side_effect = None
        backend_state.return_value = {"busy": False, "operations": []}
        with client.websocket_connect("/ws/upload") as ws:
            assert start(ws)["type"] == "ready"
            assert main.orchestrator_jobs.get(job_id).phase == JobPhase.FAILED
            assert not upload.exists()
        assert_released(client, tmp_path)
    main.orchestrator_jobs._storage_path = None
    main.orchestrator_jobs._jobs.clear()


def test_jobs_restore_across_store_reconfiguration(tmp_path):
    path = tmp_path / "jobs.json"
    original = OrchestratorJobStore()
    original.configure(path)
    job = original.create()
    original.set_filename(job.job_id, "tournament.mp4")
    original.update_analyzer_progress(job.job_id, 1, 2, 50, 100)
    original.set_phase(job.job_id, JobPhase.ANALYZING)
    restored = OrchestratorJobStore()
    restored.configure(path)
    recovered = restored.get(job.job_id)
    assert recovered.filename == "tournament.mp4"
    assert recovered.phase == JobPhase.ANALYZING
    assert recovered.analyzer_progress.frames_done == 50


def test_temporary_slowness_resets_after_speed_recovers(client, tmp_path):
    clock = SimpleNamespace(monotonic=Mock(return_value=0))
    with (
        patch("app.main.time", clock),
        client.websocket_connect("/ws/upload") as ws,
    ):
        assert start(ws, 1000)["type"] == "ready"
        ws.send_bytes(b"initial")
        assert ws.receive_json()["type"] == "progress"
        for elapsed, chunk in ((120, b"a"), (130, b"b" * 99), (700, b"c"), (750, b"d")):
            clock.monotonic.return_value = elapsed
            ws.send_bytes(chunk)
            assert ws.receive_json()["type"] == "progress"
    assert_released(client, tmp_path)


def test_pipeline_retains_state_if_remote_work_may_continue(
    client, tmp_path, backend_state
):
    backend_state.return_value = {"busy": False, "operations": []}

    async def pipeline(job_id, _path, _opts):
        main.admission.update(backend_pending=True, backend_job_id="remote-work")
        main.orchestrator_jobs.mark_failed(job_id, "remote status uncertain")
        backend_state.return_value = {
            "busy": True,
            "operations": [{"job_id": "remote-work"}],
        }

    with (
        patch("app.main._run_pipeline", pipeline),
        client.websocket_connect("/ws/upload") as ws,
    ):
        assert start(ws, 3)["type"] == "ready"
        ws.send_bytes(b"abc")
        assert ws.receive_json()["percent"] == 100
        ws.send_json({"type": "upload_complete"})
        assert ws.receive_json()["type"] == "job_created"
    assert client.get("/processing").json()["busy"] is True
    assert len(list((tmp_path / "uploads").glob("*"))) == 1
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws)["type"] == "busy"
    assert len(main.orchestrator_jobs._jobs) == 1
    backend_state.return_value = {"busy": False, "operations": []}
    with client.websocket_connect("/ws/upload") as ws:
        assert start(ws)["type"] == "ready"
    assert_released(client, tmp_path)
