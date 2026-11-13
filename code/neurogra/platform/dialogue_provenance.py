"""Persist scoped, review-aware claim links for a patient dialogue."""

from neurogra.clinical.collaboration_contracts import ClaimEvidenceLink, CollaborationClaim
from neurogra.clinical.collaboration_store import CollaborationConflict
from neurogra.clinical.utils import fingerprint, identity


def persist_claims(coordination, fence, run_id, patient_key, claims, claim_tasks, approved, conflict_ids, check):
    """Store only validated links; a model review remains a conditional assessment."""
    existing = {row['object_id']: row['payload'] for row in
                coordination.store.objects(run_id, 'collaboration_claim')}
    owned = coordination.fenced_store(fence, check=check)
    for claim, _, observations, evidence in claims:
        task_id = claim_tasks[claim.claim_id]
        approved_claim = claim.kind == 'interpretation' and claim.claim_id in approved
        links = [ClaimEvidenceLink(kind='patient', ref_id=row.observation_id, patient_key=patient_key,
                    relation='supports' if claim.kind == 'description' and row.observation_id not in conflict_ids
                    else 'unknown') for row in observations]
        for row in evidence:
            applicability = {'applicable': 'satisfied', 'not_applicable': 'violated',
                             'unknown': 'unknown'}.get(row.get('applicability'), 'not_evaluated')
            if approved_claim and applicability == 'satisfied' and row.get('allowed_use') in {
                    'direct_support', 'conditional_support'}:
                relation = 'supports'
            elif row.get('allowed_use') == 'background':
                relation = 'background'
            else:
                relation = 'unknown'
            links.append(ClaimEvidenceLink(kind='knowledge', ref_id=row['evidence_id'],
                                          patient_key=patient_key, relation=relation, applicability=applicability))
        status = ('not_reviewed' if claim.kind == 'description' else
                  'conditional' if approved_claim else 'rejected')
        entry = CollaborationClaim(task_id=task_id, patient_keys=[patient_key], claim=claim,
                                   links=links, review_status=status).model_dump(mode='json')
        key = identity('dialogue_claim_record', [task_id, claim.claim_id])
        if key in existing:
            if fingerprint(existing[key]) != fingerprint(entry):
                raise CollaborationConflict('claim_provenance_changed')
        else:
            owned.save(run_id, 'collaboration_claim', key, entry)
