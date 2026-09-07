from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Any
from datetime import datetime, timezone


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    METADATA_EXTRACTED = "METADATA_EXTRACTED"
    DOWNLOADING = "DOWNLOADING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class VideoRecord:
    video_id: str
    url: str
    title: str
    channel: str
    duration_seconds: int
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    
    # Pipeline execution tracking
    status: PipelineStatus = PipelineStatus.PENDING
    error_message: Optional[str] = None
    
    # Stored asset locations
    merged_video_path: Optional[str] = None
    audio_path: Optional[str] = None
    subtitle_json_path: Optional[str] = None
    subtitle_srt_path: Optional[str] = None
    
    # Native WhisperX segments JSON string
    subtitle_stamp: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.status, PipelineStatus):
            data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoRecord":
        if "status" in data and isinstance(data["status"], str):
            data["status"] = PipelineStatus(data["status"])
        return cls(**data)


@dataclass
class HighlightClip:
    """Represents a specific video/subtitle section containing matched keywords."""
    clip_id: int
    segment_indices: list[int]
    start: float
    end: float
    duration: float
    text: str
    matched_words: list[str] = field(default_factory=list)
    video_id: Optional[str] = None
    video_title: Optional[str] = None
    stream_url: Optional[str] = None
    subtitles_url: Optional[str] = None


@dataclass
class HighlightSearchResult:
    """Represents the complete search and extraction result for a keyword across a single video."""
    video_id: str
    keyword: str
    total_matches: int
    total_clips: int
    clips: list[HighlightClip]
    total_duration: float


@dataclass
class CrossVideoSearchResult:
    """Represents consecutive highlight search results aggregated across all saved videos."""
    keyword: str
    total_videos_matched: int
    total_matches: int
    total_clips: int
    total_duration: float
    clips: list[HighlightClip]


