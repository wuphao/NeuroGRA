"""Convert a model-proposed tree into checked executable domain conditions."""

import re

from ..catalogs import FIELDS
from ..contracts.domain import AllCondition, AnyCondition, AtomCondition, NotCondition, TextCondition
from .sources import resolve_quotes
from ..contracts.tasks import LogicOutput, LogicAtom, LogicText, LogicGroup
from ..utils import identity
from ..terminology import canonical_field, canonical_value


def number_logic(draft):
    """Flatten a nested content tree; the code owns every node and child ID."""
    nodes = []
    def visit(node, path):
        if len(path) > 16 or len(nodes) >= 128:
            raise ValueError("logic_tree_over_budget")
        node_id = identity("logic_node", [draft.statement_id, path, node.model_dump(mode="json")])
        if node.kind in {"ALL", "ANY", "NOT"}:
            # Reserve before descending to enforce the bound even for nested groups.
            index = len(nodes)
            nodes.append(None)
            children = [visit(child, [*path, i]) for i, child in enumerate(node.children)]
            nodes[index] = LogicGroup(node_id=node_id, kind=node.kind, child_ids=children)
        else:
            cls = LogicAtom if node.kind == "ATOM" else LogicText
            nodes.append(cls(node_id=node_id, **node.model_dump()))
        return node_id
    root_id = visit(draft.root, []) if draft.root is not None else None
    return LogicOutput(statement_id=draft.statement_id, root_id=root_id, nodes=nodes,
        atom_mappings={}, unresolved_items=draft.unresolved_items, logic_evidence=draft.logic_evidence)


def assemble_logic(output, statement_id, conditions, blocks, field_catalog=None):
    if output.statement_id != statement_id:
        raise ValueError("logic_statement_id_mismatch")
    allowed = {condition.local_id: condition for condition in conditions}
    fields = FIELDS if field_catalog is None else field_catalog
    seen = set()
    nodes = {node.node_id: node for node in output.nodes}
    if len(nodes) != len(output.nodes):
        raise ValueError("duplicate_logic_node_id")
    visited = set()
    active = set()

    def convert(node, negated=False):
        if node.node_id in active:
            raise ValueError("cyclic_condition_tree")
        if node.node_id in visited:
            raise ValueError("shared_node_is_not_a_condition_tree")
        visited.add(node.node_id)
        active.add(node.node_id)
        if node.kind in {"ALL", "ANY", "NOT"}:
            if any(id not in nodes for id in node.child_ids):
                raise ValueError("unknown_logic_child_id")
            children = [nodes[id] for id in node.child_ids]
        else:
            children = []
        if node.kind in {"ALL", "ANY"}:
            if len(children) < 2:
                raise ValueError("invalid_logical_group")
            converted = [convert(child, negated) for child in children]
            active.remove(node.node_id)
            return (AllCondition if node.kind == "ALL" else AnyCondition)(children=converted)
        if node.kind == "NOT":
            if len(children) != 1:
                raise ValueError("invalid_not_group")
            converted = convert(children[0], not negated)
            active.remove(node.node_id)
            return NotCondition(child=converted)
        if node.condition_id not in allowed:
            raise ValueError("invalid_condition_leaf_reference")
        seen.add(node.condition_id)
        refs = resolve_quotes(node.source_refs, blocks)
        candidate_refs = resolve_quotes(allowed[node.condition_id].source_refs, blocks)
        if not refs or any(not any(ref.block_id == origin.block_id and ref.start < origin.end and origin.start < ref.end
                                  for origin in candidate_refs) for ref in refs):
            raise ValueError("condition_leaf_source_out_of_scope")
        if node.kind == "TEXT":
            active.remove(node.node_id)
            return TextCondition(condition_id=node.condition_id, text=node.text or allowed[node.condition_id].condition_text,
                                 source_refs=refs, reason="not_reliably_structured")
        field = canonical_field(node.field)
        if field not in fields or node.operator is None or node.value is None:
            raise ValueError("condition_field_or_operator_unrecognized")
        candidate = allowed[node.condition_id]
        if (re.search(r"人群|\bpopulation\b|\bcohort\b", candidate.condition_text, re.I)
                and field != "population" and not node.population
                and not any(other.kind == "TEXT" and other.condition_id == node.condition_id
                    or other.kind == "ATOM" and other.condition_id == node.condition_id
                        and (canonical_field(other.field) == "population" or other.population)
                    for other in output.nodes)):
            raise ValueError("population_qualifier_omitted")
        if (candidate.category == "threshold" and field != "age" and not node.method
                and any(condition.category == "method" for condition in conditions)):
            raise ValueError("threshold_method_binding_omitted")
        if re.search(r"不小于|不少于|至少|大于等于", candidate.condition_text) and (node.operator != "gte" or negated):
            raise ValueError("inclusive_lower_bound_polarity_mismatch")
        lower_bounds = re.findall(r"(?:at least|not less than|greater than or equal to)\s*([+-]?\d+(?:\.\d+)?)", candidate.condition_text, re.I)
        if (node.operator in {"gt", "gte", "lt", "lte"} and isinstance(node.value, (int, float))
                and any(float(bound) == node.value for bound in lower_bounds) and (node.operator != "gte" or negated)):
            raise ValueError("inclusive_lower_bound_polarity_mismatch")
        if (candidate.category == "exclusion" and fields[field].get("true_means") == "present"
                and node.operator in {"eq", "ne"} and isinstance(node.value, bool)
                and re.search(r"(?:必须|应|需).*排除|排除.*因素|must exclude", candidate.condition_text, re.I)):
            accepted_presence = node.value if node.operator == "eq" else not node.value
            if negated:
                accepted_presence = not accepted_presence
            if accepted_presence:
                raise ValueError("exclusion_boolean_double_negation")
        active.remove(node.node_id)
        return AtomCondition(condition_id=node.condition_id, field=field, operator=node.operator,
                             value=canonical_value(field, node.value), unit=node.unit, method=node.method,
                             time_scope=node.time_scope, population=node.population, source_refs=refs)

    if output.root_id is not None and output.root_id not in nodes:
        raise ValueError("unknown_logic_root")
    tree = convert(nodes[output.root_id]) if output.root_id else None
    if set(nodes) != visited:
        raise ValueError("unreachable_logic_nodes")
    if not set(output.unresolved_items) <= set(allowed):
        raise ValueError("unknown_unresolved_condition_id")
    if seen | set(output.unresolved_items) != set(allowed):
        raise ValueError("selected_condition_omitted")
    resolve_quotes(output.logic_evidence, blocks)
    if tree is not None and output.unresolved_items:
        # Partial tree cannot represent the complete applicability contract.
        return None
    return tree
