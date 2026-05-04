"""orchestrator API のテスト."""

from __future__ import annotations

import io
from unittest.mock import patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import ANALYZER_URL, CLIPPER_URL, app


@pytest.fixture
def client(tmp_path):
    """テスト用 FastAPI クライアント（共有ディレクトリを tmp_path に差し替え）."""
    with patch("app.main.SHARED_DATA_DIR", tmp_path), TestClient(app) as c:
        yield c


class TestHealth:
    """GET /health のテスト."""

    @respx.mock
    def test_all_connected(self, client):
        """analyzer, clipper 両方接続時."""
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
        """外部サービスが停止している場合でも自身は 200 を返す."""
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


class TestHighlight:
    """POST /highlight のテスト."""

    def _make_file(self, content: bytes = b"fake-video-data") -> dict:
        """テスト用アップロードファイルを作成."""
        return {"file": ("test.mp4", io.BytesIO(content), "video/mp4")}

    @respx.mock
    def test_success(self, client):
        """正常パイプライン: analyzer → clipper → mp4 返却."""
        analyzer_response = {
            "video": "test.mp4",
            "model": "gemini-2.5-flash",
            "highlights": [
                {
                    "start_seconds": 10.0,
                    "end_seconds": 20.0,
                    "peak_intensity": 8,
                    "description": "キル",
                },
                {
                    "start_seconds": 45.0,
                    "end_seconds": 55.0,
                    "peak_intensity": 9,
                    "description": "ウルト",
                },
            ],
            "stage1_summary": {},
        }
        respx.post(f"{ANALYZER_URL}/analyze/highlights").mock(
            return_value=httpx.Response(200, json=analyzer_response)
        )

        fake_mp4 = b"\x00\x00\x00\x1cftypisom"  # 偽 mp4 ヘッダ
        respx.post(f"{CLIPPER_URL}/clip").mock(
            return_value=httpx.Response(
                200,
                content=fake_mp4,
                headers={"content-type": "video/mp4"},
            )
        )

        resp = client.post("/highlight", files=self._make_file())
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert len(resp.content) > 0

    @respx.mock
    def test_no_highlights(self, client):
        """ハイライト 0 件時は 404."""
        analyzer_response = {"video": "test.mp4", "highlights": []}
        respx.post(f"{ANALYZER_URL}/analyze/highlights").mock(
            return_value=httpx.Response(200, json=analyzer_response)
        )

        resp = client.post("/highlight", files=self._make_file())
        assert resp.status_code == 404
        assert "No highlights detected" in resp.json()["detail"]

    @respx.mock
    def test_analyzer_error(self, client):
        """analyzer がエラーを返した場合は 502."""
        respx.post(f"{ANALYZER_URL}/analyze/highlights").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        resp = client.post("/highlight", files=self._make_file())
        assert resp.status_code == 502
        assert "analyzer" in resp.json()["detail"]

    @respx.mock
    def test_analyzer_connection_error(self, client):
        """analyzer に接続できない場合は 502."""
        respx.post(f"{ANALYZER_URL}/analyze/highlights").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        resp = client.post("/highlight", files=self._make_file())
        assert resp.status_code == 502

    @respx.mock
    def test_clipper_error(self, client):
        """clipper がエラーを返した場合は 502."""
        analyzer_response = {
            "video": "test.mp4",
            "model": "gemini-2.5-flash",
            "highlights": [
                {"start_seconds": 10.0, "end_seconds": 20.0},
            ],
            "stage1_summary": {},
        }
        respx.post(f"{ANALYZER_URL}/analyze/highlights").mock(
            return_value=httpx.Response(200, json=analyzer_response)
        )
        respx.post(f"{CLIPPER_URL}/clip").mock(
            return_value=httpx.Response(500, text="FFmpeg error")
        )

        resp = client.post("/highlight", files=self._make_file())
        assert resp.status_code == 502
        assert "clipper" in resp.json()["detail"]

    def test_invalid_options(self, client):
        """不正な options JSON は 400."""
        resp = client.post(
            "/highlight",
            files=self._make_file(),
            data={"options": "not-valid-json"},
        )
        assert resp.status_code == 400
