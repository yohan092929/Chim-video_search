import unittest
import tempfile
import json
from pathlib import Path

from core.models import VideoRecord, PipelineStatus
from core.interfaces import MediaDownloader, AudioTranscriber, MediaMuxer
from core.orchestrator import PipelineOrchestrator
from adapters.database.sqlite import SQLiteMetadataRepository
from adapters.storage.local import LocalStorageBackend


class MockDownloader(MediaDownloader):
    def extract_metadata(self, url: str) -> VideoRecord:
        return VideoRecord(
            video_id="mock_id_999",
            url=url,
            title="Mock Video Title",
            channel="Mock Channel",
            duration_seconds=42,
            status=PipelineStatus.METADATA_EXTRACTED
        )

    def download_streams(self, record: VideoRecord, temp_dir: str) -> tuple[str, str]:
        audio_file = Path(temp_dir) / "audio.m4a"
        video_file = Path(temp_dir) / "video.mp4"
        audio_file.write_text("mock audio data")
        video_file.write_text("mock video data")
        return str(audio_file), str(video_file)


class MockTranscriber(AudioTranscriber):
    def transcribe_and_align(self, audio_path: str, output_dir: str) -> dict:
        srt_file = Path(output_dir) / "audio.srt"
        json_file = Path(output_dir) / "audio.json"
        srt_file.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello")
        json_file.write_text('{"segments": []}')

        result_dict = {
            "language": "en",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.0,
                    "text": "Hello",
                    "words": [{"word": "Hello", "start": 0.0, "end": 1.0, "score": 0.99}]
                }
            ]
        }
        return {
            "result": result_dict,
            "srt_path": str(srt_file),
            "json_path": str(json_file),
        }


class MockMuxer(MediaMuxer):
    def mux(self, video_path: str, audio_path: str, output_path: str) -> str:
        Path(output_path).write_text("mock merged mp4")
        return output_path


class TestPipelineOrchestrator(unittest.TestCase):
    def setUp(self):
        self.temp_root = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_root.name)
        self.db_path = self.root_path / "test.db"
        self.storage_dir = self.root_path / "storage"
        self.temp_dir = self.root_path / "temp"

        self.repo = SQLiteMetadataRepository(db_path=self.db_path)
        self.storage = LocalStorageBackend(base_storage_dir=self.storage_dir, temp_dir=self.temp_dir)
        self.downloader = MockDownloader()
        self.transcriber = MockTranscriber()
        self.muxer = MockMuxer()

        self.orchestrator = PipelineOrchestrator(
            repository=self.repo,
            storage=self.storage,
            downloader=self.downloader,
            transcriber=self.transcriber,
            muxer=self.muxer
        )

    def tearDown(self):
        self.temp_root.cleanup()

    def test_full_pipeline_success(self):
        url = "https://www.youtube.com/watch?v=mock_id_999"
        record = self.orchestrator.process_url(url)

        self.assertEqual(record.status, PipelineStatus.SUCCESS)
        self.assertEqual(record.video_id, "mock_id_999")

        # Verify DB entry
        saved = self.repo.get_by_video_id("mock_id_999")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.status, PipelineStatus.SUCCESS)
        self.assertTrue(Path(saved.merged_video_path).exists())
        self.assertTrue(Path(saved.audio_path).exists())
        self.assertTrue(Path(saved.subtitle_srt_path).exists())
        self.assertTrue(Path(saved.subtitle_json_path).exists())

        # Verify subtitle_stamp JSON
        segments = json.loads(saved.subtitle_stamp)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["words"][0]["word"], "Hello")


if __name__ == "__main__":
    unittest.main()
