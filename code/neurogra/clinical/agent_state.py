"""Source-bound coverage and reusable validated batch results."""
from .utils import fingerprint


def material_hash(task, snapshot, evidence, access):
    refs = set(task.input_refs + task.background_refs)
    return fingerprint({'patient': snapshot.patient_id, 'questions': task.questions,
        'observations': [o.model_dump(mode='json') for o in snapshot.observations if o.observation_id in refs],
        'images': [i.model_dump(mode='json') for i in snapshot.images if i.asset_id in refs],
        'evidence': evidence, 'access': access.model_dump(mode='json')})


def coverage(facts, completed=(), failed=(), deferred=()):
    return {'total': len(facts), 'organized_ids': [o.observation_id for o in facts],
            'analyzed_ids': list(dict.fromkeys(completed)), 'failed_ids': list(dict.fromkeys(failed)),
            'deferred_ids': list(dict.fromkeys(deferred)), 'verification': 'unknown'}

