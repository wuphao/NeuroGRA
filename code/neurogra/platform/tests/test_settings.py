import tempfile
import unittest
from pathlib import Path

from neurogra.platform.configuration import resolve_config
from neurogra.platform.settings import load_settings, save_settings, settings_view


class SettingsTests(unittest.TestCase):
    def test_dialogue_budget_is_configurable_and_independent_of_report_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = resolve_config(root=root)
            report_budget = current.budget.model_dump()
            changed = save_settings(root, current, {'dialogue_budget': {
                'llm': 9, 'retrieval': 4, 'patient_data': 3, 'embedding': 7,
                'seconds': 120, 'mixed_seconds': 180}})
            loaded = load_settings(root)
            self.assertEqual(loaded, changed)
            self.assertEqual(loaded.budget.model_dump(), report_budget)
            self.assertEqual(settings_view(loaded)['dialogue_budget']['patient_data'], 3)
            self.assertEqual(loaded.dialogue_budget.seconds, 120)
            self.assertEqual(loaded.dialogue_budget.mixed_seconds, 180)
            with self.assertRaises(ValueError):
                save_settings(root, loaded, {'dialogue_budget': {'patient_data': -1}})
            self.assertEqual(load_settings(root), loaded)

    def test_settings_persist_and_do_not_reset_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = resolve_config(root=root, profile='kb-only', overrides={'kb': {'output_root': 'custom/kb'}})
            changed = save_settings(root, current, {'model': {'timeout_seconds': 42}, 'kb': {'embedding': {'batch_size': 2}}})
            loaded = load_settings(root)
            self.assertEqual(loaded, changed)
            self.assertEqual(loaded.kb.output_root, Path('custom/kb'))
            self.assertEqual(loaded.model.timeout_seconds, 42)

    def test_unsafe_endpoints_and_filesystem_overrides_are_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = resolve_config(root=tmp)
            for value in [{'model': {'base_url': 'file:///private'}}, {'project_root': '/other'},
                          {'kg': {'graph': {'password_file': '/private'}}},
                          {'budget': {'llm': 1}}]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    save_settings(tmp, config, value)
            self.assertFalse((Path(tmp) / 'data/platform/settings.json').exists())

    def test_external_knowledge_storage_survives_embedding_settings_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'kb.yaml').write_text('output_root: custom/kb\ndata_root: custom/kb-data\n', encoding='utf-8')
            (root / 'kg.yaml').write_text('paths:\n  data_root: custom/kg-data\n  output_root: custom/kg\n', encoding='utf-8')
            current = resolve_config(root=root, overrides={'kb': None, 'kg': None,
                'text_knowledge_config': 'kb.yaml', 'knowledge_config': 'kg.yaml'})
            changed = save_settings(root, current, {'kb': {'embedding': {'batch_size': 2}},
                'kg': {'embedding': {'batch_size': 3}}})
            self.assertEqual(changed.kb.output_root, Path('custom/kb'))
            self.assertEqual(changed.kg.paths.data_root, Path('custom/kg-data'))
            self.assertEqual(load_settings(root), changed)
