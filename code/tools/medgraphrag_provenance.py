"""Fingerprint the MedGraphRAG adapter, excluding unrelated benchmark projects."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def adapter_files():
    base = ROOT / 'code/neurogra/benchmarks'
    paths = list((base / 'medgraphrag').rglob('*.py'))
    paths += [base / 'patient_view.py', base / '__init__.py']
    return sorted(p for p in paths if p.is_file() and not p.name.startswith('test_'))


def adapter_fingerprints():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in adapter_files()}


def verify_adapter_implementation(recorded):
    for name, checksum in adapter_fingerprints().items():
        if name not in recorded:
            raise ValueError('implementation_not_recorded: ' + name)
        if recorded[name] != checksum:
            raise ValueError('implementation_changed_since_run: ' + name)
