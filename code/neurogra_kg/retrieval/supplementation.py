"""Bounded source lookup; temporary statements never overwrite a pinned release."""

from ..catalogs import TERMINOLOGY
from ..contracts.domain import ParsedDocument, Document, KnowledgeStatement
from ..contracts.tasks import PlanningOutput
from ..storage.indexes import lexical_search
from ..utils import identity
from ..construction.pipeline import ConstructionPipeline
from ..terminology import expand_query
from .gaps import identify_gaps
from .ranking import rank_evidence


def same_source_assertion(left, right):
    if (left.subject.name, left.predicate, left.object.name) != (right.subject.name, right.predicate, right.object.name):
        return False
    if not ({ref.block_id for ref in left.source_refs} & {ref.block_id for ref in right.source_refs}):
        return False
    if left.assertion_text == right.assertion_text:
        return True
    # Exact core-field positions distinguish separate occurrences in one paragraph.
    for field in ["subject", "relation", "object"]:
        positions = lambda statement: {(ref.block_id, ref.start, ref.end, ref.quote)
            for ref in statement.field_evidence.get(field, [])}
        if not positions(left) & positions(right):
            return False
    return True


def supplement(result, request, config, corpus_data, blocks, resources, llm, output_root, budget, candidates=None,
               resume_state=None, on_checkpoint=None):
    saved = resume_state or {}
    seen_queries = set(saved.get("seen_queries", []))
    seen_gaps = set(saved.get("seen_gaps", []))
    seen_content = set(saved.get("seen_content", []))
    temporary = {row["statement_id"]: KnowledgeStatement.model_validate(row) for row in saved.get("temporary", [])}
    original_items = {item.statement.statement_id: item for item in [*result.applicable, *result.unknown, *result.excluded]}
    statements = {id: item.statement for id, item in original_items.items()}
    documents = {row["document_id"]: Document.model_validate(row) for row in corpus_data["documents"]}
    edges = corpus_data["edges"]
    rounds = min(request.max_supplement_rounds, config.retrieval.max_supplement_rounds)
    def checkpoint(round_index):
        if on_checkpoint:
            on_checkpoint(result, {"round_index": round_index, "seen_queries": sorted(seen_queries),
                "seen_gaps": sorted(seen_gaps), "seen_content": sorted(seen_content),
                "temporary": [statement.model_dump(mode="json") for statement in temporary.values()]})
    for round_index in range(saved.get("round_index", 0), rounds):
        unresolved = [gap for gap in result.gaps if gap.kind != "patient_data" and gap.status == "open"
                      and gap.gap_id not in seen_gaps]
        if not unresolved:
            break
        any_added = False
        for gap in unresolved:
            checkpoint(round_index)
            try:
                budget.remaining()
                seen_gaps.add(gap.gap_id)
                related = statements.get(gap.statement_id)
                selected_blocks = set(ref.block_id for ref in related.source_refs) if related else set()
                expanded = set(selected_blocks)
                for edge in edges:
                    if edge["source"] in selected_blocks:
                        expanded.add(edge["target"])
                    if edge["target"] in selected_blocks:
                        expanded.add(edge["source"])
                queries = []
                # Deterministic structural lookup is attempted before semantic planning.
                if not expanded or expanded == selected_blocks:
                    plan = llm.execute("L6", {"gap": gap.model_dump(mode="json"),
                        "related_statement": related.model_dump(mode="json") if related else None,
                        "candidate_edge": candidates or None, "terminology": TERMINOLOGY,
                        "allowed_corpus": list(documents), "previous_queries": sorted(seen_queries)}, PlanningOutput, budget)
                    if plan.gap_id != gap.gap_id:
                        raise ValueError("supplement_gap_id_mismatch")
                    for query in plan.queries:
                        if not set(query.document_scope) <= set(documents):
                            raise ValueError("supplement_corpus_scope_violation")
                        if query.text in seen_queries:
                            continue
                        seen_queries.add(query.text)
                        queries.append(query.text)
                        scope = set(query.document_scope) or set(documents)
                        scoped_resources = [row for row in resources if row["id"].startswith("block:")
                            and blocks[row["id"].split(":", 1)[1]].document_id in scope]
                        expanded.update(id.split(":", 1)[1] for id, score in lexical_search(scoped_resources, query.text, 5))
                new_ids = []
                for document_id in sorted({blocks[id].document_id for id in expanded if id in blocks}):
                    selected = sorted([blocks[id] for id in expanded if id in blocks and blocks[id].document_id == document_id],
                                      key=lambda block: (block.page, block.order, block.block_id))
                    # Do not repeat an identical extraction context indefinitely.
                    content_key = identity("context", [block.model_dump(mode="json") for block in sorted(selected, key=lambda b: b.block_id)])
                    if content_key in seen_content:
                        continue
                    seen_content.add(content_key)
                    from ..contracts.domain import StructureEdge
                    parsed = ParsedDocument(document=documents[document_id], blocks=selected,
                        edges=[StructureEdge.model_validate(edge) for edge in edges if edge["source"] in expanded and edge["target"] in expanded],
                        parser="source_supplement", parser_version="1")
                    new_statements, issues = ConstructionPipeline(config.construction, llm).run(parsed,
                        output_root / "supplements" / result.run_id / content_key, budget)
                    if issues:
                        result.supplementation_trace.append({"round": round_index + 1,
                            "gap_id": gap.gap_id, "status": "extraction_issues", "issues": issues})
                    if gap.kind == "graph_connection" and candidates:
                        from ..completion.verification import verify_candidate
                        verified = {}
                        for candidate in candidates:
                            supported = verify_candidate(candidate, new_statements, blocks)
                            if supported:
                                candidate["status"] = "source_verified"
                                candidate["statement_ids"] = [statement.statement_id for statement in supported]
                            verified.update((statement.statement_id, statement) for statement in supported)
                        new_statements = list(verified.values())
                    for statement in new_statements:
                        if statement.status not in {"approved", "source_checked"}:
                            continue
                        if statement.zh_annotation and statement.zh_annotation.status != "verified":
                            warning = "chinese_expression_unavailable:" + statement.statement_id
                            if warning not in result.warnings:
                                result.warnings.append(warning)
                            result.degraded = True
                        # A source-checked reconstruction of this exact assertion can
                        # refine query-local conditions while keeping its stable identity.
                        if related and statement.status == "approved" and same_source_assertion(statement, related):
                            statement = statement.model_copy(update={"statement_id": related.statement_id})
                        original = statements.get(statement.statement_id)
                        if original is not None and original.model_dump(mode="json") == statement.model_dump(mode="json"):
                            continue
                        temporary[statement.statement_id] = statement
                        new_ids.append(statement.statement_id)
                result.supplementation_trace.append({"round": round_index + 1, "gap_id": gap.gap_id,
                    "queries": queries, "source_block_ids": sorted(expanded), "new_statement_ids": new_ids,
                    "status": "temporary_evidence" if new_ids else "no_new_evidence"})
                if new_ids:
                    any_added = True
                    gap.status = "pending_review"
                else:
                    gap.status = "unresolved"
            except TimeoutError:
                # Keep the last completed gap checkpoint; the caller journals an interruption.
                raise
            except Exception as exc:
                if isinstance(exc, ValueError) and hasattr(llm, "invalidate_last"):
                    llm.invalidate_last(exc)
                gap.status = "unresolved"
                result.degraded = True
                result.warnings.append("supplementation_failed:" + type(exc).__name__)
                result.supplementation_trace.append({"round": round_index + 1, "gap_id": gap.gap_id,
                    "status": "failed", "error_type": type(exc).__name__})
        if temporary:
            merged = {**statements, **temporary}
            from ..storage.releases import statement_text
            retrieved = lexical_search([{"id": id, "text": statement_text(statement)}
                for id, statement in temporary.items()], expand_query(request.question,
                    [e for s in temporary.values() for e in [s.subject, s.object]]), len(temporary))
            maximum = max((score for _, score in retrieved), default=1)
            scores = {id: original_items[id].score_components["relevance"] for id in merged if id in original_items}
            scores.update({id: score / maximum for id, score in retrieved})
            matched = {id: original_items[id].matched_by if id in original_items and id not in temporary else ["supplement"] for id in merged}
            groups = rank_evidence(list(merged.values()), scores, matched, request, result.release_id, llm, budget, limit=False)
            for items in groups.values():
                for item in items:
                    item.temporary = item.statement.statement_id in temporary
            result.applicable, result.unknown, result.excluded = (groups[state][:request.top_k]
                for state in ["SATISFIED", "UNKNOWN", "VIOLATED"])
            graph_statements = dict(statements)
            graph_statements.update({row["statement_id"]: KnowledgeStatement.model_validate(row)
                for row in corpus_data.get("statements", [])})
            graph_statements.update(temporary)
            new_gaps, requests = identify_gaps(groups, request.question, list(graph_statements.values()))
            old_gaps = {gap.gap_id: gap for gap in result.gaps}
            new_ids = {gap.gap_id for gap in new_gaps}
            for id, gap in old_gaps.items():
                evaluated = {item.statement.statement_id for group in groups.values() for item in group}
                if id not in new_ids and gap.kind != "patient_data" and (
                    gap.statement_id in evaluated or gap.kind == "graph_connection" and bool(groups["SATISFIED"] or groups["UNKNOWN"])):
                    gap.status = "resolved"
            result.gaps = list({**old_gaps, **{gap.gap_id: gap for gap in new_gaps}}.values())
            result.data_requests = requests
            statements = merged
        checkpoint(round_index + 1)
        if not any_added:
            break
    return result
