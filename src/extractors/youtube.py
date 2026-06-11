import asyncio
import json
import os
import re
import subprocess
import tempfile

from src.extractors.base import BaseExtractor, ExtractedContent


class YouTubeExtractor(BaseExtractor):
    def __init__(self, config: dict):
        self.config = config
        pcfg = config.get("platforms", {}).get("youtube", {})
        self.subtitle_language = pcfg.get("subtitle_language", "en")

    def can_handle(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url

    async def extract(self, url: str) -> ExtractedContent:
        return await asyncio.to_thread(self._extract_sync, url)

    def _extract_sync(self, url: str) -> ExtractedContent:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "yt-dlp",
                "--skip-download",
                "--write-info-json",
                "--write-auto-sub",
                f"--sub-lang={self.subtitle_language}",
                "--no-warnings",
                "-o", os.path.join(tmpdir, "%(id)s.%(ext)s"),
                url,
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)

            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if not info_files:
                return ExtractedContent(url=url, platform="youtube", title=url)

            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)

            captions = self._read_captions(tmpdir)
            chapters = self._parse_chapters(info, url)

            return ExtractedContent(
                url=url,
                platform="youtube",
                title=info.get("title", ""),
                author=info.get("uploader") or info.get("channel"),
                body_text=info.get("description", ""),
                metadata={
                    "view_count": info.get("view_count"),
                    "upload_date": info.get("upload_date"),
                    "duration": info.get("duration"),
                    "like_count": info.get("like_count"),
                    "channel_id": info.get("channel_id"),
                    "video_id": info.get("id"),
                },
                media_urls=[info["thumbnail"]] if info.get("thumbnail") else [],
                captions=captions,
                chapters=chapters,
            )

    def _read_captions(self, tmpdir: str) -> str | None:
        for ext in (".en.vtt", ".en.srt", f".{self.subtitle_language}.vtt", f".{self.subtitle_language}.srt"):
            for fname in os.listdir(tmpdir):
                if fname.endswith(ext):
                    with open(os.path.join(tmpdir, fname), encoding="utf-8") as f:
                        raw = f.read()
                    return _strip_vtt(raw) if ext.endswith(".vtt") else _strip_srt(raw)
        return None

    def _parse_chapters(self, info: dict, url: str) -> list[dict] | None:
        chapters = info.get("chapters")
        if not chapters:
            return None
        video_id = info.get("id", "")
        result = []
        for ch in chapters:
            start = int(ch.get("start_time", 0))
            h, m, s = start // 3600, (start % 3600) // 60, start % 60
            time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            result.append({
                "time_str": time_str,
                "seconds": start,
                "title": ch.get("title", ""),
                "video_id": video_id,
            })
        return result


def _strip_vtt(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        if re.match(r'^\d{2}:\d{2}', line) or line.startswith("WEBVTT") or line.strip() == "":
            continue
        # Remove VTT tags
        clean = re.sub(r'<[^>]+>', '', line)
        if clean.strip():
            out.append(clean.strip())
    # Deduplicate consecutive identical lines
    deduped = []
    for line in out:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return " ".join(deduped)


def _strip_srt(text: str) -> str:
    lines = text.splitlines()
    out = []
    for line in lines:
        if re.match(r'^\d+$', line.strip()):
            continue
        if re.match(r'\d{2}:\d{2}:\d{2}', line):
            continue
        if line.strip():
            out.append(line.strip())
    return " ".join(out)
