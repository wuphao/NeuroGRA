"""Experiment isolation, source-preserving projection and diagnosis endpoint contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from neurogra.benchmarks.ablation import group_config, prediction, prepare_case, SingleReport, single_llm
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.diagnostic_outcomes import ClassifiedLayerDraft, validate_draft
from neurogra.clinical.schemas import DiagnosisLayer, DiagnosticOutcome, FinalReport
from neurogra.clinical.triple_knowledge import project_statements, rank_triples, triple_evidence
from neurogra_kg.contracts.domain import KnowledgeStatement, Entity, SourceRef, Block


def statement(id='s1', quote='Source A', block='b1'):
    return KnowledgeStatement(statement_id=id,
        subject=Entity(entity_id='h', name='Alpha', entity_type='other'), predicate='supports',
        object=Entity(entity_id='t', name='Beta', entity_type='other'),
        assertion_text='Do not index private assertion text', purposes=['diagnostic_assessment'],
        source_refs=[SourceRef(block_id=block, start=0, end=len(quote), quote=quote)],
        status='source_checked')


class AblationTests(unittest.TestCase):
    def test_projection_merges_edges_but_preserves_every_source_and_does_not_index_conditions(self):
        a, b = statement(), statement('s2', 'Source B', 'b2')
        b.condition_status = 'explicit_unrestricted'
        edges = project_statements([a, b])
        self.assertEqual(len(edges), 1)
        self.assertEqual(set(edges[0]['statement_ids']), {'s1', 's2'})
        self.assertEqual(len(edges[0]['source_refs']), 2)
        self.assertNotIn('condition_status', edges[0])
        self.assertNotIn('purposes', edges[0])
        self.assertEqual(rank_triples(edges, 'private assertion', 'bm25', 5), [])
        self.assertEqual(rank_triples(edges, 'Alpha', 'bm25', 5), [edges[0]['id']])

    def test_rejected_graph_statements_are_not_resurrected(self):
        a = statement()
        a.status = 'rejected'
        self.assertEqual(project_statements([a]), [])

    def test_original_conditions_in_quotes_are_preserved_without_claiming_applicability(self):
        a = statement(quote='Only under method M, Alpha supports Beta')
        edges = project_statements([a])
        block = Block(block_id='b1', document_id='d', order=0, page=1, kind='paragraph', text=a.source_refs[0].quote)
        items = triple_evidence(edges, [edges[0]['id']], {'b1': block}, {'d': {'title': 'source'}}, 'release', 'graph')
        self.assertEqual(items[0].quote, a.source_refs[0].quote)
        self.assertEqual(items[0].applicability, 'unknown')
        self.assertEqual(items[0].condition_assessment, [])
        self.assertEqual(items[0].graph_triples, [['Alpha', 'supports', 'Beta']])
        block.text = 'corrupted'
        with self.assertRaisesRegex(ValueError, 'quote_mismatch'):
            triple_evidence(edges, [edges[0]['id']], {'b1': block}, {'d': {'title': 'source'}}, 'release', 'graph')

    def test_all_roles_disable_graph_and_configs_are_independent(self):
        base = ClinicalConfig()
        no = group_config(base, 'no_kg', Path('output/no'))
        full = group_config(base, 'full', Path('output/full'))
        self.assertFalse(no.knowledge_enabled('kg'))
        self.assertTrue(no.knowledge_enabled('kb'))
        self.assertTrue(full.knowledge_enabled('kg'))
        self.assertTrue(base.main_agent.use_kg)
        self.assertNotEqual(no.database, full.database)
        direct = group_config(base, 'single_llm', Path('output/direct'))
        self.assertFalse(direct.enabled_roles())
        self.assertFalse(direct.knowledge_enabled('kb'))
        ClinicalConfig.model_validate(direct.model_dump(mode='json'))

    def test_both_retrieval_entrypoints_use_projection(self):
        from neurogra.clinical.knowledge_adapter import retrieve_backend
        from neurogra.clinical.schemas import RetrievalRequest
        config = group_config(ClinicalConfig(), 'plain_triples', Path('output/triples'))
        request = RetrievalRequest(request_id='r', question='Alpha', purpose='diagnostic_assessment')
        with patch('neurogra.clinical.triple_knowledge.retrieve_triples', return_value=('ok', [], {})) as ordinary:
            retrieve_backend(config, 'release', request, 'graph', 10, None)
            ordinary.assert_called_once()
        from neurogra.clinical.main_tools import MainToolExecutor
        from neurogra.clinical.schemas import RequestContent
        tool = MainToolExecutor.__new__(MainToolExecutor)
        tool.config, tool.graph_release_id, tool.text, tool.text_release_id = config, 'release', None, None
        with patch('neurogra.clinical.triple_knowledge.retrieve_triples', return_value=('empty', [], {})) as ordinary:
            data = tool._knowledge(RequestContent(type='knowledge', question='Alpha', reason='test'), None, False)
            self.assertEqual(ordinary.call_count, 2)
            self.assertEqual(data['backend_status']['kg_graph'], 'empty')

    def test_explicit_class_rejects_cross_level_and_abstention_mismatch(self):
        draft = ClassifiedLayerDraft(status='working_diagnosis', primary_label='AD病因倾向',
            research_class='AD', reasoning='说明', group_ids=['G1'], evidence_ids=['K1'])
        data = {'level': 'syndrome', 'groups': [{'id': 'G1'}], 'evidence': [{'id': 'K1'}]}
        with self.assertRaisesRegex(ValueError, 'wrong_diagnostic_level'):
            validate_draft(draft, data)
        data['level'] = 'etiology'
        validate_draft(draft, data)
        draft.research_class = 'UNRESOLVED'
        with self.assertRaisesRegex(ValueError, 'status_disagree'):
            validate_draft(draft, data)

    def test_single_call_validates_structural_contradiction_inside_gateway_schema(self):
        value = {'status': 'working_diagnosis', 'primary_label': '工作判断', 'research_class': 'CN',
            'reasoning': '依据', 'group_ids': ['G1']}
        data = {'syndrome': value, 'etiology': {**value, 'research_class': 'OTHER'}}
        SingleReport.model_validate(data)
        data['syndrome'] = {**value, 'status': 'indeterminate'}
        with self.assertRaisesRegex(ValueError, 'indeterminate requires UNRESOLVED'):
            SingleReport.model_validate(data)

    def test_single_call_conflicts_reference_visible_group_ids(self):
        from neurogra.clinical.schemas import CaseSnapshot, Observation
        observations = [Observation(observation_id='o' + str(i), name='score', value=i,
            status='observed', quote=str(i), source_refs=[], domains=['cognition']) for i in (1, 2)]
        snapshot = CaseSnapshot(case_id='c', patient_id='p', raw_input_hash='h', records=[],
            observations=observations, conflict_groups=[['o1', 'o2']])
        layer = {'status': 'working_diagnosis', 'primary_label': '工作判断', 'research_class': 'CN',
            'reasoning': '依据', 'group_ids': ['G1']}
        generated = SingleReport.model_validate({'syndrome': layer, 'etiology': {**layer, 'research_class': 'OTHER'}})
        with tempfile.TemporaryDirectory() as temp, patch('neurogra.clinical.llm.ModelGateway') as gateway:
            directory = Path(temp)
            config = group_config(ClinicalConfig(), 'single_llm', directory)
            gateway.return_value.generate_structured.return_value = generated
            single_llm({}, config, directory, snapshot)
            data = gateway.return_value.generate_structured.call_args.args[1]
            self.assertEqual(data['conflict_groups'], [['G1']])

    def test_mci_with_ad_etiology_is_not_mapped_to_ad_dementia_and_rejected_labels_do_not_count(self):
        layers = [DiagnosisLayer(level='syndrome', status='conditional_diagnosis', label='MCI',
                    research_class='MCI', review_status='passed'),
                  DiagnosisLayer(level='etiology', status='conditional_diagnosis', label='AD倾向',
                    research_class='AD', review_status='passed')]
        report = FinalReport(patient_id='p', run_id='r', status='partial', summary='test',
            assessment_status='conditional', claims=[], unresolved=[], citations=[],
            diagnostic_outcome=DiagnosticOutcome(status='conditional_diagnosis', layers=layers, conclusion='test'))
        self.assertEqual(prediction(report, 'full')['cohort_class'], 'MCI')
        layers[0].research_class = 'DEMENTIA'
        self.assertEqual(prediction(report, 'full')['cohort_class'], 'AD')
        layers[1].review_status = 'rejected'
        self.assertEqual(prediction(report, 'full')['cohort_class'], 'UNRESOLVED')


if __name__ == '__main__':
    unittest.main()
