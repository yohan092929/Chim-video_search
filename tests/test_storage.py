import unittest
import tempfile
from pathlib import Path
from adapters.storage.local import LocalStorageBackend


class TestLocalStorage(unittest.TestCase):
    def setUp(self):
        self.temp_root = tempfile.TemporaryDirectory()
        self.storage_dir = Path(self.temp_root.name) / "storage"
        self.temp_dir = Path(self.temp_root.name) / "temp"
        self.backend = LocalStorageBackend(
            base_storage_dir=self.storage_dir,
            temp_dir=self.temp_dir
        )

    def tearDown(self):
        self.temp_root.cleanup()

    def test_save_file_and_cleanup(self):
        # Create a dummy source file
        temp_work_dir = self.backend.create_temp_dir("job_test123")
        source_file = Path(temp_work_dir) / "test_video.mp4"
        source_file.write_text("dummy video content")

        # Save to storage
        saved_path = self.backend.save_file(str(source_file), "videos", "stored_video.mp4")
        self.assertTrue(Path(saved_path).exists())
        self.assertEqual(Path(saved_path).read_text(), "dummy video content")

        # Cleanup temp
        self.backend.cleanup_temp(temp_work_dir)
        self.assertFalse(Path(temp_work_dir).exists())


if __name__ == "__main__":
    unittest.main()
