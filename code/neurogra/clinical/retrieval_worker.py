"""Disposable read-only retrieval backend; parent alone commits clinical evidence."""
import json
import os
import subprocess
import sys
import time


def run_json_process(command, payload, deadline):
    if time.time() >= deadline:
        raise TimeoutError('retrieval_deadline')
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError('retrieval_deadline')
        output, _ = proc.communicate(json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=remaining)
        if time.time() >= deadline:
            raise TimeoutError('retrieval_deadline')
        if proc.returncode:
            raise ValueError('retrieval_worker_failed')
        return json.loads(output)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError('retrieval_deadline') from exc
    finally:
        # This worker never launches descendants. Kill and reap before returning to the executor.
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def main():
    from .config import ClinicalConfig
    from .retrieval import KnowledgeService
    from .schemas import RetrievalRequest
    try:
        data = json.loads(sys.stdin.buffer.read())
        service = KnowledgeService.__new__(KnowledgeService)
        service.config = ClinicalConfig.model_validate(data['config'])
        service.release_id = data['release_id']
        service.vector_binding = data['vector_binding']
        status, items, metadata = service._backend(RetrievalRequest.model_validate(data['request']),
                                                   data['backend'], data['deadline'])
        result = {'status': status, 'items': [i.model_dump(mode='json') for i in items], 'metadata': metadata}
    except Exception as exc:
        result = {'error': type(exc).__name__}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
