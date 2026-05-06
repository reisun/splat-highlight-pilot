"""orchestrator API のテスト."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.job_store import HighlightInfo, JobPhase
from app.main import ANALYZER_URL, CLIPPER_URL, app, orchestrator_jobs
from app.schemas import AnalyzerHighlight, AnalyzerResponse


@pytest.fixture
def shared_dir(tmp_path):
    return tmp_path


@pytest.fixture
def client(shared_dir):
    with patch("app.main.SHARED_DATA_DIR", shared_dir), TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_jobs():
    """各テスト前後にジョブストアをクリアする."""
    orchestrator_jobs._jobs.clear()
    yield
    orchestrator_jobs._jobs.clear()


SAMPLE_HIGHLIGHTS = [
    AnalyzerHighlight(
        start_seconds=10.0, end_seconds=20.0, peak_intensity=8, description="kill"
    ),
    AnalyzerHighlight(
        start_seconds=45.0, end_seconds=55.0, peak_intensity=9, description="ult"
    ),
]

FAKE_MP4 = b"\x00\x00\x00\x1cftypisom"


def _send_upload(ws, video_data=b"fake-video-data"):
    ws.send_json({"type": "start", "filename": "test.mp4", "size": len(video_data)})
    ws.send_bytes(video_data)
    upload_progress = ws.receive_json()
    ws.send_json({"type": "upload_complete"})
    return upload_progress


def _receive_until_type(ws, msg_type, max_messages=50):
    """指定タイプのメッセージが来るまで受信する."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if msg["type"] == msg_type:
            return msg
    raise AssertionError(f"Did not receive message type '{msg_type}'")


