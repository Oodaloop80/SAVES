import asyncio
import hashlib
import logging
import os
import re
import subprocess
import urllib.request
import urllib.parse

logger = logging.getLogger(__name__)


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r'[^\w\s-]', '', text.lower())
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    return s[:max_len] or "media"


async def download_media(
    platform: str,
    author: str,
    title: str,
    media_urls: list[str],
    source_url: str,
    media_root: str,
    config: dict,
    cookies_dir: str,
) -> list[str]:
    """Download media files; returns relative Obsidian embed paths."""
    if not media_urls:
        return []

    save_dir = os.path.join(media_root, platform)
    os.makedirs(save_dir, exist_ok=True)

    mcfg = config.get("media", {})
    max_size_mb = mcfg.get("max_video_size_mb", 500)
    video_quality = mcfg.get("video_quality", "bestvideo[height<=1080]+bestaudio/best")

    embed_paths = []
    cookies_path = os.path.join(cookies_dir, f"{platform}.txt")
    has_cookies = os.path.exists(cookies_path)

    for url in media_urls:
        try:
            path = await asyncio.to_thread(
                _download_one, url, save_dir, platform, source_url,
                video_quality, max_size_mb, cookies_path if has_cookies else None
            )
            if path:
                embed_paths.append(path)
        except Exception as e:
            logger.warning(f"Media download failed for {url}: {e}")

    return embed_paths


def _download_one(
    url: str, save_dir: str, platform: str, source_url: str,
    video_quality: str, max_size_mb: int, cookies_path: str | None
) -> str | None:
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    is_video = ext in (".mp4", ".webm", ".mov", ".mkv") or _url_looks_like_video(url)

    if is_video:
        return _yt_dlp_download(url, save_dir, video_quality, max_size_mb, cookies_path)
    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"):
        return _direct_download(url, save_dir)
    else:
        # Try yt-dlp first, fall back to direct
        result = _yt_dlp_download(url, save_dir, video_quality, max_size_mb, cookies_path)
        return result or _direct_download(url, save_dir)


def _yt_dlp_download(
    url: str, save_dir: str, video_quality: str,
    max_size_mb: int, cookies_path: str | None
) -> str | None:
    cmd = [
        "yt-dlp",
        "-f", video_quality,
        "--merge-output-format", "mp4",   # mux separate video+audio streams (needs ffmpeg)
        f"--max-filesize={max_size_mb}M",
        "--no-warnings",
        "--no-playlist",
        "--print", "after_move:filepath",  # print the final muxed file path to stdout
        "--no-simulate",                   # ...but still download
        "-o", os.path.join(save_dir, "%(title).80s.%(ext)s"),
    ]
    if cookies_path:
        cmd += ["--cookies", cookies_path]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode == 0:
        # The final, post-merge filepath is the last non-empty stdout line
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        if lines and os.path.exists(lines[-1]):
            return lines[-1]
        # Fallback: newest file in save_dir
        files = sorted(
            [os.path.join(save_dir, f) for f in os.listdir(save_dir)],
            key=os.path.getmtime,
        )
        if files:
            return files[-1]
    else:
        logger.warning(
            "yt-dlp failed (rc=%s) for %s. If you see a muxing/ffmpeg error, install "
            "ffmpeg and ensure it is on PATH. stderr: %s",
            result.returncode, url, (result.stderr or "")[:300],
        )
    return None


def _direct_download(url: str, save_dir: str) -> str | None:
    try:
        filename = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or ".jpg"
        dest = os.path.join(save_dir, filename + ext)
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(dest, "wb") as f:
                f.write(resp.read())
        return dest
    except Exception:
        return None


def _url_looks_like_video(url: str) -> bool:
    video_domains = ("v.redd.it", "redditmedia.com", "reddit.com/video",
                     "tiktok.com", "youtube.com", "youtu.be",
                     "instagram.com/reel", "facebook.com/watch")
    return any(d in url for d in video_domains)


def abs_to_obsidian_embed(abs_path: str, media_root: str, vault_root: str) -> str:
    """Return the media path RELATIVE TO media_root, with forward slashes.

    The note references it via the External File Embed plugin as `media://<this>`,
    where each device maps the `media://` virtual directory to its own MEDIA root
    (DEV path, N:\\ on Windows, Tailscale mount on mobile). This keeps notes
    device-independent while media lives outside the vault."""
    return os.path.relpath(abs_path, media_root).replace("\\", "/")
