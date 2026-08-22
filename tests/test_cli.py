import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bilibili_subtitles import cli
except ImportError:
    cli = None

main = getattr(cli, "main", None)


class CliTests(unittest.TestCase):
    def test_local_search_does_not_initialize_the_network_fetcher(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def unexpected_fetch(_url):
            raise AssertionError("Local transcript reads must not access the network")

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "part-001.md"
            transcript.write_text(
                "# Local transcript\n\n## Transcript\n\n"
                "[00:00:01] before\n"
                "[00:00:02] token-saving result\n",
                encoding="utf-8",
            )
            exit_code = main(
                [
                    "--action",
                    "search",
                    "--target",
                    str(transcript),
                    "--query",
                    "TOKEN",
                    "--context",
                    "0",
                ],
                info_fetcher=unexpected_fetch,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout.getvalue(),
            "[part-001.md 00:00:02] token-saving result\n",
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_local_read_reports_an_invalid_target_without_a_traceback(self):
        stderr = io.StringIO()

        exit_code = main(
            ["--action", "inventory", "--target", "missing-transcript"],
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("Path does not exist", stderr.getvalue())

    def test_local_json_output_is_parseable_without_network_access(self):
        stdout = io.StringIO()

        def unexpected_fetch(_url):
            raise AssertionError("Local JSON reads must not access the network")

        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "part-001.md"
            transcript.write_text(
                "# JSON\n\n## Transcript\n\n[00:00:01] machine result\n",
                encoding="utf-8",
            )
            exit_code = main(
                [
                    "--action",
                    "search",
                    "--target",
                    str(transcript),
                    "--query",
                    "machine",
                    "--context",
                    "0",
                    "--format",
                    "json",
                ],
                info_fetcher=unexpected_fetch,
                stdout=stdout,
                stderr=io.StringIO(),
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["action"], "search")
        self.assertEqual(payload["items"][0]["text"], "machine result")

    def test_extract_rejects_json_format_before_fetching(self):
        stderr = io.StringIO()

        def unexpected_fetch(_url):
            raise AssertionError("Invalid extract format must fail before fetching")

        exit_code = main(
            [
                "https://www.bilibili.com/video/BV1Ab411C7De",
                "--format",
                "json",
            ],
            info_fetcher=unexpected_fetch,
            stdout=io.StringIO(),
            stderr=stderr,
        )

        self.assertEqual(exit_code, 2)
        self.assertIn("only supported for local read actions", stderr.getvalue())

    def test_anonymous_access_is_the_default(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\ncaption\n",
                    }
                ]
            },
        }
        calls = []

        def fake_fetch(url, *, use_browser_cookies, playlist_end):
            calls.append((url, use_browser_cookies, playlist_end))
            return info

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(cli, "fetch_info", side_effect=fake_fetch):
                exit_code = main(
                    [
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        "--output-root",
                        temp_dir,
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [("https://www.bilibili.com/video/BV1Ab411C7De", False, 21)],
        )

    def test_browser_cookie_access_requires_explicit_opt_in(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\ncaption\n",
                    }
                ]
            },
        }
        calls = []

        def fake_fetch(url, *, use_browser_cookies, playlist_end):
            calls.append((url, use_browser_cookies, playlist_end))
            return info

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(cli, "fetch_info", side_effect=fake_fetch):
                exit_code = main(
                    [
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        "--output-root",
                        temp_dir,
                        "--use-browser-cookies",
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            calls,
            [("https://www.bilibili.com/video/BV1Ab411C7De", True, 21)],
        )

    def test_configurable_part_limit_controls_probe_and_workflow_guard(self):
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "title": "Six-part anthology",
            "entries": [
                {
                    "id": f"BV1Ab411C7De_p{part}",
                    "title": f"Part {part}",
                    "webpage_url": f"https://www.bilibili.com/video/BV1Ab411C7De?p={part}",
                    "subtitles": {},
                }
                for part in range(1, 7)
            ],
        }
        calls = []

        def fake_fetch(url, *, use_browser_cookies, playlist_end):
            calls.append((url, use_browser_cookies, playlist_end))
            return info

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(cli, "fetch_info", side_effect=fake_fetch):
                exit_code = main(
                    [
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        "--output-root",
                        temp_dir,
                        "--max-parts",
                        "5",
                    ],
                    stdout=io.StringIO(),
                    stderr=stderr,
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            calls,
            [("https://www.bilibili.com/video/BV1Ab411C7De", False, 6)],
        )
        self.assertIn("more than 5 parts", stderr.getvalue())

    def test_package_module_exposes_cli_help(self):
        completed = subprocess.run(
            [sys.executable, "-m", "bilibili_subtitles", "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Extract existing Bilibili captions", completed.stdout)

    def test_extracts_video_and_reports_output_directory(self):
        self.assertIsNotNone(main, "main must exist")
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Captioned video",
            "uploader": "Example uploader",
            "formats": [],
            "subtitles": {
                "ai-zh": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\n字幕\n",
                    }
                ]
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            exit_code = main(
                [
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    "--output-root",
                    temp_dir,
                ],
                info_fetcher=lambda _url: info,
                clock=lambda: datetime(2026, 8, 14, 10, 30, tzinfo=timezone.utc),
                stdout=stdout,
                stderr=stderr,
            )
            expected_output = Path(temp_dir) / "BV1Ab411C7De"

            self.assertEqual(exit_code, 0)
            self.assertTrue((expected_output / "index.md").is_file())
            self.assertTrue((expected_output / "part-001.md").is_file())
            self.assertIn("Extracted 1 part(s)", stdout.getvalue())
            self.assertIn(str(expected_output), stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_rejects_unsupported_url_before_calling_yt_dlp(self):
        fetch_called = False
        stdout = io.StringIO()
        stderr = io.StringIO()

        def unexpected_fetch(_url):
            nonlocal fetch_called
            fetch_called = True
            raise AssertionError("yt-dlp must not receive an unsupported URL")

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                exit_code = main(
                    [
                        "https://b23.tv/example",
                        "--output-root",
                        temp_dir,
                    ],
                    info_fetcher=unexpected_fetch,
                    stdout=stdout,
                    stderr=stderr,
                )
            except Exception as error:
                self.fail(f"CLI must report invalid input without a traceback: {error}")

        self.assertEqual(exit_code, 2)
        self.assertFalse(fetch_called)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Only direct HTTPS", stderr.getvalue())

    def test_reports_when_video_has_no_existing_captions(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "title": "Video without captions",
            "formats": [],
            "subtitles": {
                "danmaku": [
                    {
                        "ext": "xml",
                        "url": "https://comment.bilibili.com/123.xml",
                    }
                ]
            },
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                exit_code = main(
                    [
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        "--output-root",
                        temp_dir,
                    ],
                    info_fetcher=lambda _url: info,
                    stdout=stdout,
                    stderr=stderr,
                )
            except Exception as error:
                self.fail(f"CLI must report missing captions without a traceback: {error}")

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("No existing captions were found", stderr.getvalue())

    def test_reports_extraction_failure_without_a_traceback(self):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def failing_fetch(_url):
            raise RuntimeError("network unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                exit_code = main(
                    [
                        "https://www.bilibili.com/video/BV1Ab411C7De",
                        "--output-root",
                        temp_dir,
                    ],
                    info_fetcher=failing_fetch,
                    stdout=stdout,
                    stderr=stderr,
                )
            except Exception as error:
                self.fail(f"CLI must report extraction failures cleanly: {error}")

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "Error: network unavailable\n")


if __name__ == "__main__":
    unittest.main()
