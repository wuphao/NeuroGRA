import json
import runpy
import tempfile
import unittest
from pathlib import Path

from neurogra_kb import KBConfig, TextKnowledgeService
from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.config import KGConfig, GraphConfig, EmbeddingConfig
from neurogra_kg.contracts.domain import BuildResult, DocumentInput, Entity, KnowledgeStatement, SourceRef
from neurogra_kg.utils import atomic_json


class Embedding:
    config = EmbeddingConfig(model='fixture', dimension=2)
    def model_digest(self):
        return 'fixture_digest'
    def embed(self, texts, budget=None):
        return [[1.0, 0.0] for _ in texts]


class FolderBuildTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[3] / 'output/test_folder_build'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        entry = Path(__file__).resolve().parents[2] / 'build_knowledge.py'
        self.functions = runpy.run_path(str(entry))

    def test_pdf_scan_is_recursive_case_insensitive_and_ordered(self):
        (self.root / 'sub').mkdir()
        for name in ('a.PDF', 'b.pdf', 'ignore.txt', 'sub/c.pdf'):
            (self.root / name).write_text(name, encoding='utf-8')
        files = self.functions['find_pdfs'](self.root)
        self.assertEqual([p.name for p in files], ['a.PDF', 'b.pdf', 'c.pdf'])
        self.assertEqual(len(self.functions['find_pdfs'](self.root, False)), 2)

    def test_failure_does_not_drop_other_documents_or_publish_partial_graph(self):
        files = []
        for i in range(3):
            path = self.root / f'{i}.pdf'
            path.write_text(str(i), encoding='utf-8')
            files.append(path)
        kb_final, graph_final = [], []
        class KB:
            def publish(self, docs, **kwargs):
                if len(docs) == 1 and docs[0].stem == '1':
                    raise ValueError('bad_pdf')
                if kwargs.get('vector'):
                    kb_final.extend(docs)
                return {'resource_count': 2, 'release_id': 'kb_release_fixture'}
        class KG:
            def build(self, request):
                path = request.documents[0].path
                return BuildResult(build_id='build_' + path.stem,
                    status='partial' if path.stem == '1' else 'completed', failed_units=1 if path.stem == '1' else 0)
            def publish_builds(self, ids, **kwargs):
                graph_final.extend(ids)
                return {'release_id': 'kg_release_fixture'}
        result = self.functions['build_folder'](files, self.root / 'result', kb=KB(), kg=KG())
        self.assertEqual(kb_final, [files[0], files[2]])
        self.assertEqual(graph_final, ['build_0', 'build_2'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['files'][1]['kg'], 'partial')
        self.assertEqual(json.loads((self.root / 'result/summary.json').read_text(encoding='utf-8')), result)

    def test_real_two_pdf_kb_contains_both_documents(self):
        import fitz
        files = []
        for name, text in [('first', 'Memory assessment alpha'), ('second', 'Laboratory evidence beta')]:
            path = self.root / (name + '.pdf')
            doc = fitz.open()
            doc.new_page().insert_text((50, 50), text)
            doc.save(path)
            doc.close()
            files.append(path)
        kb = TextKnowledgeService(KBConfig(root=self.root, embedding=Embedding.config), embedding=Embedding())
        self.functions['build_folder'].__globals__['BUILD_KG'] = False
        result = self.functions['build_folder'](files, self.root / 'result', kb=kb)
        manifest = kb.get_release(result['kb_release_id'])
        self.assertEqual(manifest['document_count'], 2)
        self.assertTrue(manifest['vector'])
        self.assertEqual(kb.active_release(), result['kb_release_id'])

    def test_merged_graph_release_contains_both_builds_and_rejects_partial(self):
        config = KGConfig(root=self.root, graph=GraphConfig(provider='memory'))
        with KnowledgeGraphService(config, llm=object(), embedding=Embedding()) as kg:
            ids = []
            for i in range(2):
                path = self.root / f'doc{i}.json'
                text = f'X{i} supports D{i}.'
                path.write_text(json.dumps({'blocks': [{'kind': 'paragraph', 'page': 1, 'text': text}]}), encoding='utf-8')
                parsed = kg.parser.parse(DocumentInput(path=path))
                block = parsed.blocks[0]
                statement = KnowledgeStatement(statement_id=f's{i}',
                    subject=Entity(entity_id=f'h{i}', name=f'X{i}', entity_type='other'), predicate='supports',
                    object=Entity(entity_id=f't{i}', name=f'D{i}', entity_type='other'), assertion_text=text,
                    condition_status='explicit_unrestricted', status='approved', purposes=['diagnostic_assessment'],
                    source_refs=[SourceRef(block_id=block.block_id, start=0, end=len(text), quote=text)])
                build_id = f'build_fixture{i}'
                ids.append(build_id)
                directory = kg.output_root / 'builds' / build_id
                atomic_json(directory / 'documents.json', [parsed.model_dump(mode='json')])
                atomic_json(directory / 'statements.json', [statement.model_dump(mode='json')])
                kg.registry.save('build', build_id, BuildResult(build_id=build_id, status='completed'))
            manifest = kg.publish_builds(ids)
            self.assertEqual(set(manifest['statement_ids']), {'s0', 's1'})
            self.assertEqual(kg.get_release()['release_id'], manifest['release_id'])
            kg.registry.save('build', 'build_partial', BuildResult(build_id='build_partial', status='partial'))
            with self.assertRaisesRegex(ValueError, 'build_not_completed'):
                kg.publish_builds([ids[0], 'build_partial'])
            self.assertEqual(kg.get_release()['release_id'], manifest['release_id'])


if __name__ == '__main__':
    unittest.main()
