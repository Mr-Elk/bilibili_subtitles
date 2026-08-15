import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PowerShellScriptTests(unittest.TestCase):
    def test_extract_script_explains_how_to_create_missing_environment(self):
        source_script = PROJECT_ROOT / "extract.ps1"
        self.assertTrue(source_script.is_file(), "extract.ps1 must exist")

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "extract.ps1"
            shutil.copy2(source_script, script)
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Run .\\setup.ps1 first", completed.stderr)

    def test_extract_script_accepts_output_root_before_environment_check(self):
        source_script = PROJECT_ROOT / "extract.ps1"
        self.assertTrue(source_script.is_file(), "extract.ps1 must exist")

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "extract.ps1"
            output_root = Path(temp_dir) / "custom-output"
            shutil.copy2(source_script, script)
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    "--output-root",
                    str(output_root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Run .\\setup.ps1 first", completed.stderr)
        self.assertNotIn("NamedParameterNotFound", completed.stderr)

    def test_extract_script_accepts_no_browser_cookies_switch(self):
        source_script = PROJECT_ROOT / "extract.ps1"
        self.assertTrue(source_script.is_file(), "extract.ps1 must exist")

        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "extract.ps1"
            shutil.copy2(source_script, script)
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "https://www.bilibili.com/video/BV1Ab411C7De",
                    "-NoBrowserCookies",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Run .\\setup.ps1 first", completed.stderr)
        self.assertNotIn("NamedParameterNotFound", completed.stderr)


if __name__ == "__main__":
    unittest.main()
