"""Mask study target labels before profiling, planning, retrieval or model calls."""
import copy
import re
from .utils import fingerprint

TARGET = re.compile(r'(?<![A-Za-z])(?:CN|MCI|AD)(?![A-Za-z])|阿尔[茨兹]海默|轻度认知障碍|认知正常|痴呆', re.I)
FIELDS = {'患病情况', '诊断', '疾病标签', '真实标签', '真实诊断', '诊断结果', 'diagnosis', 'diagnostic_label', 'target', 'label'}


def mask_labels(raw):
    masked = copy.deepcopy(raw)
    removed = []
    def visit(value, pointer=''):
        if isinstance(value, dict):
            for key, item in value.items():
                path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if key in FIELDS or '既往诊断' in key or '临床诊断' in key:
                    if item is not None:
                        removed.append(path)
                    value[key] = None
                elif key in {'path', '路径', '文件路径', '报告路径', '附件路径'} and item:
                    # An uninspected file may reveal the target in text or metadata.
                    raise ValueError('blinded_mode_requires_separately_sanitized_attachments:' + path)
                else:
                    value[key] = visit(item, path)
        elif isinstance(value, list):
            return [visit(item, pointer + '/' + str(i)) for i, item in enumerate(value)]
        elif isinstance(value, str) and TARGET.search(value):
            removed.append(pointer)
            return None
        return value
    masked = visit(masked)
    return masked, {'mode': 'blinded_classification', 'original_hash': fingerprint(raw),
                    'masked_hash': fingerprint(masked), 'removed_paths': removed,
                    'limitations': '规则屏蔽不是已验证的全语义去泄漏；正式实验仍需人工核查。'}
