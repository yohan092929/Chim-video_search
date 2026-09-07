import os
import shutil
from pathlib import Path
from core.interfaces import StorageBackend
from config import config


class LocalStorageBackend(StorageBackend):
    """Local filesystem implementation of StorageBackend.
    Designed to be easily swappable with GCP Cloud Storage (GCS) in the future.
    """

    def __init__(self, base_storage_dir: Path | str | None = None, temp_dir: Path | str | None = None):
        self.base_storage_dir = Path(base_storage_dir or config.storage_dir)
        self.temp_dir = Path(temp_dir or config.temp_dir)
        self.ensure_directories()

    def ensure_directories(self) -> None:
        self.base_storage_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def save_file(self, source_path: str, category: str, filename: str) -> str:
        """Move or copy a file from source to the appropriate category directory."""
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source file does not exist: {source_path}")

        category_dir = self.base_storage_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        destination = category_dir / filename

        # Copy or move
        shutil.copy2(source, destination)
        return str(destination.resolve())

    def create_temp_dir(self, prefix: str) -> str:
        target = self.temp_dir / prefix
        target.mkdir(parents=True, exist_ok=True)
        return str(target.resolve())

    def cleanup_temp(self, temp_dir: str) -> None:
        p = Path(temp_dir)
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
