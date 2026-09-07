import re
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response

from adapters.database.sqlite import SQLiteMetadataRepository
from core.highlight import KeywordHighlightService
from core.models import PipelineStatus
from adapters.web.services.streaming import range_stream_file

api_router = APIRouter(prefix="/api")


def get_repository() -> SQLiteMetadataRepository:
    return SQLiteMetadataRepository()


def get_highlight_service(
    repo: SQLiteMetadataRepository = Depends(get_repository),
) -> KeywordHighlightService:
    return KeywordHighlightService(repository=repo, player=None)  # type: ignore


@api_router.get("/videos")
def list_videos(repo: SQLiteMetadataRepository = Depends(get_repository)):
    """Returns all playable video records stored in the database using lightweight summaries."""
    records = repo.list_summaries()
    playable = []
    for r in records:
        if r.status == PipelineStatus.SUCCESS and r.merged_video_path:
            p = Path(r.merged_video_path)
            if p.exists():
                playable.append({
                    "video_id": r.video_id,
                    "title": r.title or r.video_id,
                    "channel": r.channel,
                    "duration_seconds": r.duration_seconds,
                    "thumbnail_url": r.thumbnail_url,
                    "stream_url": f"/api/videos/{r.video_id}/stream",
                })
    return {"videos": playable}


@api_router.get("/videos/{video_id}")
def get_video(video_id: str, repo: SQLiteMetadataRepository = Depends(get_repository)):
    """Returns details for a specific video."""
    record = repo.get_by_video_id(video_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Video '{video_id}' not found")

    return {
        "video_id": record.video_id,
        "title": record.title or record.video_id,
        "channel": record.channel,
        "duration_seconds": record.duration_seconds,
        "thumbnail_url": record.thumbnail_url,
        "stream_url": f"/api/videos/{record.video_id}/stream",
    }


@api_router.get("/search")
def global_search(
    keyword: str = Query(..., min_length=1, description="Search keyword across all saved videos"),
    padding: float = Query(0.6, ge=0.0, le=5.0, description="Padding in seconds around keyword utterance"),
    merge_gap: float = Query(0.5, ge=0.0, le=5.0, description="Merge gap threshold in seconds"),
    repo: SQLiteMetadataRepository = Depends(get_repository),
    highlight_service: KeywordHighlightService = Depends(get_highlight_service),
):
    """Searches across all playable videos in the library, returning an aggregated sequential playlist for zero-click autoplay."""
    all_records = repo.list_all()
    playable_records = [
        r for r in all_records
        if r.status == PipelineStatus.SUCCESS and r.merged_video_path and Path(r.merged_video_path).exists()
    ]

    result = highlight_service.search_all_records(
        records=playable_records,
        keyword=keyword,
        padding=padding,
        merge_gap=merge_gap,
    )

    clips_data = [
        {
            "clip_id": c.clip_id,
            "video_id": c.video_id,
            "video_title": c.video_title,
            "stream_url": c.stream_url,
            "subtitles_url": c.subtitles_url,
            "segment_indices": c.segment_indices,
            "start": c.start,
            "end": c.end,
            "duration": c.duration,
            "text": c.text,
            "matched_words": c.matched_words,
        }
        for c in result.clips
    ]

    return {
        "keyword": result.keyword,
        "total_videos_matched": result.total_videos_matched,
        "total_matches": result.total_matches,
        "total_clips": result.total_clips,
        "total_duration": result.total_duration,
        "clips": clips_data,
    }


@api_router.get("/videos/{video_id}/search")
def search_keyword(
    video_id: str,
    keyword: str = Query(..., min_length=1, description="Search keyword"),
    padding: float = Query(0.6, ge=0.0, le=5.0, description="Padding in seconds around keyword utterance"),
    merge_gap: float = Query(0.5, ge=0.0, le=5.0, description="Merge gap threshold in seconds"),
    repo: SQLiteMetadataRepository = Depends(get_repository),
    highlight_service: KeywordHighlightService = Depends(get_highlight_service),
):
    """Searches subtitles for the keyword in a specific video."""
    record = repo.get_by_video_id(video_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Video '{video_id}' not found")

    result = highlight_service.search_sentence_clips(
        record=record,
        keyword=keyword,
        padding=padding,
        merge_gap=merge_gap,
    )

    clips_data = [
        {
            "clip_id": c.clip_id,
            "video_id": c.video_id,
            "video_title": c.video_title,
            "stream_url": c.stream_url,
            "subtitles_url": c.subtitles_url,
            "segment_indices": c.segment_indices,
            "start": c.start,
            "end": c.end,
            "duration": c.duration,
            "text": c.text,
            "matched_words": c.matched_words,
        }
        for c in result.clips
    ]

    return {
        "video_id": result.video_id,
        "keyword": result.keyword,
        "total_matches": result.total_matches,
        "total_clips": result.total_clips,
        "total_duration": result.total_duration,
        "clips": clips_data,
    }


@api_router.api_route("/videos/{video_id}/stream", methods=["GET", "HEAD"])
def stream_video(
    video_id: str,
    request: Request,
    repo: SQLiteMetadataRepository = Depends(get_repository),
):
    """Streams the MP4 video with HTTP 206 Partial Content range support."""
    record = repo.get_by_video_id(video_id)
    if not record or not record.merged_video_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Video media for '{video_id}' not found")

    file_path = Path(record.merged_video_path)
    return range_stream_file(file_path=file_path, request=request, media_type="video/mp4")


@api_router.get("/videos/{video_id}/subtitles.vtt")
def get_subtitles_vtt(
    video_id: str,
    repo: SQLiteMetadataRepository = Depends(get_repository),
):
    """Returns WebVTT subtitle track if available for in-browser video display."""
    record = repo.get_by_video_id(video_id)
    if not record or not record.subtitle_srt_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitles not found")

    srt_path = Path(record.subtitle_srt_path)
    if not srt_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle file not found")

    # Convert SRT timestamps to WebVTT format without mutating commas in dialogue text
    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    vtt_body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_content)
    vtt_content = f"WEBVTT\n\n{vtt_body}"

    return Response(content=vtt_content, media_type="text/vtt; charset=utf-8")

