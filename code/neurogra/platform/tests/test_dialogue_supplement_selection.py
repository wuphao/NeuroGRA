import unittest

from neurogra.clinical.schemas import AgentResult, AgentTask, CaseSnapshot, Observation, RequestContent, TaskPlan
from neurogra.platform.dialogue_supplement import select_specialist_followups


class SpecialistFollowupSelectionTests(unittest.TestCase):
    def setUp(self):
        self.observation = Observation(observation_id='own', name='总分', domains=['cognition'],
                                       value=23, quote='23', source_refs=['own-record'], status='observed')
        self.snapshot = CaseSnapshot(case_id='case', patient_id='synthetic', raw_input_hash='hash',
                                     records=[], observations=[self.observation])
        self.task = AgentTask(task_id='task', agent_type='cognition', case_version=1, input_refs=['own'],
                              questions=['解释量表'], why='测试', permitted_tools=[])
        self.plan = TaskPlan(tasks=[self.task])
        self.evidence = {'task': [{'evidence_id': 'e1', 'span_id': 'span1'}]}

    def request(self, kind='patient_data', **kwargs):
        return RequestContent(type=kind, question='核实资料', reason='合成测试', **kwargs)

    def select(self, requests, **kwargs):
        result = AgentResult(result_id='result', task_id='task', case_version=1,
                             status='completed', requests=requests)
        return select_specialist_followups(self.plan, [result], self.snapshot, self.evidence,
            {'moca', 'history'}, {'moca'}, knowledge_allowed=True, **kwargs)

    def test_only_valid_new_forms_and_scoped_local_knowledge_are_selected(self):
        patient = self.request(form_key='history', source_refs=['own-record'], priority='high')
        knowledge = self.request('knowledge', source_refs=['e1'], priority='low')
        invalid = [self.request(form_key='foreign', source_refs=['own']),
                   self.request(form_key='moca', source_refs=['own']),
                   self.request(form_key=None, source_refs=['own']),
                   self.request(form_key='history', source_refs=['foreign']),
                   self.request(form_key='history', source_refs=['own'], cursor='external'),
                   self.request('source_context', source_refs=['own']),
                   RequestContent(type='knowledge', question='访问 https://example.invalid', reason='测试',
                                  source_refs=['own']),
                   RequestContent(type='knowledge', question='患者总分为 99 请检索', reason='测试',
                                  source_refs=['own'])]
        selected = self.select([knowledge, patient, patient, *invalid], maximum=1)
        self.assertEqual([item.request for item in selected['selected']], [patient])
        self.assertEqual(selected['deferred'], 1)
        self.assertEqual(selected['unsupported'], len(invalid))
        repeated = self.select([knowledge, patient, *invalid], maximum=2)
        self.assertEqual([item.request_id for item in selected['selected']],
                         [item.request_id for item in repeated['selected'][:1]])

    def test_disabled_knowledge_and_mismatched_result_do_not_pass(self):
        request = self.request('knowledge', source_refs=['e1'])
        result = AgentResult(result_id='result', task_id='task', case_version=1,
                             status='completed', requests=[request])
        selected = select_specialist_followups(self.plan, [result], self.snapshot, self.evidence,
            {'moca', 'history'}, {'moca'}, knowledge_allowed=False)
        self.assertEqual(selected['selected'], [])
        self.assertEqual(selected['unsupported'], 1)
        wrong = result.model_copy(update={'task_id': 'another-patient-task'})
        with self.assertRaisesRegex(ValueError, 'specialist_followup_binding_mismatch'):
            select_specialist_followups(self.plan, [wrong], self.snapshot, self.evidence,
                {'moca', 'history'}, {'moca'}, knowledge_allowed=True)

    def test_missing_form_key_is_inferred_only_from_one_explicit_form_name(self):
        request = RequestContent(type='patient_data', question='请核查患者的 MMSE 记录', reason='补充量表',
                                 source_refs=['own'])
        result = AgentResult(result_id='result', task_id='task', case_version=1,
                             status='completed', requests=[request])
        available = {'moca': 'MoCA量表', 'mmse': 'MMSE量表'}
        chosen = select_specialist_followups(self.plan, [result], self.snapshot, self.evidence,
            available, {'moca'}, knowledge_allowed=True)
        self.assertEqual(chosen['selected'][0].request.form_key, 'mmse')
        self.assertEqual(request.form_key, None)
        ambiguous = request.model_copy(update={'question': '核查 MoCA 和 MMSE 记录'})
        result = result.model_copy(update={'requests': [ambiguous]})
        chosen = select_specialist_followups(self.plan, [result], self.snapshot, self.evidence,
            available, {'moca'}, knowledge_allowed=True)
        self.assertEqual(chosen['selected'], [])
        self.assertEqual(chosen['unsupported'], 1)


if __name__ == '__main__':
    unittest.main()
