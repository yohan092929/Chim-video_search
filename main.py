import argparse
import sys
import json
import logging
from pathlib import Path

from config import config
from core.models import PipelineStatus
from core.orchestrator import PipelineOrchestrator
from adapters.database.sqlite import SQLiteMetadataRepository
from adapters.storage.local import LocalStorageBackend
from adapters.downloader.ytdlp import YtDlpDownloader
from adapters.processor.ffmpeg import FFmpegMuxer
from adapters.transcriber.whisperx import WhisperXTranscriber
from adapters.player.ffplay import FFplayPlayer
from core.player import VideoPlayerService


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def list_records(repo: SQLiteMetadataRepository):
    records = repo.list_summaries()
    if not records:

        print("No video records found in database.")
        return

    print(f"\nFound {len(records)} record(s):")
    print("-" * 90)
    print(f"{'Status':<12} | {'Video ID':<15} | {'Duration':<8} | {'Title':<45}")
    print("-" * 90)
    for r in records:
        title_truncated = (r.title[:42] + "...") if len(r.title) > 45 else r.title
        dur = f"{r.duration_seconds}s"
        print(f"{r.status.value:<12} | {r.video_id:<15} | {dur:<8} | {title_truncated:<45}")
    print("-" * 90)


def show_record(repo: SQLiteMetadataRepository, video_id: str):
    r = repo.get_by_video_id(video_id)
    if not r:
        print(f"Record with ID '{video_id}' not found.")
        return

    print("\n" + "=" * 60)
    print(f"Video ID:    {r.video_id}")
    print(f"Title:       {r.title}")
    print(f"Channel:     {r.channel}")
    print(f"Duration:    {r.duration_seconds}s")
    print(f"Status:      {r.status.value}")
    print(f"Video Path:  {r.merged_video_path}")
    print(f"Audio Path:  {r.audio_path}")
    print(f"SRT Path:    {r.subtitle_srt_path}")
    print(f"JSON Path:   {r.subtitle_json_path}")
    if r.error_message:
        print(f"Error:       {r.error_message}")
    
    if r.subtitle_stamp:
        try:
            segments = json.loads(r.subtitle_stamp)
            print(f"\nSubtitle Segments ({len(segments)} segments):")
            for seg in segments[:3]:  # preview first 3 segments
                print(f"  [{seg.get('start', 0):.2f}s - {seg.get('end', 0):.2f}s]: {seg.get('text', '').strip()}")
                words = seg.get("words", [])
                if words:
                    word_preview = " ".join(f"{w.get('word')}({w.get('start', 0):.2f}-{w.get('end', 0):.2f})" for w in words[:5])
                    print(f"    Words: {word_preview} ...")
            if len(segments) > 3:
                print(f"  ... ({len(segments) - 3} more segments)")
        except Exception as e:
            print(f"Failed to parse subtitle_stamp: {e}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Decoupled YouTube Media Ingestion & WhisperX Subtitle Processing Pipeline"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", type=str, help="Single YouTube video URL to process")
    group.add_argument("--file", type=str, help="Path to text file containing YouTube URLs (one per line)")
    group.add_argument("--list", action="store_true", help="List all processed video records in the database")
    group.add_argument("--show", type=str, help="Show full details and subtitle stamp for a specific video ID")
    group.add_argument("--play", type=str, help="Play video from DB using ffplay by video ID")
    group.add_argument("--ui", "--web", action="store_true", help="Launch Web UI search and highlight player")

    # UI options
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Web server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open web browser")

    # Player options
    player_group = parser.add_argument_group("Playback Options")
    player_group.add_argument("--seek", "--start", dest="seek", type=float, default=None, help="Start playback position in seconds (e.g. --seek 12.5)")
    player_group.add_argument("--duration", "-t", type=float, default=None, help="Playback duration in seconds")
    player_group.add_argument("--segment", type=int, default=None, help="Play a specific subtitle segment by index (0-based)")
    player_group.add_argument("--search", type=str, default=None, help="Search spoken text in subtitles and play from match")
    player_group.add_argument("--highlight", type=str, default=None, help="Search spoken keyword in subtitles and play key utterance highlights sequentially or concatenated")
    player_group.add_argument("--mode", type=str, choices=["sequential", "concat"], default="sequential", help="Playback mode for highlights ('sequential' or 'concat'). Default: sequential")
    player_group.add_argument("--padding", type=float, default=0.6, help="Padding in seconds before/after keyword utterance (default: 0.6)")
    player_group.add_argument("--merge-gap", type=float, default=0.5, help="Merge adjacent highlight clips within this gap in seconds (default: 0.5)")
    player_group.add_argument("--export-highlight", type=str, default=None, help="File path to save the concatenated highlight video")
    player_group.add_argument("--no-sub", action="store_true", help="Disable subtitle overlay")
    player_group.add_argument("--fs", "--fullscreen", dest="fullscreen", action="store_true", help="Play in fullscreen mode")
    player_group.add_argument("--volume", type=int, default=None, help="Playback volume (0-100)")

    parser.add_argument(
        "--model",
        type=str,
        default=config.whisper_model_name,
        help=f"WhisperX model size (tiny, base, small, medium, large-v2). Default: {config.whisper_model_name}",
    )
    parser.add_argument(
        "--device",
        type=str,
        choices=["cpu", "cuda"],
        default=None,
        help="Execution device (defaults to cuda if available, else cpu)",
    )
    parser.add_argument(
        "--compute-type",
        type=str,
        default=None,
        help="Quantization/compute type for ctranslate2 (e.g. int8, float32, float16)",
    )
    parser.add_argument("--force", action="store_true", help="Re-process video even if already SUCCESS in database")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Initialize adapters
    repo = SQLiteMetadataRepository()
    storage = LocalStorageBackend()

    if args.list:
        list_records(repo)
        return

    if args.show:
        show_record(repo, args.show)
        return

    if args.ui:
        import uvicorn
        import threading
        import time
        import webbrowser

        url = f"http://{args.host}:{args.port}"
        print("=" * 60)
        print("🎬 Video Search & Highlight Player UI")
        print(f"   Server running at: {url}")
        print("   Press Ctrl+C to terminate.")
        print("=" * 60)

        if not args.no_browser:
            def open_b():
                time.sleep(1.0)
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            threading.Thread(target=open_b, daemon=True).start()

        uvicorn.run("adapters.web.app:app", host=args.host, port=args.port, log_level="info")
        return

    if args.play:
        player = FFplayPlayer()
        muxer = FFmpegMuxer()
        player_service = VideoPlayerService(repository=repo, player=player, concatenator=muxer)
        try:
            if args.highlight:
                player_service.play_highlight(
                    video_id=args.play,
                    keyword=args.highlight,
                    mode=args.mode,
                    with_subtitles=not args.no_sub,
                    padding=args.padding,
                    merge_gap=args.merge_gap,
                    export_path=args.export_highlight,
                    fullscreen=args.fullscreen,
                    volume=args.volume,
                )
            elif args.segment is not None:
                player_service.play_segment(
                    video_id=args.play,
                    segment_index=args.segment,
                    with_subtitles=not args.no_sub,
                    fullscreen=args.fullscreen,
                    volume=args.volume,
                )
            elif args.search:
                player_service.search_and_play(
                    video_id=args.play,
                    query=args.search,
                    with_subtitles=not args.no_sub,
                    duration=args.duration,
                    fullscreen=args.fullscreen,
                    volume=args.volume,
                )
            else:
                player_service.play_by_id(
                    video_id=args.play,
                    with_subtitles=not args.no_sub,
                    start_time=args.seek,
                    duration=args.duration,
                    fullscreen=args.fullscreen,
                    volume=args.volume,
                )
        except (ValueError, FileNotFoundError, IndexError) as e:
            print(f"\n[Error] {e}")
            sys.exit(1)
        return

    downloader = YtDlpDownloader()
    transcriber = WhisperXTranscriber(
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=config.batch_size,
    )
    muxer = FFmpegMuxer()

    orchestrator = PipelineOrchestrator(
        repository=repo,
        storage=storage,
        downloader=downloader,
        transcriber=transcriber,
        muxer=muxer,
    )

    if args.url:
        print(f"\nProcessing single video: {args.url}")
        record = orchestrator.process_url(args.url, force=args.force)
        print(f"\n[Completed] Status: {record.status.value}")
        show_record(repo, record.video_id)

    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {args.file}")
            sys.exit(1)

        with open(file_path, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        print(f"\nProcessing batch of {len(urls)} video(s) from: {args.file}")
        orchestrator.process_batch(urls, force=args.force)
        list_records(repo)


if __name__ == "__main__":
    main()
