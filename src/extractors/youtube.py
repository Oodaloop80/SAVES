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
        self.cookies_dir = config.get("paths", {}).get("cookies_dir", "cookies")

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
            ]
            # YouTube increasingly blocks anonymous metadata requests ("Sign in to
            # confirm you're not a bot"). Use cookies/youtube.txt when available.
            cookies_path = os.path.join(self.cookies_dir, "youtube.txt")
            if os.path.exists(cookies_path):
                cmd += ["--cookies", cookies_path]
            cmd.append(url)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            info_files = [f for f in os.listdir(tmpdir) if f.endswith(".info.json")]
            if not info_files:
                # Raise instead of fabricating an empty ExtractedContent(title=url): the
                # fabricated stub used to flow through OCR/analysis (wasted tokens) and end
                # as a junk approval card. Raising lands it as mark_failed + #SAVES-alerts
                # with the real reason. The classic cause is YouTube's bot-check
                # interstitial ("Sign in to confirm you're not a bot") — surface that as a
                # login problem so the processor routes it to the auth-retry path.
                stderr_tail = (result.stderr or "").strip()[-400:]
                if "sign in" in stderr_tail.lower():
                    raise RuntimeError(
                        f"YouTube bot-check — login/cookies required "
                        f"(refresh cookies/youtube.txt): {stderr_tail}"
                    )
                raise RuntimeError(
                    f"YouTube extraction produced no metadata "
                    f"(yt-dlp rc={result.returncode}): {stderr_tail or 'no stderr'}"
                )

            with open(os.path.join(tmpdir, info_files[0]), encoding="utf-8") as f:
                info = json.load(f)

            captions = self._read_captions(tmpdir)
            chapters = self._parse_chapters(info, url)

            # yt-dlp normalises /shorts/ID → watch?v=ID in webpage_url.
            # Obsidian's YouTube embed only recognises the watch?v= form, so we store
            # the canonical URL and use it in the formatter. Shorts also need the video
            # downloaded for frame extraction (no captions on short clips), so we pass
            # the canonical URL as the media URL instead of the thumbnail.
            canonical_url = info.get("webpage_url") or url
            is_short = "/shorts/" in url

            if is_short:
                # Shorts: download the video for frame OCR (they rarely have captions).
                media_urls = [canonical_url]
            else:
                media_urls = [info["thumbnail"]] if info.get("thumbnail") else []

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
                    "canonical_url": canonical_url,
                    "is_short": is_short,
                },
                media_urls=media_urls,
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
        # Skip blank lines, WEBVTT header, timestamp lines, and VTT header fields
        # (Kind: captions, Language: en, etc. that appear at the top of the file).
        if (not line.strip()
                or line.startswith("WEBVTT")
                or re.match(r'^\d{2}:\d{2}', line)
                or re.match(r'^[A-Za-z\-]+:\s', line)):  # "Kind: ...", "Language: ...", etc.
            continue
        # Remove VTT inline tags (<c>, <00:00:01.000>, etc.)
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
