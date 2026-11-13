"""Code-owned specialist capabilities and deterministic coverage selection."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    agent_type: str
    capabilities: frozenset[str]
    input_domains: frozenset[str]
    permitted_tools: frozenset[str]
    input_schema: str = 'AgentTask+CaseSnapshot'
    output_schema: str = 'AgentResult'
    resource: str = 'model'
    version: str = '1'
    priority: int = 100


REGISTRY = (
    Capability('history', frozenset({'history'}), frozenset({'history', 'background'}), frozenset()),
    Capability('cognition', frozenset({'cognition'}), frozenset({'cognition', 'background'}), frozenset()),
    Capability('laboratory', frozenset({'laboratory'}), frozenset({'laboratory', 'background'}), frozenset()),
    Capability('imaging', frozenset({'imaging'}), frozenset({'imaging', 'background'}), frozenset()),
)

EXPLICIT = {
    'history': re.compile(r'病史|既往史|家族史|用药史|起病经过'),
    'cognition': re.compile(r'认知|量表|(?<![A-Za-z0-9])(?:moca|mmse|cdr)(?![A-Za-z0-9])', re.I),
    'laboratory': re.compile(r'检验|化验|血清|脑脊液|生物标志物|(?<![A-Za-z0-9])(?:apoe|aβ|ptau)(?![A-Za-z0-9])', re.I),
    'imaging': re.compile(r'影像|磁共振|脑萎缩|(?<![A-Za-z0-9])(?:mri|pet|ct)(?![A-Za-z0-9])', re.I),
}


def explicitly_requested(text):
    return {name for name, pattern in EXPLICIT.items() if pattern.search(text)}


def assign_capabilities(required, already_covered, available_roles, enabled_roles):
    """Greedily cover explicit requirements, with stable priority/name tie-breaking."""
    remaining = set(required) - set(already_covered)
    candidates = [item for item in REGISTRY if item.agent_type in available_roles
                  and item.agent_type in enabled_roles]
    selected = []
    while remaining:
        ranked = sorted(candidates, key=lambda item: (-len(item.capabilities & remaining),
                                                       item.priority, item.agent_type))
        if not ranked or not (ranked[0].capabilities & remaining):
            break
        choice = ranked[0]
        selected.append(choice.agent_type)
        remaining.difference_update(choice.capabilities)
        candidates.remove(choice)
    return selected, remaining
