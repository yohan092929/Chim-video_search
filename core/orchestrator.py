import json
import logging
from pathlib import Path
from typing import Optional, List

from core.models import VideoRecord, PipelineStatus
from core.interfaces import (
    MetadataRepository,
    StorageBackend,
    MediaDownloader,
    AudioTranscriber,
    MediaMuxer,
)

logger = logging.getLogger("PipelineOrchestrator")


class PipelineOrchestrator:
    """Orchestrates the 3-stage video ingestion and subtitle generation pipeline:
    1. Pre-extract metadata & create local records
    2. Split-stream download & parallel processing (WhisperX alignment + FFmpeg muxing)
    3. Loading files into storage and persisting metadata & subtitle stamps to DB
    """

    def __init__(
        self,
        repository: MetadataRepository,
        storage: StorageBackend,
        downloader: MediaDownloader,
        transcriber: AudioTranscriber,
        muxer: MediaMuxer,
    ):
        self.repository = repository
        self.storage = storage
        self.downloader = downloader
        self.transcriber = transcriber
        self.muxer = muxer

    def process_url(self, url: str, force: bool = False) -> VideoRecord:
        """Processes a single YouTube URL through all 3 stages."""
        logger.info(f"Starting processing for URL: {url}")

        # Stage 1: Pre-extract metadata
        record = self.downloader.extract_metadata(url)
        existing = self.repository.get_by_video_id(record.video_id)

        if existing and existing.status == PipelineStatus.SUCCESS and not force:
            logger.info(f"Video {record.video_id} already successfully processed. Skipping.")
            return existing

        self.repository.upsert_record(record)
        logger.info(f"[Stage 1] Pre-extracted metadata for {record.video_id} ('{record.title}')")

        temp_dir = self.storage.create_temp_dir(f"job_{record.video_id}")

        try:
            # Stage 2: Download streams
            self.repository.update_status(record.video_id, PipelineStatus.DOWNLOADING)
            logger.info(f"[Stage 2] Downloading split audio and video streams for {record.video_id}...")
            audio_path, video_path = self.downloader.download_streams(record, temp_dir)

            self.repository.update_status(record.video_id, PipelineStatus.PROCESSING)
            logger.info(f"[Stage 2] Running WhisperX transcription & alignment on {audio_path}...")
            transcription = self.transcriber.transcribe_and_align(audio_path, temp_dir)

            logger.info(f"[Stage 2] Muxing video and audio with FFmpeg...")
            temp_merged_video = str(Path(temp_dir) / f"merged_{record.video_id}.mp4")
            self.muxer.mux(video_path, audio_path, temp_merged_video)

            # Stage 3: Loading files to Storage & persisting DB records
            logger.info(f"[Stage 3] Loading files to persistent storage and database...")
            final_video_path = self.storage.save_file(
                temp_merged_video, "videos", f"{record.video_id}.mp4"
            )
            audio_suffix = Path(audio_path).suffix or ".m4a"
            final_audio_path = self.storage.save_file(
                audio_path, "audios", f"{record.video_id}{audio_suffix}"
            )

            final_srt_path = None
            if transcription.get("srt_path"):
                final_srt_path = self.storage.save_file(
                    transcription["srt_path"], "subtitles", f"{record.video_id}.srt"
                )

            final_json_path = None
            if transcription.get("json_path"):
                final_json_path = self.storage.save_file(
                    transcription["json_path"], "subtitles", f"{record.video_id}.json"
                )

            # Extract segments containing word-level timestamps
            segments = transcription["result"].get("segments", [])
            subtitle_stamp_json = json.dumps(segments, ensure_ascii=False)

            self.repository.update_paths_and_subtitles(
                video_id=record.video_id,
                merged_video_path=final_video_path,
                audio_path=final_audio_path,
                subtitle_json_path=final_json_path,
                subtitle_srt_path=final_srt_path,
                subtitle_stamp=subtitle_stamp_json,
            )

            logger.info(f"[Stage 3] Successfully completed pipeline for video: {record.video_id}")
            updated_record = self.repository.get_by_video_id(record.video_id)
            return updated_record or record

        except Exception as e:
            logger.exception(f"Pipeline failed for video {record.video_id}: {e}")
            self.repository.update_status(record.video_id, PipelineStatus.FAILED, str(e))
            raise e
        finally:
            # Clean up transient working directory
            self.storage.cleanup_temp(temp_dir)

    def process_batch(self, urls: List[str], force: bool = False) -> list[VideoRecord]:
        """Processes a list of URLs sequentially with fault tolerance."""
        results = []
        for idx, url in enumerate(urls, start=1):
            clean_url = url.strip()
            if not clean_url or clean_url.startswith("#"):
                continue
            logger.info(f"Processing ({idx}/{len(urls)}): {clean_url}")
            try:
                rec = self.process_url(clean_url, force=force)
                results.append(rec)
            except Exception as e:
                logger.error(f"Failed to process {clean_url}: {e}")
        return results
