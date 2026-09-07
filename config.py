import os
from pathlib import Path
from dataclasses import dataclass


@dataclass
class AppConfig:
    # Base filesystem locations
    base_dir: Path = Path(__file__).resolve().parent
    data_dir: Path = base_dir / "data"
    storage_dir: Path = data_dir / "storage"
    temp_dir: Path = data_dir / "temp"
    db_path: Path = data_dir / "metadata.db"
    
    # Sub-storage paths
    video_storage_dir: Path = storage_dir / "videos"
    audio_storage_dir: Path = storage_dir / "audios"
    subtitle_storage_dir: Path = storage_dir / "subtitles"

    # WhisperX Configuration
    whisper_model_name: str = "medium"  # "tiny", "base", "small", "medium", "large-v2"
    batch_size: int = 16

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        for d in [
            self.data_dir,
            self.storage_dir,
            self.temp_dir,
            self.video_storage_dir,
            self.audio_storage_dir,
            self.subtitle_storage_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


config = AppConfig()
