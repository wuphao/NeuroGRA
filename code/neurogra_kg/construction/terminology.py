"""Exact alias matching, bounded semantic candidates and unresolved ambiguity."""

import re

from ..catalogs import TERMINOLOGY
from ..contracts.domain import Entity
from ..utils import identity
from ..terminology import normalized_label


def norm(text):
    # Preserve historical identity normalization; matching can be more permissive.
    return re.sub(r"[\s'’]", "", text).casefold()


class EntityResolver:
    def __init__(self, terminology=None, embedding=None):
        self.terminology = TERMINOLOGY if terminology is None else terminology
        self.embedding = embedding
        self._vectors = None

    def resolve(self, text, entity_type, budget=None):
        matches = [entry for entry in self.terminology if entry["entity_type"] == entity_type
                   and normalized_label(text) in {normalized_label(alias) for alias in [entry["name"], entry.get("label_zh"), entry.get("label_en"), *entry.get("aliases", [])] if alias}]
        if len(matches) == 1:
            entry = matches[0]
            return self._entity(entry, "resolved")
        candidates = [self._entity(entry, "resolved").entity_id for entry in matches]
        if not matches and self.embedding and self.terminology:
            if self._vectors is None:
                self._vectors = self.embedding.embed([entry["name"] for entry in self.terminology], budget)
            query = self.embedding.embed([text], budget)[0]
            ranked = sorted([(sum(a * b for a, b in zip(query, vector)), entry)
                             for entry, vector in zip(self.terminology, self._vectors)
                             if entry["entity_type"] == entity_type], key=lambda item: -item[0])
            # Semantic similarity proposes candidates, never silently merges entities.
            candidates = [self._entity(entry, "resolved").entity_id for score, entry in ranked[:3] if score >= .75]
        return Entity(entity_id=identity("entity", [norm(text), entity_type]), name=text,
                      entity_type=entity_type, status="ambiguous" if candidates else "local",
                      candidate_ids=candidates)

    @staticmethod
    def _entity(entry, status):
        return Entity(entity_id=identity("entity", [norm(entry["name"]), entry["entity_type"]]),
                      name=entry["name"], entity_type=entry["entity_type"],
                      aliases=entry.get("aliases", []), status=status,
                      label_zh=entry.get("label_zh"), label_en=entry.get("label_en"))
