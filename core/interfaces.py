from typing import Protocol, Optional, Any
from core.models import VideoRecord, PipelineStatus, HighlightClip


class MetadataRepository(Protocol):
    """Abstract interface for metadata persistence (SQLite, Cloud SQL, Firestore, etc.)."""
    
    def upsert_record(self, record: VideoRecord) -> None:
        """Insert or update initial video record."""
        ...

    def update_status(self, video_id: str, status: PipelineStatus, error_message: Optional[str] = None) -> None:
        """Update lifecycle status of a video."""
        ...

    def update_paths_and_subtitles(
        self,
        video_id: str,
        merged_video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        subtitle_json_path: Optional[str] = None,
        subtitle_srt_path: Optional[str] = None,
        subtitle_stamp: Optional[str] = None
    ) -> None:
        """Update the file paths and serialized subtitle stamps."""
        ...

    def get_by_video_id(self, video_id: str) -> Optional[VideoRecord]:
        """Fetch a record by its YouTube video ID."""
        ...

    def list_all(self) -> list[VideoRecord]:
        """Fetch all stored video records."""
        ...

    def list_summaries(self) -> list[VideoRecord]:
        """Fetch all stored video records without loading the heavy subtitle_stamp column."""
        ...



class StorageBackend(Protocol):
    """Abstract interface for storage (Local disk, GCS bucket, S3, etc.)."""

    def save_file(self, source_path: str, category: str, filename: str) -> str:
        """Persists a file into the storage category and returns its final stored URI/path."""
        ...

    def create_temp_dir(self, prefix: str) -> str:
        """Creates a temporary working directory for intermediate downloads."""
        ...

    def cleanup_temp(self, temp_dir: str) -> None:
        """Cleans up the temporary working directory."""
        ...


class MediaDownloader(Protocol):
    """Abstract interface for downloading media and extracting metadata."""

    def extract_metadata(self, url: str) -> VideoRecord:
        """Extract metadata without downloading media."""
        ...

    def download_streams(self, record: VideoRecord, temp_dir: str) -> tuple[str, str]:
        """Downloads audio and video streams separately. Returns (audio_path, video_path)."""
        ...


class AudioTranscriber(Protocol):
    """Abstract interface for transcribing audio and generating word-level subtitle stamps."""

    def transcribe_and_align(self, audio_path: str, output_dir: str) -> dict[str, Any]:
        """
        Transcribes audio with word alignment.
        Returns a dict containing:
          - 'result': The native WhisperX result dict (including segments with word timestamps)
          - 'srt_path': Path to generated .srt file
          - 'json_path': Path to generated .json file
        """
        ...


class MediaMuxer(Protocol):
    """Abstract interface for muxing video and audio streams."""

    def mux(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Muxes audio and video streams into a unified MP4 container."""
        ...


class MediaPlayer(Protocol):
    """Abstract interface for media player."""

    def play(
        self,
        media_path: str,
        subtitle_path: Optional[str] = None,
        start_time: Optional[float] = None,
        duration: Optional[float] = None,
        title: Optional[str] = None,
        auto_exit: bool = True,
        fullscreen: bool = False,
        volume: Optional[int] = None,
        wait: bool = True,
    ) -> Any:
        """Plays media file using configured backend."""
        ...


class ClipConcatenator(Protocol):
    """Abstract interface for extracting and concatenating video clips into a highlight reel."""

    def create_highlight_video(
        self,
        source_video_path: str,
        clips: list[HighlightClip],
        output_path: str,
        subtitle_path: Optional[str] = None,
    ) -> str:
        """Extracts and concatenates clips from source video into output_path."""
        ...

