import base64
import glob
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
    max_frames = vcfg.get("max_video_frames", 8)
    scene_threshold = vcfg.get("frame_scene_threshold", 0.3)
    frame_grid = vcfg.get("frame_grid", 2)

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
            frames = extract_video_frames(
                path, min(max_frames, remaining), scene_threshold, frame_grid
            )
            for frame_path in frames:
                if len(blocks) >= max_images:
                    break
                block = encode_image_block(frame_path)
                if block:
                    blocks.append(block)

    return blocks


def extract_video_frames(
    video_path: str,
    num_blocks: int = 8,
    scene_threshold: float = 0.3,
    grid: int = 2,
) -> list[str]:
    """
    Return up to num_blocks image-file paths for a video, for OCR/vision.

    Primary strategy is SCENE-CHANGE detection: a frame is grabbed every time the
    on-screen content changes by more than scene_threshold. For reels with burned-in
    captions that roll line-by-line, each new text card is a "scene change", so this
    captures each distinct line ~once instead of sampling blind time intervals (the
    old behavior, which missed most lines that appeared between samples).

    COST-NEUTRAL DENSITY: each returned path may be a grid×grid MONTAGE (contact sheet)
    tiling that many scene frames into one image. A vertical reel frame already exceeds
    Anthropic's image-size cap, so it bills the same ~1600 tokens whether it holds one
    frame or a 2×2 tile of four — so montaging multiplies how much rolling caption text
    we capture per image WITHOUT increasing token cost. `grid=1` disables montaging.

    Falls back to single evenly-spaced frames when scene detection finds too few frames
    (e.g. a static talking-head), and to single scene frames if montaging fails — so it
    can never return less coverage than the pre-montage behavior. Returns paths to JPEG
    files in a temp directory (persists until OS cleanup).
    """
    if num_blocks <= 0:
        return []

    grid = max(1, int(grid))
    cells = grid * grid
    # Pull enough scene frames to fill every block's grid; cap to bound CPU.
    want_frames = min(num_blocks * cells, 48)
    # Scale each scene frame so a grid×grid tile lands near the 1536px cap (legible).
    cell_width = max(480, 1280 // grid)

    scene_frames = _extract_scene_frames(
        video_path, want_frames, scene_threshold, cell_width
    )

    # Not enough scene changes (static framing) → fall back to single even frames.
    if len(scene_frames) < max(3, cells):
        even_frames = _extract_even_frames(video_path, num_blocks)
        if len(even_frames) >= len(scene_frames):
            return even_frames

    if grid <= 1 or len(scene_frames) <= 1:
        return scene_frames[:num_blocks]

    montages = _montage_frames(scene_frames, grid)
    if montages:
        return montages[:num_blocks]
    # Montaging failed → fall back to single scene frames (still better than nothing).
    return scene_frames[:num_blocks]


def _montage_frames(frame_paths: list[str], grid: int) -> list[str]:
    """Tile frames into grid×grid contact sheets (read left→right, top→bottom, each
    cell a later moment). Returns one montage path per full-or-partial group. Uses only
    ffmpeg (image2 sequence + tile filter) so it works cross-platform. Returns [] on any
    failure so the caller can fall back to single frames."""
    cells = grid * grid
    if not frame_paths or cells <= 1:
        return []
    try:
        # Renumber the selected frames contiguously so ffmpeg's image2 demuxer can read
        # them as sel_000.jpg, sel_001.jpg, ... (byte-copy; we never delete temp files).
        seqdir = tempfile.mkdtemp(prefix="saves_montage_")
        for i, src in enumerate(frame_paths):
            with open(src, "rb") as rf, open(os.path.join(seqdir, f"sel_{i:03d}.jpg"), "wb") as wf:
                wf.write(rf.read())

        in_pattern = os.path.join(seqdir, "sel_%03d.jpg")
        n_blocks = (len(frame_paths) + cells - 1) // cells
        montages = []
        for k in range(n_blocks):
            out_path = os.path.join(seqdir, f"montage_{k:02d}.jpg")
            r = subprocess.run(
                ["ffmpeg", "-start_number", str(k * cells),
                 "-i", in_pattern,
                 "-vf", f"tile={grid}x{grid}",
                 "-frames:v", "1", "-q:v", "5", "-y", out_path],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and os.path.exists(out_path):
                montages.append(out_path)
        return montages
    except Exception as e:
        logger.warning(f"Frame montage failed: {e}")
        return []


def _video_duration(video_path: str) -> float:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=10,
        )
        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        return duration if duration > 0 else 30.0
    except Exception:
        return 30.0


def _extract_scene_frames(
    video_path: str, max_frames: int, threshold: float, width: int = 1280
) -> list[str]:
    """Grab a frame at each scene change (plus the opening frame). If more than
    max_frames are found, subsample evenly across the full set to preserve coverage
    of the whole video rather than just the first scene changes. `width` scales each
    frame (smaller when the frames will be montaged into a grid)."""
    try:
        tmpdir = tempfile.mkdtemp(prefix="saves_scene_")
        out_pattern = os.path.join(tmpdir, "scene_%03d.jpg")
        # select: keep frame 0 OR any frame whose scene-change score exceeds threshold.
        # vsync vfr drops the unselected frames; scale caps width to keep files small
        # and (for montaging) keeps every cell a uniform size as tile requires.
        r = subprocess.run(
            ["ffmpeg", "-i", video_path,
             "-vf", f"select='eq(n,0)+gt(scene,{threshold})',scale={width}:-2",
             "-vsync", "vfr", "-q:v", "5", "-y", out_pattern],
            capture_output=True, timeout=120,
        )
        frames = sorted(glob.glob(os.path.join(tmpdir, "scene_*.jpg")))
        if r.returncode != 0 and not frames:
            return []
        if len(frames) > max_frames:
            step = len(frames) / max_frames
            frames = [frames[int(i * step)] for i in range(max_frames)]
        return frames
    except Exception as e:
        logger.warning(f"Scene-frame extraction failed for {video_path}: {e}")
        return []


def _extract_even_frames(video_path: str, num_frames: int) -> list[str]:
    """Evenly-spaced sampling across 10%–80% of the video (fallback strategy)."""
    try:
        duration = _video_duration(video_path)
        tmpdir = tempfile.mkdtemp(prefix="saves_frames_")
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
