"""Foundation CLI. 'prepare' does not claim to diagnose or run future agents."""
import argparse
import json
from pathlib import Path
from .config import load_config
from .service import create_context, prepare_patient
from .storage import RunStore
from .llm import ModelGateway
from .diagnostics import probe_capabilities
from .retrieval import KnowledgeService, pin_release
from .schemas import RetrievalRequest
from .utils import write_json


def main():
    parser = argparse.ArgumentParser(description="NeuroGRA clinical agents")
    parser.add_argument("--config", default="configs/clinical.default.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--patient", required=True)
    resume = sub.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    narrative = sub.add_parser('write-narrative')
    narrative.add_argument('--run-id', required=True)
    sub.add_parser('validate-diamond')
    sub.add_parser('build-vector-index')
    evaluation = sub.add_parser('evaluate-retrieval')
    evaluation.add_argument('--queries', required=True)
    evaluation.add_argument('--top-k', type=int, default=5)
    audit = sub.add_parser('audit-run')
    audit.add_argument('--run-id', required=True)
    audit.add_argument('--require-all-agents', action='store_true')
    probe = sub.add_parser("probe")
    probe.add_argument("--model-smoke", action="store_true")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--patient", required=True)
    prepare.add_argument("--no-model", action="store_true")
    prepare.add_argument("--query")
    search = sub.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--term", action="append", default=[])
    inspect = sub.add_parser("inspect-run")
    inspect.add_argument("--run-id", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.command == 'write-narrative':
        from .narrative import write_existing_report_narrative
        print(json.dumps(write_existing_report_narrative(args.run_id, config), ensure_ascii=False, indent=2))
    elif args.command == 'audit-run':
        from .acceptance import audit_run
        path, result = audit_run(config, args.run_id, args.require_all_agents)
        print(json.dumps({'artifact_path': str(path), 'protocol_passed': result['protocol_passed'],
                          'checks': len(result['checks']), 'errors': result['errors']}, ensure_ascii=False, indent=2))
        if not result['protocol_passed']:
            raise SystemExit(1)
    elif args.command == 'evaluate-retrieval':
        from .evaluation import evaluate_retrieval
        path, result = evaluate_retrieval(config, args.queries, args.top_k)
        print(json.dumps({'artifact_path': str(path), 'run_id': result['run_id'], 'aggregate': result['aggregate'],
                          'annotation_status': result['annotation_status']}, ensure_ascii=False, indent=2))
    elif args.command == 'build-vector-index':
        from .vector import build_index
        path, manifest = build_index(config, pin_release(config))
        print(json.dumps({'index_manifest': str(path), 'index_id': manifest.index_id,
                          'dimension': manifest.dimension, 'chunks': len(manifest.chunk_ids)}, ensure_ascii=False, indent=2))
    elif args.command == 'validate-diamond':
        from .diamond import model_profile, run_process
        profile = model_profile(config)
        directory = config.resolve(config.output_root) / 'diamond_validation' / profile.fingerprint
        path = directory / 'environment.json'
        code = run_process([config.resolve(config.diamond_python), Path(__file__).with_name('diamond_probe_worker.py'),
                            '--root', config.resolve(config.diamond_root), '--checkpoint', config.resolve(config.diamond_checkpoint),
                            '--output', path], config.diamond_timeout_seconds, directory / 'probe.log')
        write_json(directory / 'model_profile.json', profile)
        print(json.dumps({'exit_code': code, 'validation_status': 'blocked_validation',
                          'environment_report': str(path), 'profile': str(directory / 'model_profile.json')}, ensure_ascii=False, indent=2))
    elif args.command in {"analyze", "resume"}:
        from .workflow import analyze_patient, resume_run
        result = (analyze_patient(json.loads(Path(args.patient).read_text(encoding="utf-8-sig")), config)
                  if args.command == "analyze" else resume_run(args.run_id, config))
        print(json.dumps({"run_id": result.run_id, "status": result.status,
                          "report_paths": result.report_paths, "stop_reason": result.stop_reason}, ensure_ascii=False, indent=2))
    elif args.command == "prepare":
        raw = json.loads(Path(args.patient).read_text(encoding="utf-8-sig"))
        result = prepare_patient(raw, config, not args.no_model, args.query)
        print(json.dumps({"run_id": result.run_id, "status": result.status,
                          "plan_mode": result.plan.mode, "roles": [t.agent_type for t in result.plan.tasks],
                          "observations": len(result.snapshot.observations), "artifact_path": result.artifact_path,
                          "knowledge_release_id": result.knowledge_release_id,
                          "backend_status": result.evidence.backend_status if result.evidence else None},
                         ensure_ascii=False, indent=2))
    elif args.command == "inspect-run":
        print(json.dumps(RunStore(config.resolve(config.database)).summary(args.run_id), ensure_ascii=False, indent=2))
    else:
        store, run_id = create_context(config)
        try:
            if args.command == "probe":
                gateway = ModelGateway(config.model, store, run_id) if args.model_smoke else None
                result = probe_capabilities(config, gateway)
            else:
                result = KnowledgeService(config, pin_release(config), store, run_id).search_knowledge(
                    RetrievalRequest(request_id="cli_search", question=args.query, candidate_terms=args.term))
            path = config.resolve(config.output_root) / run_id / (args.command + ".json")
            write_json(path, result)
            store.status(run_id, "completed")
            print(json.dumps({"run_id": run_id, "artifact_path": str(path), "result": result.model_dump(mode="json")},
                             ensure_ascii=False, indent=2))
        except Exception:
            store.status(run_id, "failed")
            raise


if __name__ == "__main__":
    main()
