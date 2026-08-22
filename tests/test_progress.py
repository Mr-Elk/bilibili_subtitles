import unittest

from bilibili_subtitles.progress import ProgressEvent, emit_progress, format_progress


class ProgressTests(unittest.TestCase):
    def test_progress_format_exposes_stage_position_without_source_content(self):
        rendered = format_progress(
            ProgressEvent(
                phase="caption",
                message="Checking public caption tracks",
                current=3,
                total=33,
                part_number=7,
            ),
            elapsed_seconds=1.234,
        )

        self.assertEqual(
            rendered,
            "[progress 1.2s] [3/33] P7 Checking public caption tracks",
        )

    def test_emit_progress_is_optional_and_structured(self):
        events = []
        emit_progress(None, "metadata", "ignored")
        emit_progress(
            events.append,
            "publish",
            "Subtitle output published",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].phase, "publish")
        self.assertIsNone(events[0].part_number)


if __name__ == "__main__":
    unittest.main()
