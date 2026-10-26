"""Verify imports/resources from an installed wheel, not the editable checkout."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True)
    parser.add_argument('--wheel', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    target = Path(args.target).resolve()
    import neurogra, neurogra_kb, neurogra_kg, neurogra_common
    modules = [neurogra, neurogra_kb, neurogra_kg, neurogra_common]
    assert all(Path(module.__file__).resolve().is_relative_to(target) for module in modules)
    from neurogra_kg.contracts.tasks import TASK_SCHEMAS
    prompt = Path(neurogra_kg.__file__).parent / 'prompts'
    for task, schema in TASK_SCHEMAS.items():
        assert (prompt / f'{task}.system.txt').is_file()
        assert (prompt / f'{task}.user.txt').is_file()
        assert json.loads((prompt / f'{task}.schema.json').read_text(encoding='utf-8')) == schema.model_json_schema()
    from neurogra_kb import TextKnowledgeService, load_config
    config = load_config(root / 'configs/kb.local.yaml')
    assert config.root == root
    result = TextKnowledgeService(config).search_text('认知', use_vector=False, top_k=2)
    assert result['items']
    wheel = Path(args.wheel).resolve()
    with zipfile.ZipFile(wheel) as archive:
        assert not any('/knowledge/' in name or name.endswith('/vector.py') or 'test_history_workflow' in name
                       for name in archive.namelist())
    summary = {'wheel': str(wheel), 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
               'installed_imports': [str(module.__file__) for module in modules],
               'prompt_tasks_verified': sorted(TASK_SCHEMAS), 'installed_kb_query_hits': len(result['items']),
               'retired_code_absent': True, 'config_root': str(config.root)}
    (root / 'output/system_refactor/installed-package-check.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
