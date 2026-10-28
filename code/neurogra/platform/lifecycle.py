"""Managed storage inventory and revalidated cleanup of expired rotated logs."""
import os
import time
from pathlib import Path

from neurogra.clinical.utils import fingerprint, sha256_file

from .locking import exclusive


class Lifecycle:
    def __init__(self, app):
        self.app = app

    def inventory(self):
        from neurogra.clinical.knowledge_adapter import settings

        from .knowledge import kb_service
        config = self.app.config
        kb, kg = kb_service(config).config, settings(config)
        roots = [('platform', self.app.root / 'data/platform'),
                 ('clinical', config.resolve(config.data_root)),
                 ('reports', config.resolve(config.report_root)),
                 ('clinical_work', config.resolve(config.output_root)),
                 ('kb_sources', kb.resolve(kb.data_root)), ('kb_releases', kb.resolve(kb.output_root)),
                 ('kg_sources', kg.resolve(kg.paths.data_root)), ('kg_releases', kg.resolve(kg.paths.output_root))]
        seen, groups, skipped = set(), [], []
        for category, directory in roots:
            resolved = directory.resolve()
            group = {'category': category, 'files': 0, 'bytes': 0}
            groups.append(group)
            if not resolved.is_relative_to(self.app.root) or resolved != directory.absolute():
                skipped.append({'category': category, 'reason': 'external_or_linked_root'})
                continue
            if not directory.is_dir():
                continue
            for base, directories, names in os.walk(directory, followlinks=False):
                directories[:] = [name for name in directories
                    if (Path(base) / name).resolve() == (Path(base) / name).absolute()]
                for name in names:
                    path = Path(base) / name
                    if path.resolve() != path.absolute() or path in seen or not path.is_file():
                        continue
                    try:
                        size = path.stat().st_size
                    except FileNotFoundError:
                        continue  # A concurrently rotated log is counted on the next scan.
                    seen.add(path)
                    group['files'] += 1
                    group['bytes'] += size
        return {'categories': groups, 'bytes': sum(g['bytes'] for g in groups),
                'skipped': skipped, 'unmanaged_directories_included': False}

    def preview(self, retention_days=30):
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 7:
            raise ValueError('日志保留期至少为 7 天。')
        now = time.time()
        candidates = []
        with self.app.store.connect() as db:
            attempts = list(db.execute("SELECT attempt_id,finished FROM attempts WHERE status IN "
                "('succeeded','failed','cancelled','interrupted') AND finished IS NOT NULL AND finished<?",
                (now - retention_days * 86400,)))
            registered = {r[0] for r in db.execute('SELECT path FROM artifacts')}
        for attempt in attempts:
            for ordinal in range(1, 4):
                relative = f'data/platform/runs/{attempt[0]}/llm_calls.jsonl.{ordinal}'
                if relative in registered:
                    continue
                path = self.app.inside(relative)
                if path != (self.app.root / relative).absolute() or not path.is_file():
                    continue
                stat = path.stat()
                if stat.st_mtime >= now - retention_days * 86400:
                    continue
                candidates.append({'path': relative, 'bytes': stat.st_size,
                    'checksum': sha256_file(path), 'modified': stat.st_mtime})
        return {'schema_version': 1, 'workspace': str(self.app.root), 'created': now,
            'retention_days': retention_days, 'candidates': candidates,
            'bytes': sum(c['bytes'] for c in candidates),
            'protected': '病例、数据库、知识发布、正式报告、当前日志、活动任务及未登记文件均不在本策略内。'}

    def apply(self, plan):
        if not isinstance(plan, dict) or plan.get('schema_version') != 1 or plan.get('workspace') != str(self.app.root):
            raise ValueError('cleanup_plan_invalid')
        if not isinstance(plan.get('created'), (int, float)) or not 0 <= time.time() - plan['created'] <= 86400:
            raise ValueError('cleanup_plan_expired')
        with exclusive(self.app.root, 'model_resource'), self.app.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
                raise ValueError('请在排队和运行任务结束后执行清理。')
            current = {c['path']: c for c in self.preview(plan.get('retention_days'))['candidates']}
            candidates = plan.get('candidates')
            if not isinstance(candidates, list) or len({c.get('path') for c in candidates if isinstance(c, dict)}) != len(candidates):
                raise ValueError('cleanup_plan_invalid')
            for candidate in candidates:
                if fingerprint(candidate) != fingerprint(current.get(candidate.get('path'))):
                    raise ValueError('cleanup_plan_changed_generate_new_preview')
            deleted, removed = [], 0
            for candidate in candidates:
                path = self.app.inside(candidate['path'])
                path.unlink()
                deleted.append(candidate['path'])
                removed += candidate['bytes']
            return {'deleted': deleted, 'bytes': removed}
