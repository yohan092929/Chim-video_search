import json
import logging
from pathlib import Path
from typing import Optional, Any

from core.interfaces import MetadataRepository, MediaPlayer, ClipConcatenator
from core.models import VideoRecord, PipelineStatus, HighlightSearchResult, CrossVideoSearchResult
from core.highlight import KeywordHighlightService


logger = logging.getLogger(__name__)


class VideoPlayerService:
    """Domain service for retrieving video metadata and playing videos via MediaPlayer."""

    def __init__(
        self,
        repository: MetadataRepository,
        player: MediaPlayer,
        concatenator: Optional[ClipConcatenator] = None,
    ):
        self.repository = repository
        self.player = player
        self.concatenator = concatenator
        self.highlight_service = KeywordHighlightService(
            repository=repository,
            player=player,
            concatenator=concatenator,
        )

    def get_record(self, video_id: str) -> VideoRecord:
        """Retrieves and validates a video record from the database."""
        record = self.repository.get_by_video_id(video_id)
        if not record:
            raise ValueError(f"Video record with ID '{video_id}' was not found in the database.")
        return record

    def validate_playable(self, record: VideoRecord) -> Path:
        """Validates that the media file is present on disk and returns its Path."""
        if not record.merged_video_path:
            raise FileNotFoundError(
                f"Video '{record.video_id}' does not have a merged video path in the database."
            )
        video_path = Path(record.merged_video_path)
        if not video_path.exists():
            raise FileNotFoundError(
                f"Merged video file for '{record.video_id}' does not exist at '{video_path}'."
            )
        return video_path

    def get_segments(self, record: VideoRecord) -> list[dict[str, Any]]:
        """Parses and returns the WhisperX subtitle segments from the record."""
        if not record.subtitle_stamp:
            return []
        try:
            return json.loads(record.subtitle_stamp)
        except Exception as e:
            logger.warning(f"Failed to parse subtitle_stamp for '{record.video_id}': {e}")
            return []

    def list_playable_records(self) -> list[VideoRecord]:
        """Lists all successfully processed records whose video files exist on disk."""
        records = self.repository.list_all()
        playable = []
        for r in records:
            if r.status == PipelineStatus.SUCCESS and r.merged_video_path:
                if Path(r.merged_video_path).exists():
                    playable.append(r)
        return playable

    def play_by_id(
        self,
        video_id: str,
        with_subtitles: bool = True,
        start_time: Optional[float] = None,
        duration: Optional[float] = None,
        fullscreen: bool = False,
        volume: Optional[int] = None,
        wait: bool = True,
    ) -> Any:
        """Plays a video record by its YouTube video ID.

        Args:
            video_id: The video ID stored in the DB.
            with_subtitles: Whether to attach SRT subtitle if available.
            start_time: Playback start position in seconds.
            duration: Total duration to play in seconds.
            fullscreen: Start in fullscreen mode.
            volume: Audio volume level (0-100).
            wait: Whether to block until the player process exits.
        """
        record = self.get_record(video_id)
        video_path = self.validate_playable(record)

        # Title formatting
        title = f"{record.title} ({record.channel})" if record.title else record.video_id

        # Subtitle path
        sub_path = record.subtitle_srt_path if with_subtitles else None

        print(f"\n[Player] Playing '{title}'")
        print(f"         File: {video_path}")
        if start_time is not None:
            print(f"         Start: {start_time:.2f}s")
        if duration is not None:
            print(f"         Duration: {duration:.2f}s")
        if sub_path and Path(sub_path).exists():
            print(f"         Subtitles: {sub_path}")

        return self.player.play(
            media_path=str(video_path),
            subtitle_path=sub_path,
            start_time=start_time,
            duration=duration,
            title=title,
            fullscreen=fullscreen,
            volume=volume,
            wait=wait,
        )

    def play_segment(
        self,
        video_id: str,
        segment_index: int,
        with_subtitles: bool = True,
        fullscreen: bool = False,
        volume: Optional[int] = None,
        wait: bool = True,
    ) -> Any:
        """Plays a specific subtitle segment by its index from WhisperX stamps."""
        record = self.get_record(video_id)
        segments = self.get_segments(record)

        if not segments:
            raise ValueError(f"No subtitle segments found for video '{video_id}'.")

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(
                f"Segment index {segment_index} out of range (total segments: {len(segments)})."
            )

        target_seg = segments[segment_index]
        start = float(target_seg.get("start", 0.0))
        end = float(target_seg.get("end", 0.0))
        duration = max(0.1, end - start)
        text = target_seg.get("text", "").strip()

        print(f"\n[Segment #{segment_index}] [{start:.2f}s - {end:.2f}s]: \"{text}\"")

        return self.play_by_id(
            video_id=video_id,
            with_subtitles=with_subtitles,
            start_time=start,
            duration=duration,
            fullscreen=fullscreen,
            volume=volume,
            wait=wait,
        )

    def search_and_play(
        self,
        video_id: str,
        query: str,
        with_subtitles: bool = True,
        duration: Optional[float] = None,
        fullscreen: bool = False,
        volume: Optional[int] = None,
        wait: bool = True,
    ) -> Any:
        """Searches subtitle text and word timestamps, seeking directly to the match."""
        record = self.get_record(video_id)
        segments = self.get_segments(record)

        if not segments:
            raise ValueError(f"No subtitle segments available to search for video '{video_id}'.")

        query_lower = query.lower()
        matched_start: Optional[float] = None
        matched_text: str = ""
        matched_idx: int = -1

        # 1. First search in word-level timestamps for exact precision
        for idx, seg in enumerate(segments):
            words = seg.get("words", [])
            for w in words:
                word_clean = w.get("word", "").strip().lower()
                if query_lower in word_clean and "start" in w:
                    matched_start = float(w["start"])
                    matched_text = seg.get("text", "").strip()
                    matched_idx = idx
                    break
            if matched_start is not None:
                break

        # 2. Fall back to segment text search
        if matched_start is None:
            for idx, seg in enumerate(segments):
                seg_text = seg.get("text", "").strip()
                if query_lower in seg_text.lower():
                    matched_start = float(seg.get("start", 0.0))
                    matched_text = seg_text
                    matched_idx = idx
                    break

        if matched_start is None:
            raise ValueError(
                f"Query '{query}' not found in subtitle segments for video '{video_id}'."
            )

        print(f"\n[Search Match] Found '{query}' at {matched_start:.2f}s (Segment #{matched_idx}):")
        print(f"               \"{matched_text}\"")

        return self.play_by_id(
            video_id=video_id,
            with_subtitles=with_subtitles,
            start_time=matched_start,
            duration=duration,
            fullscreen=fullscreen,
            volume=volume,
            wait=wait,
        )

    def search_highlights(
        self,
        video_id: str,
        keyword: str,
        padding: float = 0.2,
        merge_gap: float = 0.5,
    ) -> HighlightSearchResult:
        """Searches and extracts all sentence clips for a keyword in video's subtitles."""
        record = self.get_record(video_id)
        return self.highlight_service.search_sentence_clips(
            record=record,
            keyword=keyword,
            padding=padding,
            merge_gap=merge_gap,
        )

    def search_all_highlights(
        self,
        keyword: str,
        padding: float = 0.6,
        merge_gap: float = 0.5,
    ) -> CrossVideoSearchResult:
        """Searches across all playable records in the repository for keyword utterance clips."""
        records = self.list_playable_records()
        return self.highlight_service.search_all_records(
            records=records,
            keyword=keyword,
            padding=padding,
            merge_gap=merge_gap,
        )


    def play_highlight(
        self,
        video_id: str,
        keyword: str,
        mode: str = "sequential",
        with_subtitles: bool = True,
        padding: float = 0.2,
        merge_gap: float = 0.5,
        export_path: Optional[str] = None,
        fullscreen: bool = False,
        volume: Optional[int] = None,
        wait: bool = True,
    ) -> HighlightSearchResult:
        """Plays all keyword highlight clips sequentially or concatenated."""
        return self.highlight_service.play_highlight(
            video_id=video_id,
            keyword=keyword,
            mode=mode,
            with_subtitles=with_subtitles,
            padding=padding,
            merge_gap=merge_gap,
            export_path=export_path,
            fullscreen=fullscreen,
            volume=volume,
            wait=wait,
        )

