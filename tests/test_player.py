import unittest
from unittest.mock import patch, MagicMock
import tempfile
import json
from pathlib import Path

from core.models import VideoRecord, PipelineStatus
from core.interfaces import MediaPlayer, MetadataRepository
from core.player import VideoPlayerService
from adapters.player.ffplay import FFplayPlayer


class MockPlayer(MediaPlayer):
    def __init__(self):
        self.play_calls = []

    def play(
        self,
        media_path: str,
        subtitle_path=None,
        start_time=None,
        duration=None,
        title=None,
        auto_exit=True,
        fullscreen=False,
        volume=None,
        wait=True,
    ):
        call_info = {
            "media_path": media_path,
            "subtitle_path": subtitle_path,
            "start_time": start_time,
            "duration": duration,
            "title": title,
            "auto_exit": auto_exit,
            "fullscreen": fullscreen,
            "volume": volume,
            "wait": wait,
        }
        self.play_calls.append(call_info)
        return MagicMock()


class MockRepo(MetadataRepository):
    def __init__(self, records=None):
        self.records = {r.video_id: r for r in (records or [])}

    def get_by_video_id(self, video_id: str):
        return self.records.get(video_id)

    def list_all(self):
        return list(self.records.values())

    def upsert_record(self, record):
        self.records[record.video_id] = record

    def update_status(self, video_id, status, error_message=None):
        pass

    def update_paths_and_subtitles(self, video_id, **kwargs):
        pass


class TestFFplayPlayer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.video_file = self.temp_path / "test.mp4"
        self.video_file.write_text("dummy video")
        self.srt_file = self.temp_path / "test.srt"
        self.srt_file.write_text("dummy srt")

        # Instantiate FFplayPlayer with mocked binary resolution
        with patch("shutil.which", return_value="/usr/local/bin/ffplay"):
            self.player = FFplayPlayer()
            self.player.ffmpeg_bin = "/usr/local/bin/ffmpeg"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_command_defaults(self):
        cmd = self.player.build_command(str(self.video_file))
        self.assertEqual(cmd[0], "/usr/local/bin/ffplay")
        self.assertIn("-autoexit", cmd)
        self.assertEqual(cmd[-1], str(self.video_file.resolve()))

    def test_build_command_full_options(self):
        self.player._has_subtitles_filter = True
        cmd = self.player.build_command(
            media_path=str(self.video_file),
            subtitle_path=str(self.srt_file),
            start_time=15.5,
            duration=30.0,
            title="Custom Title",
            auto_exit=True,
            fullscreen=True,
            volume=80,
        )
        self.assertIn("-window_title", cmd)
        self.assertIn("Custom Title", cmd)
        self.assertIn("-ss", cmd)
        self.assertIn("15.5", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("30.0", cmd)
        self.assertIn("-fs", cmd)
        self.assertIn("-volume", cmd)
        self.assertIn("80", cmd)
        self.assertIn("-vf", cmd)
        self.assertTrue(any("subtitles=" in arg for arg in cmd))

    def test_play_file_not_found(self):
        non_existent = self.temp_path / "non_existent.mp4"
        with self.assertRaises(FileNotFoundError):
            self.player.play(str(non_existent))

    @patch("subprocess.Popen")
    def test_play_execution(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        self.player.play(str(self.video_file), wait=True)
        mock_popen.assert_called_once()
        mock_proc.wait.assert_called_once()


class TestVideoPlayerService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.video_file = self.temp_path / "test_vid.mp4"
        self.video_file.write_text("mp4 data")
        self.srt_file = self.temp_path / "test_sub.srt"
        self.srt_file.write_text("srt data")

        self.sample_segments = [
            {
                "start": 0.0,
                "end": 3.5,
                "text": "안녕하세요 반갑습니다.",
                "words": [
                    {"word": "안녕하세요", "start": 0.0, "end": 1.5, "score": 0.98},
                    {"word": "반갑습니다.", "start": 1.6, "end": 3.5, "score": 0.95},
                ],
            },
            {
                "start": 4.0,
                "end": 8.2,
                "text": "포르쉐 쇼핑을 시작합니다.",
                "words": [
                    {"word": "포르쉐", "start": 4.0, "end": 5.2, "score": 0.99},
                    {"word": "쇼핑을", "start": 5.3, "end": 6.5, "score": 0.96},
                    {"word": "시작합니다.", "start": 6.6, "end": 8.2, "score": 0.97},
                ],
            },
        ]

        self.record = VideoRecord(
            video_id="vid_123",
            url="https://youtube.com/watch?v=vid_123",
            title="포르쉐 쇼핑 영상",
            channel="침착맨",
            duration_seconds=120,
            status=PipelineStatus.SUCCESS,
            merged_video_path=str(self.video_file),
            subtitle_srt_path=str(self.srt_file),
            subtitle_stamp=json.dumps(self.sample_segments),
        )

        self.repo = MockRepo([self.record])
        self.mock_player = MockPlayer()
        self.service = VideoPlayerService(repository=self.repo, player=self.mock_player)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_play_by_id_success(self):
        self.service.play_by_id("vid_123", start_time=10.0, duration=5.0)
        self.assertEqual(len(self.mock_player.play_calls), 1)
        call = self.mock_player.play_calls[0]
        self.assertEqual(call["media_path"], str(self.video_file))
        self.assertEqual(call["subtitle_path"], str(self.srt_file))
        self.assertEqual(call["start_time"], 10.0)
        self.assertEqual(call["duration"], 5.0)

    def test_play_by_id_not_found(self):
        with self.assertRaises(ValueError):
            self.service.play_by_id("non_existent_id")

    def test_play_by_id_missing_video_file(self):
        record_no_file = VideoRecord(
            video_id="vid_no_file",
            url="https://youtube.com/watch?v=vid_no_file",
            title="No File",
            channel="Channel",
            duration_seconds=10,
            status=PipelineStatus.SUCCESS,
            merged_video_path=str(self.temp_path / "missing.mp4"),
        )
        self.repo.upsert_record(record_no_file)
        with self.assertRaises(FileNotFoundError):
            self.service.play_by_id("vid_no_file")

    def test_play_segment(self):
        self.service.play_segment("vid_123", segment_index=1)
        self.assertEqual(len(self.mock_player.play_calls), 1)
        call = self.mock_player.play_calls[0]
        self.assertEqual(call["start_time"], 4.0)
        self.assertAlmostEqual(call["duration"], 4.2)

    def test_play_segment_out_of_bounds(self):
        with self.assertRaises(IndexError):
            self.service.play_segment("vid_123", segment_index=99)

    def test_search_and_play_word_match(self):
        # "쇼핑을" starts at 5.3s
        self.service.search_and_play("vid_123", query="쇼핑을")
        self.assertEqual(len(self.mock_player.play_calls), 1)
        call = self.mock_player.play_calls[0]
        self.assertEqual(call["start_time"], 5.3)

    def test_search_and_play_not_found(self):
        with self.assertRaises(ValueError):
            self.service.search_and_play("vid_123", query="없는단어")

    def test_list_playable_records(self):
        playable = self.service.list_playable_records()
        self.assertEqual(len(playable), 1)
        self.assertEqual(playable[0].video_id, "vid_123")


if __name__ == "__main__":
    unittest.main()
