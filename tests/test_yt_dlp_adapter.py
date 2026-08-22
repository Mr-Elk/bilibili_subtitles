import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bilibili_subtitles import yt_dlp_adapter
except ImportError:
    yt_dlp_adapter = None

fetch_info = getattr(yt_dlp_adapter, "fetch_info", None)
bilibili_json_to_srt = getattr(yt_dlp_adapter, "bilibili_json_to_srt", None)


def single_part_public_fetcher(tracks, caption_documents=None):
    responses = {
        "https://api.bilibili.com/x/web-interface/view?bvid=BV1Ab411C7De": {
            "code": 0,
            "data": {
                "aid": 123,
                "pages": [{"cid": 11, "page": 1}],
            },
        },
        "https://api.bilibili.com/x/v2/dm/view?type=1&oid=11&pid=123": {
            "code": 0,
            "data": {"subtitle": {"subtitles": tracks}},
        },
    }
    responses.update(caption_documents or {})
    return responses.__getitem__


class BilibiliJsonToSrtTests(unittest.TestCase):
    def test_converts_public_subtitle_cues_to_srt(self):
        self.assertIsNotNone(
            bilibili_json_to_srt,
            "bilibili_json_to_srt must exist",
        )
        document = {
            "body": [
                {"from": 1.25, "to": 3.0, "content": "第一行\n第二行"},
                {"from": 62.501, "to": 64.005, "content": "下一段"},
            ]
        }

        srt = bilibili_json_to_srt(document)

        self.assertEqual(
            srt,
            "1\n"
            "00:00:01,250 --> 00:00:03,000\n"
            "第一行\n第二行\n\n"
            "2\n"
            "00:01:02,501 --> 00:01:04,005\n"
            "下一段\n",
        )

    def test_rejects_an_unreasonably_large_cue_collection(self):
        document = {
            "body": [
                {"from": 1, "to": 2, "content": "caption"},
                {"from": 3, "to": 4, "content": "caption"},
            ]
        }

        with patch.object(yt_dlp_adapter, "MAX_PUBLIC_CAPTION_CUES", 1):
            with self.assertRaisesRegex(ValueError, "too many cues"):
                bilibili_json_to_srt(document)


class PublicRequestValidationTests(unittest.TestCase):
    def test_public_json_download_has_a_hard_body_size_limit(self):
        class FakeResponse(io.BytesIO):
            headers = {}

        class FakeOpener:
            def open(self, _request, *, timeout):
                self.timeout = timeout
                return FakeResponse(b"123456789")

        opener = FakeOpener()
        with patch.object(yt_dlp_adapter, "MAX_PUBLIC_JSON_BYTES", 8):
            with patch.object(yt_dlp_adapter, "build_opener", return_value=opener):
                with self.assertRaisesRegex(RuntimeError, "size limit"):
                    yt_dlp_adapter._download_public_json(
                        "https://subtitle.bilibili.com/caption.json"
                    )

        self.assertEqual(opener.timeout, 30)

    def test_rejects_caption_url_with_non_https_port(self):
        secured = yt_dlp_adapter._secure_subtitle_url(
            "http://aisubtitle.hdslb.com:8080/caption.json"
        )

        self.assertIsNone(secured)

    def test_redirect_target_must_stay_on_the_original_host_boundary(self):
        validate = getattr(yt_dlp_adapter, "_validate_https_url", None)
        self.assertIsNotNone(validate, "redirect URL validation must exist")

        with self.assertRaisesRegex(RuntimeError, "not allowed"):
            validate(
                "https://example.com/caption.json",
                {"aisubtitle.hdslb.com", "subtitle.bilibili.com"},
            )


