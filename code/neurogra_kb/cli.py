import argparse
import json
from contextlib import nullcontext

from neurogra_common.activity import active
from neurogra_common.locking import exclusive

from .api import TextKnowledgeService
from .config import load_config


def main():
    parser = argparse.ArgumentParser(description="Independent text knowledge bank")
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("documents", nargs="+")
    publish.add_argument("--lexical-only", action="store_true")
    query = commands.add_parser("query")
    query.add_argument("question")
    query.add_argument("--release-id")
    query.add_argument("--top-k", type=int, default=5)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("release_id")
    args = parser.parse_args()
    config = load_config(args.config)
    lock = exclusive(config.root, 'model_resource') if args.command in {'publish', 'query'} else nullcontext()
    with active(config.root, timeout=30), lock:
        return run_command(args, config)


def run_command(args, config):
    service = TextKnowledgeService(config)
    if args.command == "publish":
        result = service.publish(args.documents, vector=not args.lexical_only)
    elif args.command == "query":
        result = service.search_text(args.question, release_id=args.release_id, top_k=args.top_k)
    else:
        result = service.get_release(args.release_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
