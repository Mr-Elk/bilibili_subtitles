import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PowerShellScriptTests(unittest.TestCase):
    def _governance_fixture(self, root: Path) -> None:
        for name in (
            ".gitignore",
            "GOVERNANCE.md",
            "README.md",
            "READER_JSON_V1.md",
            "requirements.txt",
            "requirements-lock.txt",
            "setup.ps1",
            "install.ps1",
            "bilibili-subtitles.ps1",
            "extract.ps1",
            "verify.ps1",
        ):
            shutil.copy2(PROJECT_ROOT / name, root / name)
        for arguments in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "Governance Test"],
            ["git", "config", "user.email", "governance@example.invalid"],
            ["git", "add", "."],
            ["git", "commit", "-m", "fixture"],
        ):
            completed = subprocess.run(
                arguments,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def _run_static_verifier(self, root: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "verify.ps1"),
                "-StaticOnly",
                *extra,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_static_verifier_distinguishes_static_from_full_and_rejects_dirty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._governance_fixture(root)
            clean = self._run_static_verifier(root, "-RequireClean")
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertIn("[verify] STATIC-ONLY PASS", clean.stdout)
            self.assertNotIn("[verify] PASS", clean.stdout)

            (root / "README.md").write_text("dirty\n", encoding="utf-8")
            dirty = self._run_static_verifier(root, "-RequireClean")

        self.assertEqual(dirty.returncode, 1)
        self.assertIn("Working tree is not clean", dirty.stderr)

    def test_static_verifier_rejects_private_artifacts_and_credentials(self):
        cases = (
            (
                Path("output") / "part-001.md",
                "[00:00:00] private transcript\n",
                "Private or runtime artifacts are tracked",
                True,
            ),
            (
                Path("notes.txt"),
                "Authorization: Bearer abcdefghijklmnop\n",
                "Possible credentials are present in tracked files",
                False,
            ),
        )
        for relative_path, content, message, force in cases:
            with self.subTest(relative_path=str(relative_path)):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self._governance_fixture(root)
                    target = root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    add = ["git", "add"]
                    if force:
                        add.append("-f")
                    add.append(str(relative_path))
                    staged = subprocess.run(
                        add,
                        cwd=root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        check=False,
                    )
                    self.assertEqual(staged.returncode, 0, staged.stderr)
                    completed = self._run_static_verifier(root)

                self.assertEqual(completed.returncode, 1)
                self.assertIn(message, completed.stderr)

    def test_governance_scripts_parse_and_setup_uses_dependency_lock(self):
        for name in (
            "setup.ps1",
            "install.ps1",
            "bilibili-subtitles.ps1",
            "extract.ps1",
            "verify.ps1",
        ):
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$tokens=$null; $errors=$null; "
                        "[void][System.Management.Automation.Language.Parser]::"
                        f"ParseFile('{PROJECT_ROOT / name}', [ref]$tokens, [ref]$errors); "
                        "if($errors.Count -gt 0){$errors | ForEach-Object {$_.Message}; exit 1}"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

        setup_text = (PROJECT_ROOT / "setup.ps1").read_text(encoding="utf-8")
        verify_text = (PROJECT_ROOT / "verify.ps1").read_text(encoding="utf-8")
        self.assertIn('Join-Path $toolRoot "requirements-lock.txt"', setup_text)
        self.assertNotIn('Join-Path $toolRoot "requirements.txt"', setup_text)
        self.assertIn("diff --cached --check", verify_text)
        self.assertIn("Installed dependencies do not match", verify_text)

    def test_governance_contract_keeps_private_outputs_untracked(self):
        governance = (PROJECT_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
        ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        lock = (PROJECT_ROOT / "requirements-lock.txt").read_text(encoding="utf-8")

        for marker in ("output/", ".course-learning-private/", "part-*.md"):
            self.assertIn(marker, governance)
        self.assertIn("AI-G0", governance)
        self.assertIn(".course-learning-private/", ignore)
        self.assertIn("yt-dlp[default]==2026.7.4", lock)

    def test_main_launcher_forwards_local_search_to_python_reader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "part-001.md"
            transcript.write_text(
                "# Local transcript\n\n## Transcript\n\n"
                "[00:00:01] before\n"
                "[00:00:02] forwarded search result\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["BILIBILI_SUBTITLE_PYTHON"] = sys.executable
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PROJECT_ROOT / "bilibili-subtitles.ps1"),
                    "-Action",
                    "Search",
                    "-Target",
                    str(transcript),
                    "-Query",
                    "FORWARDED",
                    "-Context",
                    "0",
                    "-ToolRoot",
                    str(PROJECT_ROOT),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "[part-001.md 00:00:02] forwarded search result",
            completed.stdout,
        )

    def test_main_launcher_has_standalone_help(self):
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROJECT_ROOT / "bilibili-subtitles.ps1"),
                "-ShowHelp",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Extract captions anonymously", completed.stdout)
        self.assertIn("-UseBrowserCookies", completed.stdout)
        self.assertIn("-MaxParts", completed.stdout)
        self.assertIn("-Format Json", completed.stdout)

    def test_main_launcher_emits_utf8_for_chinese_caption(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "part-001.md"
            transcript.write_text(
                "# 中文字幕\n\n## Transcript\n\n"
                "[00:00:01] 中文优化结果\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["BILIBILI_SUBTITLE_PYTHON"] = sys.executable
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PROJECT_ROOT / "bilibili-subtitles.ps1"),
                    "-Action",
                    "Search",
                    "-Target",
                    str(transcript),
                    "-Query",
                    "优化",
                    "-Context",
                    "0",
                    "-ToolRoot",
                    str(PROJECT_ROOT),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("[part-001.md 00:00:01] 中文优化结果", completed.stdout)

    def test_main_launcher_keeps_json_stdout_machine_readable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "part-001.md"
            transcript.write_text(
                "# JSON\n\n## Transcript\n\n[00:00:01] 中文优化结果\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["BILIBILI_SUBTITLE_PYTHON"] = sys.executable
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(PROJECT_ROOT / "bilibili-subtitles.ps1"),
                    "-Action",
                    "Search",
                    "-Target",
                    str(transcript),
                    "-Query",
                    "优化",
                    "-Context",
                    "0",
                    "-Format",
                    "Json",
                    "-ToolRoot",
                    str(PROJECT_ROOT),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["items"][0]["text"], "中文优化结果")

    def test_installed_sidecar_resolves_tool_root_without_refreshed_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            install_root = root / "installed"
            install_root.mkdir()
            installed_script = install_root / "bilibili-subtitles.ps1"
            shutil.copy2(PROJECT_ROOT / "bilibili-subtitles.ps1", installed_script)
            (install_root / "bili-subtitles.config.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tool_root": str(PROJECT_ROOT),
                        "output_root": str(root / "output"),
                        "max_parts": 20,
                    }
                ),
                encoding="utf-8",
            )
            transcript = root / "part-001.md"
            transcript.write_text(
                "# Sidecar\n\n## Transcript\n\n[00:00:01] sidecar result\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.pop("BILIBILI_SUBTITLE_TOOL_ROOT", None)
            environment.pop("BILIBILI_SUBTITLE_OUTPUT_ROOT", None)
            environment.pop("BILIBILI_SUBTITLE_MAX_PARTS", None)
            environment["BILIBILI_SUBTITLE_PYTHON"] = sys.executable
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installed_script),
                    "-Action",
                    "Search",
                    "-Target",
                    str(transcript),
                    "-Query",
                    "sidecar",
                    "-Context",
                    "0",
                    "-Format",
                    "Json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["items"][0]["text"], "sidecar result")

    def test_invalid_sidecar_fails_cleanly_but_does_not_break_help(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installed_script = root / "bilibili-subtitles.ps1"
            shutil.copy2(PROJECT_ROOT / "bilibili-subtitles.ps1", installed_script)
            (root / "bili-subtitles.config.json").write_text(
                "{invalid",
                encoding="utf-8",
            )
            transcript = root / "part-001.md"
            transcript.write_text(
                "# Invalid\n\n## Transcript\n\n[00:00:01] caption\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.pop("BILIBILI_SUBTITLE_TOOL_ROOT", None)
            environment["BILIBILI_SUBTITLE_PYTHON"] = sys.executable
            help_result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installed_script),
                    "-ShowHelp",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=environment,
            )
            read_result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installed_script),
                    "-Action",
                    "Inventory",
                    "-Target",
                    str(transcript),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                env=environment,
            )

        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Bilibili subtitle utility", help_result.stdout)
        self.assertEqual(read_result.returncode, 1)
        self.assertEqual(read_result.stdout, "")
        self.assertIn("Installed configuration is invalid", read_result.stderr)

    def test_cmd_launcher_translates_common_help_flags(self):
        for help_flag in ("--help", "-h", "-?"):
            with self.subTest(help_flag=help_flag):
                completed = subprocess.run(
                    [str(PROJECT_ROOT / "bili-subtitles.cmd"), help_flag],
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("Bilibili subtitle utility", completed.stdout)

    def test_legacy_extract_entry_is_only_a_thin_delegate(self):
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
                    "-Page",
                    "2",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Main launcher not found", completed.stderr)
        self.assertNotIn("NamedParameterNotFound", completed.stderr)

    def test_main_launcher_rejects_conflicting_part_selection(self):
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROJECT_ROOT / "bilibili-subtitles.ps1"),
                "https://www.bilibili.com/video/BV1Ab411C7De",
                "-Page",
                "2",
                "-AllParts",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("Page and AllParts cannot be used together", completed.stderr)


if __name__ == "__main__":
    unittest.main()
