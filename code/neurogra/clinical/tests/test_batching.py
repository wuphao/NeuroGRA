import asyncio
from neurogra.clinical.orchestration import TaskContext
import unittest
from types import SimpleNamespace

from neurogra.clinical.batching import bounded_batches, observation_view
from neurogra.clinical.history import HistoryAgent, GroundedDraft
from neurogra.clinical.schemas import AgentTask, CaseSnapshot, Observation, EvidenceBundle


class BatchingTests(unittest.TestCase):
    def test_previous_result_is_scoped_and_mapped_for_each_large_batch(self):
        from neurogra.clinical.schemas import AgentResult, Claim
        facts = [Observation(observation_id=f'obs{i}', name=f'field{i}', domains=['history'],
                             quote=f'raw{i}', value=i, status='observed', source_refs=[]) for i in range(70)]
        previous = AgentResult(result_id='code_previous', task_id='code_task', case_version=1, status='completed',
            claims=[Claim(claim_id=f'code_claim{i}', text=f'raw{i}', kind='description', level='history',
                          observation_ids=[f'obs{i}'], strength='descriptive') for i in (0, 69)])
        views = []
        class Runtime:
            def record_analysis_batch(self, *args): pass
            async def generate_structured(self, system, data, schema):
                views.append(data['previous_result'])
                return GroundedDraft(claims=[], uncertainties=[])
        snapshot = CaseSnapshot(case_id='code_case', patient_id='p', raw_input_hash='h', records=[], observations=facts)
        task = AgentTask(task_id='code_new_task', agent_type='history', case_version=1,
                         input_refs=[row.observation_id for row in facts], questions=['更新'], why='新增资料', permitted_tools=[])
        asyncio.run(HistoryAgent().analyze(task, TaskContext(snapshot, Runtime(), previous_result=previous)))
        self.assertEqual(len(views), 2)
        self.assertEqual([view['claims'][0]['text'] for view in views], ['raw0', 'raw69'])
        self.assertTrue(all('claim_id' not in view['claims'][0] for view in views))
        self.assertEqual(views[0]['claims'][0]['observation_ids'], ['O1'])
    def test_every_observation_enters_exactly_one_batch(self):
        facts = [Observation(observation_id=f'obs{i}', name=f'字段{i}', domains=['history'],
                             quote=f'记录{i}', value=i, status='observed', source_refs=[])
                 for i in range(83)]
        batches = list(bounded_batches(facts, observation_view, max_chars=3000, max_items=20))
        self.assertEqual([o.observation_id for batch in batches for o in batch], [o.observation_id for o in facts])
        self.assertTrue(all(len(batch) <= 20 for batch in batches))

    def test_specialist_large_input_maps_aliases_without_losing_coverage(self):
        facts = [Observation(observation_id=f'obs{i}', name=f'field{i}', domains=['history'],
                             quote=f'raw{i}', value=i, status='observed', source_refs=[]) for i in range(83)]
        seen = []
        class Tools:
            def record_analysis_batch(self, *args):
                pass
            async def search_knowledge(self, request):
                return EvidenceBundle(bundle_id='b', request_id=request.request_id,
                                      status='empty', backend_status={'bm25': 'empty'})
            async def generate_structured(self, system, data, schema):
                seen.extend(o['quote'] for o in data['observations'])
                return GroundedDraft(claims=[{'text': data['observations'][0]['quote'],
                    'kind': 'description', 'level': 'history', 'observation_ids': ['O1'],
                    'strength': 'descriptive'}], uncertainties=[])
        snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[], observations=facts)
        task = AgentTask(task_id='t', agent_type='history', case_version=1,
                         input_refs=[o.observation_id for o in facts], questions=['核对实际资料'],
                         why='资料存在', permitted_tools=[])
        result = asyncio.run(HistoryAgent().analyze(task, TaskContext(snapshot=snapshot, runtime=Tools())))
        self.assertEqual(seen, [o.quote for o in facts])
        self.assertEqual(result.used_refs, task.input_refs)
        self.assertEqual(len({c.claim_id for c in result.claims}), 3)
        self.assertTrue(all(c.observation_ids[0].startswith('obs') for c in result.claims))
        self.assertEqual(result.status, 'partial')  # No knowledge evidence returned.
