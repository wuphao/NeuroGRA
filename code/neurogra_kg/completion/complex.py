"""CPU NumPy ComplEx with logistic negative sampling and immutable model binding."""

import json
from pathlib import Path

import numpy as np

from ..utils import atomic_json, checksum, identity


def complex_score(h, r, t):
    hr, hi = h
    rr, ri = r
    tr, ti = t
    return float(np.sum(hr * rr * tr + hi * rr * ti + hr * ri * ti - hi * ri * tr))


def score_gradients(h, r, t):
    hr, hi = h
    rr, ri = r
    tr, ti = t
    return (np.stack([rr * tr + ri * ti, rr * ti - ri * tr]),
            np.stack([hr * tr + hi * ti, hr * ti - hi * tr]),
            np.stack([hr * rr - hi * ri, hi * rr + hr * ri]))


class ComplExModel:
    def __init__(self, entities, relations, entity_embeddings, relation_embeddings, manifest):
        self.entities, self.relations = entities, relations
        self.entity_embeddings, self.relation_embeddings = entity_embeddings, relation_embeddings
        self.manifest = manifest
        self.entity_index = {value: index for index, value in enumerate(entities)}
        self.relation_index = {value: index for index, value in enumerate(relations)}

    def score(self, h, r, t):
        return complex_score(self.entity_embeddings[self.entity_index[h]],
                             self.relation_embeddings[self.relation_index[r]],
                             self.entity_embeddings[self.entity_index[t]])

    @classmethod
    def train(cls, statements, release_id, config):
        approved = [statement for statement in statements if statement.status == "approved"]
        triples = sorted({(statement.subject.entity_id, statement.predicate, statement.object.entity_id) for statement in approved})
        if not triples:
            raise ValueError("complex_requires_approved_statements")
        entities = sorted({entity for h, _, t in triples for entity in [h, t]})
        relations = sorted({r for _, r, _ in triples})
        ei, ri = {id: i for i, id in enumerate(entities)}, {id: i for i, id in enumerate(relations)}
        indexed = [(ei[h], ri[r], ei[t]) for h, r, t in triples]
        known = set(indexed)
        rng = np.random.default_rng(config.seed)
        entity = rng.normal(0, .1, (len(entities), 2, config.dimension))
        relation = rng.normal(0, .1, (len(relations), 2, config.dimension))
        losses = []
        for epoch in range(config.epochs):
            examples = []
            for h, r, t in indexed:
                examples.append((h, r, t, 1.0))
                for _ in range(20):
                    candidate = (int(rng.integers(len(entities))), r, t) if rng.random() < .5 else (h, r, int(rng.integers(len(entities))))
                    if candidate not in known:
                        examples.append((*candidate, -1.0))
                        break
            rng.shuffle(examples)
            loss = 0.0
            for h, r, t, label in examples:
                hv, rv, tv = entity[h].copy(), relation[r].copy(), entity[t].copy()
                score = complex_score(hv, rv, tv)
                loss += float(np.logaddexp(0, -label * score))
                multiplier = -label / (1 + np.exp(np.clip(label * score, -50, 50)))
                gh, gr, gt = score_gradients(hv, rv, tv)
                entity[h] -= config.learning_rate * (multiplier * gh + .00002 * hv)
                relation[r] -= config.learning_rate * (multiplier * gr + .00002 * rv)
                entity[t] -= config.learning_rate * (multiplier * gt + .00002 * tv)
            losses.append(loss / len(examples))
        if not np.isfinite(entity).all() or not np.isfinite(relation).all():
            raise ValueError("complex_training_diverged")
        manifest = {"release_id": release_id, "model_type": "complex", "training_triples": [list(row) for row in triples],
                    "config": config.model_dump(mode="json"), "seed": config.seed,
                    "epochs": config.epochs, "loss_first": losses[0], "loss_last": losses[-1]}
        manifest["model_id"] = identity("complex", [release_id, manifest["config"], triples])
        return cls(entities, relations, entity, relation, manifest)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "embeddings.npz"
        np.savez(path, entity=self.entity_embeddings, relation=self.relation_embeddings)
        manifest = {**self.manifest, "entities": self.entities, "relations": self.relations, "checksum": checksum(path)}
        atomic_json(directory / "manifest.json", manifest)
        self.manifest = manifest
        return manifest

    @classmethod
    def load(cls, directory, release_id):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if manifest["release_id"] != release_id:
            raise ValueError("complex_release_binding_mismatch")
        if checksum(directory / "embeddings.npz") != manifest["checksum"]:
            raise ValueError("complex_artifact_checksum_mismatch")
        with np.load(directory / "embeddings.npz", allow_pickle=False) as arrays:
            entity, relation = arrays["entity"], arrays["relation"]
        dim = manifest["config"]["dimension"]
        if entity.shape != (len(manifest["entities"]), 2, dim) or relation.shape != (len(manifest["relations"]), 2, dim):
            raise ValueError("complex_artifact_shape_mismatch")
        if not np.isfinite(entity).all() or not np.isfinite(relation).all():
            raise ValueError("complex_artifact_nonfinite_values")
        if len(set(manifest["entities"])) != len(manifest["entities"]) or len(set(manifest["relations"])) != len(manifest["relations"]):
            raise ValueError("complex_artifact_duplicate_identifiers")
        return cls(manifest["entities"], manifest["relations"], entity, relation, manifest)
