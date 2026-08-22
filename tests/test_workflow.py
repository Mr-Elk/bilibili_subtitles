import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bilibili_subtitles import workflow
except ImportError:
    workflow = None

extract_to_markdown = getattr(workflow, "extract_to_markdown", None)
NoSubtitlesError = getattr(workflow, "NoSubtitlesError", RuntimeError)
UnsupportedVideoStructureError = getattr(
    workflow,
    "UnsupportedVideoStructureError",
    RuntimeError,
)
PartSelectionRequiredError = getattr(
    workflow,
    "PartSelectionRequiredError",
    ValueError,
)
MissingConcurrentExtractionError = type(
    "MissingConcurrentExtractionError",
    (Exception,),
    {},
)
ConcurrentExtractionError = getattr(
    workflow,
    "ConcurrentExtractionError",
    MissingConcurrentExtractionError,
)


class ExtractToMarkdownTests(unittest.TestCase):
    def test_successful_rerun_replaces_stale_files(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Fresh captions",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n新字幕\n",
                    }
                ]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            output_dir = output_root / "BV1Ab411C7De"
            output_dir.mkdir()
            (output_dir / "stale.md").write_text("stale\n", encoding="utf-8")

            extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                output_root=output_root,
                fetch_info=lambda _url: info,
                extracted_at="2026-08-14T18:30:00+08:00",
            )

            self.assertFalse((output_dir / "stale.md").exists())
            self.assertIn(
                "[00:00:01] 新字幕",
                (output_dir / "part-001.md").read_text(encoding="utf-8"),
            )

    def test_restores_previous_output_when_publishing_staging_fails(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n新字幕\n",
                    }
                ]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            output_dir = output_root / "BV1Ab411C7De"
            output_dir.mkdir()
            (output_dir / "index.md").write_text("previous index\n", encoding="utf-8")
            real_rename = Path.rename

            def fail_staging_publish(path, target):
                if (
                    path.name.startswith(".BV1Ab411C7De-")
                    and "-backup-" not in path.name
                    and target == output_dir
                ):
                    raise OSError("staging publish failed")
                return real_rename(path, target)

            with patch.object(Path, "rename", fail_staging_publish):
                with self.assertRaises(OSError):
                    extract_to_markdown(
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        output_root=output_root,
                        fetch_info=lambda _url: info,
                        extracted_at="2026-08-14T18:30:00+08:00",
                    )

            self.assertEqual(
                (output_dir / "index.md").read_text(encoding="utf-8"),
                "previous index\n",
            )
            leftovers = [
                path
                for path in output_root.iterdir()
                if path.name.startswith(".BV1Ab411C7De-")
            ]
            self.assertEqual(leftovers, [])

    def test_rejects_a_concurrent_run_for_the_same_video(self):
        first_fetch_started = threading.Event()
        release_first_fetch = threading.Event()
        first_run_errors = []
        captioned_info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n字幕\n",
                    }
                ]
            },
        }

        def blocking_fetch(_url):
            first_fetch_started.set()
            if not release_first_fetch.wait(timeout=5):
                raise TimeoutError("Test did not release the first extraction")
            return captioned_info

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)

            def first_run():
                try:
                    extract_to_markdown(
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        output_root=output_root,
                        fetch_info=blocking_fetch,
                        extracted_at="2026-08-14T18:30:00+08:00",
                    )
                except Exception as error:
                    first_run_errors.append(error)

            thread = threading.Thread(target=first_run)
            thread.start()
            self.assertTrue(first_fetch_started.wait(timeout=5))
            try:
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=output_root,
                    fetch_info=lambda _url: {
                        "_type": "video",
                        "id": "BV1Ab411C7De",
                        "title": "No captions",
                        "subtitles": {},
                    },
                    extracted_at="2026-08-14T18:30:01+08:00",
                )
            except Exception as error:
                self.assertIsInstance(
                    error,
                    ConcurrentExtractionError,
                    f"Unexpected concurrent-run error: {error}",
                )
            else:
                self.fail("A concurrent extraction for the same BV must be rejected")
            finally:
                release_first_fetch.set()
                thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(first_run_errors, [])

    def test_cleans_staging_when_existing_output_cannot_be_moved(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n新字幕\n",
                    }
                ]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            output_dir = output_root / "BV1Ab411C7De"
            output_dir.mkdir()
            (output_dir / "index.md").write_text("previous index\n", encoding="utf-8")
            real_rename = Path.rename

            def fail_output_move(path, target):
                if path == output_dir:
                    raise OSError("output directory is locked")
                return real_rename(path, target)

            with patch.object(Path, "rename", fail_output_move):
                with self.assertRaises(OSError):
                    extract_to_markdown(
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        output_root=output_root,
                        fetch_info=lambda _url: info,
                        extracted_at="2026-08-14T18:30:00+08:00",
                    )

            self.assertEqual(
                (output_dir / "index.md").read_text(encoding="utf-8"),
                "previous index\n",
            )
            staging_dirs = [
                path
                for path in output_root.iterdir()
                if path.name.startswith(".BV1Ab411C7De-")
                and "-backup-" not in path.name
            ]
            self.assertEqual(staging_dirs, [])

    def test_recovers_interrupted_backup_before_starting_a_new_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            backup_dir = output_root / ".BV1Ab411C7De-backup-interrupted"
            backup_dir.mkdir()
            (backup_dir / "index.md").write_text(
                "previous index\n",
                encoding="utf-8",
            )
            stale_staging = output_root / ".BV1Ab411C7De-interrupted"
            stale_staging.mkdir()
            (stale_staging / "partial.md").write_text("partial\n", encoding="utf-8")

            def failing_fetch(_url):
                raise RuntimeError("network unavailable")

            with self.assertRaises(RuntimeError):
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=output_root,
                    fetch_info=failing_fetch,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

            restored = output_root / "BV1Ab411C7De" / "index.md"
            self.assertTrue(restored.is_file(), "Interrupted backup must be restored")
            self.assertEqual(restored.read_text(encoding="utf-8"), "previous index\n")
            self.assertFalse(backup_dir.exists())
            self.assertFalse(stale_staging.exists())

    def test_rejects_interactive_entries_instead_of_treating_them_as_parts(self):
        canonical_url = "https://www.bilibili.com/video/BV1Ab411C7De"
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "Interactive video",
            "webpage_url": canonical_url,
            "entries": [
                {
                    "id": "BV1Ab411C7De_123456",
                    "title": "Interactive segment",
                    "webpage_url": canonical_url,
                    "subtitles": {
                        "ai-zh": [
                            {
                                "ext": "srt",
                                "data": (
                                    "1\n00:00:01,000 --> 00:00:02,000\n分支字幕\n"
                                ),
                            }
                        ]
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(UnsupportedVideoStructureError):
                extract_to_markdown(
                    canonical_url,
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

    def test_collapses_legacy_media_fragments_into_one_logical_video(self):
        canonical_url = "https://www.bilibili.com/video/BV1Ab411C7De"
        info = {
            "_type": "multi_video",
            "id": "BV1Ab411C7De",
            "title": "Legacy video",
            "uploader": "Example uploader",
            "webpage_url": canonical_url,
            "entries": [
                {
                    "id": "BV1Ab411C7De_0",
                    "title": "Legacy fragment 1",
                    "webpage_url": canonical_url,
                    "subtitles": {
                        "ai-zh": [
                            {
                                "ext": "srt",
                                "data": (
                                    "1\n00:00:01,000 --> 00:00:02,000\n完整字幕\n"
                                ),
                            }
                        ]
                    },
                },
                {
                    "id": "BV1Ab411C7De_1",
                    "title": "Legacy fragment 2",
                    "webpage_url": canonical_url,
                    "subtitles": None,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                result = extract_to_markdown(
                    canonical_url,
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )
            except UnsupportedVideoStructureError as error:
                self.fail(f"Legacy fragments should form one logical video: {error}")
            markdown = (result.output_dir / "part-001.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.no_subtitle_count, 0)
        self.assertIn("# Legacy video", markdown)
        self.assertIn(f"- Source: {canonical_url}", markdown)
        self.assertIn("[00:00:01] 完整字幕", markdown)

    def test_falls_back_when_preferred_track_has_no_usable_cues(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Fallback captions",
            "subtitles": {
                "zh-Hans": [{"ext": "srt", "data": ""}],
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n可用字幕\n",
                    }
                ],
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                result = extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )
            except NoSubtitlesError as error:
                self.fail(f"A lower-priority usable track must be selected: {error}")
            markdown = (result.output_dir / "part-001.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(result.success_count, 1)
        self.assertIn("- Subtitle language: `ai-zh`", markdown)
        self.assertIn("[00:00:01] 可用字幕", markdown)

    def test_falls_back_when_preferred_track_is_malformed(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Malformed preferred captions",
            "subtitles": {
                "zh-Hans": [
                    {
                        "ext": "srt",
                        "data": "1\ninvalid --> invalid\n损坏字幕\n",
                    }
                ],
                "en": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:03,000 --> 00:00:04,000\nfallback\n",
                    }
                ],
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                result = extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )
            except ValueError as error:
                self.fail(f"A malformed preferred track must be skipped: {error}")
            markdown = (result.output_dir / "part-001.md").read_text(
                encoding="utf-8"
            )

        self.assertIn("- Subtitle language: `en`", markdown)
        self.assertIn("[00:00:03] fallback", markdown)

    def test_writes_captioned_parts_and_marks_parts_without_subtitles(self):
        self.assertIsNotNone(extract_to_markdown, "extract_to_markdown must exist")
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "示例多P视频",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De",
            "entries": [
                {
                    "id": "BV1Ab411C7De_p1",
                    "title": "第一部分",
                    "uploader": "示例UP主",
                    "webpage_url": (
                        "https://www.bilibili.com/video/BV1Ab411C7De?p=1"
                    ),
                    "subtitles": {
                        "zh-Hans": [
                            {
                                "ext": "srt",
                                "data": (
                                    "1\n00:00:01,000 --> 00:00:03,000\n第一段字幕\n"
                                ),
                            }
                        ]
                    },
                },
                {
                    "id": "BV1Ab411C7De_p2",
                    "title": "第二部分",
                    "uploader": "示例UP主",
                    "webpage_url": (
                        "https://www.bilibili.com/video/BV1Ab411C7De?p=2"
                    ),
                    "subtitles": {
                        "danmaku": [{"ext": "xml", "data": "<i>弹幕</i>"}]
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            result = extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                output_root=Path(temp_dir),
                fetch_info=lambda _url: info,
                extracted_at="2026-08-14T18:30:00+08:00",
            )
            video_dir = Path(temp_dir) / "BV1Ab411C7De"

            self.assertEqual(result.success_count, 1)
            self.assertEqual(result.no_subtitle_count, 1)
            self.assertEqual(result.output_dir, video_dir)
            self.assertTrue((video_dir / "part-001.md").is_file())
            self.assertFalse((video_dir / "part-002.md").exists())
            self.assertIn(
                "[00:00:01] 第一段字幕",
                (video_dir / "part-001.md").read_text(encoding="utf-8"),
            )
            index = (video_dir / "index.md").read_text(encoding="utf-8")
            self.assertIn("[第一部分](part-001.md)", index)
            self.assertIn("第二部分: `no_subtitles`", index)
            manifest = json.loads(
                (video_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertTrue(manifest["coverage_complete"])
            self.assertEqual(
                [part["status"] for part in manifest["parts"]],
                ["captioned", "no_subtitles"],
            )

    def test_url_page_query_extracts_only_that_part(self):
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "示例多P视频",
            "entries": [
                {
                    "id": "BV1Ab411C7De_p1",
                    "title": "第一部分",
                    "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=1",
                    "subtitles": {
                        "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\n第一部分\n"}]
                    },
                },
                {
                    "id": "BV1Ab411C7De_p2",
                    "title": "第二部分",
                    "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                    "subtitles": {
                        "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\n第二部分\n"}]
                    },
                },
            ],
        }
        requested_urls = []

        def fake_fetch(url):
            requested_urls.append(url)
            return info

        with tempfile.TemporaryDirectory() as temp_dir:
            result = extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                output_root=Path(temp_dir),
                fetch_info=fake_fetch,
                extracted_at="2026-08-14T18:30:00+08:00",
            )
            video_dir = Path(temp_dir) / "BV1Ab411C7De"

            self.assertEqual(result.success_count, 1)
            self.assertEqual(requested_urls, ["https://www.bilibili.com/video/BV1Ab411C7De?p=2"])
            self.assertFalse((video_dir / "part-001.md").exists())
            self.assertTrue((video_dir / "part-002.md").is_file())
            manifest = json.loads(
                (video_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["coverage_complete"])
            self.assertEqual(
                manifest["last_request"],
                {"mode": "part", "part_number": 2},
            )

    def test_selected_part_atomically_updates_without_removing_other_parts(self):
        original_info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "完整合集",
            "entries": [
                {
                    "id": "BV1Ab411C7De_p1",
                    "title": "第一部分",
                    "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=1",
                    "subtitles": {
                        "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\n保留第一部分\n"}]
                    },
                },
                {
                    "id": "BV1Ab411C7De_p2",
                    "title": "第二部分",
                    "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                    "subtitles": {
                        "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\n旧第二部分\n"}]
                    },
                },
            ],
        }
        updated_part = {
            "_type": "video",
            "id": "BV1Ab411C7De_p2",
            "title": "第二部分（更新）",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
            "subtitles": {
                "ai-zh": [{"ext": "srt", "data": "1\n00:00:03,000 --> 00:00:04,000\n新第二部分\n"}]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                output_root=output_root,
                fetch_info=lambda _url: original_info,
                extracted_at="2026-08-14T18:30:00+08:00",
            )
            video_dir = output_root / "BV1Ab411C7De"
            original_first_part = (video_dir / "part-001.md").read_text(
                encoding="utf-8"
            )

            extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                output_root=output_root,
                fetch_info=lambda _url: updated_part,
                extracted_at="2026-08-15T18:30:00+08:00",
            )

            self.assertEqual(
                (video_dir / "part-001.md").read_text(encoding="utf-8"),
                original_first_part,
            )
            self.assertIn(
                "[00:00:03] 新第二部分",
                (video_dir / "part-002.md").read_text(encoding="utf-8"),
            )
            manifest = json.loads(
                (video_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["coverage_complete"])
            self.assertEqual(
                [part["part_number"] for part in manifest["parts"]],
                [1, 2],
            )
            self.assertEqual(
                manifest["last_request"],
                {"mode": "part", "part_number": 2},
            )
            index = (video_dir / "index.md").read_text(encoding="utf-8")
            self.assertIn("[第一部分](part-001.md)", index)
            self.assertIn("[第二部分（更新）](part-002.md)", index)

    def test_selected_part_migrates_legacy_markdown_without_deleting_it(self):
        first_part = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "第一部分",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=1",
            "subtitles": {
                "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\n旧格式字幕\n"}]
            },
        }
        second_part = {
            "_type": "video",
            "id": "BV1Ab411C7De_p2",
            "title": "第二部分",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
            "subtitles": {
                "zh-Hans": [{"ext": "srt", "data": "1\n00:00:03,000 --> 00:00:04,000\n新增字幕\n"}]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                output_root=output_root,
                fetch_info=lambda _url: first_part,
                extracted_at="2026-08-14T18:30:00+08:00",
            )
            video_dir = output_root / "BV1Ab411C7De"
            (video_dir / "manifest.json").unlink()

            extract_to_markdown(
                "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                output_root=output_root,
                fetch_info=lambda _url: second_part,
                extracted_at="2026-08-15T18:30:00+08:00",
            )

            self.assertTrue((video_dir / "part-001.md").is_file())
            self.assertTrue((video_dir / "part-002.md").is_file())
            manifest = json.loads(
                (video_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["coverage_complete"])
            self.assertEqual(
                [part["part_number"] for part in manifest["parts"]],
                [1, 2],
            )

    def test_large_anthology_requires_an_explicit_choice(self):
        entries = []
        for part in range(1, 22):
            entries.append(
                {
                    "id": f"BV1Ab411C7De_p{part}",
                    "title": f"第{part}部分",
                    "webpage_url": f"https://www.bilibili.com/video/BV1Ab411C7De?p={part}",
                    "subtitles": {},
                }
            )
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "大型合集",
            "entries": entries,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(PartSelectionRequiredError):
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

    def test_rejects_a_nonexistent_page_on_a_single_part_video(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Single-part video",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De",
            "subtitles": {
                "zh-Hans": [{"ext": "srt", "data": "1\n00:00:01,000 --> 00:00:02,000\ncaption\n"}]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(PartSelectionRequiredError, "Part 2"):
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                    output_root=Path(temp_dir),
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

    def test_keeps_previous_output_when_a_new_run_fails_midway(self):
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "示例多P视频",
            "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De",
            "entries": [
                {
                    "id": "BV1Ab411C7De_p1",
                    "title": "第一部分",
                    "webpage_url": (
                        "https://www.bilibili.com/video/BV1Ab411C7De?p=1"
                    ),
                    "subtitles": {
                        "zh-Hans": [
                            {
                                "ext": "srt",
                                "data": "1\n00:00:01,000 --> 00:00:03,000\n可用字幕\n",
                            }
                        ]
                    },
                },
                {
                    "id": "BV1Ab411C7De_p2",
                    "title": "损坏部分",
                    "webpage_url": (
                        "https://www.bilibili.com/video/BV1Ab411C7De?p=2"
                    ),
                    "subtitles": {
                        "zh-Hans": [
                            {"ext": "srt", "data": "1\ninvalid --> invalid\n损坏字幕\n"}
                        ]
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            video_dir = output_root / "BV1Ab411C7De"
            video_dir.mkdir()
            (video_dir / "index.md").write_text("previous index\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=output_root,
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

            self.assertEqual(
                (video_dir / "index.md").read_text(encoding="utf-8"),
                "previous index\n",
            )
            self.assertFalse((video_dir / "part-001.md").exists())

    def test_fails_without_replacing_previous_output_when_no_part_has_subtitles(self):
        info = {
            "id": "BV1Ab411C7De",
            "title": "无字幕视频",
            "subtitles": {
                "danmaku": [{"ext": "xml", "data": "<i>弹幕</i>"}]
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            video_dir = output_root / "BV1Ab411C7De"
            video_dir.mkdir()
            (video_dir / "index.md").write_text("previous index\n", encoding="utf-8")

            with self.assertRaises(NoSubtitlesError):
                extract_to_markdown(
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    output_root=output_root,
                    fetch_info=lambda _url: info,
                    extracted_at="2026-08-14T18:30:00+08:00",
                )

            self.assertEqual(
                (video_dir / "index.md").read_text(encoding="utf-8"),
                "previous index\n",
            )


if __name__ == "__main__":
    unittest.main()
