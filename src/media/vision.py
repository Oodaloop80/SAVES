import base64
import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}

_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
}

# 4MB raw → safely under the 5MB base64 limit
_MAX_FILE_BYTES = 4_000_000


def prepare_images_for_claude(
    media_paths: list[str],
    platform: str,
    config: dict,
) -> list[dict]:
    """
    Returns up to max_images Claude API image content blocks.
    YouTube is always skipped (uses captions instead).
    Videos → extract evenly-spaced keyframes.
    Images → base64-encode directly.
    """
    vcfg = config.get("vision", {})
    if not vcfg.get("enabled", True):
        return []
    if platform == "youtube":
        return []

    skip_platforms = vcfg.get("skip_platforms", [])
    if platform in skip_platforms:
        return []

    max_images = vcfg.get("max_images", 5)
    max_frames = vcfg.get("max_video_frames", 4)

    blocks = []
    for path in media_paths:
        if len(blocks) >= max_images:
            break
        if not os.path.exists(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS:
            block = encode_image_block(path)
            if block:
                blocks.append(block)
        elif ext in VIDEO_EXTS:
            remaining = max_images - len(blocks)
            frames = extract_video_frames(path, min(max_frames, remaining))
            for frame_path in frames:
                if len(blocks) >= max_images:
                    break
                block = encode_image_block(frame_path)
                if block:
                    blocks.append(block)

    return blocks


def extract_video_frames(video_path: str, num_frames: int = 4) -> list[str]:
    """
    Extract num_frames evenly-spaced frames from a video using ffmpeg.
    Frames are placed at 10%, 30%, 50%, 70% of duration to skip black intros/outros.
    Returns paths to JPEG files in a temp directory (persists until OS cleanup).
    """
    if num_frames <= 0:
        return []
    try:
        # Get duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if duration <= 0:
            duration = 30.0  # safe fallback

        tmpdir = tempfile.mkdtemp(prefix="saves_frames_")
        # Positions across 10%–80% of the video
        step = 0.7 / max(num_frames - 1, 1) if num_frames > 1 else 0
        positions = [0.1 + step * i for i in range(num_frames)]

        frame_paths = []
        for i, pos in enumerate(positions):
            ts = min(pos * duration, duration - 0.5)
            out_path = os.path.join(tmpdir, f"frame_{i:02d}.jpg")
            r = subprocess.run(
                ["ffmpeg", "-ss", f"{ts:.2f}", "-i", video_path,
                 "-frames:v", "1", "-q:v", "5", "-y", out_path],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and os.path.exists(out_path):
                frame_paths.append(out_path)

        return frame_paths
    except Exception as e:
        logger.warning(f"Frame extraction failed for {video_path}: {e}")
        return []


def encode_image_block(image_path: str) -> dict | None:
    """
    Return an Anthropic image content block for the given file.
    Resizes with ffmpeg if the file exceeds the 4MB threshold.
    """
    try:
        if not os.path.exists(image_path):
            return None

        path_to_encode = image_path
        if os.path.getsize(image_path) > _MAX_FILE_BYTES:
            resized = _resize_image(image_path)
            if resized is None:
                logger.warning(f"Could not resize {image_path} — skipping")
                return None
            path_to_encode = resized

        ext = os.path.splitext(image_path)[1].lower()
        media_type = _MEDIA_TYPES.get(ext, "image/jpeg")

        with open(path_to_encode, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")

        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    except Exception as e:
        logger.warning(f"Image encoding failed for {image_path}: {e}")
        return None


def _resize_image(image_path: str) -> str | None:
    """Resize to max 1280px on longest side, JPEG quality 80."""
    try:
        tmpdir = tempfile.mkdtemp(prefix="saves_resized_")
        out_path = os.path.join(tmpdir, "resized.jpg")
        r = subprocess.run(
            [
                "ffmpeg", "-i", image_path,
                "-vf", "scale=1280:1280:force_original_aspect_ratio=decrease",
                "-q:v", "8", "-y", out_path,
            ],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and os.path.exists(out_path):
            return out_path
        return None
    except Exception:
        return None
