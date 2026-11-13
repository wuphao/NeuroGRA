"""Question-level journeys through real chat/storage/tools with synthetic model outputs.

Separate from routing probes: assertions check returned facts, tool dispatch,
report idempotency and persisted replies. No clinical efficacy is measured.
"""
import argparse
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from neurogra.platform.application import Application
from neurogra.platform.configuration import resolve_config
from neurogra.platform.patient_agents import DialoguePlan
from neurogra.platform.patient_chat import PatientChat
from neurogra.platform.tests.test_multi_patient import SyntheticWorkspace
from neurogra.platform.tests.test_patient_collaboration_integration import PatientCollaborationIntegrationTests
from neurogra.platform.tests.test_report_followup import ReportFollowupTests
from neurogra.platform.report_followup import FollowupDecision


QUESTIONS = [
    ('fact', '请核对患者 MoCA 记录里的总分'),
    ('fact', '列出当前患者 MoCA 的原始数值'),
    ('longitudinal', '比较该患者最近两次 MoCA 记录'),
    ('longitudinal', '查看该患者历次 MoCA 总分趋势'),
    ('focused', '请解读这位患者的 MoCA 量表结果'),
    ('focused', '无需完整报告，分析患者的 MoCA 结果'),
    ('report', '请出具这个患者的完整分析报告'),
    ('report', '对这位患者进行整体评估'),
    ('followup', '解释这份报告结论引用的依据'),
    ('followup', '核对已选报告的原始结论'),
    ('comparison', '对比两名患者的 MoCA 总分记录'),
    ('comparison', '这两位的 MoCA 评分差异是什么'),
    ('cohort', '统计本项目患者 MoCA 总分低于 26 的比例'),
    ('cohort', '统计所有患者 MoCA 总分的平均值'),
    ('mixed', '解读患者的 MoCA 结果，并出具完整分析报告'),
    ('mixed', '评估患者的 MoCA 结果，同时生成完整分析报告'),
]


def execute(category, question):
    if category in {'fact', 'focused', 'report', 'mixed', 'longitudinal'}:
        fixture = PatientCollaborationIntegrationTests()
        fixture.setUp()
        try:
            plan = DialoguePlan(patient_specific=True, forms=['moca'], roles=['cognition'], knowledge=False)
            with patch('neurogra.platform.patient_agents.ModelGateway.generate_structured', return_value=plan), \
                 patch('neurogra.platform.patient_agents.Executor.execute_tasks', new_callable=AsyncMock,
                       side_effect=fixture.specialist) as specialist, \
                 patch.object(fixture.chat.analyses, 'report_status', return_value={'status': 'missing'}), \
                 patch.object(fixture.chat.analyses, 'submit', return_value={'job_id': 'synthetic-job', 'status': 'queued'}) as submit:
                body = {'message': question, 'request_id': 'acceptance', 'capabilities': ['interactions_v1']}
                reply = fixture.chat.send(fixture.session, '1', fixture.sid, body, lambda: None)['reply']
                again = fixture.chat.send(fixture.session, '1', fixture.sid, body, lambda: None)['reply']
                assert again == reply, 'same request changed persisted result'
                if category in {'report', 'mixed'}:
                    assert reply.get('analysis_job_id') == 'synthetic-job', 'report goal lost'
                    submit.assert_called_once()
                else:
                    submit.assert_not_called()
                if category in {'fact', 'focused', 'mixed'}:
                    assert '23' in reply['text'], 'patient fact missing'
                    if category != 'fact':
                        specialist.assert_called_once()
                if category == 'longitudinal':
                    assert reply['result_status'] == 'partial', 'single visit became complete trajectory'
                messages = fixture.chat.store.database().get_messages(fixture.sid)
                assert sum(row['role'] == 'assistant' for row in messages) == 1, 'duplicate reply'
                return {'text': reply['text'], 'status': reply.get('result_status'),
                        'report_dispatches': submit.call_count, 'specialist_batches': specialist.call_count}
        finally:
            fixture.doCleanups()
    if category == 'followup':
        fixture = ReportFollowupTests()
        fixture.setUp()
        try:
            fixture.request['message'] = question
            with patch('neurogra.clinical.llm.ModelGateway.generate_structured',
                       return_value=FollowupDecision(route='answer_from_report', report_refs=['original'])):
                reply = fixture.send()
            assert '已有报告结论' in reply['text'], 'selected report conclusion missing'
            return {'text': reply['text'], 'selected_report_verified': True}
        finally:
            fixture.doCleanups()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        app = Application(root, resolve_config(root=root, profile='patient-only'))
        workspace = SyntheticWorkspace()
        analyses = Mock()
        chat = PatientChat(app, workspace, analyses)
        session = {'account': 'synthetic'}
        sid = chat.create(session, '1')['id']
        body = {'message': question, 'request_id': 'acceptance',
                'comparison_patient_ids': ['2'] if category == 'comparison' else []}
        reply = chat.send(session, '1', sid, body, lambda: None)['reply']
        if category == 'comparison':
            assert 'P1' in reply['text'] and 'P2' in reply['text'], 'comparison subject lost'
            assert '22' in reply['text'] and '28' in reply['text'], 'patient values lost'
        else:
            expected = '50.00%' if '比例' in question else '25'
            assert expected in reply['text'], 'cohort calculation wrong'
        analyses.submit.assert_not_called()
        return {'text': reply['text'], 'status': reply.get('result_status')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows = []
    for index, (category, question) in enumerate(QUESTIONS, 1):
        started = time.monotonic()
        row = {'id': f'J{index:02d}', 'category': category, 'question': question}
        try:
            row.update(passed=True, actual=execute(category, question))
        except Exception as error:
            row.update(passed=False, error=type(error).__name__, detail=str(error))
        row['seconds'] = round(time.monotonic() - started, 3)
        rows.append(row)
    result = {'scope': 'Synthetic question-level integration; model outputs substituted, real tools and persistence',
              'total': len(rows), 'passed': sum(row['passed'] for row in rows), 'cases': rows}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'total': result['total'], 'passed': result['passed'],
                      'failures': [row for row in rows if not row['passed']]}, ensure_ascii=False))
    return result['passed'] != result['total']


if __name__ == '__main__':
    raise SystemExit(main())
