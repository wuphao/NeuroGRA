"""Read-only historical artifact audit; never changes clinical conclusions."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def inspect_history(config):
    database = config.resolve(config.database)
    if not database.is_file():
        return {'runs': [], 'missing': 0, 'checked': 0, 'database_exists': False}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'objects' not in tables:
            return {'runs': [], 'missing': 0, 'checked': 0, 'database_exists': True}
        rows = db.execute("SELECT run_id,version,payload FROM objects WHERE kind='run_result' ORDER BY version DESC").fetchall()
    seen, runs = set(), []
    for run_id, version, payload in rows:
        if run_id in seen:
            continue
        seen.add(run_id)
        data = json.loads(payload)
        artifacts = []
        for kind, raw_path in data.get('report_paths', {}).items():
            path = config.resolve(Path(raw_path))
            inside = path.is_relative_to(config.project_root.resolve())
            status = 'available' if inside and path.is_file() else 'missing' if inside else 'outside_workspace'
            artifacts.append({'kind': kind, 'status': status})
        runs.append({'run_id': run_id, 'version': version, 'execution_status': data.get('status'),
            'artifact_status': 'available' if artifacts and all(a['status'] == 'available' for a in artifacts) else 'unavailable',
            'message': '' if artifacts and all(a['status'] == 'available' for a in artifacts) else '文件已清理或不可用，保留历史运行记录。',
            'artifacts': artifacts})
    return {'runs': runs, 'checked': sum(len(r['artifacts']) for r in runs),
        'missing': sum(a['status'] != 'available' for r in runs for a in r['artifacts']), 'database_exists': True}
