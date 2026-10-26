"""Post-verification Chinese expressions never replace source text or logic."""
import re
import unicodedata

from ..contracts.domain import ChineseAnnotation
from ..contracts.tasks import LocalizationOutput, LocalizationVerification
from ..utils import identity
from .sources import resolve_quotes


def text_language(text):
    chinese = bool(re.search(r"[\u3400-\u9fff]", text))
    english = bool(re.search(r"[A-Za-z]{2,}", text))
    return "mixed" if chinese and english else "zh" if chinese else "en" if english else "und"


def localized_text(text, annotation):
    return annotation.text if annotation and annotation.status == "verified" and annotation.text.strip() else text


def localization_targets(statement):
    targets = [("assertion", statement, statement.assertion_text, statement.source_refs)]
    def visit(condition, path):
        if condition is None:
            return
        if condition.kind in {"ALL", "ANY", "NOT"}:
            children = [condition.child] if condition.kind == "NOT" else condition.children
            for index, child in enumerate(children):
                visit(child, path + "/" + str(index))
        elif condition.kind == "TEXT":
            targets.append((path, condition, condition.text, condition.source_refs))
        # ATOM explanations are rendered from checked fields, not retranslated logic.
    visit(statement.condition, "condition")
    return targets


def number_signature(text):
    return sorted(re.findall(r"[+-]?\d+(?:\.\d+)?(?:%|‰)?", unicodedata.normalize("NFKC", text)))


def annotate_statement(statement, blocks, llm, budget):
    statement.assertion_language = text_language(statement.assertion_text)
    if statement.status not in {"approved", "source_checked"}:
        return statement
    targets = localization_targets(statement)
    pending = []
    for target, owner, text, refs in targets:
        if owner.zh_annotation and owner.zh_annotation.status == "verified":
            continue
        if text_language(text) == "zh":
            # Existing Chinese extracted expression was already checked by L4.
            owner.zh_annotation = ChineseAnnotation(text=text, status="verified", generator_binding={"kind": "source_checked_expression"})
        else:
            pending.append((target, owner, text, refs))
    if not pending:
        return statement
    payload_targets = [{"target": key, "text": text, "source_refs": [r.model_dump(mode="json") for r in refs]}
                       for key, _, text, refs in pending]
    referenced = {ref.block_id for _, _, _, refs in pending for ref in refs}
    source_blocks = [blocks[key].model_dump(mode="json") for key in sorted(referenced)]
    payload = {"targets": payload_targets, "source_blocks": source_blocks}
    generator_cache = None
    def validate_refs(target, refs):
        resolved = resolve_quotes(refs, blocks)
        origins = next(origins for key, _, _, origins in pending if key == target)
        if not resolved or any(not any(r.block_id == original.block_id and
            original.start <= r.start and r.end <= original.end for original in origins) for r in resolved):
            raise ValueError("localization_quote_out_of_scope")
    try:
        binding = llm.binding() if hasattr(llm, "binding") else {"adapter": type(llm).__name__}
        output = llm.execute("L7", payload, LocalizationOutput, budget)
        generator_cache = getattr(llm, "last_cache_path", None)
        if not isinstance(output, LocalizationOutput):
            raise ValueError("localization_invalid_output")
        expected = {key for key, _, _, _ in pending}
        if len(output.items) != len(expected) or {item.target for item in output.items} != expected:
            raise ValueError("localization_targets_mismatch")
        translated = {item.target: item for item in output.items}
        for key, _, text, _ in pending:
            item = translated[key]
            if text_language(item.text_zh) not in {"zh", "mixed"}:
                raise ValueError("localization_not_chinese")
            if number_signature(text) != number_signature(item.text_zh):
                raise ValueError("localization_numeric_change")
            if (re.search(r"\bexplicitly (?:absent|excluded)\b", text, re.I)
                    and re.search(r"缺失|未记录|未记载|未提供|未检查|未查", item.text_zh)):
                raise ValueError("localization_explicit_absence_became_missing")
            validate_refs(key, item.source_refs)
        verification = llm.execute("L8", {**payload, "translations": output.model_dump(mode="json")}, LocalizationVerification, budget)
        if not isinstance(verification, LocalizationVerification):
            raise ValueError("localization_invalid_verification")
        if len(verification.checks) != len(expected) or {c.target for c in verification.checks} != expected:
            raise ValueError("localization_checks_mismatch")
        for check in verification.checks:
            validate_refs(check.target, check.source_refs)
            if not check.equivalent:
                raise ValueError("localization_semantic_mismatch:" + check.target)
        source_binding = identity("localization_source", payload)
        for key, owner, _, _ in pending:
            owner.zh_annotation = ChineseAnnotation(text=translated[key].text_zh, status="verified",
                generator_binding={"tasks": ["L7", "L8"], "model": binding, "source_binding": source_binding})
    except TimeoutError:
        # Preserve the global construction interruption and resume contract.
        raise
    except Exception as exc:
        if isinstance(exc, ValueError) and hasattr(llm, "invalidate_last"):
            llm.invalidate_last(exc)
            if hasattr(llm, "invalidate_cached"):
                llm.invalidate_cached(generator_cache, exc)
        issue = "localization_failed:" + type(exc).__name__ + ":" + str(exc)[:100]
        for _, owner, _, _ in pending:
            owner.zh_annotation = ChineseAnnotation(status="failed", issues=[issue])
        statement.issues.append(issue)
    return statement
