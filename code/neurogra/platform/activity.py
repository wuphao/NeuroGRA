"""Compatibility exports for the workspace's common maintenance gate."""

import sqlite3
from contextlib import closing
from pathlib import Path

from neurogra_common.activity import SLOTS, active, maintenance, tracked
from neurogra_common.locking import ResourceBusy


def assert_chat_quiescent(root):
    """Maintenance owns the gate; also reject a queued turn between thread handoff."""
    path = (Path(root).resolve() / 'data/platform/chat.sqlite').resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('chat_database_outside_workspace')
    if not path.is_file():
        return
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=15)) as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_turn_state'").fetchone()
        if table and db.execute("SELECT 1 FROM chat_turn_state WHERE state IN "
                                "('accepted','waiting_model','running') LIMIT 1").fetchone():
            raise ResourceBusy('chat_turns_not_quiescent')


__all__ = ['SLOTS', 'active', 'maintenance', 'tracked', 'assert_chat_quiescent']
