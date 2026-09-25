"""Pydantic models for request/response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


# ── Enums ────────────────────────────────────────────────────────────────────

class MediaType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    ALL = "all"


class QualityPreset(str, Enum):
    BEST = "best"
    GOOD = "good"
    WORST = "worst"
    AUDIO_ONLY = "audio_only"
    CUSTOM = "custom"


class CompressionPreset(str, Enum):
    """FFmpeg compression presets (lower CRF = better quality, bigger file)."""
    LIGHT = "light"        # CRF 28 — smallest file, noticeable quality loss
    BALANCED = "balanced"  # CRF 23 — default, good size/quality tradeoff
    AGGRESSIVE = "aggressive"  # CRF 20 — larger file, best quality


# ── Request Models ───────────────────────────────────────────────────────────

class ExtractRequest(BaseModel):
    url: str = Field(..., description="URL of the media to extract", examples=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
    media_type: MediaType = Field(default=MediaType.ALL, description="Filter by media type")
    quality: QualityPreset = Field(default=QualityPreset.BEST, description="Quality preset")
    format_id: str | None = Field(default=None, description="Specific format ID (quality=custom)")
    include_subtitles: bool = Field(default=False, description="Include subtitle info")
    include_comments: bool = Field(default=False, description="Include video comments")
    cookies: str | None = Field(default=None, description="Cookie string for authenticated extraction")
    compress: bool = Field(default=False, description="Compress the downloaded file with FFmpeg")
    compression_preset: CompressionPreset = Field(
        default=CompressionPreset.BALANCED,
        description="FFmpeg compression preset used when compress=true",
    )


class DownloadRequest(BaseModel):
    url: str = Field(..., description="URL of the media to download")
    media_type: MediaType = Field(default=MediaType.ALL, description="Filter by media type")
    quality: QualityPreset = Field(default=QualityPreset.BEST, description="Quality preset")
    format_id: str | None = Field(default=None, description="Specific format ID")
    compress: bool = Field(default=False, description="Compress the downloaded file with FFmpeg")
    compression_preset: CompressionPreset = Field(
        default=CompressionPreset.BALANCED,
        description="FFmpeg compression preset used when compress=true",
    )


class PlaylistRequest(BaseModel):
    url: str = Field(..., description="URL of the playlist to extract")
    limit: int = Field(default=50, ge=1, le=500, description="Max items to extract")
    media_type: MediaType = Field(default=MediaType.ALL, description="Filter by media type")


class BatchExtractRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=20, description="List of URLs to extract")
    media_type: MediaType = Field(default=MediaType.ALL, description="Filter by media type")
    quality: QualityPreset = Field(default=QualityPreset.BEST, description="Quality preset")


# ── Response Models ──────────────────────────────────────────────────────────

class FormatInfo(BaseModel):
    format_id: str = ""
    ext: str = ""
    resolution: str = ""
    fps: float | None = None
    vcodec: str = ""
    acodec: str = ""
    filesize: int | None = None
    tbr: float | None = None
    vbr: float | None = None
    abr: float | None = None
    width: int | None = None
    height: int | None = None
    url: str = ""
    format_note: str = ""
    quality: str = ""
    has_video: bool = False
    has_audio: bool = False


class MediaInfo(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    thumbnail: str = ""
    thumbnails: list[dict[str, Any]] = Field(default_factory=list)
    duration: float | None = None
    duration_string: str = ""
    view_count: int | None = None
    like_count: int | None = None
    upload_date: str = ""
    uploader: str = ""
    uploader_id: str | None = None
    channel: str = ""
    channel_id: str | None = None
    channel_follower_count: int | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    webpage_url: str = ""
    original_url: str = ""
    extractor: str = ""
    extractor_key: str = ""
    platform: str = ""
    media_type_detected: str = ""
    formats: list[FormatInfo] = Field(default_factory=list)
    subtitles: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    auto_captions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    comment_count: int | None = None
    age_limit: int | None = None
    chapters: list[dict[str, Any]] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    success: bool = True
    media: MediaInfo
    download_url: str | None = None
    message: str = ""


class PlaylistItem(BaseModel):
    id: str = ""
    title: str = ""
    url: str = ""
    duration: float | None = None
    thumbnail: str = ""
    uploader: str = ""
    index: int | None = None


class PlaylistResponse(BaseModel):
    success: bool = True
    title: str = ""
    description: str = ""
    uploader: str = ""
    playlist_count: int = 0
    items: list[PlaylistItem] = Field(default_factory=list)


class BatchExtractResponse(BaseModel):
    success: bool = True
    results: list[ExtractResponse] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)
    total: int = 0
    successful: int = 0
    failed: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
    yt_dlp_version: str = ""
    supported_platforms: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str = ""
