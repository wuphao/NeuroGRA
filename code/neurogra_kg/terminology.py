"""Shared conservative bilingual mappings for knowledge, facts and retrieval."""
import re
import unicodedata

from .catalogs import FIELD_ALIASES, METHOD_ALIASES, POPULATION_ALIASES, VALUE_ALIASES, TERMINOLOGY
from .utils import identity, mentions_term


def normalized_label(text):
    return re.sub(r"[\s'’]", "", unicodedata.normalize("NFKC", text)).casefold()


def canonical_label(value, aliases):
    if not isinstance(value, str):
        return value
    matches = [key for key, names in aliases.items()
               if normalized_label(value) in {normalized_label(n) for n in [key, *names]}]
    return matches[0] if len(matches) == 1 else value


def canonical_field(value):
    return canonical_label(value, FIELD_ALIASES)


def canonical_method(value):
    return canonical_label(value, METHOD_ALIASES)


def canonical_population(value):
    return canonical_label(value, POPULATION_ALIASES)


def canonical_value(field, value):
    if isinstance(value, list):
        return [canonical_value(field, item) for item in value]
    return canonical_label(value, VALUE_ALIASES.get(field, {}))


def entity_names(entity):
    return list(dict.fromkeys(n for n in [entity.name, entity.label_zh, entity.label_en, *entity.aliases] if n))


def terminology_binding():
    return identity("terminology", [TERMINOLOGY, FIELD_ALIASES, METHOD_ALIASES, POPULATION_ALIASES, VALUE_ALIASES])


def expand_query(text, entities=()):
    names = [[entry['name'], entry.get('label_zh'), entry.get('label_en'), *entry.get('aliases', [])]
             for entry in TERMINOLOGY]
    names.extend(entity_names(entity) for entity in entities)
    extra = []
    for group in names:
        group = [name for name in group if name]
        if any(mentions_term(text, name) for name in group):
            extra.extend(group)
    return text + (" " + " ".join(dict.fromkeys(extra)) if extra else "")
