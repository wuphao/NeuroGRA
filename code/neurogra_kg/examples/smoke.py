"""Real local-model query smoke against the synthetic example release.

The in-memory graph is deliberate; this script does not verify Neo4j.
Run with an already built/published synthetic example and PYTHONPATH=code.
"""

from datetime import datetime, timezone

from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.config import load_config
from neurogra_kg.contracts.domain import PatientFact, RetrievalRequest, SourceRef
from neurogra_kg.utils import atomic_json


def example_fact(field, value, unit=None, method=None):
    text = f"合成记录：{field}={value};unit={unit};method={method}"
    return PatientFact(fact_id="example-" + field, field=field, value=value, unit=unit, method=method,
                      source_ref=SourceRef(block_id="record-" + field, start=0, end=len(text), quote=text))


def main():
    config = load_config("configs/kg.local.yaml")
    config.graph.provider = "memory"
    base = [example_fact("age", 72, "year"), example_fact("indicator_x", 12, method="示例方法 M"),
            example_fact("population", "示例人群")]
    results = []
    with KnowledgeGraphService(config) as kg:
        for label, facts, expected in [
            ("complete", base + [example_fact("exclusion_e", False)], "SATISFIED"),
            ("missing_exclusion", base, "UNKNOWN"),
            ("violated_exclusion", base + [example_fact("exclusion_e", True)], "VIOLATED"),
        ]:
            request = RetrievalRequest(facts=facts, purpose="biomarker_interpretation",
                question="指标 X 是否支持状态 D", reference_time=datetime(2026, 10, 10, tzinfo=timezone.utc),
                max_supplement_rounds=0, max_seconds=300)
            result = kg.retrieve(request)
            items = [*result.applicable, *result.unknown, *result.excluded]
            row = {"case": label, "expected": expected, "states": [item.state for item in items],
                   "degraded": result.degraded, "run_id": result.run_id,
                   "data_requests": [item.model_dump(mode="json") for item in result.data_requests]}
            row["passed"] = bool(items) and all(item.state == expected for item in items) and not result.degraded
            results.append(row)
            print(row, flush=True)
        atomic_json(config.resolve(config.paths.output_root) / "engineering_query_smoke.json", results)
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
