import json
import logging
import re
from pathlib import Path
from typing import Optional, Any

from core.interfaces import MetadataRepository, MediaPlayer, ClipConcatenator
from core.models import VideoRecord, HighlightClip, HighlightSearchResult, CrossVideoSearchResult


logger = logging.getLogger(__name__)


def _clean_token(text: str) -> str:
    """Strips punctuation and whitespace for resilient token matching."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


class KeywordHighlightService:
    """Domain service for searching keyword sentences in subtitles and orchestrating highlight playback."""

    def __init__(
        self,
        repository: MetadataRepository,
        player: MediaPlayer,
        concatenator: Optional[ClipConcatenator] = None,
    ):
        self.repository = repository
        self.player = player
        self.concatenator = concatenator

    def get_segments(self, record: VideoRecord) -> list[dict[str, Any]]:
        """Parses and returns the WhisperX subtitle segments from the record."""
        if not record.subtitle_stamp:
            return []
        try:
            return json.loads(record.subtitle_stamp)
        except Exception as e:
            logger.warning(f"Failed to parse subtitle_stamp for '{record.video_id}': {e}")
            return []

    def search_sentence_clips(
        self,
        record: VideoRecord,
        keyword: str,
        padding: float = 0.6,
        merge_gap: float = 0.5,
        context_words: int = 2,
    ) -> HighlightSearchResult:
        """Searches subtitle data and extracts concise key uttering sections around the keyword.

        Uses WhisperX phoneme-aligned word timestamps to cut precisely before and after
        the keyword utterance rather than the entire rambling sentence segment.

        Args:
            record: VideoRecord containing subtitle_stamp JSON.
            keyword: The search keyword or phrase.
            padding: Additional seconds to pad before and after keyword utterance (default 0.6s).
            merge_gap: Maximum gap in seconds between adjacent matching clips to merge them (default 0.5s).
            context_words: Number of neighboring words before/after keyword in snippet (default 2).

        Returns:
            HighlightSearchResult with matching concise clips and summary metrics.
        """
        segments = self.get_segments(record)
        keyword_clean = keyword.strip().lower()

        if not keyword_clean or not segments:
            return HighlightSearchResult(
                video_id=record.video_id,
                keyword=keyword,
                total_matches=0,
                total_clips=0,
                clips=[],
                total_duration=0.0,
            )

        max_duration = float(record.duration_seconds) if record.duration_seconds else float("inf")
        raw_matches: list[dict[str, Any]] = []

        for idx, seg in enumerate(segments):
            seg_text = seg.get("text", "").strip()
            words = seg.get("words", [])

            # Filter valid words containing start and end timestamps from WhisperX
            valid_words = [
                w for w in words
                if isinstance(w, dict) and "word" in w and "start" in w and "end" in w
            ]

            if valid_words:
                matched_indices: list[int] = []
                for i, w in enumerate(valid_words):
                    w_str = w.get("word", "").strip().lower()
                    w_clean = _clean_token(w_str)
                    if keyword_clean in w_str or keyword_clean in w_clean:
                        matched_indices.append(i)

                # Phrase search across multi-word spans if no single-word match
                if not matched_indices and " " in keyword_clean:
                    kw_tokens = [_clean_token(t) for t in keyword_clean.split()]
                    kw_len = len(kw_tokens)
                    cleaned_valid_words = [_clean_token(w.get("word", "")) for w in valid_words]
                    for i in range(len(valid_words) - kw_len + 1):
                        window = cleaned_valid_words[i : i + kw_len]
                        if kw_tokens == window or " ".join(kw_tokens) in " ".join(window):
                            matched_indices.append(i)


                if matched_indices:
                    for mi in matched_indices:
                        w = valid_words[mi]
                        w_start = float(w["start"])
                        w_end = float(w["end"])
                        if w_end <= w_start:
                            w_end = w_start + 0.3

                        # Build concise context snippet around the keyword
                        c_start = max(0, mi - context_words)
                        c_end = min(len(valid_words), mi + 1 + context_words)
                        snippet = " ".join(valid_words[j].get("word", "").strip() for j in range(c_start, c_end)).strip()
                        if c_start > 0:
                            snippet = "... " + snippet
                        if c_end < len(valid_words):
                            snippet = snippet + " ..."

                        p_start = max(0.0, w_start - padding)
                        p_end = min(max_duration, w_end + padding)

                        raw_matches.append({
                            "segment_indices": [idx],
                            "start": p_start,
                            "end": p_end,
                            "text": snippet,
                            "matched_words": [w.get("word", "").strip()],
                        })
                elif keyword_clean in seg_text.lower():
                    # Segment text contains keyword but word timestamps missed it
                    s_start = float(seg.get("start", 0.0))
                    s_end = float(seg.get("end", 0.0))
                    if s_end <= s_start:
                        s_end = s_start + 1.0
                    raw_matches.append({
                        "segment_indices": [idx],
                        "start": max(0.0, s_start - padding),
                        "end": min(max_duration, s_end + padding),
                        "text": seg_text,
                        "matched_words": [keyword],
                    })
            else:
                # Fallback: No word-level timestamps in this segment
                if keyword_clean in seg_text.lower():
                    s_start = float(seg.get("start", 0.0))
                    s_end = float(seg.get("end", 0.0))
                    if s_end <= s_start:
                        s_end = s_start + 1.0
                    raw_matches.append({
                        "segment_indices": [idx],
                        "start": max(0.0, s_start - padding),
                        "end": min(max_duration, s_end + padding),
                        "text": seg_text,
                        "matched_words": [keyword],
                    })

        if not raw_matches:
            return HighlightSearchResult(
                video_id=record.video_id,
                keyword=keyword,
                total_matches=0,
                total_clips=0,
                clips=[],
                total_duration=0.0,
            )

        # Sort intervals by start time
        raw_matches.sort(key=lambda x: x["start"])

        # Merge overlapping or closely adjacent intervals
        merged: list[dict[str, Any]] = []
        curr = raw_matches[0]
        for next_inv in raw_matches[1:]:
            # If next interval starts before current end + merge_gap, merge them
            if next_inv["start"] <= curr["end"] + merge_gap:
                curr["end"] = max(curr["end"], next_inv["end"])
                curr["segment_indices"].extend(
                    [s for s in next_inv["segment_indices"] if s not in curr["segment_indices"]]
                )
                curr["matched_words"].extend(next_inv["matched_words"])
                if next_inv["text"] not in curr["text"]:
                    curr["text"] = f"{curr['text']} / {next_inv['text']}"
            else:
                merged.append(curr)
                curr = next_inv
        merged.append(curr)

        # Build HighlightClip instances
        clips: list[HighlightClip] = []
        total_duration = 0.0
        video_title = record.title or record.video_id
        for i, m in enumerate(merged, start=1):
            dur = max(0.1, m["end"] - m["start"])
            total_duration += dur
            clips.append(
                HighlightClip(
                    clip_id=i,
                    segment_indices=m["segment_indices"],
                    start=round(m["start"], 3),
                    end=round(m["end"], 3),
                    duration=round(dur, 3),
                    text=m["text"].strip(),
                    matched_words=m["matched_words"],
                    video_id=record.video_id,
                    video_title=video_title,
                    stream_url=f"/api/videos/{record.video_id}/stream",
                    subtitles_url=f"/api/videos/{record.video_id}/subtitles.vtt",
                )
            )

        return HighlightSearchResult(
            video_id=record.video_id,
            keyword=keyword,
            total_matches=len(raw_matches),
            total_clips=len(clips),
            clips=clips,
            total_duration=round(total_duration, 3),
        )

    def search_all_records(
        self,
        records: list[VideoRecord],
        keyword: str,
        padding: float = 0.6,
        merge_gap: float = 0.5,
    ) -> CrossVideoSearchResult:
        """Searches across multiple video records and aggregates all matching clips for consecutive autoplay."""
        all_clips: list[HighlightClip] = []
        total_matches = 0
        total_duration = 0.0
        matched_video_count = 0

        global_clip_counter = 1
        for rec in records:
            res = self.search_sentence_clips(
                record=rec,
                keyword=keyword,
                padding=padding,
                merge_gap=merge_gap,
            )
            if res.total_clips > 0:
                matched_video_count += 1
                total_matches += res.total_matches
                for clip in res.clips:
                    clip.clip_id = global_clip_counter
                    clip.video_id = rec.video_id
                    clip.video_title = rec.title or rec.video_id
                    clip.stream_url = f"/api/videos/{rec.video_id}/stream"
                    clip.subtitles_url = f"/api/videos/{rec.video_id}/subtitles.vtt"
                    all_clips.append(clip)
                    total_duration += clip.duration
                    global_clip_counter += 1

        return CrossVideoSearchResult(
            keyword=keyword,
            total_videos_matched=matched_video_count,
            total_matches=total_matches,
            total_clips=len(all_clips),
            total_duration=round(total_duration, 3),
            clips=all_clips,
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
        """Finds all sentence clips for keyword and plays them sequentially or concatenated.

        Args:
            video_id: Target video ID in metadata repository.
            keyword: Keyword to search and highlight.
            mode: 'sequential' for clip-by-clip playback, 'concat' for seamless single-window playback.
            with_subtitles: Whether to include subtitle display.
            padding: Seconds added before/after sentences.
            merge_gap: Gap threshold to merge contiguous matches.
            export_path: Optional path to save merged highlight video.
            fullscreen: Fullscreen playback mode.
            volume: Audio volume level (0-100).
            wait: Block until playback finishes.

        Returns:
            HighlightSearchResult instance.
        """
        record = self.repository.get_by_video_id(video_id)
        if not record:
            raise ValueError(f"Video record with ID '{video_id}' was not found in the database.")

        if not record.merged_video_path or not Path(record.merged_video_path).exists():
            raise FileNotFoundError(
                f"Video media file for '{video_id}' does not exist on disk: {record.merged_video_path}"
            )

        search_result = self.search_sentence_clips(
            record=record,
            keyword=keyword,
            padding=padding,
            merge_gap=merge_gap,
        )

        if search_result.total_clips == 0:
            print(f"\n[Highlight] No sentences containing '{keyword}' found for video '{video_id}'.")
            return search_result

        title = record.title or record.video_id
        sub_path = record.subtitle_srt_path if with_subtitles else None

        print(f"\n============================================================")
        print(f"🎬 Keyword Highlight: '{keyword}' in '{title}'")
        print(f"   Matched Sentences: {search_result.total_matches} | Continuous Clips: {search_result.total_clips}")
        print(f"   Total Highlight Duration: {search_result.total_duration:.2f}s | Playback Mode: {mode.upper()}")
        print(f"============================================================")

        for clip in search_result.clips:
            print(f"  • Clip #{clip.clip_id:02d} [{clip.start:6.2f}s - {clip.end:6.2f}s] ({clip.duration:5.2f}s): \"{clip.text}\"")
        print("-" * 60)

        # Single clip playback (when only 1 result)
        if search_result.total_clips == 1 and not export_path and mode == "sequential":
            clip = search_result.clips[0]
            self.player.play(
                media_path=record.merged_video_path,
                subtitle_path=sub_path,
                start_time=clip.start,
                duration=clip.duration,
                title=f"[1/1] {title} - '{keyword}'",
                fullscreen=fullscreen,
                volume=volume,
                wait=wait,
            )
            return search_result

        # Mode: Concat (or if export_path is requested)
        if mode == "concat" or export_path:
            if not self.concatenator:
                logger.warning("No ClipConcatenator configured. Falling back to sequential playback.")
                mode = "sequential"
            else:
                import tempfile
                temp_output = None
                if export_path:
                    target_video = str(Path(export_path).resolve())
                else:
                    tf = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    tf.close()
                    temp_output = tf.name
                    target_video = temp_output

                try:
                    print(f"\n[Highlight] Generating concatenated highlight video ({search_result.total_clips} clips)...")
                    concat_video = self.concatenator.create_highlight_video(
                        source_video_path=record.merged_video_path,
                        clips=search_result.clips,
                        output_path=target_video,
                        subtitle_path=sub_path,
                    )
                    print(f"[Highlight] Generated highlight video: {concat_video}")

                    # Generated rebased SRT if available
                    rebased_srt = str(Path(concat_video).with_suffix(".srt"))
                    play_sub = rebased_srt if Path(rebased_srt).exists() else None

                    if mode == "concat":
                        print(f"[Highlight] Starting seamless continuous playback...")
                        self.player.play(
                            media_path=concat_video,
                            subtitle_path=play_sub,
                            start_time=0.0,
                            duration=search_result.total_duration,
                            title=f"Highlight Reel: '{keyword}' ({search_result.total_clips} clips) - {title}",
                            fullscreen=fullscreen,
                            volume=volume,
                            wait=wait,
                        )
                finally:
                    if temp_output and Path(temp_output).exists():
                        try:
                            Path(temp_output).unlink()
                            temp_srt = Path(temp_output).with_suffix(".srt")
                            if temp_srt.exists():
                                temp_srt.unlink()
                        except Exception:
                            pass

                return search_result

        # Mode: Sequential (play each clip one after another)
        for clip in search_result.clips:
            print(f"\n▶ Playing Clip [{clip.clip_id}/{search_result.total_clips}] [{clip.start:.2f}s - {clip.end:.2f}s]: \"{clip.text}\"")
            try:
                self.player.play(
                    media_path=record.merged_video_path,
                    subtitle_path=sub_path,
                    start_time=clip.start,
                    duration=clip.duration,
                    title=f"[{clip.clip_id}/{search_result.total_clips}] '{keyword}' ({clip.start:.1f}s-{clip.end:.1f}s) - {title}",
                    fullscreen=fullscreen,
                    volume=volume,
                    wait=True,
                )
            except KeyboardInterrupt:
                print(f"\n[Highlight] Clip #{clip.clip_id} skipped by user.")

        print(f"\n[Highlight] Completed sequential playback of {search_result.total_clips} clips.")
        return search_result
