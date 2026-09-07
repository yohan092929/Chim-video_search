import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import HTTPException

from adapters.web.app import app
from adapters.web.routes.api import get_repository
from adapters.web.services.streaming import parse_range_header
from adapters.database.sqlite import SQLiteMetadataRepository
from core.models import VideoRecord, PipelineStatus


@pytest.fixture
def test_repo(tmp_path):
    db_path = tmp_path / "test.db"
    repo = SQLiteMetadataRepository(db_path=db_path)

    # Synthetic media and subtitle files
    video_file = tmp_path / "dummy_vid.mp4"
    video_file.write_bytes(b"\x00" * 4096)  # 4KB synthetic video
    srt_file = tmp_path / "dummy_sub.srt"
    srt_file.write_text("1\n00:00:01,000 --> 00:00:03,500\n안녕하세요, 롤렉스 시계입니다.\n", encoding="utf-8")

    sub_segments = [
        {
            "start": 1.0,
            "end": 3.5,
            "text": "안녕하세요, 롤렉스 시계입니다.",
            "words": [
                {"word": "안녕하세요,", "start": 1.0, "end": 2.0},
                {"word": "롤렉스", "start": 2.1, "end": 2.8},
                {"word": "시계입니다.", "start": 2.9, "end": 3.5},
            ]
        }
    ]

    record = VideoRecord(
        video_id="test_vid_101",
        url="https://youtube.com/watch?v=test_vid_101",
        title="롤렉스 리뷰 영상",
        channel="테스트채널",
        duration_seconds=120,
        status=PipelineStatus.SUCCESS,
        merged_video_path=str(video_file),
        subtitle_srt_path=str(srt_file),
        subtitle_stamp=json.dumps(sub_segments),
    )
    repo.upsert_record(record)

    app.dependency_overrides[get_repository] = lambda: repo
    yield repo
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_repo):
    return TestClient(app)


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "video-player" in response.text
    assert "search-input" in response.text



def test_list_videos(client):
    response = client.get("/api/videos")
    assert response.status_code == 200
    data = response.json()
    assert "videos" in data
    assert len(data["videos"]) == 1
    first = data["videos"][0]
    assert first["video_id"] == "test_vid_101"
    assert first["title"] == "롤렉스 리뷰 영상"
    assert "stream_url" in first


def test_get_video_detail(client):
    res = client.get("/api/videos/test_vid_101")
    assert res.status_code == 200
    data = res.json()
    assert data["video_id"] == "test_vid_101"
    assert data["title"] == "롤렉스 리뷰 영상"
    assert data["duration_seconds"] == 120


def test_get_video_not_found(client):
    res = client.get("/api/videos/nonexistent_id_99999")
    assert res.status_code == 404


def test_global_search_cross_videos(client):
    # Global search across all videos
    res = client.get("/api/search?keyword=롤렉스")
    assert res.status_code == 200
    data = res.json()
    assert data["keyword"] == "롤렉스"
    assert data["total_videos_matched"] == 1
    assert data["total_clips"] == 1
    clip = data["clips"][0]
    assert clip["video_id"] == "test_vid_101"
    assert clip["video_title"] == "롤렉스 리뷰 영상"
    assert "stream_url" in clip
    assert "subtitles_url" in clip
    assert clip["end"] >= clip["start"]


def test_single_video_search(client):
    res = client.get("/api/videos/test_vid_101/search?keyword=롤렉스")
    assert res.status_code == 200
    data = res.json()
    assert data["video_id"] == "test_vid_101"
    assert data["keyword"] == "롤렉스"
    assert data["total_clips"] > 0


def test_search_nonexistent_keyword(client):
    res = client.get("/api/search?keyword=xyzabcnoneexistent123")
    assert res.status_code == 200
    data = res.json()
    assert data["total_matches"] == 0
    assert data["total_clips"] == 0
    assert len(data["clips"]) == 0


def test_subtitles_vtt_preserves_dialogue_comma(client):
    res = client.get("/api/videos/test_vid_101/subtitles.vtt")
    assert res.status_code == 200
    text = res.text
    # Timestamp comma must be converted to dot
    assert "00:00:01.000 --> 00:00:03.500" in text
    # Dialogue comma MUST be preserved
    assert "안녕하세요, 롤렉스" in text


def test_stream_video_range(client):
    headers = {"Range": "bytes=0-1023"}
    res = client.get("/api/videos/test_vid_101/stream", headers=headers)
    assert res.status_code == 206
    assert "Content-Range" in res.headers
    assert res.headers["Content-Range"].startswith("bytes 0-1023/")
    assert len(res.content) == 1024


def test_parse_range_header():
    start, end = parse_range_header("bytes=0-100", 1000)
    assert start == 0
    assert end == 100

    start, end = parse_range_header("bytes=500-", 1000)
    assert start == 500
    assert end == 999

    start, end = parse_range_header("bytes=-200", 1000)
    assert start == 800
    assert end == 999

    with pytest.raises(HTTPException):
        parse_range_header("invalid_range", 1000)

    with pytest.raises(HTTPException):
        parse_range_header("bytes=2000-3000", 1000)

