"""app/clip.py のテスト."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.clip import ClipError, _build_filter_complex, clip_video_async, process_clip


class TestBuildFilterComplex:
    """filter_complex 文字列生成のテスト."""

    def test_two_segments(self) -> None:
        result = _build_filter_complex(
            [
                {"start": "10", "end": "25"},
                {"start": "40", "end": "55"},
            ]
        )
        assert "split=2" in result
        assert "asplit=2" in result
        assert "trim=start=10:end=25" in result
        assert "atrim=start=40:end=55" in result
        assert "concat=n=2:v=1:a=1[v][a]" in result

    def test_single_segment(self) -> None:
        result = _build_filter_complex([{"start": "0", "end": "15"}])
        assert "split=1" in result
        assert "concat=n=1:v=1:a=1[v][a]" in result

    def test_three_segments(self) -> None:
        result = _build_filter_complex(
            [
                {"start": "0", "end": "10"},
                {"start": "20", "end": "30"},
                {"start": "40", "end": "50"},
            ]
        )
        assert "split=3" in result
        assert "asplit=3" in result
        assert "concat=n=3:v=1:a=1[v][a]" in result
        assert "trim=start=0:end=10" in result
        assert "trim=start=20:end=30" in result
        assert "trim=start=40:end=50" in result


class TestProcessClip:
    """process_clip のテスト（FFmpeg をモック）."""

    @patch("app.clip._run_ffmpeg")
    def test_single_segment_uses_ss_to(self, mock_ffmpeg: MagicMock, tmp_path) -> None:
        """単一セグメントは -ss/-to を使用."""
        input_file = tmp_path / "input.mp4"
        input_file.write_bytes(b"\x00" * 100)
        output_file = tmp_path / "output.mp4"

        mock_ffmpeg.return_value = MagicMock(returncode=0)

        process_clip(input_file, [{"start": "10", "end": "20"}], output_file)

        call_args = mock_ffmpeg.call_args[0][0]
        assert "-ss" in call_args
        assert "10" in call_args
        assert "-to" in call_args
        assert "20" in call_args
        assert "-c:v" in call_args
        assert "libx264" in call_args
        assert "-crf" in call_args
        assert "18" in call_args
        assert "-preset" in call_args
        assert "fast" in call_args
        assert "-c:a" in call_args
        assert "aac" in call_args
        assert "-b:a" in call_args
        assert "192k" in call_args
        assert "-movflags" in call_args
        assert "+faststart" in call_args
        assert "-avoid_negative_ts" in call_args
        assert "make_zero" in call_args

    @patch("app.clip._run_ffmpeg")
    def test_multiple_segments_uses_filter_complex(
        self, mock_ffmpeg: MagicMock, tmp_path
    ) -> None:
        """複数セグメントは filter_complex を使用."""
        input_file = tmp_path / "input.mp4"
        input_file.write_bytes(b"\x00" * 100)
        output_file = tmp_path / "output.mp4"

        mock_ffmpeg.return_value = MagicMock(returncode=0)

        segments = [
            {"start": "10", "end": "20"},
            {"start": "30", "end": "40"},
        ]
        process_clip(input_file, segments, output_file)

        call_args = mock_ffmpeg.call_args[0][0]
        assert "-filter_complex" in call_args
        fc_idx = call_args.index("-filter_complex")
        fc_str = call_args[fc_idx + 1]
        assert "trim=" in fc_str
        assert "atrim=" in fc_str
        assert "concat=n=2" in fc_str
        assert "-map" in call_args
        assert "[v]" in call_args
        assert "[a]" in call_args

    def test_input_not_found_raises(self, tmp_path) -> None:
        """存在しない入力ファイルで FileNotFoundError."""
        output_file = tmp_path / "output.mp4"
        with pytest.raises(FileNotFoundError):
            process_clip(
                Path("/nonexistent/file.mp4"),
                [{"start": "0", "end": "10"}],
                output_file,
            )

    @patch("app.clip._run_ffmpeg")
    def test_ffmpeg_failure_raises_clip_error(
        self, mock_ffmpeg: MagicMock, tmp_path
    ) -> None:
        """FFmpeg 失敗で ClipError."""
        input_file = tmp_path / "input.mp4"
        input_file.write_bytes(b"\x00" * 100)
        output_file = tmp_path / "output.mp4"

        mock_ffmpeg.side_effect = ClipError("FFmpeg failed: error")

        with pytest.raises(ClipError):
            process_clip(input_file, [{"start": "0", "end": "10"}], output_file)


class TestClipVideoAsync:
    """clip_video_async の非同期ラッパーテスト."""

    @patch("app.clip.process_clip")
    async def test_async_wrapper_calls_process_clip(
        self, mock_process: MagicMock, tmp_path
    ) -> None:
        """async ラッパーが process_clip を呼び出す."""
        input_file = tmp_path / "input.mp4"
        input_file.write_bytes(b"\x00" * 100)
        output_file = tmp_path / "output.mp4"
        segments = [{"start": "0", "end": "10"}]

        await clip_video_async(input_file, segments, output_file)

        mock_process.assert_called_once_with(input_file, segments, output_file)
