import sqlite3
import tempfile
import unittest
from pathlib import Path

from neurogra.platform.chat_database import ChatDatabase
from neurogra.platform.chat_migration import import_chat_rows


class ChatDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name

    def test_import_preserves_scope_order_and_analysis_metadata(self):
        sessions = [('sid', 'account-scope', 'model', 1, 0)]
        messages = [(10, 'sid', 'user', 'hello', None, 3, 'request:hash', 1),
                    (11, 'sid', 'assistant', 'answer', None, 3, 'request:hash', 1),
                    (12, 'sid', 'tool', '{"job_id":"job","patient_id":"11"}', 'neurogra_analysis', 4, 'request2', 1)]
        self.assertEqual(import_chat_rows(self.root, sessions, messages)['messages'], 3)
        database = ChatDatabase(self.root)
        self.assertFalse(database.list_sessions('different-scope'))
        self.assertEqual(database.list_sessions('account-scope')[0]['id'], 'sid')
        saved = database.get_messages('sid')
        self.assertEqual([row['role'] for row in saved], ['user', 'assistant', 'tool'])
        self.assertEqual(saved[1]['platform_message_id'], 'request:hash')
        self.assertEqual(saved[2]['tool_name'], 'neurogra_analysis')
        self.assertEqual(import_chat_rows(self.root, sessions, messages)['status'], 'already_imported')
        self.assertEqual(len(database.get_messages('sid')), 3)

    def test_failed_import_rolls_back_and_live_chat_cannot_be_overwritten(self):
        sessions = [('sid', 'scope', None, 1, 0)]
        invalid = [(10, 'unlinked', 'user', 'hello', None, 3, None, 1)]
        with self.assertRaises(sqlite3.IntegrityError):
            import_chat_rows(self.root, sessions, invalid)
        database = ChatDatabase(self.root)
        self.assertIsNone(database.get_session('sid'))
        database.create_session('live', 'scope', model='model')
        with self.assertRaisesRegex(ValueError, '已有会话'):
            import_chat_rows(self.root, sessions, [])
        with database.transaction() as writer:
            writer.append_message('live', 'user', 'hello')
            writer.append_message('live', 'assistant', 'reply')
        self.assertEqual([row['content'] for row in ChatDatabase(self.root).get_messages('live')], ['hello', 'reply'])

    def test_collaboration_binding_and_completion_are_scoped_and_idempotent(self):
        database = ChatDatabase(self.root)
        database.create_session('sid', 'scope', model='model')
        database.begin_turn('sid', 'request', 'digest', '11', 'owner', '问题')
        binding = database.bind_collaboration('sid', 'request', 'digest', 'source', 'project', '11')
        self.assertEqual(database.bind_collaboration('sid', 'request', 'digest', 'source', 'project', '11'), binding)
        self.assertEqual(database.subjects('sid')[0]['patient_key'], '11')
        with self.assertRaisesRegex(ValueError, 'chat_request_conflict'):
            database.bind_collaboration('sid', 'request', 'other', 'source', 'project', '11')
        with self.assertRaisesRegex(ValueError, 'collaboration_binding_changed'):
            database.bind_collaboration('sid', 'request', 'digest', 'other-source', 'project', '11')
        with database.transaction() as writer:
            message_id = writer.append_message('sid', 'assistant', '回答', platform_message_id='request:digest')
            self.assertTrue(writer.complete_collaboration('sid', 'request', binding['run_id'], message_id, 'result'))
            self.assertFalse(writer.complete_collaboration('sid', 'request', binding['run_id'], message_id, 'result'))
        with self.assertRaisesRegex(ValueError, 'collaboration_completion_conflict'):
            with database.transaction() as writer:
                writer.complete_collaboration('sid', 'request', binding['run_id'], message_id, 'changed')
        self.assertEqual(len(list(Path(self.root).glob('data/platform/chat.sqlite.pre-collaboration-*.sqlite'))), 1)
        ChatDatabase(self.root)
        self.assertEqual(len(list(Path(self.root).glob('data/platform/chat.sqlite.pre-collaboration-*.sqlite'))), 1)

    def test_completion_rejects_another_sessions_message(self):
        database = ChatDatabase(self.root)
        database.create_session('first', 'scope1', model='model')
        database.create_session('second', 'scope2', model='model')
        binding = database.bind_collaboration('first', 'request', 'digest', 'source', 'project', '11')
        with database.transaction() as writer:
            other = writer.append_message('second', 'assistant', '不能跨会话引用')
        with self.assertRaisesRegex(ValueError, 'collaboration_completion_message_mismatch'):
            with database.transaction() as writer:
                writer.complete_collaboration('first', 'request', binding['run_id'], other, 'result')
