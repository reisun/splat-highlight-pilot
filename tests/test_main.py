"""orchestrator API のテスト."""

from __future__ import annotations

import time
import zipfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.job_store import HighlightInfo, JobPhase
from app.main import (
    ANALYZER_URL,
    _build_zip,
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
    with (
        patch("app.main.SHARED_DATA_DIR", shared_dir),
        TestClient(app) as c,
    ):
        yield c


@pytest.fixture(autouse=True)
def _clear_jobs():
    """各テスト前後にジョブストアをクリアする."""
    orchestrator_jobs._jobs.clear()
    yield
    orchestrator_jobs._jobs.clear()


SAMPLE_HIGHLIGHTS = [
    AnalyzerHighlight(start_seconds=10.0, end_seconds=20.0, peak_intensity=8),
    AnalyzerHighlight(start_seconds=45.0, end_seconds=55.0, peak_intensity=9),
]

FAKE_MP4 = b"\x00\x00\x00\x1cftypisom"
FAKE_ZIP = b"PK\x03\x04fake-zip"


def _send_upload(ws, video_data=b"fake-video-data"):
    ws.send_json(
        {
            "type": "start",
            "filename": "test.mp4",
            "size": len(video_data),
        }
    )
    ws.send_bytes(video_data)
    upload_progress = ws.receive_json()
    ws.send_json({"type": "upload_complete"})
    return upload_progress


def _mock_scan_responses(analyzer_job_id="scan-job-123"):
    """試合境界スキャンのモックレスポンスを設定する."""
    respx.post(f"{ANALYZER_URL}/analyze/matches/scan/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": analyzer_job_id})
    )
    scan_poll_url = f"{ANALYZER_URL}/analyze/matches/scan/jobs/{analyzer_job_id}"
    respx.get(scan_poll_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": analyzer_job_id,
                "status": "completed",
                "progress": {
                    "frames_done": 10,
                    "frames_total": 10,
                },
                "result": {
                    "matches": [
                        {
                            "start_seconds": 0.0,
                            "duration_seconds": 300,
                            "duration_type": "5min",
                        },
                    ],
                },
                "error": None,
                "started_at": 1234567890.0,
            },
        )
    )


def _mock_highlight_responses(analyzer_job_id="highlight-job-123"):
    """ハイライト分析のモックレスポンスを設定する."""
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
        return_value=httpx.Response(
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
        )
    )


