"""Exercise supported capability combinations against real sync/async services."""
import argparse
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

from neurogra.platform.patient_agents import DialoguePlan
from neurogra.platform.tests.test_async_chat import AsyncChatTests
from neurogra.platform.tests.test_patient_collaboration_integration import PatientCollaborationIntegrationTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    combinations = [[], ['async_turns_v1'], ['async_turns_v1', 'interactions_v1'],
                    ['async_turns_v1', 'interactions_v1', 'collaboration_progress_v1'],
                    ['async_turns_v1', 'future_unknown_capability']]
    results = []
    original_thread = threading.Thread
    for capabilities in combinations:
        fixture = AsyncChatTests()
        fixture.setUp()
        workers = []

        def track_thread(*positional, **named):
            worker = original_thread(*positional, **named)
            workers.append(worker)
            return worker

        try:
            plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
            with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
                 patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                       side_effect=PatientCollaborationIntegrationTests.specialist) as specialist, \
                 patch('neurogra.platform.async_chat.threading.Thread', side_effect=track_thread):
                body = {'message': '解读该患者的 MoCA 结果', 'request_id': 'compatibility',
                        'capabilities': capabilities}
                if 'async_turns_v1' in capabilities:
                    fixture.manager.start(fixture.cookie, fixture.session, '1', fixture.sid, body)
                    fixture.wait_status('compatibility', {'completed'})
                    reply = fixture.manager.get(fixture.session, '1', fixture.sid, 'compatibility')['reply']
                    replay = fixture.manager.start(fixture.cookie, fixture.session, '1', fixture.sid, body)['reply']
                else:
                    reply = fixture.chat.send(fixture.session, '1', fixture.sid, body, lambda: None)['reply']
                    replay = fixture.chat.send(fixture.session, '1', fixture.sid, body, lambda: None)['reply']
                assert '23' in reply['text'] and replay['text'] == reply['text']
                assert isinstance(reply['workflow']['tasks'], list), 'legacy workflow projection changed'
                specialist.assert_called_once()
                assert sum(row['role'] == 'assistant' for row in fixture.chat.store.database().get_messages(fixture.sid)) == 1
                results.append({'capabilities': capabilities, 'passed': True,
                                'idempotent': True, 'legacy_workflow_tasks': 'list'})
        except Exception as error:
            results.append({'capabilities': capabilities, 'passed': False,
                            'error': type(error).__name__, 'detail': str(error)})
        finally:
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=5)
            fixture.doCleanups()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'scope': 'Service protocol integration, not a historical browser binary test',
                                 'combinations': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False))
    return not all(row['passed'] for row in results)


if __name__ == '__main__':
    raise SystemExit(main())
