"""
Avatax — Advanced Media Extractor API
Built on yt-dlp 2026.08.19+ and FastAPI.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from models import (
    BatchExtractRequest,
    BatchExtractResponse,
    ErrorResponse,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    PlaylistRequest,
    PlaylistResponse,
)
from extractor import (
    SUPPORTED_PLATFORMS,
    batch_extract,
    detect_platform,
    extract_media,
    extract_playlist,
)

# ── Temp Download Directory & File Tracker ─────────────────────────────────

import json
import asyncio
import subprocess

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "mediaforge_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

TRACKER_FILE = DOWNLOAD_DIR / ".tracker.json"
MAX_FILE_AGE_SECONDS = 15 * 60  # 15 minutes
CLEANUP_INTERVAL_SECONDS = 60   # check every minute


def _load_tracker() -> dict:
    """Load the file tracker JSON."""
    if TRACKER_FILE.exists():
        try:
            return json.loads(TRACKER_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_tracker(data: dict):
    """Save the file tracker JSON."""
    try:
        TRACKER_FILE.write_text(json.dumps(data))
    except OSError:
        pass


def _track_file(file_path: Path):
    """Register a file for tracking."""
    tracker = _load_tracker()
    tracker[str(file_path)] = {
        "created_at": time.time(),
        "filename": file_path.name,
    }
    _save_tracker(tracker)


def _cleanup_old_files():
    """Remove tracked files older than MAX_FILE_AGE_SECONDS."""
    now = time.time()
    tracker = _load_tracker()
    to_remove = []

    for fpath_str, meta in tracker.items():
        created = meta.get("created_at", 0)
        if (now - created) > MAX_FILE_AGE_SECONDS:
            to_remove.append(fpath_str)

    # Also sweep for untracked files older than the limit
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file() and f.name != ".tracker.json":
            if (now - f.stat().st_mtime) > MAX_FILE_AGE_SECONDS:
                to_remove.append(str(f))

    removed = 0
    for fpath_str in set(to_remove):
        p = Path(fpath_str)
        if p.exists():
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        tracker.pop(fpath_str, None)

    if removed > 0:
        _save_tracker(tracker)


async def _cleanup_loop():
    """Background loop that cleans up old files every minute."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            _cleanup_old_files()
        except Exception:
            pass


# ── App Lifespan ─────────────────────────────────────────────────────────────

_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.time()
    _cleanup_old_files()  # clean on startup
    task = asyncio.create_task(_cleanup_loop())  # start background cleanup
    yield
    task.cancel()  # stop cleanup on shutdown


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="Avatax",
    description=(
        "Advanced multi-platform media extractor API. "
        "Extract video, audio, images, and metadata from 30+ platforms "
        "including YouTube, Twitter/X, Instagram, TikTok, Reddit, "
        "Facebook, Vimeo, Twitch, Pornhub, and more."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Error Handlers ───────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Any, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error=str(exc), detail="An unexpected error occurred").model_dump(),
    )


# ── Health & Info ────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_ui():
    """Serve the web UI."""
    return FileResponse("static/index.html")


