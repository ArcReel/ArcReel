"""Deterministic, declarative normalization for provider video outputs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.content_digest import canonical_json_digest, sha256_file


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return value


@dataclass(frozen=True, slots=True)
class VideoOutputNormalizationPolicy:
    input_width: int
    input_height: int
    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    output_width: int
    output_height: int
    scale_filter: str

    @classmethod
    def from_dict(cls, value: object) -> VideoOutputNormalizationPolicy:
        body = _mapping(value, "video output normalization policy")
        if set(body) != {"schema_version", "kind", "input", "crop", "output", "scale_filter"}:
            raise ValueError("video output normalization policy fields are invalid")
        if body["schema_version"] != 1 or body["kind"] != "safe_frame_crop":
            raise ValueError("unsupported video output normalization policy")
        if body["scale_filter"] != "lanczos":
            raise ValueError("video output normalization scale_filter must be lanczos")
        input_size = _mapping(body["input"], "input")
        crop = _mapping(body["crop"], "crop")
        output_size = _mapping(body["output"], "output")
        if set(input_size) != {"width", "height"} or set(output_size) != {"width", "height"}:
            raise ValueError("input and output dimensions require width and height")
        if set(crop) != {"x", "y", "width", "height"}:
            raise ValueError("crop requires x, y, width and height")
        policy = cls(
            input_width=_positive_int(input_size["width"], "input.width"),
            input_height=_positive_int(input_size["height"], "input.height"),
            crop_x=_nonnegative_int(crop["x"], "crop.x"),
            crop_y=_nonnegative_int(crop["y"], "crop.y"),
            crop_width=_positive_int(crop["width"], "crop.width"),
            crop_height=_positive_int(crop["height"], "crop.height"),
            output_width=_positive_int(output_size["width"], "output.width"),
            output_height=_positive_int(output_size["height"], "output.height"),
            scale_filter="lanczos",
        )
        if (
            policy.crop_x + policy.crop_width > policy.input_width
            or policy.crop_y + policy.crop_height > policy.input_height
        ):
            raise ValueError("crop must stay inside input bounds")
        return policy

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "safe_frame_crop",
            "input": {"width": self.input_width, "height": self.input_height},
            "crop": {
                "x": self.crop_x,
                "y": self.crop_y,
                "width": self.crop_width,
                "height": self.crop_height,
            },
            "output": {"width": self.output_width, "height": self.output_height},
            "scale_filter": self.scale_filter,
        }

    @property
    def digest(self) -> str:
        return canonical_json_digest(self.to_dict(), allow_nan=False)

    @property
    def filter_graph(self) -> str:
        return (
            f"crop={self.crop_width}:{self.crop_height}:{self.crop_x}:{self.crop_y},"
            f"scale={self.output_width}:{self.output_height}:flags={self.scale_filter}"
        )


@dataclass(frozen=True, slots=True)
class VideoOutputNormalizationReceipt:
    policy_digest: str
    raw_sha256: str
    normalized_sha256: str
    input_dimensions: dict[str, int]
    output_dimensions: dict[str, int]
    filter_graph: str
    ffmpeg_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "VideoOutputNormalizationReceipt",
            "policy_digest": self.policy_digest,
            "raw_sha256": self.raw_sha256,
            "normalized_sha256": self.normalized_sha256,
            "input_dimensions": self.input_dimensions,
            "output_dimensions": self.output_dimensions,
            "filter_graph": self.filter_graph,
            "ffmpeg_version": self.ffmpeg_version,
        }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = stderr.strip() if isinstance(stderr, str) and stderr.strip() else str(exc)
        raise ValueError(f"video output normalization command failed: {detail}") from exc


def _probe_dimensions(path: Path) -> dict[str, int]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        payload: Any = json.loads(result.stdout)
        stream = payload["streams"][0]
        width = _positive_int(stream["width"], "video.width")
        height = _positive_int(stream["height"], "video.height")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("video output dimensions could not be verified") from exc
    return {"width": width, "height": height}


def _ffmpeg_version() -> str:
    first_line = _run(["ffmpeg", "-version"]).stdout.splitlines()[0]
    parts = first_line.split()
    if len(parts) < 3 or parts[:2] != ["ffmpeg", "version"]:
        raise ValueError("ffmpeg version could not be verified")
    return parts[2]


def normalize_video_output(
    raw_path: Path,
    normalized_path: Path,
    policy: VideoOutputNormalizationPolicy,
) -> VideoOutputNormalizationReceipt:
    raw_path = Path(raw_path)
    normalized_path = Path(normalized_path)
    if not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    if raw_path.resolve(strict=True) == normalized_path.resolve(strict=False):
        raise ValueError("normalized output path must differ from raw input")
    input_dimensions = _probe_dimensions(raw_path)
    expected_input = {"width": policy.input_width, "height": policy.input_height}
    if input_dimensions != expected_input:
        raise ValueError(
            f"input dimensions {input_dimensions} do not match policy {expected_input}"
        )
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.unlink(missing_ok=True)
    try:
        _run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(raw_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                policy.filter_graph,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(normalized_path),
            ]
        )
        output_dimensions = _probe_dimensions(normalized_path)
        expected_output = {"width": policy.output_width, "height": policy.output_height}
        if output_dimensions != expected_output:
            raise ValueError(
                f"output dimensions {output_dimensions} do not match policy {expected_output}"
            )
        return VideoOutputNormalizationReceipt(
            policy_digest=policy.digest,
            raw_sha256=sha256_file(raw_path),
            normalized_sha256=sha256_file(normalized_path),
            input_dimensions=input_dimensions,
            output_dimensions=output_dimensions,
            filter_graph=policy.filter_graph,
            ffmpeg_version=_ffmpeg_version(),
        )
    except BaseException:
        normalized_path.unlink(missing_ok=True)
        raise


__all__ = [
    "VideoOutputNormalizationPolicy",
    "VideoOutputNormalizationReceipt",
    "normalize_video_output",
]
