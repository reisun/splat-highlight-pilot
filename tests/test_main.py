"""orchestrator API のテスト."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import ANALYZER_URL, CLIPPER_URL, app
from app.schemas import AnalyzerHighlight, AnalyzerResponse


@pytest.fixture
def shared_dir(tmp_path):
    return tmp_path


@pytest.fixture
def client(shared_dir):
    with patch("app.main.SHARED_DATA_DIR", shared_dir), TestClient(app) as c:
        yield c


SAMPLE_HIGHLIGHTS = [
    AnalyzerHighlight(
        start_seconds=10.0, end_seconds=20.0, peak_intensity=8, description="kill"
    ),
    AnalyzerHighlight(
        start_seconds=45.0, end_seconds=55.0, peak_intensity=9, description="ult"
    ),
]

FAKE_MP4 = b"\x00\x00\x00\x1cftypisom"


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


def _send_upload(ws, video_data=b"fake-video-data"):
    ws.send_json({"type": "start", "filename": "test.mp4", "size": len(video_data)})
    ws.send_bytes(video_data)
    upload_progress = ws.receive_json()
    ws.send_json({"type": "upload_complete"})
    return upload_progress


class TestWebSocketHighlight:
    def test_success(self, client):
        mock_analyzer = AsyncMock(return_value=list(SAMPLE_HIGHLIGHTS))
        mock_clipper = AsyncMock(return_value=FAKE_MP4)

        with (
            patch("app.main._call_analyzer", mock_analyzer),
            patch("app.main._call_clipper", mock_clipper),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            upload_progress = _send_upload(ws)
            assert upload_progress["type"] == "progress"
            assert upload_progress["phase"] == "uploading"

            analyzing = ws.receive_json()
            assert analyzing["type"] == "progress"
            assert analyzing["phase"] == "analyzing"

            clipping = ws.receive_json()
            assert clipping["type"] == "progress"
            assert clipping["phase"] == "clipping"

            done = ws.receive_json()
            assert done["type"] == "done"
            assert "download_url" in done
            assert done["download_url"].startswith("/download/")

        job_id = done["download_url"].split("/download/")[1]
        resp = client.get(f"/download/{job_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert resp.content == FAKE_MP4

    def test_no_highlights(self, client):
        mock_analyzer = AsyncMock(return_value=[])

        with (
            patch("app.main._call_analyzer", mock_analyzer),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            analyzing = ws.receive_json()
            assert analyzing["phase"] == "analyzing"

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "No highlights detected" in error["message"]

    def test_analyzer_error(self, client):
        mock_analyzer = AsyncMock(
            side_effect=RuntimeError("analyzer がエラーを返しました (status=500)")
        )

        with (
            patch("app.main._call_analyzer", mock_analyzer),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _analyzing = ws.receive_json()

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "analyzer" in error["message"]

    def test_analyzer_connection_error(self, client):
        mock_analyzer = AsyncMock(
            side_effect=RuntimeError("analyzer への接続に失敗しました")
        )

        with (
            patch("app.main._call_analyzer", mock_analyzer),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _analyzing = ws.receive_json()

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "analyzer" in error["message"]

    def test_clipper_error(self, client):
        mock_analyzer = AsyncMock(return_value=list(SAMPLE_HIGHLIGHTS))
        mock_clipper = AsyncMock(
            side_effect=RuntimeError("clipper がエラーを返しました (status=500)")
        )

        with (
            patch("app.main._call_analyzer", mock_analyzer),
            patch("app.main._call_clipper", mock_clipper),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            _analyzing = ws.receive_json()
            _clipping = ws.receive_json()

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "clipper" in error["message"]


class TestAnalyzerPolling:
    """_call_analyzer のポーリングフローを respx で検証."""

    @respx.mock
    def test_polling_completed(self, client):
        """ジョブ作成 → running → completed のフロー."""
        job_id = "test-job-123"
        result_data = AnalyzerResponse(
            video="test.mp4",
            model="test-model",
            highlights=[h.model_dump() for h in SAMPLE_HIGHLIGHTS],
        )

        # ジョブ作成
        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": job_id})
        )

        # ポーリング: 1回目 running, 2回目 completed
        poll_url = f"{ANALYZER_URL}/analyze/highlights/jobs/{job_id}"
        respx.get(poll_url).mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "job_id": job_id,
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
                        "job_id": job_id,
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

        mock_clipper = AsyncMock(return_value=FAKE_MP4)

        with (
            patch("app.main.POLL_INTERVAL", 0),
            patch("app.main._call_clipper", mock_clipper),
            client.websocket_connect("/ws/highlight") as ws,
        ):
            _send_upload(ws)

            # analyzing 開始
            analyzing = ws.receive_json()
            assert analyzing["phase"] == "analyzing"

            # 1回目ポーリング進捗
            progress1 = ws.receive_json()
            assert progress1["type"] == "progress"
            assert progress1["phase"] == "analyzing"
            assert progress1["detail"]["stage"] == 1
            assert progress1["detail"]["frames_done"] == 5

            # 2回目ポーリング進捗
            progress2 = ws.receive_json()
            assert progress2["type"] == "progress"
            assert progress2["phase"] == "analyzing"
            assert progress2["detail"]["stage"] == 2
            assert progress2["detail"]["frames_done"] == 60

            # clipping フェーズ
            clipping = ws.receive_json()
            assert clipping["phase"] == "clipping"

            done = ws.receive_json()
            assert done["type"] == "done"

    @respx.mock
    def test_polling_failed(self, client):
        """ジョブが failed になった場合のフロー."""
        job_id = "test-job-fail"

        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": job_id})
        )

        poll_url = f"{ANALYZER_URL}/analyze/highlights/jobs/{job_id}"
        respx.get(poll_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": job_id,
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

            _analyzing = ws.receive_json()

            # failed の進捗メッセージ
            _progress = ws.receive_json()

            error = ws.receive_json()
            assert error["type"] == "error"
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

            _analyzing = ws.receive_json()

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "analyzer" in error["message"]


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
