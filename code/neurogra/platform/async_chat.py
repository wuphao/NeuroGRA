"""Persist chat turn state while keeping RWE credentials only in the live process."""

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing

from neurogra.clinical.collaboration_contracts import SubjectScope

from .activity import tracked
from . import multi_patient
from .mixed_collaboration import mixed_run_id
from .patient_chat import REQUEST_ID, request_fingerprint
from .patient_workspace import WorkspaceError


class AsyncChat:
    def __init__(self, chat, workspace):
        self.chat, self.workspace = chat, workspace

    def authorize(self, session, patient_id, sid):
        scope, _ = self.chat.scope(session, patient_id)
        self.chat.authorized_session(self.chat.store.database(), scope, sid)

    @tracked(lambda self: self.chat.app.root)
    def start(self, session_id, session, patient_id, sid, body):
        if patient_id is None:
            raise WorkspaceError('请先选择患者。', 400)
        text, request_id = body.get('message'), body.get('request_id')
        if (body.get('action_response') is not None or not isinstance(text, str) or
                not 1 <= len(text.strip()) <= 2000 or not isinstance(request_id, str) or
                not REQUEST_ID.fullmatch(request_id)):
            raise WorkspaceError('对话请求无效。', 400)
        capabilities = body.get('capabilities')
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise WorkspaceError('客户端能力标识无效。', 400)
        self.authorize(session, patient_id, sid)
        db = self.chat.store.database()
        other_patients = body.get('comparison_patient_ids', [])
        if (not isinstance(other_patients, list) or len(other_patients) > 5 or
                any(not isinstance(key, str) or not re.fullmatch(r'[0-9]{1,20}', key) or key == patient_id
                    for key in other_patients) or
                len(set(other_patients)) != len(other_patients)):
            raise WorkspaceError('对比患者选择无效。', 400)
        digest = request_fingerprint(text, body.get('report'), other_patients)
        owner = uuid.uuid4().hex
        try:
            row, created = db.begin_turn(sid, request_id, digest, patient_id, owner, text.strip())
        except ValueError as error:
            if str(error) == 'chat_queue_full':
                raise WorkspaceError('对话等待队列已满，请稍后重试。', 429) from None
            raise WorkspaceError('同一请求标识已用于其他问题。', 409) from None
        if created:
            thread = threading.Thread(target=self._run,
                args=(session_id, session, patient_id, sid, dict(body), owner), daemon=True)
            thread.start()
        return self._view(session, patient_id, sid, row)

    @tracked(lambda self: self.chat.app.root, timeout=1200)
    def _run(self, session_id, session, patient_id, sid, body, owner, resume=False):
        request_id = body['request_id']
        db = self.chat.store.database()
        stop, lost = threading.Event(), threading.Event()
        deadline = time.time() + max(1200, self.chat.app.config.model.timeout_seconds * 2)

        def heartbeat():
            while not stop.wait(5):
                try:
                    if not db.update_turn(sid, request_id, owner):
                        lost.set()
                        return
                except Exception:
                    lost.set()
                    return

        def check():
            if lost.is_set():
                raise WorkspaceError('对话状态已失效，请重试。', 409)
            if not db.owns_turn(sid, request_id, owner):
                lost.set()
                raise WorkspaceError('对话状态已失效，请重试。', 409)
            if self.workspace.session(session_id) is not session:
                raise WorkspaceError('登录已失效，请重新登录。', 401)
            if time.time() >= deadline:
                raise WorkspaceError('对话等待超时，请重试。', 503)

        def state(value):
            if not db.update_turn(sid, request_id, owner, state=value):
                lost.set()
                check()

        check.state = state
        check.turn_owner = owner
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            while not db.is_turn_head(sid, request_id):
                check()
                state('waiting_model')
                stop.wait(.25)
            state('running')
            while True:
                check()
                try:
                    if resume:
                        result = self.chat.send(session, patient_id, sid, body, check, resume_collaboration=True)
                    else:
                        result = self.chat.send(session, patient_id, sid, body, check)
                    break
                except WorkspaceError as error:
                    if error.status != 409 or str(error) != '对话或模型正在使用中，请稍后重试。':
                        raise
                    state('waiting_model')
                    stop.wait(.25)
            db.update_turn(sid, request_id, owner, state='completed',
                           response=json.dumps(result, ensure_ascii=False))
        except WorkspaceError as error:
            db.update_turn(sid, request_id, owner, state='interrupted' if error.status == 401 else 'failed',
                           error=str(error))
        except Exception:
            db.update_turn(sid, request_id, owner, state='failed', error='对话未完成，请稍后重试。')
        finally:
            stop.set()
            heartbeat_thread.join()

    @tracked(lambda self: self.chat.app.root)
    def get(self, session, patient_id, sid, request_id):
        self.authorize(session, patient_id, sid)
        db = self.chat.store.database()
        row = db.turn(sid, request_id)
        if not row or row['patient_id'] != patient_id:
            raise WorkspaceError('对话请求不存在或无权查看。', 404)
        if row['state'] != 'completed':
            key = request_id + ':' + row['request_digest']
            previous = [item for item in db.get_messages(sid) if item.get('platform_message_id') == key]
            if any(item['role'] == 'assistant' for item in previous):
                self.workspace.authorize_forms(session, patient_id)
                reply = self.chat.public_messages(previous)[-1]
                db.reconcile_turn(sid, request_id, json.dumps({'reply': reply}, ensure_ascii=False))
                row = db.turn(sid, request_id)
        return self._view(session, patient_id, sid, row)

    @tracked(lambda self: self.chat.app.root)
    def resume(self, session_id, session, patient_id, sid, request_id):
        """Resume only an explicitly requested, freshly authorized collaboration turn."""
        if patient_id is None:
            raise WorkspaceError('请先选择患者。', 400)
        self.authorize(session, patient_id, sid)
        db = self.chat.store.database()
        row = db.turn(sid, request_id)
        if not row or row['patient_id'] != patient_id:
            raise WorkspaceError('对话请求不存在或无权查看。', 404)
        if row['state'] == 'completed':
            return self._view(session, patient_id, sid, row)
        binding = db.collaboration_binding(sid, request_id)
        request = db.collaboration_request(sid, request_id)
        if binding is None or request is None:
            raise WorkspaceError('该请求没有可恢复的协作记录，请重新提问。', 409)
        if (binding['patient_key'] != patient_id or binding['source_id'] != self.workspace.source_id or
                binding['project_id'] != str(self.workspace.schema.project_id) or
                binding['request_digest'] != row['request_digest']):
            raise WorkspaceError('协作记录与当前患者来源不一致。', 403)
        try:
            digest = request_fingerprint(request['message'], request['report'], request['comparison_patient_ids'])
        except (KeyError, TypeError, ValueError):
            raise WorkspaceError('原始对话请求无法验证，请重新提问。', 409) from None
        if digest != row['request_digest']:
            raise WorkspaceError('原始对话请求已变化，请重新提问。', 409)
        self.workspace.authorize_forms(session, patient_id)
        self.chat.authorize_turn(session, sid, request_id)
        for other in request['comparison_patient_ids']:
            self.workspace.authorize_forms(session, other)
        # Parent scope may contain an authorized cohort that was not committed
        # to chat history when the process stopped.
        self.collaboration_progress(session, patient_id, sid, request_id, limit=1)
        owner = uuid.uuid4().hex
        try:
            resumed, created = db.resume_turn(sid, request_id, owner)
        except ValueError as error:
            if str(error) == 'chat_queue_full':
                raise WorkspaceError('对话等待队列已满，请稍后重试。', 429) from None
            raise WorkspaceError('该请求暂时无法恢复，请重新提问。', 409) from None
        if created:
            body = {**request, 'request_id': request_id,
                    'capabilities': ['async_turns_v1', 'interactions_v1', 'collaboration_progress_v1']}
            threading.Thread(target=self._run, args=(session_id, session, patient_id, sid, body, owner, True),
                             daemon=True).start()
        return self._view(session, patient_id, sid, resumed)

    @tracked(lambda self: self.chat.app.root)
    def collaboration_progress(self, session, patient_id, sid, request_id, *, after=0, limit=100):
        """Return only authorized operational metadata, never stored case or model text."""
        if not isinstance(after, int) or not isinstance(limit, int) or after < 0 or not 1 <= limit <= 200:
            raise WorkspaceError('协作进度游标无效。', 400)
        self.authorize(session, patient_id, sid)
        database = self.chat.store.database()
        turn = database.turn(sid, request_id)
        if not turn or turn['patient_id'] != patient_id:
            raise WorkspaceError('对话请求不存在或无权查看。', 404)
        binding = database.collaboration_binding(sid, request_id)
        if binding is None:
            return {'available': False, 'events': [], 'next_cursor': after}
        if (binding['request_digest'] != turn['request_digest'] or binding['patient_key'] != patient_id or
                binding['source_id'] != self.workspace.source_id or
                binding['project_id'] != str(self.workspace.schema.project_id)):
            raise WorkspaceError('协作记录与当前患者来源不一致。', 403)
        self.workspace.authorize_forms(session, patient_id)
        self.chat.authorize_turn(session, sid, request_id)
        config = self.chat.app.configuration('standard')
        path = config.resolve(config.database)
        if not path.is_file():
            return {'available': False, 'events': [], 'next_cursor': after}
        try:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                selected_run = mixed_run_id(binding['run_id'])
                row = connection.execute('''SELECT state,graph_version,scope FROM collaboration_runs
                    WHERE run_id=?''', (selected_run,)).fetchone()
                if row is None:
                    selected_run = binding['run_id']
                    row = connection.execute('''SELECT state,graph_version,scope FROM collaboration_runs
                        WHERE run_id=?''', (selected_run,)).fetchone()
                if row is None:
                    return {'available': False, 'events': [], 'next_cursor': after}
                scope = SubjectScope.model_validate_json(row['scope'])
                if (scope.source_id != binding['source_id'] or scope.project_id != binding['project_id'] or
                        scope.primary_patient != patient_id):
                    raise WorkspaceError('协作记录的患者范围不一致。', 403)
                if selected_run == binding['run_id'] and (scope.comparison_patients or scope.cohort_binding):
                    raise WorkspaceError('协作记录的患者范围不一致。', 403)
                tasks = [dict(item) for item in connection.execute('''
                    SELECT n.task_id,n.task_version,n.kind,n.agent_type,n.state
                    FROM collaboration_nodes n JOIN collaboration_graph_nodes g
                      ON n.run_id=g.run_id AND n.task_id=g.task_id AND n.task_version=g.task_version
                    WHERE g.run_id=? AND g.graph_version=? ORDER BY g.ordinal''',
                    (selected_run, row['graph_version']))]
                definitions = [json.loads(item['definition']) for item in connection.execute('''
                    SELECT n.definition FROM collaboration_nodes n JOIN collaboration_graph_nodes g
                      ON n.run_id=g.run_id AND n.task_id=g.task_id AND n.task_version=g.task_version
                    WHERE g.run_id=? AND g.graph_version=? AND n.kind='cohort.aggregate' ''',
                    (selected_run, row['graph_version']))]
                if selected_run != binding['run_id']:
                    child = connection.execute('''SELECT graph_version,scope FROM collaboration_runs
                        WHERE run_id=?''', (binding['run_id'],)).fetchone()
                    if child is not None:
                        child_scope = SubjectScope.model_validate_json(child['scope'])
                        if (child_scope.source_id != scope.source_id or child_scope.project_id != scope.project_id or
                                child_scope.primary_patient != patient_id or child_scope.comparison_patients or
                                child_scope.cohort_binding):
                            raise WorkspaceError('局部协作记录的患者范围不一致。', 403)
                        for item in connection.execute('''SELECT n.task_id,n.task_version,n.kind,n.agent_type,n.state
                            FROM collaboration_nodes n JOIN collaboration_graph_nodes g
                              ON n.run_id=g.run_id AND n.task_id=g.task_id AND n.task_version=g.task_version
                            WHERE g.run_id=? AND g.graph_version=? ORDER BY g.ordinal''',
                            (binding['run_id'], child['graph_version'])):
                            task = dict(item)
                            task['task_id'] = 'child_' + task['task_id']
                            tasks.append(task)
                # event_seq is a database-wide sequence, so parent and child
                # can share one stable cursor without dropping interleaved events.
                records = connection.execute('''SELECT run_id,event_seq,kind,payload FROM collaboration_events
                    WHERE run_id IN (?,?) AND event_seq>? ORDER BY event_seq LIMIT ?''',
                    (selected_run, binding['run_id'], after, limit + 1)).fetchall()
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error):
                return {'available': False, 'events': [], 'next_cursor': after}
            raise WorkspaceError('协作进度暂时不可用。', 503) from None
        if selected_run != binding['run_id']:
            request = database.collaboration_request(sid, request_id)
            try:
                valid_request = (request is not None and
                    request_fingerprint(request['message'], request['report'], request['comparison_patient_ids']) ==
                    binding['request_digest'] and
                    (not scope.comparison_patients or scope.comparison_patients == request['comparison_patient_ids']))
            except (KeyError, TypeError, ValueError):
                valid_request = False
            if not valid_request:
                raise WorkspaceError('协作记录的患者范围无法验证。', 403)
            for other in scope.comparison_patients:
                self.workspace.authorize_forms(session, other)
            if scope.cohort_binding is not None:
                form_keys = {key for definition in definitions for key in definition.get('form_keys', [])}
                for form_key in form_keys or {None}:
                    multi_patient.reauthorize_cohort(self.workspace, session, {
                        'source_id': scope.source_id, 'project_id': scope.project_id,
                        'roster_hash': scope.cohort_binding, 'form_key': form_key})
        allowed_payload = {'task_id', 'task_version', 'status', 'reason', 'task_count', 'graph_version'}
        events = []
        for item in records[:limit]:
            payload = json.loads(item['payload'])
            if (selected_run != binding['run_id'] and item['run_id'] == binding['run_id'] and
                    isinstance(payload.get('task_id'), str)):
                payload['task_id'] = 'child_' + payload['task_id']
            events.append({'event_seq': item['event_seq'], 'kind': item['kind'],
                           'payload': {key: value for key, value in payload.items() if key in allowed_payload}})
        return {'available': True, 'run_id': selected_run, 'status': row['state'],
                'graph_version': row['graph_version'], 'tasks': tasks, 'events': events,
                'next_cursor': events[-1]['event_seq'] if events else after, 'has_more': len(records) > limit}

    def _view(self, session, patient_id, sid, row):
        state = row['state']
        turn = {'request_id': row['request_id'], 'status': state, 'resumable': bool(row.get('resumable'))}
        if state == 'completed' and row['response']:
            self.workspace.authorize_forms(session, patient_id)
            self.chat.authorize_turn(session, sid, row['request_id'])
            result = json.loads(row['response'])
            reply = result['reply']
            if reply.get('report_ref'):
                self.chat.report_data(session, patient_id, reply['report_ref'], self.chat.store.database().get_messages(sid))
            if reply.get('analysis_job_id'):
                self.chat.analyses.job(session, patient_id, reply['analysis_job_id'])
            return {'turn': turn, **result}
        text = {'accepted': '已接收，等待处理…', 'waiting_model': '正在等待模型资源…',
                'running': '正在分析…', 'interrupted': '请求已中断，请重新发送。',
                'failed': row.get('error') or '请求未完成，请重试。'}.get(state, '正在处理…')
        return {'turn': turn, 'reply': {'kind': 'assistant', 'text': text, 'sources': []}}
