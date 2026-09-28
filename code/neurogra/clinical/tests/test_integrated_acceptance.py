"""S14-S16 protocol acceptance across every specialist. Model replies are test doubles."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.schemas import *
from neurogra.clinical.service import PreparationResult, create_context, prepare_patient
from neurogra.clinical.orchestration import fallback_plan
from neurogra.clinical.history import HistoryAgent, RevisionDraft
from neurogra.clinical.workflow import continue_run, resume_run, ReviewPlan, ReviewConcern, Synthesis
from neurogra.clinical.storage import RunStore
from neurogra.clinical.acceptance import audit_run
from neurogra.clinical.utils import write_json, identity


class IntegratedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=ClinicalConfig(project_root=Path(self.tmp.name))

    def preparation(self):
        store,run=create_context(self.config)
        observations=[];records=[];inventory=[]
        for role in ROLES:
            record=SourceRecord(record_id='source_'+role,kind='field',text='未提供相关信息。',content_hash=role)
            observation=Observation(observation_id='obs_'+role,name='原始项目',domains=[role],value='未提供',
                quote=record.text,status='not_recorded',source_refs=[record.record_id])
            records.append(record);observations.append(observation)
            inventory.append(InventoryItem(domain=role,availability='partial',observation_ids=[observation.observation_id]))
        snapshot=CaseSnapshot(case_id='c',patient_id='p',raw_input_hash='raw',records=records,observations=observations,inventory=inventory)
        plan=fallback_plan(snapshot)
        path=self.config.resolve(self.config.output_root)/run/'preparation.json'
        prep=PreparationResult(run_id=run,status='prepared',snapshot=snapshot,plan=plan,knowledge_release_id=None,issues=[],artifact_path=str(path))
        store.save(run,'snapshot','c',snapshot);store.save(run,'plan','main',plan);write_json(path,prep)
        return store,prep

    async def fake_analyze(self, task, context):
        return AgentResult(result_id=identity('result',task.task_id),task_id=task.task_id,case_version=1,status='completed',
            used_refs=task.input_refs,claims=[Claim(claim_id='c1',text='否认幻觉。',kind='description',level=task.agent_type,
                observation_ids=[task.input_refs[0]],strength='descriptive')])

    def replies(self, observed_roles):
        def generate(gateway,system,data,schema):
            if schema == ReviewPlan:
                self.assertEqual(len(data['peer_results']),3)
                self.assertTrue(all('facts' in peer for peer in data['peer_results']))
                return ReviewPlan()
            if schema == RevisionDraft:
                role=data['request']['target_agent'];observed_roles.append(role)
                return RevisionDraft(resolution='corrected',answers=['原文未提供，不能作为明确阴性。'],
                    claims=[Claim(claim_id='c1',text='未提供相关信息。',kind='description',level=role,
                        observation_ids=['obs_'+role],strength='descriptive')])
            if schema == Synthesis: return Synthesis(ordered_claim_ids=[c['claim_id'] for c in data['claims']])
            raise AssertionError(schema)
        return generate

    def test_all_agents_review_latest_versions_and_resume(self):
        store,prep=self.preparation();roles=[]
        with patch.object(HistoryAgent,'analyze',self.fake_analyze), patch('neurogra.clinical.workflow.ModelGateway.generate_structured',self.replies(roles)):
            result=continue_run(prep,self.config)
        self.assertEqual(set(roles),set(ROLES))
        self.assertEqual(len(store.objects(prep.run_id,'review_response')),4)
        self.assertEqual(len(store.objects(prep.run_id,'result')),8)
        self.assertEqual(len(result.report.claims),4)
        self.assertTrue(all(d.result_version==2 for deps in result.report.claim_dependencies.values() for d in deps))
        _,audit=audit_run(self.config,prep.run_id,True)
        self.assertTrue(audit['protocol_passed'],audit['errors'])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured',side_effect=AssertionError('completed')):
            self.assertEqual(resume_run(prep.run_id,self.config),result)

    def test_all_agents_report_crash_replays_without_new_model_calls(self):
        store,prep=self.preparation();roles=[]
        with patch.object(HistoryAgent,'analyze',self.fake_analyze), patch('neurogra.clinical.workflow.ModelGateway.generate_structured',self.replies(roles)):
            with patch('neurogra.clinical.workflow.render_report',side_effect=KeyboardInterrupt('crash')):
                with self.assertRaises(KeyboardInterrupt):continue_run(prep,self.config)
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured',side_effect=AssertionError('already committed')):
            result=resume_run(prep.run_id,self.config)
        self.assertEqual(len(result.report.claims),4)
        _,audit=audit_run(self.config,prep.run_id,True)
        self.assertTrue(audit['protocol_passed'],audit['errors'])

    def test_preparation_resume_keeps_snapshot_and_original_deadline(self):
        captured=[]
        original=RunStore.create_run
        def create(store,*args):
            run=original(store,*args);captured.append(run);return run
        with patch.object(RunStore,'create_run',create), patch('neurogra.clinical.service.MainAgent.plan',side_effect=KeyboardInterrupt('before plan')):
            with self.assertRaises(KeyboardInterrupt): prepare_patient({'患者ID':'p'},self.config,False)
        run=captured[0];store=RunStore(self.config.resolve(self.config.database));deadline=store.run(run)['deadline']
        self.assertEqual(len(store.objects(run,'snapshot')),1)
        with patch('neurogra.clinical.service.profile_case',side_effect=AssertionError('snapshot already saved')):
            result=resume_run(run,self.config)
        self.assertEqual(result.status,'completed')
        self.assertEqual(store.run(run)['deadline'],deadline)
        self.assertEqual(len(store.objects(run,'snapshot')),1)

    def test_acceptance_detects_superseded_dependencies(self):
        store,prep=self.preparation()
        with patch.object(HistoryAgent,'analyze',self.fake_analyze), patch('neurogra.clinical.workflow.ModelGateway.generate_structured',self.replies([])):
            result=continue_run(prep,self.config)
        next(iter(result.report.claim_dependencies.values()))[0].result_version=1
        store.save(prep.run_id,'run_result','main',result,version=2,expected_version=1)
        _,audit=audit_run(self.config,prep.run_id,True)
        self.assertFalse(audit['protocol_passed'])
        self.assertTrue(any(e.startswith('latest_claim_dependencies') for e in audit['errors']))

    def test_expired_budget_keeps_facts_without_new_calls(self):
        store,prep=self.preparation()
        for task in prep.plan.tasks:
            result=asyncio.run(self.fake_analyze(task,None))
            result.claims[0].text='未提供相关信息。'
            store.save(prep.run_id,'result',result.result_id,result)
        with store.connect() as db: db.execute('UPDATE runs SET deadline=1 WHERE run_id=?',(prep.run_id,))
        result=continue_run(prep,self.config)
        self.assertEqual(result.status,'partial')
        self.assertEqual(store.summary(prep.run_id)['calls'],[])
        self.assertEqual(len(result.report.claims),4)

    def test_each_agent_can_maintain_source_backed_result_against_main_question(self):
        store,prep=self.preparation()
        for task in prep.plan.tasks:
            result=asyncio.run(self.fake_analyze(task,None));result.claims[0].text='未提供相关信息。'
            store.save(prep.run_id,'result',result.result_id,result)
        def reply(_,system,data,schema):
            if schema==ReviewPlan:
                return ReviewPlan(concerns=[ReviewConcern(claim_ids=['c1'],question='请确认原文是否明确写明未提供相关信息',success_criterion='引用原始观察核实缺失状态是否描述准确')])
            if schema==RevisionDraft:
                return RevisionDraft(resolution='maintained_with_evidence',answers=['原文明确写明未提供相关信息，无需改成阴性。'],
                    claims=[Claim.model_validate(c) for c in data['previous']['claims']])
            return Synthesis(ordered_claim_ids=[c['claim_id'] for c in data['claims']])
        with patch('neurogra.clinical.workflow.ModelGateway.generate_structured',reply):
            result=continue_run(prep,self.config)
        replies=store.objects(prep.run_id,'review_response')
        self.assertEqual(len(replies),4)
        self.assertTrue(all(r['payload']['resolution']=='maintained_with_evidence' for r in replies))
        self.assertEqual(len(result.report.claims),4)

    def test_one_review_failure_does_not_drop_other_agents(self):
        store,prep=self.preparation();base=self.replies([])
        def reply(gateway,system,data,schema):
            if schema==RevisionDraft and data['request']['target_agent']=='laboratory':
                raise RuntimeError('laboratory review unavailable')
            return base(gateway,system,data,schema)
        with patch.object(HistoryAgent,'analyze',self.fake_analyze),patch('neurogra.clinical.workflow.ModelGateway.generate_structured',reply):
            result=continue_run(prep,self.config)
        self.assertEqual(len(result.report.claims),3)
        self.assertEqual(result.status,'partial')
        self.assertNotIn('laboratory',{c.level for c in result.report.claims})


if __name__ == '__main__': unittest.main()
