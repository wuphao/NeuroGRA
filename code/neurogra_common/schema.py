"""Transport grammar normalization; authoritative Pydantic validation stays strict."""

from copy import deepcopy


def grammar_schema(schema):
    """Avoid sampler grammar expansion of bounded arrays/strings on local backends.

Only size keywords are omitted from the decoding grammar. Types, required fields,
enums, const, references and extra-field rejection remain intact. The original
model schema validates every response including its size bounds and ID contract.
"""
    size = {"minItems", "maxItems", "minLength", "maxLength"}
    def visit(value, mapping=False):
        if isinstance(value, list):
            return [visit(child) for child in value]
        if not isinstance(value, dict):
            return value
        if mapping:
            return {name: visit(child) for name, child in value.items()}
        result = {}
        for key, child in value.items():
            if key in size:
                continue
            if key in {"const", "enum", "default"}:
                result[key] = deepcopy(child)
            else:
                result[key] = visit(child, mapping=key in {"properties", "$defs", "definitions"})
        return result
    return visit(schema)
