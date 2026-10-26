"""Independent durable worker. No model work is performed in the web process."""
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
        self.store.interrupt_expired()
        attempt = self.store.claim(self.identity)
        if attempt is None:
            return False
        attempt_id, job_id = attempt['attempt_id'], attempt['job_id']
        stop, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not stop.wait(5):
                try:
                    self.store.heartbeat(attempt_id, self.identity)
                except Exception:
                    lost.set()
                    return

        def check():
            row = self.store.attempt(attempt_id)
            if lost.is_set() or row['status'] != 'running' or row['worker'] != self.identity:
                raise BudgetExceeded('platform_ownership_lost')
            if row['cancel_requested']:
                raise BudgetExceeded('platform_cancelled')

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
                            self.store.finish(attempt_id, self.identity, 'succeeded', result=outcome)
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
        configure_llm_log(directory / 'llm_calls.jsonl')
        check()
        if job['kind'] != 'analysis':
            raise ValueError('unsupported_job_kind')
        if attempt['mode'] == 'resume':
            run_id = attempt['resume_run_id']
            self.store.bind_run(attempt['attempt_id'], self.identity, run_id)
            result = resume_run(run_id, config)
        else:
            raw = self.app.patient(job['version_id'])
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

    def serve(self):
        try:
            while True:
                if not self.run_once():
                    time.sleep(1)
        except KeyboardInterrupt:
            return
