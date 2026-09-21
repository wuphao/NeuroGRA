from __future__ import annotations

import unittest
from decimal import Decimal

from pydantic import TypeAdapter, ValidationError

from neurogra.knowledge.config import BuildConfig
from neurogra.knowledge.schemas.graph import AtomCondition, ClauseRevision, ConditionNode
from neurogra.knowledge.schemas.source import Document, EvidenceRef
from neurogra.knowledge.storage.sqlite import init_store


def evidence() -> EvidenceRef:
    return EvidenceRef(span_id="span_1", start=0, end=1, quote="A")


class S01ContractTests(unittest.TestCase):
    def test_config_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            BuildConfig.model_validate({"schema_version": "1", "unexpected": True})

    def test_document_rejects_invalid_enum(self) -> None:
        with self.assertRaises(ValidationError):
            Document(
                document_id="doc_1",
                checksum="abc",
                source_path="source.pdf",
                snapshot_path="snapshot.pdf",
                source_role="blog",
            )

    def test_condition_tree_rejects_single_child_and(self) -> None:
        adapter = TypeAdapter(ConditionNode)
        with self.assertRaises(ValidationError):
            adapter.validate_python(
                {
                    "kind": "AND",
                    "children": [
                        {
                            "kind": "ATOM",
                            "field": "finding_a",
                            "operator": "eq",
                            "value": True,
                            "evidence_refs": [evidence().model_dump()],
                        }
                    ],
                }
            )

    def test_numeric_operator_requires_decimal(self) -> None:
        with self.assertRaises(ValidationError):
            AtomCondition(
                field="score",
                operator="gt",
                value="3",
                evidence_refs=[evidence()],
            )
        condition = AtomCondition(
            field="score",
            operator="gt",
            value=Decimal("3"),
            evidence_refs=[evidence()],
        )
        self.assertEqual(condition.value, Decimal("3"))

    def test_clause_rejects_incompatible_direction(self) -> None:
        with self.assertRaises(ValidationError):
            ClauseRevision(
                clause_id="clause_1",
                revision_id="clause_1:1",
                candidate_id="candidate_1",
                subject_entity_id="entity_a",
                object_entity_id="entity_b",
                predicate="supports",
                direction="weakening",
                modality="recommended",
                condition_executable=True,
                evidence_refs=[evidence()],
                assertion_text="A supports B.",
                extraction_run_id="run_1",
            )

    def test_init_store_creates_empty_database(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = BuildConfig.model_validate(
                {
                    "schema_version": "1",
                    "paths": {
                        "data_root": str(tmp_path / "data"),
                        "output_root": str(tmp_path / "out"),
                    },
                    "database": {"path": str(tmp_path / "data" / "registry.sqlite")},
                }
            )
            with init_store(config) as repo:
                self.assertEqual(repo.migration_versions(), [1])
                tables = set(repo.table_names())
                expected = {"documents", "spans", "chunks", "clause_revisions", "active_release"}
                self.assertLessEqual(expected, tables)


if __name__ == "__main__":
    unittest.main()