class TestHealth:
    @respx.mock
    def test_all_connected(self, client):
        respx.get(f"{ANALYZER_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        respx.get(f"{CLIPPER_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["services"]) == 2
        assert all(s["status"] == "connected" for s in data["services"])

    @respx.mock
    def test_services_down(self, client):
        respx.get(f"{ANALYZER_URL}/health").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        respx.get(f"{CLIPPER_URL}/health").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert all(s["status"] == "disconnected" for s in data["services"])


class TestWebSocketHighlight:
    def test_success(self, client, shared_dir):
        """アップロード -> ジョブ作成 -> 完了のフロー."""

        async def mock_pipeline(job_id, upload_path, opts):
            orchestrator_jobs.set_phase(job_id, JobPhase.ANALYZING)
            await asyncio.sleep(0)
            orchestrator_jobs.set_highlights(
                job_id,
                [
                    HighlightInfo(
                        start_seconds=10.0,
                        end_seconds=20.0,
                        peak_intensity=8,
                        description="kill",
                    ),
                ],
            )
            orchestrator_jobs.mark_completed(job_id, f"/download/{job_id}")

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            upload_progress = _send_upload(ws)
            assert upload_progress["type"] == "progress"
            assert upload_progress["phase"] == "uploading"

            # job_created
            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"
            assert "job_id" in job_msg

            # done (skip intermediate progress messages)
            done = _receive_until_type(ws, "done")
            assert "download_url" in done
            assert "highlights" in done
            assert len(done["highlights"]) >= 1
            assert done["highlights"][0]["start_seconds"] == 10.0

    def test_no_highlights(self, client):
        """ハイライトが検出されなかった場合."""

        async def mock_pipeline(job_id, upload_path, opts):
            orchestrator_jobs.set_phase(job_id, JobPhase.ANALYZING)
            await asyncio.sleep(0)
            orchestrator_jobs.mark_failed(job_id, "No highlights detected")

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            # job_created
            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"

            # error
            error = _receive_until_type(ws, "error")
            assert "No highlights detected" in error["message"]

    def test_pipeline_error(self, client):
        """パイプライン中にエラーが発生した場合."""

        async def mock_pipeline(job_id, upload_path, opts):
            orchestrator_jobs.mark_failed(
                job_id, "analyzer がエラーを返しました (status=500)"
            )

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _job_msg = ws.receive_json()

            error = _receive_until_type(ws, "error")
            assert "analyzer" in error["message"]


class TestAnalyzerPolling:
    """_call_analyzer_background のポーリングフローを respx で検証."""

    @respx.mock
    def test_polling_completed(self, client, shared_dir):
        """ジョブ作成 -> running -> completed のフロー."""
        analyzer_job_id = "test-job-123"
        result_data = AnalyzerResponse(
            video="test.mp4",
            model="test-model",
            highlights=[h.model_dump() for h in SAMPLE_HIGHLIGHTS],
        )

        # ジョブ作成
        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": analyzer_job_id})
        )

        # ポーリング: 1回目 running, 2回目 completed
        poll_url = f"{ANALYZER_URL}/analyze/highlights/jobs/{analyzer_job_id}"
        respx.get(poll_url).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "job_id": analyzer_job_id,
                        "status": "running",
                        "progress": {
                            "phase": 1,
                            "phase_total": 2,
                            "frames_done": 5,
                            "frames_total": 60,
                        },
                        "result": None,
                        "error": None,
                        "started_at": 1234567890.0,
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "job_id": analyzer_job_id,
                        "status": "completed",
                        "progress": {
                            "phase": 2,
                            "phase_total": 2,
                            "frames_done": 60,
                            "frames_total": 60,
                        },
                        "result": result_data.model_dump(),
                        "error": None,
                        "started_at": 1234567890.0,
                    },
                ),
            ]
        )

        mock_clipper = AsyncMock(return_value=None)

        with (
            patch("app.main.POLL_INTERVAL", 0),
            patch("app.main._call_clipper_background", mock_clipper),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            # job_created
            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"

            # done (skip intermediate progress messages)
            done = _receive_until_type(ws, "done")
            assert done["type"] == "done"
            assert "download_url" in done
            assert "highlights" in done
            assert len(done["highlights"]) == 2

    @respx.mock
    def test_polling_failed(self, client):
        """ジョブが failed になった場合のフロー."""
        analyzer_job_id = "test-job-fail"

        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": analyzer_job_id})
        )

        poll_url = f"{ANALYZER_URL}/analyze/highlights/jobs/{analyzer_job_id}"
        respx.get(poll_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": analyzer_job_id,
                    "status": "failed",
                    "progress": None,
                    "result": None,
                    "error": "GPU out of memory",
                    "started_at": 1234567890.0,
                },
            )
        )

        with (
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _job_msg = ws.receive_json()

            error = _receive_until_type(ws, "error")
            assert "GPU out of memory" in error["message"]

    @respx.mock
    def test_job_creation_error(self, client):
        """ジョブ作成時にエラーが返った場合."""
        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with (
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _job_msg = ws.receive_json()

            error = _receive_until_type(ws, "error")
            assert "analyzer" in error["message"]


class TestGetJobStatus:
    """GET /jobs/{job_id} のテスト."""

    def test_get_existing_job(self, client):
        """既存ジョブの状態を取得できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_phase(job.job_id, JobPhase.ANALYZING)
        orchestrator_jobs.update_analyzer_progress(
            job.job_id,
            stage=1,
            stage_total=2,
            frames_done=10,
            frames_total=100,
        )

        resp = client.get(f"/jobs/{job.job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job.job_id
        assert data["phase"] == "analyzing"
        assert data["analyzer_progress"]["stage"] == 1
        assert data["analyzer_progress"]["frames_done"] == 10

    def test_get_completed_job_with_highlights(self, client):
        """完了ジョブのハイライト情報を取得できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_highlights(
            job.job_id,
            [
                HighlightInfo(
                    start_seconds=10.0,
                    end_seconds=20.0,
                    peak_intensity=8,
                    description="kill",
                ),
            ],
        )
        orchestrator_jobs.mark_completed(job.job_id, "/download/test")

        resp = client.get(f"/jobs/{job.job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "completed"
        assert data["download_url"] == "/download/test"
        assert len(data["highlights"]) == 1
        assert data["highlights"][0]["start_seconds"] == 10.0
        assert data["highlights"][0]["peak_intensity"] == 8

    def test_job_not_found(self, client):
        """存在しないジョブIDで404."""
        resp = client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404


class TestJobRecovery:
    """WebSocket復帰フローのテスト."""

    def test_resume_completed_job(self, client):
        """完了済みジョブに復帰できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_highlights(
            job.job_id,
            [
                HighlightInfo(
                    start_seconds=10.0,
                    end_seconds=20.0,
                    peak_intensity=8,
                    description="kill",
                ),
            ],
        )
        orchestrator_jobs.mark_completed(job.job_id, f"/download/{job.job_id}")

        with (
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            ws.send_json(
                {
                    "type": "start",
                    "filename": "test.mp4",
                    "size": 100,
                    "job_id": job.job_id,
                }
            )

            # 完了ジョブなので progress(completed) + done が来る
            done = _receive_until_type(ws, "done")
            assert done["download_url"] == f"/download/{job.job_id}"
            assert len(done["highlights"]) == 1

    def test_resume_nonexistent_job_falls_through(self, client):
        """存在しないジョブIDで復帰しようとすると通常フローに入る."""

        async def mock_pipeline(job_id, upload_path, opts):
            orchestrator_jobs.mark_completed(job_id, f"/download/{job_id}")

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            ws.send_json(
                {
                    "type": "start",
                    "filename": "test.mp4",
                    "size": len(b"fake"),
                    "job_id": "nonexistent-id",
                }
            )
            ws.send_bytes(b"fake")
            _upload_progress = ws.receive_json()
            ws.send_json({"type": "upload_complete"})

            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"

            done = _receive_until_type(ws, "done")
            assert done["type"] == "done"


class TestDownload:
    def test_success(self, client, shared_dir):
        results_dir = shared_dir / "results"
        results_dir.mkdir()
        result_file = results_dir / "test-job-id.mp4"
        result_file.write_bytes(FAKE_MP4)

        resp = client.get("/download/test-job-id")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == FAKE_MP4

    def test_not_found(self, client):
        resp = client.get("/download/nonexistent-id")
        assert resp.status_code == 404
