import unittest
import tempfile
import subprocess
from pathlib import Path
from adapters.processor.ffmpeg import FFmpegMuxer


class TestFFmpegMuxer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.muxer = FFmpegMuxer()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mux_synthetic_streams(self):
        video_src = self.root / "test_v.mp4"
        audio_src = self.root / "test_a.m4a"
        output_mp4 = self.root / "output_merged.mp4"

        # Generate a 1-second test video using ffmpeg testsrc
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=30",
            "-c:v", "libx264", str(video_src)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # Generate a 1-second silent audio using ffmpeg anullsrc
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "1", "-c:a", "aac", str(audio_src)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # Test muxer
        result_path = self.muxer.mux(str(video_src), str(audio_src), str(output_mp4))

        self.assertTrue(Path(result_path).exists())
        self.assertGreater(Path(result_path).stat().st_size, 0)

    def test_parse_srt_timestamp_varied_digits(self):
        from adapters.processor.ffmpeg import _parse_srt_timestamp, _format_srt_timestamp

        # 1-digit subsecond (0.5s)
        self.assertAlmostEqual(_parse_srt_timestamp("00:00:01,5"), 1.5)
        # 2-digit subsecond (0.50s)
        self.assertAlmostEqual(_parse_srt_timestamp("00:00:01,50"), 1.5)
        # 3-digit subsecond (0.500s)
        self.assertAlmostEqual(_parse_srt_timestamp("00:00:01,500"), 1.5)
        # Complex timestamp
        self.assertAlmostEqual(_parse_srt_timestamp("01:02:03,456"), 3723.456)
        # Dot separator
        self.assertAlmostEqual(_parse_srt_timestamp("00:01:30.25"), 90.25)

        # Formatting
        self.assertEqual(_format_srt_timestamp(1.5), "00:00:01,500")
        self.assertEqual(_format_srt_timestamp(3723.456), "01:02:03,456")
        self.assertEqual(_format_srt_timestamp(-5.0), "00:00:00,000")


if __name__ == "__main__":
    unittest.main()

