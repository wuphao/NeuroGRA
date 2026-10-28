"""Independent durable worker. No model work is performed in the web process."""
import os
import sqlite3
import threading
import time
import uuid

from neurogra_common.llm_log import configure_llm_log

from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.storage import BudgetExceeded

from .application import Application
from .control import execution_control
from .locking import ResourceBusy, exclusive


class Worker:
    def __init__(self, root, executor=None):
        self.app = Application(root)
        self.store = self.app.store
        self.identity = uuid.uuid4().hex
        self.executor = executor or self.execute

    def run_once(self):
        self.store.worker_seen(self.identity, os.getpid(), 'idle')
        self.store.interrupt_expired()
        attempt = self.store.claim(self.identity)
        if attempt is None:
            return False
        self.store.worker_seen(self.identity, os.getpid(), 'working')
        attempt_id, job_id = attempt['attempt_id'], attempt['job_id']
        job_config = ClinicalConfig.model_validate_json(self.store.job(job_id)['config'])
        deadline = attempt['started'] + job_config.budget.seconds
        stop, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not stop.wait(5):
                try:
                    self.store.heartbeat(attempt_id, self.identity)
                    self.store.worker_seen(self.identity, os.getpid(), 'working')
                except Exception:
                    lost.set()
                    return

        def check():
            row = self.store.attempt(attempt_id)
            if lost.is_set() or row['status'] != 'running' or row['worker'] != self.identity:
                raise BudgetExceeded('platform_ownership_lost')
            if row['cancel_requested']:
                raise BudgetExceeded('platform_cancelled')
            if time.time() >= deadline:
                raise BudgetExceeded('platform_deadline')

        def event(kind, payload):
            # Progress is small and separate from complete engine audit records.
            self.store.event(attempt_id, kind, {k: v for k, v in payload.items() if k in {
                'task_id', 'batch_index', 'status', 'batches', 'observations', 'role', 'round', 'stop_reason'}})

        thread = threading.Thread(target=heartbeat)
        thread.start()
        try:
            with exclusive(self.app.root, 'job_' + job_id):
                self.store.event(attempt_id, 'waiting_for_model_resource', {})
                while True:
                    check()
                    try:
                        with exclusive(self.app.root, 'model_resource'):
                            with execution_control(check, event):
                                outcome = self.executor(attempt, check)
                            check()
                            terminal = outcome.pop('_job_status', 'succeeded')
                            self.store.finish(attempt_id, self.identity, terminal, result=outcome)
                        break
                    except ResourceBusy:
                        time.sleep(.25)
        except BudgetExceeded as exc:
            self._finish(attempt_id, 'cancelled' if str(exc) == 'platform_cancelled' else 'interrupted', error=str(exc))
        except Exception as exc:
            # Detailed exceptions stay in local engine logs, not public job errors.
            self._finish(attempt_id, 'failed', error=type(exc).__name__)
        finally:
            stop.set()
            thread.join(timeout=10)
            configure_llm_log(None)
            self.store.worker_seen(self.identity, os.getpid(), 'idle')
        return True

    def _finish(self, attempt_id, status, **kwargs):
        if self.store.attempt(attempt_id)['status'] == 'running':
            self.store.finish(attempt_id, self.identity, status, **kwargs)

    def execute(self, attempt, check):
        from neurogra.clinical.service import create_context, prepare_run
        from neurogra.clinical.workflow import continue_run, resume_run
        job = self.store.job(attempt['job_id'])
        config = ClinicalConfig.model_validate_json(job['config'])
        directory = self.app.root / 'data/platform/runs' / attempt['attempt_id']
        config = config.model_copy(update={'output_root': directory / 'work', 'report_root': directory / 'reports'})
        directory.mkdir(parents=True, exist_ok=True)
        configure_llm_log(directory / 'llm_calls.jsonl', full_content=config.llm_debug_logging,
            max_bytes=2 * 1024 * 1024, backup_count=3)
        check()
        if job['kind'] == 'rwe_import':
            return self.import_rwe(job, config, check)
        if job['kind'] in {'knowledge_build', 'document_preview'}:
            return self.build_knowledge(job, config, attempt, check)
        if job['kind'] != 'analysis':
            raise ValueError('unsupported_job_kind')
        if attempt['mode'] == 'resume':
            run_id = attempt['resume_run_id']
            self.store.bind_run(attempt['attempt_id'], self.identity, run_id)
            result = resume_run(run_id, config)
        else:
            raw = self.app.patient(job['version_id'])
            remaining = max(1, attempt['started'] + config.budget.seconds - time.time())
            config = config.model_copy(update={'budget': config.budget.model_copy(update={'seconds': remaining})})
            engine_store, run_id = create_context(config)
            self.store.bind_run(attempt['attempt_id'], self.identity, run_id)
            self.store.event(attempt['attempt_id'], 'preparing', {'run_id': run_id})
            with engine_store.workflow_lock(run_id):
                engine_store.save(run_id, 'patient_input', 'main', {'raw': raw, 'use_model': True, 'query': None})
                preparation = prepare_run(raw, config, engine_store, run_id)
            check()
            result = continue_run(preparation, config)
        check()
        self.app.register_artifacts(attempt['attempt_id'], result.report_paths)
        self.store.event(attempt['attempt_id'], 'report_ready', {'status': result.status})
        return {'run_id': result.run_id, 'execution_status': result.status, 'stop_reason': result.stop_reason}

    def import_rwe(self, job, config, check):
        import json

        from neurogra.clinical.rwe import export_patient

        from .knowledge import rwe_configuration
        payload = json.loads(job['payload'])
        check()
        self.app.store.event(job['attempts'][-1]['attempt_id'], 'rwe_importing', {})
        raw, export, source = export_patient(payload['patient_number'], config, rwe_configuration(config))
        check()
        case = self.app.import_case(raw, source={'kind': 'rwe', 'export_id': export['export_id'],
            'source_path': self.app.inside(source).relative_to(self.app.root).as_posix(),
            'issues': export['issues']})
        self.app.register_artifacts(job['attempts'][-1]['attempt_id'], {'source_json': source})
        return {'case_id': case['case_id'], 'version_id': case['version_id'],
                'execution_status': 'partial' if export['issues'] else 'completed',
                'import_issues': export['issues']}

    def build_knowledge(self, job, config, attempt, check):
        import json

        from neurogra_kg.construction.parsing import DocumentParser
        from neurogra_kg.runtime import Budget

        from .knowledge import KnowledgeManager, kb_service
        payload = json.loads(job['payload'])
        documents = KnowledgeManager(self.app).inputs(payload['document_ids'])
        deadline = attempt['started'] + config.budget.seconds
        def remaining():
            return max(1, deadline - time.time())
        budget = Budget(remaining(), max_llm_calls=config.budget.llm, max_embedding_calls=config.budget.embedding)
        rows, accepted_kb, completed_builds = [], [], []
        kb = kb_service(config) if payload['kb'] and job['kind'] == 'knowledge_build' else None
        graph = None
        if payload['kg'] and job['kind'] == 'knowledge_build':
            from neurogra_kg.api import KnowledgeGraphService

            from neurogra.clinical.knowledge_adapter import settings
            graph = KnowledgeGraphService(settings(config))
        try:
            if graph:
                try:
                    graph._check_runtime()
                except ValueError:
                    from neurogra_kg.diagnostics import probe
                    check()
                    self.store.event(attempt['attempt_id'], 'knowledge_runtime_probe', {})
                    if not probe(graph.config, budget=budget)['ready']:
                        raise ValueError('knowledge_runtime_probe_failed')
            for index, document in enumerate(documents):
                check()
                self.store.event(attempt['attempt_id'], 'document_processing', {'index': index + 1, 'total': len(documents)})
                row = {'name': document.title, 'kb': 'disabled', 'kg': 'disabled'}
                rows.append(row)
                if job['kind'] == 'document_preview':
                    try:
                        parsed = DocumentParser(self.app.root / 'data/platform/document-previews').parse(document)
                        usable = [b for b in parsed.blocks if b.text.strip()]
                        row.update(status='needs_review' if parsed.issues else 'parsed',
                            issues=parsed.issues, block_count=len(usable),
                            preview=[{'text': b.text[:500], 'page': b.page if b.page_kind != 'unavailable' else None,
                                      'locator': b.source_locator} for b in usable[:12]])
                        if not usable:
                            row['status'] = 'failed'
                    except (OSError, ValueError) as exc:
                        row.update(status='failed', error=type(exc).__name__)
                    continue
                if kb:
                    try:
                        staged = kb.publish([document], vector=False, activate=False)
                        row.update(kb='parsed', blocks=staged['resource_count'])
                        accepted_kb.append(document)
                    except (OSError, ValueError) as exc:
                        row.update(kb='failed', kb_error=type(exc).__name__)
                check()
                if graph:
                    from neurogra_kg.contracts.domain import BuildRequest
                    try:
                        built = graph.build(BuildRequest(documents=[document], max_seconds=remaining(),
                            max_llm_calls=max(1, config.budget.llm), max_embedding_calls=max(1, config.budget.embedding)), budget=budget)
                        row.update(kg=built.status, build_id=built.build_id, issues=built.issues)
                        if built.status == 'completed':
                            completed_builds.append(built.build_id)
                    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                        check()
                        row.update(kg='failed', kg_error=type(exc).__name__)
            check()
            result = {'documents': rows, 'activated': False}
            if job['kind'] == 'document_preview':
                any_valid = any(r.get('status') != 'failed' for r in rows)
                result['execution_status'] = 'partial' if any(r.get('status') != 'parsed' for r in rows) else 'completed'
                result['_job_status'] = 'succeeded' if any_valid else 'failed'
                return result
            if accepted_kb:
                try:
                    manifest = kb.publish(accepted_kb, vector=payload['vector'], activate=False,
                        budget=budget)
                    result['kb_release'] = manifest['release_id']
                    for row in rows:
                        if row['kb'] == 'parsed':
                            row['kb'] = 'candidate_ready'
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    check()
                    result['kb_publication_error'] = type(exc).__name__
            check()
            if completed_builds:
                try:
                    manifest = graph.publish_builds(completed_builds, max_seconds=remaining(),
                        max_embedding_calls=max(1, config.budget.embedding), activate=False, budget=budget)
                    result['kg_release'] = manifest['release_id']
                    for row in rows:
                        if row['kg'] == 'completed':
                            row['kg'] = 'candidate_ready'
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    check()
                    result['kg_publication_error'] = type(exc).__name__
            failed = any(r[key] not in {'disabled', 'candidate_ready'} for r in rows for key in ('kb', 'kg'))
            result['execution_status'] = 'partial' if failed else 'completed'
            result['usage'] = {'llm_calls': budget.llm_calls, 'embedding_calls': budget.embedding_calls}
            result['_job_status'] = 'succeeded' if result.get('kb_release') or result.get('kg_release') else 'failed'
            return result
        finally:
            if graph:
                graph.close()

    def serve(self):
        try:
            while not self.store.worker_should_stop(self.identity):
                try:
                    worked = self.run_once()
                except sqlite3.OperationalError as exc:
                    if 'locked' not in str(exc).lower():
                        raise
                    # A consistent backup may briefly fence metadata writers.
                    # An idle worker must survive this maintenance window.
                    worked = False
                if not worked:
                    time.sleep(1)
        except KeyboardInterrupt:
            return
        finally:
            self.store.worker_seen(self.identity, os.getpid(), 'stopped')
