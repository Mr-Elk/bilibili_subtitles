import json
import tempfile
import unittest
from pathlib import Path

from bilibili_subtitles.output_manifest import (
    ManifestValidationError,
    load_or_migrate_manifest,
    read_manifest,
    write_manifest,
)


BVID = "BV1Ab411C7De"
SOURCE_URL = f"https://www.bilibili.com/video/{BVID}"


def captioned_part(number: int) -> dict:
    return {
        "part_number": number,
        "title": f"Part {number}",
        "status": "captioned",
        "source_url": f"{SOURCE_URL}?p={number}",
        "language": "ai-zh",
        "file": f"part-{number:03d}.md",
        "extraction_method": "existing_bilibili_captions",
    }


def no_subtitles_part(number: int) -> dict:
    return {
        "part_number": number,
        "title": f"Part {number}",
        "status": "no_subtitles",
        "source_url": f"{SOURCE_URL}?p={number}",
        "language": None,
        "file": None,
        "extraction_method": None,
    }


def valid_manifest(parts: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "bvid": BVID,
        "title": "Governed course",
        "source_url": SOURCE_URL,
        "updated_at": "2026-08-22T12:00:00+08:00",
        "coverage_complete": True,
        "last_request": {"mode": "all"},
        "parts": parts if parts is not None else [],
    }


class OutputManifestTests(unittest.TestCase):
    def test_valid_manifest_is_loaded_with_sorted_parts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            for number in (1, 2):
                (output_dir / f"part-{number:03d}.md").write_text(
                    "caption\n", encoding="utf-8"
                )
            manifest = valid_manifest([captioned_part(2), captioned_part(1)])
            (output_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            loaded = read_manifest(output_dir, expected_bvid=BVID)

            self.assertEqual(
                [part["part_number"] for part in loaded["parts"]], [1, 2]
            )

    def test_learning_notes_are_user_owned_and_not_manifest_orphans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "part-001.md").write_text("caption\n", encoding="utf-8")
            note = output_dir / "part-001-learning-note-draft.md"
            note.write_text("private note\n", encoding="utf-8")
            (output_dir / "manifest.json").write_text(
                json.dumps(valid_manifest([captioned_part(1)])), encoding="utf-8"
            )

            loaded = read_manifest(output_dir, expected_bvid=BVID)

            self.assertEqual(loaded["parts"][0]["file"], "part-001.md")
            self.assertEqual(note.read_text(encoding="utf-8"), "private note\n")

    def test_existing_corrupt_manifest_fails_instead_of_migrating_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "manifest.json").write_text("{broken", encoding="utf-8")
            (output_dir / "part-001.md").write_text(
                "# Legacy\n\n- BV: `BV1Ab411C7De`\n"
                f"- Source: {SOURCE_URL}?p=1\n"
                "- Subtitle language: `ai-zh`\n\n## Transcript\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ManifestValidationError, "valid UTF-8"):
                load_or_migrate_manifest(
                    output_dir,
                    bvid=BVID,
                    fallback_title="Fallback",
                    source_url=SOURCE_URL,
                    updated_at="2026-08-22T12:00:00+08:00",
                )

    def test_coverage_complete_requires_a_json_boolean(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            manifest = valid_manifest()
            manifest["coverage_complete"] = "false"
            (output_dir / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            with self.assertRaisesRegex(ManifestValidationError, "must be a boolean"):
                read_manifest(output_dir)

    def test_manifest_and_transcript_files_must_match(self):
        cases = (
            ("missing", [captioned_part(1)], ()),
            ("orphan", [], ("part-001.md",)),
        )
        for name, parts, files in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)
                for filename in files:
                    (output_dir / filename).write_text("caption\n", encoding="utf-8")
                (output_dir / "manifest.json").write_text(
                    json.dumps(valid_manifest(parts)), encoding="utf-8"
                )

                with self.assertRaises(ManifestValidationError):
                    read_manifest(output_dir)

    def test_write_manifest_validates_top_level_and_no_subtitles_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            manifest = valid_manifest([no_subtitles_part(1)])
            write_manifest(output_dir, manifest)
            self.assertEqual(read_manifest(output_dir)["parts"][0]["status"], "no_subtitles")

            manifest["parts"][0]["extraction_method"] = "unexpected"
            with self.assertRaisesRegex(ManifestValidationError, "part entry"):
                write_manifest(output_dir, manifest)


if __name__ == "__main__":
    unittest.main()