@app.get("/health", response_model=HealthResponse, tags=["Info"])
async def health_check():
    """Health check with service info."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        yt_dlp_version=yt_dlp.version.__version__,
        supported_platforms=SUPPORTED_PLATFORMS,
        features=[
            "multi_platform_extraction",
            "quality_selection",
            "audio_extraction",
            "video_extraction",
            "thumbnail_extraction",
            "playlist_extraction",
            "batch_extraction",
            "format_listing",
            "subtitle_extraction",
            "metadata_extraction",
            "gallery_extraction",
            "age_restriction_bypass",
            "geo_bypass",
        ],
    )


@app.get("/platforms", tags=["Info"])
async def list_platforms():
    """List all supported platforms."""
    return {
        "platforms": SUPPORTED_PLATFORMS,
        "count": len(SUPPORTED_PLATFORMS),
        "note": "Any URL supported by yt-dlp works, even if not listed here.",
    }


# ── Core Extraction ──────────────────────────────────────────────────────────

@app.post("/extract", response_model=ExtractResponse, tags=["Extract"])
async def extract_media_endpoint(request: ExtractRequest):
    """
    Extract media information and formats from a URL.

    Returns metadata, available formats, and a direct download URL
    without downloading the file.
    """
    try:
        result = extract_media(request)
        return result
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


@app.post("/extract/batch", response_model=BatchExtractResponse, tags=["Extract"])
async def batch_extract_endpoint(request: BatchExtractRequest):
    """
    Extract media info from multiple URLs in one request.

    Maximum 20 URLs per batch.
    """
    try:
        return batch_extract(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch extraction failed: {e}")


@app.get("/extract/url", response_model=ExtractResponse, tags=["Extract"])
async def extract_media_quick(
    url: str = Query(..., description="Media URL to extract"),
    media_type: str = Query("all", description="Filter: video, audio, image, all"),
    quality: str = Query("best", description="Quality: best, good, worst, audio_only"),
):
    """
    Quick extraction via GET — paste a URL and get media info + download link.
    Useful for browser-based integrations.
    """
    try:
        req = ExtractRequest(url=url, media_type=media_type, quality=quality)  # type: ignore[arg-type]
        result = extract_media(req)
        return result
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")


# ── Download via yt-dlp ──────────────────────────────────────────────────────

def _ydl_opts_for_download(
    url: str,
    quality: str = "best",
    format_id: str | None = None,
    outtmpl: str | None = None,
) -> dict[str, Any]:
    """Build yt-dlp options for actual file download.

    Per yt-dlp docs:
    - When selecting a specific format_id, always merge with +bestaudio
      so the result has both video and audio tracks.
    - For audio-only, use FFmpegExtractAudio postprocessor to output mp3.
    """
    postprocessors = []

    if quality == "custom" and format_id:
        # Specific format selected from the list.
        # Always append +bestaudio to ensure audio is merged.
        fmt = f"{format_id}+bestaudio/bestvideo+bestaudio/best"
    elif quality == "audio_only":
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
        # Convert to mp3 using FFmpegExtractAudio per yt-dlp docs:
        # https://github.com/yt-dlp/yt-dlp#postprocessors
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        })
    elif quality == "good":
        fmt = "best[height<=720][ext=mp4]/best[height<=720]/best"
    elif quality == "worst":
        fmt = "worst[ext=mp4]/worst"
    else:
        # Default "best": merge best video + best audio into mp4
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "skip_download": False,
        "outtmpl": outtmpl or str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
        "ignoreerrors": "only_download",
        "no_color": True,
        "geo_bypass": True,
        "socket_timeout": 120,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "web_embedded", "mweb"],
            },
            "pornhub": {
                "format": "best",
            },
        },
        "age_limit": None,
        "postprocessors": postprocessors,
    }

    # Only set merge_output_format when NOT doing audio extraction
    # (FFmpegExtractAudio handles its own output format)
    if not postprocessors:
        opts["merge_output_format"] = "mp4"

    return opts


def _download_with_ytdlp(
    url: str,
    quality: str = "best",
    format_id: str | None = None,
) -> Path | None:
    """Download media using yt-dlp. Returns the path to the downloaded file."""
    # Use a unique ID to avoid collisions
    unique_id = uuid.uuid4().hex[:12]
    outtmpl = str(DOWNLOAD_DIR / f"{unique_id}.%(ext)s")

    opts = _ydl_opts_for_download(url, quality, format_id, outtmpl)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        return None

    if info is None:
        return None

    # Find the downloaded file
    # yt-dlp may have merged formats, so look for the final output
    info_dict = dict(ydl.sanitize_info(info)) if isinstance(info, dict) else dict(info)

    # Try to find the file by the output template
    requested_downloads = info_dict.get("_filename") or info_dict.get("filename")
    if requested_downloads and os.path.exists(requested_downloads):
        result_path = Path(requested_downloads)
        _track_file(result_path)
        return result_path

    # Fallback: search for files matching the unique ID
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file() and f.stem.startswith(unique_id):
            _track_file(f)
            return f

    # Second fallback: find most recent file
    files = sorted(DOWNLOAD_DIR.glob(f"{unique_id}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if files:
        _track_file(files[0])
        return files[0]

    return None


# ── FFmpeg Compression ───────────────────────────────────────────────────────────────

MAX_COMPRESSION_SECONDS = 10 * 60  # hard cap on FFmpeg compression time
MIN_COMPRESS_FILE_SIZE = 1_000_000  # don't bother re-encoding files under ~1 MB

COMPRESSION_CRF: dict[str, str] = {
    "light": "28",
    "balanced": "23",
    "aggressive": "20",
}

# Container types FFmpeg can re-encode into a smaller MP4.
COMPRESSIBLE_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts"}


def _compress_with_ffmpeg(file_path: Path, preset: str = "balanced") -> Path:
    """Re-encode a downloaded media file with FFmpeg to reduce its size.

    Produces ``<stem>-compressed.mp4`` next to the original, registers it
    with the existing file tracker (so the background cleanup loop removes
    it after the TTL), and returns its path.

    Raises RuntimeError if FFmpeg is unavailable, times out, or fails, so
    callers can fall back to the original file.
    """
    if not file_path.exists():
        raise RuntimeError(f"File not found: {file_path}")

    # Images and audio-only containers are already small — nothing to do.
    if file_path.suffix.lower() not in COMPRESSIBLE_EXTS:
        return file_path

    # Not worth the CPU for tiny files.
    if file_path.stat().st_size < MIN_COMPRESS_FILE_SIZE:
        return file_path

    crf = COMPRESSION_CRF.get(preset, COMPRESSION_CRF["balanced"])
    out_path = file_path.with_name(f"{file_path.stem}-compressed.mp4")
    out_path.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(file_path),
        "-c:v", "libx264", "-preset", "slow", "-crf", crf,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MAX_COMPRESSION_SECONDS,
        )
    except FileNotFoundError as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"ffmpeg compression exceeded {MAX_COMPRESSION_SECONDS}s cap"
        ) from e

    if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "unknown error"
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {detail}")

    # Only worth serving the re-encode if it actually shrank the file.
    if out_path.stat().st_size >= file_path.stat().st_size:
        out_path.unlink(missing_ok=True)
        return file_path

    _track_file(out_path)
    return out_path


@app.get("/dl", tags=["Download"])
async def download_media_ytdlp(
    url: str = Query(..., description="Original media page URL to download from"),
    quality: str = Query("best", description="Quality: best, good, worst, audio_only"),
    format_id: str | None = Query(default=None, description="Specific yt-dlp format ID"),
    filename: str = Query(default="", description="Suggested filename"),
    compress: bool = Query(default=False, description="Compress the downloaded file with FFmpeg before serving"),
    compression_preset: str = Query(
        default="balanced",
        description="Compression preset: light, balanced, aggressive",
    ),
):
    """
    Download media using yt-dlp and serve the file.

    This is the REAL download — yt-dlp handles HLS, auth, cookies,
    format merging, and all platform-specific logic.
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # Clean up old files periodically
    _cleanup_old_files()

    try:
        file_path = _download_with_ytdlp(url, quality, format_id)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=f"Download failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download error: {e}")

    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=500, detail="yt-dlp could not download the file")

    # Optionally re-encode with FFmpeg before serving; fall back to the
    # original file if compression fails for any reason. Run in a worker
    # thread so a long encode can't block the event loop.
    served_path = file_path
    if compress:
        try:
            served_path = await asyncio.to_thread(
                _compress_with_ffmpeg, file_path, compression_preset
            )
        except Exception as e:
            print(f"[compression] failed for {file_path.name}: {e}", flush=True)
            served_path = file_path

    file_size = served_path.stat().st_size
    if file_size == 0:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Downloaded file is empty")

    # Determine content type from extension
    ext = served_path.suffix.lower()
    content_type_map = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".opus": "audio/opus",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    content_type = content_type_map.get(ext, "application/octet-stream")

    # Build filename for download
    if not filename:
        # Try to get title from info dict, fallback to file stem
        filename = served_path.stem
        # Clean the filename
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        if len(filename) < 3:
            filename = "media_download"
    download_name = f"{filename}{ext}"

    return FileResponse(
        path=str(served_path),
        media_type=content_type,
        filename=download_name,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/dl/test", tags=["Download"])
async def download_test(
    url: str = Query(..., description="URL to test"),
):
    """Debug: test yt-dlp extraction on a URL."""
    try:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "no_color": True,
            "geo_bypass": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            return {"error": "No info returned"}
        info = dict(ydl.sanitize_info(info))
        formats = info.get("formats", [])
        return {
            "title": info.get("title"),
            "id": info.get("id"),
            "ext": info.get("ext"),
            "format_id": info.get("format_id"),
            "formats_count": len(formats),
            "formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "resolution": f.get("resolution"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "filesize": f.get("filesize"),
                    "url_preview": (f.get("url") or "")[:100],
                }
                for f in formats[:5]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Format Listing ───────────────────────────────────────────────────────────

@app.get("/formats", tags=["Formats"])
async def list_formats(
    url: str = Query(..., description="Media URL"),
    media_type: str = Query("all", description="Filter: video, audio, image, all"),
):
    """
    List all available formats for a URL.

    Returns format details including resolution, codec, bitrate,
    and direct URLs for each available format.
    """
    try:
        from models import ExtractRequest, MediaType
        req = ExtractRequest(url=url, media_type=MediaType(media_type))  # type: ignore[arg-type]
        result = extract_media(req)
        if not result.success:
            raise HTTPException(status_code=404, detail=result.message)
        return {
            "success": True,
            "title": result.media.title,
            "platform": result.media.platform,
            "duration": result.media.duration_string,
            "formats": [f.model_dump() for f in result.media.formats],
            "total_formats": len(result.media.formats),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Audio Extraction ─────────────────────────────────────────────────────────

@app.get("/audio", tags=["Audio"])
async def extract_audio(
    url: str = Query(..., description="Media URL to extract audio from"),
    codec: str = Query("best", description="Preferred codec: best, mp3, m4a, opus, flac"),
):
    """
    Extract audio-only from any supported media URL.

    Returns metadata and a direct download URL for the audio stream.
    """
    try:
        from models import ExtractRequest, QualityPreset
        req = ExtractRequest(url=url, quality=QualityPreset.AUDIO_ONLY)
        result = extract_media(req)
        if not result.success:
            raise HTTPException(status_code=404, detail=result.message)

        audio_formats = [f for f in result.media.formats if f.has_audio]

        if codec != "best" and audio_formats:
            codec_map = {
                "mp3": ["mp3", "mp3a"],
                "m4a": ["mp4a", "m4a", "aac"],
                "opus": ["opus"],
                "flac": ["flac", "fLaC"],
            }
            preferred = codec_map.get(codec, [codec])
            filtered = [f for f in audio_formats if any(p in f.acodec.lower() for p in preferred)]
            if filtered:
                audio_formats = filtered

        best = max(audio_formats, key=lambda f: f.abr or 0) if audio_formats else None

        return {
            "success": True,
            "title": result.media.title,
            "platform": result.media.platform,
            "duration": result.media.duration_string,
            "uploader": result.media.uploader,
            "thumbnail": result.media.thumbnail,
            "audio_formats": [f.model_dump() for f in audio_formats],
            "best_audio_url": best.url if best else None,
            "best_audio_bitrate": f"{best.abr:.0f}kbps" if best and best.abr else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Thumbnail Extraction ─────────────────────────────────────────────────────

@app.get("/thumbnail", tags=["Thumbnails"])
async def extract_thumbnail(
    url: str = Query(..., description="Media URL"),
    index: int = Query(0, description="Thumbnail index (0 = best)"),
):
    """Extract thumbnails for a media URL."""
    try:
        from models import ExtractRequest
        req = ExtractRequest(url=url)
        result = extract_media(req)
        if not result.success:
            raise HTTPException(status_code=404, detail=result.message)

        thumbs = result.media.thumbnails
        selected = thumbs[index] if index < len(thumbs) else (thumbs[-1] if thumbs else {})

        return {
            "success": True,
            "title": result.media.title,
            "platform": result.media.platform,
            "selected_thumbnail": selected,
            "all_thumbnails": thumbs,
            "total_thumbnails": len(thumbs),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Subtitle Extraction ──────────────────────────────────────────────────────

@app.get("/subtitles", tags=["Subtitles"])
async def extract_subtitles(
    url: str = Query(..., description="Media URL"),
    lang: str = Query("en", description="Language code (en, es, fr, etc.)"),
):
    """Extract available subtitles and auto-captions for a media URL."""
    try:
        from models import ExtractRequest
        req = ExtractRequest(url=url, include_subtitles=True)
        result = extract_media(req)
        if not result.success:
            raise HTTPException(status_code=404, detail=result.message)

        subtitles = result.media.subtitles
        auto_captions = result.media.auto_captions

        sub_entry = subtitles.get(lang, subtitles.get("en", []))
        auto_entry = auto_captions.get(lang, auto_captions.get("en", []))

        return {
            "success": True,
            "title": result.media.title,
            "platform": result.media.platform,
            "available_languages": list(subtitles.keys()),
            "auto_caption_languages": list(auto_captions.keys()),
            "requested_subtitles": sub_entry,
            "requested_auto_captions": auto_entry,
            "has_subtitles": bool(subtitles),
            "has_auto_captions": bool(auto_captions),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Playlist Extraction ──────────────────────────────────────────────────────

@app.post("/playlist", response_model=PlaylistResponse, tags=["Playlist"])
async def extract_playlist_endpoint(request: PlaylistRequest):
    """Extract playlist metadata and items."""
    try:
        return extract_playlist(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playlist extraction failed: {e}")


@app.get("/playlist", response_model=PlaylistResponse, tags=["Playlist"])
async def extract_playlist_quick(
    url: str = Query(..., description="Playlist URL"),
    limit: int = Query(50, ge=1, le=500, description="Max items"),
):
    """Quick playlist extraction via GET."""
    try:
        req = PlaylistRequest(url=url, limit=limit)
        return extract_playlist(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playlist extraction failed: {e}")


# ── Metadata Only ────────────────────────────────────────────────────────────

@app.get("/metadata", tags=["Metadata"])
async def extract_metadata(
    url: str = Query(..., description="Media URL"),
):
    """Extract only metadata without format info."""
    try:
        from models import ExtractRequest
        req = ExtractRequest(url=url)
        result = extract_media(req)
        if not result.success:
            raise HTTPException(status_code=404, detail=result.message)

        m = result.media
        return {
            "success": True,
            "id": m.id,
            "title": m.title,
            "description": m.description,
            "thumbnail": m.thumbnail,
            "duration": m.duration,
            "duration_string": m.duration_string,
            "view_count": m.view_count,
            "like_count": m.like_count,
            "upload_date": m.upload_date,
            "uploader": m.uploader,
            "channel": m.channel,
            "platform": m.platform,
            "media_type": m.media_type_detected,
            "tags": m.tags,
            "categories": m.categories,
            "comment_count": m.comment_count,
            "age_limit": m.age_limit,
            "chapters": m.chapters,
            "webpage_url": m.webpage_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Platform Detection ───────────────────────────────────────────────────────

@app.get("/detect", tags=["Info"])
async def detect_platform_endpoint(
    url: str = Query(..., description="URL to identify the platform"),
):
    """Detect which platform a URL belongs to."""
    name, key = detect_platform(url)
    return {
        "url": url,
        "platform": name,
        "platform_key": key,
        "is_supported": key in SUPPORTED_PLATFORMS,
    }


# ── Info / Metadata ──────────────────────────────────────────────────────────

@app.get("/info", tags=["Info"])
async def info():
    """API information and capabilities."""
    return {
        "name": "MediaForge",
        "version": "1.0.0",
        "description": "Advanced multi-platform media extractor API",
        "yt_dlp_version": yt_dlp.version.__version__,
        "endpoints": {
            "health": "GET /health — Service health and platform list",
            "extract": "POST /extract — Full media extraction with formats",
            "extract_quick": "GET /extract/url?url=... — Quick extraction via GET",
            "batch": "POST /extract/batch — Extract from multiple URLs",
            "dl": "GET /dl?url=...&quality=...&compress=true[&compression_preset=light|balanced|aggressive] — Download via yt-dlp + serve file (optionally FFmpeg-compressed)",
            "dl_test": "GET /dl/test?url=... — Debug: test extraction",
            "formats": "GET /formats?url=... — List all available formats",
            "audio": "GET /audio?url=... — Extract audio-only streams",
            "thumbnail": "GET /thumbnail?url=... — Extract thumbnails",
            "subtitles": "GET /subtitles?url=... — Extract subtitles/captions",
            "playlist": "GET|POST /playlist — Extract playlist items",
            "metadata": "GET /metadata?url=... — Metadata only (fast)",
            "detect": "GET /detect?url=... — Detect platform from URL",
            "platforms": "GET /platforms — List supported platforms",
        },
        "quality_presets": ["best", "good", "worst", "audio_only", "custom"],
        "supported_media_types": ["video", "audio", "image", "all"],
    }


# ── Mount Static Files ─────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Uvicorn Entry ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
