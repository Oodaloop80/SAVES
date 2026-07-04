import asyncio
import json
import logging
import os
import subprocess

import requests

from src.utils.retry import with_retry

logger = logging.getLogger(__name__)

_whisper_model = None

# Only these are worth handing to Whisper. Images and other files are skipped
# (an image post has nothing to transcribe — Claude vision handles those).
_AV_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts", ".3gp",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma",
}


def is_audio_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _AV_EXTENSIONS


def _exceeds_duration_cap(audio_path: str, max_minutes: float) -> bool:
    """True if the media is longer than the cap. Uses ffprobe. Fails open (returns False) on
    any probe error — better to attempt a transcription than to silently drop a good file for
    a probe hiccup. A cap of 0/None disables the check."""
    if not max_minutes or max_minutes <= 0:
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        duration_secs = float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return False
    if duration_secs > max_minutes * 60:
        logger.info(
            "Skipping transcription: %.0fs > %.0fs limit (%s)",
            duration_secs, max_minutes * 60, os.path.basename(audio_path),
        )
        return True
    return False


def _get_model(model_name: str, device: str = "cpu", compute_type: str = "int8"):
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _whisper_model


async def transcribe(audio_path: str, config: dict) -> str | None:
    tcfg = config.get("transcription", {})
    if not tcfg.get("enabled", True):
        return None
    if not os.path.exists(audio_path):
        return None
    if not is_audio_video(audio_path):
        logger.debug("Skipping transcription — not an audio/video file: %s", audio_path)
        return None

    # Duration cap applies to BOTH modes. Enforced here (before dispatch) so an oversized file
    # never reaches the remote POST — where a 300s timeout × retry backoff could stall the
    # serial queue for ~17 min and still lose the transcript — nor loads the local model.
    max_minutes = tcfg.get("max_duration_minutes", 30)
    if await asyncio.to_thread(_exceeds_duration_cap, audio_path, max_minutes):
        return None

    mode = tcfg.get("mode", "local")
    if mode == "remote":
        return await asyncio.to_thread(_transcribe_remote, audio_path, config)
    return await asyncio.to_thread(_transcribe_local, audio_path, tcfg)


def _transcribe_remote(audio_path: str, config: dict) -> str | None:
    tcfg = config.get("transcription", {})
    remote_url = tcfg.get("remote_url", "")
    if not remote_url:
        logger.error("transcription.remote_url is not set in config.yaml")
        return None

    # The Whisper server runs on the workstation and is often still warming up (model load)
    # or briefly unreachable when the first video of a session arrives. Retry the POST with
    # backoff (utils/retry.py) so a transient blip doesn't silently drop the transcript. The
    # POST is stateless/idempotent — the file is re-opened fresh on each attempt.
    pcfg = config.get("processing", {})
    attempts = pcfg.get("retry_attempts", 3)
    base_delay = pcfg.get("retry_delay_seconds", 30)

    @with_retry(attempts=attempts, base_delay=base_delay, exceptions=(requests.RequestException,))
    def _post() -> str | None:
        with open(audio_path, "rb") as f:
            resp = requests.post(
                remote_url,
                files={"audio": (os.path.basename(audio_path), f)},
                timeout=300,
            )
        resp.raise_for_status()
        return resp.json().get("text") or None

    try:
        return _post()
    except Exception as e:
        logger.warning(f"Remote transcription failed for {audio_path} after retries: {e}")
        return None


def _transcribe_local(audio_path: str, tcfg: dict) -> str | None:
    # The duration cap is enforced centrally in transcribe() before dispatch, so it is not
    # re-checked here.
    model_name = tcfg.get("model", "base")
    language = tcfg.get("language", "en")

    try:
        model = _get_model(model_name)
        segments, _ = model.transcribe(
            audio_path,
            language=language or None,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None

    except Exception as e:
        logger.warning(f"Local transcription failed for {audio_path}: {e}")
        return None
