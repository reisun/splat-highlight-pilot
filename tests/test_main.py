"""orchestrator API のテスト."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.job_store import HighlightInfo, JobPhase
from app.main import (
    ANALYZER_URL,
    CLIPPER_URL,
    _flatten_clipped_scores,
    app,
    orchestrator_jobs,
)
from app.schemas import (
    AnalyzerFrameResult,
    AnalyzerHighlight,
    AnalyzerResponse,
)


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


class TestWebSocketUpload:
    """WebSocket はアップロード専用。job_id を返して close する."""

    def test_upload_returns_job_id(self, client):
        """アップロード完了後に job_id を返す."""

        async def mock_pipeline(job_id, upload_path, opts):
            pass

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            client.websocket_connect("/ws/upload") as ws,
        ):
            upload_progress = _send_upload(ws)
            assert upload_progress["type"] == "progress"
            assert upload_progress["phase"] == "uploading"

            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"
            assert "job_id" in job_msg

    def test_invalid_start_message(self, client):
        """start 以外のメッセージでエラーになる."""
        with client.websocket_connect("/ws/upload") as ws:
            ws.send_json({"type": "invalid"})
            error_msg = ws.receive_json()
            assert error_msg["type"] == "error"


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

        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": analyzer_job_id})
        )

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
                            "phase_total": 1,
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
                            "phase": 1,
                            "phase_total": 1,
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
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"
            job_id = job_msg["job_id"]

        for _ in range(50):
            resp = client.get(f"/jobs/{job_id}")
            data = resp.json()
            if data["phase"] == "completed":
                break
            time.sleep(0.05)

        assert data["phase"] == "completed"
        assert data["download_url"] is not None

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
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            job_id = job_msg["job_id"]

        for _ in range(50):
            resp = client.get(f"/jobs/{job_id}")
            data = resp.json()
            if data["phase"] == "failed":
                break
            time.sleep(0.05)

        assert data["phase"] == "failed"
        assert "GPU out of memory" in data["error"]

    @respx.mock
    def test_job_creation_error(self, client):
        """ジョブ作成時にエラーが返った場合."""
        respx.post(f"{ANALYZER_URL}/analyze/highlights/jobs").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        with (
            patch("app.main.POLL_INTERVAL", 0),
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            job_id = job_msg["job_id"]

        for _ in range(50):
            resp = client.get(f"/jobs/{job_id}")
            data = resp.json()
            if data["phase"] == "failed":
                break
            time.sleep(0.05)

        assert data["phase"] == "failed"
        assert "analyzer" in data["error"]


class TestGetJobStatus:
    """GET /jobs/{job_id} のテスト."""

    def test_get_existing_job(self, client):
        """既存ジョブの状態を取得できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_phase(job.job_id, JobPhase.ANALYZING)
        orchestrator_jobs.update_analyzer_progress(
            job.job_id,
            stage=1,
            stage_total=1,
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

    def test_get_completed_job(self, client):
        """完了ジョブの情報を取得できる."""
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
        assert data["analysis_url"] == f"/download/{job.job_id}/analysis"

    def test_job_not_found(self, client):
        """存在しないジョブIDで404."""
        resp = client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404


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

    def test_file_persists_after_download(self, client, shared_dir):
        """ダウンロード後もファイルが残る（複数回DL可能）."""
        results_dir = shared_dir / "results"
        results_dir.mkdir(exist_ok=True)
        result_file = results_dir / "persist-test.mp4"
        result_file.write_bytes(FAKE_MP4)

        client.get("/download/persist-test")
        assert result_file.exists()

        resp2 = client.get("/download/persist-test")
        assert resp2.status_code == 200

    def test_not_found(self, client):
        resp = client.get("/download/nonexistent-id")
        assert resp.status_code == 404


class TestDownloadAnalysis:
    def test_success(self, client, shared_dir):
        results_dir = shared_dir / "results"
        results_dir.mkdir()
        analysis_file = results_dir / "test-job-id_analysis.json"
        analysis_file.write_text("[]", encoding="utf-8")

        resp = client.get("/download/test-job-id/analysis")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"

    def test_not_found(self, client):
        resp = client.get("/download/nonexistent-id/analysis")
        assert resp.status_code == 404


class TestCleanup:
    """自動クリーンアップのテスト."""

    def test_cleanup_removes_old_jobs_and_files(self, shared_dir):
        """期限切れジョブとファイルが削除される."""
        results_dir = shared_dir / "results"
        results_dir.mkdir()

        job = orchestrator_jobs.create()
        orchestrator_jobs.mark_completed(job.job_id, f"/download/{job.job_id}")
        job_obj = orchestrator_jobs.get(job.job_id)
        job_obj.completed_at = time.time() - 7200

        mp4_file = results_dir / f"{job.job_id}.mp4"
        analysis_file = results_dir / f"{job.job_id}_analysis.json"
        mp4_file.write_bytes(FAKE_MP4)
        analysis_file.write_text("[]", encoding="utf-8")

        removed = orchestrator_jobs.cleanup_old(results_dir, max_age_seconds=3600)

        assert removed == 1
        assert orchestrator_jobs.get(job.job_id) is None
        assert not mp4_file.exists()
        assert not analysis_file.exists()

    def test_cleanup_keeps_recent_jobs(self, shared_dir):
        """期限内のジョブは削除されない."""
        results_dir = shared_dir / "results"
        results_dir.mkdir()

        job = orchestrator_jobs.create()
        orchestrator_jobs.mark_completed(job.job_id, f"/download/{job.job_id}")

        mp4_file = results_dir / f"{job.job_id}.mp4"
        mp4_file.write_bytes(FAKE_MP4)

        removed = orchestrator_jobs.cleanup_old(results_dir, max_age_seconds=3600)

        assert removed == 0
        assert orchestrator_jobs.get(job.job_id) is not None
        assert mp4_file.exists()


class TestFlattenClippedScores:
    """クリップ済み区間のスコア平坦化テスト."""

    def _make_frames(
        self, scores: list[tuple[float, int, int]]
    ) -> list[AnalyzerFrameResult]:
        return [
            AnalyzerFrameResult(timestamp_seconds=ts, score=sc, score_gain=sg)
            for ts, sc, sg in scores
        ]

    def test_clipped_region_replaced_with_average(self):
        frames = self._make_frames(
            [
                (0.0, 2, 1),
                (5.0, 4, 2),
                (10.0, 8, 6),
                (15.0, 10, 8),
                (20.0, 6, 3),
                (25.0, 2, 1),
            ]
        )
        highlights = [AnalyzerHighlight(start_seconds=10.0, end_seconds=20.0)]

        _flatten_clipped_scores(frames, highlights)

        avg_score = (2 + 4 + 8 + 10 + 6 + 2) // 6  # 5
        avg_gain = (1 + 2 + 6 + 8 + 3 + 1) // 6  # 3
        assert frames[0].score == 2  # 区間外: 変更なし
        assert frames[1].score == 4  # 区間外: 変更なし
        assert frames[2].score == avg_score  # 区間内: 平均に置換
        assert frames[3].score == avg_score  # 区間内: 平均に置換
        assert frames[4].score == avg_score  # 区間内: 平均に置換
        assert frames[5].score == 2  # 区間外: 変更なし
        assert frames[2].score_gain == avg_gain
        assert frames[3].score_gain == avg_gain
        assert frames[4].score_gain == avg_gain

    def test_multiple_highlights(self):
        frames = self._make_frames(
            [
                (0.0, 2, 1),
                (5.0, 10, 8),
                (10.0, 2, 1),
                (15.0, 10, 8),
                (20.0, 2, 1),
            ]
        )
        highlights = [
            AnalyzerHighlight(start_seconds=5.0, end_seconds=5.0),
            AnalyzerHighlight(start_seconds=15.0, end_seconds=15.0),
        ]

        _flatten_clipped_scores(frames, highlights)

        avg_score = (2 + 10 + 2 + 10 + 2) // 5  # 5
        assert frames[0].score == 2
        assert frames[1].score == avg_score
        assert frames[2].score == 2
        assert frames[3].score == avg_score
        assert frames[4].score == 2

    def test_empty_frames(self):
        highlights = [AnalyzerHighlight(start_seconds=0, end_seconds=10)]
        _flatten_clipped_scores([], highlights)

    def test_no_highlights(self):
        frames = self._make_frames([(0.0, 5, 3), (5.0, 10, 7)])
        _flatten_clipped_scores(frames, [])
        assert frames[0].score == 5
        assert frames[1].score == 10
