"""Durable FIFO admission in front of the existing cross-process model lock."""

import hashlib
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from .locking import ResourceBusy, exclusive


class ModelQueueError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelFence:
    ticket_id: str
    owner: str


@contextmanager
def model_ticket(store, root, owner_type, work_key, check, *, deadline, max_waiters=128):
    key = hashlib.sha256(work_key.encode('utf-8')).hexdigest()
    ticket, owner = uuid.uuid4().hex, uuid.uuid4().hex
    now = time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE model_wait_ticket SET state='interrupted' WHERE state='waiting' AND deadline<=?", (now,))
        db.execute("UPDATE model_wait_ticket SET state='interrupted' WHERE state='claimed' AND lease_until<=?", (now,))
        previous = db.execute('SELECT state FROM model_wait_ticket WHERE work_key=?', (key,)).fetchone()
        if previous and previous['state'] in {'waiting', 'claimed'}:
            db.execute('ROLLBACK')
            raise ModelQueueError('model_request_already_waiting')
        count = db.execute("SELECT count(*) FROM model_wait_ticket WHERE state IN ('waiting','claimed')").fetchone()[0]
        if count >= max_waiters:
            db.execute('ROLLBACK')
            raise ModelQueueError('model_queue_full')
        db.execute('''INSERT INTO model_wait_ticket(ticket_id,owner_type,work_key,state,created,deadline)
            VALUES(?,?,?,'waiting',?,?) ON CONFLICT(work_key) DO UPDATE SET
            ticket_id=excluded.ticket_id,owner_type=excluded.owner_type,state='waiting',
            created=excluded.created,deadline=excluded.deadline,owner=NULL,lease_until=NULL''',
            (ticket, owner_type, key, now, deadline))
        db.execute('COMMIT')

    stop, lost = threading.Event(), threading.Event()
    thread = None
    completed = False

    def ensure(*, call_check=True):
        if call_check:
            check()
        if lost.is_set():
            raise ModelQueueError('model_queue_ownership_lost')
        if time.time() >= deadline:
            raise ModelQueueError('model_queue_deadline')
        if thread is not None:
            with store.connect() as db:
                owned = db.execute("""SELECT 1 FROM model_wait_ticket WHERE ticket_id=? AND owner=?
                    AND state='claimed' AND lease_until>?""", (ticket, owner, time.time())).fetchone()
            if owned is None:
                raise ModelQueueError('model_queue_ownership_lost')

    def heartbeat():
        while not stop.wait(5):
            try:
                with store.connect() as db:
                    changed = db.execute("""UPDATE model_wait_ticket SET lease_until=?
                        WHERE ticket_id=? AND owner=? AND state='claimed' AND lease_until>?""",
                        (time.time() + 20, ticket, owner, time.time())).rowcount
                if not changed:
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    try:
        while True:
            ensure()
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                now = time.time()
                db.execute("UPDATE model_wait_ticket SET state='interrupted' WHERE state='waiting' AND deadline<=?", (now,))
                db.execute("UPDATE model_wait_ticket SET state='interrupted' WHERE state='claimed' AND lease_until<=?", (now,))
                active = db.execute("SELECT ticket_id FROM model_wait_ticket WHERE state='claimed' LIMIT 1").fetchone()
                first = db.execute("""SELECT ticket_id FROM model_wait_ticket WHERE state='waiting'
                    ORDER BY created,ticket_id LIMIT 1""").fetchone()
                granted = not active and first is not None and first['ticket_id'] == ticket
                if granted:
                    db.execute("UPDATE model_wait_ticket SET state='claimed',owner=?,lease_until=? WHERE ticket_id=?",
                               (owner, now + 20, ticket))
                db.execute('COMMIT')
            if granted:
                break
            stop.wait(.2)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        while True:
            ensure()
            stack = ExitStack()
            try:
                stack.enter_context(exclusive(root, 'model_resource'))
            except ResourceBusy:
                stack.close()
                stop.wait(.2)
                continue
            with stack:
                ensure()
                yield ModelFence(ticket, owner)
                ensure(call_check=False)
                completed = True
            break
    finally:
        stop.set()
        if thread is not None:
            # The caller's activity slot must outlive the heartbeat's final DB write.
            thread.join()
        with store.connect() as db:
            db.execute("UPDATE model_wait_ticket SET state=? WHERE ticket_id=? AND state IN ('waiting','claimed')",
                       ('done' if completed else 'interrupted', ticket))
