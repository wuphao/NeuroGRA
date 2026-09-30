"""Compact, source-preserving model views and explicit bounded batches."""
from .utils import dumps


def observation_view(observation):
    return {'observation_id': observation.observation_id, 'name': observation.name,
            'quote': observation.quote, 'status': observation.status, 'time': observation.time.raw,
            'unit': observation.unit,
            'context': {k: v for k, v in observation.context.items()
                        if k in {'名称', '表格名称', '量表名称', '版本', '教育校正', '语言', '单位',
                                 '参考范围', '方法', '样本', '检测平台'}}}


def bounded_batches(items, view, max_chars=10000, max_items=35):
    current = []
    for item in items:
        if len(dumps(view(item))) > max_chars:
            raise ValueError('single_item_exceeds_batch_limit')
        if current and (len(current) >= max_items or len(dumps([view(i) for i in current + [item]])) > max_chars):
            yield current
            current = []
        current.append(item)
    if current:
        yield current