class BoundedBilibiliExtractorTests(unittest.TestCase):
    class FakeBiliBiliIE:
        _HEADERS = {"Referer": "https://www.bilibili.com/"}

        def __init__(self, subtitle_url):
            self.subtitle_url = subtitle_url

        def _download_json(self, url, _video_id, **_kwargs):
            if url != "https://api.bilibili.com/x/player/wbi/v2":
                raise AssertionError("Only subtitle discovery may use yt-dlp requests")
            return {
                "code": 0,
                "data": {
                    "need_login_subtitle": False,
                    "subtitle": {
                        "subtitles": [
                            {"lan": "ai-zh", "subtitle_url": self.subtitle_url}
                        ]
                    },
                },
            }

        def report_warning(self, _message, **_kwargs):
            pass

        def _login_hint(self):
            return "login hint"

    def test_routes_extractor_caption_body_through_bounded_fetcher(self):
        factory = getattr(
            yt_dlp_adapter,
            "_bounded_bilibili_extractor_class",
            None,
        )
        self.assertIsNotNone(factory, "bounded Bilibili extractor must exist")
        fetched_urls = []

        def fetch_json(url):
            fetched_urls.append(url)
            return {"body": [{"from": 1, "to": 2, "content": "安全字幕"}]}

        extractor_type = factory(self.FakeBiliBiliIE, fetch_json)
        extractor = extractor_type("http://aisubtitle.hdslb.com/caption")

        subtitles = extractor._get_subtitles("BV1Ab411C7De", 11)

        self.assertEqual(fetched_urls, ["https://aisubtitle.hdslb.com/caption"])
        self.assertIn("安全字幕", subtitles["ai-zh"][0]["data"])

    def test_installs_bounded_extractor_under_standard_bilibili_key(self):
        install = getattr(
            yt_dlp_adapter,
            "_install_bounded_bilibili_extractor",
            None,
        )
        self.assertIsNotNone(install, "bounded extractor installer must exist")
        added = []

        class FakeYoutubeDL:
            def add_info_extractor(self, extractor):
                added.append(extractor)

        install(FakeYoutubeDL())

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].ie_key(), "BiliBili")
        self.assertEqual(
            added[0]._get_subtitles.__func__.__module__,
            "bilibili_subtitles.yt_dlp_adapter",
        )

    def test_rejects_extractor_caption_body_outside_allowlist(self):
        factory = getattr(
            yt_dlp_adapter,
            "_bounded_bilibili_extractor_class",
            None,
        )
        self.assertIsNotNone(factory, "bounded Bilibili extractor must exist")

        def unexpected_fetch(_url):
            raise AssertionError("Rejected subtitle URL must not be fetched")

        extractor_type = factory(self.FakeBiliBiliIE, unexpected_fetch)
        extractor = extractor_type("https://example.com/caption")

        with self.assertRaisesRegex(RuntimeError, "host boundary"):
            extractor._get_subtitles("BV1Ab411C7De", 11)


class PublicCaptionFallbackTests(unittest.TestCase):
    def test_does_not_replace_usable_english_with_public_english(self):
        existing_srt = "1\n00:00:01,000 --> 00:00:02,000\nExisting English\n"
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "subtitles": {
                "en": [{"ext": "srt", "data": existing_srt}],
            },
        }
        fetch_json = single_part_public_fetcher(
            [
                {
                    "lan": "en",
                    "subtitle_url": "https://aisubtitle.hdslb.com/duplicate-english",
                }
            ]
        )

        try:
            yt_dlp_adapter._add_public_subtitles(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                info,
                fetch_json,
            )
        except KeyError as error:
            self.fail(f"Existing usable English must prevent duplicate fetch: {error}")

        self.assertEqual(info["subtitles"]["en"][0]["data"], existing_srt)

    def test_replaces_blank_chinese_metadata_and_prefers_public_chinese_over_english(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "subtitles": {
                "zh-Hans": [{"ext": "srt", "data": "   "}],
                "en": [
                    {
                        "ext": "srt",
                        "data": "1\n00:00:01,000 --> 00:00:02,000\nEnglish\n",
                    }
                ],
            },
        }
        fetch_json = single_part_public_fetcher(
            [
                {
                    "lan": "ai-zh",
                    "subtitle_url": "https://aisubtitle.hdslb.com/chinese",
                }
            ],
            {
                "https://aisubtitle.hdslb.com/chinese": {
                    "body": [{"from": 1, "to": 2, "content": "中文"}],
                }
            },
        )

        yt_dlp_adapter._add_public_subtitles(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            info,
            fetch_json,
        )

        self.assertIn("中文", info["subtitles"]["ai-zh"][0]["data"])

    def test_tries_next_public_track_when_preferred_track_url_is_rejected(self):
        info = {
            "_type": "video",
            "id": "BV1Ab411C7De",
            "subtitles": {},
        }
        fetch_json = single_part_public_fetcher(
            [
                {
                    "lan": "zh-Hans",
                    "subtitle_url": "https://example.com/rejected",
                },
                {
                    "lan": "ai-zh",
                    "subtitle_url": "https://aisubtitle.hdslb.com/usable",
                },
            ],
            {
                "https://aisubtitle.hdslb.com/usable": {
                    "body": [{"from": 1, "to": 2, "content": "可用字幕"}],
                }
            },
        )

        yt_dlp_adapter._add_public_subtitles(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            info,
            fetch_json,
        )

        self.assertIn("可用字幕", info["subtitles"]["ai-zh"][0]["data"])

    def test_injects_public_caption_into_fragment_retained_by_legacy_collapse(self):
        fragment = {
            "_type": "video",
            "id": "fragment-1",
            "subtitles": {},
        }
        info = {
            "_type": "playlist",
            "id": "BV1Ab411C7De",
            "entries": [
                {
                    "_type": "multi_video",
                    "id": "BV1Ab411C7De_p1",
                    "entries": [fragment],
                }
            ],
        }
        fetch_json = single_part_public_fetcher(
            [
                {
                    "lan": "ai-zh",
                    "subtitle_url": "https://subtitle.bilibili.com/legacy",
                }
            ],
            {
                "https://subtitle.bilibili.com/legacy": {
                    "body": [{"from": 1, "to": 2, "content": "旧式分段字幕"}],
                }
            },
        )

        yt_dlp_adapter._add_public_subtitles(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            info,
            fetch_json,
        )

        self.assertIn("旧式分段字幕", fragment["subtitles"]["ai-zh"][0]["data"])


