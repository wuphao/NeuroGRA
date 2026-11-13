"""Read-only import from the former NeuroGRA PostgreSQL chat schema."""

import os
import re

from .activity import active
from .chat_database import ChatDatabase
from .evochat_store import evochat_root


def import_chat_rows(root, sessions, messages):
    with active(root), ChatDatabase(root).transaction() as writer:
        connection = writer.connection
        if connection.execute("SELECT 1 FROM chat_migrations WHERE name='legacy_postgres'").fetchone():
            return {'status': 'already_imported'}
        if connection.execute('SELECT 1 FROM sessions LIMIT 1').fetchone():
            raise ValueError('目标 SQLite 已有会话，迁移需要空库。')
        if not sessions:
            return {'status': 'empty_source', 'sessions': 0, 'messages': 0}
        connection.executemany('INSERT INTO sessions(id, source, model, started_at, archived) VALUES (?,?,?,?,?)', sessions)
        connection.executemany('''INSERT INTO messages
            (id, session_id, role, content, tool_name, timestamp, platform_message_id, active)
            VALUES (?,?,?,?,?,?,?,?)''', messages)
        connection.execute("INSERT INTO chat_migrations VALUES ('legacy_postgres')")
        return {'status': 'imported', 'sessions': len(sessions), 'messages': len(messages)}


def migrate_chat(root):
    try:
        import psycopg
        from dotenv import dotenv_values
        from psycopg import sql
    except ImportError:
        raise ValueError('旧库导入需要安装 chat-migration 可选依赖。') from None
    schema = os.environ.get('NEUROGRA_EVOCHAT_SCHEMA') or 'neurogra_chat'
    if not re.fullmatch(r'neurogra_chat(?:_[a-z0-9]{1,32})?', schema):
        raise ValueError('迁移仅接受 NeuroGRA 专用的旧 schema。')
    dsn = os.environ.get('NEUROGRA_EVOCHAT_DATABASE_URL') or dotenv_values(evochat_root() / '.env').get('DATABASE_URL')
    if not dsn:
        raise ValueError('找不到旧聊天数据库连接。')
    scope = '^neurogra-[a-f0-9]{64}$'
    try:
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            connection.read_only = True
            connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            old = sql.Identifier(schema)
            sessions = connection.execute(sql.SQL('''
                SELECT id, source, model, started_at, archived FROM {}.sessions
                WHERE source ~ %s ORDER BY started_at, id''').format(old), (scope,)).fetchall()
            messages = connection.execute(sql.SQL('''
                SELECT m.id, m.session_id, m.role, m.content, m.tool_name, m.timestamp, m.platform_message_id, m.active
                FROM {}.messages m JOIN {}.sessions s ON s.id=m.session_id
                WHERE s.source ~ %s ORDER BY m.id''').format(old, old), (scope,)).fetchall()
    except psycopg.errors.UndefinedTable:
        return {'status': 'empty_source', 'sessions': 0, 'messages': 0}
    except psycopg.Error:
        raise ValueError('无法读取旧 NeuroGRA 聊天数据库。') from None
    return import_chat_rows(root, sessions, messages)
