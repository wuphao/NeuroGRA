"""Literal patient fact and conditional evidence boundaries of the new SDK bridge."""
import unittest
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from neurogra.clinical.knowledge_adapter import patient_facts
from neurogra.clinical.schemas import CaseSnapshot, SourceRecord, Observation, TimeValue


class KnowledgeAdapterTests(unittest.TestCase):
    def snapshot(self, status="observed", name="年龄", value=70, quote="年龄70岁", text="年龄70岁"):
        return CaseSnapshot(case_id="c", patient_id="p", raw_input_hash="h",
            records=[SourceRecord(record_id="record", kind="field", text=text, content_hash="h")],
            observations=[Observation(observation_id="o", name=name, value=value, status=status,
                domains=["history"], source_refs=["record"], quote=quote, unit="year",
                time=TimeValue(value="2025-01-01", precision="day"))])

    def test_literal_source_and_date_are_preserved(self):
        facts, omitted = patient_facts(self.snapshot(), ["o"])
        self.assertEqual(omitted, [])
        self.assertEqual(facts[0].field, "age")
        self.assertEqual(facts[0].value, 70)
        self.assertEqual(facts[0].source_ref.quote, "年龄70岁")
        self.assertEqual(facts[0].observed_at.year, 2025)

    def test_missing_or_untraceable_records_are_not_patient_facts(self):
        for snapshot in [self.snapshot(status="not_recorded"), self.snapshot(text="未记录年龄"),
                         self.snapshot(name="未知指标"), self.snapshot(value="70")]:
            self.assertEqual(patient_facts(snapshot, ["o"])[0], [])

    def test_conflicting_observation_stays_uncertain(self):
        facts, _ = patient_facts(self.snapshot(status="conflicting"), [])
        self.assertEqual(facts[0].polarity, "uncertain")

    def test_month_precision_is_not_fabricated_as_a_day(self):
        snapshot = self.snapshot()
        snapshot.observations[0].time = TimeValue(value="2025-01", precision="month")
        facts, _ = patient_facts(snapshot, [])
        self.assertIsNone(facts[0].observed_at)

    def test_requested_fact_scope_is_respected(self):
        self.assertEqual(patient_facts(self.snapshot(), ["other"])[0], [])

    def test_chinese_fields_and_methods_use_shared_catalog(self):
        snapshot=self.snapshot(name="视幻觉",status="absent",value=False,quote="明确否认视幻觉",text="明确否认视幻觉")
        facts,omitted=patient_facts(snapshot,[])
        self.assertEqual(omitted,[])
        self.assertEqual(facts[0].field,"symptom.visual_hallucinations")
        self.assertFalse(facts[0].value)
        self.assertEqual(facts[0].source_ref.quote,"明确否认视幻觉")
        snapshot=self.snapshot(name="MMSE",value=18,quote="MMSE18分",text="MMSE18分")
        snapshot.observations[0].context['method']='简易精神状态检查'
        facts,_=patient_facts(snapshot,[])
        self.assertEqual(facts[0].method,'MMSE')

    def test_actual_local_kg_condition_reaches_diagnosis_as_unknown_or_excluded(self):
        from neurogra.clinical.config import ClinicalConfig
        from neurogra.clinical.diagnostic_outcomes import LayerDraft, LayerReview, diagnose_layer
        from neurogra.clinical.knowledge_adapter import retrieve_backend
        from neurogra.clinical.schemas import RetrievalRequest
        from neurogra.clinical.storage import RunStore
        from neurogra_kg.adapters.graph import MemoryGraphStore
        from neurogra_kg.config import GraphConfig, KGConfig
        from neurogra_kg.contracts.domain import AtomCondition
        from neurogra_kg.runtime import Budget
        from neurogra_kg.storage.registry import Registry
        from neurogra_kg.storage.releases import create_release
        from neurogra_kg.tests.test_bilingual import BilingualTests
        from neurogra_kg.tests.test_release_integrity import FixtureEmbedding

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / 'data/kg'
            data_root.mkdir(parents=True)
            statement, document = BilingualTests().data('Synthetic marker rule applies when age is at least 80.')
            condition = AtomCondition(condition_id='age-rule', field='age', operator='gte', value=80,
                unit='year', source_refs=statement.source_refs)
            statement = type(statement).model_validate({**statement.model_dump(mode='json'),
                'condition': condition.model_dump(mode='json'), 'condition_status': 'constrained'})
            with closing(Registry(data_root / 'registry.sqlite')) as registry:
                manifest = create_release(data_root, registry, 'synthetic-build', [statement], [document],
                                          FixtureEmbedding(), MemoryGraphStore(), Budget(10))
            config = ClinicalConfig(project_root=root, kg=KGConfig(root=root, graph=GraphConfig(provider='memory')))
            request = RetrievalRequest(request_id='synthetic', question='Synthetic marker rule',
                patient_fact_refs=['o'], requested_backends=['bm25'],
                reference_time=datetime(2026, 11, 1, tzinfo=timezone.utc))
            unknown = self.snapshot(status='not_recorded', value=None, quote='未记录', text='未记录')
            unknown.records.append(SourceRecord(record_id='symptom-record', kind='field',
                text='记忆下降', content_hash='synthetic'))
            unknown.observations.append(Observation(observation_id='symptom', name='记忆症状',
                domains=['history'], value='记忆下降', quote='记忆下降', source_refs=['symptom-record'], status='observed'))
            _, unknown_evidence, _ = retrieve_backend(config, manifest['release_id'], request, 'bm25', 10, unknown)
            self.assertTrue(unknown_evidence)
            self.assertTrue(all(item.applicability == 'unknown' and item.allowed_use == 'background'
                                for item in unknown_evidence))

            def draft(_system, data, schema):
                if schema is LayerDraft:
                    group = next(item['id'] for item in data['groups'] if item['name'] == '记忆症状')
                    return LayerDraft(status='working_diagnosis', primary_label='合成工作判断',
                        reasoning='仅用于测试的条件判断', group_ids=[group], evidence_ids=['K1'])
                return LayerReview(supported=True)

            store = RunStore(root / 'clinical.sqlite')
            run_id = store.create_run({}, {'llm': 10, 'seconds': 60})
            with patch('neurogra.clinical.diagnostic_outcomes.ModelGateway.generate_structured', side_effect=draft):
                layer, _ = diagnose_layer(unknown, unknown_evidence, config, store, run_id, 'syndrome')
            self.assertEqual(layer.status, 'conditional_diagnosis')
            self.assertTrue(any('适用条件' in item for item in layer.missing_conditions))

            def preconditional(_system, data, schema):
                if schema is LayerDraft:
                    group = next(item['id'] for item in data['groups'] if item['name'] == '记忆症状')
                    return LayerDraft(status='conditional_diagnosis', primary_label='合成工作判断',
                        reasoning='仅用于测试的条件判断', group_ids=[group], evidence_ids=['K1'],
                        missing_conditions=[f'另一项条件{i}' for i in range(6)])
                return LayerReview(supported=True)

            conditional_run = store.create_run({}, {'llm': 10, 'seconds': 60})
            with patch('neurogra.clinical.diagnostic_outcomes.ModelGateway.generate_structured', side_effect=preconditional):
                conditional, _ = diagnose_layer(unknown, unknown_evidence, config, store, conditional_run, 'syndrome')
            self.assertEqual(conditional.status, 'conditional_diagnosis')
            self.assertTrue(any('适用条件' in item for item in conditional.missing_conditions))
            self.assertLessEqual(len(conditional.missing_conditions), 6)
            self.assertTrue(any('未展示的条件' in item for item in conditional.missing_conditions))

            missing_only = self.snapshot(status='not_recorded', value=None, quote='未记录', text='未记录')
            missing_run = store.create_run({}, {'llm': 10, 'seconds': 60})

            def cite_missing(_system, _data, schema):
                if schema is LayerDraft:
                    return LayerDraft(status='working_diagnosis', primary_label='合成工作判断',
                        reasoning='仅用于测试的条件判断', group_ids=['G1'], evidence_ids=['K1'])
                return LayerReview(supported=True)

            with patch('neurogra.clinical.diagnostic_outcomes.ModelGateway.generate_structured',
                       side_effect=cite_missing) as missing_model:
                missing, _ = diagnose_layer(missing_only, unknown_evidence, config, store, missing_run, 'syndrome')
            self.assertNotIn(missing.status, {'working_diagnosis', 'conditional_diagnosis'})
            missing_model.assert_not_called()

            conflicted = unknown.model_copy(deep=True)
            conflicted.records.append(SourceRecord(record_id='other-record', kind='field',
                text='记忆稳定', content_hash='other'))
            conflicted.observations.append(Observation(observation_id='other-symptom', name='记忆症状',
                domains=['history'], value='记忆稳定', quote='记忆稳定', source_refs=['other-record'], status='observed'))
            conflicted.conflict_groups = [['symptom', 'other-symptom']]
            conflict_run = store.create_run({}, {'llm': 10, 'seconds': 60})
            with patch('neurogra.clinical.diagnostic_outcomes.ModelGateway.generate_structured', side_effect=preconditional):
                conflict_layer, _ = diagnose_layer(conflicted, unknown_evidence, config, store, conflict_run, 'syndrome')
            self.assertEqual(conflict_layer.status, 'conditional_diagnosis')
            self.assertTrue(any('适用条件' in item for item in conflict_layer.missing_conditions))
            self.assertTrue(any('冲突' in item for item in conflict_layer.missing_conditions))

            violated = self.snapshot(value=70)
            _, violated_evidence, _ = retrieve_backend(config, manifest['release_id'], request, 'bm25', 10, violated)
            self.assertTrue(violated_evidence)
            self.assertTrue(all(item.applicability == 'not_applicable' and item.allowed_use == 'background'
                                for item in violated_evidence))
            other_run = store.create_run({}, {'llm': 10, 'seconds': 60})
            with patch('neurogra.clinical.diagnostic_outcomes.ModelGateway.generate_structured') as model:
                layer, _ = diagnose_layer(violated, violated_evidence, config, store, other_run, 'syndrome')
            self.assertEqual(layer.status, 'indeterminate')
            model.assert_not_called()
