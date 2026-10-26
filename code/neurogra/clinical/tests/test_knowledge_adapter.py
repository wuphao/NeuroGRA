"""Literal patient fact and conditional evidence boundaries of the new SDK bridge."""
import unittest
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
