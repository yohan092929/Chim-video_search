import unittest
from unittest.mock import MagicMock, patch
import tempfile
import json
from pathlib import Path

from core.models import VideoRecord, PipelineStatus, HighlightClip, HighlightSearchResult
from core.interfaces import MediaPlayer, MetadataRepository, ClipConcatenator
from core.highlight import KeywordHighlightService
from core.player import VideoPlayerService
from adapters.processor.ffmpeg import FFmpegMuxer, _parse_srt_timestamp, _format_srt_timestamp


class MockPlayer(MediaPlayer):
    def __init__(self):
        self.play_calls = []

    def play(
        self,
        media_path: str,
        subtitle_path=None,
        start_time=None,
        duration=None,
        title=None,
        auto_exit=True,
        fullscreen=False,
        volume=None,
        wait=True,
    ):
        call_info = {
            "media_path": media_path,
            "subtitle_path": subtitle_path,
            "start_time": start_time,
            "duration": duration,
            "title": title,
            "auto_exit": auto_exit,
            "fullscreen": fullscreen,
            "volume": volume,
            "wait": wait,
        }
        self.play_calls.append(call_info)
        return MagicMock()


class MockRepo(MetadataRepository):
    def __init__(self, records=None):
        self.records = {r.video_id: r for r in (records or [])}

    def get_by_video_id(self, video_id: str):
        return self.records.get(video_id)

    def list_all(self):
        return list(self.records.values())

    def upsert_record(self, record):
        self.records[record.video_id] = record

    def update_status(self, video_id, status, error_message=None):
        pass

    def update_paths_and_subtitles(self, video_id, **kwargs):
        pass


class MockConcatenator(ClipConcatenator):
    def __init__(self):
        self.concat_calls = []

    def create_highlight_video(self, source_video_path, clips, output_path, subtitle_path=None):
        self.concat_calls.append({
            "source_video_path": source_video_path,
            "clips": clips,
            "output_path": output_path,
            "subtitle_path": subtitle_path,
        })
        Path(output_path).write_text("mock concatenated video")
        return output_path


