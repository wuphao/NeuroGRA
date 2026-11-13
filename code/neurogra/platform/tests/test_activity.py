import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from neurogra.platform.activity import active, maintenance
from neurogra.platform.locking import ResourceBusy


class ActivityGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_activity_is_reentrant_but_blocks_maintenance(self):
        with active(self.root), active(self.root):
            with self.assertRaises(ResourceBusy), maintenance(self.root):
                pass
        with maintenance(self.root):
            pass

    def test_maintenance_blocks_new_activity_without_partial_entry(self):
        with maintenance(self.root):
            with self.assertRaises(ResourceBusy), active(self.root, timeout=0):
                pass
        with active(self.root, timeout=0):
            pass

    def test_concurrent_threads_have_independent_activity_slots(self):
        ready, release = threading.Event(), threading.Event()

        def worker():
            with active(self.root):
                ready.set()
                release.wait(5)

        thread = threading.Thread(target=worker)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(ready.wait(5))
        with active(self.root):
            release.set()
            thread.join(5)
            with self.assertRaises(ResourceBusy), maintenance(self.root):
                pass
        with maintenance(self.root):
            pass

    def test_process_exit_releases_registered_activity(self):
        code = ('import sys; from neurogra.platform.activity import active; '
                'root=sys.argv[1]; gate=active(root); gate.__enter__(); '
                'print("ready",flush=True); sys.stdin.readline()')
        process = subprocess.Popen([sys.executable, '-c', code, str(self.root)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertEqual(process.stdout.readline().strip(), 'ready')
        with self.assertRaises(ResourceBusy), maintenance(self.root):
            pass
        process.stdin.write('\n')
        process.stdin.flush()
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        with maintenance(self.root):
            pass

    def test_standalone_knowledge_commands_share_activity_and_model_locks(self):
        from neurogra_common.locking import exclusive
        from neurogra_kb import cli as kb_cli
        from neurogra_kb.config import KBConfig
        from neurogra_kg import cli as kg_cli
        from neurogra_kg.config import KGConfig

        def inspect_locks(*_args):
            with self.assertRaises(ResourceBusy), maintenance(self.root):
                pass
            with self.assertRaises(ResourceBusy), exclusive(self.root, 'model_resource'):
                pass
            return 7

        with patch.object(kg_cli, 'load_config', return_value=KGConfig(root=self.root)), \
                patch.object(kg_cli, 'run_command', side_effect=inspect_locks):
            self.assertEqual(kg_cli.main(['retrieve', '--request', 'synthetic.json']), 7)
        with patch.object(kb_cli, 'load_config', return_value=KBConfig(root=self.root)), \
                patch.object(kb_cli, 'run_command', side_effect=inspect_locks), \
                patch('sys.argv', ['neurogra-kb', 'query', 'synthetic']):
            self.assertEqual(kb_cli.main(), 7)

    def test_activity_outlives_a_delayed_collaboration_heartbeat(self):
        from neurogra.clinical.collaboration_contracts import SubjectScope
        from neurogra.clinical.collaboration_store import CollaborationStore
        from neurogra.clinical.storage import RunStore

        engine = RunStore(self.root / 'clinical.sqlite')
        run_id = engine.create_run({}, {'seconds': 60, 'llm': 1})
        coordination = CollaborationStore(engine)
        coordination.create(run_id, SubjectScope(source_id='synthetic', project_id='1', primary_patient='1'),
                            entry_kind='chat', entry_key='synthetic', config_digest='synthetic')
        entered, leaving, release, done = (threading.Event() for _ in range(4))
        heartbeat = coordination.heartbeat

        def delayed(*args, **kwargs):
            entered.set()
            release.wait(6)
            return heartbeat(*args, **kwargs)

        def operation():
            try:
                with active(self.root):
                    with coordination.owned(run_id, 'synthetic', lease_seconds=.3):
                        entered.wait(5)
                        leaving.set()
            finally:
                done.set()

        with patch.object(coordination, 'heartbeat', side_effect=delayed):
            thread = threading.Thread(target=operation)
            thread.start()
            self.addCleanup(release.set)
            self.assertTrue(leaving.wait(5))
            self.assertFalse(done.wait(2.3))
            with self.assertRaises(ResourceBusy), maintenance(self.root):
                pass
            release.set()
            thread.join(5)
        self.assertTrue(done.is_set())
        with maintenance(self.root):
            pass
