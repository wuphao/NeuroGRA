"""Functional local-model smoke with code-created synthetic input, not an experiment."""

import json
from pathlib import Path

from neurogra.clinical.config import load_config
from neurogra.clinical.service import prepare_patient
from neurogra.clinical.workflow import continue_run
from neurogra.clinical.acceptance import audit_run
from neurogra.clinical.utils import identity, write_json


def main():
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "configs/clinical.default.yaml").model_copy(update={
        "database": Path("output/system_refactor/local_model_smoke.sqlite"),
        "data_root": Path("output/system_refactor/local_model_smoke/data"),
        "output_root": Path("output/system_refactor/local_model_smoke"),
        # Explicitly exercise a missing graph publication, without using synthetic KG rules as clinical evidence.
        "knowledge_release_id": identity("release", ["functional_smoke_unpublished_graph"]),
        "verify_narrative": True})
    raw = {"患者ID": identity("synthetic_patient", ["main_iteration_functional_smoke"]), "synthetic": True,
           "年龄": 68, "病史": "示例资料：自述记忆下降，未提供既往用药记录。",
           "认知评估": {"量表名称": "MMSE", "总分": 24, "教育年限": None, "时间": "2026-10-01"},
           "检验": {"项目": "维生素B12", "结果": 300, "单位": "pg/mL", "时间": "2026-10-01"},
           "影像报告": "示例MRI文字报告记录海马体积减小，未提供原始影像。"}
    preparation = prepare_patient(raw, config, use_model=False)
    write_json(root / "output/system_refactor/local_model_smoke/current.json", {
        "run_id": preparation.run_id, "config": config.model_dump(mode="json")})
    print(json.dumps({"run_id": preparation.run_id, "status": "prepared"}), flush=True)
    outcome = continue_run(preparation, config)
    _, audit = audit_run(config, preparation.run_id, require_all_agents=True)
    summary = {"run_id": preparation.run_id, "status": outcome.status, "stop_reason": outcome.stop_reason,
               "protocol_passed": audit["protocol_passed"], "errors": audit["errors"],
               "roles": audit["roles"], "calls": outcome.usage["calls"], "report_paths": outcome.report_paths,
               "scope": "synthetic_input_local_model_functional_check_no_clinical_accuracy_claim"}
    write_json(root / "output/system_refactor/local-model-smoke-result.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not audit["protocol_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
