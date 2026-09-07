import os
from pathlib import Path
from typing import Optional
import yt_dlp

from core.models import VideoRecord, PipelineStatus
from core.interfaces import MediaDownloader


class YtDlpDownloader(MediaDownloader):
    """Downloader adapter utilizing yt-dlp to extract metadata and download separate streams."""

    def __init__(self, quiet: bool = True):
        self.quiet = quiet

    def extract_metadata(self, url: str) -> VideoRecord:
        ydl_opts = {
            "quiet": self.quiet,
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            sanitized = ydl.sanitize_info(info)

            video_id = sanitized.get("id") or url.split("=")[-1]
            title = sanitized.get("title") or "Unknown Title"
            channel = sanitized.get("channel") or sanitized.get("uploader") or "Unknown Channel"
            duration = int(sanitized.get("duration") or 0)
            upload_date = sanitized.get("upload_date")
            view_count = sanitized.get("view_count")
            description = sanitized.get("description")
            thumbnail_url = sanitized.get("thumbnail")

            return VideoRecord(
                video_id=video_id,
                url=sanitized.get("webpage_url") or url,
                title=title,
                channel=channel,
                duration_seconds=duration,
                upload_date=upload_date,
                view_count=view_count,
                description=description,
                thumbnail_url=thumbnail_url,
                status=PipelineStatus.METADATA_EXTRACTED,
            )

    def download_streams(self, record: VideoRecord, temp_dir: str) -> tuple[str, str]:
        """Downloads audio and video streams separately into temp_dir.
        Returns:
            (audio_path, video_path)
        """
        temp_path = Path(temp_dir)
        temp_path.mkdir(parents=True, exist_ok=True)

        audio_outtmpl = str(temp_path / "audio.%(ext)s")
        video_outtmpl = str(temp_path / "video.%(ext)s")

        # 1. Download best audio stream
        audio_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": audio_outtmpl,
            "quiet": self.quiet,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(audio_opts) as ydl:
            ydl.download([record.url])

        # 2. Download best video stream
        video_opts = {
            "format": "bestvideo[vcodec^=av01]/bestvideo/best",
            "outtmpl": video_outtmpl,
            "quiet": self.quiet,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(video_opts) as ydl:
            ydl.download([record.url])

        # Find the actual downloaded files
        audio_files = list(temp_path.glob("audio.*"))
        video_files = list(temp_path.glob("video.*"))

        if not audio_files:
            raise FileNotFoundError(f"Audio stream failed to download in {temp_dir}")
        if not video_files:
            raise FileNotFoundError(f"Video stream failed to download in {temp_dir}")

        return str(audio_files[0].resolve()), str(video_files[0].resolve())
