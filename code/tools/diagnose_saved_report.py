"""Run clinical synthesis on saved patient facts and evidence, without rerunning specialists."""
import argparse
import json
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurogra.clinical.schemas import FinalReport, CaseSnapshot, EvidenceItem
from neurogra.clinical.storage import RunStore
from neurogra.clinical.diagnostic_synthesis import add_diagnostic_assessment
from neurogra.clinical.reporting import render_saved_report
from neurogra.clinical.utils import write_json
from neurogra_common.llm_log import configure_llm_log


def main():
    parser = argparse.ArgumentParser(description='对保存病例进行真实的综合诊断推理与复核，使用本地配置模型；另存新报告。')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve() == args.source.resolve():
        raise ValueError('output_directory_must_be_new')
    project = Path(__file__).resolve().parents[2]
    config = runpy.run_path(str(project / 'code/main.py'))['build_config']()
    # This is an engineering replay of old, label-visible facts and retrieval.
    config.diagnosis_task_mode = 'follow_up'
    configure_llm_log(args.output.parent / (args.output.name + '.llm_calls.jsonl'))
    # A new, separately audited run: the original run deadline and budget remain unchanged.
    store = RunStore(project / 'output/clinical/diagnostic-synthesis.sqlite')
    limits = config.budget.model_dump()
    limits.update(llm=16, seconds=900)
    run_id = store.create_run(config.model_dump(mode='json'), limits)
    report = FinalReport.model_validate_json((args.source / 'report.json').read_text(encoding='utf-8'))
    provenance = json.loads((args.source / 'provenance.json').read_text(encoding='utf-8'))
    if (report.run_id, report.patient_id) != (provenance['run_id'], provenance['patient_id']):
        raise ValueError('report_provenance_identity_mismatch')
    snapshot = CaseSnapshot(case_id=report.run_id, patient_id=report.patient_id, raw_input_hash='saved_report',
        observations=provenance['observations'], records=provenance['records'],
        conflict_groups=provenance.get('conflict_groups', []))
    evidence = [EvidenceItem.model_validate(e) for e in provenance['evidence']]
    print('综合诊断推理运行：' + run_id, flush=True)
    add_diagnostic_assessment(report, snapshot, evidence, config, store, run_id)
    path = render_saved_report(args.source, args.output, report_override=report, evidence_override=evidence)
    updated = json.loads((args.output / 'provenance.json').read_text(encoding='utf-8'))
    updated['diagnostic_synthesis_run_id'] = run_id
    updated['diagnostic_outcome'] = report.diagnostic_outcome.model_dump(mode='json')
    updated['replay_scope'] = '原病例事实和原检索来源的非盲法诊断工程重放，不是完整流程或独立分类实验。'
    write_json(args.output / 'provenance.json', updated)
    with (args.output / 'audit.md').open('a', encoding='utf-8') as handle:
        handle.write('\n\n## 新增综合诊断推理\n\n运行编号：' + run_id +
                     '\n\n来源与独立模型核对记录：output/clinical/diagnostic-synthesis.sqlite。'
                     '\n\n该核对为自动化来源一致性检查，不代表人工临床复核。\n')
    store.status(run_id, report.status)
    print('诊断状态：' + report.diagnostic_outcome.status, flush=True)
    print('首选工作判断：' + (report.diagnostic_outcome.primary_working_label or '待定或技术失败，详见报告'), flush=True)
    print(path, flush=True)


if __name__ == '__main__':
    main()
