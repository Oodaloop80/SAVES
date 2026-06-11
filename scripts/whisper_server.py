#!/usr/bin/env python3
"""
Remote Whisper server — runs on your workstation (Ryzen 9 7950X).
The NAS container POSTs audio files here when transcription.mode = "remote".

Uses faster-whisper (CTranslate2 backend) which is ~4x faster than openai/whisper
on CPU and requires no GPU drivers. The 7950X handles large-v3-turbo at real-time
speed or faster with int8 quantization.

Setup (Windows or Linux, no ROCm needed):
    pip install faster-whisper flask

Run:
    python scripts/whisper_server.py --model large-v3-turbo --port 5000

Model options (speed vs accuracy trade-off):
    large-v3-turbo  — recommended: ~8x faster than large-v3, nearly identical accuracy
    large-v3        — maximum accuracy, ~1-2x real-time on 7950X CPU
    medium.en       — faster, English-only
    small.en        — fastest, English-only, lower accuracy

Note on AMD RX 6800 XT + GPU acceleration:
    ROCm 7 does not officially support RDNA2 (gfx1030). If you want GPU inference,
    use ROCm 6.1 on Linux with:
        pip install torch --index-url https://download.pytorch.org/whl/rocm6.1
    and set --device cuda in this script. But faster-whisper on the 7950X CPU
    is already fast enough for this use case.
"""
import argparse
import logging
import os
import tempfile

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
_model = None


def get_model(model_name: str, device: str, compute_type: str):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper {model_name!r} on {device} ({compute_type})")
        _model = WhisperModel(model_name, device=device, compute_type=compute_type)
        logger.info("Model ready")
    return _model


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file in request"}), 400

    audio_file = request.files["audio"]
    suffix = os.path.splitext(audio_file.filename)[1] if audio_file.filename else ".mp4"
    if not suffix:
        suffix = ".mp4"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        model = get_model(
            app.config["WHISPER_MODEL"],
            app.config["DEVICE"],
            app.config["COMPUTE_TYPE"],
        )
        language = request.form.get("language") or app.config.get("LANGUAGE") or None

        segments, info = model.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,       # skip silent segments — faster, cleaner output
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info(
            f"Transcribed {len(text)} chars | "
            f"lang={info.language} ({info.language_probability:.0%}) | "
            f"{audio_file.filename!r}"
        )
        return jsonify({"text": text, "language": info.language})
    except Exception as e:
        logger.exception(f"Transcription failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": app.config["WHISPER_MODEL"],
        "device": app.config["DEVICE"],
        "compute_type": app.config["COMPUTE_TYPE"],
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remote Whisper transcription server")
    parser.add_argument(
        "--model", default="large-v3-turbo",
        help="Model name: large-v3-turbo (default), large-v3, medium.en, small.en",
    )
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--language", default=None,
                        help="Force language (e.g. 'en'). Default: auto-detect.")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"],
                        help="cpu (default) or cuda (Linux + ROCm 6.1)")
    parser.add_argument("--compute-type", default="int8",
                        help="int8 (default, fastest CPU), float16 (GPU), float32 (CPU fallback)")
    args = parser.parse_args()

    app.config["WHISPER_MODEL"] = args.model
    app.config["DEVICE"] = args.device
    app.config["COMPUTE_TYPE"] = args.compute_type
    app.config["LANGUAGE"] = args.language

    get_model(args.model, args.device, args.compute_type)

    logger.info(f"Whisper server listening on {args.host}:{args.port}")
    logger.info(f"Set in config.yaml:  transcription.remote_url: \"http://YOUR-IP:{args.port}/transcribe\"")
    app.run(host=args.host, port=args.port, threaded=False)
