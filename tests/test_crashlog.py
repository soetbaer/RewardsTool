"""Absturzprotokoll ohne Konsole (wie pythonw.exe unter Windows)."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def run_without_console(code: str, log_dir: Path) -> subprocess.CompletedProcess:
    """Startet Python so, wie pythonw.exe läuft: sys.stdout und sys.stderr sind None."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(BASE)!r})
        sys.stdout = sys.stderr = None
        from pathlib import Path
        from rewards.util import capture_crashes, log, setup_logging
        log_dir = Path({str(log_dir)!r})
        setup_logging(log_dir)
        capture_crashes(log_dir, "webui", log_exit=True)
    """) + textwrap.dedent(code)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)


class CaptureCrashesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        return (self.log_dir / name).read_text(encoding="utf-8")

    def test_unhandled_error_is_logged(self):
        res = run_without_console("raise RuntimeError('kaputt beim Start')", self.log_dir)
        self.assertNotEqual(res.returncode, 0)
        log = self.read("rewards.log")
        self.assertIn("webui abgestürzt", log)
        self.assertIn("RuntimeError: kaputt beim Start", log)
        self.assertIn("webui beendet", log)

    def test_thread_error_is_logged(self):
        res = run_without_console("""
            import threading
            def boom():
                raise ValueError('Fehler im Hintergrund')
            t = threading.Thread(target=boom, name='updates')
            t.start(); t.join()
        """, self.log_dir)
        self.assertEqual(res.returncode, 0)
        log = self.read("rewards.log")
        self.assertIn("Unbehandelter Fehler im Thread updates", log)
        self.assertIn("ValueError: Fehler im Hintergrund", log)

    def test_stray_output_and_hard_crash_go_to_crash_log(self):
        res = run_without_console("""
            print('Ausgabe ohne Konsole')
            import faulthandler
            faulthandler._sigsegv()
        """, self.log_dir)
        self.assertNotEqual(res.returncode, 0)
        crash = self.read("webui-crash.log")
        self.assertIn("--- webui gestartet", crash)
        self.assertIn("Ausgabe ohne Konsole", crash)
        self.assertIn("Fatal Python error", crash)

    def test_console_handler_only_with_console(self):
        res = run_without_console("log.info('nur in der Datei')", self.log_dir)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("nur in der Datei", self.read("rewards.log"))
        self.assertNotIn("nur in der Datei", self.read("webui-crash.log"))


if __name__ == "__main__":
    unittest.main()
