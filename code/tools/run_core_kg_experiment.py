"""User-authorized one-pass KG construction followed by the four-arm QA run."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from neurogra_kg.utils import atomic_json
from neurogra.benchmarks.kgqa.core import read_json, load_config, run_lock


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / 'output/kg/core_corpus_20261017_full'
    env = {**os.environ, 'PYTHONPATH': str(root / 'code')}
    state_path = output / 'pipeline.json'
    state = read_json(state_path) if state_path.exists() else {'created_at': time.time()}
    def save(stage):
        state.update(stage=stage, process_id=os.getpid(), updated_at=time.time())
        atomic_json(state_path, state)
    def run(module, args):
        return subprocess.run([sys.executable, '-u', '-m', module, *args], cwd=root, env=env).returncode
    with run_lock(output / 'pipeline_lease'):
        try:
            save('building_kg_single_pass_skip_failed')
            code = run('tools.build_core_kg', ['--config', 'configs/kg.core_build.local.yaml',
                '--publish-config', 'configs/kg.local.yaml', '--output', str(output), '--fast'])
            if code:
                raise RuntimeError('kg_job_failed:' + str(code))
            graph = read_json(output / 'progress.json')
            state['release_id'] = graph['release_id']
            experiment = Path(graph['experiment_config'])
            config = load_config(experiment)
            run_dir = Path(config['output'])
            save('running_120_qa_cases')
            args = ['--config', str(experiment)]
            if (run_dir / 'manifest.json').exists():
                args.append('--resume')
            code = run('neurogra.benchmarks.kgqa.runner', args)
            if not (run_dir / 'manifest.json').exists():
                raise RuntimeError('experiment_failed_before_manifest:' + str(code))
            manifest = read_json(run_dir / 'manifest.json')
            if manifest['status'] not in {'completed', 'finished_with_errors'}:
                raise RuntimeError('experiment_not_finished:' + manifest['status'])
            state['experiment_directory'] = str(run_dir)
            state['experiment_status'] = manifest['status']
            save('exporting_review_materials')
            review = run_dir.with_name(run_dir.name + '-review')
            if not review.exists():
                code = run('neurogra.benchmarks.kgqa.review', ['export', '--run', str(run_dir),
                    '--output', str(review), '--gold', str(root / 'docs/experiments/kgqa/core30_gold.jsonl')])
                if code:
                    raise RuntimeError('review_export_failed')
            elif read_json(review / 'private_mapping.json')['run_binding'] != manifest['binding']:
                raise ValueError('review_binding_changed')
            results = run_dir.with_name(run_dir.name + '-unscored.json')
            code = run('neurogra.benchmarks.kgqa.review', ['score', '--run', str(run_dir),
                '--mapping', str(review / 'private_mapping.json'), '--reviews', str(review / '评分表.csv'),
                '--output', str(results)])
            if code:
                raise RuntimeError('unscored_summary_failed')
            state.update(review_directory=str(review), unscored_results=str(results))
            save('awaiting_human_review')
            return 0
        except BaseException as exc:
            state['error'] = type(exc).__name__ + ':' + str(exc)
            save('interrupted_or_failed')
            raise


if __name__ == '__main__':
    raise SystemExit(main())
