"""Successful builds remove intermediates; failed builds preserve diagnostics."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('monitor_build', Path(__file__).resolve().parents[1] / 'build_exe.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildCleanupTests(unittest.TestCase):
    def test_success_cleans_build_and_preserves_executable(self):
        for options in ([], ['--onedir']):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'build').mkdir()
                (root / 'build' / 'old.spec').write_text('diagnostic')
                outputs = []
                def package(command, **kwargs):
                    name = command[command.index('--name') + 1]
                    output = root / 'dist' / name / (name + '.exe') if options else root / 'dist' / (name + '.exe')
                    output.parent.mkdir(parents=True)
                    output.write_bytes(b'executable')
                    outputs.append(output)
                with patch.object(builder, '__file__', str(root / 'build_exe.py')), \
                     patch.object(builder.importlib.util, 'find_spec', return_value=object()), \
                     patch.object(builder.subprocess, 'run', side_effect=package):
                    self.assertEqual(builder.build(options), 0)
                self.assertFalse((root / 'build').exists())
                self.assertEqual(outputs[0].read_bytes(), b'executable')

    def test_failure_preserves_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'build').mkdir()
            diagnostic = root / 'build' / 'failure.log'
            diagnostic.write_text('diagnostic')
            with patch.object(builder, '__file__', str(root / 'build_exe.py')), \
                 patch.object(builder.importlib.util, 'find_spec', return_value=object()), \
                 patch.object(builder.subprocess, 'run', side_effect=RuntimeError('build failed')):
                with self.assertRaisesRegex(RuntimeError, 'build failed'):
                    builder.build([])
            self.assertEqual(diagnostic.read_text(), 'diagnostic')


if __name__ == '__main__': unittest.main()
