from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class WindowsRunnerTests(unittest.TestCase):
    def windows_powershell_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        windows_directory = Path(environment["WINDIR"])
        environment["PSModulePath"] = str(
            windows_directory
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "Modules"
        )
        return environment

    def make_fixture(
        self, python_body: str, git_body: str = "exit /b 0\r\n"
    ) -> Path:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="option-dashboard-runner-test-"
        )
        self.addCleanup(temporary_directory.cleanup)
        fixture_root = Path(temporary_directory.name)
        fixture_windows = fixture_root / "windows"
        fixture_secrets = fixture_windows / ".secrets"
        fixture_bin = fixture_root / "fake-bin"
        (fixture_root / "data").mkdir()
        (fixture_root / "scripts").mkdir()
        fixture_secrets.mkdir(parents=True)
        fixture_bin.mkdir()

        shutil.copy2(ROOT / "windows" / "run_refresh.ps1", fixture_windows)
        helper = ROOT / "windows" / "native_command.ps1"
        if helper.exists():
            shutil.copy2(helper, fixture_windows)

        (fixture_bin / "git.cmd").write_text(
            "@echo off\r\n" + git_body, encoding="utf-8"
        )
        (fixture_bin / "python.cmd").write_text(
            "@echo off\r\n" + python_body, encoding="utf-8"
        )

        token_file = fixture_secrets / "tradier_token.txt"
        escaped_token_file = str(token_file).replace("'", "''")
        token_command = (
            "$testToken = ConvertTo-SecureString 'fixture-token' "
            "-AsPlainText -Force; "
            "$testToken | ConvertFrom-SecureString | "
            f"Set-Content -LiteralPath '{escaped_token_file}'"
        )
        token_result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", token_command],
            env=self.windows_powershell_environment(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            token_result.returncode,
            0,
            msg=token_result.stdout + token_result.stderr,
        )

        self.fixture_environment = self.windows_powershell_environment()
        self.fixture_environment["PATH"] = (
            str(fixture_bin) + os.pathsep + self.fixture_environment["PATH"]
        )
        return fixture_root

    def run_fixture(self, fixture_root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(fixture_root / "windows" / "run_refresh.ps1"),
                "-Force",
            ],
            cwd=fixture_root,
            env=self.fixture_environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def latest_log(self, fixture_root: Path) -> str:
        logs = sorted((fixture_root / "windows" / "logs").glob("refresh_*.log"))
        self.assertTrue(logs, "runner did not create a log")
        contents = logs[-1].read_bytes()
        encoding = "utf-16" if contents.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        return contents.decode(encoding)

    def test_zero_exit_native_stderr_is_logged_without_failing_refresh(self) -> None:
        fixture_root = self.make_fixture(
            '> "data\\dashboard.json" echo '
            '{"updated_et":"fixture","option_data_source":"fixture"}\r\n'
            ">&2 echo harmless native stderr\r\n"
            "exit /b 0\r\n",
            'if /I "%1"=="pull" >&2 echo From fixture remote\r\n'
            "exit /b 0\r\n",
        )

        result = self.run_fixture(fixture_root)

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        log = self.latest_log(fixture_root)
        self.assertIn("From fixture remote", log)
        self.assertIn("harmless native stderr", log)
        self.assertNotIn("git pull attempt", log)
        self.assertIn("SUCCESS", log)

    def test_nonzero_native_exit_still_fails_refresh(self) -> None:
        fixture_root = self.make_fixture(
            ">&2 echo fatal native stderr\r\n"
            "exit /b 7\r\n"
        )

        result = self.run_fixture(fixture_root)

        self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
        log = self.latest_log(fixture_root)
        self.assertIn("fatal native stderr", log)
        self.assertIn("FAILED:", log)
        self.assertIn("code 7", log)


if __name__ == "__main__":
    unittest.main()