class TestKeywordHighlightService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.video_file = self.temp_path / "test_video.mp4"
        self.video_file.write_text("dummy mp4")
        self.srt_file = self.temp_path / "test_sub.srt"
        self.srt_file.write_text("1\n00:00:04,420 --> 00:00:07,500\n쇼핑 하나 하죠\n")

        self.sample_segments = [
            {
                "start": 4.42,
                "end": 7.50,
                "text": "쇼핑 하나 하죠 쇼핑 여러분들은 뭐 시계 어때요?",
                "words": [
                    {"word": "쇼핑", "start": 4.42, "end": 4.76},
                    {"word": "시계", "start": 6.30, "end": 6.80},
                ],
            },
            {
                "start": 7.90,
                "end": 10.85,
                "text": "시계 시계 그러면 고가의 시계만 한번 구경하죠",
                "words": [
                    {"word": "시계", "start": 7.90, "end": 8.20},
                    {"word": "고가의", "start": 9.00, "end": 9.50},
                ],
            },
            {
                "start": 470.0,
                "end": 473.0,
                "text": "혁준상 롤렉스 샀어?",
                "words": [
                    {"word": "혁준상", "start": 470.0, "end": 471.0},
                    {"word": "롤렉스", "start": 471.2, "end": 472.0},
                    {"word": "샀어?", "start": 472.1, "end": 473.0},
                ],
            },
            {
                "start": 505.0,
                "end": 510.0,
                "text": "그러면 롤렉스를 왜 샀어요?",
                "words": [
                    {"word": "그러면", "start": 505.0, "end": 506.0},
                    {"word": "롤렉스를", "start": 506.5, "end": 508.0},
                    {"word": "샀어요?", "start": 508.5, "end": 510.0},
                ],
            },
        ]

        self.record = VideoRecord(
            video_id="test_vid_1",
            url="https://youtube.com/watch?v=test_vid_1",
            title="명품시계 쇼핑",
            channel="침착맨",
            duration_seconds=600,
            status=PipelineStatus.SUCCESS,
            merged_video_path=str(self.video_file),
            subtitle_srt_path=str(self.srt_file),
            subtitle_stamp=json.dumps(self.sample_segments),
        )

        self.repo = MockRepo([self.record])
        self.player = MockPlayer()
        self.concatenator = MockConcatenator()
        self.service = KeywordHighlightService(
            repository=self.repo,
            player=self.player,
            concatenator=self.concatenator,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_sentence_clips_multiple_results(self):
        # Searching "롤렉스" -> WhisperX word-level timestamps:
        # Segment 2: '롤렉스' at [471.2, 472.0] -> with padding 0.2: [471.0, 472.2]
        # Segment 3: '롤렉스를' at [506.5, 508.0] -> with padding 0.2: [506.3, 508.2]
        res = self.service.search_sentence_clips(self.record, keyword="롤렉스", padding=0.2, merge_gap=0.5)
        self.assertEqual(res.total_matches, 2)
        self.assertEqual(res.total_clips, 2)
        self.assertEqual(len(res.clips), 2)

        clip1 = res.clips[0]
        self.assertEqual(clip1.clip_id, 1)
        self.assertAlmostEqual(clip1.start, 471.0)  # 471.2 - 0.2
        self.assertAlmostEqual(clip1.end, 472.2)    # 472.0 + 0.2
        self.assertIn("롤렉스", clip1.text)

        clip2 = res.clips[1]
        self.assertEqual(clip2.clip_id, 2)
        self.assertAlmostEqual(clip2.start, 506.3)  # 506.5 - 0.2
        self.assertAlmostEqual(clip2.end, 508.2)    # 508.0 + 0.2
        self.assertIn("롤렉스를", clip2.text)

    def test_search_sentence_clips_merge_adjacent(self):
        # "시계" at [6.30, 6.80] and [7.90, 8.20]
        # With padding=0.5: [5.80, 7.30] and [7.40, 8.70]
        # Gap between 7.30 and 7.40 is 0.10 <= merge_gap(0.5) -> should be merged!
        res = self.service.search_sentence_clips(self.record, keyword="시계", padding=0.5, merge_gap=0.5)
        self.assertEqual(res.total_matches, 2)
        self.assertEqual(res.total_clips, 1)  # Merged into 1 unified clip
        clip = res.clips[0]
        self.assertAlmostEqual(clip.start, 5.80)   # 6.30 - 0.5
        self.assertAlmostEqual(clip.end, 8.70)     # 8.20 + 0.5
        self.assertIn("시계", clip.text)

    def test_search_sentence_clips_no_merge_when_gap_zero(self):
        # Without merging (merge_gap=0.0 and no padding), they remain 2 separate clips at exact word timestamps
        res = self.service.search_sentence_clips(self.record, keyword="시계", padding=0.0, merge_gap=0.0)
        self.assertEqual(res.total_matches, 2)
        self.assertEqual(res.total_clips, 2)
        self.assertEqual(res.clips[0].start, 6.30)
        self.assertEqual(res.clips[1].start, 7.90)

    def test_search_sentence_clips_no_match(self):
        res = self.service.search_sentence_clips(self.record, keyword="페라리")
        self.assertEqual(res.total_matches, 0)
        self.assertEqual(res.total_clips, 0)
        self.assertEqual(len(res.clips), 0)
        self.assertEqual(res.total_duration, 0.0)

    def test_search_sentence_clips_empty_subtitles(self):
        rec_empty = VideoRecord(
            video_id="empty_sub",
            url="https://youtube.com/watch?v=empty",
            title="Empty",
            channel="Channel",
            duration_seconds=100,
            status=PipelineStatus.SUCCESS,
            subtitle_stamp=None,
        )
        res = self.service.search_sentence_clips(rec_empty, keyword="롤렉스")
        self.assertEqual(res.total_matches, 0)
        self.assertEqual(res.total_clips, 0)

    def test_play_highlight_sequential_multiple_clips(self):
        # Sequential playback of "롤렉스" (2 clips) at exact word timestamps [471.2-472.0] and [506.5-508.0]
        res = self.service.play_highlight(
            video_id="test_vid_1",
            keyword="롤렉스",
            mode="sequential",
            padding=0.0,
            merge_gap=0.0,
        )
        self.assertEqual(res.total_clips, 2)
        self.assertEqual(len(self.player.play_calls), 2)

        call1 = self.player.play_calls[0]
        self.assertEqual(call1["start_time"], 471.2)
        self.assertAlmostEqual(call1["duration"], 0.8)

        call2 = self.player.play_calls[1]
        self.assertEqual(call2["start_time"], 506.5)
        self.assertAlmostEqual(call2["duration"], 1.5)

    def test_play_highlight_concat_mode(self):
        # Concat mode for "롤렉스" -> should call concatenator and play the merged video once
        res = self.service.play_highlight(
            video_id="test_vid_1",
            keyword="롤렉스",
            mode="concat",
            padding=0.0,
            merge_gap=0.0,
        )
        self.assertEqual(len(self.concatenator.concat_calls), 1)
        concat_call = self.concatenator.concat_calls[0]
        self.assertEqual(len(concat_call["clips"]), 2)

        # Player should be invoked once with the concatenated video
        self.assertEqual(len(self.player.play_calls), 1)
        self.assertEqual(self.player.play_calls[0]["start_time"], 0.0)

    def test_play_highlight_single_match(self):
        # Search for "고가의" -> word timestamp is [9.00 - 9.50]
        res = self.service.play_highlight(
            video_id="test_vid_1",
            keyword="고가의",
            mode="sequential",
            padding=0.0,
        )
        self.assertEqual(res.total_matches, 1)
        self.assertEqual(res.total_clips, 1)
        self.assertEqual(len(self.player.play_calls), 1)
        self.assertEqual(self.player.play_calls[0]["start_time"], 9.00)
        self.assertAlmostEqual(self.player.play_calls[0]["duration"], 0.5)

    def test_play_highlight_not_found(self):
        res = self.service.play_highlight(
            video_id="test_vid_1",
            keyword="존재하지않는단어",
        )
        self.assertEqual(res.total_clips, 0)
        self.assertEqual(len(self.player.play_calls), 0)


class TestVideoPlayerServiceIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.video_file = self.temp_path / "video.mp4"
        self.video_file.write_text("sample")

        self.segments = [
            {"start": 10.0, "end": 15.0, "text": "첫 번째 문장 키워드 포함"},
            {"start": 30.0, "end": 35.0, "text": "두 번째 문장 키워드 포함"},
        ]
        self.record = VideoRecord(
            video_id="v_integration",
            url="https://youtube.com/v_integration",
            title="통합 테스트",
            channel="Test",
            duration_seconds=100,
            status=PipelineStatus.SUCCESS,
            merged_video_path=str(self.video_file),
            subtitle_stamp=json.dumps(self.segments),
        )
        self.repo = MockRepo([self.record])
        self.player = MockPlayer()
        self.concatenator = MockConcatenator()
        self.service = VideoPlayerService(
            repository=self.repo,
            player=self.player,
            concatenator=self.concatenator,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_search_highlights(self):
        res = self.service.search_highlights("v_integration", "키워드", padding=0.0)
        self.assertEqual(res.total_matches, 2)
        self.assertEqual(res.total_clips, 2)

    def test_play_highlight(self):
        res = self.service.play_highlight("v_integration", "키워드", mode="sequential", padding=0.0)
        self.assertEqual(len(self.player.play_calls), 2)
        self.assertEqual(self.player.play_calls[0]["start_time"], 10.0)
        self.assertEqual(self.player.play_calls[1]["start_time"], 30.0)


class TestFFmpegMuxerHighlightHelpers(unittest.TestCase):
    def test_parse_and_format_srt_timestamp(self):
        self.assertEqual(_format_srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(_format_srt_timestamp(65.432), "00:01:05,432")
        self.assertAlmostEqual(_parse_srt_timestamp("00:01:05,432"), 65.432, places=2)

    def test_rebase_subtitles(self):
        muxer = FFmpegMuxer()
        clips = [
            HighlightClip(clip_id=1, segment_indices=[0], start=10.0, end=15.0, duration=5.0, text="첫번째 클립"),
            HighlightClip(clip_id=2, segment_indices=[1], start=30.0, end=34.0, duration=4.0, text="두번째 클립"),
        ]
        with tempfile.TemporaryDirectory() as td:
            out_srt = Path(td) / "rebased.srt"
            muxer.rebase_subtitles(clips, str(out_srt))
            self.assertTrue(out_srt.exists())
            content = out_srt.read_text(encoding="utf-8")
            self.assertIn("00:00:00,000 --> 00:00:05,000", content)
            self.assertIn("첫번째 클립", content)
            self.assertIn("00:00:05,000 --> 00:00:09,000", content)
            self.assertIn("두번째 클립", content)

    def test_search_phrase_with_punctuation(self):
        repo = MockRepo([])
        player = MockPlayer()
        service = KeywordHighlightService(repository=repo, player=player)

        segments = [
            {
                "start": 5.0,
                "end": 8.0,
                "text": "시계, 포르쉐! 정말 좋습니다.",
                "words": [
                    {"word": "시계,", "start": 5.0, "end": 5.8},
                    {"word": "포르쉐!", "start": 6.0, "end": 6.9},
                    {"word": "정말", "start": 7.0, "end": 7.5},
                    {"word": "좋습니다.", "start": 7.6, "end": 8.0},
                ],
            }
        ]
        rec = VideoRecord(
            video_id="v_punct",
            url="https://youtube.com/watch?v=v_punct",
            title="Punctuation Test",
            channel="Channel",
            duration_seconds=10,
            subtitle_stamp=json.dumps(segments),
        )

        res = service.search_sentence_clips(rec, "시계 포르쉐", padding=0.0)
        self.assertEqual(res.total_clips, 1)
        self.assertAlmostEqual(res.clips[0].start, 5.0)

    def test_search_all_records(self):
        repo = MockRepo([])
        player = MockPlayer()
        service = KeywordHighlightService(repository=repo, player=player)

        seg1 = [{"start": 1.0, "end": 3.0, "text": "롤렉스 시계", "words": [{"word": "롤렉스", "start": 1.0, "end": 2.0}]}]
        seg2 = [{"start": 10.0, "end": 14.0, "text": "새로운 롤렉스 모델", "words": [{"word": "롤렉스", "start": 11.0, "end": 12.0}]}]

        rec1 = VideoRecord(video_id="vid_1", url="url1", title="Video 1", channel="Ch1", duration_seconds=50, subtitle_stamp=json.dumps(seg1))
        rec2 = VideoRecord(video_id="vid_2", url="url2", title="Video 2", channel="Ch2", duration_seconds=60, subtitle_stamp=json.dumps(seg2))

        cross_res = service.search_all_records([rec1, rec2], "롤렉스", padding=0.0)
        self.assertEqual(cross_res.total_videos_matched, 2)
        self.assertEqual(cross_res.total_clips, 2)
        self.assertEqual(cross_res.clips[0].clip_id, 1)
        self.assertEqual(cross_res.clips[0].video_id, "vid_1")
        self.assertEqual(cross_res.clips[0].video_title, "Video 1")
        self.assertEqual(cross_res.clips[1].clip_id, 2)
        self.assertEqual(cross_res.clips[1].video_id, "vid_2")
        self.assertEqual(cross_res.clips[1].video_title, "Video 2")


if __name__ == "__main__":
    unittest.main()

