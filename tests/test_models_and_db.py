import unittest
import tempfile
import json
from pathlib import Path
from core.models import VideoRecord, PipelineStatus
from adapters.database.sqlite import SQLiteMetadataRepository


class TestModelsAndDB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.repo = SQLiteMetadataRepository(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_upsert_and_retrieve(self):
        record = VideoRecord(
            video_id="test1234",
            url="https://youtube.com/watch?v=test1234",
            title="Test Video Title",
            channel="Test Channel",
            duration_seconds=120,
            status=PipelineStatus.METADATA_EXTRACTED
        )
        self.repo.upsert_record(record)

        retrieved = self.repo.get_by_video_id("test1234")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.video_id, "test1234")
        self.assertEqual(retrieved.title, "Test Video Title")
        self.assertEqual(retrieved.status, PipelineStatus.METADATA_EXTRACTED)

    def test_update_paths_and_subtitles(self):
        record = VideoRecord(
            video_id="test_sub",
            url="https://youtube.com/watch?v=test_sub",
            title="Subtitle Test",
            channel="Test Channel",
            duration_seconds=60,
        )
        self.repo.upsert_record(record)

        mock_segments = [
            {
                "start": 0.0,
                "end": 2.5,
                "text": "Hello world",
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 1.0, "score": 0.95},
                    {"word": "world", "start": 1.1, "end": 2.5, "score": 0.98},
                ]
            }
        ]
        subtitle_stamp = json.dumps(mock_segments)

        self.repo.update_paths_and_subtitles(
            video_id="test_sub",
            merged_video_path="/data/storage/videos/test_sub.mp4",
            audio_path="/data/storage/audios/test_sub.m4a",
            subtitle_json_path="/data/storage/subtitles/test_sub.json",
            subtitle_srt_path="/data/storage/subtitles/test_sub.srt",
            subtitle_stamp=subtitle_stamp
        )

        updated = self.repo.get_by_video_id("test_sub")
        self.assertEqual(updated.status, PipelineStatus.SUCCESS)
        self.assertEqual(updated.merged_video_path, "/data/storage/videos/test_sub.mp4")
        self.assertIsNotNone(updated.subtitle_stamp)

        parsed_segments = json.loads(updated.subtitle_stamp)
        self.assertEqual(len(parsed_segments), 1)
        self.assertEqual(parsed_segments[0]["words"][0]["word"], "Hello")


if __name__ == "__main__":
    unittest.main()
