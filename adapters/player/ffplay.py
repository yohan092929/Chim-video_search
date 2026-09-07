import os
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional

from core.interfaces import MediaPlayer

logger = logging.getLogger(__name__)


class FFplayPlayer(MediaPlayer):
    """FFplay adapter that plays media files with customizable parameters."""

    def __init__(self, ffplay_bin: str = "ffplay", ffmpeg_bin: str = "ffmpeg"):
        resolved_bin = shutil.which(ffplay_bin)
        if not resolved_bin:
            raise FileNotFoundError(
                f"ffplay binary '{ffplay_bin}' not found in PATH. Please install ffmpeg/ffplay."
            )
        self.ffplay_bin = resolved_bin
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin
        self._has_subtitles_filter = self._check_filter_support("subtitles")

    def _check_filter_support(self, filter_name: str) -> bool:
        """Checks if ffmpeg/ffplay supports a specific filter (e.g. 'subtitles')."""
        try:
            result = subprocess.run(
                [self.ffmpeg_bin, "-filters"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == filter_name:
                    return True
            return False
        except Exception:
            return False

    def build_command(
        self,
        media_path: str,
        subtitle_path: Optional[str] = None,
        start_time: Optional[float] = None,
        duration: Optional[float] = None,
        title: Optional[str] = None,
        auto_exit: bool = True,
        fullscreen: bool = False,
        volume: Optional[int] = None,
    ) -> list[str]:
        """Constructs ffplay command arguments list."""
        cmd = [self.ffplay_bin]

        # Window title
        if title:
            cmd.extend(["-window_title", str(title)])

        # Filters
        video_filters: list[str] = []
        audio_filters: list[str] = []

        # Start time (seek)
        if start_time is not None and start_time > 0:
            cmd.extend(["-ss", str(start_time)])
            video_filters.append(f"trim=start={start_time},setpts=PTS-STARTPTS")
            audio_filters.append(f"atrim=start={start_time},asetpts=PTS-STARTPTS")

        # Duration
        if duration is not None and duration > 0:
            cmd.extend(["-t", str(duration)])

        # Auto exit when playback finishes
        if auto_exit:
            cmd.append("-autoexit")

        # Fullscreen
        if fullscreen:
            cmd.append("-fs")

        # Volume (0-100)
        if volume is not None:
            clamped_vol = max(0, min(100, volume))
            cmd.extend(["-volume", str(clamped_vol)])

        # Subtitles (if requested and file exists)
        if subtitle_path and Path(subtitle_path).exists():
            if self._has_subtitles_filter:
                # Escape special characters for ffmpeg filtergraph syntax
                escaped_path = (
                    str(Path(subtitle_path).resolve())
                    .replace("\\", "\\\\")
                    .replace("'", "'\\''")
                    .replace(":", "\\:")
                )
                # Apply subtitles before trim so subtitle timestamps match original video PTS
                video_filters.insert(0, f"subtitles='{escaped_path}'")
            else:
                logger.debug(
                    "FFmpeg/FFplay was compiled without libass; skipping video filter subtitles overlay."
                )

        if video_filters:
            cmd.extend(["-vf", ",".join(video_filters)])

        if audio_filters:
            cmd.extend(["-af", ",".join(audio_filters)])

        # Input file
        cmd.append(str(Path(media_path).resolve()))
        return cmd

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
    ) -> subprocess.Popen:
        """Plays the media file using ffplay.

        Args:
            media_path: Path to the media file (MP4, MKV, etc.).
            subtitle_path: Optional path to SRT/ASS subtitle file.
            start_time: Optional start position in seconds.
            duration: Optional duration to play in seconds.
            title: Optional window title.
            auto_exit: Automatically close ffplay window when media ends.
            fullscreen: Start ffplay in fullscreen mode.
            volume: Audio volume level (0-100).
            wait: If True, blocks until player process exits (with Ctrl+C handling).
                  If False, returns immediately with running subprocess.Popen object.
        """
        if not Path(media_path).exists():
            raise FileNotFoundError(f"Media file does not exist: {media_path}")

        cmd = self.build_command(
            media_path=media_path,
            subtitle_path=subtitle_path,
            start_time=start_time,
            duration=duration,
            title=title,
            auto_exit=auto_exit,
            fullscreen=fullscreen,
            volume=volume,
        )

        logger.info(f"Launching ffplay: {' '.join(cmd)}")
        process = subprocess.Popen(cmd)

        if wait:
            try:
                process.wait()
            except KeyboardInterrupt:
                logger.info("Playback interrupted by user. Terminating ffplay...")
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

        return process
