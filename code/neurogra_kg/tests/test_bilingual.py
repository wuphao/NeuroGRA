"""Cross-language identity, source boundaries, applicability and release isolation."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from neurogra_kg.adapters.graph import MemoryGraphStore, Neo4jGraphStore
from neurogra_kg.adapters.ollama_llm import source_bound_schema
from neurogra_kg.config import ConstructionConfig, KGConfig, GraphConfig
from neurogra_kg.construction.localization import annotate_statement
from neurogra_kg.construction.parsing import DocumentParser
from neurogra_kg.construction.pipeline import ConstructionPipeline
from neurogra_kg.construction.terminology import EntityResolver
from neurogra_kg.contracts.domain import (AtomCondition, Block, ChineseAnnotation, Document, DocumentInput,
    Entity, KnowledgeStatement, ParsedDocument, PatientFact, RetrievalRequest, SourceRef, TextCondition)
from neurogra_kg.contracts.tasks import LocalizationOutput, LocalizationVerification
from neurogra_kg.retrieval.applicability import evaluate_atom
from neurogra_kg.retrieval.pipeline import retrieve
from neurogra_kg.runtime import Budget
from neurogra_kg.storage.indexes import LocalVectorStore
from neurogra_kg.storage.registry import Registry
from neurogra_kg.storage.releases import create_release, load_release, statement_text
from neurogra_kg.terminology import expand_query
from neurogra_kg.tests.test_release_integrity import FixtureEmbedding


class BilingualLLM:
    def __init__(self, text="合成指标支持示例状态。", equivalent=True, wrong_quote=False):
        self.text, self.equivalent, self.wrong_quote = text, equivalent, wrong_quote
        self.calls = []
    def binding(self):
        return {"model": "bilingual-fixture"}
    def execute(self, task, payload, schema, budget):
        self.calls.append(task)
        def refs(row):
            r = row['source_refs'][0]
            return [{'block_id':r['block_id'], 'quote':'伪造译文' if self.wrong_quote else r['quote'], 'occurrence':0}]
        if task == 'L7':
            return schema.model_validate({'items':[{'target':r['target'], 'text_zh':self.text, 'source_refs':refs(r)} for r in payload['targets']]})
        return schema.model_validate({'checks':[{'target':r['target'], 'equivalent':self.equivalent,
            'reason':'fixture semantic check','source_refs':refs(r)} for r in payload['targets']]})


class BilingualTests(unittest.TestCase):
    def data(self, text="Marker X supports state D."):
        b = Block(block_id='b', document_id='d', page=1, order=0, kind='paragraph', text=text)
        ref = SourceRef(block_id='b', start=0, end=len(text), quote=text)
        s = KnowledgeStatement(statement_id='s', subject=Entity(entity_id='h',name='Marker X',entity_type='biomarker'),
            predicate='supports', object=Entity(entity_id='t',name='state D',entity_type='other'), assertion_text=text,
            condition_status='explicit_unrestricted', status='approved', purposes=['diagnostic_assessment'], source_refs=[ref])
        doc = ParsedDocument(document=Document(document_id='d',checksum='fixture',source_path='fixture',snapshot_path='fixture',
            title='fixture',version='1',language='en'),blocks=[b],parser='fixture',parser_version='1')
        return s,doc

    def test_legacy_json_defaults_and_entity_identity(self):
        s,doc=self.data()
        data=s.model_dump(mode='json')
        for key in ['assertion_language','zh_annotation']:data.pop(key)
        for e in [data['subject'],data['object']]:
            e.pop('label_zh');e.pop('label_en')
        old=KnowledgeStatement.model_validate(data)
        self.assertEqual(old.statement_id,s.statement_id)
        self.assertEqual(old.assertion_language,'und')
        resolver=EntityResolver()
        zh,en,abbr=[resolver.resolve(n,'disease') for n in ['阿尔茨海默病',"Alzheimer's disease",'AD']]
        self.assertEqual(zh.entity_id,en.entity_id)
        self.assertEqual(zh.entity_id,abbr.entity_id)
        self.assertEqual(en.label_zh,'阿尔茨海默病')
        self.assertNotEqual(resolver.resolve('MCI','syndrome').entity_id,en.entity_id)

    def test_document_language_preserves_source_identity_and_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'source.json'
            p.write_text(json.dumps({'blocks':[{'page':1,'kind':'paragraph','text':'English original.'}]}),encoding='utf-8')
            parser=DocumentParser(Path(tmp)/'data')
            a=parser.parse(DocumentInput(path=p))
            b=parser.parse(DocumentInput(path=p,language='en'))
            self.assertEqual(a.document.document_id,b.document.document_id)
            self.assertEqual(a.blocks[0].block_id,b.blocks[0].block_id)
            self.assertEqual(b.blocks[0].text,'English original.')
            self.assertEqual(b.document.language,'en')

    def test_translation_verified_separately_without_changing_core(self):
        s,doc=self.data(); before=s.model_dump(mode='json'); model=BilingualLLM()
        annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
        self.assertEqual(model.calls,['L7','L8'])
        self.assertEqual(s.zh_annotation.status,'verified')
        for key in ['statement_id','assertion_text','condition','source_refs','subject','object','status']:
            self.assertEqual(s.model_dump(mode='json')[key],before[key])
        self.assertIn('合成指标',statement_text(s))

    def test_translation_semantic_rejection_does_not_enter_index(self):
        s,doc=self.data(); model=BilingualLLM(text='错误确诊结论',equivalent=False)
        annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
        self.assertEqual(s.status,'approved')
        self.assertEqual(s.zh_annotation.status,'failed')
        self.assertNotIn('错误确诊结论',statement_text(s))

    def test_translated_quotes_are_rejected(self):
        s,doc=self.data(); model=BilingualLLM(wrong_quote=True)
        annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
        self.assertEqual(s.zh_annotation.status,'failed')
        self.assertEqual(model.calls,['L7'])
        self.assertEqual(s.source_refs[0].quote,doc.blocks[0].text)

    def test_numeric_and_embedded_biomarker_changes_are_rejected(self):
        for original,zh in [('Age at least 18.','年龄至少19。'),('p-tau181 supports X.','p-tau217支持示例状态。')]:
            s,doc=self.data(original); model=BilingualLLM(text=zh)
            annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
            self.assertEqual(s.zh_annotation.status,'failed')
            self.assertEqual(model.calls,['L7'])

    def test_explicit_absence_cannot_be_translated_as_missing(self):
        s,doc=self.data('Exclusion E must be explicitly absent.')
        model=BilingualLLM(text='排除项E必须明确缺失。')
        annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
        self.assertEqual(s.zh_annotation.status,'failed')
        self.assertEqual(model.calls,['L7'])

    def test_translation_target_references_are_decoder_bound(self):
        bound=source_bound_schema(LocalizationOutput,{'targets':[{'target':'assertion'}]})
        self.assertEqual(bound['$defs']['LocalizationItem']['properties']['target']['enum'],['assertion'])
        self.assertNotIn('enum',LocalizationOutput.model_json_schema()['$defs']['LocalizationItem']['properties']['target'])

    def test_native_chinese_does_not_require_translation_calls(self):
        s,doc=self.data('合成指标支持示例状态。'); model=BilingualLLM()
        annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
        self.assertEqual(model.calls,[])
        self.assertEqual(s.zh_annotation.status,'verified')

    def test_text_conditions_have_their_own_translation_scope(self):
        s,doc=self.data('Marker X supports D. Exclude example E.')
        s=s.model_copy(update={'condition':TextCondition(condition_id='c',text='Exclude example E.',reason='not_structured',source_refs=s.source_refs), 'condition_status':'constrained'})
        annotate_statement(s,{'b':doc.blocks[0]},BilingualLLM(),Budget(10))
        self.assertEqual(s.condition.text,'Exclude example E.')
        self.assertEqual(s.condition.zh_annotation.status,'verified')
        self.assertEqual(s.condition.condition_id,'c')

    def test_method_population_and_value_equivalence_preserves_unknown(self):
        s,doc=self.data(); ref=s.source_refs[0]; time=datetime(2026,1,1,tzinfo=timezone.utc)
        c=AtomCondition(condition_id='c',field='mmse',operator='gte',value=18,method='Mini-Mental State Examination',population='adults',source_refs=[ref])
        f=PatientFact(fact_id='f',field='MMSE',value=18,method='简易精神状态检查',source_ref=ref)
        pop=PatientFact(fact_id='p',field='population',value='成人',source_ref=ref)
        self.assertEqual(evaluate_atom(c,[f,pop],time).state,'SATISFIED')
        self.assertEqual(evaluate_atom(c,[f],time).state,'UNKNOWN')
        f.method='未知量表版本'
        self.assertEqual(evaluate_atom(c,[f,pop],time).state,'UNKNOWN')
        sex=AtomCondition(condition_id='c2',field='sex',operator='eq',value='male',source_refs=[ref])
        male=PatientFact(fact_id='m',field='性别',value='男性',source_ref=ref)
        self.assertEqual(evaluate_atom(sex,[male],time).state,'SATISFIED')

    def test_explicit_absence_differs_from_unrecorded(self):
        s,doc=self.data();ref=s.source_refs[0];time=datetime(2026,1,1,tzinfo=timezone.utc)
        c=AtomCondition(condition_id='c',field='symptom.visual_hallucinations',operator='eq',value=False,source_refs=[ref])
        f=PatientFact(fact_id='f',field='视幻觉',value=False,source_ref=ref)
        self.assertEqual(evaluate_atom(c,[f],time).state,'SATISFIED')
        self.assertEqual(evaluate_atom(c,[],time).state,'UNKNOWN')
        f.polarity='uncertain'
        self.assertEqual(evaluate_atom(c,[f],time).state,'UNKNOWN')

    def test_release_retrieval_and_vectors_use_one_statement_identity(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Registry(Path(tmp)/'registry.sqlite')) as registry:
            root=Path(tmp)
            s,doc=self.data(); model=BilingualLLM()
            annotate_statement(s,{'b':doc.blocks[0]},model,Budget(10))
            graph=MemoryGraphStore();embedding=FixtureEmbedding()
            manifest=create_release(root,registry,'build',[s],[doc],embedding,graph,Budget(10))
            request=RetrievalRequest(question='合成指标',purpose='diagnostic_assessment',reference_time=datetime(2026,1,1,tzinfo=timezone.utc),backends=['lexical'],max_supplement_rounds=0)
            result=retrieve(manifest,request,KGConfig().retrieval,embedding,graph,None,Budget(10))
            self.assertEqual(result.applicable[0].statement.statement_id,'s')
            _,_,resources,_=load_release(manifest)
            index=LocalVectorStore(Path(manifest['directory']),embedding)
            index.validate_binding(resources,manifest['release_id'])
            resources[0]['text']='mutated translation'
            with self.assertRaisesRegex(ValueError,'corpus_binding_mismatch'):
                index.validate_binding(resources,manifest['release_id'])
            old_dir=manifest['directory'];old_checksum=manifest['checksums']['corpus.json']
            s.zh_annotation.text='示例状态由合成指标支持。'
            new=create_release(root,registry,'build2',[s],[doc],embedding,graph,Budget(10))
            self.assertNotEqual(manifest['release_id'],new['release_id'])
            self.assertEqual(load_release(manifest)[0][0].statement_id,'s')
            self.assertEqual(manifest['directory'],old_dir)
            self.assertEqual(manifest['checksums']['corpus.json'],old_checksum)

    def test_failed_translation_is_visible_as_degradation(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Registry(Path(tmp)/'registry.sqlite')) as registry:
            s,doc=self.data();s.zh_annotation=ChineseAnnotation(status='failed')
            graph=MemoryGraphStore();embedding=FixtureEmbedding()
            manifest=create_release(Path(tmp),registry,'build',[s],[doc],embedding,graph,Budget(10))
            request=RetrievalRequest(question='Marker X',purpose='diagnostic_assessment',reference_time=datetime(2026,1,1,tzinfo=timezone.utc),backends=['lexical'])
            result=retrieve(manifest,request,KGConfig().retrieval,embedding,graph,None,Budget(10))
            self.assertTrue(result.degraded)
            self.assertIn('chinese_expression_unavailable:s',result.warnings)

    def test_graph_stores_include_both_labels(self):
        s,doc=self.data();s.subject.label_zh='合成指标';s.subject.label_en='Synthetic marker'
        graph=MemoryGraphStore();graph.publish('release',[s])
        self.assertEqual(graph.neighbors('release',['合成指标'],1,10),['s'])
        queries=[]
        class Result:
            def consume(self):pass
        class Session:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,query,**kw):queries.append((query,kw));return Result()
            def execute_write(self,write):write(self)
        class Driver(Session):
            def session(self,**kw):return Session()
        with patch.object(Neo4jGraphStore,'_driver',return_value=Driver()):
            Neo4jGraphStore(GraphConfig()).publish('release',[s],{'b':doc.blocks[0]})
        params=next(kw for q,kw in queries if 'MERGE (h:NGKGEntity' in q)
        self.assertIn('合成指标',params['h_aliases'])
        self.assertEqual(params['h_label_en'],'Synthetic marker')

    def test_short_abbreviation_not_matched_inside_word(self):
        self.assertNotIn('阿尔茨海默病',expand_query('lead'))
        self.assertIn('阿尔茨海默病',expand_query('AD患者'))

    def test_chinese_logic_field_alias_keeps_english_inclusive_boundary(self):
        from neurogra_kg.construction.logic import assemble_logic, number_logic
        from neurogra_kg.contracts.tasks import ExtractedCondition, LogicDraft
        _,doc=self.data('Age at least 18.')
        quote={'block_id':'b','quote':doc.blocks[0].text,'occurrence':0}
        candidate=ExtractedCondition(local_id='c',condition_text=doc.blocks[0].text,category='threshold',scope_hint='local',source_refs=[quote])
        node={'kind':'ATOM','condition_id':'c','field':'年龄','operator':'gte','value':18,'unit':'year',
              'method':None,'time_scope':None,'population':None,'source_refs':[quote]}
        draft=LogicDraft(statement_id='s',root=node,unresolved_items=[],logic_evidence=[quote])
        tree=assemble_logic(number_logic(draft),'s',[candidate],{'b':doc.blocks[0]})
        self.assertEqual((tree.field,tree.operator,tree.value),('age','gte',18))
        node['operator']='gt'
        draft=LogicDraft(statement_id='s',root=node,unresolved_items=[],logic_evidence=[quote])
        with self.assertRaisesRegex(ValueError,'inclusive_lower_bound'):
            assemble_logic(number_logic(draft),'s',[candidate],{'b':doc.blocks[0]})

    def test_invalid_single_child_group_is_rejected_before_assembly(self):
        from pydantic import ValidationError
        from neurogra_kg.contracts.tasks import LogicDraft
        leaf={'kind':'TEXT','condition_id':'c','text':'An explicit condition.',
              'source_refs':[{'block_id':'b','quote':'An explicit condition.','occurrence':0}]}
        with self.assertRaises(ValidationError):
            LogicDraft(statement_id='s',root={'kind':'ALL','children':[leaf]},unresolved_items=[],logic_evidence=[])
        valid=LogicDraft(statement_id='s',root=leaf,unresolved_items=[],logic_evidence=[])
        self.assertEqual(valid.root.kind,'TEXT')

    def test_pipeline_localization_toggle_preserves_generated_statement_ids(self):
        class PipelineLLM(BilingualLLM):
            def execute(self,task,payload,schema,budget):
                if task in {'L7','L8'}:return super().execute(task,payload,schema,budget)
                self.calls.append(task)
                block=payload.get('blocks',payload.get('source_blocks'))[0]
                ref={'block_id':block['block_id'],'quote':block['text'],'occurrence':0}
                if task=='L1':
                    return schema.model_validate({'statements':[{'subject_text':'Marker X','subject_type':'biomarker',
                        'predicate':'supports','object_text':'state D','object_type':'other','assertion_text':block['text'],
                        'purposes':['diagnostic_assessment'],'condition_status':'explicit_unrestricted',
                        'source_refs':[ref],'field_evidence':{f:[ref] for f in ['subject','relation','object','purpose']}}],
                        'conditions':[],'issues':[]})
                return schema.model_validate({'verdict':'supported','checks':[{'field':f,'state':'supported',
                    'evidence_refs':[ref],'reason':'fixture'} for f in ['subject','relation','object','conditions','logic','purpose','source_scope']],
                    'issues':[]})
        _,doc=self.data()
        with tempfile.TemporaryDirectory() as tmp:
            old=ConstructionPipeline(ConstructionConfig(localize_zh=False),PipelineLLM())
            new=ConstructionPipeline(ConstructionConfig(localize_zh=True),PipelineLLM())
            original,_=old.run(doc,Path(tmp)/'old',Budget(10))
            localized,_=new.run(doc,Path(tmp)/'new',Budget(10))
            self.assertEqual(original[0].statement_id,localized[0].statement_id)
            self.assertEqual(original[0].subject.entity_id,localized[0].subject.entity_id)
            self.assertEqual(localized[0].zh_annotation.status,'verified')
            new.llm.calls.clear()
            restored,_=new.run(doc,Path(tmp)/'new',Budget(10))
            self.assertEqual(new.llm.calls,[])
            self.assertEqual(restored[0],localized[0])

    def test_legacy_release_resources_load_without_forcing_new_index(self):
        from neurogra_kg.utils import checksum
        with tempfile.TemporaryDirectory() as tmp, closing(Registry(Path(tmp)/'registry.sqlite')) as registry:
            s,doc=self.data();embedding=FixtureEmbedding()
            manifest=create_release(Path(tmp),registry,'build',[s],[doc],embedding,MemoryGraphStore(),Budget(10))
            directory=Path(manifest['directory']);data=json.loads((directory/'corpus.json').read_text(encoding='utf-8'))
            data['documents'][0].pop('language')
            for row in data['statements']:
                row.pop('assertion_language');row.pop('zh_annotation')
                for field in ['subject','object']:
                    row[field].pop('label_zh');row[field].pop('label_en')
            (directory/'corpus.json').write_text(json.dumps(data),encoding='utf-8')
            manifest['checksums']['corpus.json']=checksum(directory/'corpus.json')
            manifest.pop('index_text_version');manifest.pop('terminology_binding')
            statements,blocks,resources,raw=load_release(manifest)
            self.assertEqual(statements[0].assertion_language,'und')
            self.assertEqual(raw['documents'][0].get('language','und'),'und')
            LocalVectorStore(directory,embedding).validate_binding(resources,manifest['release_id'])


if __name__=='__main__':unittest.main()
