"""Crash hooks are exercised in child processes so the test runner stays intact."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CrashDiagnosticsTests(unittest.TestCase):
    def run_child(self, script):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(__file__).resolve().parents[1] / 'src'
            bootstrap = f'import sys;sys.path.insert(0,{str(source)!r})\n'
            bootstrap += f'from crash_diagnostics import install\nd=install({directory!r})\n'
            result = subprocess.run([sys.executable,'-X','utf8','-c',bootstrap+script],
                                    capture_output=True,timeout=15)
            logs=list((Path(directory)/'logs'/'crash_reports').glob('*.log'))
            self.assertEqual(len(logs),1)
            return result,logs[0].read_text(encoding='utf-8',errors='replace')

    def test_unhandled_python_exception_is_durable(self):
        result,log=self.run_child("raise RuntimeError('test qt-slot-style exception')\n")
        self.assertNotEqual(result.returncode,0)
        self.assertIn('UNHANDLED_PYTHON_EXCEPTION',log)
        self.assertIn('RuntimeError: test qt-slot-style exception',log)
        self.assertIn('SESSION_START',log)

    def test_thread_exception_and_resource_snapshot(self):
        result,log=self.run_child("import threading\ndef fail(): raise ValueError('thread failure')\nt=threading.Thread(target=fail);t.start();t.join()\nd.resources()\n")
        self.assertEqual(result.returncode,0)
        self.assertIn('UNHANDLED_THREAD_EXCEPTION',log)
        self.assertIn('ValueError: thread failure',log)
        self.assertIn('PROCESS_MEMORY',log)
        self.assertIn('PYTHON_ATEXIT',log)

    def test_native_abort_writes_fault_stack(self):
        script="import os\n"
        if os.name=='nt':
            script+="import ctypes\nctypes.windll.kernel32.SetErrorMode(0x0002|0x8000)\n"
        result,log=self.run_child(script+"os.abort()\n")
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Fatal Python error',log)
        self.assertNotIn('PYTHON_ATEXIT',log)

    def test_qt_slot_exception_is_logged_and_requests_controlled_stop(self):
        result,log=self.run_child("from PyQt5.QtWidgets import QApplication\nfrom PyQt5.QtCore import QTimer\napp=QApplication([])\nd.attach_qt()\ndef stop(message):\n d.write('STOP_CALLBACK '+message)\n app.quit()\nd.exception_callback=stop\ndef fail(): raise RuntimeError('Qt slot failure')\nQTimer.singleShot(0,fail)\nQTimer.singleShot(2000,app.quit)\napp.exec_()\n")
        self.assertEqual(result.returncode,0)
        self.assertIn('UNHANDLED_PYTHON_EXCEPTION',log)
        self.assertIn('STOP_CALLBACK RuntimeError: Qt slot failure',log)


if __name__=='__main__': unittest.main()
