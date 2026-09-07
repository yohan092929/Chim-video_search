import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from core.interfaces import MediaMuxer, ClipConcatenator
from core.models import HighlightClip


def _parse_srt_timestamp(ts_str: str) -> float:
    """Converts HH:MM:SS,mmm string to float seconds."""
    ts_str = ts_str.strip()
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts_str)
    if not match:
        return 0.0
    hours, minutes, seconds, sub = match.groups()
    sub_seconds = float(f"0.{sub}")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + sub_seconds


def _format_srt_timestamp(seconds: float) -> str:
    """Converts float seconds to HH:MM:SS,mmm string."""
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours = millis // (3600 * 1000)
    millis %= (3600 * 1000)
    minutes = millis // (60 * 1000)
    millis %= (60 * 1000)
    secs = millis // 1000
    millis %= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"



class FFmpegMuxer(MediaMuxer, ClipConcatenator):
    """Muxer adapter that combines streams and extracts/concatenates highlight clips."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg"):
        self.ffmpeg_bin = shutil.which(ffmpeg_bin) or ffmpeg_bin

    def mux(self, video_path: str, audio_path: str, output_path: str) -> str:
        """Merges audio and video streams using ffmpeg without re-encoding video.
        Uses `-c:v copy -c:a aac` to ensure maximum compatibility in MP4 container.
        """
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_bin,
            "-y",  # Overwrite output
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(out.resolve()),
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg muxing failed:\n{result.stderr}")

        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"Muxed output file is missing or empty: {output_path}")

        return str(out.resolve())

    def rebase_subtitles(
        self,
        clips: list[HighlightClip],
        output_srt_path: str,
        source_srt_path: Optional[str] = None,
    ) -> str:
        """Generates a rebased SRT file for concatenated clips."""
        out_srt = Path(output_srt_path)
        out_srt.parent.mkdir(parents=True, exist_ok=True)

        # Parse source SRT cues if available
        cues: list[tuple[float, float, str]] = []
        if source_srt_path and Path(source_srt_path).exists():
            try:
                content = Path(source_srt_path).read_text(encoding="utf-8")
                blocks = re.split(r"\n\s*\n", content.strip())
                for b in blocks:
                    lines = b.strip().splitlines()
                    if len(lines) >= 2:
                        time_line_idx = 1 if lines[0].strip().isdigit() else 0
                        if "-->" in lines[time_line_idx]:
                            parts = lines[time_line_idx].split("-->")
                            c_start = _parse_srt_timestamp(parts[0])
                            c_end = _parse_srt_timestamp(parts[1])
                            c_text = "\n".join(lines[time_line_idx + 1:]).strip()
                            if c_end > c_start and c_text:
                                cues.append((c_start, c_end, c_text))
            except Exception:
                cues = []

        rebased_entries: list[str] = []
        cumulative_offset = 0.0
        entry_counter = 1

        for clip in clips:
            clip_start = clip.start
            clip_end = clip.end
            clip_dur = clip.duration

            # Find matching cues from source SRT that overlap with this clip
            clip_cues = [
                (cs, ce, ct)
                for cs, ce, ct in cues
                if ce > clip_start and cs < clip_end
            ]

            if clip_cues:
                for cs, ce, ct in clip_cues:
                    shifted_start = cumulative_offset + max(0.0, cs - clip_start)
                    shifted_end = cumulative_offset + min(clip_dur, ce - clip_start)
                    if shifted_end > shifted_start + 0.05:
                        rebased_entries.append(
                            f"{entry_counter}\n"
                            f"{_format_srt_timestamp(shifted_start)} --> {_format_srt_timestamp(shifted_end)}\n"
                            f"{ct}\n"
                        )
                        entry_counter += 1
            else:
                # Fallback to clip's own text if no matching SRT cues
                rebased_entries.append(
                    f"{entry_counter}\n"
                    f"{_format_srt_timestamp(cumulative_offset)} --> {_format_srt_timestamp(cumulative_offset + clip_dur)}\n"
                    f"{clip.text}\n"
                )
                entry_counter += 1

            cumulative_offset += clip_dur

        out_srt.write_text("\n".join(rebased_entries), encoding="utf-8")
        return str(out_srt.resolve())

    def create_highlight_video(
        self,
        source_video_path: str,
        clips: list[HighlightClip],
        output_path: str,
        subtitle_path: Optional[str] = None,
    ) -> str:
        """Extracts and concatenates clips from source video into output_path."""
        if not Path(source_video_path).exists():
            raise FileNotFoundError(f"Source video not found: {source_video_path}")

        if not clips:
            raise ValueError("No highlight clips provided for concatenation.")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Single clip fast copy
        if len(clips) == 1:
            clip = clips[0]
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss", str(clip.start),
                "-to", str(clip.end),
                "-i", source_video_path,
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(out.resolve()),
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                # Fallback to ultrafast re-encode if copy fails on boundary
                cmd_fallback = [
                    self.ffmpeg_bin,
                    "-y",
                    "-ss", str(clip.start),
                    "-to", str(clip.end),
                    "-i", source_video_path,
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac",
                    str(out.resolve()),
                ]
                subprocess.run(cmd_fallback, check=True)

            if subtitle_path:
                self.rebase_subtitles(clips, str(out.with_suffix(".srt")), subtitle_path)

            return str(out.resolve())

        # Multiple clips: cut into temporary directory and concat demux
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            clip_paths: list[Path] = []

            for i, clip in enumerate(clips, 1):
                part_file = temp_path / f"part_{i:04d}.mp4"
                cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-ss", str(clip.start),
                    "-to", str(clip.end),
                    "-i", source_video_path,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    str(part_file),
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0 or not part_file.exists() or part_file.stat().st_size == 0:
                    # Fallback to re-encode for this clip
                    cmd_reencode = [
                        self.ffmpeg_bin,
                        "-y",
                        "-ss", str(clip.start),
                        "-to", str(clip.end),
                        "-i", source_video_path,
                        "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac",
                        str(part_file),
                    ]
                    subprocess.run(cmd_reencode, check=True)

                clip_paths.append(part_file)

            # Write concat playlist
            concat_list = temp_path / "concat.txt"
            concat_lines = []
            for p in clip_paths:
                escaped_posix = p.resolve().as_posix().replace("'", "'\\''")
                concat_lines.append(f"file '{escaped_posix}'")
            concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")



            # Concat demux
            concat_cmd = [
                self.ffmpeg_bin,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out.resolve()),
            ]
            concat_res = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if concat_res.returncode != 0 or not out.exists() or out.stat().st_size == 0:
                # Fallback to re-encoding concat if stream copy concat fails
                filter_cmd = [
                    self.ffmpeg_bin,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_list),
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac",
                    str(out.resolve()),
                ]
                subprocess.run(filter_cmd, check=True)

        # Generate rebased subtitle file
        if subtitle_path:
            self.rebase_subtitles(clips, str(out.with_suffix(".srt")), subtitle_path)

        return str(out.resolve())

