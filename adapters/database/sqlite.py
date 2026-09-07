import sqlite3
import json
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from datetime import datetime, timezone
from core.models import VideoRecord, PipelineStatus
from core.interfaces import MetadataRepository
from config import config


class SQLiteMetadataRepository(MetadataRepository):
    """SQLite implementation of MetadataRepository.
    Stores metadata and native WhisperX segments in `subtitle_stamp` column.
    Easily replaceable with Cloud SQL / PostgreSQL / Firestore.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS video_records (
                    video_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    channel TEXT,
                    duration_seconds INTEGER,
                    upload_date TEXT,
                    view_count INTEGER,
                    description TEXT,
                    thumbnail_url TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    merged_video_path TEXT,
                    audio_path TEXT,
                    subtitle_json_path TEXT,
                    subtitle_srt_path TEXT,
                    subtitle_stamp TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_video_records_status ON video_records(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_video_records_created ON video_records(created_at);")
            conn.commit()


    def upsert_record(self, record: VideoRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO video_records (
                    video_id, url, title, channel, duration_seconds,
                    upload_date, view_count, description, thumbnail_url,
                    status, error_message, merged_video_path, audio_path,
                    subtitle_json_path, subtitle_srt_path, subtitle_stamp,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    url=excluded.url,
                    title=excluded.title,
                    channel=excluded.channel,
                    duration_seconds=excluded.duration_seconds,
                    upload_date=excluded.upload_date,
                    view_count=excluded.view_count,
                    description=excluded.description,
                    thumbnail_url=excluded.thumbnail_url,
                    status=excluded.status,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
            """, (
                record.video_id,
                record.url,
                record.title,
                record.channel,
                record.duration_seconds,
                record.upload_date,
                record.view_count,
                record.description,
                record.thumbnail_url,
                record.status.value,
                record.error_message,
                record.merged_video_path,
                record.audio_path,
                record.subtitle_json_path,
                record.subtitle_srt_path,
                record.subtitle_stamp,
                record.created_at or now,
                now
            ))
            conn.commit()

    def update_status(self, video_id: str, status: PipelineStatus, error_message: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE video_records
                SET status = ?, error_message = ?, updated_at = ?
                WHERE video_id = ?
            """, (status.value, error_message, now, video_id))
            conn.commit()

    def update_paths_and_subtitles(
        self,
        video_id: str,
        merged_video_path: Optional[str] = None,
        audio_path: Optional[str] = None,
        subtitle_json_path: Optional[str] = None,
        subtitle_srt_path: Optional[str] = None,
        subtitle_stamp: Optional[str] = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE video_records
                SET merged_video_path = COALESCE(?, merged_video_path),
                    audio_path = COALESCE(?, audio_path),
                    subtitle_json_path = COALESCE(?, subtitle_json_path),
                    subtitle_srt_path = COALESCE(?, subtitle_srt_path),
                    subtitle_stamp = COALESCE(?, subtitle_stamp),
                    status = ?,
                    updated_at = ?
                WHERE video_id = ?
            """, (
                merged_video_path,
                audio_path,
                subtitle_json_path,
                subtitle_srt_path,
                subtitle_stamp,
                PipelineStatus.SUCCESS.value,
                now,
                video_id
            ))
            conn.commit()

    def get_by_video_id(self, video_id: str) -> Optional[VideoRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM video_records WHERE video_id = ?", (video_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def list_all(self) -> list[VideoRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM video_records ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    def list_summaries(self) -> list[VideoRecord]:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT video_id, url, title, channel, duration_seconds,
                       upload_date, view_count, description, thumbnail_url,
                       status, error_message, merged_video_path, audio_path,
                       subtitle_json_path, subtitle_srt_path,
                       NULL as subtitle_stamp,
                       created_at, updated_at
                FROM video_records
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            return [self._row_to_record(row) for row in rows]


    def _row_to_record(self, row: sqlite3.Row) -> VideoRecord:
        d = dict(row)
        return VideoRecord.from_dict(d)
