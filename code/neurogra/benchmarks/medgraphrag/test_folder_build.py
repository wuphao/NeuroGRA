"""Folder build publication, stable provenance, resume and failure regressions."""
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from .folder_build import build_folder, scan_pdfs
from .pipeline import load_knowledge, knowledge_graph
from .runtime import Config
from .test_adapter import temporary_directory, FakeModel, FakeStore


class Reader:
    """Isolate PDF decoding; actual PDF decoding is checked separately on local PDFs."""
    is_encrypted = False

    def __init__(self, path):
        value = Path(path).read_bytes().decode()
        if value == 'corrupt':
            raise ValueError('corrupt PDF')
        self.pages = [type('Page', (), {'extract_text': lambda self: '' if value == 'scan' else value})()]


class FolderBuildTests(unittest.TestCase):
    def test_recursive_scan_and_empty_folder(self):
        with temporary_directory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'no_pdf'):
                scan_pdfs(root)
            (root/'nested').mkdir()
            (root/'A.PDF').write_bytes(b'a')
            (root/'nested/b.pdf').write_bytes(b'b')
            (root/'ignored.txt').write_bytes(b'txt')
            self.assertEqual(len(scan_pdfs(root)), 2)
            self.assertEqual(len(scan_pdfs(root, recursive=False)), 1)

    @patch('pypdf.PdfReader', Reader)
    def test_partial_success_dedup_and_inference_reuse(self):
        with temporary_directory() as directory:
            root = Path(directory)
            source = root/'input'
            source.mkdir()
            for name, text in [('a.pdf', 'MMSE document'), ('b.PDF', 'MMSE document'),
                               ('c.pdf', 'corrupt'), ('d.pdf', 'scan')]:
                (source/name).write_text(text, encoding='utf-8')
            output, model = root/'output', FakeModel()
            result = build_folder(source, Config(), output, client=model, store=FakeStore())
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['counts'], {'parsed': 0, 'built': 1, 'duplicate': 1, 'failed': 2})
            docs, refs, _ = load_knowledge(output/'knowledge_manifest.json', 2400)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]['layer'], 'middle')
            self.assertTrue(all(r['page'] == 1 for r in refs.values()))
            before = model.calls
            knowledge_graph(model, docs[0], Config(), output)
            self.assertEqual(model.calls, before)  # Patient inference uses existing PDF graph.
            result2 = build_folder(source, Config(), output, resume=True, client=model, store=FakeStore())
            self.assertEqual(result2['counts']['built'], 1)
            self.assertEqual(model.calls, before)  # Failed neighbors do not invalidate good document cache.

    @patch('pypdf.PdfReader', Reader)
    def test_check_has_no_model_or_database_calls(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root/'input').mkdir()
            (root/'input/a.pdf').write_text('MMSE synthetic', encoding='utf-8')
            with patch('neurogra.benchmarks.medgraphrag.folder_build.LocalModel', side_effect=AssertionError('model called')):
                result = build_folder(root/'input', Config(), root/'check', check=True)
            self.assertEqual(result['status'], 'checked')
            self.assertTrue((root/'check/prepared_manifest.json').exists())
            self.assertFalse((root/'check/knowledge_manifest.json').exists())

    @patch('pypdf.PdfReader', Reader)
    def test_all_fail_cannot_publish_stale_manifest(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root/'input').mkdir()
            source = root/'input/a.pdf'
            source.write_text('MMSE synthetic', encoding='utf-8')
            result = build_folder(root/'input', Config(), root/'out', client=FakeModel(), store=FakeStore())
            self.assertEqual(result['status'], 'completed')
            source.write_text('scan', encoding='utf-8')
            result = build_folder(root/'input', Config(), root/'out', resume=True, client=FakeModel(), store=FakeStore())
            self.assertEqual(result['status'], 'failed')
            manifest = json.loads((root/'out/knowledge_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['documents'], [])

    @patch('pypdf.PdfReader', Reader)
    def test_dictionary_layer_optional_and_parameter_change_rejected(self):
        with temporary_directory() as directory:
            root = Path(directory)
            for folder in ('papers', 'dictionary'):
                (root/folder).mkdir()
                (root/folder/'a.pdf').write_text('MMSE '+folder, encoding='utf-8')
            output = root/'out'
            result = build_folder(root/'papers', Config(), output, dictionary_folder=root/'dictionary',
                                  client=FakeModel(), store=FakeStore())
            self.assertEqual(result['layers'], ['bottom', 'middle'])
            docs, _, _ = load_knowledge(output/'knowledge_manifest.json', 2400)
            with self.assertRaisesRegex(ValueError, 'parameters_or_models_changed'):
                knowledge_graph(FakeModel(), docs[0], Config(merge_threshold=0.9), output)

    def test_cannot_ingest_output_snapshots(self):
        with temporary_directory() as directory:
            root = Path(directory)
            (root/'a.pdf').write_bytes(b'test')
            with self.assertRaisesRegex(ValueError, 'outside_input'):
                build_folder(root, Config(), root/'out', check=True)


if __name__ == '__main__':
    unittest.main()