class FetchInfoTests(unittest.TestCase):
    def test_page_query_limits_yt_dlp_to_the_selected_part(self):
        received_options = []

        class FakeYoutubeDL:
            def __init__(self, options):
                received_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De_p4",
                    "title": "第四部分",
                    "subtitles": {
                        "ai-zh": [
                            {
                                "ext": "srt",
                                "data": "1\n00:00:01,000 --> 00:00:02,000\ncaption\n",
                            }
                        ]
                    },
                }

        fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De?p=4",
            ydl_factory=FakeYoutubeDL,
            use_browser_cookies=False,
        )

        self.assertEqual(received_options[0]["playlist_items"], "4")

    def test_can_skip_browser_cookie_access(self):
        received_options = []

        class FakeYoutubeDL:
            def __init__(self, options):
                received_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Public captioned video",
                    "subtitles": {
                        "ai-zh": [
                            {
                                "ext": "srt",
                                "data": "1\n00:00:01,000 --> 00:00:02,000\ncaption\n",
                            }
                        ]
                    },
                }

        fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
            use_browser_cookies=False,
        )

        self.assertEqual(len(received_options), 1)
        self.assertNotIn("cookiesfrombrowser", received_options[0])
        self.assertEqual(received_options[0]["playlistend"], 21)

    def test_all_parts_can_disable_the_default_playlist_probe_limit(self):
        received_options = []

        class FakeYoutubeDL:
            def __init__(self, options):
                received_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Public captioned video",
                    "subtitles": {
                        "ai-zh": [{"ext": "srt", "data": "caption"}]
                    },
                }

        fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
            playlist_end=None,
        )

        self.assertNotIn("playlistend", received_options[0])

    def test_skips_public_api_when_yt_dlp_already_has_captions(self):
        expected_srt = "1\n00:00:01,000 --> 00:00:02,000\ncaption\n"

        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Captioned video",
                    "subtitles": {
                        "zh-Hans": [{"ext": "srt", "data": expected_srt}]
                    },
                }

        def unexpected_public_fetch(_url):
            raise AssertionError("Public fallback must not run")

        try:
            info = fetch_info(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                ydl_factory=FakeYoutubeDL,
                public_json_fetcher=unexpected_public_fetch,
            )
        except AssertionError as error:
            self.fail(str(error))

        self.assertEqual(info["subtitles"]["zh-Hans"][0]["data"], expected_srt)

    def test_fills_every_playlist_part_from_public_smart_subtitles(self):
        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "playlist",
                    "id": "BV1Ab411C7De",
                    "title": "Two-part video",
                    "entries": [
                        {
                            "id": "BV1Ab411C7De_p1",
                            "title": "Part one",
                            "subtitles": {
                                "danmaku": [
                                    {
                                        "ext": "xml",
                                        "url": "https://comment.bilibili.com/11.xml",
                                    }
                                ]
                            },
                        },
                        {
                            "id": "BV1Ab411C7De_p2",
                            "title": "Part two",
                            "subtitles": {},
                        },
                    ],
                }

        responses = {
            "https://api.bilibili.com/x/web-interface/view?bvid=BV1Ab411C7De": {
                "code": 0,
                "data": {
                    "aid": 123,
                    "pages": [
                        {"cid": 11, "page": 1},
                        {"cid": 22, "page": 2},
                    ],
                },
            },
            "https://api.bilibili.com/x/v2/dm/view?type=1&oid=11&pid=123": {
                "code": 0,
                "data": {
                    "subtitle": {
                        "subtitles": [
                            {
                                "lan": "ai-en",
                                "subtitle_url": "http://aisubtitle.hdslb.com/part-1-en",
                            },
                            {
                                "lan": "ai-zh",
                                "subtitle_url": "http://aisubtitle.hdslb.com/part-1-zh",
                            },
                        ]
                    }
                },
            },
            "https://api.bilibili.com/x/v2/dm/view?type=1&oid=22&pid=123": {
                "code": 0,
                "data": {
                    "subtitle": {
                        "subtitles": [
                            {
                                "lan": "zh-Hans",
                                "subtitle_url": "https://subtitle.bilibili.com/part-2",
                            }
                        ]
                    }
                },
            },
            "https://aisubtitle.hdslb.com/part-1-zh": {
                "body": [{"from": 1, "to": 2, "content": "第一部分"}],
            },
            "https://subtitle.bilibili.com/part-2": {
                "body": [{"from": 3, "to": 4, "content": "第二部分"}],
            },
        }

        try:
            info = fetch_info(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                ydl_factory=FakeYoutubeDL,
                public_json_fetcher=responses.__getitem__,
            )
        except TypeError as error:
            self.fail(f"fetch_info must support public subtitle fallback: {error}")

        first = info["entries"][0]["subtitles"]["ai-zh"][0]
        second = info["entries"][1]["subtitles"]["zh-Hans"][0]
        self.assertEqual(
            first,
            {
                "ext": "srt",
                "data": "1\n00:00:01,000 --> 00:00:02,000\n第一部分\n",
            },
        )
        self.assertEqual(
            second,
            {
                "ext": "srt",
                "data": "1\n00:00:03,000 --> 00:00:04,000\n第二部分\n",
            },
        )

    def test_raises_when_public_caption_lookup_api_returns_an_error(self):
        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Caption lookup failure",
                    "subtitles": {},
                }

        responses = {
            "https://api.bilibili.com/x/web-interface/view?bvid=BV1Ab411C7De": {
                "code": 0,
                "data": {
                    "aid": 123,
                    "pages": [{"cid": 11, "page": 1}],
                },
            },
            "https://api.bilibili.com/x/v2/dm/view?type=1&oid=11&pid=123": {
                "code": -412,
                "message": "request blocked",
            },
        }

        with self.assertRaisesRegex(RuntimeError, "-412"):
            fetch_info(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                ydl_factory=FakeYoutubeDL,
                public_json_fetcher=responses.__getitem__,
            )

    def test_preserves_inline_srt_returned_by_yt_dlp(self):
        expected_srt = "1\n00:00:01,000 --> 00:00:02,000\ncaption\n"

        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Captioned video",
                    "formats": [],
                    "subtitles": {
                        "ai-zh": [
                            {
                                "ext": "srt",
                                "data": expected_srt,
                            }
                        ]
                    },
                }

        info = fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
        )

        variants = info["subtitles"].get("ai-zh") or []
        actual_srt = variants[0].get("data") if variants else None
        self.assertEqual(actual_srt, expected_srt)

    def test_preserves_lazy_playlist_entries_for_the_workflow(self):
        class FakeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                entries = (
                    {
                        "_type": "video",
                        "id": f"part-{part_number}",
                        "title": f"Part {part_number}",
                        "formats": [],
                        "subtitles": {},
                    }
                    for part_number in (1, 2)
                )
                return {
                    "_type": "playlist",
                    "id": "BV1Ab411C7De",
                    "title": "Lazy two-part video",
                    "entries": entries,
                }

        info = fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
        )

        self.assertEqual(
            [entry["title"] for entry in info["entries"]],
            ["Part 1", "Part 2"],
        )

    def test_requests_regular_subtitle_metadata_from_yt_dlp(self):
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                subtitles = {}
                if self.options.get("writesubtitles"):
                    subtitles = {
                        "zh-Hans": [
                            {
                                "ext": "srt",
                                "data": (
                                    "1\n00:00:01,000 --> 00:00:02,000\ncaption\n"
                                ),
                            }
                        ]
                    }
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Captioned video",
                    "formats": [],
                    "subtitles": subtitles,
                }

        info = fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
        )

        self.assertEqual(
            info["subtitles"].get("zh-Hans", [{}])[0].get("data"),
            "1\n00:00:01,000 --> 00:00:02,000\ncaption\n",
        )

    def test_explicit_opt_in_uses_chrome_for_the_first_attempt(self):
        created_options = []

        class FakeYoutubeDL:
            def __init__(self, options):
                created_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Video without captions",
                    "formats": [],
                    "subtitles": {},
                }

        fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
            use_browser_cookies=True,
        )

        self.assertEqual(created_options[0].get("cookiesfrombrowser"), ("chrome",))

    def test_warns_and_retries_anonymously_when_chrome_cookies_cannot_be_read(self):
        class FakeCookieLoadError(Exception):
            pass

        attempts = []
        warnings = []

        class AnonymousYoutubeDL:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Anonymous result",
                    "formats": [],
                    "subtitles": {},
                }

        def fake_ydl_factory(options):
            attempts.append(options)
            if options.get("cookiesfrombrowser"):
                raise FakeCookieLoadError("Chrome cookie database is locked")
            return AnonymousYoutubeDL()

        try:
            info = fetch_info(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                ydl_factory=fake_ydl_factory,
                cookie_error_type=FakeCookieLoadError,
                warn=warnings.append,
                use_browser_cookies=True,
            )
        except TypeError as error:
            self.fail(f"fetch_info must support cookie fallback inputs: {error}")

        self.assertEqual(info["title"], "Anonymous result")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0].get("cookiesfrombrowser"), ("chrome",))
        self.assertNotIn("cookiesfrombrowser", attempts[1])
        self.assertEqual(
            warnings,
            ["Chrome cookies could not be read; retrying without browser login."],
        )

    def test_retries_when_yt_dlp_wraps_the_cookie_error(self):
        class FakeCookieLoadError(Exception):
            pass

        class FakeDownloadError(Exception):
            pass

        attempts = []
        warnings = []

        class AnonymousYoutubeDL:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                if download:
                    raise AssertionError("Media download must stay disabled")
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Anonymous result",
                    "formats": [],
                    "subtitles": {},
                }

        def fake_ydl_factory(options):
            attempts.append(options)
            if options.get("cookiesfrombrowser"):
                try:
                    raise FakeCookieLoadError("Chrome cookie database is locked")
                except FakeCookieLoadError:
                    raise FakeDownloadError("yt-dlp wrapped the cookie error")
            return AnonymousYoutubeDL()

        try:
            info = fetch_info(
                "https://www.bilibili.com/video/BV1Ab411C7De",
                ydl_factory=fake_ydl_factory,
                cookie_error_type=FakeCookieLoadError,
                warn=warnings.append,
                use_browser_cookies=True,
            )
        except FakeDownloadError as error:
            self.fail(f"Wrapped cookie errors must trigger anonymous retry: {error}")

        self.assertEqual(info["title"], "Anonymous result")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            warnings,
            ["Chrome cookies could not be read; retrying without browser login."],
        )

    def test_disables_media_download_in_yt_dlp_options(self):
        self.assertIsNotNone(fetch_info, "fetch_info must exist")
        created_options = []
        extract_download_arguments = []

        class FakeYoutubeDL:
            def __init__(self, options):
                created_options.append(options)

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                return False

            def extract_info(self, _url, *, download):
                extract_download_arguments.append(download)
                return {
                    "_type": "video",
                    "id": "BV1Ab411C7De",
                    "title": "Captioned video",
                    "uploader": "Example uploader",
                    "webpage_url": "https://www.bilibili.com/video/BV1Ab411C7De",
                    "formats": [],
                    "subtitles": {},
                }

        fetch_info(
            "https://www.bilibili.com/video/BV1Ab411C7De",
            ydl_factory=FakeYoutubeDL,
        )

        self.assertEqual(extract_download_arguments, [False])
        self.assertTrue(created_options[0]["skip_download"])
        self.assertFalse(created_options[0]["extract_flat"])


if __name__ == "__main__":
    unittest.main()
