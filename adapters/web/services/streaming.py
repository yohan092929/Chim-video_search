import re
from pathlib import Path
from typing import Generator
from fastapi import Request, HTTPException, status
from fastapi.responses import StreamingResponse, Response

CHUNK_SIZE = 1024 * 1024  # 1MB chunks


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    """Parses HTTP Range header string (e.g. 'bytes=0-1023' or 'bytes=1024-') into (start, end)."""
    match = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Invalid Range header format",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    start_str, end_str = match.groups()
    if not start_str and not end_str:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Empty Range header",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    else:  # suffix range (e.g. bytes=-500)
        start = max(0, file_size - int(end_str))
        end = file_size - 1

    if start > end or start >= file_size:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail="Requested range out of bounds",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    end = min(end, file_size - 1)
    return start, end


def file_iterator(file_path: Path, start: int, end: int, chunk_size: int = CHUNK_SIZE) -> Generator[bytes, None, None]:
    """Yields chunks of a file between start and end inclusive."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            data = f.read(read_size)
            if not data:
                break
            remaining -= len(data)
            yield data


def range_stream_file(file_path: Path, request: Request, media_type: str = "video/mp4") -> Response:
    """Streams a file handling HTTP Range headers and HEAD requests for media seeking."""
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file not found")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if not range_header:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": media_type,
        }
        if request.method.upper() == "HEAD":
            return Response(status_code=status.HTTP_200_OK, headers=headers, media_type=media_type)
        return StreamingResponse(
            file_iterator(file_path, 0, file_size - 1),
            status_code=status.HTTP_200_OK,
            headers=headers,
            media_type=media_type,
        )

    start, end = parse_range_header(range_header, file_size)
    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
    }
    if request.method.upper() == "HEAD":
        return Response(status_code=status.HTTP_206_PARTIAL_CONTENT, headers=headers, media_type=media_type)
    return StreamingResponse(
        file_iterator(file_path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
        media_type=media_type,
    )
