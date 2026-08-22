import json
import tempfile
import unittest
from pathlib import Path

from bilibili_subtitles.reader import (
    ReaderInputError,
    bounded_text,
    captions_from_file,
    chunk_map,
    inventory,
    read_transcripts,
    search,
    slice_captions,
    timestamp_to_seconds,
    transcript_files,
)


def write_transcript(path: Path, title: str, captions: list[tuple[str, str]]) -> None:
    lines = [f"# {title}", "", "## Transcript", ""]
    lines.extend(f"[{timestamp}] {text}" for timestamp, text in captions)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_status_fixture(output_dir: Path) -> None:
    write_transcript(
        output_dir / "part-001.md",
        "Captioned part",
        [("00:00:01", "governed caption")],
    )
    manifest = {
        "schema_version": 1,
        "bvid": "BV1Ab411C7De",
        "title": "Governed course",
        "source_url": "https://www.bilibili.com/video/BV1Ab411C7De",
        "updated_at": "2026-08-22T12:00:00+08:00",
        "coverage_complete": False,
        "last_request": {"mode": "part", "part_number": 1},
        "parts": [
            {
                "part_number": 1,
                "title": "Captioned part",
                "status": "captioned",
                "source_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=1",
                "language": "ai-zh",
                "file": "part-001.md",
                "extraction_method": "existing_bilibili_captions",
            },
            {
                "part_number": 2,
                "title": "Missing captions",
                "status": "no_subtitles",
                "source_url": "https://www.bilibili.com/video/BV1Ab411C7De?p=2",
                "language": None,
                "file": None,
                "extraction_method": None,
            },
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


class ReaderTests(unittest.TestCase):
    def test_status_reports_manifest_coverage_in_text_and_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            write_status_fixture(output_dir)

            text_status = read_transcripts("status", target=output_dir)
            json_status = json.loads(
                read_transcripts("status", target=output_dir, output_format="json")
            )

        self.assertIn("COVERAGE\tincomplete", text_status)
        self.assertIn("PARTS\t2\tCAPTIONED\t1\tNO_SUBTITLES\t1", text_status)
        self.assertEqual(json_status["action"], "status")
        self.assertFalse(json_status["manifest"]["coverage_complete"])
        self.assertEqual(json_status["manifest"]["no_subtitles_count"], 1)
        self.assertEqual(json_status["items"][1]["status"], "no_subtitles")

    def test_status_rejects_a_corrupt_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "manifest.json").write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(ReaderInputError, "valid UTF-8"):
                read_transcripts("status", target=output_dir)

    def test_inventory_reports_file_range_size_and_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "  A   useful   title  ",
                [("00:00:01", "first"), ("00:01:02", "第二段")],
            )

            lines = inventory([path])

        self.assertEqual(lines[0], "FILE\tCUES\tRANGE\tTEXT_CHARS\tTITLE")
        self.assertEqual(
            lines[1],
            "part-001.md\t2\t00:00:01-00:01:02\t8\tA useful title",
        )

    def test_directory_selection_only_returns_sorted_part_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_transcript(root / "part-010.md", "ten", [("00:00:01", "ten")])
            write_transcript(root / "part-002.md", "two", [("00:00:01", "two")])
            (root / "part-001-learning-note-draft.md").write_text(
                "# private note\n", encoding="utf-8"
            )
            (root / "part-003-other.md").write_text(
                "# unrelated\n", encoding="utf-8"
            )
            (root / "index.md").write_text("index\n", encoding="utf-8")

            files = transcript_files(root)

            self.assertEqual([path.name for path in files], ["part-002.md", "part-010.md"])

    def test_direct_noncanonical_markdown_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            note = Path(temp_dir) / "part-001-learning-note-draft.md"
            note.write_text("# private note\n", encoding="utf-8")

            with self.assertRaisesRegex(ReaderInputError, "canonical part-NNN.md"):
                transcript_files(note)

    def test_chunk_map_splits_before_crossing_the_character_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "chunks",
                [
                    ("00:00:01", "a" * 120),
                    ("00:00:02", "b" * 120),
                    ("00:00:03", "short"),
                ],
            )

            lines = chunk_map([path], chunk_chars=200)

        self.assertEqual(lines[0], "CHUNK\tRANGE\tCUES\tTEXT_CHARS")
        self.assertIn("part-001.md#001\t00:00:01-00:00:01\t1\t121", lines)
        self.assertIn("part-001.md#002\t00:00:02-00:00:03\t2\t127", lines)

    def test_search_returns_bounded_context_and_limits_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "search",
                [
                    ("00:00:01", "before"),
                    ("00:00:02", "Target one"),
                    ("00:00:03", "between"),
                    ("00:00:04", "target two"),
                    ("00:00:05", "after"),
                ],
            )

            lines = search([path], query="TARGET", context=1, max_results=1)

        self.assertEqual(
            lines,
            [
                "[part-001.md 00:00:01] before",
                "[part-001.md 00:00:02] Target one",
                "[part-001.md 00:00:03] between",
            ],
        )

    def test_search_reports_no_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(path, "search", [("00:00:01", "caption")])

            lines = search([path], query="missing", context=1, max_results=8)

        self.assertEqual(lines, ["NO_MATCH"])

    def test_slice_uses_source_names_only_for_multiple_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "part-001.md"
            second = root / "part-002.md"
            write_transcript(
                first,
                "first",
                [("00:00:01", "early"), ("00:00:03", "selected one")],
            )
            write_transcript(second, "second", [("00:00:04", "selected two")])

            single = slice_captions([first], start="00:00:03", end=None)
            multiple = slice_captions(
                [first, second], start="00:00:03", end="00:00:04"
            )

        self.assertEqual(single, ["[00:00:03] selected one"])
        self.assertEqual(
            multiple,
            [
                "[part-001.md 00:00:03] selected one",
                "[part-002.md 00:00:04] selected two",
            ],
        )

    def test_empty_slice_has_a_stable_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(path, "slice", [("00:00:01", "caption")])

            lines = slice_captions(
                [path], start="00:01:00", end="00:02:00"
            )

        self.assertEqual(lines, ["NO_CAPTIONS_IN_RANGE"])

    def test_bounded_output_never_exceeds_the_requested_character_limit(self):
        rendered = bounded_text(["x" * 400], limit=200)

        self.assertLessEqual(len(rendered), 200)
        self.assertTrue(rendered.endswith("raise --max-chars]"))

    def test_dispatch_applies_action_default_output_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(path, "dispatch", [("00:00:01", "find me")])

            rendered = read_transcripts(
                "search",
                target=path,
                query="find",
                context=0,
            )

        self.assertEqual(rendered, "[part-001.md 00:00:01] find me")

    def test_json_search_is_versioned_and_marks_matches_separately_from_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "JSON search",
                [
                    ("00:00:01", "before"),
                    ("00:00:02", "中文优化结果"),
                    ("00:00:03", "after"),
                ],
            )

            rendered = read_transcripts(
                "search",
                target=path,
                query="优化",
                context=1,
                output_format="json",
                max_chars=2_000,
            )
            payload = json.loads(rendered)

        self.assertIn("中文优化结果", rendered)
        self.assertEqual(payload["schema"], "bilibili-subtitles.reader")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["action"], "search")
        self.assertEqual(payload["target"], str(path.resolve()))
        self.assertEqual(payload["match_count"], 1)
        self.assertEqual(payload["item_count"], 3)
        self.assertEqual(payload["returned_count"], 3)
        self.assertFalse(payload["truncated"])
        self.assertEqual(
            [item["is_match"] for item in payload["items"]],
            [False, True, False],
        )

    def test_json_actions_expose_action_specific_parameters_and_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "JSON actions",
                [("00:00:01", "first"), ("00:00:03", "selected")],
            )

            inventory_payload = json.loads(
                read_transcripts("inventory", target=path, output_format="json")
            )
            map_payload = json.loads(
                read_transcripts(
                    "map",
                    target=path,
                    chunk_chars=200,
                    output_format="json",
                )
            )
            slice_payload = json.loads(
                read_transcripts(
                    "slice",
                    target=path,
                    start="00:00:03",
                    end="00:00:03",
                    output_format="json",
                )
            )

        self.assertEqual(inventory_payload["items"][0]["cue_count"], 2)
        self.assertEqual(map_payload["parameters"], {"chunk_chars": 200})
        self.assertEqual(map_payload["items"][0]["chunk_id"], "part-001.md#001")
        self.assertEqual(
            slice_payload["parameters"],
            {"start": "00:00:03", "end": "00:00:03"},
        )
        self.assertEqual(slice_payload["items"][0]["text"], "selected")

    def test_bounded_json_remains_valid_when_items_are_truncated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "part-001.md"
            write_transcript(
                path,
                "bounded JSON",
                [
                    (f"00:00:{index:02d}", f"target {'x' * 120}")
                    for index in range(1, 13)
                ],
            )

            rendered = read_transcripts(
                "search",
                target=path,
                query="target",
                context=0,
                max_results=20,
                max_chars=500,
                output_format="json",
            )
            payload = json.loads(rendered)

        self.assertLessEqual(len(rendered), 500)
        self.assertEqual(payload["item_count"], 12)
        self.assertLess(payload["returned_count"], 12)
        self.assertTrue(payload["truncated"])

    def test_invalid_inputs_fail_before_returning_partial_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            empty = root / "part-001.md"
            empty.write_text("# no captions\n", encoding="utf-8")

            with self.assertRaisesRegex(ReaderInputError, "No timestamped"):
                captions_from_file(empty)
            with self.assertRaisesRegex(ReaderInputError, "earlier"):
                slice_captions([empty], start="00:01:00", end="00:00:59")
            with self.assertRaisesRegex(ReaderInputError, "Path does not exist"):
                transcript_files(root / "missing")
            with self.assertRaisesRegex(ReaderInputError, "Invalid timestamp"):
                timestamp_to_seconds("00:99:00")
            with self.assertRaisesRegex(ReaderInputError, "output format"):
                read_transcripts("inventory", target=empty, output_format="yaml")

    def test_long_hour_timestamps_are_supported(self):
        self.assertEqual(timestamp_to_seconds("100:00:01"), 360_001)


if __name__ == "__main__":
    unittest.main()
