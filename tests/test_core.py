import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bilibili_subtitles import core
except ImportError:
    core = None

InvalidBilibiliUrl = getattr(core, "InvalidBilibiliUrl", None)
choose_subtitle = getattr(core, "choose_subtitle", None)
parse_bv_url = getattr(core, "parse_bv_url", None)
parse_srt = getattr(core, "parse_srt", None)
render_markdown = getattr(core, "render_markdown", None)


class ParseBvUrlTests(unittest.TestCase):
    def test_canonicalizes_direct_bv_url_and_removes_part_query(self):
        self.assertIsNotNone(parse_bv_url, "parse_bv_url must exist")

        parsed = parse_bv_url(
            "https://www.bilibili.com/video/BV1Ab411C7De/?p=3&spm_id_from=333.1007"
        )

        self.assertEqual(parsed.bvid, "BV1Ab411C7De")
        self.assertEqual(parsed.page, 3)
        self.assertEqual(
            parsed.canonical_url,
            "https://www.bilibili.com/video/BV1Ab411C7De",
        )

    def test_rejects_urls_outside_supported_https_bv_video_scope(self):
        unsupported_urls = [
            "http://www.bilibili.com/video/BV1Ab411C7De",
            "https://b23.tv/example",
            "https://example.com/video/BV1Ab411C7De",
            "https://www.bilibili.com/bangumi/play/ep123",
            "https://www.bilibili.com/video/not-a-bvid",
            "https://user@www.bilibili.com/video/BV1Ab411C7De",
            "https://www.bilibili.com:443/video/BV1Ab411C7De",
            "https://www.bilibili.com/video//BV1Ab411C7De",
            "https://www.bilibili.com/video/BV1Ab411C7De?p=0",
            "https://www.bilibili.com/video/BV1Ab411C7De?p=abc",
            "https://www.bilibili.com/video/BV1Ab411C7De?p=1&p=2",
        ]

        for raw_url in unsupported_urls:
            with self.subTest(raw_url=raw_url):
                with self.assertRaises(InvalidBilibiliUrl):
                    parse_bv_url(raw_url)


class ChooseSubtitleTests(unittest.TestCase):
    def test_prefers_simplified_chinese_and_excludes_danmaku(self):
        self.assertIsNotNone(choose_subtitle, "choose_subtitle must exist")
        subtitles = {
            "danmaku": [{"ext": "xml", "data": "<i>not captions</i>"}],
            "en": [{"ext": "srt", "data": "English captions"}],
            "zh-Hans": [{"ext": "srt", "data": "Simplified captions"}],
        }

        selected = choose_subtitle(subtitles)

        self.assertEqual(selected.language, "zh-Hans")
        self.assertEqual(selected.srt, "Simplified captions")

    def test_falls_back_to_other_chinese_before_non_chinese(self):
        subtitles = {
            "danmaku": [{"ext": "xml", "data": "<i>not captions</i>"}],
            "en": [{"ext": "srt", "data": "English captions"}],
            "zh-TW": [{"ext": "srt", "data": "Chinese captions"}],
        }

        selected = choose_subtitle(subtitles)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.language, "zh-TW")
        self.assertEqual(selected.srt, "Chinese captions")

    def test_treats_bilibili_ai_zh_as_chinese(self):
        subtitles = {
            "en": [{"ext": "srt", "data": "English captions"}],
            "ai-zh": [{"ext": "srt", "data": "AI Chinese captions"}],
        }

        selected = choose_subtitle(subtitles)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.language, "ai-zh")
        self.assertEqual(selected.srt, "AI Chinese captions")

    def test_falls_back_to_first_caption_language_but_never_danmaku(self):
        subtitles = {
            "danmaku": [{"ext": "xml", "data": "<i>not captions</i>"}],
            "en": [{"ext": "srt", "data": "English captions"}],
            "ja": [{"ext": "srt", "data": "Japanese captions"}],
        }

        selected = choose_subtitle(subtitles)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.language, "en")
        self.assertEqual(selected.srt, "English captions")


class ParseSrtTests(unittest.TestCase):
    def test_parses_millisecond_timestamps_and_normalizes_multiline_text(self):
        self.assertIsNotNone(parse_srt, "parse_srt must exist")
        srt = (
            "1\r\n"
            "00:00:01,250 --> 00:00:03,000\r\n"
            "第一行\r\n"
            "第二行\r\n\r\n"
            "2\n"
            "00:01:02.500 --> 00:01:04.000\n"
            "下一段\n"
        )

        captions = parse_srt(srt)

        self.assertEqual(
            [(item.start_ms, item.end_ms, item.text) for item in captions],
            [
                (1250, 3000, "第一行 第二行"),
                (62500, 64000, "下一段"),
            ],
        )


class RenderMarkdownTests(unittest.TestCase):
    def test_renders_metadata_and_timestamped_caption_lines(self):
        self.assertIsNotNone(render_markdown, "render_markdown must exist")
        metadata = core.PartMetadata(
            bvid="BV1Ab411C7De",
            part_number=2,
            title="第二部分",
            uploader="示例UP主",
            source_url="https://www.bilibili.com/video/BV1Ab411C7De?p=2",
            language="zh-Hans",
            extracted_at="2026-08-14T18:30:00+08:00",
        )
        captions = [
            core.Caption(start_ms=1250, end_ms=3000, text="第一段字幕"),
            core.Caption(start_ms=3_723_000, end_ms=3_725_000, text="后一段字幕"),
        ]

        markdown = render_markdown(metadata, captions)

        self.assertIn("# 第二部分", markdown)
        self.assertIn("- BV: `BV1Ab411C7De`", markdown)
        self.assertIn("- Part: `2`", markdown)
        self.assertIn("- Uploader: 示例UP主", markdown)
        self.assertIn("- Subtitle language: `zh-Hans`", markdown)
        self.assertIn("- Extracted at: `2026-08-14T18:30:00+08:00`", markdown)
        self.assertIn(
            "- Extraction method: existing Bilibili captions (no speech-to-text)",
            markdown,
        )
        self.assertIn("[00:00:01] 第一段字幕", markdown)
        self.assertIn("[01:02:03] 后一段字幕", markdown)


if __name__ == "__main__":
    unittest.main()