class TestHealth:
    @respx.mock
    def test_all_connected(self, client):
        respx.get(f"{ANALYZER_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["services"]) == 1
        assert all(s["status"] == "connected" for s in data["services"])

    @respx.mock
    def test_services_down(self, client):
        respx.get(f"{ANALYZER_URL}/health").mock(
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

    def test_upload_stores_filename(self, client):
        """アップロード時にfilenameがジョブに保存される."""

        async def mock_pipeline(job_id, upload_path, opts):
            pass

        with (
            patch("app.main._run_pipeline", mock_pipeline),
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            job_id = job_msg["job_id"]

        job = orchestrator_jobs.get(job_id)
        assert job is not None
        assert job.filename == "test.mp4"

    def test_invalid_start_message(self, client):
        """start 以外のメッセージでエラーになる."""
        with client.websocket_connect("/ws/upload") as ws:
            ws.send_json({"type": "invalid"})
            error_msg = ws.receive_json()
            assert error_msg["type"] == "error"


class TestMultiMatchPipeline:
    """複数試合パイプラインのテスト."""

    @respx.mock
    def test_pipeline_completed(self, client, shared_dir):
        """scan -> analyze -> clip -> zip の正常フロー."""
        _mock_scan_responses()
        _mock_highlight_responses()
        mock_clip = AsyncMock(return_value=None)

        with (
            patch("app.main.POLL_INTERVAL", 0),
            patch("app.main.clip_video_async", mock_clip),
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            assert job_msg["type"] == "job_created"
            job_id = job_msg["job_id"]

        for _ in range(50):
            resp = client.get(f"/jobs/{job_id}")
            data = resp.json()
            if data["phase"] in ("completed", "failed"):
                break
            time.sleep(0.05)

        assert data["phase"] == "completed"
        assert data["download_url"] is not None
        assert data["match_progress"] is not None
        assert data["match_progress"]["total_matches"] == 1

    @respx.mock
    def test_no_matches_detected(self, client):
        """試合0件で failed になる."""
        respx.post(f"{ANALYZER_URL}/analyze/matches/scan/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": "scan-empty"})
        )
        respx.get(f"{ANALYZER_URL}/analyze/matches/scan/jobs/scan-empty").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "scan-empty",
                    "status": "completed",
                    "progress": {
                        "frames_done": 5,
                        "frames_total": 5,
                    },
                    "result": {"matches": []},
                    "error": None,
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
        assert "No matches detected" in data["error"]

    @respx.mock
    def test_scan_failed(self, client):
        """スキャンジョブが失敗した場合."""
        respx.post(f"{ANALYZER_URL}/analyze/matches/scan/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": "scan-fail"})
        )
        respx.get(f"{ANALYZER_URL}/analyze/matches/scan/jobs/scan-fail").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "scan-fail",
                    "status": "failed",
                    "progress": None,
                    "result": None,
                    "error": "Video too short",
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
        assert "Video too short" in data["error"]

    @respx.mock
    def test_scan_api_error(self, client):
        """スキャンAPI接続エラーの場合."""
        respx.post(f"{ANALYZER_URL}/analyze/matches/scan/jobs").mock(
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

    @respx.mock
    def test_knockout_clamps_match_end(self, client, shared_dir):
        """KO時に次の試合開始で match_end が切り詰められる."""
        # 2試合: 1試合目は5分ルールだが180秒でKO→次の試合が180秒から開始
        scan_job_id = "scan-ko-123"
        respx.post(f"{ANALYZER_URL}/analyze/matches/scan/jobs").mock(
            return_value=httpx.Response(200, json={"job_id": scan_job_id})
        )
        respx.get(f"{ANALYZER_URL}/analyze/matches/scan/jobs/{scan_job_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": scan_job_id,
                    "status": "completed",
                    "progress": {"frames_done": 10, "frames_total": 10},
                    "result": {
                        "matches": [
                            {
                                "start_seconds": 0.0,
                                "duration_seconds": 300,
                                "duration_type": "5min",
                            },
                            {
                                "start_seconds": 180.0,
                                "duration_seconds": 300,
                                "duration_type": "5min",
                            },
                        ],
                    },
                    "error": None,
                    "started_at": 1234567890.0,
                },
            )
        )
        _mock_highlight_responses()
        mock_clip = AsyncMock(return_value=None)

        captured_opts: list = []

        async def capture_analyzer_call(job_id, file_path, opts):
            captured_opts.append(opts)
            result_data = AnalyzerResponse(
                video="test.mp4",
                model="test-model",
                highlights=[h.model_dump() for h in SAMPLE_HIGHLIGHTS],
            )
            return result_data

        with (
            patch("app.main.POLL_INTERVAL", 0),
            patch("app.main.clip_video_async", mock_clip),
            patch(
                "app.main._call_analyzer_background",
                side_effect=capture_analyzer_call,
            ),
            client.websocket_connect("/ws/upload") as ws,
        ):
            _send_upload(ws)
            job_msg = ws.receive_json()
            job_id = job_msg["job_id"]

        for _ in range(50):
            resp = client.get(f"/jobs/{job_id}")
            data = resp.json()
            if data["phase"] in ("completed", "failed"):
                break
            time.sleep(0.05)

        assert data["phase"] == "completed"
        assert len(captured_opts) == 2
        # 1試合目: min(0+300, 180) = 180
        assert captured_opts[0].start == 0.0
        assert captured_opts[0].end == 180.0
        # 2試合目: 最後の試合なので 180+300 = 480
        assert captured_opts[1].start == 180.0
        assert captured_opts[1].end == 480.0


