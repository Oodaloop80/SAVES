import asyncio
import json
import logging
import os
import subprocess

import requests

logger = logging.getLogger(__name__)

_whisper_model = None


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

    mode = tcfg.get("mode", "local")
    if mode == "remote":
        return await asyncio.to_thread(_transcribe_remote, audio_path, tcfg)
    return await asyncio.to_thread(_transcribe_local, audio_path, tcfg)


def _transcribe_remote(audio_path: str, tcfg: dict) -> str | None:
    remote_url = tcfg.get("remote_url", "")
    if not remote_url:
        logger.error("transcription.remote_url is not set in config.yaml")
        return None
    try:
        with open(audio_path, "rb") as f:
            resp = requests.post(
                remote_url,
                files={"audio": (os.path.basename(audio_path), f)},
                timeout=300,
            )
        resp.raise_for_status()
        return resp.json().get("text") or None
    except Exception as e:
        logger.warning(f"Remote transcription failed for {audio_path}: {e}")
        return None


def _transcribe_local(audio_path: str, tcfg: dict) -> str | None:
    model_name = tcfg.get("model", "base")
    language = tcfg.get("language", "en")
    max_minutes = tcfg.get("max_duration_minutes", 30)

    try:
        # Check duration before loading the model
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
                capture_output=True, text=True, timeout=10,
            )
            info = json.loads(result.stdout)
            duration_secs = float(info.get("format", {}).get("duration", 0))
            if duration_secs > max_minutes * 60:
                logger.info(f"Skipping transcription: {duration_secs:.0f}s > {max_minutes * 60:.0f}s limit")
                return None
        except Exception:
            pass

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
