import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

from neurogra.platform.store import PlatformStore


SCRIPT = Path(__file__).resolve().parent.parent / 'start.py'


class StartupScriptTests(unittest.TestCase):
    def invoke(self, cwd, *args):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd,
            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_check_from_another_directory_uses_checkout_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'new-workspace'
            result = self.invoke(directory, '--root', root, '--check')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(root), result.stdout)
            self.assertFalse((root / 'data/platform/platform.sqlite').exists())

    def test_port_conflict_does_not_spawn_worker(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            result = self.invoke(directory, '--root', directory, '--no-browser',
                                 '--port', listener.getsockname()[1])
            self.assertEqual(result.returncode, 1)
            self.assertEqual(PlatformStore(directory).workers(), [])

    def test_invalid_port_is_rejected_before_workspace_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'unused'
            result = self.invoke(directory, '--root', root, '--port', '65536')
            self.assertEqual(result.returncode, 2)
            self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
