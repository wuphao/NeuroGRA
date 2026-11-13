"""Workspace-local SQLite storage for conversations and analysis links."""

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path


class ChatDatabase:
    def __init__(self, root):
        root = Path(root).resolve()
        self.path = (root / 'data/platform/chat.sqlite').resolve()
        if not self.path.is_relative_to(root):
            raise ValueError('chat_database_outside_workspace')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY NOT NULL, source TEXT NOT NULL, model TEXT,
                    started_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS chat_sessions_source ON sessions(source, archived, started_at);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
                    content TEXT, tool_name TEXT, timestamp REAL NOT NULL, platform_message_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS chat_messages_session ON messages(session_id, active, id);
                CREATE TABLE IF NOT EXISTS chat_migrations (name TEXT PRIMARY KEY NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_pending_action (
                    action_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending','consumed','expired','cancelled')),
                    result TEXT, expires REAL NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_actions_session ON chat_pending_action(session_id,state);
                CREATE TABLE IF NOT EXISTS chat_turn_state (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, request_digest TEXT NOT NULL, patient_id TEXT NOT NULL,
                    state TEXT NOT NULL, owner TEXT NOT NULL, response TEXT, error TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    PRIMARY KEY(session_id,request_id)
                );
                CREATE INDEX IF NOT EXISTS chat_turn_status ON chat_turn_state(state,updated);
                CREATE TABLE IF NOT EXISTS chat_turn_subject (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, source_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    patient_key TEXT NOT NULL, role TEXT NOT NULL,
                    PRIMARY KEY(session_id,request_id,source_id,project_id,patient_key)
                );
                CREATE TABLE IF NOT EXISTS chat_turn_cohort (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, source_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    roster_hash TEXT NOT NULL, form_key TEXT,
                    PRIMARY KEY(session_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS chat_turn_dependency (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, depends_on TEXT NOT NULL,
                    PRIMARY KEY(session_id,request_id,depends_on)
                );
                CREATE TABLE IF NOT EXISTS chat_session_subject (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL, project_id TEXT NOT NULL, patient_key TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(session_id,source_id,project_id,patient_key)
                );
            ''')
        self._migrate_collaboration()
        self._migrate_collaboration_resume()

    def _migrate_collaboration_resume(self):
        with closing(self.connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                if connection.execute("SELECT 1 FROM chat_collaboration_migrations "
                                      "WHERE name='collaboration_resume_v1'").fetchone():
                    connection.commit()
                    return
                backup = self.path.with_name(self.path.name + '.pre-resume-' + uuid.uuid4().hex + '.sqlite')
                with closing(self.connect()) as source, closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                connection.execute('''CREATE TABLE IF NOT EXISTS chat_collaboration_request (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL, request_body TEXT NOT NULL,
                    PRIMARY KEY(session_id,request_id),
                    FOREIGN KEY(session_id,request_id) REFERENCES chat_collaboration_binding(session_id,request_id)
                        ON DELETE CASCADE)''')
                connection.execute("INSERT INTO chat_collaboration_migrations VALUES('collaboration_resume_v1',?)",
                                   (time.time(),))
                if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('chat_resume_migration_foreign_key_violation')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _migrate_collaboration(self):
        """Add collaboration bindings without altering legacy fixed-column tables."""
        with closing(self.connect()) as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                exists = connection.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                    AND name='chat_collaboration_migrations'""").fetchone()
                if exists and connection.execute("SELECT 1 FROM chat_collaboration_migrations "
                                                 "WHERE name='collaboration_v2_1'").fetchone():
                    connection.commit()
                    return
                # A second read connection sees a consistent committed snapshot while
                # BEGIN IMMEDIATE prevents other chat writers until migration commits.
                backup = self.path.with_name(self.path.name + '.pre-collaboration-' + uuid.uuid4().hex + '.sqlite')
                with closing(self.connect()) as source, closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
                connection.execute('''CREATE TABLE IF NOT EXISTS chat_collaboration_migrations (
                    name TEXT PRIMARY KEY, applied REAL NOT NULL)''')
                connection.execute('''CREATE TABLE IF NOT EXISTS chat_collaboration_binding (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    request_id TEXT NOT NULL, request_digest TEXT NOT NULL, source_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, patient_key TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE,
                    created REAL NOT NULL, PRIMARY KEY(session_id,request_id))''')
                connection.execute('''CREATE TABLE IF NOT EXISTS chat_collaboration_completion (
                    session_id TEXT NOT NULL, request_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL REFERENCES messages(id), result_digest TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(session_id,request_id),
                    FOREIGN KEY(session_id,request_id) REFERENCES chat_collaboration_binding(session_id,request_id)
                        ON DELETE CASCADE)''')
                connection.execute("INSERT INTO chat_collaboration_migrations VALUES('collaboration_v2_1',?)",
                                   (time.time(),))
                if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('chat_collaboration_migration_foreign_key_violation')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        return connection

    def create_session(self, sid, source, *, model):
        with closing(self.connect()) as connection, connection:
            connection.execute('INSERT INTO sessions(id, source, model, started_at) VALUES (?,?,?,?)',
                               (sid, source, model, time.time()))

    def get_session(self, sid):
        with closing(self.connect()) as connection:
            return connection.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()

    def list_sessions(self, source, limit=30):
        with closing(self.connect()) as connection:
            return connection.execute('''
                SELECT s.id, s.started_at FROM sessions s WHERE s.source=? AND s.archived=0
                ORDER BY COALESCE((SELECT m.timestamp FROM messages m
                    WHERE m.session_id=s.id AND m.active=1 ORDER BY m.id DESC LIMIT 1), s.started_at) DESC,
                    s.id DESC LIMIT ?
            ''', (source, limit)).fetchall()

    def get_messages(self, sid):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute('''
                SELECT role, content, tool_name, platform_message_id FROM messages
                WHERE session_id=? AND active=1 ORDER BY id
            ''', (sid,))]

    def append_message(self, sid, role, content, **metadata):
        with self.transaction() as writer:
            writer.append_message(sid, role, content, **metadata)

    def create_action(self, action_id, sid, request_id, kind, payload, *, ttl=1800):
        with closing(self.connect()) as connection, connection:
            connection.execute('''INSERT OR IGNORE INTO chat_pending_action
                (action_id,session_id,request_id,kind,payload,state,expires,created)
                VALUES(?,?,?,?,?,'pending',?,?)''',
                (action_id, sid, request_id, kind, payload, time.time() + ttl, time.time()))

    def get_action(self, action_id, sid):
        with closing(self.connect()) as connection:
            row = connection.execute('SELECT * FROM chat_pending_action WHERE action_id=? AND session_id=?',
                                     (action_id, sid)).fetchone()
            return dict(row) if row else None

    def begin_turn(self, sid, request_id, digest, patient_id, owner, text, *, maximum=32):
        now = time.time()
        message_key = request_id + ':' + digest
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("""UPDATE chat_turn_state SET state='interrupted',error='服务已中断，请重新发送。',updated=?
                WHERE state IN ('accepted','waiting_model','running') AND updated<?""", (now, now - 30))
            row = connection.execute('''SELECT t.*,EXISTS(SELECT 1 FROM chat_collaboration_request r
                WHERE r.session_id=t.session_id AND r.request_id=t.request_id) AS resumable
                FROM chat_turn_state t WHERE t.session_id=? AND t.request_id=?''',
                                     (sid, request_id)).fetchone()
            if row:
                if row['request_digest'] != digest or row['patient_id'] != patient_id:
                    raise ValueError('chat_request_conflict')
                return dict(row), False
            previous = connection.execute('''SELECT platform_message_id FROM messages WHERE session_id=?
                AND substr(platform_message_id,1,?)=? LIMIT 1''',
                (sid, len(request_id) + 1, request_id + ':')).fetchone()
            if previous and previous['platform_message_id'] != message_key:
                raise ValueError('chat_request_conflict')
            count = connection.execute("SELECT count(*) FROM chat_turn_state WHERE state IN ('accepted','waiting_model','running')").fetchone()[0]
            if count >= maximum:
                raise ValueError('chat_queue_full')
            connection.execute('INSERT INTO chat_turn_state VALUES(?,?,?,?,?,?,?,?,?,?)',
                (sid, request_id, digest, patient_id, 'accepted', owner, None, None, now, now))
            if not previous:
                ChatWriter(connection).append_message(sid, 'user', text, platform_message_id=message_key)
            row = connection.execute('SELECT * FROM chat_turn_state WHERE session_id=? AND request_id=?',
                                     (sid, request_id)).fetchone()
            return dict(row), True

    def turn(self, sid, request_id):
        with closing(self.connect()) as connection, connection:
            connection.execute("""UPDATE chat_turn_state SET state='interrupted',error='服务已中断，请重新发送。',updated=?
                WHERE session_id=? AND request_id=? AND state IN ('accepted','waiting_model','running') AND updated<?""",
                (time.time(), sid, request_id, time.time() - 30))
            row = connection.execute('''SELECT t.*,EXISTS(SELECT 1 FROM chat_collaboration_request r
                WHERE r.session_id=t.session_id AND r.request_id=t.request_id) AS resumable
                FROM chat_turn_state t WHERE t.session_id=? AND t.request_id=?''',
                                     (sid, request_id)).fetchone()
            return dict(row) if row else None

    def turns(self, sid):
        with closing(self.connect()) as connection, connection:
            connection.execute("""UPDATE chat_turn_state SET state='interrupted',error='服务已中断，请重新发送。',updated=?
                WHERE session_id=? AND state IN ('accepted','waiting_model','running') AND updated<?""",
                (time.time(), sid, time.time() - 30))
            return {row['request_id']: dict(row) for row in connection.execute('''
                SELECT t.*,EXISTS(SELECT 1 FROM chat_collaboration_request r
                    WHERE r.session_id=t.session_id AND r.request_id=t.request_id) AS resumable
                FROM chat_turn_state t WHERE t.session_id=?''', (sid,))}

    def resume_turn(self, sid, request_id, owner, *, maximum=32):
        """Atomically grant one caller ownership of an interrupted request."""
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('''SELECT t.*,EXISTS(SELECT 1 FROM chat_collaboration_request r
                WHERE r.session_id=t.session_id AND r.request_id=t.request_id) AS resumable
                FROM chat_turn_state t WHERE t.session_id=? AND t.request_id=?''',
                                     (sid, request_id)).fetchone()
            if row is None:
                raise ValueError('unknown_chat_turn')
            if row['state'] in {'accepted', 'waiting_model', 'running', 'completed'}:
                return dict(row), False
            if row['state'] not in {'interrupted', 'failed'}:
                raise ValueError('chat_turn_not_resumable')
            if not connection.execute('''SELECT 1 FROM chat_collaboration_request
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone():
                raise ValueError('chat_resume_request_missing')
            count = connection.execute("SELECT count(*) FROM chat_turn_state "
                                       "WHERE state IN ('accepted','waiting_model','running')").fetchone()[0]
            if count >= maximum:
                raise ValueError('chat_queue_full')
            connection.execute("""UPDATE chat_turn_state SET state='accepted',owner=?,response=NULL,error=NULL,
                updated=? WHERE session_id=? AND request_id=?""", (owner, time.time(), sid, request_id))
            return dict(connection.execute('SELECT * FROM chat_turn_state WHERE session_id=? AND request_id=?',
                                           (sid, request_id)).fetchone()), True

    def is_turn_head(self, sid, request_id):
        with closing(self.connect()) as connection:
            row = connection.execute('''SELECT request_id FROM chat_turn_state
                WHERE session_id=? AND state IN ('accepted','waiting_model','running')
                ORDER BY created,rowid LIMIT 1''', (sid,)).fetchone()
            return row is not None and row['request_id'] == request_id

    def owns_turn(self, sid, request_id, owner):
        with closing(self.connect()) as connection:
            return connection.execute('''SELECT 1 FROM chat_turn_state
                WHERE session_id=? AND request_id=? AND owner=? AND updated>=?
                  AND state IN ('accepted','waiting_model','running')''',
                (sid, request_id, owner, time.time() - 30)).fetchone() is not None

    def update_turn(self, sid, request_id, owner, *, state=None, response=None, error=None):
        with closing(self.connect()) as connection, connection:
            return connection.execute('''UPDATE chat_turn_state SET state=COALESCE(?,state),
                response=COALESCE(?,response),error=COALESCE(?,error),updated=?
                WHERE session_id=? AND request_id=? AND owner=? AND state IN ('accepted','waiting_model','running')''',
                (state, response, error, time.time(), sid, request_id, owner)).rowcount == 1

    def reconcile_turn(self, sid, request_id, response):
        with closing(self.connect()) as connection, connection:
            connection.execute('''UPDATE chat_turn_state SET state='completed',response=?,error=NULL,updated=?
                WHERE session_id=? AND request_id=? AND state IN ('accepted','waiting_model','running','interrupted','failed')''',
                (response, time.time(), sid, request_id))

    def subjects(self, sid):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM chat_turn_subject WHERE session_id=?', (sid,))]

    def cohorts(self, sid):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM chat_turn_cohort WHERE session_id=?', (sid,))]

    def dependencies(self, sid):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM chat_turn_dependency WHERE session_id=?', (sid,))]

    def selected_subjects(self, sid):
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT * FROM chat_session_subject WHERE session_id=? ORDER BY ordinal', (sid,))]

    def set_subjects(self, sid, source_id, project_id, patient_keys):
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('DELETE FROM chat_session_subject WHERE session_id=?', (sid,))
            connection.executemany('INSERT INTO chat_session_subject VALUES(?,?,?,?,?)',
                [(sid, source_id, str(project_id), key, index) for index, key in enumerate(patient_keys)])

    def bind_collaboration(self, sid, request_id, digest, source_id, project_id, patient_key, *, request=None):
        """Bind the primary patient before any collaboration result can be exposed."""
        values = (digest, source_id, str(project_id), patient_key)
        if not all(isinstance(item, str) and item.strip() for item in values):
            raise ValueError('invalid_collaboration_binding')
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            turn = connection.execute('''SELECT request_digest,patient_id FROM chat_turn_state
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
            if turn and (turn['request_digest'] != digest or turn['patient_id'] != patient_key):
                raise ValueError('chat_request_conflict')
            existing = connection.execute('''SELECT * FROM chat_collaboration_binding
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
            if existing:
                if tuple(existing[key] for key in ('request_digest', 'source_id', 'project_id', 'patient_key')) != values:
                    raise ValueError('collaboration_binding_changed')
            else:
                run_id = uuid.uuid4().hex
                connection.execute('''INSERT INTO chat_collaboration_binding
                    (session_id,request_id,request_digest,source_id,project_id,patient_key,run_id,created)
                    VALUES(?,?,?,?,?,?,?,?)''', (sid, request_id, *values, run_id, time.time()))
                ChatWriter(connection).add_subject(sid, request_id, source_id, project_id, patient_key, 'primary')
            if request is not None:
                if not isinstance(request, dict) or set(request) != {'message', 'report', 'comparison_patient_ids'}:
                    raise ValueError('invalid_collaboration_request')
                request_body = json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=False)
                previous = connection.execute('''SELECT request_body FROM chat_collaboration_request
                    WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
                if previous and previous['request_body'] != request_body:
                    raise ValueError('collaboration_request_changed')
                if not previous:
                    connection.execute('INSERT INTO chat_collaboration_request VALUES(?,?,?)',
                                       (sid, request_id, request_body))
            return dict(connection.execute('''SELECT * FROM chat_collaboration_binding
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone())

    def collaboration_binding(self, sid, request_id):
        with closing(self.connect()) as connection:
            row = connection.execute('''SELECT * FROM chat_collaboration_binding
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
            return dict(row) if row else None

    def collaboration_request(self, sid, request_id):
        with closing(self.connect()) as connection:
            row = connection.execute('''SELECT request_body FROM chat_collaboration_request
                WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
            return json.loads(row['request_body']) if row else None

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            yield ChatWriter(connection)


class ChatWriter:
    def __init__(self, connection):
        self.connection = connection

    def require_turn_owner(self, sid, request_id, owner):
        row = self.connection.execute('''SELECT 1 FROM chat_turn_state
            WHERE session_id=? AND request_id=? AND owner=? AND updated>=?
              AND state IN ('accepted','waiting_model','running')''',
            (sid, request_id, owner, time.time() - 30)).fetchone()
        if row is None:
            raise ValueError('chat_turn_ownership_lost')

    def append_message(self, sid, role, content, *, tool_name=None, platform_message_id=None):
        cursor = self.connection.execute('''
            INSERT INTO messages(session_id, role, content, tool_name, timestamp, platform_message_id)
            VALUES (?,?,?,?,?,?)
        ''', (sid, role, content, tool_name, time.time(), platform_message_id))
        return cursor.lastrowid

    def complete_collaboration(self, sid, request_id, run_id, message_id, result_digest):
        binding = self.connection.execute('''SELECT run_id FROM chat_collaboration_binding
            WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
        if not binding or binding['run_id'] != run_id:
            raise ValueError('collaboration_completion_binding_mismatch')
        message = self.connection.execute('SELECT session_id,role FROM messages WHERE id=?', (message_id,)).fetchone()
        if not message or message['session_id'] != sid or message['role'] != 'assistant':
            raise ValueError('collaboration_completion_message_mismatch')
        existing = self.connection.execute('''SELECT run_id,message_id,result_digest
            FROM chat_collaboration_completion WHERE session_id=? AND request_id=?''', (sid, request_id)).fetchone()
        if existing:
            if tuple(existing) != (run_id, message_id, result_digest):
                raise ValueError('collaboration_completion_conflict')
            return False
        self.connection.execute('''INSERT INTO chat_collaboration_completion
            (session_id,request_id,run_id,message_id,result_digest,created) VALUES(?,?,?,?,?,?)''',
            (sid, request_id, run_id, message_id, result_digest, time.time()))
        return True

    def consume_action(self, action_id, sid, result):
        changed = self.connection.execute('''UPDATE chat_pending_action SET state='consumed',result=?
            WHERE action_id=? AND session_id=? AND state='pending' AND expires>?''',
            (result, action_id, sid, time.time())).rowcount
        if not changed:
            raise ValueError('chat_action_not_pending')

    def add_subject(self, sid, request_id, source_id, project_id, patient_key, role):
        self.connection.execute('INSERT OR IGNORE INTO chat_turn_subject VALUES(?,?,?,?,?,?)',
                                (sid, request_id, source_id, str(project_id), patient_key, role))

    def add_cohort(self, sid, request_id, source_id, project_id, roster_hash, form_key):
        self.connection.execute('INSERT OR REPLACE INTO chat_turn_cohort VALUES(?,?,?,?,?,?)',
                                (sid, request_id, source_id, str(project_id), roster_hash, form_key))

    def add_dependency(self, sid, request_id, depends_on):
        self.connection.execute('INSERT OR IGNORE INTO chat_turn_dependency VALUES(?,?,?)',
                                (sid, request_id, depends_on))
