"""Create source-preserving local RWE test subsets without model or RWE calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.profiling import profile_case

from neurogra.clinical.rwe_preprocessing import trim_patient


def stats(raw, config):
    intake = ingest_patient(raw, config)
    snapshot = profile_case(intake, [], config, None)
    return {'field_records': len(intake.records), 'observations': len(snapshot.observations),
            'form_records': sum(len(f['records']) for f in raw['forms'].values())}


def write_new_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental replacement of an existing dataset or its manifest.
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description='按通用规则裁剪任意RWE患者JSON，保留原文件并生成清单。')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path, help='默认与原始文件同目录；目标文件必须不存在')
    parser.add_argument('--mode', choices=('short', 'smoke', 'both', 'none'), default='both',
                        help='默认同时生成short和smoke；none仅去除备注列')
    parser.add_argument('--detail-years', type=int, nargs='+', default=[],
                        help='short模式额外保留完整分项的年份；默认不固定任何年份')
    args = parser.parse_args()
    if args.detail_years and args.mode not in {'short', 'both'}:
        parser.error('--detail-years仅适用于short或both模式')
    source = args.source.resolve()
    source_bytes = source.read_bytes()
    raw = json.loads(source_bytes.decode('utf-8-sig'))
    directory = (args.output_dir or source.parent).resolve()
    config = ClinicalConfig(project_root=Path(__file__).resolve().parents[2], max_records=5000)
    original_stats = stats(raw, config)
    outputs = []
    modes = ('smoke', 'short') if args.mode == 'both' else (args.mode,)
    for mode in modes:
        data_path = directory / f'{source.stem}_{mode}.json'
        manifest_path = directory / f'{source.stem}_{mode}.manifest.json'
        if data_path.exists() or manifest_path.exists() or data_path == source:
            raise ValueError('output_already_exists: ' + str(data_path))
        data, manifest = trim_patient(raw, mode,
            complete_detail_years=args.detail_years if mode == 'short' else ())
        manifest.update({'schema_version': '1', 'source_file': str(source),
            'source_sha256': hashlib.sha256(source_bytes).hexdigest(), 'source_stats': original_stats,
            'output_file': str(data_path), 'output_stats': stats(data, config)})
        outputs.append((data_path, manifest_path, data, manifest))
    for data_path, manifest_path, data, manifest in outputs:
        write_new_json(data_path, data)
        manifest['output_sha256'] = hashlib.sha256(data_path.read_bytes()).hexdigest()
        manifest['output_bytes'] = data_path.stat().st_size
        write_new_json(manifest_path, manifest)
        print(json.dumps({'file': str(data_path), 'bytes': manifest['output_bytes'],
                          **manifest['output_stats']}, ensure_ascii=False))
    if source.read_bytes() != source_bytes:
        raise RuntimeError('source_changed_during_trimming')


if __name__ == '__main__':
    main()
