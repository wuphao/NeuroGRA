"""Command line interface for module-one knowledge construction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from neurogra.knowledge.config import dump_schema, load_config, normalize_config_dict
from neurogra.knowledge.storage.sqlite import init_store

EXIT_SUCCESS = 0
EXIT_CONFIG = 2
EXIT_DEPENDENCY = 3
EXIT_VALIDATION = 4
EXIT_BLOCKED = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m neurogra.knowledge.cli")
    parser.add_argument(
        "--config",
        default="configs/knowledge.default.yaml",
        help="Path to module-one YAML configuration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config-check", help="Validate config and print JSON.")
    subparsers.add_parser("init-store", help="Initialize SQLite authoritative store.")

    schema_parser = subparsers.add_parser("schema-export", help="Export JSON Schema files.")
    schema_parser.add_argument(
        "--out",
        default="data/knowledge/schemas/build_config.schema.json",
        help="Output path for BuildConfig JSON Schema.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ValidationError as exc:
        _print_error("validation_error", exc.errors(include_url=False))
        return EXIT_CONFIG
    except ValueError as exc:
        _print_error("config_error", str(exc))
        return EXIT_CONFIG
    except OSError as exc:
        _print_error("dependency_or_filesystem_error", str(exc))
        return EXIT_DEPENDENCY


def _dispatch(args: argparse.Namespace) -> int:
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "config-check": _config_check,
        "init-store": _init_store,
        "schema-export": _schema_export,
    }
    return commands[args.command](args)


def _config_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    payload = {
        "status": "ok",
        "config_hash": config.fingerprint(),
        "config": normalize_config_dict(config),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _init_store(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with init_store(config) as repo:
        payload = {
            "status": "ok",
            "database": str(repo.db_path),
            "migrations": repo.migration_versions(),
            "tables": repo.table_names(),
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_SUCCESS


def _schema_export(args: argparse.Namespace) -> int:
    load_config(args.config)
    output_path = Path(args.out)
    dump_schema(output_path)
    print(json.dumps({"status": "ok", "schema": str(output_path)}, ensure_ascii=False))
    return EXIT_SUCCESS


def _print_error(code: str, detail: object) -> None:
    print(json.dumps({"status": "error", "code": code, "detail": detail}, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