class TestGetJobStatus:
    """GET /jobs/{job_id} のテスト."""

    def test_get_scanning_job(self, client):
        """scanningフェーズのジョブ状態を取得できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_phase(job.job_id, JobPhase.SCANNING)
        orchestrator_jobs.update_analyzer_progress(
            job.job_id,
            stage=0,
            stage_total=1,
            frames_done=3,
            frames_total=10,
        )

        resp = client.get(f"/jobs/{job.job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "scanning"
        assert data["analyzer_progress"]["frames_done"] == 3

    def test_get_analyzing_job_with_match_progress(self, client):
        """analyzingフェーズで試合進捗が取得できる."""
        job = orchestrator_jobs.create()
        orchestrator_jobs.set_phase(job.job_id, JobPhase.ANALYZING)
        orchestrator_jobs.update_analyzer_progress(
            job.job_id,
            stage=1,
            stage_total=1,
            frames_done=10,
            frames_total=100,
        )
        orchestrator_jobs.update_match_progress(
            job.job_id, current_match=2, total_matches=3
        )

        resp = client.get(f"/jobs/{job.job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job.job_id
        assert data["phase"] == "analyzing"
        assert data["analyzer_progress"]["stage"] == 1
        assert data["analyzer_progress"]["frames_done"] == 10
        assert data["match_progress"]["current_match"] == 2
        assert data["match_progress"]["total_matches"] == 3

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
    def test_zip_download(self, client, shared_dir):
        """zip ファイルのダウンロード."""
        results_dir = shared_dir / "results"
        results_dir.mkdir()
        zip_file = results_dir / "test-job-id.zip"
        zip_file.write_bytes(FAKE_ZIP)

        job = orchestrator_jobs.create()
        job.job_id = "test-job-id"
        orchestrator_jobs._jobs["test-job-id"] = job
        orchestrator_jobs.set_filename("test-job-id", "my_video.mp4")

        resp = client.get("/download/test-job-id")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "my_video_highlight.zip" in resp.headers.get("content-disposition", "")

    def test_mp4_fallback(self, client, shared_dir):
        """mp4 フォールバック（後方互換）."""
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


class TestBuildZip:
    """zip ファイル作成のテスト."""

    def test_creates_valid_zip(self, tmp_path):
        """正しいzip構造が作成される."""
        match_dir = tmp_path / "match_1"
        match_dir.mkdir()
        highlight = match_dir / "highlight.mp4"
        highlight.write_bytes(FAKE_MP4)
        analysis = match_dir / "analysis.json"
        analysis.write_text('{"test": true}', encoding="utf-8")

        match_outputs = [
            {
                "match_index": 1,
                "highlight_path": str(highlight),
                "analysis_path": str(analysis),
                "highlights": [],
            }
        ]

        zip_path = tmp_path / "output.zip"
        _build_zip(match_outputs, zip_path)

        assert zip_path.exists()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "match_1/highlight.mp4" in names
            assert "match_1/analysis.json" in names

    def test_multiple_matches(self, tmp_path):
        """複数試合の zip 構造."""
        match_outputs = []
        for i in range(1, 3):
            match_dir = tmp_path / f"match_{i}"
            match_dir.mkdir()
            highlight = match_dir / "highlight.mp4"
            highlight.write_bytes(FAKE_MP4)
            analysis = match_dir / "analysis.json"
            analysis.write_text("{}", encoding="utf-8")
            match_outputs.append(
                {
                    "match_index": i,
                    "highlight_path": str(highlight),
                    "analysis_path": str(analysis),
                    "highlights": [],
                }
            )

        zip_path = tmp_path / "output.zip"
        _build_zip(match_outputs, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "match_1/highlight.mp4" in names
            assert "match_1/analysis.json" in names
            assert "match_2/highlight.mp4" in names
            assert "match_2/analysis.json" in names


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

        zip_file = results_dir / f"{job.job_id}.zip"
        zip_file.write_bytes(FAKE_ZIP)

        removed = orchestrator_jobs.cleanup_old(results_dir, max_age_seconds=3600)

        assert removed == 1
        assert orchestrator_jobs.get(job.job_id) is None
        assert not zip_file.exists()

    def test_cleanup_keeps_recent_jobs(self, shared_dir):
        """期限内のジョブは削除されない."""
        results_dir = shared_dir / "results"
        results_dir.mkdir()

        job = orchestrator_jobs.create()
        orchestrator_jobs.mark_completed(job.job_id, f"/download/{job.job_id}")

        zip_file = results_dir / f"{job.job_id}.zip"
        zip_file.write_bytes(FAKE_ZIP)

        removed = orchestrator_jobs.cleanup_old(results_dir, max_age_seconds=3600)

        assert removed == 0
        assert orchestrator_jobs.get(job.job_id) is not None
        assert zip_file.exists()


class TestFlattenClippedScores:
    """クリップ済み区間のスコア平坦化テスト."""

    def _make_frames(
        self, scores: list[tuple[float, int, int]]
    ) -> list[AnalyzerFrameResult]:
        return [
            AnalyzerFrameResult(
                timestamp_seconds=ts,
                score=sc,
                score_count_gain=sg,
            )
            for ts, sc, sg in scores
        ]

    def test_clipped_region_zeroed(self):
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

        assert frames[0].score == 2
        assert frames[1].score == 4
        assert frames[2].score == 0
        assert frames[3].score == 0
        assert frames[4].score == 0
        assert frames[5].score == 2

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

        assert frames[0].score == 2
        assert frames[1].score == 0
        assert frames[2].score == 2
        assert frames[3].score == 0
        assert frames[4].score == 2

    def test_empty_frames(self):
        highlights = [AnalyzerHighlight(start_seconds=0, end_seconds=10)]
        _flatten_clipped_scores([], highlights)

    def test_no_highlights(self):
        frames = self._make_frames([(0.0, 5, 3), (5.0, 10, 7)])
        _flatten_clipped_scores(frames, [])
        assert frames[0].score == 5
        assert frames[1].score == 10
