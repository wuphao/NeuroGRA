import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from neurogra.clinical.collaboration_contracts import CollaborationGraph, CollaborationTask, SubjectScope
from neurogra.clinical.collaboration_store import CollaborationStore
from neurogra.clinical.storage import RunStore
from neurogra.platform.application import Application
from neurogra.platform.async_chat import AsyncChat
from neurogra.platform.backup import Backups
from neurogra.platform.configuration import resolve_config
from neurogra.platform.lifecycle import Lifecycle
from neurogra.platform.locking import ResourceBusy, exclusive
from neurogra.platform.patient_analysis import PatientAnalysis
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.patient_workspace import PatientWorkspace, WorkspaceError
from neurogra.platform.tests.test_patient_analysis import Reader


class AsyncChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Application(self.root, resolve_config(root=self.root, profile='kb-only'))
        self.workspace = PatientWorkspace(self.app.config, reader_factory=lambda *args: Reader())
        with patch('neurogra.platform.patient_workspace.obtain_token', return_value='private-token'):
            self.cookie = self.workspace.login('synthetic-account', 'private-password')
        self.session = self.workspace.session(self.cookie)
        self.chat = PatientChat(self.app, self.workspace, PatientAnalysis(self.app, self.workspace, starter=lambda _: None))
        self.manager = AsyncChat(self.chat, self.workspace)
        self.sid = self.chat.create(self.session, '1')['id']
        self.memory = SimpleNamespace(format_for_system_prompt=lambda _: '')

    @contextmanager
    def fake_memory(self, _scope):
        yield self.memory

    def wait_status(self, request_id, allowed, seconds=8):
        deadline = time.time() + seconds
        while time.time() < deadline:
            row = self.chat.store.database().turn(self.sid, request_id)
            if row and row['state'] in allowed:
                return row
            time.sleep(.05)
        self.fail(f'timed out waiting for {request_id}: {row}')

    def test_messages_wait_for_busy_model_and_are_recoverable(self):
        one = {'request_id': 'request-one', 'message': '你好',
               'capabilities': ['async_turns_v1', 'interactions_v1']}
        two = {'request_id': 'request-two', 'message': '谢谢',
               'capabilities': ['async_turns_v1', 'interactions_v1']}
        def model_reply(*args, model_access=None, **kwargs):
            with model_access('test'):
                return '已处理。', []

        with patch.object(self.chat.store, 'memory', self.fake_memory), \
             patch.object(self.chat, 'model_reply', side_effect=model_reply):
            with exclusive(self.root, 'model_resource'):
                accepted = self.manager.start(self.cookie, self.session, '1', self.sid, one)
                self.assertIn(accepted['turn']['status'], {'accepted', 'running', 'waiting_model'})
                self.manager.start(self.cookie, self.session, '1', self.sid, two)
                self.wait_status('request-one', {'waiting_model'})
                saved = self.chat.messages(self.session, '1', self.sid)['messages']
                self.assertEqual(sum(item['kind'] == 'user' for item in saved), 2)
                self.assertTrue(any(item.get('turn', {}).get('status') == 'waiting_model' for item in saved))
                with self.assertRaises(WorkspaceError):
                    self.manager.start(self.cookie, self.session, '1', self.sid, {**one, 'message': '不同内容'})
            self.wait_status('request-one', {'completed'})
            self.wait_status('request-two', {'completed'})
            self.assertEqual(self.manager.get(self.session, '1', self.sid, 'request-one')['reply']['text'], '已处理。')
            replay = self.manager.start(self.cookie, self.session, '1', self.sid, one)
            self.assertEqual(replay['turn']['status'], 'completed')
            self.assertEqual(len([row for row in self.chat.store.database().get_messages(self.sid)
                                  if row['role'] == 'assistant']), 2)
        data = (self.root / 'data/platform/chat.sqlite').read_bytes()
        self.assertNotIn(b'private-token', data)
        self.assertNotIn(b'private-password', data)

    def test_stale_running_turn_is_marked_interrupted(self):
        db = self.chat.store.database()
        db.begin_turn(self.sid, 'stale-turn', 'digest', '1', 'old-owner', '你好')
        with db.transaction() as tx:
            tx.connection.execute("UPDATE chat_turn_state SET state='running',updated=? WHERE request_id='stale-turn'",
                                  (time.time() - 60,))
        result = self.manager.get(self.session, '1', self.sid, 'stale-turn')
        self.assertEqual(result['turn']['status'], 'interrupted')
        self.assertIn('中断', result['reply']['text'])

    def test_late_async_owner_cannot_commit_after_ownership_changes(self):
        db = self.chat.store.database()
        original_transaction = self.chat.store.transaction
        original_run = self.manager._run
        attempted, stopped = threading.Event(), threading.Event()

        def run_and_signal(*args, **kwargs):
            try:
                return original_run(*args, **kwargs)
            finally:
                stopped.set()

        @contextmanager
        def change_owner_before_commit():
            with db.transaction() as transaction:
                transaction.connection.execute("UPDATE chat_turn_state SET owner='replacement',updated=? "
                                               "WHERE session_id=? AND request_id=?",
                                               (time.time(), self.sid, 'stale-owner'))
            try:
                with original_transaction() as transaction:
                    yield transaction
            finally:
                attempted.set()

        body = {'request_id': 'stale-owner', 'message': '你好', 'capabilities': ['async_turns_v1']}
        with patch.object(self.chat.store, 'memory', self.fake_memory), \
             patch.object(self.chat, 'model_reply', return_value=('不应提交的旧回复', [])), \
             patch.object(self.chat.store, 'transaction', side_effect=change_owner_before_commit), \
             patch.object(self.manager, '_run', side_effect=run_and_signal):
            self.manager.start(self.cookie, self.session, '1', self.sid, body)
            self.assertTrue(attempted.wait(5))
            self.assertTrue(stopped.wait(5))
        self.assertFalse(any(row['role'] == 'assistant' for row in db.get_messages(self.sid)))
        self.assertEqual(db.turn(self.sid, 'stale-owner')['owner'], 'replacement')
        self.assertTrue(db.update_turn(self.sid, 'stale-owner', 'replacement', state='failed', error='synthetic'))

    def test_maintenance_is_rejected_between_calls_of_active_chat(self):
        between_calls, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        plan = Lifecycle(self.app).preview()
        body = {'request_id': 'maintenance-gap', 'message': '你好',
                'capabilities': ['async_turns_v1', 'interactions_v1']}

        def model_reply(*_args, model_access=None, **_kwargs):
            with model_access('first'):
                pass
            between_calls.set()
            release.wait(8)
            with model_access('second'):
                return '合成回复。', []

        with patch.object(self.chat.store, 'memory', self.fake_memory), \
             patch.object(self.chat, 'model_reply', side_effect=model_reply):
            self.manager.start(self.cookie, self.session, '1', self.sid, body)
            self.assertTrue(between_calls.wait(5))
            with exclusive(self.root, 'model_resource'):
                with self.assertRaises(ResourceBusy):
                    Backups(self.app).create(self.root / 'synthetic-backup')
                with self.assertRaises(ResourceBusy):
                    Lifecycle(self.app).apply(plan)
            self.assertFalse((self.root / 'synthetic-backup').exists())
            release.set()
            self.wait_status('maintenance-gap', {'completed'})

    def test_maintenance_rejects_a_queued_turn_before_worker_registration(self):
        db = self.chat.store.database()
        db.begin_turn(self.sid, 'unstarted-turn', 'digest', '1', 'owner', '合成问题')
        plan = Lifecycle(self.app).preview()
        with self.assertRaisesRegex(ResourceBusy, 'chat_turns_not_quiescent'):
            Backups(self.app).create(self.root / 'synthetic-backup')
        with self.assertRaisesRegex(ResourceBusy, 'chat_turns_not_quiescent'):
            Lifecycle(self.app).apply(plan)
        self.assertFalse((self.root / 'synthetic-backup').exists())
        self.assertTrue(db.update_turn(self.sid, 'unstarted-turn', 'owner', state='failed', error='synthetic'))
        Lifecycle(self.app).apply(plan)

    def test_collaboration_progress_requires_live_patient_access_and_hides_content(self):
        db = self.chat.store.database()
        db.begin_turn(self.sid, 'progress-one', 'digest', '1', 'owner', '患者的问题')
        binding = db.bind_collaboration(self.sid, 'progress-one', 'digest', self.workspace.source_id,
                                        self.workspace.schema.project_id, '1')
        config = self.app.configuration('standard')
        runs = RunStore(config.resolve(config.database))
        runs.create_run({}, {'seconds': 60}, run_id=binding['run_id'])
        engine = CollaborationStore(runs)
        scope = SubjectScope(source_id=self.workspace.source_id, project_id=str(self.workspace.schema.project_id),
                             primary_patient='1')
        engine.create(binding['run_id'], scope, entry_kind='chat', entry_key='one', config_digest='config')
        fence = engine.claim(binding['run_id'], 'worker')
        node = CollaborationTask(task_id='read', kind='patient.read', objective='隐私内容不得出现在进度中',
                                 patient_keys=['1'], form_keys=['moca'])
        engine.install_graph(fence, CollaborationGraph(tasks=[node]), form_keys={'moca'}, agent_types=set())
        engine.claim_next(fence)
        progress = self.manager.collaboration_progress(self.session, '1', self.sid, 'progress-one')
        self.assertTrue(progress['available'])
        self.assertEqual(progress['tasks'][0]['state'], 'running')
        self.assertNotIn('隐私内容', str(progress))
        self.assertNotIn('patient_keys', str(progress))
        cursor = progress['next_cursor']
        self.assertEqual(self.manager.collaboration_progress(
            self.session, '1', self.sid, 'progress-one', after=cursor)['events'], [])
        with patch.object(self.workspace, 'authorize_forms', side_effect=WorkspaceError('无权访问', 403)):
            with self.assertRaises(WorkspaceError) as error:
                self.manager.collaboration_progress(self.session, '1', self.sid, 'progress-one')
        self.assertEqual(error.exception.status, 403)


if __name__ == '__main__':
    unittest.main()
