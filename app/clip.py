"""動画クリッピングモジュール（FFmpeg ベース）."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 600


class ClipError(Exception):
    """FFmpeg 処理の失敗."""


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an FFmpeg command and raise ClipError on failure."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise ClipError(f"FFmpeg failed: {result.stderr.strip()}")
    return result


def _build_filter_complex(segments: list[dict[str, str]]) -> str:
    """Build a filter_complex string for trim+concat in a single pass."""
    n = len(segments)
    parts: list[str] = []

    parts.append(f"[0:v]split={n}" + "".join(f"[vc{i}]" for i in range(n)) + ";")
    parts.append(f"[0:a]asplit={n}" + "".join(f"[ac{i}]" for i in range(n)) + ";")

    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        parts.append(f"[vc{i}]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}];")
        parts.append(
            f"[ac{i}]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}];"
        )

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[v][a]")

    return " ".join(parts)


def process_clip(
    input_path: Path,
    segments: list[dict[str, str]],
    output_path: Path,
) -> None:
    """Trim and concatenate video segments using FFmpeg.

    Single segment uses -ss/-to with re-encoding for frame-accurate cuts.
    Multiple segments use filter_complex with trim/concat for seamless joins.

    Args:
        input_path: Path to the source video.
        segments: List of {"start": ..., "end": ...} dicts (seconds as strings).
        output_path: Path for the final output file.
    """
    if not input_path.exists():
        msg = f"Input file not found: {input_path}"
        raise FileNotFoundError(msg)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(segments) == 1:
        seg = segments[0]
        _run_ffmpeg(
            [
                "-i",
                str(input_path),
                "-ss",
                seg["start"],
                "-to",
                seg["end"],
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                str(output_path),
            ]
        )
    else:
        filter_complex = _build_filter_complex(segments)
        _run_ffmpeg(
            [
                "-i",
                str(input_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "fast",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )


async def clip_video_async(
    input_path: Path,
    segments: list[dict[str, str]],
    output_path: Path,
) -> None:
    """Async wrapper around process_clip."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, process_clip, input_path, segments, output_path)
