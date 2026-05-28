"""動画クリッピングモジュール（FFmpeg ベース）."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 600

INTRO_VIDEO = Path(__file__).resolve().parent.parent / "resources" / "title_movie_1.mp4"


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


def _probe_video(path: Path) -> dict:
    """ffprobe で映像ストリームの情報を取得."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "v:0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603
    if result.returncode != 0:
        raise ClipError(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ClipError(f"No video stream found in {path}")
    return streams[0]


def _get_resolution(video_path: Path) -> tuple[int, int]:
    """入力動画の解像度を取得."""
    info = _probe_video(video_path)
    return int(info["width"]), int(info["height"])


def _build_filter_complex(
    segments: list[dict[str, str]],
    *,
    intro: bool = False,
    target_width: int = 0,
    target_height: int = 0,
) -> str:
    """Build a filter_complex string for trim+concat in a single pass."""
    n = len(segments)
    parts: list[str] = []
    concat_count = n

    if intro:
        parts.append(
            f"[1:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setpts=PTS-STARTPTS[vintro];"
        )
        parts.append("[1:a]asetpts=PTS-STARTPTS[aintro];")
        concat_count += 1

    parts.append(f"[0:v]split={n}" + "".join(f"[vc{i}]" for i in range(n)) + ";")
    parts.append(f"[0:a]asplit={n}" + "".join(f"[ac{i}]" for i in range(n)) + ";")

    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        parts.append(f"[vc{i}]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}];")
        parts.append(
            f"[ac{i}]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}];"
        )

    if intro:
        concat_inputs = "[vintro][aintro]" + "".join(f"[v{i}][a{i}]" for i in range(n))
    else:
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={concat_count}:v=1:a=1[v][a]")

    return " ".join(parts)


def process_clip(
    input_path: Path,
    segments: list[dict[str, str]],
    output_path: Path,
    *,
    intro: bool = False,
) -> None:
    """Trim and concatenate video segments using FFmpeg.

    Args:
        input_path: Path to the source video.
        segments: List of {"start": ..., "end": ...} dicts (seconds as strings).
        output_path: Path for the final output file.
        intro: If True, prepend the intro video clip.
    """
    if not input_path.exists():
        msg = f"Input file not found: {input_path}"
        raise FileNotFoundError(msg)

    use_intro = intro and INTRO_VIDEO.exists()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(segments) == 1 and not use_intro:
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
        target_w, target_h = 0, 0
        if use_intro:
            target_w, target_h = _get_resolution(input_path)

        filter_complex = _build_filter_complex(
            segments,
            intro=use_intro,
            target_width=target_w,
            target_height=target_h,
        )
        inputs = ["-i", str(input_path)]
        if use_intro:
            inputs += ["-i", str(INTRO_VIDEO)]
        _run_ffmpeg(
            [
                *inputs,
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
    *,
    intro: bool = False,
) -> None:
    """Async wrapper around process_clip."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: process_clip(input_path, segments, output_path, intro=intro)
    )
