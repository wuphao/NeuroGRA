"""Standalone knowledge service commands."""

import argparse
import json

from .config import load_config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="neurogra-kg")
    commands = parser.add_subparsers(dest="command", required=True)
    probe_command = commands.add_parser("probe", help="Check the configured local models")
    probe_command.add_argument("--config", default="configs/kg.local.yaml")
    probe_command.add_argument("--installed-only", action="store_true")
    for name in ["build", "inspect-build", "publish", "retrieve", "inspect-query", "resume-query", "inspect-evidence", "train-complex"]:
        command = commands.add_parser(name)
        command.add_argument("--config", default="configs/kg.local.yaml")
        if name == "build":
            command.add_argument("--manifest", required=True)
            command.add_argument("--build-id")
        elif name in {"inspect-build", "publish"}:
            command.add_argument("--build-id", required=True)
        elif name == "retrieve":
            command.add_argument("--request", required=True)
        elif name in {"inspect-query", "resume-query"}:
            command.add_argument("--run-id", required=True)
        elif name == "train-complex":
            command.add_argument("--release-id")
        else:
            command.add_argument("--evidence-id", required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == "probe":
        from .diagnostics import probe
        result = probe(config, execute=not args.installed_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] or args.installed_only else 1
    from pathlib import Path
    from .api import KnowledgeGraphService
    from .contracts.domain import BuildRequest, RetrievalRequest
    with KnowledgeGraphService(config) as kg:
        if args.command == "build":
            payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            if args.build_id:
                payload["build_id"] = args.build_id
            result = kg.build(BuildRequest.model_validate(payload))
        elif args.command == "inspect-build":
            result = kg.inspect_build(args.build_id)
        elif args.command == "publish":
            result = kg.publish(args.build_id)
        elif args.command == "retrieve":
            result = kg.retrieve(RetrievalRequest.model_validate_json(Path(args.request).read_text(encoding="utf-8")))
        elif args.command == "inspect-query":
            result = kg.inspect_query(args.run_id)
        elif args.command == "resume-query":
            result = kg.resume_query(args.run_id)
        elif args.command == "train-complex":
            result = kg.train_complex(args.release_id)
        else:
            result = kg.get_evidence(args.evidence_id)
    print(json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result,
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
